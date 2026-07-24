"""
services/results.py — Typed Service Results
============================================
Services return these dataclasses instead of loose dicts, so every face knows
exactly what fields exist. Each result also exposes to_dict() for the faces
that need plain JSON (MCP tool responses, SaaS API payloads).

Phase 1 defines GenerationResult. Export/Execution/Reporting results arrive in
Phase 2 alongside their services.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass
class GenerationResult:
    """
    The outcome of a single GenerationService.generate() call.

    Attributes:
        test_cases (list[dict]): Valid, schema-checked test cases.
        source (str): The source identifier the cases were generated from
            (file path, "story://...", "jira://KEY", etc.).
        module (str | None): Module the cases were attributed to, if known.
        from_cache (bool): True when the result came from the file-level cache
            (no API call was made).
        model (str): Model id that was used (or "cache" when from_cache).
        summary (dict): Stats dict from validate_batch — total / valid_count /
            invalid_count / needs_review_count / avg_confidence.
        invalid (list[dict]): Cases that failed schema validation, each a dict
            with "test_case" and "error" keys.
        error (str | None): Populated only when generation failed outright.
            Normal "zero test cases" is NOT an error — it is success with an
            empty test_cases list.
    """

    test_cases: list[dict] = field(default_factory=list)
    source: str = ""
    module: str | None = None
    from_cache: bool = False
    model: str = ""
    summary: dict = field(default_factory=dict)
    invalid: list[dict] = field(default_factory=list)
    error: str | None = None

    @property
    def count(self) -> int:
        """Number of valid test cases produced."""
        return len(self.test_cases)

    @property
    def success(self) -> bool:
        """True when generation completed without an outright error."""
        return self.error is None

    @property
    def avg_confidence(self) -> float:
        """Average confidence score across produced cases (0.0 if none)."""
        return float(self.summary.get("avg_confidence", 0.0))

    def to_dict(self) -> dict:
        """
        Return a plain-dict form suitable for JSON serialisation.

        Used by the MCP and SaaS faces, which must hand back JSON rather
        than a Python object.

        Returns:
            dict: All fields plus derived count/success/avg_confidence.
        """
        data = asdict(self)
        data["count"] = self.count
        data["success"] = self.success
        return data


@dataclass
class BatchGenerationResult:
    """
    The outcome of generating across a whole source (folder, PR, ...) — i.e. one
    or more input items each producing its own GenerationResult.

    This is what a multi-file source returns: the per-item results plus flattened
    convenience views (all valid test cases together, combined summary) that the
    UI and CLI render directly.

    Attributes:
        input_type (str): The input adapter used — "file", "folder", "jira",
            "pr", or "story".
        source (str): The source identifier that was fetched.
        items (list[GenerationResult]): One result per input item.
        error (str | None): Set only when the source itself could not be
            resolved (bad adapter, nothing fetched). Per-item errors live on the
            individual GenerationResult objects.
    """

    input_type: str = "file"
    source: str = ""
    items: list = field(default_factory=list)
    error: str | None = None

    @property
    def test_cases(self) -> list[dict]:
        """All valid test cases across every item, flattened."""
        cases: list[dict] = []
        for r in self.items:
            cases.extend(r.test_cases)
        return cases

    @property
    def count(self) -> int:
        """Total valid test cases produced across all items."""
        return len(self.test_cases)

    @property
    def item_count(self) -> int:
        """Number of input items processed (files, tickets, ...)."""
        return len(self.items)

    @property
    def invalid_count(self) -> int:
        """Total cases that failed validation across all items."""
        return sum(len(r.invalid) for r in self.items)

    @property
    def success(self) -> bool:
        """True when the source resolved (individual items may still be empty)."""
        return self.error is None

    @property
    def avg_confidence(self) -> float:
        """Average confidence across all produced cases (0.0 if none)."""
        scores = [
            c.get("confidence_score", 0)
            for c in self.test_cases
            if isinstance(c, dict)
        ]
        return round(sum(scores) / len(scores), 2) if scores else 0.0

    @property
    def summary(self) -> dict:
        """Combined stats across all items."""
        return {
            "input_type": self.input_type,
            "items": self.item_count,
            "total": self.count + self.invalid_count,
            "valid_count": self.count,
            "invalid_count": self.invalid_count,
            "avg_confidence": self.avg_confidence,
        }

    def to_dict(self) -> dict:
        """Return a JSON-friendly form for MCP/SaaS faces."""
        return {
            "input_type": self.input_type,
            "source": self.source,
            "success": self.success,
            "error": self.error,
            "item_count": self.item_count,
            "count": self.count,
            "test_cases": self.test_cases,
            "summary": self.summary,
        }


@dataclass
class ComprehensionResult:
    """
    The outcome of RepoComprehensionService.comprehend() — a high-level map of
    the source built before generation, so the generated tests go deeper.

    Attributes:
        overview (str): One or two sentences on what the app/module does.
        main_flows (list[str]): Key user/business flows.
        entry_points (list[str]): Controllers/routes/handlers ("file: role").
        risk_areas (list[str]): Areas to test hardest (auth, payments, ...).
        files (list[dict]): [{"path", "role"}] for the most important files.
        file_count (int): Number of source files read.
        error (str | None): Set only when comprehension failed outright.
    """

    overview: str = ""
    main_flows: list = field(default_factory=list)
    entry_points: list = field(default_factory=list)
    risk_areas: list = field(default_factory=list)
    files: list = field(default_factory=list)
    file_count: int = 0
    error: str | None = None

    @property
    def success(self) -> bool:
        """True when comprehension completed without an outright error."""
        return self.error is None

    def as_context(self) -> str:
        """
        Build a compact text block to inject into generation as app context.

        Returns:
            str: A few labelled lines summarising the app, or "" if empty.
        """
        lines: list[str] = []
        if self.overview:
            lines.append(f"App overview: {self.overview}")
        if self.main_flows:
            lines.append("Main flows: " + "; ".join(str(f) for f in self.main_flows))
        if self.risk_areas:
            lines.append("High-risk areas to test hard: "
                         + ", ".join(str(r) for r in self.risk_areas))
        if self.entry_points:
            lines.append("Entry points: "
                         + "; ".join(str(e) for e in self.entry_points[:8]))
        return "\n".join(lines)

    def to_dict(self) -> dict:
        """Return a JSON-friendly form for MCP/SaaS faces."""
        data = asdict(self)
        data["success"] = self.success
        return data


@dataclass
class FailureVerdict:
    """
    The Critic's verdict on a single failed test.

    Attributes:
        test_case_id (str): The failing test's id.
        title (str): The failing test's title.
        classification (str): "real_bug" | "broken_test" | "uncertain".
        reason (str): One-sentence explanation of the verdict.
        suggestion (str): How to fix the test, or how to reproduce the bug.
        failed_step (int | None): Step number where it first failed.
    """

    test_case_id: str = ""
    title: str = ""
    classification: str = "uncertain"
    reason: str = ""
    suggestion: str = ""
    failed_step: int | None = None

    @property
    def is_real_bug(self) -> bool:
        return self.classification == "real_bug"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class CriticResult:
    """
    The outcome of CriticService.review() — the "verify, don't trust" pass that
    separates real application bugs from broken/self-inflicted test failures.

    Attributes:
        verdicts (list[FailureVerdict]): One verdict per failed test.
        error (str | None): Set only when the review failed outright.
    """

    verdicts: list = field(default_factory=list)
    error: str | None = None

    @property
    def real_bugs(self) -> list:
        """Verdicts classified as genuine application bugs."""
        return [v for v in self.verdicts if v.classification == "real_bug"]

    @property
    def broken_tests(self) -> list:
        """Verdicts classified as the test's own fault."""
        return [v for v in self.verdicts if v.classification == "broken_test"]

    @property
    def uncertain(self) -> list:
        """Verdicts the Critic could not classify confidently."""
        return [v for v in self.verdicts if v.classification == "uncertain"]

    @property
    def success(self) -> bool:
        return self.error is None

    def summary(self) -> str:
        """One-line human summary of the review."""
        return (f"{len(self.real_bugs)} real bug(s), "
                f"{len(self.broken_tests)} broken test(s), "
                f"{len(self.uncertain)} uncertain")

    def to_dict(self) -> dict:
        return {
            "real_bugs": len(self.real_bugs),
            "broken_tests": len(self.broken_tests),
            "uncertain": len(self.uncertain),
            "verdicts": [v.to_dict() for v in self.verdicts],
            "error": self.error,
            "success": self.success,
        }


