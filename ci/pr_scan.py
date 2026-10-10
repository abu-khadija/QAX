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

Env:
  QAX_TARGET_URL   the running app to scan (required for live probes)
  QAX_REPO         source path to model (default ".")
  QAX_FAIL_ON      comma list of severities that fail the build (default "P1,P2")
  QAX_DEEP         "1" to run the deep LLM pass (default off)
  QAX_COMMENT_PATH where to write the PR comment (default "pr_comment.md")
  QAX_JSON_PATH    where to write the JSON result (default "output/qax_scan.json")

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


def format_comment(url: str, confirmed: list, *, duration: float | None = None,
                   created: int = 0, cleaned: int = 0) -> tuple[str, list, list]:
    """
    Build the PR-comment markdown from confirmed findings. Pure/testable.
    Returns (markdown, claims, leads) where claims are P1/P2 and leads are the rest.
    """
    confirmed = _enrich(list(confirmed or []))
    claims = [b for b in confirmed if str(b.get("severity", "")).upper() in ("P1", "P2")]
    leads = [b for b in confirmed if b not in claims]

    lines: list[str] = ["## 🛡️ QAX security scan", ""]
    if claims:
        lines.append(f"**🛑 {len(claims)} confirmed issue{'s' if len(claims) != 1 else ''} — "
                     f"proven exploitable**" + (f" · {len(leads)} lead{'s' if len(leads) != 1 else ''} to review"
                                                if leads else ""))
    else:
        lines.append(f"**✅ No confirmed issues** — the app held up"
                     + (f" · {len(leads)} low-confidence lead{'s' if len(leads) != 1 else ''}" if leads else ""))
    meta = []
    if url:
        meta.append(f"`{url}`")
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
            out.append(f"| {emoji} {sev} | {title} | `{target}` |")
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
        lines += ["", "### Confirmed — proven exploitable"] + _rows(claims) + _details(claims)
    if leads:
        lines += ["", "### Leads — flagged, not confirmed"] + _rows(leads)

    footer = "QAX · deterministic exploit probes · reports only what it proves"
    if created:
        footer += f" · wrote {created} disposable test record(s), cleaned {cleaned}"
    lines += ["", f"<sub>{footer}</sub>"]
    return "\n".join(lines), claims, leads


def main() -> int:
    url = os.getenv("QAX_TARGET_URL", "").strip()
    repo = os.getenv("QAX_REPO", ".").strip() or "."
    fail_on = {s.strip().upper() for s in os.getenv("QAX_FAIL_ON", "P1,P2").split(",") if s.strip()}
    deep = os.getenv("QAX_DEEP", "").strip() in ("1", "true", "yes")
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
                       "scan. Start your app in CI and pass its URL to run the probes.\n")
        print("QAX: no target URL; skipping (not blocking).")
        return 0

    try:
        from services.context import QAXContext
        from services.orchestrator_service import OrchestratorService
        ctx = QAXContext.from_env()
    except Exception as exc:
        _write_comment(f"## 🛡️ QAX security scan\n\n⚠️ Config error: {exc}. Not blocking the PR.\n")
        print(f"QAX: config error: {exc}")
        return 0

    t0 = time.time()
    try:
        result = OrchestratorService(ctx).run(repo, url, deep=deep, tour=False)
    except Exception as exc:
        _write_comment(f"## 🛡️ QAX security scan\n\n⚠️ Scan error: {exc}. Not blocking the PR.\n")
        print(f"QAX: scan error: {exc}")
        return 0
    dur = time.time() - t0

    if getattr(result, "error", None):
        _write_comment(f"## 🛡️ QAX security scan\n\n⚠️ {result.error}. Not blocking the PR.\n")
        print(f"QAX: {result.error}")
        return 0

    md, claims, leads = format_comment(
        url, list(result.confirmed or []), duration=dur,
        created=getattr(result, "created_records", 0), cleaned=getattr(result, "cleaned_records", 0))
    _write_comment(md)
    try:
        os.makedirs(os.path.dirname(json_path) or ".", exist_ok=True)
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump({"url": url, "claims": claims, "leads": leads,
                       "duration_sec": round(dur, 1)}, f, indent=2, default=str)
    except Exception:
        pass

    blocking = [b for b in claims if str(b.get("severity", "")).upper() in fail_on]
    print(f"QAX: {len(claims)} confirmed, {len(leads)} leads in {dur:.0f}s — "
          f"{'FAILING' if blocking else 'passing'} (fail_on={sorted(fail_on)})")
    return 1 if blocking else 0


if __name__ == "__main__":
    sys.exit(main())
