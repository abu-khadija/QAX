"""
ci/pr_scan.py — QAX security scan for CI (PR gate)
==================================================
Runs a QAX scan against a running app, writes a PR-comment markdown of the PROVEN
findings (with remediation) plus a machine-readable JSON, and exits non-zero when a
confirmed issue at or above the fail threshold is found — so a GitHub Action can
comment on the PR and gate the merge.

Reports only what QAX could PROVE (confirmed P1/P2) as blocking; low-confidence leads
(P3) are listed separately and never fail the build. An infrastructure error (no app
reachable, config missing) is reported but does NOT block the PR.

The comment follows the same honesty rules as the CLI: CONFIRMED (deterministic) findings
are kept apart from non-deterministic AI leads (never mixed by severity), a clean result is
never called "clean" while checks could not run (a NOT TESTED section says which and what would
unblock them), flaky hits are shown as UNSTABLE, and any test records QAX could not remove are
listed. `QAX_REQUIRE_PROBES` turns a missing check into a CI failure, so a green check can never
mean "the authorization probes silently didn't run".

Env:
  QAX_TARGET_URL     the running app (e.g. a preview deployment) to scan (required for live probes)
  QAX_REPO           source path to model; EMPTY (default) = live-app-only, which needs no API key
  QAX_TOKEN          a valid auth token (enables the authenticated probes)
  QAX_TOKEN_B        a second, ORDINARY user's token (enables BOLA / BFLA / access-revocation)
  QAX_AUTH_SCHEME    token scheme (default "Bearer"; "token" for Gitea-style; "" for a raw API key)
  QAX_AUTH_HEADER    header name (default "Authorization")
  QAX_FAIL_ON        comma list of severities that fail the build (default "P1,P2")
  QAX_REQUIRE_PROBES comma list of probes that MUST have run (e.g. "bola,bfla"); fail if not
  QAX_REVERIFY       N extra passes — confirm only what reproduces every time (default 0)
  QAX_DEEP           "1" to run the deep LLM pass (needs ANTHROPIC_API_KEY; default off)
  QAX_COMMENT_PATH   where to write the PR comment (default "pr_comment.md")
  QAX_JSON_PATH      where to write the JSON result (default "output/qax_scan.json")

Usage (locally or in CI):
  QAX_TARGET_URL=http://localhost:8000 python -m ci.pr_scan
"""

from __future__ import annotations

import json
import os
import sys
import time

_SEV_EMOJI = {"P1": "🔴", "P2": "🟠", "P3": "🟡"}


def _enrich(confirmed: list) -> list:
    """Attach remediation guidance to each finding (best-effort)."""
    try:
        from services.remediation import remediation_for
    except Exception:
        return confirmed
    for b in confirmed:
        if isinstance(b, dict) and not b.get("remediation"):
            b["remediation"] = remediation_for(b)
    return confirmed


_DETERMINISTIC = ("probe", "coverage")

# What to do about a recurring gap, in CI terms (secrets / env), not CLI flags.
_CI_NEEDS_HINT = {
    "token_b": "set the `QAX_TOKEN` and `QAX_TOKEN_B` secrets (the resource owner's and a second, "
               "ordinary user's token)",
    "auth-token": "set the `QAX_TOKEN` secret (QAX could not self-register or log in)",
    "valid-body": "QAX could not build a request this app accepts — an example valid request body for "
                  "the create endpoints would unblock it",
}


def _is_deterministic(b: dict) -> bool:
    """Probe/coverage findings are reproducible; a finding with no source is treated as one."""
    src = str(b.get("source", "") or "")
    return (not src) or src in _DETERMINISTIC


def _gap_lines(gaps: list) -> list[str]:
    """NOT TESTED, grouped once per prerequisite (CI wording)."""
    grouped: dict = {}
    loose: list = []
    for g in gaps:
        (grouped.setdefault(g["needs"], []) if g.get("needs") else loose).append(g)
    out: list[str] = []
    for needs, items in grouped.items():
        probes = ", ".join(f"`{p}`" for p in sorted({str(i.get("probe")) for i in items}))
        out.append(f"- {probes} — to enable: {_CI_NEEDS_HINT.get(needs, needs)}")
    for g in loose:
        out.append(f"- `{g.get('probe')}` — {g.get('reason')}")
    return out