@dataclass
class AppModel:
    """
    A whole-system model of an application, built by reading the ENTIRE repo (not
    one file). This is the substrate every later phase reasons over — the fix for
    QAX's file-centric blindness, where the worst bugs live BETWEEN files (a secret
    in config, a schema-vs-security contradiction, a frontend/backend contract gap).

    All sections are plain lists/dicts (the shape comes from one comprehension
    call), with convenience views on top.

    Attributes:
        root (str): The repo path that was modelled.
        files (list[dict]): [{path, role}] — role e.g. router/config/security/
            schema/model/frontend-api/frontend-component/other.
        endpoints (list[dict]): [{method, path, auth_required, request_fields,
            response_fields, status_codes, file}].
        entities (list[dict]): [{name, fields:[{name, type, constraints}], file}].
        config (list[dict]): [{key, default, is_secret, risk}] — secrets with a
            hardcoded/placeholder default are the crown-jewel finding here.
        auth (dict): {scheme, token_type, secret_source, password_hash}.
        contracts (list[dict]): [{frontend_call, backend_endpoint, mismatch}].
        risks (list[str]): High-level cross-file concerns worth testing hardest.
        file_count (int): How many source files were read.
        error (str | None): Set only when modelling failed outright.
    """

    root: str = ""
    files: list = field(default_factory=list)
    endpoints: list = field(default_factory=list)
    entities: list = field(default_factory=list)
    config: list = field(default_factory=list)
    auth: dict = field(default_factory=dict)
    contracts: list = field(default_factory=list)
    risks: list = field(default_factory=list)
    file_count: int = 0
    error: str | None = None

    @property
    def success(self) -> bool:
        return self.error is None

    @property
    def exposed_secrets(self) -> list:
        """Config entries that are secrets AND carry a hardcoded/placeholder default."""
        out = []
        for c in self.config:
            if not isinstance(c, dict):
                continue
            default = c.get("default")
            if c.get("is_secret") and default not in (None, "", "None"):
                out.append(c)
        return out

    @property
    def contract_mismatches(self) -> list:
        """Frontend/backend contract entries that disagree."""
        return [c for c in self.contracts if isinstance(c, dict) and c.get("mismatch")]

    def as_context(self) -> str:
        """
        Render a compact whole-system brief for injection into generation,
        detectors, and red-team — so tests reason over the system, not one file.
        """
        lines: list[str] = []
        if self.auth:
            lines.append("Auth: " + ", ".join(f"{k}={v}" for k, v in self.auth.items() if v))
        if self.endpoints:
            lines.append(f"Endpoints ({len(self.endpoints)}):")
            for e in self.endpoints[:30]:
                a = " [auth]" if e.get("auth_required") else ""
                lines.append(f"  {e.get('method','?')} {e.get('path','?')}{a}"
                             + (f" → {e.get('status_codes')}" if e.get('status_codes') else ""))
        if self.exposed_secrets:
            lines.append("EXPOSED SECRETS (hardcoded/placeholder default — high risk):")
            for s in self.exposed_secrets:
                lines.append(f"  {s.get('key')} = {str(s.get('default'))[:40]!r} ({s.get('risk','')})")
        if self.contract_mismatches:
            lines.append("CONTRACT MISMATCHES (frontend vs backend):")
            for c in self.contract_mismatches[:10]:
                lines.append(f"  {c.get('frontend_call')} vs {c.get('backend_endpoint')}: {c.get('mismatch')}")
        if self.risks:
            lines.append("Cross-file risks: " + "; ".join(str(r) for r in self.risks[:10]))
        return "\n".join(lines)

    def to_dict(self) -> dict:
        data = asdict(self)
        data["success"] = self.success
        data["exposed_secret_count"] = len(self.exposed_secrets)
        data["contract_mismatch_count"] = len(self.contract_mismatches)
        return data


