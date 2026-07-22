"""
services/exceptions.py — Typed Service-Layer Errors
====================================================
Services raise these instead of calling sys.exit() so every face (CLI, UI,
MCP, SaaS) can decide how to present the failure. A CLI may print and exit;
a web API may return a 4xx/5xx; an MCP tool may return an error payload.

Hierarchy:
    QAXError                 — base for everything the service layer raises
      QAXConfigError         — missing/invalid credentials or configuration
      QAXGenerationError     — failure during test-case generation
      QAXExecutionError      — failure during test execution
"""


class QAXError(Exception):
    """
    Base class for all QAX service-layer errors.

    Catch this to handle any expected failure raised by a service without
    catching unrelated built-in exceptions.
    """


class QAXConfigError(QAXError):
    """
    Raised when required configuration or credentials are missing or invalid.

    Example: ANTHROPIC_API_KEY not set when a generation is requested.
    """


class QAXGenerationError(QAXError):
    """
    Raised when test-case generation fails for a reason the caller should see.

    Note: routine "no test cases produced" is NOT an error — that returns an
    empty GenerationResult. This is reserved for genuine failures.
    """


class QAXExecutionError(QAXError):
    """
    Raised when the execution phase fails in a way the caller should see.

    Reserved for future ExecutionService use (Phase 2).
    """
