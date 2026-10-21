"""
core/release_check.py — one decision per change: GO, NO-GO or NOT PROVEN, with the proof
=========================================================================================
The release check joins what QAX already measures into the one answer a team needs before it
merges or ships: the functional cases a change can affect (core.change_selection), run in a real
browser; the security scan of the same app (deterministic, confirmed findings only); and what every
page showed on the way (accessibility, performance, page errors — observations).

    GO          every case that ran passed, no confirmed security bug at or above the bar, and
                everything the decision depends on actually ran.
    NO-GO       a case failed because of the app (cause app/unclear, not quarantined), or the scan
                confirmed a security bug at or above the bar (default P1).
    NOT PROVEN  nothing failed, but something the decision depends on did not run to a verdict
                (a case ERROR/SKIP, the scan could not run, no case was selected) — QAX does not
                turn "could not check" into "fine".

Deterministic: no AI decides anything here. Never raises. The verdict says what it did NOT cover.
"""

from __future__ import annotations

_SEV_ORDER = {"P1": 1, "P2": 2, "P3": 3, "P4": 4}
EXIT = {"GO": 0, "NO-GO": 1, "NOT PROVEN": 2}


def _sev_at_or_above(sev: str, bar: str) -> bool:
    """P1 is above P2: 'P2' meets bar 'P2' and bar 'P3', not bar 'P1'."""
    return _SEV_ORDER.get(str(sev).upper(), 9) <= _SEV_ORDER.get(str(bar).upper(), 1)


def decide(functional: "dict | None", *, selection: "dict | None" = None, scan: "dict | None" = None,
           scan_requested: bool = False, quarantined: "set | None" = None, fail_on: str = "P1",
           a11y_fail_on: str = "") -> dict:
    """
    The release decision from measured results.

    Args:
        functional: the batch result ({"results": [...]}) or None when no case ran.
        selection: core.change_selection.select() output (None = every case was asked to run).
        scan: {"claims": [...confirmed, deterministic...], "leads": [...], "coverage_gaps": [...],
               "error": str} or None when no scan ran.
        scan_requested: the scan was part of this check (its absence then matters).
        quarantined: case ids that run and are shown but never block (known flaky).
        fail_on: lowest confirmed security severity that blocks (P1 default).
        a11y_fail_on: "" (accessibility never blocks) or an axe impact ("critical", "serious") that does.

    Returns:
        dict: {"verdict", "exit_code", "reasons": [...], "blockers": [...], "not_proven": [...],
               "not_covered": [...], "counts": {...}, "security": {...}, "observations": {...}}
    """
    q = set(quarantined or ())
    results = [r for r in ((functional or {}).get("results") or []) if isinstance(r, dict)]
    blockers, not_proven, not_covered, reasons = [], [], [], []

    # 1 · functional
    counts = {"run": len(results), "passed": 0, "failed": 0, "not_run": 0, "quarantined": 0}
    for r in results:
        cid, st, cause = str(r.get("test_case_id")), r.get("status"), r.get("cause") or ""
        title = str(r.get("title") or "")[:100]
        if cid in q:
            counts["quarantined"] += 1
            continue
        if st in ("PASS",):
            counts["passed"] += 1
        elif st == "FAIL" and cause not in ("test", "environment"):
            counts["failed"] += 1
            blockers.append({"kind": "functional", "id": cid, "title": title, "cause": cause or "app",
                             "why": _first_failure(r)})
        else:
            counts["not_run"] += 1
            not_proven.append({"kind": "functional", "id": cid, "title": title, "status": st,
                               "cause": cause or "test", "why": _first_failure(r) or r.get("error") or ""})
    if functional is None or not results:
        not_proven.append({"kind": "functional", "id": "—", "title": "no functional case ran",
                           "status": "NOT RUN", "cause": "selection" if selection else "input",
                           "why": (selection or {}).get("why", "no cases were given")})
    if selection and not selection.get("run_all"):
        for s in selection.get("skipped") or []:
            not_covered.append({"kind": "functional", "id": s.get("id"), "why": s.get("reason", "")})

    # 2 · security (confirmed, deterministic findings only — leads never block)
    sec = {"ran": False, "confirmed": 0, "blocking": 0, "gaps": 0, "leads": 0}
    if scan is not None:
        if scan.get("error"):
            not_proven.append({"kind": "security", "id": "scan", "title": "the security scan did not finish",
                               "status": "ERROR", "cause": "environment", "why": str(scan["error"])[:200]})
        else:
            sec["ran"] = True
            claims = [c for c in (scan.get("claims") or []) if isinstance(c, dict)]
            sec.update(confirmed=len(claims), gaps=len(scan.get("coverage_gaps") or []),
                       leads=len(scan.get("leads") or []))
            for c in claims:
                if _sev_at_or_above(c.get("severity", ""), fail_on):
                    sec["blocking"] += 1
                    blockers.append({"kind": "security", "id": str(c.get("severity", "")),
                                     "title": str(c.get("title", ""))[:120], "cause": "app",
                                     "why": str(c.get("target") or c.get("endpoint") or "")[:120]})
            for g in (scan.get("coverage_gaps") or [])[:20]:
                not_covered.append({"kind": "security", "id": str(g.get("probe") or g.get("kind") or "check"),
                                    "why": str(g.get("reason") or g.get("kind") or "")[:160]})
    elif scan_requested:
        not_proven.append({"kind": "security", "id": "scan", "title": "the security scan was asked for but did not run",
                           "status": "NOT RUN", "cause": "environment", "why": ""})

    # 3 · page observations (never block unless asked)
    obs = _observations(results)
    if a11y_fail_on:
        bar = {"minor": 4, "moderate": 3, "serious": 2, "critical": 1}.get(a11y_fail_on.lower(), 1)
        rank = {"minor": 4, "moderate": 3, "serious": 2, "critical": 1}
        for issue in obs["accessibility"]:
            if rank.get(str(issue.get("impact", "")).lower(), 9) <= bar:
                blockers.append({"kind": "accessibility", "id": issue.get("wcag", ""),
                                 "title": issue.get("rule", ""), "cause": "app",
                                 "why": f"{issue.get('page', '')} {issue.get('target', '')}".strip()})

    if blockers:
        verdict = "NO-GO"
        reasons.append(f"{len(blockers)} blocking problem(s): "
                       + "; ".join(f"{b['kind']} {b['id']}" for b in blockers[:5]))
    elif not_proven:
        verdict = "NOT PROVEN"
        reasons.append(f"nothing failed, but {len(not_proven)} thing(s) the decision depends on did not run to a verdict")
    else:
        verdict = "GO"
        reasons.append(f"{counts['passed']} case(s) passed" + (", the security scan confirmed nothing at or above "
                       + fail_on if sec["ran"] else ""))
    return {"verdict": verdict, "exit_code": EXIT[verdict], "reasons": reasons, "blockers": blockers,
            "not_proven": not_proven, "not_covered": not_covered, "counts": counts, "security": sec,
            "observations": obs, "fail_on": fail_on,
            "selection": {"run_all": bool((selection or {}).get("run_all", True)),
                          "why": (selection or {}).get("why", "every case was run"),
                          "files": (selection or {}).get("files", 0)}}