def format_comment(url: str, confirmed: list, *, duration: float | None = None,
                   created: int = 0, cleaned: int = 0, not_tested: list | None = None,
                   unstable: list | None = None, left: list | None = None,
                   build: dict | None = None, fail_on=("P1", "P2")) -> tuple[str, list, list]:
    """
    Build the PR-comment markdown. Pure/testable. Returns (markdown, claims, leads):
    `claims` = deterministic findings at a blocking severity; `leads` = everything else
    (low-severity confirmed findings + non-deterministic AI leads).

    `confirmed` may mix both kinds; they are separated by `source` (probe/coverage =
    deterministic), never by severity — a P3 missing-header finding is CONFIRMED, just not
    blocking, while an AI suspicion stays a lead whatever its severity.
    """
    confirmed = _enrich(list(confirmed or []))
    fail = {str(x).upper() for x in fail_on}
    det = [b for b in confirmed if _is_deterministic(b)]
    ai = [b for b in confirmed if not _is_deterministic(b)]
    claims = [b for b in det if str(b.get("severity", "")).upper() in fail]
    low = [b for b in det if b not in claims]
    leads = low + ai
    gaps = [n for n in (not_tested or []) if n.get("kind") in ("missing-prerequisite", "error")]
    unstable = list(unstable or [])
    left = list(left or [])

    lines: list[str] = ["## 🛡️ QAX security scan", ""]
    if claims:
        lines.append(f"**🛑 {len(claims)} confirmed blocking issue{'s' if len(claims) != 1 else ''} — "
                     f"proven exploitable**" + (f" · {len(low)} lower-severity confirmed" if low else ""))
    elif det:
        lines.append(f"**✅ No blocking issues** · {len(low)} lower-severity confirmed finding"
                     f"{'s' if len(low) != 1 else ''}")
    elif gaps:
        lines.append(f"**⚠️ No confirmed issues — but {len(gaps)} check{'s' if len(gaps) != 1 else ''} "
                     f"could not run** (see NOT TESTED), so this is **not** a clean bill of health")
    else:
        lines.append("**✅ No confirmed issues** — the app held up")
    if gaps and det:
        lines.append(f"\n⚠️ {len(gaps)} check{'s' if len(gaps) != 1 else ''} could not run — see NOT TESTED below.")
    meta = []
    if url:
        meta.append(f"`{url}`")
    if build and build.get("id"):
        meta.append(f"build `{build['id']}`" + (f" ({build['version']})" if build.get("version") else ""))
    if duration is not None:
        meta.append(f"{duration:.0f}s")
    if meta:
        lines += ["", " · ".join(meta)]

    def _rows(items):
        out = ["", "| Sev | Issue | Where |", "|---|---|---|"]
        for b in items:
            sev = str(b.get("severity", "P?")).upper()
            emoji = _SEV_EMOJI.get(sev, "⚪")
            title = str(b.get("title", "")).replace("|", "\\|")
            target = str(b.get("target", "") or b.get("source", "")).replace("|", "\\|")
            rep = b.get("reproduced")
            tag = f" ⟨{rep[0]}/{rep[1]}⟩" if rep else ""
            out.append(f"| {emoji} {sev} | {title}{tag} | `{target}` |")
        return out

    def _details(items):
        out = []
        for b in items:
            rem = b.get("remediation") or {}
            if not (rem.get("why") or rem.get("fix")):
                continue
            out += ["", f"<details><summary><b>{str(b.get('title',''))}</b></summary>", ""]
            if b.get("evidence"):
                out += [f"> {str(b.get('evidence'))}", ""]
            if rem.get("why"):
                out.append(f"**Why it matters:** {rem['why']}")
            if rem.get("fix"):
                out.append(f"**How to fix:** {rem['fix']}")
            out.append("</details>")
        return out

    if claims:
        lines += ["", "### Blocking — confirmed, proven exploitable"] + _rows(claims) + _details(claims)
    if low:
        lines += ["", "### Confirmed — lower severity (does not block)"] + _rows(low)
    if ai:
        lines += ["", f"### AI leads — not confirmed ({len(ai)}, may vary between runs)"] + _rows(ai)
    if unstable:
        lines += ["", f"### ⚠️ Unstable — {len(unstable)} hit(s) did not reproduce on re-verification (NOT confirmed)"]
        lines += [f"- `{u.get('probe')}` {u.get('target') or ''} — {u.get('reason')}" for u in unstable]
    if gaps:
        lines += ["", "### ⚠️ NOT TESTED — these checks could not run",
                  "A quiet result here means *unknown*, not *safe*.", ""] + _gap_lines(gaps)
    if left:
        lines += ["", f"### Left on the target — QAX created test records and could not remove {len(left)}"]
        lines += [f"- `{i.get('collection') or '?'}`" + (f" `{i['key']}`" if i.get("key") else "") +
                  f" — {i.get('why')}" for i in left[:12]]
        if len(left) > 12:
            lines.append(f"- … and {len(left) - 12} more (see the uploaded JSON)")

    footer = "QAX · deterministic exploit probes · confirmed findings only block"
    if created:
        footer += f" · wrote {created} disposable test record(s), cleaned {cleaned}"
    lines += ["", f"<sub>{footer}</sub>"]
    return "\n".join(lines), claims, leads


def required_missing(required: set, not_tested: list) -> list:
    """The required probes that did NOT run (they appear in the not-tested ledger as a gap)."""
    gap_probes = {str(n.get("probe")) for n in (not_tested or [])
                  if n.get("kind") in ("missing-prerequisite", "error", "not-applicable")}
    return sorted(required & gap_probes)


