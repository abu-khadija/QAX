"""
web/functional.py — the functional-QA (E2E) routes of the web UI
================================================================
Reading the app, understanding all inputs and writing traced cases
(/api/functional/analyze), recorded sessions for SSO / 2FA apps (/api/session), the app
map (/api/map) and filing the failures as Jira bugs (/api/functional/bugs). Moved out of
web/server.py; the server registers them with its gates and helpers (register()).
"""

from __future__ import annotations

import os

from flask import jsonify, request


def _map_locked(*args, **kwargs):
    """Read the app under the server's browser lock: the reader sets APP_* process variables
    (login, base URL), so a concurrent browser run must not see them (or vice versa)."""
    import asyncio

    from core.app_mapper import map_app
    with _BROWSER_LOCK:
        return asyncio.run(map_app(*args, **kwargs))


def _repo_source(repo: str) -> "tuple[str, str]":
    """
    A source repository for the Understand step: a local folder, or an https Git URL
    cloned shallowly into output/repos/. Returns (folder, error). No credentials in
    URLs (they would be logged); only http(s); never raises.
    """
    import re as _re
    import subprocess
    from pathlib import Path
    from urllib.parse import urlparse
    repo = (repo or "").strip()
    if not repo:
        return "", ""
    if not repo.startswith(("http://", "https://")):
        path = Path(repo).expanduser().resolve()
        # a folder's files go to the AI provider: never a hidden folder (~/.ssh, ~/.aws …); with
        # QAX_REPO_ROOTS set, only folders under those roots
        if any(part.startswith(".") for part in path.parts[1:]):
            return "", "Hidden folders (e.g. ~/.ssh) are never read as a repository."
        roots = [Path(r).expanduser().resolve() for r in os.getenv("QAX_REPO_ROOTS", "").split(",") if r.strip()]
        if roots and not any(path.is_relative_to(r) for r in roots):
            return "", "That folder is outside QAX_REPO_ROOTS."
        return (str(path), "") if path.is_dir() else ("", f"Repository folder not found: {repo}")
    if not repo.startswith("https://"):
        return "", "Use an https:// repository URL (or a local folder)."
    u = urlparse(repo)
    if "@" in u.netloc:
        return "", "Remove the credentials from the repository URL (they would end up in logs)."
    slug = _re.sub(r"[^A-Za-z0-9_.-]+", "_", (u.netloc + u.path).strip("/"))[:80] or "repo"
    dest = Path(__file__).resolve().parent.parent / "output" / "repos" / slug
    if dest.is_dir():
        return str(dest), ""
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        r = subprocess.run(["git", "-c", "protocol.file.allow=never", "-c", "protocol.ext.allow=never", "-c", "http.followRedirects=false",
                            "clone", "--depth", "1", "--quiet", "--", repo, str(dest)],
                           capture_output=True, text=True, timeout=180,
                           env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
        if r.returncode != 0:
            return "", f"Could not clone the repository: {(r.stderr or '').strip()[:200]}"
        return str(dest), ""
    except Exception as exc:  # noqa: BLE001
        return "", f"Could not clone the repository: {str(exc)[:200]}"


def functional_analyze():
    """
    Functional QA, steps 1-3: take every input the user has — the running app's URL, a
    source repository (folder or https Git URL), a user story / acceptance criteria, code —
    understand them (read the app, understand the repo, number the acceptance criteria),
    then write test cases grounded in all of it, each traced to a criterion and a technique.
    Returns {understanding, tests, invalid, coverage, error}. Reading the app needs the
    authorization checkbox; the login values are used for this read only.
    """
    import asyncio
    data = request.get_json(force=True, silent=True) or {}
    url = str(data.get("url", "")).strip().rstrip("/")
    story = str(data.get("story", "")).strip()
    code = str(data.get("code", "")).strip()
    repo = str(data.get("repo", "")).strip()
    project = _clean_project(data.get("project")) or "app"
    story_key = ""
    import re as _re
    if _re.fullmatch(r"[A-Z][A-Z0-9_]+-\d+", story.upper()):
        # a Jira story key: read the story (read-only) — its verdicts go back to it later
        story_key = story.upper()
        try:
            from core.input_adapters.jira_adapter import JiraInputAdapter
            items = JiraInputAdapter().fetch(story_key)
        except Exception:  # noqa: BLE001
            items = []
        story = str((items[0] or {}).get("content", "")).strip() if items else ""
        if not story:
            return jsonify({"error": f"Could not read {story_key} from Jira (check JIRA_* in .env), "
                                     "or paste the story text instead."}), 400

    def _on(v) -> bool:
        return str(v).lower() in ("1", "true", "yes", "on")
    if not (url or story or code or repo):
        return jsonify({"error": "Give QAX something to test: the app URL, a repository, a user story or code."}), 400
    understanding: dict = {"project": project, "pages": [], "criteria": [], "repo": None, "notes": [],
                           "story_key": story_key}
    app_map = None

    # 1 · the running app, read from its URL
    if url:
        if not url.startswith(("http://", "https://")):
            return jsonify({"error": "The app URL must start with http:// or https://."}), 400
        if not _target_authorized():
            return jsonify({"error": _UNAUTHORIZED_TARGET}), 400
        from core.app_mapper import map_app, save_map
        from core.session_state import state_for
        session = None if data.get("username") else state_for(project)
        if session:
            understanding["notes"].append("Using the session you recorded for this project (SSO / 2FA login).")
        app_map = _map_locked(url, username=str(data.get("username", "")),
                                      password=str(data.get("password", "")),
                                      insecure=_on(data.get("insecure", "")),
                                      explore_actions=_on(data.get("explore_actions", "")),
                                      storage_state=session)
        if app_map.error:
            understanding["notes"].append(f"Could not read the app: {app_map.error}")
            app_map = None
        else:
            with _BROWSER_LOCK:       # a browser run may be reading selectors.json
                save_map(app_map, project)
            understanding.update(
                pages=[{"path": p["path"], "elements": len(p.get("elements") or {})} for p in app_map.pages],
                login_ok=app_map.login_ok, skipped_links=app_map.skipped_links,
                explored_actions=app_map.explored_actions,
                writes=len([w for w in app_map.writes if not w.get("login")]))

    # 2 · the source repository
    repo_context = None
    if repo:
        folder, err = _repo_source(repo)
        if err:
            understanding["notes"].append(err)
        else:
            try:
                from services.comprehension_service import RepoComprehensionService
                from services.context import QAXContext
                comp = RepoComprehensionService(QAXContext.from_env()).comprehend(folder, input_type="folder")
                if getattr(comp, "error", None):
                    understanding["notes"].append(_ai_error_message(comp.error).replace(
                        "Generation failed", "Could not understand the repository"))
                else:
                    repo_context = comp.as_context()
                    understanding["repo"] = {"folder": folder, "overview": getattr(comp, "overview", ""),
                                             "risk_areas": getattr(comp, "risk_areas", [])}
            except Exception as exc:  # noqa: BLE001
                understanding["notes"].append(_ai_error_message(exc).replace(
                    "Generation failed", "Could not understand the repository"))

    # 3 · the requirements: numbered acceptance criteria
    from core.traceability import (coverage, criteria_block, extract_criteria, security_block,
                                   security_risks)
    criteria = extract_criteria(story)
    understanding["criteria"] = criteria
    risks = security_risks(criteria)
    understanding["security_risks"] = risks
    if not (story or code):
        story = ("Explore and test the main user flows of this application: what a user can create, "
                 "change, delete and see, including invalid input and permissions.")
        understanding["notes"].append("No user story given — QAX wrote an exploratory charter from what it read.")

    # 4 · test cases, grounded in all of the above
    try:
        from services.context import QAXContext
        from services.exceptions import QAXConfigError
        from services.generation_service import GenerationOptions, GenerationService
        from services.progress import NullProgressReporter
        ctx = "\n\n".join(x for x in (repo_context, criteria_block(criteria), security_block(risks)) if x) or None
        opts = GenerationOptions(user_story=story, content=code or story, app_map=app_map,
                                 repo_context=ctx, test_type=str(data.get("test_type") or "") or None)
        try:
            res = GenerationService(QAXContext.from_env()).generate(
                "code://web-input" if code else "story://web-input", options=opts,
                progress=NullProgressReporter())
        except QAXConfigError as exc:
            return jsonify({"understanding": understanding, "tests": [], "error": str(exc)})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"understanding": understanding, "tests": [], "error": _ai_error_message(exc)})
    if res.error:
        return jsonify({"understanding": understanding, "tests": [], "error": _ai_error_message(res.error)})
    tests = res.test_cases or []
    from core.project_overview import save_requirements
    cov = coverage(tests, criteria)
    if criteria:
        save_requirements(project, criteria, cov, story_key)
    return jsonify({"understanding": understanding, "tests": tests, "invalid": len(res.invalid or []),
                    "summary": res.summary or {}, "coverage": cov})


