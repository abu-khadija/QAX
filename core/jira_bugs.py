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


def _first_sentence(text: str) -> str:
    t = str(text or "").strip()
    m = re.search(r"^(.+?[.!?])(\s|$)", t)
    return (m.group(1) if m else t)[:240]


def finding_to_bug(b: dict, i: int = 0, *, environment: str | None = None) -> dict:
    """
    Map a QAX confirmed finding to a Jira Bug in the owner's COMPACT security-bug
    format (_bug_report_templates.md, Format 2):
    Summary · Severity (with a one-line reason) · Environment · Component ·
    Steps to Reproduce · Expected (a MUST rule) · Actual (evidence block) ·
    Security Impact · Fix Recommendation · Regression Test · Notes (data safety).

    The caller enriches `b` with `remediation` (why/fix) and `qa` (how/expected/
    data_safety) from services.remediation — this module stays dependency-free.
    The summary carries [P#] because a team-managed Bug screen may have no
    priority field; the fingerprint uses title + target only (stable across scans).
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
    fp = fingerprint(title, target)

    how = str(qa.get("how") or "")
    lead, _, observe = how.partition(" and ")
    if how.startswith("and ") or observe:
        # Observation finding (headers, cookies, fields, counts): one request, then
        # look — there is no injected condition, so no control request to send.
        lead = "" if how.startswith("and ") else lead
        look = (how[4:] if how.startswith("and ") else observe).strip()
        steps = [f"Send {method} {path}{(' ' + lead) if lead else ''}.",
                 look[:1].upper() + look[1:] + ".",
                 "Compare with the Expected Result below."]
    else:
        steps = [f"Send {method} {path} {how or 'as described under Actual Result'}.",
                 f"Send the same {method} {path} request WITHOUT that condition (control).",
                 "Compare the two responses: the difference shown under Actual Result is the defect."]
    blocks = [
        ("p", [("Severity: ", True), (f"{sev} / {_SEV_NAME.get(sev, '')} — {reason}", False)]),
        ("p", [("Environment: ", True), (env, False)]),
        ("p", [("Component: ", True), (f"{module} — {method} {path}", False)]),
        ("p", [("Found by: ", True), (f"QAX deterministic probe '{probe}' — reproduced with a real "
                                      f"request and a control, zero-false-positive layer", False)]),
        ("h", "Steps to Reproduce"), ("ol", steps),
        ("h", "Expected Result"),
        ("p", [(qa.get("expected") or "The request should be rejected or handled safely.", False)]),
        ("h", "Actual Result"), ("code", str(b.get("evidence", "") or "(no evidence captured)")),
    ]
    if rem.get("why"):
        blocks += [("h", "Security Impact"), ("p", [(rem["why"], False)])]
    if rem.get("fix"):
        blocks += [("h", "Fix Recommendation"), ("p", [(rem["fix"], False)])]
    blocks += [
        ("h", "Regression Test"),
        ("p", [(f"QAX re-checks this on every scan (probe '{probe}'). Once fixed, re-run a quick "
                f"scan: the probe must stay silent. Fingerprint label: {fp}.", False)]),
    ]
    if qa.get("data_safety"):
        blocks += [("h", "Notes"), ("p", [(qa["data_safety"], False)])]

    plain = "\n".join(
        (str(x) if k in ("h", "code") else
         "\n".join(f"{n}. {t}" for n, t in enumerate(x, 1)) if k == "ol" else
         "".join(t for t, _ in x))
        for k, x in blocks)
    labels = ["qax", "security", f"severity-{sev.lower()}"]
    if probe and probe != "sec":
        labels.append(_label(probe))
    return {
        "test_case_id": f"QAX-{probe.upper().replace(' ', '-')}-{i + 1}",
        "title": f"[{sev}] [{module}] {title} ({probe})",
        "severity": sev,
        "description": plain,
        "adf": _adf_blocks(blocks),
        "labels": labels,
        "fingerprint": fp,
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


def fingerprint(title: str, target: str = "") -> str:
    """Stable Jira label for a finding: same bug on a later scan → same label."""
    digest = hashlib.sha1(f"{title.strip().lower()}|{target.strip().lower()}".encode()).hexdigest()
    return f"qax-fp-{digest[:12]}"


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

    def find_duplicate(self, fp: str) -> str | None:
        """Key of an OPEN issue already carrying this fingerprint label, else None."""
        if not (fp and self.configured()):
            return None
        try:
            jql = (f'project = "{self.project_key}" AND labels = "{fp}" '
                   f'AND statusCategory != Done ORDER BY created DESC')
            r = self.session.post(f"{self.base_url}/rest/api/3/search/jql",
                                  json={"jql": jql, "maxResults": 1, "fields": ["key"]},
                                  auth=(self.email, self.token),
                                  headers={"Accept": "application/json"}, timeout=20)
            if r.status_code != 200:
                return None
            issues = (r.json() or {}).get("issues") or []
            return issues[0].get("key") if issues else None
        except Exception as exc:
            logger.warning("QAX JiraBugFiler: duplicate check failed: %s", exc)
            return None

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
               "duplicate_of": None, "dropped_fields": []}
        try:
            screen = self.screen_fields()
            if self.configured() and screen is None and not self.dry_run:
                res["error"] = (f"Project {self.project_key} has no readable Bug issue type "
                                "— add 'Bug' in Project settings → Work types.")
                return res
            payload, dropped = self.build_payload(bug, screen)
            res["dropped_fields"] = dropped
            dup = self.find_duplicate(bug.get("fingerprint", ""))
            if dup:
                res.update(success=True, jira_key=dup, duplicate_of=dup)
                if self.dry_run:
                    res["preview"] = payload
                return res
            if self.dry_run:
                res.update(success=True, jira_key=f"DRY-RUN-BUG-{bid}", preview=payload)
                return res
            if not self.configured():
                res["error"] = "Jira credentials are not configured (JIRA_BASE_URL/EMAIL/API_TOKEN/PROJECT_KEY)."
                return res
            r = self.session.post(f"{self.base_url}/rest/api/3/issue", json=payload,
                                  auth=(self.email, self.token),
                                  headers={"Accept": "application/json",
                                           "Content-Type": "application/json"}, timeout=30)
            if r.status_code in (200, 201):
                res.update(success=True, jira_key=(r.json() or {}).get("key"))
            else:
                res["error"] = f"HTTP {r.status_code}: {r.text[:300]}"
        except Exception as exc:
            res["error"] = str(exc)
        return res

    def file_many(self, bugs: list) -> dict:
        """File a batch. Adapter-shaped result plus per-bug detail (never raises)."""
        results = [self.file(b) for b in (bugs or []) if isinstance(b, dict)]
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
