"""
core/jira_verify.py — re-verify filed findings against a fresh scan (the "Dev Done" pass)
=========================================================================================
Closes the loop find → file → VERIFY. Verdicts come
from LIVE behaviour (a fresh scan), never from the ticket's status, in three buckets that
are never rounded into each other:

  FIXED              the finding no longer reproduces AND its probe actually ran
  STILL REPRODUCES   the same finding (same fingerprint) fires again
  COULD NOT TEST     we cannot honestly say — and the reason is stated

What keeps FIXED honest (each rule is a mistake the owner's own history paid for):
  • a probe that merely went quiet is NOT clearance: if that probe reported ANY not-tested
    entry (missing prerequisite / crashed / not applicable now / unstable) the verdict is
    COULD NOT TEST, never FIXED ("absence of a finding != a pass");
  • a bug that vanishes on an UNCHANGED build is flakiness, not a fix ("intermittent
    absence is not clearance") — so a known-identical build downgrades FIXED to COULD NOT TEST;
  • a scan of a DIFFERENT origin than the ticket was filed against proves nothing;
  • a scan that grounded no endpoints tested nothing.
STILL REPRODUCES additionally says whether the build changed ("fix not deployed" vs "fix did
not work") and flags tickets already closed that still reproduce ("closed but not fixed").

QAX never closes anything on its own: reading is the default; `apply_fixed` (explicit,
opt-in) comments on FIXED tickets and moves them to a Done status — never touching
STILL REPRODUCES / COULD NOT TEST tickets, never as "Duplicate".
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlparse

from core.build_fingerprint import describe, same_build
from core.jira_bugs import JiraBugFiler, _adf, fingerprint

logger = logging.getLogger(__name__)

FIXED = "FIXED"
STILL = "STILL REPRODUCES"
UNKNOWN = "COULD NOT TEST"

_FP_LABEL = re.compile(r"^(?:qax-)?fp-([0-9a-f]{12})$")
_BUILD_LINE = re.compile(r"Build:\s*([0-9a-f]{12})(?:\s*\(([^)]*)\))?")
_ORIGIN_IN_STEP = re.compile(r"Send\s+(?:GET|POST|PUT|PATCH|DELETE)\s+(https?://[^\s/]+)")
_RESERVED_LABELS = ("qax", "security")
_DETERMINISTIC = ("probe", "coverage")


# ── tickets ──────────────────────────────────────────────────────────────────

@dataclass
class Ticket:
    """One Jira issue filed by QAX, reduced to what verification needs."""
    key: str
    summary: str
    status: str
    status_category: str          # "new" | "indeterminate" | "done"
    digest: str                   # the 12-hex finding fingerprint (from its label)
    probe: str | None             # the probe id (from its label)
    build: dict | None            # {"id", "version"} recorded when it was filed
    origin: str | None            # scheme://host[:port] it was filed against
    client_safe: bool             # labelled fp-* (no tool name) rather than qax-fp-*

    @property
    def done(self) -> bool:
        return self.status_category == "done"


def adf_text(node) -> str:
    """Plain text of an Atlassian Document Format node (paragraphs → lines)."""
    if isinstance(node, str):
        return node
    if isinstance(node, list):
        return "".join(adf_text(n) for n in node)
    if not isinstance(node, dict):
        return ""
    if node.get("type") == "text":
        return str(node.get("text", ""))
    inner = adf_text(node.get("content") or [])
    return inner + ("\n" if node.get("type") in ("paragraph", "heading", "listItem", "codeBlock") else "")


def parse_ticket(issue: dict) -> Ticket | None:
    """A Ticket from a Jira issue, or None when it was not filed by QAX (no fingerprint label)."""
    f = issue.get("fields") or {}
    labels = [str(x) for x in (f.get("labels") or [])]
    fp_label = next((l for l in labels if _FP_LABEL.match(l)), None)
    if not fp_label:
        return None
    digest = _FP_LABEL.match(fp_label).group(1)
    probe = next((l for l in labels if l not in _RESERVED_LABELS and not l.startswith("severity-")
                  and not _FP_LABEL.match(l)), None)
    text = adf_text(f.get("description"))
    bm = _BUILD_LINE.search(text)
    om = _ORIGIN_IN_STEP.search(text)
    status = f.get("status") or {}
    return Ticket(
        key=str(issue.get("key", "")), summary=str(f.get("summary", "")),
        status=str(status.get("name", "")),
        status_category=str((status.get("statusCategory") or {}).get("key", "")),
        digest=digest, probe=probe,
        build={"id": bm.group(1), "version": bm.group(2)} if bm else None,
        origin=om.group(1) if om else None,
        client_safe=fp_label.startswith("fp-"))


# ── verdicts ─────────────────────────────────────────────────────────────────

@dataclass
class Verdict:
    ticket: Ticket
    bucket: str                   # FIXED | STILL | UNKNOWN
    reason: str
    closed_but_reproduces: bool = False
    # FIXED, but the fix could not be tied to a deploy (the build is unknown on either side):
    # reported, never auto-closed. "Match the build to the fix version".
    weak: bool = False


def digest_of(title: str, target: str = "") -> str:
    """The 12-hex fingerprint digest of a finding (same function that labelled the ticket)."""
    return fingerprint(title, target)[-12:]


def _origin(url: str | None) -> str | None:
    if not url:
        return None
    u = urlparse(url if "//" in url else "//" + url)
    return f"{u.scheme or 'http'}://{u.netloc}" if u.netloc else None


def scan_unusable_reason(scan: dict) -> str | None:
    """Why a scan result cannot verify ANYTHING (it tested nothing), or None when it is usable."""
    if scan.get("error") or scan.get("success") is False:
        return f"the scan itself failed ({scan.get('error') or 'unsuccessful'})"
    legs = scan.get("legs") or {}
    if "endpoints_grounded" in legs and not legs.get("endpoints_grounded") and not scan.get("confirmed"):
        return "the scan grounded no endpoints, so nothing was actually tested"
    return None


_LEDGER_WHY = {
    "missing-prerequisite": "its probe could not run in this scan",
    "error": "its probe crashed in this scan",
    "not-applicable": "its probe no longer applies to this app (the surface changed)",
    "unstable": "it fired in some passes but not all",
}


def _why_no_build(t: Ticket, cur_build: dict | None) -> str:
    """Which side of the build comparison is missing — so the operator knows what to fix."""
    if not t.build and not cur_build:
        return "neither the ticket nor this scan has a build fingerprint"
    if not t.build:
        return "the ticket was filed before QAX recorded builds"
    return "this scan could not fingerprint the app's build"


def verdict_for(t: Ticket, scan: dict) -> Verdict:
    """The bucket for one ticket against one scan result. Pure; never raises."""
    unusable = scan_unusable_reason(scan)
    if unusable:
        return Verdict(t, UNKNOWN, unusable)
    cur_origin, ticket_origin = _origin(scan.get("target_url")), t.origin
    if cur_origin and ticket_origin and cur_origin.lower() != ticket_origin.lower():
        return Verdict(t, UNKNOWN, f"the ticket was filed against {ticket_origin} but this scan is of "
                                   f"{cur_origin} — a different app proves nothing")

    cur_build = scan.get("build")
    same = same_build(t.build, cur_build)
    det = {digest_of(b.get("title", ""), b.get("target", "")): b
           for b in (scan.get("confirmed") or []) if str(b.get("source", "")) in _DETERMINISTIC}

    if t.digest in det:
        if same is True:
            why = (f"still reproduces on the SAME build ({describe(cur_build)}) — the fix has "
                   f"probably not been deployed yet")
        elif same is False:
            why = (f"still reproduces on a NEW build (filed on {describe(t.build)}, now "
                   f"{describe(cur_build)}) — the fix did not work")
        else:
            why = f"still reproduces (build comparison unavailable: {_why_no_build(t, cur_build)})"
        return Verdict(t, STILL, why, closed_but_reproduces=t.done)

    # Not reproduced. Before calling that a fix, make sure the probe really ran and was stable.
    ledger = [n for n in (scan.get("not_tested") or [])
              if n.get("probe") == t.probe
              or (n.get("kind") == "unstable" and digest_of(n.get("title", ""), n.get("target", "")) == t.digest)]
    if ledger:
        n = ledger[0]
        return Verdict(t, UNKNOWN, f"not reproduced, but that is not clearance — {_LEDGER_WHY.get(n.get('kind'), 'its probe did not run cleanly')}"
                                   f"{': ' + str(n['reason']) if n.get('reason') else ''}")
    if not t.probe:
        return Verdict(t, UNKNOWN, "not reproduced, but the ticket carries no probe label to confirm the probe ran")
    if same is True:
        return Verdict(t, UNKNOWN, f"no longer reproduces, but the build is UNCHANGED ({describe(cur_build)}) — "
                                   f"a bug that vanishes without a deploy is flaky, not fixed; re-run with --reverify 2")
    note = (f"the build changed ({describe(t.build)} → {describe(cur_build)})" if same is False
            else f"build comparison unavailable: {_why_no_build(t, cur_build)}")
    return Verdict(t, FIXED, f"no longer reproduces; its probe ran and reported no gap; {note}",
                   weak=same is None)


def summarize(verdicts: list[Verdict]) -> dict:
    """Counts per bucket — three numbers that always sum to the total, never rounded together."""
    out = {FIXED: 0, STILL: 0, UNKNOWN: 0}
    for v in verdicts:
        out[v.bucket] += 1
    out["total"] = len(verdicts)
    out["closed_but_reproduces"] = sum(1 for v in verdicts if v.closed_but_reproduces)
    out["fixed_build_unverified"] = sum(1 for v in verdicts if v.bucket == FIXED and v.weak)
    return out


# ── Jira I/O ─────────────────────────────────────────────────────────────────

class JiraTicketReader:
    """Lists QAX-filed tickets and (opt-in) records verified fixes. Shares auth/config with JiraBugFiler."""

    def __init__(self, filer: JiraBugFiler | None = None, **kw) -> None:
        self.f = filer or JiraBugFiler(dry_run=True, **kw)

    def configured(self) -> bool:
        return self.f.configured()

    def list_tickets(self, project: str | None = None, limit: int = 200) -> list[Ticket]:
        """Every issue in the project that QAX filed (has a fingerprint label), any status."""
        proj = project or self.f.project_key
        jql = f'project = "{proj}" AND labels = "security" ORDER BY created DESC'
        out: list[Ticket] = []
        token = None
        while len(out) < limit:
            body = {"jql": jql, "maxResults": 100,
                    "fields": ["summary", "status", "labels", "description"]}
            if token:
                body["nextPageToken"] = token
            r = self.f.session.post(f"{self.f.base_url}/rest/api/3/search/jql", json=body,
                                    auth=(self.f.email, self.f.token),
                                    headers={"Accept": "application/json"}, timeout=30)
            if r.status_code != 200:
                raise RuntimeError(f"Jira search failed: HTTP {r.status_code}: {getattr(r, 'text', '')[:200]}")
            data = r.json() or {}
            for issue in data.get("issues") or []:
                t = parse_ticket(issue)
                if t:
                    out.append(t)
            token = data.get("nextPageToken")
            if data.get("isLast", True) or not token:
                break
        return out[:limit]

    def _done_transition(self, key: str):
        r = self.f.session.get(f"{self.f.base_url}/rest/api/3/issue/{key}/transitions",
                               auth=(self.f.email, self.f.token),
                               headers={"Accept": "application/json"}, timeout=20)
        if r.status_code != 200:
            return None
        for tr in (r.json() or {}).get("transitions") or []:
            to = tr.get("to") or {}
            if (to.get("statusCategory") or {}).get("key") == "done" and "duplicate" not in str(tr.get("name", "")).lower():
                return tr.get("id")
        return None

    def apply_fixed(self, verdicts: list[Verdict], env: str = "", dry_run: bool = True,
                    include_unverified_build: bool = False) -> list[dict]:
        """
        For FIXED tickets that are not already Done: add a "re-verified" comment and move them
        to a Done status. NEVER touches STILL REPRODUCES / COULD NOT TEST tickets. A FIXED
        verdict whose build could not be compared (`weak`) is SKIPPED (reported as such)
        unless `include_unverified_build` — a fix not tied to a deploy is not closed
        automatically. dry_run (default) only reports what would happen.
        Returns one result per FIXED, not-yet-closed ticket.
        """
        out: list[dict] = []
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        for v in verdicts:
            if v.bucket != FIXED or v.ticket.done:
                continue
            t = v.ticket
            if v.weak and not include_unverified_build:
                out.append({"key": t.key, "commented": False, "transitioned": False, "dry_run": dry_run,
                            "skipped": "the fix could not be tied to a deploy (build unverified) — "
                                       "verify by hand, or pass --include-unverified-build"})
                continue
            who = "" if t.client_safe else " (automated re-check by QAX)"
            text = (f"Re-verified on {today}{(' against ' + env) if env else ''}: the issue no longer "
                    f"reproduces{who}. Moving to Done.")
            res = {"key": t.key, "commented": False, "transitioned": False, "dry_run": dry_run,
                   "would": {"comment": text}}
            if not dry_run:
                try:
                    c = self.f.session.post(f"{self.f.base_url}/rest/api/3/issue/{t.key}/comment",
                                            json={"body": _adf(text)}, auth=(self.f.email, self.f.token),
                                            headers={"Accept": "application/json"}, timeout=30)
                    res["commented"] = c.status_code in (200, 201)
                    tid = self._done_transition(t.key)
                    if tid and res["commented"]:
                        tr = self.f.session.post(f"{self.f.base_url}/rest/api/3/issue/{t.key}/transitions",
                                                 json={"transition": {"id": tid}}, auth=(self.f.email, self.f.token),
                                                 headers={"Accept": "application/json"}, timeout=30)
                        res["transitioned"] = tr.status_code in (200, 204)
                    elif not tid:
                        res["error"] = "no transition to a Done status is available from this status"
                except Exception as exc:
                    res["error"] = str(exc)
            out.append(res)
        return out