def functional_bugs():
    """
    Functional QA, step 5: the failed cases of a run as Jira bugs (ISTQB defect-report
    shape, evidence included). Same live-gate as every export: a dry-run preview unless
    the operator enabled QAX_ALLOW_LIVE_EXPORT on the server AND the caller asked.
    """
    import re as _re
    data = request.get_json(force=True, silent=True) or {}
    results = [r for r in (data.get("results") or []) if isinstance(r, dict) and r.get("status") == "FAIL"]
    story_key = str(data.get("story_key", "")).strip().upper()
    if story_key and not _re.fullmatch(r"[A-Z][A-Z0-9_]+-\d+", story_key):
        return jsonify({"error": f"'{story_key}' is not a Jira issue key (like PROJ-123)."}), 400
    if not results and not story_key:
        return jsonify({"error": "No failed case to report."}), 400
    cases = {str(t.get("test_case_id")): t for t in (data.get("tests") or []) if isinstance(t, dict)}
    allow_live = os.getenv("QAX_ALLOW_LIVE_EXPORT", "false").lower() == "true"
    dry_run = True if not allow_live else (data.get("dry_run", True) is not False)
    from core.jira_bugs import JiraBugFiler, functional_result_to_bug
    url = str(data.get("url", "")).strip()
    lang = "ar" if str(data.get("lang", "")).lower().startswith("ar") else "en"
    bugs = [dict(functional_result_to_bug(r, cases.get(str(r.get("test_case_id"))), base_url=url, lang=lang),
                 **({"story_key": story_key} if story_key else {}))
            for r in results]
    try:
        filer = JiraBugFiler(dry_run=dry_run, project_key=str(data.get("project_key", "")).strip() or None)
        out = filer.file_many(bugs) if bugs else {"success": True, "results": [], "references": []}
        out = dict(out or {})
        # the story gets the requirement verdicts as a comment (dry run: preview)
        criteria = data.get("criteria") or []
        if story_key and criteria:
            from core.traceability import requirement_verdicts, verdict_comment
            verdicts = requirement_verdicts(criteria, data.get("coverage") or {}, data.get("all_results") or results)
            text = verdict_comment(verdicts, url, [r.get("jira_key") for r in out.get("results", []) if r.get("jira_key")])
            out["story_comment"] = dict(filer.comment(story_key, text), story_key=story_key, text=text)
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": f"Bug filing failed: {exc}"}), 502
    out["dry_run"] = dry_run
    if bugs and not dry_run:
        # every bug filed becomes a regression case: re-checked on every later run
        from core.run_history import add_regression_cases
        keys = {r.get("test_case_id"): r.get("jira_key") for r in out.get("results", []) if r.get("jira_key")}
        filed = [cases[str(r.get("test_case_id"))] for r in results if str(r.get("test_case_id")) in cases]
        out["regression_suite"] = add_regression_cases(
            _clean_project(data.get("project")) or "default", filed, keys)
    out["bugs"] = [{"test_case_id": b["test_case_id"], "title": b["title"], "text": b["description"]}
                   for b in bugs]
    return jsonify(out)