@dataclass
class ProbeFinding:
    """
    The result of one active probe that tried to PROVE an exploit the standard
    HTTP/UI runner can't express (forging a token, crashing with oversized input).

    Attributes:
        probe (str): Probe id (e.g. "forge-jwt", "crash-fuzz").
        title (str): What was proven (or attempted).
        exploited (bool): True when the exploit actually worked — a confirmed bug.
        severity (str): "P1".."P4".
        evidence (str): The concrete proof captured.
        target (str): Endpoint/asset the probe hit.
    """

    probe: str = ""
    title: str = ""
    exploited: bool = False
    severity: str = "P3"
    evidence: str = ""
    target: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ProbeResult:
    """
    The outcome of ProbeService.run() — active exploit attempts. A probe that
    succeeds is proof on its own (deterministic), so its findings are confirmed.

    Attributes:
        findings (list[ProbeFinding]): All probe outcomes.
        error (str | None): Set only when the run failed outright.
    """

    findings: list = field(default_factory=list)
    error: str | None = None

    @property
    def success(self) -> bool:
        return self.error is None

    @property
    def exploited(self) -> list:
        """Probes that actually broke in — confirmed bugs."""
        return [f for f in self.findings if f.exploited]

    def summary(self) -> str:
        """One-line human summary."""
        return f"{len(self.exploited)} exploit(s) proven of {len(self.findings)} probe(s) run"

    def to_dict(self) -> dict:
        return {
            "exploited": len(self.exploited),
            "run": len(self.findings),
            "findings": [f.to_dict() for f in self.findings],
            "error": self.error,
            "success": self.success,
        }


