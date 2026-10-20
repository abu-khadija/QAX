"""
core/jira_bugs.py — file QAX findings as Jira BUGS (standard Jira issue API)
==========================================================================
Found by the first real filing attempt (B6, 2026-10-15, a fresh Jira Cloud
team-managed project). The old path in XrayClient.push_bug:
  • always sent `priority` — not on a team-managed Bug screen → HTTP 400;
  • its "dry run" only logged the title, so nobody could see what would be filed;
  • had no de-duplication — every re-scan filed every bug again;
  • lived behind XrayClient, whose live mode demands Xray credentials that plain
    Bug filing never uses.

This module needs ONLY Jira credentials (base URL, email, API token, project key):
  1. reads the project's Bug create-screen (read-only createmeta) and sends only the
     fields that are on it — anything else is dropped and reported, never 400s;
  2. de-duplicates by a stable fingerprint label (finding title + target, not the
     per-run evidence) against OPEN issues — a re-scan never double-files;
  3. a dry run returns the exact payload that WOULD be POSTed (plus the duplicate
     check and dropped fields), doing only read-only calls when credentials exist.

Never raises from file()/file_many(): failures come back in the result dict.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re

import requests

logger = logging.getLogger(__name__)

_SEV_TO_PRIORITY = {"P1": "Highest", "P2": "High", "P3": "Medium", "P4": "Low"}
_PLACEHOLDERS = ("your-domain", "your-email", "your-api-token", "YOUR_PROJECT", "")

# Fields every Jira Bug create-screen has; used when the screen can't be read
# (no credentials / network) so a preview never promises a field that may 400.
_SAFE_FIELDS = frozenset({"project", "summary", "description", "issuetype", "labels"})


_SEV_NAME = {"P1": "Critical", "P2": "High", "P3": "Medium", "P4": "Low"}
_METHOD_PATH = re.compile(r"\b(GET|POST|PUT|PATCH|DELETE)\s+(/\S+)")
_PREFIX_SEG = re.compile(r"^(?:api|rest|v\d+(?:\.\d+)?)$", re.I)


def _module_of(target: str) -> str:
    """'/api/v1/admin/orgs' → 'Admin' (skips mount/version prefixes and params)."""
    for seg in str(target or "").split("?")[0].split("/"):
        if seg and not seg.startswith("{") and not _PREFIX_SEG.match(seg):
            return seg.replace("-", " ").replace("_", " ").title()
    return "App"


def _sentences(text: str) -> list[str]:
    """Split plain text into sentences (good enough for the short guidance strings here)."""
    t = " ".join(str(text or "").split())
    return [x.strip() for x in re.split(r"(?<=[.!?])\s+", t) if x.strip()]


def _first_sentence(text: str) -> str:
    t = str(text or "").strip()
    m = re.search(r"^(.+?[.!?])(\s|$)", t)
    return (m.group(1) if m else t)[:240]


def finding_to_bug(b: dict, i: int = 0, *, environment: str | None = None,
                   base_url: str | None = None, client_safe: bool = False,
                   reproduced: tuple[int, int] | None = None,
                   build: dict | None = None) -> dict:
    """
    Map a QAX confirmed finding to a Jira Bug covering the owner's 13 mandatory fields
    in the compact security-bug layout:
    Summary · Severity (+ one-line business reason) · Environment · Location ·
    Reproducibility · Workaround · Steps to Reproduce (step 1 = a direct URL) ·
    Expected (a MUST rule) · Actual (evidence block) · Console Errors · Attachments ·
    Security Impact · Fix Recommendation · Regression Test · Notes (data safety).

    The caller enriches `b` with `remediation` (why/fix) and `qa` (how/expected/
    data_safety) from services.remediation — this module stays dependency-free.

    base_url: the scanned app's origin, so step 1 is a real, copy-pasteable URL.
    build: the scanned app's build fingerprint (core.build_fingerprint) — written as a
        parseable "Build: <id> (<version>)" line so `verify-fixes` can later tell a fix that
        shipped from a build that never changed.
    reproduced: (hits, attempts) when the caller MEASURED it (e.g. a re-verify pass);
        otherwise the report states only what is known — one scan run (
        "never repeat a number you didn't measure").
    client_safe: a filed ticket is read by developers and possibly the
        client, so it must read like a plain manual bug report: no tool name anywhere
        in the body or labels (the de-dup fingerprint label loses its tool prefix too).
    The summary carries [P#] because a team-managed Bug screen may have no priority
    field; the fingerprint uses title + target only (stable across scans).
    """
    sev = str(b.get("severity") or "P3").upper()
    rem = b.get("remediation") or {}
    qa = b.get("qa") or {}
    probe = str(b.get("probe", "") or b.get("source", "") or "sec")
    title = str(b.get("title", "Finding"))
    target = str(b.get("target", "") or "")
    m = _METHOD_PATH.search(title)
    method, path = (m.group(1), m.group(2)) if m else ("GET", target or "/")
    module = _module_of(target or path)
    reason = _first_sentence(rem.get("why", "")) or "Proven by a real request against the running app."
    env = environment or "the scanned application"
    fp = fingerprint(title, target, client_safe=client_safe)
    url = (base_url.rstrip("/") + path) if base_url and str(path).startswith("/") else path

    how = str(qa.get("how") or "")
    lead, _, observe = how.partition(" and ")
    if how.startswith("and ") or observe:
        # Observation finding (headers, cookies, fields, counts): one request, then
        # look — there is no injected condition, so no control request to send.
        lead = "" if how.startswith("and ") else lead
        look = (how[4:] if how.startswith("and ") else observe).strip()
        steps = [f"Send {method} {url}{(' ' + lead) if lead else ''}.",
                 look[:1].upper() + look[1:] + ".",
                 "Compare with the Expected Result below."]
    else:
        steps = [f"Send {method} {url} {how or 'as described under Actual Result'}.",
                 f"Send the same {method} {url} request WITHOUT that condition (control).",
                 "Compare the two responses: the difference shown under Actual Result is the defect."]

    # Reproducibility — only what was measured.
    if reproduced:
        repro = f"Reproduced {reproduced[0]}/{reproduced[1]} attempts on {env}."
    else:
        repro = (f"Reproduced 1/1 attempts on {env}. Not yet re-run on other environments — "
                 "repeat the steps above to confirm on yours.")
    # Summary = what happened + (only if there is more than the Severity reason already
    # says) what it means. The same sentence must not appear three times in one ticket.
    why_sents = _sentences(rem.get("why", ""))
    summary_parts = [_first_sentence(b.get("evidence", "")), why_sents[1] if len(why_sents) > 1 else ""]
    summary = " ".join(x for x in summary_parts if x) or title

    blocks = [
        ("h", "Summary"), ("p", [(summary, False)]),
        ("p", [("Severity: ", True), (f"{sev} / {_SEV_NAME.get(sev, '')} — {reason}", False)]),
        ("p", [("Environment: ", True), (env, False)]),
    ]
    if build and build.get("id"):
        from core.build_fingerprint import describe
        blocks.append(("p", [("Build: ", True), (describe(build), False)]))
    blocks += [
        ("p", [("Location: ", True), (f"{module} — {method} {path}", False)]),
        ("p", [("Reproducibility: ", True), (repro, False)]),
        ("p", [("Workaround: ", True), ("None known — not assessed. See Fix Recommendation for "
                                        "the permanent fix.", False)]),
    ]
    if not client_safe:
        blocks.append(("p", [("Found by: ", True), (f"QAX deterministic probe '{probe}' — reproduced with a "
                                                    f"real request and a control, zero-false-positive layer", False)]))
    blocks += [
        ("h", "Steps to Reproduce"), ("ol", steps),
        ("h", "Expected Result"),
        ("p", [(qa.get("expected") or "The request should be rejected or handled safely.", False)]),
        ("h", "Actual Result"), ("code", str(b.get("evidence", "") or "(no evidence captured)")),
        ("h", "Console Errors"),
        ("p", [("N/A — API-level finding; no browser was involved. The raw behaviour is under "
                "Actual Result.", False)]),
        ("h", "Screenshot/Attachments"),
        ("p", [("N/A — pure network finding, nothing visual to capture. "
                + ("The full evidence is attached as a text file." if not client_safe else
                   "The request/response evidence is attached as a text file."), False)]),
    ]
    if len(why_sents) > 1:      # a one-sentence "why" is already the Severity reason — don't repeat it
        blocks += [("h", "Security Impact"), ("p", [(rem["why"], False)])]
    if rem.get("fix"):
        blocks += [("h", "Fix Recommendation"), ("p", [(rem["fix"], False)])]
    if client_safe:
        regression = (f"After the fix, repeat the steps above: the request must now be rejected or "
                      f"handled safely. Reference: {fp}.")
    else:
        regression = (f"QAX re-checks this on every scan (probe '{probe}'). Once fixed, re-run a quick "
                      f"scan: the probe must stay silent. Fingerprint label: {fp}.")
    blocks += [("h", "Regression Test"), ("p", [(regression, False)])]
    if qa.get("data_safety"):
        blocks += [("h", "Notes"), ("p", [(qa["data_safety"], False)])]

    text = "\n".join(
        (str(x) if k in ("h", "code") else
         "\n".join(f"{n}. {t}" for n, t in enumerate(x, 1)) if k == "ol" else
         "".join(t for t, _ in x))
        for k, x in blocks)
    labels = ["security", f"severity-{sev.lower()}"] if client_safe else \
             ["qax", "security", f"severity-{sev.lower()}"]
    if probe and probe != "sec":
        labels.append(_label(probe))

    # The evidence file attached to the issue ("filing isn't done until
    # evidence is attached"). Secrets are masked before it leaves the machine.
    evidence_lines = [f"{title}", f"Location: {method} {url}", f"Environment: {env}", "",
                      "Evidence:", str(b.get("evidence", "") or "(none captured)"), "",
                      "Steps to reproduce:"] + [f"  {n}. {t}" for n, t in enumerate(steps, 1)]
    try:
        from services.results import redact_secrets
        evidence_text = redact_secrets("\n".join(evidence_lines))
    except Exception:
        evidence_text = "\n".join(evidence_lines)
    return {
        "test_case_id": f"QAX-{probe.upper().replace(' ', '-')}-{i + 1}",
        "title": f"[{sev}] [{module}] {title} ({probe})",
        "severity": sev,
        "description": text,
        "adf": _adf_blocks(blocks),
        "labels": labels,
        "fingerprint": fp,
        "evidence_text": evidence_text,
        "evidence_filename": f"evidence-{_label(probe)}-{fp[-8:]}.txt",
    }


_PRIORITY_TO_SEV = {"critical": "P1", "high": "P2", "medium": "P3", "low": "P4"}

# Bug headings per language (the case's own text stays as written)
_BUG_WORDS = {
    "en": {"summary": "Summary", "severity": "Severity: ", "sev_note": "from the test case priority",
           "env": "Environment: ", "req": "Requirement: ", "repro": "Reproducibility: ",
           "repro_text": "Failed 1/1 {tool}run on {env}. Repeat the steps to confirm.",
           "steps": "Steps to Reproduce", "expected": "Expected Result", "actual": "Actual Result",
           "problems": "Page problems seen", "evidence": "Evidence", "fails_at": "fails at step",
           "open": "Open", "as_stated": "As stated by the test case.", "see_evidence": "See the attached evidence.",
           "step_failed": "The step failed", "t_steps": "Steps", "t_exp": "Expected", "t_act": "Actual"},
    "ar": {"summary": "الملخص", "severity": "الخطورة: ", "sev_note": "من أولوية حالة الاختبار",
           "env": "البيئة: ", "req": "المتطلب: ", "repro": "إمكانية التكرار: ",
           "repro_text": "فشل في 1 من 1 تشغيل {tool}على {env}. أعد الخطوات للتأكيد.",
           "steps": "خطوات إعادة الحدوث", "expected": "النتيجة المتوقعة", "actual": "النتيجة الفعلية",
           "problems": "مشاكل ظهرت في الصفحة", "evidence": "الأدلة", "fails_at": "يفشل عند الخطوة",
           "open": "افتح", "as_stated": "كما في حالة الاختبار.", "see_evidence": "راجع الأدلة المرفقة.",
           "step_failed": "فشلت الخطوة", "t_steps": "الخطوات", "t_exp": "النتيجة المتوقعة",
           "t_act": "النتيجة الفعلية"},
}


def functional_result_to_bug(result: dict, case: dict | None = None, *, base_url: str = "",
                             environment: str | None = None, client_safe: bool = False,
                             lang: str = "en") -> dict:
    """
    A failed functional (E2E) test case as a Jira Bug in the ISTQB defect-report shape:
    title, severity, environment, the requirement it breaks, steps to reproduce (the
    case's own steps, in order), expected vs actual (from the failing step), evidence
    (screenshot / trace paths, page problems seen), reproducibility (only what was
    measured: one run). Same bug-dict keys as finding_to_bug(), so JiraBugFiler files it.

    Args:
        result (dict): One TestExecutor/APIRunner result (status FAIL).
        case (dict | None): The test case that ran (title, steps, priority, description).
        base_url (str): The app under test (step 1 gets a real URL).
        environment (str | None): Where it ran (defaults to base_url).
        client_safe (bool): No tool name in the text or labels.
        lang (str): "en" or "ar" — the language of the bug's headings.
    """
    import re as _re
    w = _BUG_WORDS.get(lang, _BUG_WORDS["en"])
    case = case or {}
    title_case = str(case.get("title") or result.get("title") or "Functional test failed")
    sev = _PRIORITY_TO_SEV.get(str(case.get("priority") or "medium").lower(), "P3")
    steps_run = result.get("steps") or []
    failing = next((st for st in steps_run if st.get("status") == "FAIL"), {})
    env = environment or base_url or "the application under test"
    covers = _re.search(r"Covers:\s*([^|.]+)", str(case.get("description") or ""))
    requirement = covers.group(1).strip() if covers else ""

    repro_steps = []
    for st in case.get("manual_steps") or []:
        a = str(st.get("action") or "").strip()
        if a:
            repro_steps.append(a)
    if not repro_steps:                               # from what actually ran
        for st in steps_run:
            a = str(st.get("action") or st.get("action_type") or "").strip()
            act = str(st.get("actual_result") or "").split(" (")[0]
            if act:
                repro_steps.append(act if not a else f"{a}: {act}"[:200])
    if base_url and repro_steps:
        repro_steps[0] = f"{w['open']} {base_url} — {repro_steps[0]}"

    expected = str(case.get("expected_result") or "")
    for st in case.get("manual_steps") or []:
        if st.get("step_number") == failing.get("step_number") and st.get("expected_result"):
            expected = str(st["expected_result"])
    if not expected:
        # an automated case: the failing step's own assertion is the expected result
        autos = case.get("automation_steps") or []
        n = failing.get("step_number")
        st = next((a for a in autos if a.get("step_number") == n), None) or \
            (autos[n - 1] if isinstance(n, int) and 0 < n <= len(autos) else {})
        if st.get("assertion_type"):
            where = st.get("selector_key") or "the page"
            expected = f"{st.get('assertion_type')} check on {where}: {st.get('assertion_value', '')}".strip()
    actual = str(failing.get("actual_result") or result.get("error") or w["step_failed"])
    if failing.get("error"):
        actual += f" — {failing['error']}"
    evidence = [p for p in (failing.get("screenshot"), result.get("screenshot"),
                            result.get("trace"), result.get("network_log")) if p]
    problems = [f"{e.get('kind')}: {e.get('text')}" for e in (result.get("page_errors") or [])][:8]

    summary = f"{title_case} — {w['fails_at']} {failing.get('step_number', '?')}"
    tool = "" if client_safe else "QAX "
    blocks = [
        ("h", w["summary"]), ("p", [(summary, False)]),
        ("p", [(w["severity"], True), (f"{sev} / {_SEV_NAME.get(sev, '')} — {w['sev_note']}", False)]),
        ("p", [(w["env"], True), (env, False)]),
    ]
    if requirement:
        blocks.append(("p", [(w["req"], True), (requirement, False)]))
    blocks += [("p", [(w["repro"], True), (w["repro_text"].format(tool=tool, env=env), False)]),
               ("h", w["steps"]), ("ol", repro_steps or [w["see_evidence"]]),
               ("h", w["expected"]), ("p", [(expected or w["as_stated"], False)]),
               ("h", w["actual"]), ("p", [(actual, False)])]
    if problems:
        blocks += [("h", w["problems"]), ("ol", problems)]
    if evidence:
        blocks += [("h", w["evidence"]), ("ol", evidence)]
    text = "\n".join([summary, f"{w['severity']}{sev}", f"{w['env']}{env}"]
                     + ([f"{w['req']}{requirement}"] if requirement else [])
                     + [w["t_steps"] + ":"] + [f"{n}. {t}" for n, t in enumerate(repro_steps, 1)]
                     + [f"{w['t_exp']}: {expected}", f"{w['t_act']}: {actual}"] + problems + evidence)
    fp = fingerprint(title_case, base_url, client_safe=client_safe)
    labels = ["functional", "e2e"] + ([] if client_safe else ["qax"])
    return {
        "test_case_id": str(result.get("test_case_id") or case.get("test_case_id") or "E2E"),
        "title": f"[{sev}] {title_case}",
        "severity": sev,
        "description": text,
        "adf": _adf_blocks(blocks),
        "labels": labels,
        "fingerprint": fp,
        "evidence_text": text,
        "evidence_filename": f"evidence-e2e-{fp[-8:]}.txt",
    }


def _adf_blocks(blocks: list) -> dict:
    """
    ADF doc from (kind, content) blocks: 'h' heading, 'p' paragraph of
    (text, bold) runs, 'ol' ordered list of strings, 'code' code block.
    """
    content = []
    for kind, x in blocks:
        if kind == "h":
            content.append({"type": "heading", "attrs": {"level": 3},
                            "content": [{"type": "text", "text": str(x)}]})
        elif kind == "p":
            runs = [{"type": "text", "text": str(t), **({"marks": [{"type": "strong"}]} if bold else {})}
                    for t, bold in x if str(t)]
            if runs:
                content.append({"type": "paragraph", "content": runs})
        elif kind == "ol":
            content.append({"type": "orderedList", "content": [
                {"type": "listItem", "content": [{"type": "paragraph",
                                                  "content": [{"type": "text", "text": str(t)}]}]}
                for t in x if str(t)]})
        elif kind == "code":
            content.append({"type": "codeBlock", "attrs": {},
                            "content": [{"type": "text", "text": str(x)}]})
    return {"type": "doc", "version": 1, "content": content or
            [{"type": "paragraph", "content": [{"type": "text", "text": " "}]}]}


def fingerprint(title: str, target: str = "", *, client_safe: bool = False) -> str:
    """
    Stable Jira label for a finding: same bug on a later scan → same label. Client-safe
    tickets drop the tool prefix (the digest is the same, so each mode de-dups within itself).
    """
    digest = hashlib.sha1(f"{title.strip().lower()}|{target.strip().lower()}".encode()).hexdigest()
    return f"fp-{digest[:12]}" if client_safe else f"qax-fp-{digest[:12]}"


def _label(s: str) -> str:
    """Jira labels cannot contain whitespace."""
    return re.sub(r"\s+", "-", str(s).strip())[:255]


def _adf(text: str) -> dict:
    """Minimal Atlassian Document Format doc from plain text (Jira Cloud needs ADF)."""
    paras = [{"type": "paragraph", "content": [{"type": "text", "text": ln}]}
             for ln in str(text or "").split("\n") if ln.strip()]
    return {"type": "doc", "version": 1, "content": paras or
            [{"type": "paragraph", "content": [{"type": "text", "text": " "}]}]}


class JiraBugFiler:
    """File findings as Jira Bugs — screen-aware, de-duplicated, honest dry run."""

    def __init__(self, dry_run: bool | None = None, *, base_url: str | None = None,
                 email: str | None = None, token: str | None = None,
                 project_key: str | None = None,
                 session: requests.Session | None = None) -> None:
        """
        Args:
            dry_run: Request-scoped flag; wins when given. Else DRY_RUN env, default TRUE.
            base_url/email/token/project_key: override the JIRA_* env vars.
            session: injectable HTTP session (tests).
        """
        self.base_url = (base_url if base_url is not None else os.getenv("JIRA_BASE_URL", "")).rstrip("/")
        self.email = email if email is not None else os.getenv("JIRA_EMAIL", "")
        self.token = token if token is not None else os.getenv("JIRA_API_TOKEN", "")
        self.project_key = project_key or os.getenv("JIRA_PROJECT_KEY", "")
        self.dry_run = bool(dry_run) if dry_run is not None else \
            os.getenv("DRY_RUN", "true").lower() == "true"
        self.session = session or requests.Session()
        self._screen: frozenset | None = None
        self._screen_read = False

    # ── configuration / read-only lookups ────────────────────────────────────

    def configured(self) -> bool:
        """True when real (non-placeholder) Jira credentials and a project are set."""
        vals = (self.base_url, self.email, self.token, self.project_key)
        return all(v and not any(p and p in v for p in _PLACEHOLDERS[:-1]) for v in vals)

    def _get(self, path: str, **params):
        return self.session.get(f"{self.base_url}{path}", params=params or None,
                                auth=(self.email, self.token),
                                headers={"Accept": "application/json"}, timeout=20)

    def screen_fields(self) -> frozenset | None:
        """
        Field ids on the project's Bug create-screen (read-only, cached), or None
        when it can't be read. Also returns None if the project has no Bug type.
        """
        if self._screen_read:
            return self._screen
        self._screen_read = True
        if not self.configured():
            return None
        try:
            r = self._get(f"/rest/api/3/issue/createmeta/{self.project_key}/issuetypes")
            if r.status_code != 200:
                return None
            body = r.json() or {}
            types = body.get("issueTypes") or body.get("values") or []
            bug = next((t for t in types if str(t.get("name", "")).lower() == "bug"), None)
            if not bug:
                return None
            r = self._get(f"/rest/api/3/issue/createmeta/{self.project_key}/issuetypes/{bug['id']}")
            if r.status_code != 200:
                return None
            body = r.json() or {}
            fields = body.get("fields") or body.get("values") or []
            self._screen = frozenset(str(f.get("fieldId")) for f in fields if f.get("fieldId"))
        except Exception as exc:
            logger.warning("QAX JiraBugFiler: could not read the Bug screen: %s", exc)
            self._screen = None
        return self._screen

    UNVERIFIED = "?duplicate-check-failed"

    def find_duplicate(self, fp: str) -> str | None:
        """
        Key of an OPEN issue already carrying this fingerprint label, else None — or
        UNVERIFIED when Jira could not be asked (it used to fail OPEN: a rate-limited search
        meant "no duplicate", and a duplicate ticket was filed). Retries 429/5xx twice.
        """
        if not (fp and self.configured()):
            return None
        import time as _t
        jql = (f'project = "{self.project_key}" AND labels = "{fp}" '
               f'AND statusCategory != Done ORDER BY created DESC')
        for attempt in range(3):
            try:
                r = self.session.post(f"{self.base_url}/rest/api/3/search/jql",
                                      json={"jql": jql, "maxResults": 1, "fields": ["key"]},
                                      auth=(self.email, self.token),
                                      headers={"Accept": "application/json"}, timeout=20)
            except Exception as exc:  # noqa: BLE001
                logger.warning("QAX JiraBugFiler: duplicate check failed: %s", exc)
                r = None
            if r is not None and r.status_code == 200:
                issues = (r.json() or {}).get("issues") or []
                return issues[0].get("key") if issues else None
            if r is not None and r.status_code not in (429, 500, 502, 503, 504):
                break
            _t.sleep(min(2 ** attempt, 4))
        return self.UNVERIFIED

    # ── payload ──────────────────────────────────────────────────────────────

    def build_payload(self, bug: dict, screen: frozenset | None = None) -> tuple[dict, list[str]]:
        """
        The Jira create payload for one bug, restricted to the Bug screen.
        Returns (payload, dropped_field_ids).
        """
        allowed = screen if screen is not None else _SAFE_FIELDS
        labels = [_label(x) for x in (bug.get("labels") or ["qax", "security"]) if str(x).strip()]
        if bug.get("fingerprint") and bug["fingerprint"] not in labels:
            labels.append(bug["fingerprint"])
        wanted = {
            "project": {"key": self.project_key},
            "summary": str(bug.get("title", "Security finding"))[:250],
            "description": bug.get("adf") or _adf(bug.get("description", "")),
            "issuetype": {"name": "Bug"},
            "labels": labels,
        }
        pri = _SEV_TO_PRIORITY.get(str(bug.get("severity", "")).upper())
        if pri:
            wanted["priority"] = {"name": pri}
        fields = {k: v for k, v in wanted.items() if k in allowed}
        dropped = sorted(k for k in wanted if k not in allowed)
        return {"fields": fields}, dropped

    # ── filing ───────────────────────────────────────────────────────────────

    def file(self, bug: dict) -> dict:
        """
        File one bug (or preview it in dry run). Result keys: success, test_case_id,
        jira_key, error, duplicate_of, dropped_fields, preview (dry run only).
        """
        bid = bug.get("test_case_id") or bug.get("id") or "BUG"
        res = {"success": False, "test_case_id": bid, "jira_key": None, "error": None,
               "duplicate_of": None, "dropped_fields": [],
               "summary": str(bug.get("title", ""))[:250]}
        try:
            screen = self.screen_fields()
            if self.configured() and screen is None and not self.dry_run:
                res["error"] = (f"Project {self.project_key} has no readable Bug issue type "
                                "— add 'Bug' in Project settings → Work types.")
                return res
            payload, dropped = self.build_payload(bug, screen)
            res["dropped_fields"] = dropped
            dup = self.find_duplicate(bug.get("fingerprint", ""))
            if dup == self.UNVERIFIED:
                if self.dry_run:
                    res.update(success=True, jira_key=f"DRY-RUN-BUG-{bid}", preview=payload,
                               note="the duplicate check could not reach Jira")
                else:
                    res["error"] = ("Jira's duplicate check could not be completed (rate limit or outage) — "
                                    "not filed, to avoid a duplicate ticket. Try again later.")
                return res
            if dup:
                res.update(success=True, jira_key=dup, duplicate_of=dup)
                if self.dry_run:
                    res["preview"] = payload
                return res
            if self.dry_run:
                res.update(success=True, jira_key=f"DRY-RUN-BUG-{bid}", preview=payload,
                           would_attach=bug.get("evidence_filename"))
                return res
            if not self.configured():
                res["error"] = "Jira credentials are not configured (JIRA_BASE_URL/EMAIL/API_TOKEN/PROJECT_KEY)."
                return res
            r = self.session.post(f"{self.base_url}/rest/api/3/issue", json=payload,
                                  auth=(self.email, self.token),
                                  headers={"Accept": "application/json",
                                           "Content-Type": "application/json"}, timeout=30)
            if r.status_code in (200, 201):
                key = (r.json() or {}).get("key")
                res.update(success=True, jira_key=key)
                if key and bug.get("evidence_text"):
                    # filing isn't done until the evidence is attached.
                    res["attachment"] = self._attach(key, bug.get("evidence_filename") or "evidence.txt",
                                                     bug["evidence_text"])
            else:
                res["error"] = f"HTTP {r.status_code}: {r.text[:300]}"
        except Exception as exc:
            res["error"] = str(exc)
        return res

    def link(self, bug_key: str, story_key: str) -> dict:
        """
        Link a filed bug to the story it breaks ("Relates"). Dry run: preview only.
        Returns {"success", "preview"?, "error"?}. Never raises.
        """
        payload = {"type": {"name": "Relates"}, "inwardIssue": {"key": bug_key},
                   "outwardIssue": {"key": story_key}}
        if self.dry_run:
            return {"success": True, "preview": payload}
        if not self.configured():
            return {"success": False, "error": "Jira credentials are not configured."}
        try:
            r = self.session.post(f"{self.base_url}/rest/api/3/issueLink", json=payload,
                                  auth=(self.email, self.token),
                                  headers={"Accept": "application/json", "Content-Type": "application/json"},
                                  timeout=20)
            return {"success": r.status_code in (200, 201)} if r.status_code in (200, 201) else \
                {"success": False, "error": f"HTTP {r.status_code}: {r.text[:200]}"}
        except Exception as exc:  # noqa: BLE001
            return {"success": False, "error": str(exc)[:200]}

    def comment(self, key: str, text: str) -> dict:
        """
        Add a comment to an issue (the requirement verdicts on the story). Dry run:
        preview only. Returns {"success", "preview"?, "error"?}. Never raises.
        """
        payload = {"body": _adf(text)}
        if self.dry_run:
            return {"success": True, "preview": text}
        if not self.configured():
            return {"success": False, "error": "Jira credentials are not configured."}
        try:
            r = self.session.post(f"{self.base_url}/rest/api/3/issue/{key}/comment", json=payload,
                                  auth=(self.email, self.token),
                                  headers={"Accept": "application/json", "Content-Type": "application/json"},
                                  timeout=20)
            return {"success": r.status_code in (200, 201)} if r.status_code in (200, 201) else \
                {"success": False, "error": f"HTTP {r.status_code}: {r.text[:200]}"}
        except Exception as exc:  # noqa: BLE001
            return {"success": False, "error": str(exc)[:200]}

    def _attach(self, key: str, filename: str, text: str) -> bool:
        """
        Attach a text evidence file to an issue (best-effort — a failed attachment never
        fails the filing; the caller sees `attachment: False`). Never raises.
        """
        try:
            r = self.session.post(
                f"{self.base_url}/rest/api/3/issue/{key}/attachments",
                files={"file": (filename, str(text).encode("utf-8"), "text/plain")},
                auth=(self.email, self.token),
                headers={"X-Atlassian-Token": "no-check", "Accept": "application/json"}, timeout=30)
            return r.status_code in (200, 201)
        except Exception as exc:
            logger.warning("QAX JiraBugFiler: could not attach evidence to %s: %s", key, exc)
            return False

    def file_many(self, bugs: list) -> dict:
        """File a batch. Adapter-shaped result plus per-bug detail (never raises).
        A bug carrying "story_key" is linked to that story once filed (or previewed)."""
        results = []
        for b in (bugs or []):
            if not isinstance(b, dict):
                continue
            r = self.file(b)
            story = str(b.get("story_key") or "").strip()
            if story and r.get("success") and r.get("jira_key"):
                lk = self.link(r["jira_key"], story)
                r["linked_to"] = story if lk.get("success") else None
                if lk.get("error"):
                    r["link_error"] = lk["error"]
            results.append(r)
        ok = [r for r in results if r.get("success")]
        return {
            "success": bool(results) and len(ok) == len(results),
            "exported_count": len([r for r in ok if not r.get("duplicate_of")]),
            "duplicate_count": len([r for r in ok if r.get("duplicate_of")]),
            "failed_count": len(results) - len(ok),
            "references": [r["jira_key"] for r in ok if r.get("jira_key")],
            "results": results,
            "error": None if ok or not results else (results[0].get("error") or "all failed"),
        }
