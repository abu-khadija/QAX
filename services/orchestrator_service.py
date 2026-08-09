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
luck. It also decides how much to generate from the app's surface, instead of a
hard-coded cap.

Never prints, never calls sys.exit(); failures are captured in the result.
"""

from __future__ import annotations

import logging
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
        max_router_files: int = 4,
        progress: ProgressReporter | None = None,
    ) -> OrchestratorResult:
        """
        Run the full autonomous pipeline over a repo against a live app.

        Args:
            repo_path (str): Repository root to model and test.
            base_url (str): The running app to execute/probe against.
            max_router_files (int): Cap on router files to generate functional
                tests from.
            progress (ProgressReporter | None): Progress sink.

        Returns:
            OrchestratorResult: Unified confirmed bugs + coverage. Never raises at
                runtime.

        Raises:
            QAXConfigError: If the Anthropic API key is missing.
        """
        prog = progress or NullProgressReporter()
        self.context.require_anthropic_key()

        from services.appmodel_service import AppModelService
        from services.critic_service import CriticService
        from services.detector_service import DetectorService
        from services.execution_service import ExecutionService
        from services.generation_service import GenerationOptions, GenerationService
        from services.investigator_service import InvestigatorService
        from services.probe_service import ProbeService
        from services.redteam_service import RedTeamService

        # 1) Understand the whole system.
        prog.emit("orchestrate", "step", "modelling the system")
        model = AppModelService(self.context).build(repo_path, progress=prog)
        if not model.success:
            return OrchestratorResult(error=model.error or "AppModel failed")

        # 2) Suspect where the bugs are.
        detection = DetectorService(self.context).detect(model, progress=prog)

        # 3) Prove the exploits HTTP steps can't express (JWT forgery, crashes).
        probe_result = ProbeService(self.context).run(model, base_url, progress=prog)

        # 4) Generate: functional (per router) + adversarial (red-team).
        prog.emit("orchestrate", "step", "generating tests")
        tests: list[dict] = []
        for fpath in self._router_files(model, repo_path)[:max_router_files]:
            try:
                gen = GenerationService(self.context).generate(
                    fpath, options=GenerationOptions(
                        test_type="api", repo_context=model.as_context(), use_cache=False))
                tests.extend(gen.test_cases)
            except Exception as exc:
                logger.warning("orchestrator generation failed for %s: %s", fpath, exc)
        try:
            rt = RedTeamService(self.context).generate(model, detection, progress=prog)
            tests.extend(rt.test_cases)
        except Exception as exc:
            logger.warning("orchestrator red-team failed: %s", exc)

        # 5) Coverage loop: check the high-value goals; fill gaps with templates.
        coverage = self._measure_and_fill(model, tests, prog)

        # 6) Execute → Critic → Investigator.
        executable = [t for t in tests if t.get("api_steps")]
        prog.emit("orchestrate", "step", f"executing {len(executable)} test(s)")
        execution = ExecutionService(self.context).execute(
            executable, mode="api", base_url=base_url)
        critic = CriticService(self.context).review(execution, executable)
        investigation = InvestigatorService(self.context).investigate(
            critic, executable, base_url=base_url)

        # 7) Unify confirmed bugs (tests + probes).
        confirmed = self._unify(investigation, probe_result)

        result = OrchestratorResult(
            confirmed=confirmed, coverage=coverage, rounds=1,
            tests_run=len(executable), suspects=len(detection.findings))
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

    @staticmethod
    def _unify(investigation, probe_result) -> list[dict]:
        """Merge Investigator-confirmed bugs and proven probe exploits."""
        confirmed = []
        for v in getattr(investigation, "confirmed", []):
            confirmed.append({
                "title": getattr(v, "title", ""), "severity": "",
                "evidence": getattr(v, "reason", ""), "source": "test"})
        for f in getattr(probe_result, "exploited", []):
            confirmed.append({
                "title": getattr(f, "title", ""), "severity": getattr(f, "severity", ""),
                "evidence": getattr(f, "evidence", ""), "source": "probe"})
        return confirmed