@dataclass
class Finding:
    """
    One suspected defect surfaced by a detector over the AppModel — a candidate to
    be tested and proven later, not yet a confirmed bug.

    Attributes:
        title (str): Short description of the suspected issue.
        pattern (str): The failure-pattern id it matches (e.g. "hardcoded-secret").
        severity (str): "P1" | "P2" | "P3" | "P4".
        evidence (str): What in the app triggered it.
        where (str): File / endpoint / field the finding points at.
        source (str): "static" (deterministic rule) or "knowledge" (model pass).
        confidence (str): "low" | "medium" | "high".
    """

    title: str = ""
    pattern: str = ""
    severity: str = "P3"
    evidence: str = ""
    where: str = ""
    source: str = "static"
    confidence: str = "medium"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class DetectionResult:
    """
    The outcome of DetectorService.detect() — suspected defects found by scanning
    the AppModel against the failure-mode knowledge base.

    Attributes:
        findings (list[Finding]): Suspected issues, most-severe first.
        error (str | None): Set only when detection failed outright.
    """

    findings: list = field(default_factory=list)
    error: str | None = None

    @property
    def success(self) -> bool:
        return self.error is None

    def by_severity(self, sev: str) -> list:
        """Findings at a given severity (P1..P4)."""
        return [f for f in self.findings if f.severity == sev]

    @property
    def critical(self) -> list:
        """P1 findings — the ones that matter most."""
        return self.by_severity("P1")

    def summary(self) -> str:
        """One-line human summary by severity."""
        counts = {}
        for f in self.findings:
            counts[f.severity] = counts.get(f.severity, 0) + 1
        parts = [f"{counts[s]}×{s}" for s in ("P1", "P2", "P3", "P4") if counts.get(s)]
        return f"{len(self.findings)} suspect(s): " + (", ".join(parts) if parts else "none")

    def to_dict(self) -> dict:
        return {
            "count": len(self.findings),
            "findings": [f.to_dict() for f in self.findings],
            "error": self.error,
            "success": self.success,
        }