def session_route():
    """
    Log in by hand once (SSO, 2FA, CAPTCHA): action "record" opens a visible browser at the
    app on THIS machine and keeps the session when the operator closes the window; "status"
    says whether one is saved; "forget" deletes it. The session file is never sent back.
    """
    import asyncio

    from core.session_state import forget, has_state, record_session, saved_at
    data = request.get_json(force=True, silent=True) or {}
    project = _clean_project(data.get("project"))
    action = str(data.get("action", "status"))
    if not project:
        return jsonify({"error": "Enter a project name first."}), 400
    if action == "forget":
        return jsonify({"forgotten": forget(project), "saved": False})
    if action == "record":
        url = str(data.get("url", "")).strip()
        if not url.startswith(("http://", "https://")):
            return jsonify({"error": "The app URL must start with http:// or https://."}), 400
        if not _target_authorized():
            return jsonify({"error": _UNAUTHORIZED_TARGET}), 400
        if os.getenv("QAX_IN_CONTAINER"):
            return jsonify({"error": "A browser window cannot open from inside the container — run "
                                     "`python main.py login --url … --project …` on your machine."}), 400
        ok, msg = asyncio.run(record_session(url, project,
                                             insecure=str(data.get("insecure", "")).lower() in ("1", "true", "on")))
        return (jsonify({"saved": ok, "message": msg, "when": saved_at(project)}), 200 if ok else 502)
    return jsonify({"saved": has_state(project), "when": saved_at(project)})


