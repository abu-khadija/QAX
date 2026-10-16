"""
core/accepted.py — "we know about this one": accepted / by-design findings
===========================================================================
not every real finding is a defect to fix — some are by-design, config-dependent, an
accepted risk, a known duplicate or an environment artefact, and a tool that re-reports them on every
run trains people to ignore it (and makes a PR gate permanently red).

A team records those in `.qax/accepted.json` (or --accepted FILE):

    {"accepted": [
       {"probe": "cors", "target": "/", "category": "by-design",
        "reason": "Public read-only API, CORS is intentionally open", "expires": "2027-03-01",
        "owner": "alice"},
       {"fingerprint": "qax-fp-0123456789ab", "category": "accepted-risk", ...}
    ]}

Safeguards — this file can HIDE bugs, so it is deliberately strict and always visible:
  • `reason` (>= 8 chars) and `expires` (ISO date, at most 366 days ahead) are REQUIRED; an entry without
    them is rejected and reported, never silently honoured;
  • a selector is required (fingerprint, or probe [+ target] [+ title_contains]); no wildcard-everything;
  • an EXPIRED entry stops suppressing — the finding is active again, flagged as lapsed;
  • accepted findings are never dropped from the output: they are listed (with reason/owner/expiry) in a
    separate ACCEPTED block, and entries that matched nothing are reported as unused;
  • QAX never writes this file.
Pure functions, never raise.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

CATEGORIES = ("by-design", "config-dependent", "accepted-risk", "known-duplicate", "environment")
MAX_DAYS = 366
DEFAULT_PATH = ".qax/accepted.json"


def _today() -> date:
    """Today (patched in tests)."""
    return datetime.now().date()


def _parse_entry(raw, today: date) -> tuple[dict | None, str | None]:
    """(normalised entry, None) or (None, why it was rejected)."""
    if not isinstance(raw, dict):
        return None, "entry is not an object"
    reason = str(raw.get("reason") or "").strip()
    if len(reason) < 8:
        return None, "a `reason` (at least 8 characters) is required"
    category = str(raw.get("category") or "").strip().lower()
    if category not in CATEGORIES:
        return None, f"`category` must be one of {', '.join(CATEGORIES)}"
    try:
        expires = date.fromisoformat(str(raw.get("expires") or ""))
    except ValueError:
        return None, "`expires` (YYYY-MM-DD) is required"
    if (expires - today).days > MAX_DAYS:
        return None, f"`expires` is more than {MAX_DAYS} days ahead — accepted risks must be revisited at least yearly"
    fp = str(raw.get("fingerprint") or "").strip().lower()
    probe = str(raw.get("probe") or "").strip().lower()
    if not fp and not probe:
        return None, "needs a selector: `fingerprint`, or `probe` (+ optional `target`, `title_contains`)"
    return {"fingerprint": fp, "probe": probe,
            "target": str(raw.get("target") or "").strip().lower(),
            "title_contains": str(raw.get("title_contains") or "").strip().lower(),
            "category": category, "reason": reason, "expires": expires.isoformat(),
            "owner": str(raw.get("owner") or "").strip()}, None


def load(path: str | None = None, *, today: date | None = None) -> dict:
    """
    {"entries": [...valid...], "invalid": [{"entry": raw, "why": str}], "path": str|None, "error": str|None}.
    A missing DEFAULT file is not an error; an unreadable explicit file is reported (nothing is suppressed).
    """
    today = today or _today()
    explicit = path is not None
    p = Path(path or DEFAULT_PATH)
    out = {"entries": [], "invalid": [], "path": None, "error": None}
    if not p.is_file():
        if explicit:
            out["error"] = f"accepted-findings file not found: {p}"
        return out
    out["path"] = str(p)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        out["error"] = f"cannot read {p}: {exc}"
        return out
    rows = data.get("accepted") if isinstance(data, dict) else data
    if not isinstance(rows, list):
        out["error"] = f"{p}: expected {{\"accepted\": [...]}}"
        return out
    for raw in rows:
        entry, why = _parse_entry(raw, today)
        if entry:
            out["entries"].append(entry)
        else:
            out["invalid"].append({"entry": raw, "why": why})
    return out


def matches(entry: dict, finding: dict) -> bool:
    """Does this entry cover this finding? All given selector fields must agree."""
    try:
        title = str(finding.get("title") or "")
        target = str(finding.get("target") or "")
        if entry["fingerprint"]:
            from core.jira_bugs import fingerprint
            return entry["fingerprint"] in (fingerprint(title, target),
                                            fingerprint(title, target, client_safe=True))
        if entry["probe"] != str(finding.get("probe") or finding.get("source") or "").lower():
            return False
        if entry["target"] and entry["target"] != target.strip().lower():
            return False
        return not entry["title_contains"] or entry["title_contains"] in title.lower()
    except Exception:
        return False


def split(findings: list, entries: list, *, today: date | None = None) -> dict:
    """
    {"active": [...], "accepted": [{"finding", "entry"}], "lapsed": [{"finding", "entry"}], "unused": [entry]}
    `lapsed` findings are ALSO in `active` (an expired acceptance suppresses nothing).
    """
    today = today or _today()
    active, accepted, lapsed, used = [], [], [], set()
    for f in findings or []:
        hit = next((e for e in entries if isinstance(f, dict) and matches(e, f)), None)
        if hit is None:
            active.append(f)
            continue
        used.add(id(hit))
        if date.fromisoformat(hit["expires"]) < today:
            lapsed.append({"finding": f, "entry": hit})
            active.append(f)
        else:
            accepted.append({"finding": f, "entry": hit})
    unused = [e for e in entries if id(e) not in used]
    return {"active": active, "accepted": accepted, "lapsed": lapsed, "unused": unused}


def render(info: dict, loaded: dict | None = None) -> str:
    """Console/markdown-neutral text for the accepted block; '' when there is nothing to say."""
    lines = []
    if loaded and loaded.get("error"):
        lines.append(f"ACCEPTED-FINDINGS FILE PROBLEM: {loaded['error']} — nothing was suppressed.")
    for bad in (loaded or {}).get("invalid", []):
        lines.append(f"REJECTED accepted-entry ({bad['why']}): {json.dumps(bad['entry'], default=str)[:120]}")
    if info["accepted"]:
        lines.append(f"ACCEPTED — {len(info['accepted'])} known finding(s), not counted:")
        for a in info["accepted"]:
            e, f = a["entry"], a["finding"]
            lines.append(f"  • [{f.get('severity', '?')}] {f.get('title', '')}  — {e['category']}: {e['reason']}"
                         f" (until {e['expires']}{', ' + e['owner'] if e['owner'] else ''})")
    for a in info["lapsed"]:
        lines.append(f"LAPSED acceptance (expired {a['entry']['expires']}) — counted again: {a['finding'].get('title', '')}")
    for e in info["unused"]:
        lines.append(f"UNUSED accepted-entry (matched nothing — fixed? remove it): "
                     f"{e['fingerprint'] or e['probe'] + ' ' + e['target']}".rstrip())
    return "\n".join(lines)
