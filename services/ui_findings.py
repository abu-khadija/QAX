"""
services/ui_findings.py — turn Live-Tour observations into UI bug findings
==========================================================================
The Live Tour drives the real browser and records, per screen, the console/page
errors and failed API calls it saw. This converts those observations into QAX
findings — the UI equivalent of the API probes — so they flow into the scorecard.

Deterministic and conservative (don't cry wolf):
  • an UNCAUGHT page exception (pageerror) or a 5xx on a request the page made is a
    confirmed defect → P2.
  • a console.error or a 4xx on a page request is a low-confidence LEAD → P3 (it may
    be third-party noise, or an expected 401/404), reported separately.
  • console.warning is ignored entirely.

Pure logic over the tour result (a TourResult or its raw dict); no browser, no model.
"""

from __future__ import annotations


def _screens(tour_result) -> list[dict]:
    if tour_result is None:
        return []
    screens = getattr(tour_result, "screens", None)
    if screens is None and isinstance(tour_result, dict):
        screens = tour_result.get("screens")
    return [s for s in (screens or []) if isinstance(s, dict)]


def findings_from_tour(tour_result) -> list[dict]:
    """
    Build UI findings from a Live-Tour result. Each finding is a dict shaped like the
    scorecard's confirmed bugs: {title, severity, evidence, source, target}.
    """
    out: list[dict] = []
    seen: set = set()

    for s in _screens(tour_result):
        url = str(s.get("url", "")) or "(unknown screen)"

        for e in s.get("errors", []) or []:
            es = str(e)
            low = es.lower()
            if low.startswith("console.warning"):
                continue  # warnings are not defects
            if low.startswith("pageerror"):
                sev, kind = "P2", "Uncaught JavaScript error"
            elif low.startswith("console.error"):
                sev, kind = "P3", "Console error"
            else:
                continue
            key = (url, es)
            if key in seen:
                continue
            seen.add(key)
            out.append({
                "title": f"{kind} on {url}",
                "severity": sev,
                "evidence": f"The browser reported on {url}: {es}",
                "source": "ui-tour",
                "target": url,
            })

        for n in s.get("net_errors", []) or []:
            ns = str(n)
            status = ns.strip().split(" ", 1)[0]
            is_5xx = status.isdigit() and 500 <= int(status) < 600
            sev = "P2" if is_5xx else "P3"
            kind = "Server error (5xx)" if is_5xx else ("Request failure" if not status.isdigit()
                                                        else "Client error (4xx)")
            key = (url, ns)
            if key in seen:
                continue
            seen.add(key)
            out.append({
                "title": f"{kind} on a request from {url}",
                "severity": sev,
                "evidence": f"A request the page made from {url} failed: {ns}",
                "source": "ui-tour",
                "target": url,
            })

    return out