@dataclass
class TourResult:
    """
    The outcome of TourService.tour() — the app's real UI surface, observed by
    driving a browser over the running app (the UNDERSTAND-mode "live tour").

    Attributes:
        base_url (str): The app root that was toured.
        screens (list[dict]): One per visited screen —
            {url, title, elements: [{tag, type, testid, selector, label}], errors}.
        login_ok (bool | None): Whether the optional login step succeeded.
        error (str | None): Set only when the tour failed outright.
    """

    base_url: str = ""
    screens: list = field(default_factory=list)
    login_ok: bool | None = None
    error: str | None = None

    @property
    def success(self) -> bool:
        return self.error is None

    @property
    def screen_count(self) -> int:
        return len(self.screens)

    @property
    def element_count(self) -> int:
        """Total interactive elements observed across all screens."""
        return sum(len(s.get("elements", [])) for s in self.screens)

    @property
    def console_errors(self) -> list:
        """Console/page errors captured across all screens (with their screen)."""
        out = []
        for s in self.screens:
            for e in s.get("errors", []):
                out.append({"url": s.get("url", ""), "error": e})
        return out

    def as_context(self) -> str:
        """
        Build an "OBSERVED UI SURFACE" text block to inject into generation, so
        tests are grounded in real screens and use real selectors.

        Returns:
            str: A compact labelled block, or "" when nothing was observed.
        """
        if not self.screens:
            return ""
        lines = ["OBSERVED UI SURFACE (from a live tour — use these REAL selectors, "
                 "do not invent element names):"]
        for s in self.screens:
            url = s.get("url", "")
            title = s.get("title", "")
            lines.append(f"\nScreen {url}" + (f" — {title}" if title else ""))
            for el in s.get("elements", [])[:40]:
                sel = el.get("selector") or "(no stable selector)"
                kind = el.get("tag", "")
                if el.get("type"):
                    kind += f"[{el['type']}]"
                label = el.get("label", "")
                lines.append(f"  - {sel}  · {kind}" + (f" · \"{label}\"" if label else ""))
            if s.get("errors"):
                lines.append(f"  ! {len(s['errors'])} console/page error(s) on this screen")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        data = asdict(self)
        data["success"] = self.success
        data["screen_count"] = self.screen_count
        data["element_count"] = self.element_count
        return data


@dataclass
class InvestigationVerdict:
    """
    The Investigator's verdict on one suspected bug after trying to reproduce it.

    Attributes:
        test_case_id (str): The test's id.
        title (str): The test's title.
        status (str): "confirmed" | "not_reproduced" | "inconclusive".
        reproduced (bool): Whether the failure recurred on re-run.
        reason (str): One-sentence explanation, citing the evidence.
        evidence (str): The intended request vs the actual response captured.
    """

    test_case_id: str = ""
    title: str = ""
    status: str = "inconclusive"
    reproduced: bool = False
    reason: str = ""
    evidence: str = ""

    @property
    def is_confirmed(self) -> bool:
        return self.status == "confirmed"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class InvestigationResult:
    """
    The outcome of InvestigatorService.investigate() — the "verify before file"
    pass that reproduces each suspected bug on the live app and either confirms
    it with evidence or downgrades it (bias toward not_reproduced).

    Attributes:
        verdicts (list[InvestigationVerdict]): One verdict per suspected bug.
        error (str | None): Set only when the investigation failed outright.
        note (str): Optional human note (e.g. why nothing could be reproduced).
    """

    verdicts: list = field(default_factory=list)
    error: str | None = None
    note: str = ""

    @property
    def confirmed(self) -> list:
        """Bugs reproduced AND judged to be genuine application defects."""
        return [v for v in self.verdicts if v.status == "confirmed"]

    @property
    def not_reproduced(self) -> list:
        """Suspected bugs that did not hold up — false alarms."""
        return [v for v in self.verdicts if v.status == "not_reproduced"]

    @property
    def inconclusive(self) -> list:
        """Suspected bugs that could not be verified either way."""
        return [v for v in self.verdicts if v.status == "inconclusive"]

    @property
    def success(self) -> bool:
        return self.error is None

    def summary(self) -> str:
        """One-line human summary of the investigation."""
        return (f"{len(self.confirmed)} confirmed, "
                f"{len(self.not_reproduced)} not reproduced, "
                f"{len(self.inconclusive)} inconclusive")

    def to_dict(self) -> dict:
        return {
            "confirmed": len(self.confirmed),
            "not_reproduced": len(self.not_reproduced),
            "inconclusive": len(self.inconclusive),
            "verdicts": [v.to_dict() for v in self.verdicts],
            "error": self.error,
            "note": self.note,
            "success": self.success,
        }


