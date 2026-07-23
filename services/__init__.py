"""
services/ — QAX Service Layer
==============================
The clean, UI-agnostic core of QAX.

Every capability (generate, export, execute, report) lives here as a plain
class/function that takes parameters and returns typed results. Services
NEVER print, call input(), call sys.exit(), or read os.environ as state —
those are the concerns of the *faces* that sit on top of this layer:

    CLI (main.py)  ──┐
    UI (Streamlit) ──┤
    MCP server     ──┼──►  services/  ──►  core/ + execution/
    SaaS API       ──┘

Design rules (enforced across all services):
  1. No print / input / sys.exit / argparse inside a service.
  2. Progress is emitted via a ProgressReporter, never printed.
  3. dry_run is an explicit parameter (carried on QAXContext), never a global.
  4. Errors raise typed QAXError subclasses or are captured in result objects.
  5. Configuration and secrets arrive via QAXContext, not scattered load_dotenv.

This is the shared foundation that makes MCP and SaaS possible without
rewriting the generation logic.
"""

from services.context import QAXContext
from services.exceptions import (
    QAXConfigError,
    QAXError,
    QAXExecutionError,
    QAXGenerationError,
)
from services.progress import (
    NullProgressReporter,
    PrintProgressReporter,
    ProgressEvent,
    ProgressReporter,
)
from services.results import (
    BatchGenerationResult,
    ComprehensionResult,
    CriticResult,
    ExecutionResult,
    ExportResult,
    FailureVerdict,
    GenerationResult,
    InvestigationResult,
    InvestigationVerdict,
    ReportResult,
    TourResult,
)

__all__ = [
    "QAXContext",
    "QAXError",
    "QAXConfigError",
    "QAXGenerationError",
    "QAXExecutionError",
    "ProgressReporter",
    "ProgressEvent",
    "PrintProgressReporter",
    "NullProgressReporter",
    "GenerationResult",
    "BatchGenerationResult",
    "ComprehensionResult",
    "ExportResult",
    "ExecutionResult",
    "ReportResult",
    "CriticResult",
    "FailureVerdict",
    "InvestigationResult",
    "InvestigationVerdict",
    "TourResult",
]