def _first_failure(r: dict) -> str:
    """The first failing step's observed result, short."""
    for s in r.get("steps") or []:
        if s.get("status") not in ("PASS", "HEALED"):
            return f"step {s.get('step_number', '?')}: " + str(s.get("actual_result") or s.get("error") or "")[:160]
    return str(r.get("error") or "")[:160]


def _observations(results: list) -> dict:
    """Accessibility issues, slow pages and page errors seen by the cases (deduplicated by page)."""
    seen, a11y, slow, errors = set(), [], [], 0
    for r in results:
        errors += len(r.get("page_errors") or [])
        for p in ((r.get("quality") or {}).get("pages") or []):
            if p.get("url") in seen:
                continue
            seen.add(p.get("url"))
            for i in p.get("accessibility") or []:
                a11y.append(dict(i, page=p.get("url")))
            if p.get("slow"):
                slow.append({"page": p.get("url"), "metrics": p.get("slow")})
    return {"accessibility": a11y, "slow_pages": slow, "page_errors": errors, "pages": len(seen)}


def render_markdown(d: dict, *, app_url: str = "", change: str = "") -> str:
    """The decision as a short Markdown report — ready to post as a PR comment."""
    icon = {"GO": "✅", "NO-GO": "⛔", "NOT PROVEN": "⚠️"}[d["verdict"]]
    c, sec, obs = d["counts"], d["security"], d["observations"]
    lines = [f"## {icon} QAX release check: **{d['verdict']}**", ""]
    if app_url or change:
        lines += [f"App: `{app_url}`" + (f" · change since `{change}`" if change else ""), ""]
    lines += [f"- {r}" for r in d["reasons"]]
    lines += ["", "| | |", "|---|---|",
              f"| Functional cases | {c['passed']} passed · {c['failed']} failed · {c['not_run']} did not run"
              + (f" · {c['quarantined']} quarantined" if c["quarantined"] else "") + " |",
              f"| Which cases | {d['selection']['why']} |",
              ("| Security scan | " + (f"{sec['confirmed']} confirmed · {sec['blocking']} at or above {d['fail_on']} · "
                                      f"{sec['gaps']} check(s) not run" if sec["ran"] else "not run") + " |"),
              f"| Pages checked | {obs['pages']} · {len(obs['accessibility'])} accessibility issue(s) · "
              f"{len(obs['slow_pages'])} slow · {obs['page_errors']} page error(s) |", ""]
    if d["blockers"]:
        lines += ["### Blocking", ""] + [f"- **{b['kind']} {b['id']}** {b['title']} — {b['why']}" for b in d["blockers"][:15]] + [""]
    if d["not_proven"]:
        lines += ["### Did not run to a verdict (not counted as passed)", ""] + \
                 [f"- {n['kind']} {n['id']} {n['title']} — {n['status']}, {n['cause']}: {n['why']}" for n in d["not_proven"][:15]] + [""]
    if d["not_covered"]:
        lines += ["<details><summary>Not covered by this check "
                  f"({len(d['not_covered'])})</summary>", ""] + \
                 [f"- {n['kind']} {n['id']}: {n['why']}" for n in d["not_covered"][:40]] + ["", "</details>", ""]
    lines.append("<sub>Verdicts come from deterministic checks in a real browser and confirmed probes — no AI decides "
                 "a pass, a fail or a vulnerability. Accessibility and performance are observations.</sub>")
    return "\n".join(lines)
