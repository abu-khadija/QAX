"""
services/orchestrator_service.py — OrchestratorService (Phase 4: the conductor)
===============================================================================
The whole agent team, run end to end from one call, with a coverage loop so the
result doesn't depend on a lucky generation. This is what makes QAX an autonomous
QA engineer rather than a pile of services a human has to wire together each time.

Pipeline:
    AppModel → Detectors → (functional generation + Red-Team) → coverage check
    → fill gaps with guaranteed templates → Execute → Critic → Investigator
    → + active Probes → unified confirmed bugs.

The coverage loop is the point. QAX kept catching the pagination boundary bug only
~2/3 of the time because generation sometimes picked an aligned count. The
orchestrator derives the high-value coverage goals from the model, checks whether
the generated tests actually meet them, and for any critical gap it injects a
DETERMINISTIC template test — so a known-critical goal is covered every run, not by
luck. Functional generation (deep mode) is budgeted: it ranks the router files by
risk (Detector suspects + git churn + endpoint surface, not the file name alone) and
spends an ENDPOINT budget on them highest-risk-first (bounded by a file ceiling),
recording every router it covered or SKIPPED in the coverage map — so the report
never implies a router was tested when it wasn't. (The probes and coverage templates
always run over the whole endpoint map, independent of this budget.)

Never prints, never calls sys.exit(); failures are captured in the result.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from services.context import QAXContext
from services.progress import NullProgressReporter, ProgressReporter
from services.results import AppModel, OrchestratorResult

logger = logging.getLogger(__name__)


class OrchestratorService:
    """Conduct the full QAX agent team over a repo + live app, with coverage goals."""

    def __init__(self, context: QAXContext | None = None) -> None:
        """
        Args:
            context (QAXContext | None): Config/credentials. Built from the
                environment when None.
        """
        self.context = context or QAXContext.from_env()

    def run(
        self,
        repo_path: str,
        base_url: str,
        *,
        deep: bool = True,
        max_router_files: int = 4,
        max_endpoints: int = 40,
        use_cache: bool = True,
        auth: dict | None = None,
        tour: bool = False,
        tour_login: dict | None = None,
        visual_baseline: str | None = None,
        progress: ProgressReporter | None = None,
    ) -> OrchestratorResult:
        """
        Run the autonomous pipeline over a repo against a live app.

        Args:
            repo_path (str): Repository root to model and test.
            base_url (str): The running app to execute/probe against.
            deep (bool): Full run when True. When False ("quick scan"), skip the
                LLM-heavy passes — the knowledge-pass, per-router functional
                generation, and red-team — and rely on the model, the static
                detectors, the active probes, and the deterministic coverage
                templates. A quick scan makes ~1 model call (or 0 when cached) yet
                still proves the exploit probes and the guaranteed security classes.
            max_router_files (int): Hard ceiling on router files for functional
                generation (safety bound on a pathological repo).
            max_endpoints (int): The real generation budget — routers are chosen
                highest-risk-first until this many endpoints are covered (budget by
                work, not a flat file count), bounded by max_router_files. Routers
                are ranked by Detector suspects + git churn + endpoint surface, not
                the file name alone.
            use_cache (bool): Reuse a cached AppModel for unchanged source.
            progress (ProgressReporter | None): Progress sink.

        Returns:
            OrchestratorResult: Unified confirmed bugs + coverage. Never raises at
                runtime.

        Raises:
            QAXConfigError: If the Anthropic API key is missing and a fresh model
                (or an LLM pass) is needed.
        """
        prog = progress or NullProgressReporter()

        from services.appmodel_service import AppModelService
        from services.critic_service import CriticService
        from services.detector_service import DetectorService
        from services.execution_service import ExecutionService
        from services.generation_service import GenerationOptions, GenerationService
        from services.investigator_service import InvestigatorService
        from services.probe_service import ProbeService
        from services.redteam_service import RedTeamService

        # 1) Understand the whole system (cached when the source is unchanged).
        prog.emit("orchestrate", "step", "modelling the system")
        model = AppModelService(self.context).build(repo_path, use_cache=use_cache, progress=prog)
        if not model.success:
            # No code model (e.g. a UI-only target, or a repo with no readable
            # source). The running app is still the source of truth — proceed with
            # an empty model so live grounding, the probes and the UI tour can work
            # off the live app. Only a deep code-generation pass truly needs source.
            prog.emit("orchestrate", "info",
                      f"no code model ({model.error or 'no source'}) — scanning the live app only")
            model = AppModel(root=repo_path)

        # 1b) Ground the endpoints in the RUNNING app (rule #1: the running app is
        #     the source of truth; the code only says WHAT is risky). The code model
        #     keeps config/secrets/risks; the live OpenAPI spec supplies exact paths.
        try:
            from services.live_surface import ground_endpoints, recover_base_path
            live = ground_endpoints(base_url)
            if live:
                model.endpoints = live
                prog.emit("orchestrate", "info",
                          f"grounded {len(live)} endpoint(s) from the live app spec")
            else:
                # No OpenAPI: the code-derived paths may miss the base path the API is
                # mounted under (e.g. /users vs /api/users). Recover it from the live app.
                pref = recover_base_path(model, base_url)
                if pref:
                    prog.emit("orchestrate", "info", f"recovered base path '{pref}' from the live app")
            # Canonicalise path params to {name} so the probes recognise them whatever
            # framework spelled them (Flask <id>, Express/Gin/Rails :id, …).
            from services.live_surface import normalize_path_params
            for e in getattr(model, "endpoints", []):
                if isinstance(e, dict) and e.get("path"):
                    e["path"] = normalize_path_params(str(e["path"]))
        except Exception as exc:
            logger.warning("live endpoint grounding failed: %s", exc)

        # 2) Suspect where the bugs are (the knowledge pass is an LLM call — deep only).
        detection = DetectorService(self.context).detect(
            model, use_knowledge_pass=deep, progress=prog)

        # 3) Prove the exploits HTTP steps can't express (JWT forgery, crashes) — free.
        probe_result = ProbeService(self.context).run(model, base_url, auth=auth, progress=prog)

        # 3b) Tour the live UI (browser-driven) and turn what the browser sees —
        #     uncaught JS errors, failed API calls — into findings. Needs Playwright
        #     (exec venv, Python 3.11/3.12); degrades to nothing when unavailable.
        ui_confirmed: list[dict] = []
        ui_screens: list[dict] = []
        if tour:
            try:
                from services.tour_service import TourService
                from services.ui_findings import findings_from_tour
                shot_dir = f"output/tour/{time.strftime('%Y%m%dT%H%M%S')}"
                tour_result = TourService(self.context).tour(
                    base_url, login=tour_login, capture_screenshots=True,
                    screenshot_dir=shot_dir, progress=prog)
                if tour_result.error:
                    prog.emit("orchestrate", "info", f"UI tour skipped: {tour_result.error}")
                else:
                    ui_confirmed = findings_from_tour(tour_result)
                    ui_screens = [s for s in (getattr(tour_result, "screens", []) or [])
                                  if isinstance(s, dict) and s.get("screenshot")]
                    prog.emit("orchestrate", "info",
                              f"UI tour: {tour_result.screen_count} screen(s), "
                              f"{len(ui_confirmed)} UI finding(s)")
                    # Visual regression against a saved baseline (first run seeds it).
                    if visual_baseline:
                        try:
                            from services.visual_regression import visual_findings
                            vf = visual_findings(visual_baseline, shot_dir, diff_dir=f"{shot_dir}/diff")
                            ui_confirmed += vf
                            prog.emit("orchestrate", "info",
                                      f"visual regression: {len(vf)} change(s) vs baseline"
                                      if vf else "visual baseline established / no change")
                        except Exception as exc:
                            logger.warning("visual regression failed: %s", exc)
            except Exception as exc:
                logger.warning("UI tour failed: %s", exc)

        # 4) Generate: functional (per router) + adversarial (red-team) — deep only.
        tests: list[dict] = []
        chosen_routers: list[str] = []
        skipped_routers: list[str] = []
        endpoint_groups: list[str] = []
        functional_count = 0        # B2: functional/fallback tests produced (not red-team)
        functional_attempted = False
        if deep:
            prog.emit("orchestrate", "step", "generating tests")
            ranked = self._ranked_router_files(model, repo_path, detection=detection)
            chosen_routers, skipped_routers = self._select_within_budget(
                ranked, model, max_router_files, max_endpoints)
            if ranked:
                chosen_eps = sum(self._router_endpoint_count(p, model) for p in chosen_routers)
                prog.emit("orchestrate", "info",
                          f"functional generation: {len(chosen_routers)} of {len(ranked)} router(s) "
                          f"(~{chosen_eps} endpoint(s), budget {max_endpoints} ep / {max_router_files} files, "
                          f"risk-ranked: suspects + churn + surface)")
            elif getattr(model, "endpoints", None):
                # Fallback: no router source files (UI-only repo, unreadable source,
                # or a live-only target). Generate functional tests straight from the
                # endpoint surface, grouped by file/collection, so a no-router repo
                # still gets generated coverage — not just probes.
                functional_attempted = True
                egen, endpoint_groups = self._generate_from_endpoints(
                    model, max_router_files, max_endpoints, prog)
                tests.extend(egen)
                functional_count += len(egen)
            for fpath in chosen_routers:
                functional_attempted = True
                try:
                    gen = GenerationService(self.context).generate(
                        fpath, options=GenerationOptions(
                            test_type="api", repo_context=model.as_context(), use_cache=False))
                    tests.extend(gen.test_cases)
                    functional_count += len(gen.test_cases)
                except Exception as exc:
                    logger.warning("orchestrator generation failed for %s: %s", fpath, exc)
            try:
                rt = RedTeamService(self.context).generate(model, detection, progress=prog)
                tests.extend(rt.test_cases)
            except Exception as exc:
                logger.warning("orchestrator red-team failed: %s", exc)
            # B2: a generation leg that was attempted but produced nothing is exactly
            # the silent gap that hid the API-validation bug for months — say it LOUD.
            if functional_attempted and functional_count == 0:
                prog.emit("orchestrate", "warn",
                          "SILENT-GAP: functional generation ran but produced 0 tests — "
                          "a stage is failing quietly (check validation / generation).")
        else:
            prog.emit("orchestrate", "info", "quick scan — probes + deterministic coverage only")

        # 5) Coverage loop: check the high-value goals; fill gaps with templates.
        coverage = self._measure_and_fill(model, tests, prog)
        # Honesty: record which routers got functional generation and which were
        # skipped for budget, so the report never implies a router was tested when
        # it wasn't. Deep mode only (quick scan does no functional generation).
        import os as _os
        for fp in chosen_routers:
            coverage[f"functional:{_os.path.relpath(fp, repo_path)}"] = "generation"
        for fp in skipped_routers:
            coverage[f"functional:{_os.path.relpath(fp, repo_path)}"] = "skipped (budget)"
        for grp in endpoint_groups:
            coverage[f"functional:endpoints:{grp}"] = "generation (from endpoints, no router file)"
        if deep and not chosen_routers and not endpoint_groups and getattr(model, "endpoints", None):
            coverage["functional:routers"] = "skipped (no router files detected)"

        # 6) Execute → Critic → Investigator. The verify legs make LLM calls only
        #    when there are failures; wrapped so a missing key/credit degrades to
        #    the probe-proven bugs instead of crashing the scan.
        executable = [t for t in tests if t.get("api_steps")]
        if tests and not executable:
            prog.emit("orchestrate", "warn",
                      "SILENT-GAP: tests were generated but none are executable "
                      "(no api_steps) — nothing will run.")
        prog.emit("orchestrate", "step", f"executing {len(executable)} test(s)")
        execution = ExecutionService(self.context).execute(
            executable, mode="api", base_url=base_url)
        investigation = None
        # The deterministic coverage templates carry their own verdict
        # (_confirmed_from_templates, with calibrated severities). Don't let the LLM
        # Critic/Investigator RE-judge them — that over-confirmed debatable findings
        # (e.g. "accepts an empty title") as bugs. Only generated tests are investigated.
        generated = [t for t in executable if t.get("attack") != "coverage-template"]
        try:
            critic = CriticService(self.context).review(execution, generated)
            investigation = InvestigatorService(self.context).investigate(
                critic, generated, base_url=base_url)
        except Exception as exc:
            logger.warning("orchestrator verify legs skipped: %s", exc)
            prog.emit("orchestrate", "info", f"verify skipped ({exc})")

        # 7) Unify confirmed bugs: Investigator (LLM) + probes + self-proving
        #    deterministic templates (a template that fails its own assertion is a
        #    proven bug, no LLM judgement needed — this is what makes a quick scan
        #    find bugs with zero model calls).
        confirmed = self._unify(investigation, probe_result)
        confirmed += self._confirmed_from_templates(execution, executable, confirmed)
        confirmed += ui_confirmed  # browser-observed UI bugs (console/network)

        # 7b) Family expansion: bugs come in families (the same dev made the same
        #     mistake elsewhere). Once a class is CONFIRMED, sweep EVERY matching
        #     endpoint for it with the per-class cap lifted. Deterministic and bounded,
        #     and it starts ONLY from confirmed bugs — never suspects — so a false
        #     positive can't compound across the family.
        try:
            family = self._expand_families(confirmed, model, base_url, auth=auth)
            if family:
                confirmed += family
                prog.emit("orchestrate", "info",
                          f"family expansion: +{len(family)} sibling bug(s) of confirmed classes")
        except Exception as exc:
            logger.warning("family expansion failed: %s", exc)

        # 7c) One bounded second round. It assembles its tests from two sources and
        #     runs them together, exactly once (so it can't loop or burn budget):
        #       (a) DETERMINISTIC setup repair — round-1 tests that broke before their
        #           attack landed get a known-good register→login preamble spliced in
        #           front of their original attack tail and re-run. This rescues the
        #           "login without register" class WITHOUT asking the LLM to rewrite
        #           the attack (which tends to repeat the same setup mistake).
        #       (b) REGENERATION — suspects still genuinely unverified (orphans the
        #           app already defended are excluded by _unresolved_suspects) get a
        #           fresh red-team attack. Deep mode only.
        rounds = 1
        if deep:
            round2_tests: list[dict] = []
            repaired = self._repair_setup(
                self._setup_broken_tests(execution, executable), model)
            round2_tests.extend(repaired)
            unresolved = self._unresolved_suspects(
                getattr(detection, "findings", []), confirmed,
                execution=execution, tests=executable)[:10]
            if unresolved:
                try:
                    rt2 = RedTeamService(self.context).generate(model, unresolved, progress=prog)
                    round2_tests.extend(rt2.test_cases)
                except Exception as exc:
                    logger.warning("round 2 red-team failed: %s", exc)
            if round2_tests:
                try:
                    prog.emit("orchestrate", "step",
                              f"round 2 — {len(repaired)} setup-repaired + "
                              f"{len(round2_tests) - len(repaired)} re-attack test(s)")
                    exe2 = ExecutionService(self.context).execute(
                        [t for t in round2_tests if t.get("api_steps")], mode="api", base_url=base_url)
                    gen2 = [t for t in round2_tests if t.get("attack") != "coverage-template"]
                    crit2 = CriticService(self.context).review(exe2, gen2)
                    inv2 = InvestigatorService(self.context).investigate(crit2, gen2, base_url=base_url)
                    rounds = 2
                    seen = {(str(b.get("title", "")), str(b.get("target", ""))) for b in confirmed}
                    new2 = [b for b in (self._unify(inv2, None)
                                        + self._confirmed_from_templates(exe2, round2_tests, confirmed))
                            if (str(b.get("title", "")), str(b.get("target", ""))) not in seen]
                    confirmed += new2
                    prog.emit("orchestrate", "info",
                              f"round 2: +{len(new2)} confirmed bug(s)" if new2
                              else "round 2: no new confirmed bugs")
                except Exception as exc:
                    logger.warning("round 2 failed: %s", exc)

        # B2: per-leg output contract — record what each stage actually produced, so a
        # stage that silently yields nothing is visible (not hidden like the 8-month
        # API-validation bug). `_attempted` flags let silent_legs() tell "legitimately
        # empty" apart from "ran and produced nothing".
        templates_injected = sum(1 for t in tests if t.get("attack") == "coverage-template")
        probe_confirmed = sum(1 for b in confirmed if str(b.get("source", "")) == "probe")
        legs = {
            "endpoints_grounded": len(getattr(model, "endpoints", []) or []),
            "suspects": len(getattr(detection, "findings", []) or []),
            "probes_confirmed": probe_confirmed,
            "functional_attempted": functional_attempted,
            "functional_generated": functional_count,
            "templates_injected": templates_injected,
            "tests_executed": len(executable),
            "deterministic_confirmed": sum(
                1 for b in confirmed if str(b.get("source", "")) in ("probe", "coverage")),
        }
        # S6: a durable, REDACTED replay trail — the executed test definitions and the
        # per-step request/response outcomes — so a confirmed finding can be reproduced
        # and audited independently (not just a summary). Secrets are masked.
        from services.results import redact_secrets
        evidence = {
            "tests": redact_secrets(executable),
            "execution": redact_secrets(getattr(execution, "results", []) or []),
            "probes": redact_secrets([b for b in confirmed if str(b.get("source")) == "probe"]),
        }
        result = OrchestratorResult(
            confirmed=confirmed, coverage=coverage, rounds=rounds,
            tests_run=len(executable), suspects=len(detection.findings),
            ui_screens=ui_screens, legs=legs, evidence=evidence,
            created_records=getattr(probe_result, "created_records", 0),
            cleaned_records=getattr(probe_result, "cleaned_records", 0))
        silent = result.silent_legs()
        if silent:
            prog.emit("orchestrate", "warn",
                      "SILENT-GAP in: " + ", ".join(silent) + " — a stage produced nothing it should have.")
        prog.emit("orchestrate", "ok", result.summary())
        return result

    # ── Coverage ──────────────────────────────────────────────────────────────

    def _measure_and_fill(self, model: AppModel, tests: list[dict],
                          prog: ProgressReporter) -> dict:
        """
        Derive high-value goals from the model, check them against the generated
        tests, and inject a deterministic template for any critical gap.
        """
        coverage: dict = {}

        pag = self._paginated_endpoint(model)
        if pag:
            goal = f"pagination-boundary:{pag['list']}"
            if self._has_pagination_boundary_test(tests, pag):
                coverage[goal] = "generation"
            else:
                template = self._pagination_boundary_template(pag, model)
                if template:
                    tests.append(template)
                    coverage[goal] = "template"
                    prog.emit("orchestrate", "info", f"filled coverage gap: {goal}")
                else:
                    coverage[goal] = "uncovered"

        # Deterministic guarantees for the high-value security/validation classes,
        # so they're covered every run regardless of what generation happened to do.
        from core.auth_recipe import derive_auth_recipe
        from services.coverage_goals import all_templates
        for goal, tpls in all_templates(model, derive_auth_recipe(model)).items():
            tests.extend(tpls)
            coverage[goal] = "template"
            prog.emit("orchestrate", "info",
                      f"coverage goal '{goal}': {len(tpls)} guaranteed test(s)")
        return coverage

    @staticmethod
    def _paginated_endpoint(model: AppModel) -> dict | None:
        """Find a GET list endpoint that returns pagination, and its create sibling."""
        list_path = create_path = None
        for e in model.endpoints:
            if not isinstance(e, dict):
                continue
            method = str(e.get("method", "")).upper()
            resp = " ".join(str(x) for x in (e.get("response_fields") or []))
            if method == "GET" and ("total_pages" in resp or "page" in resp.lower()):
                list_path = str(e.get("path", ""))
            if method == "POST" and list_path and str(e.get("path", "")).rstrip("/") == list_path.rstrip("/"):
                create_path = str(e.get("path", ""))
        if not list_path:
            return None
        # Fallback: the create path is usually the same collection path with POST.
        if not create_path:
            for e in model.endpoints:
                if (isinstance(e, dict) and str(e.get("method", "")).upper() == "POST"
                        and str(e.get("path", "")).rstrip("/") == list_path.rstrip("/")):
                    create_path = str(e.get("path", ""))
        # Derive the pagination response field names from the list endpoint.
        from core.auth_recipe import field_names
        list_ep = next((e for e in model.endpoints if isinstance(e, dict)
                        and str(e.get("method", "")).upper() == "GET"
                        and str(e.get("path", "")) == list_path), {})
        resp = field_names(list_ep.get("response_fields"))
        pages_field = next((f for f in resp if f.lower() in
                            ("total_pages", "totalpages", "pages", "page_count", "num_pages")), "total_pages")
        total_field = next((f for f in resp if f.lower() in ("total", "count", "total_count")), "total")
        return {"list": list_path, "create": create_path or list_path,
                "pages_field": pages_field, "total_field": total_field}

    @staticmethod
    def _has_pagination_boundary_test(tests: list[dict], pag: dict) -> bool:
        """
        True only if a test creates a NON-multiple count of items and asserts the
        computed total_pages — an aligned-count test does not count (it hides the
        off-by-one). This is why generation alone was unreliable.
        """
        list_path = pag["list"].rstrip("/")
        for t in tests:
            steps = t.get("api_steps") or []
            creates = sum(1 for s in steps
                          if str(s.get("method", "")).upper() == "POST"
                          and str(s.get("path", "")).rstrip("/").split("?")[0] == pag["create"].rstrip("/"))
            get_step = next((s for s in steps
                             if str(s.get("method", "")).upper() == "GET"
                             and list_path in str(s.get("path", ""))
                             and "total_pages" in str(s.get("expect_json", ""))), None)
            if not get_step or creates == 0:
                continue
            ps = OrchestratorService._page_size_of(get_step)
            if ps and creates % ps != 0:
                return True
        return False

    @staticmethod
    def _page_size_of(step: dict) -> int | None:
        """Extract page_size from a GET step's path query, if present."""
        import re
        m = re.search(r"page_size=(\d+)", str(step.get("path", "")))
        return int(m.group(1)) if m else None

    def _pagination_boundary_template(self, pag: dict, model: AppModel) -> dict | None:
        """
        A deterministic pagination boundary test: create 3 items, list with
        page_size=2, and assert total_pages == ceil(3/2) == 2 and total == 3. This
        GUARANTEES the boundary is covered rather than hoping generation picks it.

        All paths and field names are derived from the AppModel (auth flow, the
        create body field, the page-size query param, the total_pages field), so
        the template generalises beyond one app.
        """
        create = pag["create"]
        list_path = pag["list"]
        if not create or not list_path:
            return None

        from core.auth_recipe import derive_auth_recipe
        r = derive_auth_recipe(model)
        create_field = self._create_body_field(model, create)
        size_param = self._page_size_param(pag)
        total_field = pag.get("total_field", "total")
        pages_field = pag.get("pages_field", "total_pages")

        steps = [
            {"step_number": 1, "method": "POST", "path": r["register_path"],
             "body": {r["id_field"]: "pg_{{random}}@example.com", r["secret_field"]: "Secret123!"},
             "expected_status": 201},
            {"step_number": 2, "method": "POST", "path": r["login_path"],
             "body": {r["id_field"]: "pg_{{random}}@example.com", r["secret_field"]: "Secret123!"},
             "expected_status": 200, "save": {"token": r["token_field"]}},
        ]
        for i in range(3):
            steps.append({"step_number": 3 + i, "method": "POST", "path": create,
                          "body": {create_field: f"pg{i}"}, "expected_status": 201, "use_auth": True})
        steps.append({"step_number": 6, "method": "GET",
                      "path": f"{list_path}?page=1&{size_param}=2", "expected_status": 200,
                      "use_auth": True, "expect_json": {total_field: 3, pages_field: 2}})
        return {
            "test_case_id": "COV-PAG-0001",
            "title": "Coverage: pagination total_pages is correct at a boundary (3 items, size 2)",
            "test_type": "boundary", "module": "coverage", "attack": "coverage-template",
            "api_steps": steps,
        }

    @staticmethod
    def _create_body_field(model: AppModel, create_path: str) -> str:
        """The field a create needs — prefer a title/name-like text field."""
        from core.auth_recipe import field_names
        for e in model.endpoints:
            if (isinstance(e, dict) and str(e.get("method", "")).upper() == "POST"
                    and str(e.get("path", "")).rstrip("/") == create_path.rstrip("/")):
                names = field_names(e.get("request_fields"))
                for pref in ("title", "name", "label", "text"):
                    if pref in names:
                        return pref
                if names:
                    return names[0]
        return "title"

    @staticmethod
    def _page_size_param(pag: dict) -> str:
        """The query param that controls page size (page_size / per_page / limit)."""
        return pag.get("size_param", "page_size")

    # ── Router discovery + result unification ─────────────────────────────────

    @staticmethod
    def _router_files(model: AppModel, repo_path: str) -> list[str]:
        """Absolute paths of the model's router files (they hold the endpoints)."""
        root = Path(repo_path)
        out = []
        for f in model.files:
            if isinstance(f, dict) and f.get("role") == "router" and f.get("path"):
                p = root / f["path"]
                if p.exists():
                    out.append(str(p))
        return out

    # Router file names that usually hold sensitive logic vs. low-value surface.
    _HIGH_RISK_KW = ("auth", "login", "logout", "register", "signup", "password", "token",
                     "session", "payment", "billing", "checkout", "card", "invoice", "order",
                     "transfer", "wallet", "account", "user", "admin", "role", "permission",
                     "transaction", "fund", "money", "bank", "secret", "key", "oauth")
    _LOW_RISK_KW = ("health", "ping", "status", "about", "docs", "swagger", "openapi",
                    "static", "index", "home", "version", "metrics", "favicon", "robots")

    @classmethod
    def _router_score(cls, path: str, model: AppModel, detection=None,
                      churn: int = 0) -> int:
        """
        Risk score for a router file, so the generation budget goes to the most
        dangerous ones first (payments before health). Combines four signals:
          • the file name (high-risk vs trivial keywords),
          • the endpoints attributed to it by name convention (writes + auth weigh more),
          • the Detector's suspects pointing AT this file — a rule or the knowledge
            pass already flagged real risk here, the strongest single signal, and
          • git churn (bug-fix commits in its history — regression-prone code).
        `detection` and `churn` are optional, so the bare (path, model) call still
        scores on the static signals alone.
        """
        name = Path(path).stem.lower()
        score = 0
        if any(k in name for k in cls._HIGH_RISK_KW):
            score += 10
        if any(k in name for k in cls._LOW_RISK_KW):
            score -= 6
        stem = name.rstrip("s") or name
        eps = [e for e in getattr(model, "endpoints", []) if isinstance(e, dict)
               and stem and stem in str(e.get("path", "")).lower()]
        score += len(eps)
        score += sum(2 for e in eps if str(e.get("method", "")).upper() in ("POST", "PUT", "DELETE", "PATCH"))
        score += sum(1 for e in eps if e.get("auth_required"))
        score += 3 * cls._file_suspect_count(path, detection)   # Detector already flagged it
        score += 2 * min(int(churn or 0), 3)                     # bug-fix-prone history (capped)
        return score

    @staticmethod
    def _file_suspect_count(path: str, detection) -> int:
        """How many Detector suspects point at this router file (by name/relpath)."""
        if not detection:
            return 0
        findings = getattr(detection, "findings", detection) or []
        name = Path(path).name.lower()
        stem = Path(path).stem.lower()
        n = 0
        for f in findings:
            where = str(getattr(f, "where", "")).lower()
            if where and (name in where or (stem and f"/{stem}" in where) or where.endswith(stem)):
                n += 1
        return n

    @classmethod
    def _router_endpoint_count(cls, path: str, model: AppModel) -> int:
        """Endpoints attributed to a router by name convention (min 1 so it has a cost)."""
        stem = Path(path).stem.lower().rstrip("s") or Path(path).stem.lower()
        n = sum(1 for e in getattr(model, "endpoints", []) if isinstance(e, dict)
                and stem and stem in str(e.get("path", "")).lower())
        return n or 1

    def _ranked_router_files(self, model: AppModel, repo_path: str,
                             detection=None) -> list[str]:
        """Router files ordered highest-risk first (for the functional-generation budget)."""
        files = self._router_files(model, repo_path)
        churn: dict = {}
        try:
            from core.git_analyzer import GitAnalyzer
            ga = GitAnalyzer()
            for f in files:
                try:
                    churn[f] = int(ga.get_file_history(f).get("bug_fix_count", 0))
                except Exception:
                    churn[f] = 0
        except Exception:
            pass
        return sorted(files, key=lambda p: self._router_score(
            p, model, detection=detection, churn=churn.get(p, 0)), reverse=True)

    @classmethod
    def _select_within_budget(cls, ranked: list[str], model: AppModel,
                              max_files: int, max_endpoints: int | None) -> tuple[list, list]:
        """
        Choose routers highest-risk-first until the ENDPOINT budget is spent (budget
        by the work each router implies, not a flat file count), bounded by a hard
        file ceiling so a pathological repo can't blow the budget. Always keeps at
        least the single highest-risk router.
        """
        chosen: list[str] = []
        used = 0
        for p in ranked:
            if len(chosen) >= max(1, max_files):
                break
            cost = cls._router_endpoint_count(p, model)
            if chosen and max_endpoints and used + cost > max_endpoints:
                break
            chosen.append(p)
            used += cost
        return chosen, ranked[len(chosen):]

    # ── Fallback generation: no router files → generate from the endpoint surface ──

    @staticmethod
    def _group_endpoints(model: AppModel) -> list[tuple[str, list]]:
        """
        Group the model's endpoints by their source file (when known) else by the
        first non-parameter path segment (/api/orders/{id} → "orders"), ranked
        highest-risk-first (size + write methods + auth). This is the unit the
        no-router fallback generates from.
        """
        groups: dict[str, list] = {}
        for e in getattr(model, "endpoints", []):
            if not isinstance(e, dict):
                continue
            key = str(e.get("file") or e.get("source") or "").strip()
            if not key:
                segs = [s for s in str(e.get("path", "")).split("/") if s and not s.startswith("{")]
                key = segs[0] if segs else "api"
            groups.setdefault(key, []).append(e)

        def gscore(eps: list) -> int:
            return (len(eps)
                    + sum(2 for e in eps if str(e.get("method", "")).upper() in ("POST", "PUT", "DELETE", "PATCH"))
                    + sum(1 for e in eps if e.get("auth_required")))
        return sorted(groups.items(), key=lambda kv: gscore(kv[1]), reverse=True)

    @staticmethod
    def _example_value(field: str):
        """A type-sensible example value for a request field, from its leaf name."""
        leaf = str(field).lower().rsplit(".", 1)[-1]
        if "email" in leaf:
            return "user@example.com"
        if any(k in leaf for k in ("password", "secret", "pwd")):
            return "Secret123!"
        if any(k in leaf for k in ("username", "login")) or leaf in ("user", "handle"):
            return "testuser"
        if any(k in leaf for k in ("url", "link", "uri", "image", "avatar", "photo")):
            return "https://example.com/x.png"
        import re as _re
        if _re.search(r"(?:^|_)(age|count|num|number|qty|quantity|amount|price|id)(?:$|_)", leaf) \
                or leaf == "id" or leaf.endswith("_id"):
            return 1
        return "test"

    @classmethod
    def _endpoints_as_spec(cls, key: str, eps: list) -> str:
        """
        Render an endpoint group as a directive spec GenerationService can turn into
        concrete api_steps. A terse path list makes the model return little or nothing;
        an explicit instruction + a per-endpoint example body + the auth flow makes it
        reliably emit real, assertable tests.
        """
        from core.auth_recipe import field_names
        lines = [
            f"# API endpoints for module '{key}' (no router source file — generated "
            "from the live/model surface).",
            "# Write API test cases (api_steps) for EACH endpoint below: send the shown "
            "request body and assert the expected outcome. For an endpoint marked "
            "[auth required], first register then log in to obtain a bearer token and "
            "send it as Authorization: Bearer <token>; also add a no-token case that "
            "must be rejected (401/403).",
            "",
        ]
        for e in eps:
            m = str(e.get("method", "GET")).upper()
            p = str(e.get("path", ""))
            auth = "  [auth required]" if e.get("auth_required") else ""
            lines.append(f"Endpoint: {m} {p}{auth}")
            fields = field_names(e.get("request_fields"))
            if fields:
                import json as _json
                body = {f: cls._example_value(f) for f in fields}
                lines.append(f"  request body: {_json.dumps(body)}")
            if m in ("POST",):
                lines.append("  success: 2xx (resource created / action accepted).")
            elif m in ("PUT", "PATCH", "DELETE"):
                lines.append("  success: 2xx (resource updated / removed).")
            else:
                lines.append("  success: 200 OK with the resource(s).")
        return "\n".join(lines)

    def _generate_from_endpoints(self, model: AppModel, max_files: int,
                                 max_endpoints: int, prog: ProgressReporter) -> tuple[list, list]:
        """
        Generate functional API tests directly from the endpoint surface, grouped by
        file/collection, honouring the same endpoint budget. Returns (test_cases,
        covered_group_keys). Never raises — a failed group is logged and skipped.
        """
        from services.generation_service import GenerationOptions, GenerationService

        ranked = self._group_endpoints(model)
        if not ranked:
            return [], []
        # Budget the groups the same way routers are budgeted: highest-risk-first
        # until the endpoint budget is spent, bounded by the file ceiling.
        chosen: list[tuple[str, list]] = []
        used = 0
        for key, eps in ranked:
            if len(chosen) >= max(1, max_files):
                break
            if chosen and max_endpoints and used + len(eps) > max_endpoints:
                break
            chosen.append((key, eps))
            used += len(eps)

        prog.emit("orchestrate", "info",
                  f"no router files — generating from the endpoint surface: "
                  f"{len(chosen)} group(s), ~{used} endpoint(s)")
        gs = GenerationService(self.context)
        ctx = model.as_context()
        out: list = []
        covered: list[str] = []
        for key, eps in chosen:
            spec = self._endpoints_as_spec(key, eps)
            cases: list = []
            # One retry: generation from a synthesised spec is occasionally empty
            # (LLM non-determinism on a short input); a single retry makes the
            # fallback reliably yield tests without looping.
            for attempt in range(2):
                try:
                    gen = gs.generate(f"endpoints://{key}", options=GenerationOptions(
                        content=spec, test_type="api", module=key,
                        repo_context=ctx, use_cache=False))
                    cases = gen.test_cases or []
                    if cases:
                        break
                    logger.info("endpoint-group generation produced no tests for %s "
                                "(attempt %d, %s)", key, attempt + 1,
                                getattr(gen, "error", "") or "empty")
                except Exception as exc:
                    logger.warning("endpoint-group generation failed for %s: %s", key, exc)
                    break
            if cases:
                out.extend(cases)
                covered.append(key)   # record coverage only when tests were produced
        return out, covered

    # Probe classes where a confirmed bug implies siblings worth sweeping exhaustively.
    _FAMILY_SWEEP_LIMIT = 50

    # Keywords that classify a confirmed bug into a family-sweepable probe class when
    # it carries no `probe` field — i.e. bugs confirmed by a coverage template
    # (source "coverage", e.g. COV-IDOR) or by the LLM Investigator (source "test").
    # Order matters: the most specific class is listed first so "sql injection" maps
    # to sqli, not the generic injection class. Each class maps to a probe that is a
    # strict, zero-false-positive control, so classifying loosely only ever costs a
    # bounded deterministic sweep — it can never manufacture a false sibling.
    _CLASS_KEYWORDS = (
        ("sqli", ("sql injection", "sqli", "sql-injection")),
        ("path-traversal", ("path traversal", "directory traversal", "path-traversal",
                            "lfi", "local file inclusion", "arbitrary file read")),
        ("bola", ("idor", "bola", "object-level", "object level", "broken object",
                  "another user", "other user", "unauthorized object")),
        ("injection", ("command injection", "os command", "code injection", "injection")),
    )

    @classmethod
    def _family_class(cls, bug: dict) -> str | None:
        """
        Map a confirmed bug to a family probe id (bola/sqli/injection/path-traversal),
        or None when it has no sibling-sweepable class.

        Probe-confirmed bugs name the class directly in `probe`. Template- and
        Investigator-confirmed bugs carry no `probe`, so they are classified from
        their id/title/evidence text — this is what lets a template-confirmed IDOR or
        an LLM-confirmed SQL injection sweep its whole family, not just probe hits.
        """
        family = {"bola", "sqli", "injection", "path-traversal"}
        probe = str(bug.get("probe", "")).lower()
        if probe in family:
            return probe
        text = " ".join(str(bug.get(k, "")) for k in
                        ("test_case_id", "title", "evidence")).lower()
        for cid, kws in cls._CLASS_KEYWORDS:
            if any(k in text for k in kws):
                return cid
        return None

    def _expand_families(self, confirmed: list[dict], model: AppModel, base_url: str,
                         *, auth: dict | None = None) -> list[dict]:
        """
        For each CONFIRMED class, re-run its probe over EVERY matching endpoint (cap
        lifted) and return the sibling bugs not already confirmed. Deterministic,
        bounded, and driven only by confirmed bugs — never suspects. The class comes
        from the bug's `probe` when present, else is inferred from its text
        (_family_class), so template- and Investigator-confirmed bugs expand too.
        """
        import requests
        from execution.probes import (bola_probe, injection_probe, path_traversal_probe, sqli_probe)
        family = {"bola": bola_probe, "sqli": sqli_probe,
                  "injection": injection_probe, "path-traversal": path_traversal_probe}
        classes: set[str] = set()
        seen: set[tuple[str, str]] = set()
        for b in confirmed:
            c = self._family_class(b)
            if not c:
                continue
            classes.add(c)
            if b.get("target"):
                seen.add((c, str(b.get("target", ""))))
        todo = [(c, family[c]) for c in sorted(classes) if c in family]
        if not todo:
            return []
        out: list[dict] = []
        sess = requests.Session()
        for cid, fn in todo:
            try:
                for f in fn(model, base_url, session=sess, auth=auth, limit=self._FAMILY_SWEEP_LIMIT):
                    if getattr(f, "exploited", False) and (cid, str(getattr(f, "target", ""))) not in seen:
                        seen.add((cid, str(getattr(f, "target", ""))))
                        out.append({"title": getattr(f, "title", ""), "severity": getattr(f, "severity", ""),
                                    "evidence": getattr(f, "evidence", ""), "source": "probe",
                                    "probe": getattr(f, "probe", ""), "target": getattr(f, "target", "")})
            except Exception as exc:
                logger.warning("family expansion for %s failed: %s", cid, exc)
        return out

    @classmethod
    def _unresolved_suspects(cls, findings, confirmed: list[dict],
                             execution=None, tests: list[dict] | None = None) -> list:
        """
        Detector suspects worth a second red-team pass: those neither confirmed NOR
        already cleanly tested in round 1.

        A suspect is dropped when (a) the endpoint it points at (`where`) appears in a
        confirmed bug's target/title — it became a bug — OR (b) round 1 delivered an
        attack all the way to that endpoint and the app HELD (an executed test whose
        final step ran, that did not become a confirmed bug). Re-attacking a suspect
        the app already defended just burns model calls every scan. What's left is the
        genuinely unverified: suspects whose round-1 test broke on setup (the attack
        never landed), or that no test touched — exactly the cases round 2 can rescue.
        """
        resolved = " ".join((str(b.get("target", "")) + " " + str(b.get("title", "")))
                            for b in (confirmed or [])).lower()
        cleared = cls._cleared_endpoints(execution, tests)
        out = []
        for f in (findings or []):
            where = str(getattr(f, "where", "")).strip()
            if not where:
                continue
            if where.lower() in resolved:
                continue  # became a confirmed bug
            toks = cls._endpoint_tokens(where)
            if toks and cleared and any(cls._paths_match(t, c) for t in toks for c in cleared):
                continue  # attacked in round 1 and the app held — don't re-attack
            out.append(f)
        return out

    @staticmethod
    def _endpoint_tokens(text: str) -> set[str]:
        """Normalized endpoint path tokens (`/a/{id}`) parsed out of a free-text `where`."""
        import re
        from services.live_surface import normalize_path_params
        toks = set()
        for m in re.findall(r"/[^\s,;:'\"()]+", str(text)):
            p = normalize_path_params(m.split("?")[0]).rstrip("/").lower()
            if p and p != "/":
                toks.add(p)
        return toks

    @staticmethod
    def _paths_match(a: str, b: str) -> bool:
        """True when two normalized endpoint paths refer to the same route."""
        return a == b or a.startswith(b + "/") or b.startswith(a + "/")

    @classmethod
    def _cleared_endpoints(cls, execution, tests: list[dict] | None) -> set[str]:
        """
        Endpoint paths round 1 delivered an attack to where the app HELD: the set of
        normalized paths touched by executed tests whose FINAL step actually ran (so
        the attack landed, not a setup break). Used to stop round 2 re-attacking a
        suspect the app already defended.
        """
        if not execution or not tests:
            return set()
        from services.live_surface import normalize_path_params
        by_key: dict = {}
        for t in tests:
            by_key[t.get("test_case_id", "")] = t
            by_key[t.get("title", "")] = t
        cleared: set[str] = set()
        for r in getattr(execution, "results", []):
            tc = by_key.get(r.get("test_case_id", "")) or by_key.get(r.get("title", ""))
            if not tc:
                continue
            steps = tc.get("api_steps") or []
            rsteps = r.get("steps") or []
            if not steps or not rsteps:
                continue
            ran = {s.get("step_number") for s in rsteps if s.get("status") in ("PASS", "FAIL")}
            if steps[-1].get("step_number") not in ran:
                continue  # broke before the attack landed — leave it for round 2
            for s in steps:
                p = normalize_path_params(str(s.get("path", "")).split("?")[0]).rstrip("/").lower()
                if p and p != "/":
                    cleared.add(p)
        return cleared

    # ── Setup repair: fix a broken auth preamble and re-run (item 3) ───────────

    @classmethod
    def _setup_broken_tests(cls, execution, tests: list[dict] | None) -> list[dict]:
        """
        Round-1 tests that broke BEFORE their attack landed: the final step never ran
        and an earlier step failed/errored. These are the ones a setup repair rescues
        (e.g. a login with no prior register) — distinct from tests the app defended.
        """
        if not execution or not tests:
            return []
        by_key: dict = {}
        for t in tests:
            by_key[t.get("test_case_id", "")] = t
            by_key[t.get("title", "")] = t
        broken: list[dict] = []
        for r in getattr(execution, "results", []):
            tc = by_key.get(r.get("test_case_id", "")) or by_key.get(r.get("title", ""))
            if not tc:
                continue
            steps = tc.get("api_steps") or []
            rsteps = r.get("steps") or []
            if not steps or not rsteps:
                continue
            ran = {s.get("step_number") for s in rsteps if s.get("status") in ("PASS", "FAIL")}
            if steps[-1].get("step_number") in ran:
                continue  # attack landed — not a setup break
            if any(s.get("status") in ("FAIL", "ERROR") for s in rsteps):
                broken.append(tc)
        return broken

    @staticmethod
    def _attack_tail(steps: list[dict], recipe: dict) -> list[dict]:
        """Drop a test's leading register/login (setup) steps, keeping its attack tail."""
        auth_paths = {str(recipe.get("register_path", "")).rstrip("/"),
                      str(recipe.get("login_path", "")).rstrip("/")}
        i = 0
        while i < len(steps):
            p = str(steps[i].get("path", "")).split("?")[0].rstrip("/")
            if p in auth_paths:
                i += 1
            else:
                break
        return steps[i:]

    @staticmethod
    def _has_auth_header(step: dict) -> bool:
        headers = step.get("headers") or {}
        return any(str(k).lower() == "authorization" for k in headers)

    def _repair_setup(self, broken: list[dict], model: AppModel) -> list[dict]:
        """
        Rebuild each setup-broken test with a KNOWN-GOOD auth preamble (the same
        register→login→save-token steps the coverage templates use successfully) in
        front of its original attack tail, and re-run that — instead of asking the
        LLM to regenerate the whole attack, which can repeat the same setup mistake.
        Deterministic; returns [] when no auth recipe can be derived.
        """
        from core.auth_recipe import derive_auth_recipe
        from services.coverage_goals import _auth_steps
        recipe = derive_auth_recipe(model)
        if not recipe:
            return []
        repaired: list[dict] = []
        for t in broken:
            tail = self._attack_tail(t.get("api_steps") or [], recipe)
            if not tail:
                continue  # nothing but setup — no attack to replay
            preamble = _auth_steps(recipe, model, "rp", "token", 1)
            start = len(preamble) + 1
            new_tail = []
            for i, s in enumerate(tail):
                ns = dict(s)
                ns["step_number"] = start + i
                if "use_auth" not in ns and not self._has_auth_header(ns):
                    ns["use_auth"] = "token"   # attach the repaired token to the attack
                new_tail.append(ns)
            repaired.append({**t,
                             "test_case_id": f"{t.get('test_case_id', 'RT')}-rep",
                             "title": f"[setup-repaired] {t.get('title', '')}".strip(),
                             "api_steps": preamble + new_tail})
        return repaired

    _COV_SEVERITY = {"COV-AUTH": "P1", "COV-IDOR": "P1", "COV-VAL": "P3", "COV-PAG": "P2"}

    @classmethod
    def _confirmed_from_templates(cls, execution, tests: list[dict], already: list[dict]) -> list[dict]:
        """
        A deterministic coverage template that FAILS at its own assertion step is a
        proven bug — no LLM verdict needed. Setup failures (register/login/create)
        are ignored; only a failure at the final assertion step counts.
        """
        by_key = {}
        for t in tests:
            by_key[t.get("test_case_id", "")] = t
            by_key[t.get("title", "")] = t
        seen_titles = {b.get("title") for b in already}
        out = []
        for r in getattr(execution, "results", []):
            if r.get("status") not in ("FAIL", "ERROR"):
                continue
            tc = by_key.get(r.get("test_case_id", "")) or by_key.get(r.get("title", ""))
            if not tc or tc.get("attack") != "coverage-template":
                continue
            steps = tc.get("api_steps") or []
            if not steps:
                continue
            last_step_no = steps[-1].get("step_number")
            fstep = next((s for s in (r.get("steps") or [])
                          if s.get("status") in ("FAIL", "ERROR")), None)
            if not fstep or fstep.get("step_number") != last_step_no:
                continue  # failed at setup, not at the assertion — not a proven bug
            # The security/validation templates expect a REJECTION; the vuln only
            # manifests when the endpoint actually SUCCEEDED (2xx) without being
            # rejected. A 404/405/5xx at the assertion means the path was wrong or
            # the endpoint doesn't exist — NOT an auth bypass. (Pagination's failure
            # is a body-assertion on a 200, so it still qualifies.)
            code = fstep.get("response_code")
            if not (isinstance(code, int) and 200 <= code < 300):
                continue
            title = str(r.get("title", ""))
            if title in seen_titles:
                continue
            sev = next((v for k, v in cls._COV_SEVERITY.items()
                        if str(tc.get("test_case_id", "")).startswith(k)), "P2")
            # Carry the asserted endpoint as `target` and the template id as
            # `test_case_id` so family expansion can classify and sweep siblings of a
            # template-confirmed class (e.g. COV-IDOR → the bola family).
            last = steps[-1]
            target = (f"{str(last.get('method', '')).upper()} "
                      f"{str(last.get('path', '')).split('?')[0]}").strip()
            out.append({"title": title, "severity": sev,
                        "evidence": str(fstep.get("error", ""))[:200], "source": "coverage",
                        "test_case_id": str(tc.get("test_case_id", "")), "target": target})
        return out

    @staticmethod
    def _unify(investigation, probe_result) -> list[dict]:
        """Merge Investigator-confirmed bugs and proven probe exploits."""
        confirmed = []
        for v in getattr(investigation, "confirmed", []):
            confirmed.append({
                "title": getattr(v, "title", ""), "severity": "",
                "evidence": getattr(v, "reason", ""), "source": "test",
                "target": getattr(v, "target", "")})
        for f in getattr(probe_result, "exploited", []):
            confirmed.append({
                "title": getattr(f, "title", ""), "severity": getattr(f, "severity", ""),
                "evidence": getattr(f, "evidence", ""), "source": "probe",
                "probe": getattr(f, "probe", ""), "target": getattr(f, "target", "")})
        return confirmed