@dataclass
class BugReportEntry:
    """
    One professional bug report — a single root-cause family, however many tests
    surfaced it. Modelled on the user's real bug-report craft.

    Attributes:
        title (str): Summary-line — "[Area] symptom — decisive detail".
        severity (str): "P1" | "P2" | "P3" | "P4".
        severity_reason (str): One-line justification for the severity.
        description (str): What happens and why it matters.
        steps_to_reproduce (list[str]): Numbered steps, from a clean start.
        expected (str): What should happen.
        actual (str): What actually happens (with evidence).
        root_cause (str): Mechanism-level hypothesis.
        impact (str): Who/what this affects.
        occurrences (list[str]): Titles of the failing tests in this family.
    """

    title: str = ""
    severity: str = "P3"
    severity_reason: str = ""
    description: str = ""
    steps_to_reproduce: list = field(default_factory=list)
    expected: str = ""
    actual: str = ""
    root_cause: str = ""
    impact: str = ""
    occurrences: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    def to_markdown(self) -> str:
        """Render this entry as a self-contained markdown bug report."""
        sev = {"P1": "P1 / Critical", "P2": "P2 / High",
               "P3": "P3 / Medium", "P4": "P4 / Low"}.get(self.severity, self.severity)
        lines = [f"## {self.title}", ""]
        lines.append(f"**Severity:** {sev}"
                     + (f" — {self.severity_reason}" if self.severity_reason else ""))
        if self.occurrences:
            lines.append(f"**Surfaced by:** {len(self.occurrences)} test(s) — "
                         + "; ".join(str(o) for o in self.occurrences[:6]))
        if self.description:
            lines += ["", "### Description", self.description]
        if self.steps_to_reproduce:
            lines += ["", "### Steps to reproduce"]
            lines += [f"{i}. {s}" for i, s in enumerate(self.steps_to_reproduce, 1)]
        if self.expected or self.actual:
            lines += ["", "### Expected vs actual",
                      f"- **Expected:** {self.expected}",
                      f"- **Actual:** {self.actual}"]
        if self.root_cause:
            lines += ["", "### Root cause (hypothesis)", self.root_cause]
        if self.impact:
            lines += ["", "### Impact", self.impact]
        return "\n".join(lines)


@dataclass
class BugReportResult:
    """
    The outcome of ReporterService.report() — confirmed bugs grouped into
    root-cause families and written up as professional reports.

    Attributes:
        entries (list[BugReportEntry]): One per root-cause family.
        error (str | None): Set only when reporting failed outright.
    """

    entries: list = field(default_factory=list)
    error: str | None = None

    @property
    def success(self) -> bool:
        return self.error is None

    @property
    def occurrence_count(self) -> int:
        """Total failing tests folded into the reports."""
        return sum(len(e.occurrences) for e in self.entries)

    def summary(self) -> str:
        """One-line human summary."""
        n = len(self.entries)
        return (f"{n} bug{'' if n == 1 else 's'} "
                f"from {self.occurrence_count} failing test(s)")

    def to_markdown(self) -> str:
        """Render the whole report as one markdown document."""
        if not self.entries:
            return "# Bug report\n\nNo confirmed bugs.\n"
        head = [f"# Bug report — {self.summary()}", ""]
        return "\n".join(head) + "\n\n---\n\n".join(e.to_markdown() for e in self.entries)

    def to_dict(self) -> dict:
        return {
            "count": len(self.entries),
            "occurrence_count": self.occurrence_count,
            "entries": [e.to_dict() for e in self.entries],
            "error": self.error,
            "success": self.success,
        }