def main() -> int:
    url = os.getenv("QAX_TARGET_URL", "").strip()
    repo = os.getenv("QAX_REPO", "").strip()                    # empty = live-app-only (no API key needed)
    fail_on = {s.strip().upper() for s in os.getenv("QAX_FAIL_ON", "P1,P2").split(",") if s.strip()}
    required = {s.strip() for s in os.getenv("QAX_REQUIRE_PROBES", "").split(",") if s.strip()}
    deep = os.getenv("QAX_DEEP", "").strip() in ("1", "true", "yes")
    try:
        reverify = max(0, int(os.getenv("QAX_REVERIFY", "0") or 0))
    except ValueError:
        reverify = 0
    comment_path = os.getenv("QAX_COMMENT_PATH", "pr_comment.md")
    json_path = os.getenv("QAX_JSON_PATH", "output/qax_scan.json")

    def _write_comment(md: str):
        try:
            with open(comment_path, "w", encoding="utf-8") as f:
                f.write(md)
        except Exception as exc:
            print(f"QAX: could not write comment: {exc}", file=sys.stderr)

    if not url:
        _write_comment("## 🛡️ QAX security scan\n\n⚠️ No `QAX_TARGET_URL` set — skipping live "
                       "scan. Start your app (or point at a preview deployment) and pass its URL.\n")
        print("QAX: no target URL; skipping (not blocking).")
        return 0

    try:
        from services.context import QAXContext
        from services.orchestrator_service import OrchestratorService
        ctx = QAXContext.from_env()
        # A key is needed only where an LLM is used: a deep scan, or modelling a repo's source.
        if deep or repo:
            ctx.require_anthropic_key()
    except Exception as exc:
        _write_comment(f"## 🛡️ QAX security scan\n\n⚠️ Config error: {exc}. Not blocking the PR.\n")
        print(f"QAX: config error: {exc}")
        return 0

    auth = None
    tok, tok_b = os.getenv("QAX_TOKEN", "").strip(), os.getenv("QAX_TOKEN_B", "").strip()
    if tok or tok_b:
        auth = {"token": tok or None, "token_b": tok_b or None,
                "header": os.getenv("QAX_AUTH_HEADER", "Authorization") or "Authorization",
                "scheme": os.getenv("QAX_AUTH_SCHEME", "Bearer")}
    scan_repo = repo
    if not scan_repo:                                           # live-app-only: an empty source dir
        import tempfile
        scan_repo = tempfile.mkdtemp(prefix="qax_ci_")

    t0 = time.time()
    try:
        result = OrchestratorService(ctx).run(scan_repo, url, deep=deep, tour=False,
                                              auth=auth, reverify=reverify)
    except Exception as exc:
        _write_comment(f"## 🛡️ QAX security scan\n\n⚠️ Scan error: {exc}. Not blocking the PR.\n")
        print(f"QAX: scan error: {exc}")
        return 0
    dur = time.time() - t0

    if getattr(result, "error", None):
        _write_comment(f"## 🛡️ QAX security scan\n\n⚠️ {result.error}. Not blocking the PR.\n")
        print(f"QAX: {result.error}")
        return 0

    try:
        from core.build_fingerprint import build_fingerprint
        build = build_fingerprint(url)
    except Exception:
        build = None

    not_tested = list(getattr(result, "not_tested", []) or [])
    unstable = [n for n in not_tested if n.get("kind") == "unstable"]
    md, claims, leads = format_comment(
        url, list(result.confirmed or []), duration=dur,
        created=getattr(result, "created_records", 0), cleaned=getattr(result, "cleaned_records", 0),
        not_tested=not_tested, unstable=unstable, left=list(getattr(result, "left_on_target", []) or []),
        build=build, fail_on=fail_on)

    # Required checks: a probe that was supposed to guard this PR but could not run is a FAILURE,
    # never a silent pass (the "green check that never ran the authorization probes" failure).
    missing = required_missing(required, not_tested)
    if missing:
        md += ("\n\n**🛑 Required check(s) did not run: " + ", ".join(f"`{m}`" for m in missing) +
               "** — failing the check (`QAX_REQUIRE_PROBES`).")
    _write_comment(md)
    try:
        os.makedirs(os.path.dirname(json_path) or ".", exist_ok=True)
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump({"url": url, "build": build, "claims": claims, "leads": leads,
                       "not_tested": not_tested, "left_on_target": list(getattr(result, "left_on_target", []) or []),
                       "required_missing": missing, "duration_sec": round(dur, 1)}, f, indent=2, default=str)
    except Exception:
        pass

    blocking = [b for b in claims if str(b.get("severity", "")).upper() in fail_on]
    print(f"QAX: {len(claims)} blocking confirmed, {len(leads)} other, {len(not_tested)} not-tested entries "
          f"in {dur:.0f}s — {'FAILING' if (blocking or missing) else 'passing'} "
          f"(fail_on={sorted(fail_on)}, required_missing={missing or 'none'})")
    return 1 if (blocking or missing) else 0


if __name__ == "__main__":
    sys.exit(main())
