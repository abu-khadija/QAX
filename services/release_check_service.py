"""
services/release_check_service.py — run a release check: select → run → scan → decide
======================================================================================
The one call behind `qax release-check`, the web UI and the MCP tool. Each part is an existing,
benchmarked engine; this only runs them in order and hands the results to core.release_check.decide:

  1. which cases the change can affect (core.change_selection; every case when no change is given)
  2. those cases in a real browser (ExecutionService, the same engine the benchmark scores)
  3. the quick security scan of the same app — read-only unless the caller allows writes
  4. the decision, a Markdown report (PR-comment ready) and a JSON record under output/release/

Never raises: a part that cannot run becomes NOT PROVEN, never a pass.
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("qax.release")

_ENV_KEYS = ("APP_BASE_URL", "DEFAULT_PROJECT", "QAX_AI_HEAL", "AUTO_BUG_REPORT", "QAX_A11Y", "QAX_PAGE_QUALITY",
             "QAX_BROWSER", "QAX_DEVICE", "QAX_STEP_SCREENSHOTS")


def run_release_check(cases: list, base_url: str, project: str = "", *, repo: str = "", changed_since: str = "",
                      scan: bool = True, scan_read_only: bool = True, fail_on: str = "P1", a11y: str = "basic",
                      a11y_fail_on: str = "", browser: str = "chromium", device: str = "desktop",
                      out_dir: "str | None" = None, progress=None) -> dict:
    """
    Run the release check and write its report.

    Args:
        cases: test cases (browser cases with automation_steps).
        base_url: the running app.
        project: the QAX project (selectors / app map / history).
        repo, changed_since: the app's git checkout and the base ref — run only what the change can affect.
        scan: also run the quick security scan of base_url.
        scan_read_only: the scan sends no data-changing requests (default; staging may allow writes).
        fail_on: lowest confirmed security severity that blocks ("P1").
        a11y: "basic" | "axe" | "off" — page checks while the cases run.
        a11y_fail_on: "" (observations only) or an axe impact that blocks ("critical", "serious").
        out_dir: where release.json / release.md go (default output/release/<time>).
        progress: optional callable(str) for live lines.

    Returns:
        dict: the decision (core.release_check.decide) + "report_md", "files", "duration_s".
    """
    from core.release_check import decide, render_markdown
    say = progress or (lambda m: logger.info("QAX release: %s", m))
    t0 = time.monotonic()

    # 1 · selection
    selection = None
    if changed_since and repo:
        try:
            from core.change_selection import changed_files, select
            from core.paths import projects_dir
            files, err = changed_files(repo, changed_since, "HEAD")
            if err:
                selection = {"run_all": True, "why": f"could not read the change ({err}) — running every case",
                             "selected": [{"id": str(c.get("test_case_id"))} for c in cases], "skipped": [], "files": 0}
            else:
                selection = select(cases, files, repo, projects_dir() / (project or "_none_"))
                selection["files"] = len(files)
            keep = {x["id"] for x in selection["selected"]}
            cases = [c for c in cases if str(c.get("test_case_id", "?")) in keep]
            say(selection["why"])
        except Exception as exc:  # noqa: BLE001
            say(f"change selection failed ({exc}) — running every case")
            selection = None

    # 2 · functional run
    functional = None
    if cases:
        say(f"running {len(cases)} case(s) in {browser} ({device})")
        saved = {k: os.environ.get(k) for k in _ENV_KEYS}
        try:
            os.environ.update(APP_BASE_URL=base_url, QAX_AI_HEAL="false", AUTO_BUG_REPORT="false",
                              QAX_STEP_SCREENSHOTS="true", QAX_BROWSER=browser, QAX_DEVICE=device,
                              QAX_PAGE_QUALITY="false" if a11y == "off" else "true",
                              QAX_A11Y="axe" if a11y == "axe" else "basic")
            if project:
                os.environ["DEFAULT_PROJECT"] = project
            from services.execution_service import ExecutionService
            res = ExecutionService().execute(cases, mode="ui", headless=True)
            functional = res.raw or {"results": []}
            if res.error and not functional.get("results"):
                functional = None
                say(f"the browser run did not start: {res.error}")
        except Exception as exc:  # noqa: BLE001
            say(f"the browser run failed: {exc}")
            functional = None
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    # 3 · security scan (quick, deterministic confirmed findings)
    scan_out = None
    if scan:
        say("security scan (" + ("read-only" if scan_read_only else "may send data-changing requests") + ")")
        scan_out = _quick_scan(base_url, scan_read_only)

    # 4 · decide, record
    q = set()
    if project:
        try:
            from core.run_history import quarantine, save_run
            q = set(quarantine(project))
            if functional and functional.get("results"):
                save_run(project, base_url, functional["results"], selection=selection)
        except Exception:  # noqa: BLE001
            pass
    d = decide(functional, selection=selection, scan=scan_out, scan_requested=scan, quarantined=q,
               fail_on=fail_on, a11y_fail_on=a11y_fail_on)
    d["duration_s"] = round(time.monotonic() - t0, 1)
    d["report_md"] = render_markdown(d, app_url=base_url, change=changed_since)
    d["files"] = _write(d, functional, scan_out, out_dir)
    briefs = _write_briefs(d, functional, cases, base_url, project, repo)
    if briefs:
        d["files"]["fix_briefs"] = briefs
        d["report_md"] += ("\n\n**Fix briefs for your coding agent** (steps, evidence, where to look, how to verify): "
                           + ", ".join(f"`{Path(b).name}`" for b in briefs) + "\n")
        try:
            Path(d["files"]["markdown"]).write_text(d["report_md"], encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass
    say(f"verdict: {d['verdict']}")
    return d


def _write_briefs(d: dict, functional, cases: list, base_url: str, project: str, repo: str) -> list:
    """One fix brief (core.fix_brief) per functional blocker, next to the report. Never raises."""
    try:
        if not d.get("files", {}).get("json"):
            return []
        from core.fix_brief import build
        by_id = {str(c.get("test_case_id")): c for c in cases or []}
        res = {str(r.get("test_case_id")): r for r in ((functional or {}).get("results") or [])}
        folder = Path(d["files"]["json"]).parent / "briefs"
        out = []
        for b in d.get("blockers") or []:
            if b.get("kind") != "functional" or b.get("id") not in res:
                continue
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / f"{b['id']}.md"
            path.write_text(build(res[b["id"]], by_id.get(b["id"]), base_url=base_url, project=project, repo=repo), encoding="utf-8")
            out.append(str(path))
        return out
    except Exception as exc:  # noqa: BLE001
        logger.warning("QAX release: could not write fix briefs: %s", exc)
        return []


def _quick_scan(base_url: str, read_only: bool) -> dict:
    """The quick scan of the running app: confirmed (deterministic) findings, leads, gaps; never raises."""
    import shutil
    import tempfile
    # An EMPTY folder, never "": Path("") is the current directory, and the orchestrator would model that
    # source with the AI (a model call that needs a key and sends code off the machine). A quick scan reads
    # the live app only, like `qax scan --quick` and the web UI do.
    nosrc = tempfile.mkdtemp(prefix="qax_nosrc_")
    try:
        from services.context import QAXContext
        from services.orchestrator_service import OrchestratorService
        result = OrchestratorService(QAXContext.from_env()).run(nosrc, base_url, deep=False, tour=False,
                                                               read_only=read_only)
        if result.error:
            return {"error": result.error}
        return {"claims": list(result.confirmed_deterministic), "leads": list(result.leads_ai),
                "coverage_gaps": list(result.coverage_gaps)}
    except Exception as exc:  # noqa: BLE001
        from core.public_error import clean
        return {"error": clean(str(exc)) or type(exc).__name__}
    finally:
        shutil.rmtree(nosrc, ignore_errors=True)


def _write(d: dict, functional, scan_out, out_dir) -> dict:
    """release.json (decision + raw results, no secrets: steps are already masked) and release.md."""
    try:
        from core.paths import output_dir
        base = Path(out_dir) if out_dir else output_dir() / "release" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        base.mkdir(parents=True, exist_ok=True)
        rec = {k: v for k, v in d.items() if k != "report_md"}
        rec["functional_results"] = (functional or {}).get("results", [])
        rec["scan"] = scan_out
        (base / "release.json").write_text(json.dumps(rec, indent=2, default=str), encoding="utf-8")
        (base / "release.md").write_text(d["report_md"], encoding="utf-8")
        return {"json": str(base / "release.json"), "markdown": str(base / "release.md")}
    except Exception as exc:  # noqa: BLE001
        logger.warning("QAX release: could not write the report: %s", exc)
        return {}