@dataclass
class ExportResult:
    """
    The outcome of an ExportService.export() call.

    Mirrors the adapter result contract so no information is lost, but as a
    typed object every face can rely on.

    Attributes:
        target (str): Adapter key used — "xray", "zephyr", "excel", "csv", "none".
        success (bool): True when at least one case was exported.
        exported_count (int): Number of cases successfully exported.
        failed_count (int): Number of cases that failed.
        references (list[str]): External identifiers — Xray keys, file paths, etc.
        error (str | None): First/summary error, or None on success.
    """

    target: str = "none"
    success: bool = False
    exported_count: int = 0
    failed_count: int = 0
    references: list = field(default_factory=list)
    error: str | None = None

    def to_dict(self) -> dict:
        """Return a plain-dict form suitable for JSON serialisation."""
        return asdict(self)


@dataclass
class ExecutionResult:
    """
    The outcome of an ExecutionService.execute() call.

    Wraps the raw batch dict produced by the execution engine (so nothing is
    lost, and ReportingService can build a report from it) while exposing the
    common summary fields as convenient properties.

    Attributes:
        mode (str): Execution mode — "ui", "api", "both", or "none".
        raw (dict): The full batch-result dict from the runner.
        error (str | None): Populated only when execution failed outright.
    """

    mode: str = "none"
    raw: dict = field(default_factory=dict)
    error: str | None = None

    @property
    def total(self) -> int:
        """Total test cases attempted."""
        return int(self.raw.get("total", 0))

    @property
    def passed(self) -> int:
        """Count of PASS results."""
        return int(self.raw.get("passed", 0))

    @property
    def failed(self) -> int:
        """Count of FAIL results."""
        return int(self.raw.get("failed", 0))

    @property
    def errors(self) -> int:
        """Count of ERROR results."""
        return int(self.raw.get("errors", 0))

    @property
    def pass_rate(self) -> float:
        """Percentage of passed tests (0.0 when nothing ran)."""
        return float(self.raw.get("pass_rate", 0.0))

    @property
    def results(self) -> list:
        """Per-test-case result dicts."""
        return self.raw.get("results", [])

    @property
    def success(self) -> bool:
        """True when execution completed without an outright error."""
        return self.error is None

    def to_batch_dict(self) -> dict:
        """
        Return the raw batch dict for ReportingService / ResultsReporter.

        Returns:
            dict: The runner's batch-result dict (never None).
        """
        return self.raw or {
            "total": 0, "passed": 0, "failed": 0, "errors": 0, "skipped": 0,
            "pass_rate": 0.0, "results": [],
        }

    def to_dict(self) -> dict:
        """Return a JSON-friendly summary (excludes the heavy raw results)."""
        return {
            "mode": self.mode,
            "success": self.success,
            "total": self.total,
            "passed": self.passed,
            "failed": self.failed,
            "errors": self.errors,
            "pass_rate": self.pass_rate,
            "error": self.error,
        }


@dataclass
class ReportResult:
    """
    The outcome of a ReportingService.report() call.

    Attributes:
        html_path (str): Path to the generated standalone HTML report ("" if none).
        bugs_created (list[str]): References to any bug tickets created.
        error (str | None): Populated only when reporting failed outright.
    """

    html_path: str = ""
    bugs_created: list = field(default_factory=list)
    error: str | None = None

    @property
    def success(self) -> bool:
        """True when reporting completed without an outright error."""
        return self.error is None

    def to_dict(self) -> dict:
        """Return a plain-dict form suitable for JSON serialisation."""
        data = asdict(self)
        data["success"] = self.success
        return data