def map_app_route():
    """
    Read a running app from its URL (functional QA, step 1): GET links only, logout/delete
    style links never followed, no form submitted; optional login. Saves the project's
    app map + selectors, which generation and the browser engine then use. The login
    values are used for this run only — never stored, never logged.
    """
    import asyncio
    data = request.get_json(force=True, silent=True) or {}
    url = str(data.get("url", "")).strip().rstrip("/")
    project = _clean_project(data.get("project"))
    if not url.startswith(("http://", "https://")):
        return jsonify({"error": "Enter the URL of your running app (http:// or https://)."}), 400
    if not project:
        return jsonify({"error": "Give the project a name (letters, digits, - and _)."}), 400
    if not _target_authorized():
        return jsonify({"error": _UNAUTHORIZED_TARGET}), 400
    from core.app_mapper import map_app, save_map
    try:
        max_pages = max(1, min(int(data.get("max_pages") or 20), 60))
    except (TypeError, ValueError):
        max_pages = 20
    def _on(v) -> bool:
        return str(v).lower() in ("1", "true", "yes", "on")
    from core.session_state import state_for
    result = _map_locked(url, max_pages=max_pages,
                                 username=str(data.get("username", "")),
                                 password=str(data.get("password", "")),
                                 insecure=_on(data.get("insecure", "")),
                                 explore_actions=_on(data.get("explore_actions", "")),
                                 storage_state=None if data.get("username") else state_for(project))
    if result.error:
        return jsonify({"error": result.error}), 502
    with _BROWSER_LOCK:
        save_map(result, project)
    return jsonify({"project": project, "pages": [
        {"path": p["path"], "elements": len(p.get("elements") or {})} for p in result.pages],
        "login_ok": result.login_ok, "skipped_links": result.skipped_links,
        "explored_actions": result.explored_actions, "writes": len([w for w in result.writes if not w.get("login")]),
        "revealed": sum(len(p.get("opened_by") or {}) for p in result.pages)})


def overview_route():
    """One project's quality from what QAX measured (core.project_overview) — no invented numbers."""
    from core.app_mapper import CONFIG_DIR
    from core.project_overview import build_overview
    project = _clean_project(request.args.get("project", ""))
    if not project:
        return jsonify({"error": "Enter a project name."}), 400
    return jsonify(build_overview(project, CONFIG_DIR, _RUNS_DIR))


def register(app, server) -> None:
    """Attach the routes to the server's app, with the server's gates and helpers."""
    g = globals()
    for name in ("_clean_project", "_target_authorized", "_UNAUTHORIZED_TARGET", "_ai_error_message", "_BROWSER_LOCK", "_RUNS_DIR"):
        g[name] = getattr(server, name)
    app.post("/api/functional/analyze")(functional_analyze)
    app.get("/api/overview")(overview_route)
    app.post("/api/functional/bugs")(functional_bugs)
    app.post("/api/session")(session_route)
    app.post("/api/map")(map_app_route)
