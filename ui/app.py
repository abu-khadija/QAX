"""
ui/app.py — QAX Streamlit UI
==============================
Professional dark-theme interface for QAX — the AI QA Engineer.
Single-file, 4-tab design:
  Tab 1 — Analyze  : generate test cases with live streaming output
  Tab 2 — Run      : config-driven pipeline with progress tracking
  Tab 3 — Dashboard: stats, trends, module breakdown, cache info
  Tab 4 — Settings : connections, projects, model config, danger zone

Requires: streamlit>=1.28, plotly>=5.0
Run via:  python main.py ui  (from the QAX project root)
      or: streamlit run ui/app.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

import streamlit as st

# ── Resolve QAX root (parent of ui/) ─────────────────────────────────────────
QAX_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(QAX_ROOT))

# Load .env before importing project modules
from dotenv import load_dotenv
load_dotenv(QAX_ROOT / ".env", override=True)

# ── Page config (must be first Streamlit call) ────────────────────────────────
st.set_page_config(
    page_title="QAX",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ══════════════════════════════════════════════════════════════════════════════
# CSS
# ══════════════════════════════════════════════════════════════════════════════

st.markdown("""
<style>
/* ── Cards ──────────────────────────────────────────────────────────── */
.qax-card {
    background: #111827;
    border: 1px solid #1f2937;
    border-radius: 12px;
    padding: 18px 20px;
    margin-bottom: 10px;
}
.qax-card-title {
    font-size: 1rem;
    font-weight: 700;
    color: #f1f5f9;
    margin-bottom: 8px;
}

/* ── Badges ──────────────────────────────────────────────────────────── */
.badge {
    display: inline-block;
    padding: 2px 9px;
    border-radius: 5px;
    font-size: 0.72rem;
    font-weight: 600;
    margin-right: 4px;
    letter-spacing: 0.02em;
}
.badge-purple { background: rgba(124,58,237,.18); color: #c4b5fd;
                border: 1px solid rgba(124,58,237,.35); }
.badge-green  { background: rgba(34,197,94,.15);  color: #4ade80;
                border: 1px solid rgba(34,197,94,.30); }
.badge-red    { background: rgba(239,68,68,.15);  color: #f87171;
                border: 1px solid rgba(239,68,68,.30); }
.badge-amber  { background: rgba(245,158,11,.15); color: #fbbf24;
                border: 1px solid rgba(245,158,11,.30); }
.badge-teal   { background: rgba(20,184,166,.15); color: #2dd4bf;
                border: 1px solid rgba(20,184,166,.30); }
.badge-gray   { background: rgba(156,163,175,.12);color: #9ca3af;
                border: 1px solid rgba(156,163,175,.25); }

/* ── Confidence bar ──────────────────────────────────────────────────── */
.conf-bar {
    background: #1f2937;
    border-radius: 4px;
    height: 6px;
    overflow: hidden;
    margin-top: 6px;
}
.conf-fill { height: 6px; border-radius: 4px; }

/* ── Terminal box ────────────────────────────────────────────────────── */
.terminal {
    background: #0d1117;
    border: 1px solid #30363d;
    border-radius: 8px;
    padding: 12px 16px;
    font-family: 'Monaco', 'Menlo', 'Consolas', monospace;
    font-size: 0.76rem;
    color: #7ee787;
    white-space: pre-wrap;
    word-break: break-all;
    max-height: 340px;
    overflow-y: auto;
    line-height: 1.5;
}

/* ── Empty state ─────────────────────────────────────────────────────── */
.empty-state {
    border: 2px dashed #1f2937;
    border-radius: 12px;
    padding: 56px 24px;
    text-align: center;
    color: #6b7280;
}

/* ── Mode card ────────────────────────────────────────────────────────── */
.mode-card {
    background: #111827;
    border: 1px solid #1f2937;
    border-radius: 10px;
    padding: 16px;
    text-align: center;
    cursor: pointer;
    transition: border-color .2s;
}
.mode-card:hover { border-color: #7c3aed; }
.mode-card-icon  { font-size: 1.6rem; }
.mode-card-title { font-weight: 700; font-size: 0.9rem; margin-top: 6px; }
.mode-card-desc  { font-size: 0.75rem; color: #6b7280; margin-top: 4px; }

/* ── Env row ──────────────────────────────────────────────────────────── */
.env-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 8px 0;
    border-bottom: 1px solid #1f2937;
    font-size: 0.85rem;
}
.env-val {
    font-family: monospace;
    font-size: 0.78rem;
    color: #9ca3af;
}

/* ── Sidebar logo ─────────────────────────────────────────────────────── */
.qax-logo {
    background: linear-gradient(135deg, #7c3aed, #14b8a6);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    background-clip: text;
    font-size: 2.4rem;
    font-weight: 900;
    letter-spacing: -2px;
    line-height: 1;
}
.qax-sub {
    color: #6b7280;
    font-size: 0.78rem;
    margin-top: 2px;
    letter-spacing: .06em;
    text-transform: uppercase;
}

/* ── Metric row ───────────────────────────────────────────────────────── */
.stat-pill {
    background: #111827;
    border: 1px solid #1f2937;
    border-radius: 8px;
    padding: 10px 14px;
    text-align: center;
    font-size: 0.78rem;
    color: #9ca3af;
}
.stat-num {
    font-size: 1.4rem;
    font-weight: 800;
    color: #f1f5f9;
    display: block;
}

/* ── Test-case card confidence borders ────────────────────────────────── */
.tc-high   { border-left: 4px solid #22c55e !important; }
.tc-medium { border-left: 4px solid #f59e0b !important; }
.tc-low    { border-left: 4px solid #ef4444 !important; }

/* ── Onboarding banner ────────────────────────────────────────────────── */
.onboarding-banner {
    background: linear-gradient(135deg, rgba(124,58,237,.13), rgba(20,184,166,.08));
    border: 1px solid rgba(124,58,237,.35);
    border-radius: 14px;
    padding: 32px 28px;
    margin: 0 0 20px 0;
    text-align: center;
}
.onboarding-step {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    background: rgba(255,255,255,.04);
    border: 1px solid #1f2937;
    border-radius: 8px;
    padding: 6px 12px;
    font-size: 0.8rem;
    color: #9ca3af;
    margin: 4px;
}

/* ── Generation progress steps ────────────────────────────────────────── */
.progress-step {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 6px 0;
    font-size: 0.84rem;
}
.step-done    { color: #22c55e; }
.step-active  { color: #a78bfa; }
.step-pending { color: #374151; }

/* ── Connection pill badges (sidebar) ─────────────────────────────────── */
.conn-pill {
    display: inline-flex;
    align-items: center;
    gap: 5px;
    border-radius: 20px;
    padding: 3px 10px 3px 7px;
    font-size: 0.74rem;
    font-weight: 600;
    margin: 2px 0;
    border: 1px solid;
}
.pill-ok  { background: rgba(34,197,94,.1); border-color: rgba(34,197,94,.3); color: #4ade80; }
.pill-err { background: rgba(239,68,68,.1); border-color: rgba(239,68,68,.3); color: #f87171; }
.pill-warn{ background: rgba(245,158,11,.1);border-color: rgba(245,158,11,.3);color: #fbbf24; }

/* ── Mode card disabled ───────────────────────────────────────────────── */
.mode-card-disabled { opacity: 0.42; cursor: not-allowed; filter: grayscale(.4); }
.mode-cred-warn     { font-size: 0.7rem; color: #f87171; margin-top: 5px; }

/* ── Setup progress bar (settings) ───────────────────────────────────── */
.setup-progress {
    background: #1f2937;
    border-radius: 4px;
    height: 8px;
    overflow: hidden;
    margin: 6px 0 2px;
}
.setup-fill {
    height: 8px;
    border-radius: 4px;
    background: linear-gradient(90deg, #7c3aed, #14b8a6);
    transition: width .4s ease;
}

/* ── Styled action buttons ────────────────────────────────────────────── */
.btn-accept-wrap button { background: rgba(34,197,94,.13) !important;
    color: #4ade80 !important; border: 1px solid rgba(34,197,94,.35) !important; }
.btn-accept-wrap button:hover { background: rgba(34,197,94,.22) !important; }
.btn-reject-wrap button { background: rgba(239,68,68,.13) !important;
    color: #f87171 !important; border: 1px solid rgba(239,68,68,.35) !important; }
.btn-reject-wrap button:hover { background: rgba(239,68,68,.22) !important; }
.btn-push-wrap   button { background: rgba(124,58,237,.13) !important;
    color: #c4b5fd !important; border: 1px solid rgba(124,58,237,.35) !important; }
.btn-push-wrap   button:hover { background: rgba(124,58,237,.22) !important; }
</style>
""", unsafe_allow_html=True)

# ── Session state defaults ────────────────────────────────────────────────────
_ss_defaults: dict = {
    "active_project": os.getenv("DEFAULT_PROJECT", "example"),
    "results":        None,
    "run_results":    None,
    "last_file":      None,
    "last_project":   None,
    "onboarded":      False,  # set True after first successful generation
}
for _k, _v in _ss_defaults.items():
    if _k not in st.session_state:
        st.session_state[_k] = _v


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def run_qax_command(
    args: list[str],
    stream_to: st.delta_generator.DeltaGenerator | None = None,
) -> tuple[int, str]:
    """
    Run `python main.py <args>` from the QAX root directory.

    If stream_to is an st.empty() placeholder, updates it line-by-line so
    the terminal output appears live in the UI.

    Args:
        args: CLI arguments after `main.py`, e.g. ["analyze", "--file", "..."].
        stream_to: An st.empty() placeholder to stream output into.
                   If None, output is captured and returned only at the end.

    Returns:
        tuple[int, str]: (return_code, full_output_text)
    """
    cmd = [sys.executable, str(QAX_ROOT / "main.py")] + args
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            cwd=str(QAX_ROOT),
        )
        lines: list[str] = []
        if stream_to is not None:
            for line in proc.stdout:  # type: ignore[union-attr]
                lines.append(line)
                stream_to.markdown(
                    f'<div class="terminal">{"".join(lines)}</div>',
                    unsafe_allow_html=True,
                )
        else:
            stdout_data, _ = proc.communicate()
            lines = [stdout_data]

        proc.wait()
        return proc.returncode, "".join(lines)
    except Exception as exc:
        err = f"[QAX UI] Command failed: {exc}"
        if stream_to:
            stream_to.markdown(f'<div class="terminal">{err}</div>', unsafe_allow_html=True)
        return 1, err


# ══════════════════════════════════════════════════════════════════════════════
# Service-layer progress bridge
# ══════════════════════════════════════════════════════════════════════════════

from services.progress import ProgressReporter as _ServiceProgressReporter


class _UIProgressReporter(_ServiceProgressReporter):
    """
    Bridge the GenerationService's progress events onto the Analyze tab's step
    checklist. This replaces the old approach of scraping stdout lines from a
    subprocess — the service now drives the UI directly with structured events.
    """

    # Service event step -> UI step keys to mark done when it fires.
    _STEP_MAP = {
        "pre_check": ("risk", "cache"),
        "cache":     ("cache",),
        "generate":  ("risk", "cache", "claude"),
        "validate":  ("risk", "cache", "claude", "valid"),
    }

    def __init__(self, steps: dict, placeholder, render) -> None:
        """
        Args:
            steps: The Analyze tab's _steps dict (mutated in place).
            placeholder: An st.empty() placeholder to re-render into.
            render: A function(steps) -> HTML string.
        """
        super().__init__()
        self._steps = steps
        self._placeholder = placeholder
        self._render = render

    def _handle(self, event) -> None:
        """Mark the mapped steps done and re-render the checklist."""
        for key in self._STEP_MAP.get(event.step, ()):
            if key in self._steps:
                self._steps[key]["done"] = True
        try:
            self._placeholder.markdown(self._render(self._steps), unsafe_allow_html=True)
        except Exception:
            pass


@st.cache_data(ttl=30)
def get_projects() -> list[str]:
    """
    Scan config/projects/ and return a sorted list of project names.

    Returns:
        list[str]: Project folder names, e.g. ["example", "example2", "example3"].
    """
    projects_dir = QAX_ROOT / "config" / "projects"
    if not projects_dir.exists():
        default = os.getenv("DEFAULT_PROJECT", "")
        return [default] if default else []
    found = sorted(p.name for p in projects_dir.iterdir() if p.is_dir())
    if not found:
        default = os.getenv("DEFAULT_PROJECT", "")
        return [default] if default else []
    return found


@st.cache_data(ttl=10)
def load_all_test_cases() -> list[dict]:
    """
    Load all test cases from every output/test_cases_*.json file.

    Returns:
        list[dict]: Flat list of all test case dicts across all output files.
    """
    output_dir = QAX_ROOT / "output"
    all_cases: list[dict] = []
    for f in sorted(output_dir.glob("test_cases_*.json"), reverse=True):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            if isinstance(data, list):
                all_cases.extend(data)
        except Exception:
            continue
    return all_cases


def load_latest_test_cases() -> list[dict]:
    """
    Load test cases from the most recently generated output JSON file.

    Returns:
        list[dict]: Test cases from the latest file, or [] if none found.
    """
    output_dir = QAX_ROOT / "output"
    files = sorted(output_dir.glob("test_cases_*.json"), reverse=True)
    if not files:
        return []
    try:
        return json.loads(files[0].read_text(encoding="utf-8"))
    except Exception:
        return []


def get_connection_status() -> dict[str, bool | str]:
    """
    Check whether each required service is configured via env vars.

    Does not make actual API calls (instant check from .env values).

    Returns:
        dict: Keys: claude, jira, xray, dry_run. Values: bool (or str for dry_run).
    """
    return {
        "claude":   bool(os.getenv("ANTHROPIC_API_KEY")),
        "jira":     bool(os.getenv("JIRA_BASE_URL") and os.getenv("JIRA_EMAIL") and os.getenv("JIRA_API_TOKEN")),
        "xray":     bool(os.getenv("XRAY_CLIENT_ID") and os.getenv("XRAY_CLIENT_SECRET")),
        "dry_run":  os.getenv("DRY_RUN", "true").lower() == "true",
    }


@st.cache_data(ttl=30)
def get_feedback_stats() -> dict:
    """
    Load acceptance statistics from FeedbackStore.

    Returns:
        dict: Stats dict from FeedbackStore.get_stats(), or empty stats on error.
    """
    try:
        from core.feedback_store import FeedbackStore
        return FeedbackStore().get_stats()
    except Exception:
        return {"total_reviewed": 0, "accepted": 0, "rejected": 0,
                "acceptance_rate": 0.0, "avg_confidence_accepted": 0.0, "by_module": {}}


@st.cache_data(ttl=30)
def get_cache_stats() -> dict:
    """
    Load cache file count and size from CacheManager.

    Returns:
        dict: {"count": int, "size_mb": float}
    """
    try:
        from core.cache_manager import CacheManager
        return CacheManager().stats()
    except Exception:
        return {"count": 0, "size_mb": 0.0}


@st.cache_data(ttl=30)
def get_recent_sessions(limit: int = 5) -> list[dict]:
    """
    Load the most recent run sessions from SessionManager.

    Args:
        limit: Maximum number of sessions to return.

    Returns:
        list[dict]: Session summary dicts.
    """
    try:
        from core.session_manager import SessionManager
        return SessionManager().list_sessions(limit=limit)
    except Exception:
        return []


def badge(text: str, cls: str = "purple") -> str:
    """
    Render a coloured badge as an HTML string.

    Args:
        text: Badge label text.
        cls:  Colour class suffix — purple/green/red/amber/teal/gray.

    Returns:
        str: HTML span element.
    """
    return f'<span class="badge badge-{cls}">{text}</span>'


def confidence_bar(score: float) -> str:
    """
    Render an HTML confidence progress bar.

    Score 0–10 maps to 0–100% width. Colour shifts:
      ≥8.5 green | ≥7.0 teal | ≥5.0 amber | <5.0 red

    Args:
        score: Confidence score 0–10.

    Returns:
        str: HTML div element.
    """
    pct = min(100, max(0, score * 10))
    if score >= 8.5:
        colour = "#22c55e"
    elif score >= 7.0:
        colour = "#14b8a6"
    elif score >= 5.0:
        colour = "#f59e0b"
    else:
        colour = "#ef4444"
    return (
        f'<div class="conf-bar">'
        f'<div class="conf-fill" style="width:{pct}%;background:{colour}"></div>'
        f'</div>'
    )


def risk_badge_class(risk: str) -> str:
    """Map a risk level string to a badge CSS class."""
    return {"critical": "red", "high": "amber", "medium": "teal", "low": "gray"}.get(
        (risk or "").lower(), "gray"
    )


def score_badge_class(score: float) -> str:
    """Map a confidence score to a badge CSS class."""
    if score >= 8.5:
        return "green"
    if score >= 7.0:
        return "teal"
    if score >= 5.0:
        return "amber"
    return "red"


def _on_project_change() -> None:
    """Clear cached results when the active project changes."""
    st.session_state["results"] = None
    st.session_state["run_results"] = None
    st.session_state["last_file"] = None
    st.toast(
        f"Switched to {st.session_state.active_project}",
        icon="🔄",
    )


def count_todos(path: str) -> int:
    """Count TODO occurrences in a file."""
    if not os.path.exists(path):
        return 0
    return open(path).read().count("TODO")


def mask_secret(value: str) -> str:
    """
    Return a masked version of a secret string for display.

    Shows first 4 chars + asterisks.

    Args:
        value: The secret string to mask.

    Returns:
        str: Masked string, e.g. "sk-a****".
    """
    if not value:
        return "(not set)"
    return value[:4] + "•" * min(12, len(value) - 4)


def build_markdown_export(test_cases: list[dict]) -> str:
    """
    Build a Markdown string from a list of test cases.

    Tries to use core.markdown_reporter if available;
    falls back to a built-in formatter.

    Args:
        test_cases: List of test case dicts.

    Returns:
        str: Markdown-formatted test case output.
    """
    try:
        from core.markdown_reporter import generate_pr_comment
        return generate_pr_comment(test_cases, [])
    except Exception:
        lines = [f"# QAX — Test Cases ({len(test_cases)} generated)\n"]
        for tc in test_cases:
            score = tc.get("confidence_score", 0)
            lines.append(f"## {tc.get('test_case_id','?')} — {tc.get('title','Untitled')}")
            lines.append(f"**Module:** {tc.get('module','N/A')} | **Type:** {tc.get('test_type','N/A')} | **Confidence:** {score}/10\n")
            for step in tc.get("manual_steps", []):
                lines.append(f"{step.get('step_number','?')}. {step.get('action','')}")
            lines.append("")
        return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════════
# SIDEBAR
# ══════════════════════════════════════════════════════════════════════════════

with st.sidebar:
    # ── Logo ──────────────────────────────────────────────────────────────
    st.markdown('<div class="qax-logo">QAX</div>', unsafe_allow_html=True)
    st.markdown('<div class="qax-sub">AI QA Engineer</div>', unsafe_allow_html=True)
    st.divider()

    # ── Project selector ───────────────────────────────────────────────────
    projects = get_projects()
    # Apply a project created elsewhere (e.g. the wizard's "New project") so it
    # becomes the active selection. Must run BEFORE the selectbox is created —
    # Streamlit forbids modifying a widget's key after it is instantiated.
    _pending_project = st.session_state.pop("_pending_project", None)
    if _pending_project and _pending_project in projects:
        st.session_state["active_project"] = _pending_project
    default_project = os.getenv("DEFAULT_PROJECT", "example")
    default_idx = projects.index(default_project) if default_project in projects else 0
    st.selectbox(
        "🗂️ Project",
        options=projects,
        index=default_idx,
        key="active_project",
        on_change=_on_project_change,
    )
    st.divider()

    # ── Connection status — pill badges ───────────────────────────────────
    st.markdown("**Connections**")
    conn = get_connection_status()

    def _pill(label: str, ok: bool, warn_text: str = "") -> str:
        cls = "pill-ok" if ok else "pill-err"
        dot = "●" if ok else "●"
        suffix = f' <span style="font-size:0.68rem;color:#6b7280">{warn_text}</span>' if warn_text and not ok else ""
        return f'<div class="conn-pill {cls}">{dot} {label}{suffix}</div>'

    st.markdown(
        _pill("Claude API", conn["claude"])
        + _pill("Jira", conn["jira"])
        + _pill("Xray", conn["xray"]),
        unsafe_allow_html=True,
    )
    if conn["dry_run"]:
        st.markdown(
            '<div class="conn-pill pill-warn">◑ DRY RUN active</div>',
            unsafe_allow_html=True,
        )
    st.divider()

    # ── Quick stats ────────────────────────────────────────────────────────
    st.markdown("**Quick Stats**")
    stats = get_feedback_stats()
    all_cases = load_all_test_cases()
    total_gen  = len(all_cases)
    total_acc  = stats.get("accepted", 0)

    sc1, sc2 = st.columns(2)
    sc1.metric("Generated", total_gen)
    sc2.metric("Accepted",  total_acc)
    st.divider()

    st.caption("v1.0 · Phase C")


# ══════════════════════════════════════════════════════════════════════════════
# TABS
# ══════════════════════════════════════════════════════════════════════════════

# ══════════════════════════════════════════════════════════════════════════════
# ANALYZE WIZARD — Project → Source → Configure → Generate → Export
# ══════════════════════════════════════════════════════════════════════════════

_WIZARD_STEPS = ["Project", "Source", "Understand", "Generate", "Export", "Execute", "Bug reports"]

# Display label → input adapter name + field hints
_INPUT_TYPES = {
    "Folder / repo": {"adapter": "folder", "field": "text",
                      "label": "Folder path",
                      "placeholder": "src/  (a project folder on disk)"},
    "GitHub PR":     {"adapter": "pr", "field": "text",
                      "label": "Pull request",
                      "placeholder": "owner/repo#123 or a PR URL"},
    "Single file":   {"adapter": "file", "field": "file",
                      "label": "File path",
                      "placeholder": "src/Controller.cs"},
    "Jira ticket":   {"adapter": "jira", "field": "text",
                      "label": "Ticket key",
                      "placeholder": "PROJ-123"},
    "User story":    {"adapter": "story", "field": "textarea",
                      "label": "Story / requirement",
                      "placeholder": "As a user I want to...\nAcceptance criteria:\n- ..."},
}


def _wiz_go(step: int) -> None:
    """Move the wizard to a step and rerun."""
    st.session_state["wiz_step"] = step
    st.rerun()


def _wizard_stepper(current: int) -> None:
    """Render the 5-step progress header."""
    parts = ['<div style="display:flex;align-items:flex-start;justify-content:space-between;'
             'margin:2px 0 22px;gap:4px">']
    for i, name in enumerate(_WIZARD_STEPS, 1):
        if i < current:
            circle, mark = "background:#7c3aed;color:#fff", "✓"
        elif i == current:
            circle, mark = "background:#7c3aed;color:#fff;box-shadow:0 0 0 3px rgba(124,58,237,.25)", str(i)
        else:
            circle, mark = "background:#1f2937;color:#6b7280;border:1px solid #374151", str(i)
        lbl = "#f1f5f9" if i == current else ("#9ca3af" if i < current else "#6b7280")
        parts.append(
            f'<div style="display:flex;flex-direction:column;align-items:center;flex:1;gap:6px">'
            f'<div style="width:30px;height:30px;border-radius:50%;display:flex;align-items:center;'
            f'justify-content:center;font-size:13px;font-weight:700;{circle}">{mark}</div>'
            f'<span style="font-size:12px;color:{lbl}">{name}</span></div>'
        )
    parts.append("</div>")
    st.markdown("".join(parts), unsafe_allow_html=True)


def _wiz_new_steps() -> dict:
    """Fresh step-checklist dict for a generation run."""
    return {
        "risk":   {"label": "Risk & context", "done": False},
        "cache":  {"label": "Cache lookup",   "done": False},
        "claude": {"label": "Calling Claude", "done": False},
        "valid":  {"label": "Validating",     "done": False},
    }


def _wiz_render_steps(steps: dict) -> str:
    """Render the generation step checklist as HTML."""
    html = ['<div style="padding:4px 0 10px">']
    for s in steps.values():
        mark = "✓" if s["done"] else "○"
        col = "#4ade80" if s["done"] else "#6b7280"
        html.append(f'<div style="color:{col};font-size:13px;padding:3px 0">{mark}&nbsp; {s["label"]}</div>')
    html.append("</div>")
    return "".join(html)


def _wiz_nav(step: int, *, back_to=None, next_to=None, next_label="Next →",
            next_disabled=False, next_warn=None) -> None:
    """Render Back / Next navigation for a wizard step."""
    st.divider()
    b1, _, b2 = st.columns([1, 2, 1])
    if back_to is not None:
        with b1:
            if st.button("← Back", use_container_width=True, key=f"wiz_back_{step}"):
                _wiz_go(back_to)
    if next_to is not None:
        with b2:
            if st.button(next_label, type="primary", use_container_width=True,
                         key=f"wiz_next_{step}", disabled=next_disabled):
                if next_warn:
                    st.warning(next_warn)
                else:
                    _wiz_go(next_to)


def _wiz_step_project() -> None:
    """Step 1 — confirm the active project or create a new one."""
    st.subheader("Step 1 · Project")
    project = st.session_state.get("active_project", "")
    st.markdown(f"Working on project: **{project or '(default)'}**")
    st.caption("Switch projects from the sidebar on the left, or create a new one below.")

    with st.popover("➕ New project"):
        new_name = st.text_input("New project name", key="wiz_new_project",
                                 placeholder="acme-checkout")
        if st.button("Create", key="wiz_create_project"):
            name = (new_name or "").strip().lower()
            if not name:
                st.warning("Enter a name.")
            else:
                rc, out = run_qax_command(["init-project", "--name", name])
                if rc == 0:
                    get_projects.clear()
                    # Switch to the new project on the next run (applied in the sidebar)
                    st.session_state["_pending_project"] = name
                    st.toast(f"Created '{name}' — switching to it", icon="✅")
                    st.rerun()
                else:
                    st.error(out[-300:])

    st.divider()
    _, _, col = st.columns([1, 2, 1])
    with col:
        if st.button("Next: Source →", type="primary", use_container_width=True, key="wiz_next_1"):
            _wiz_go(2)


def _wiz_step_source(project: str) -> None:
    """Step 2 — choose the input type and provide the source."""
    st.subheader("Step 2 · Source")
    st.caption("Where should QAX read the code or requirement from?")

    labels = list(_INPUT_TYPES.keys())
    # Streamlit clears widget state when a step isn't rendered, so restore prior
    # choices from stable (non-widget) keys before rendering the widgets.
    st.session_state.setdefault("wiz_input_type", st.session_state.get("src_type", labels[0]))
    st.session_state.setdefault("wiz_source_val", st.session_state.get("src_val", ""))

    choice = st.radio("Input type", labels, horizontal=True, key="wiz_input_type")
    meta = _INPUT_TYPES[choice]

    if meta["field"] == "textarea":
        st.text_area(meta["label"], placeholder=meta["placeholder"], height=120,
                     key="wiz_source_val")
    else:
        st.text_input(meta["label"], placeholder=meta["placeholder"], key="wiz_source_val")
        if meta["field"] == "file":
            up = st.file_uploader("…or upload a file",
                                  type=["cs", "py", "js", "ts", "java", "kt", "rb", "go"],
                                  key="wiz_upload")
            if up:
                suffix = Path(up.name).suffix
                with tempfile.NamedTemporaryFile(delete=False, suffix=suffix,
                                                 dir=str(QAX_ROOT / "output")) as tmp:
                    tmp.write(up.getbuffer())
                    st.session_state["wiz_uploaded_tmp"] = tmp.name
                st.success(f"Uploaded: {up.name}")

    # Persist to stable keys that survive navigation between steps
    st.session_state["src_type"] = choice
    st.session_state["src_val"] = st.session_state.get("wiz_source_val", "") or ""

    has_source = bool(st.session_state["src_val"] or
                      (meta["field"] == "file" and st.session_state.get("wiz_uploaded_tmp")))
    _wiz_nav(2, back_to=1, next_to=3, next_label="Next: Understand →",
             next_warn=None if has_source else "Provide a source first.")


def _wiz_options_expander() -> None:
    """Generation options — shown inside the Generate step."""
    with st.expander("⚙️ Options"):
        st.session_state.setdefault("wiz_test_type", st.session_state.get("cfg_test_type", "auto"))
        st.session_state.setdefault("wiz_ext", st.session_state.get("cfg_ext", ".py"))
        st.session_state.setdefault("wiz_git", st.session_state.get("cfg_git", False))
        st.session_state.setdefault("wiz_bugs", st.session_state.get("cfg_bugs", False))
        st.session_state.setdefault("wiz_auto", st.session_state.get("cfg_auto", False))
        st.session_state.setdefault("wiz_story", st.session_state.get("cfg_story", ""))
        st.session_state.setdefault("wiz_dry", st.session_state.get("cfg_dry", True))

        c1, c2 = st.columns(2)
        with c1:
            st.selectbox("Test type",
                         ["auto", "regression", "feature", "smoke", "uat", "api", "e2e"],
                         key="wiz_test_type")
            st.text_input("Folder file extension", key="wiz_ext",
                          help="Only used when the source is a folder.")
        with c2:
            st.checkbox("Git history", key="wiz_git")
            st.checkbox("Bug patterns", key="wiz_bugs")
            st.checkbox("Auto-accept ≥ 8.5", key="wiz_auto")
        st.text_area("Extra context / user story (optional)", key="wiz_story", height=70)
        st.toggle("DRY RUN — don't push to Jira/Xray", key="wiz_dry")

        for _w, _c in [("wiz_test_type", "cfg_test_type"), ("wiz_ext", "cfg_ext"),
                       ("wiz_git", "cfg_git"), ("wiz_bugs", "cfg_bugs"),
                       ("wiz_auto", "cfg_auto"), ("wiz_story", "cfg_story"),
                       ("wiz_dry", "cfg_dry")]:
            st.session_state[_c] = st.session_state.get(_w)


def _wiz_coming_soon(step: int, title: str, desc: str, *,
                     back_to: int, next_to: int | None = None,
                     next_label: str = "Skip →") -> None:
    """Render a placeholder for a step that isn't built yet."""
    st.subheader(title)
    st.info(f"🚧 {desc}\n\nComing soon — not built yet.")
    st.divider()
    b1, _, b2 = st.columns([1, 2, 1])
    with b1:
        if st.button("← Back", use_container_width=True, key=f"wiz_back_{step}"):
            _wiz_go(back_to)
    if next_to is not None:
        with b2:
            if st.button(next_label, use_container_width=True, key=f"wiz_next_{step}"):
                _wiz_go(next_to)


def _run_wizard_comprehension(project: str, adapter: str, source: str, ext: str | None) -> None:
    """Run RepoComprehensionService and store the app map in session."""
    from services.comprehension_service import RepoComprehensionService
    from services.context import QAXContext

    ctx = QAXContext.from_env(project=project)
    with st.spinner("Understanding the source…"):
        try:
            result = RepoComprehensionService(ctx).comprehend(
                source, input_type=adapter, ext=ext)
        except Exception as exc:
            st.error(f"Comprehension error: {exc}")
            return

    if result.error:
        st.warning(result.error)
        st.session_state["comprehension"] = None
        st.session_state["repo_context"] = None
    else:
        st.session_state["comprehension"] = result
        st.session_state["repo_context"] = result.as_context()
        st.toast("App map ready", icon="🧠")


def _wiz_render_comprehension(comp) -> None:
    """Render the comprehension map."""
    if comp.overview:
        st.markdown(f"**Overview:** {comp.overview}")
    c1, c2 = st.columns(2)
    with c1:
        if comp.main_flows:
            st.markdown("**Main flows**")
            for f in comp.main_flows:
                st.markdown(f"- {f}")
    with c2:
        if comp.risk_areas:
            st.markdown("**Risk areas**")
            for r in comp.risk_areas:
                st.markdown(f"- {r}")
    if comp.entry_points:
        with st.expander("Entry points"):
            for e in comp.entry_points:
                st.markdown(f"- {e}")
    if comp.files:
        with st.expander(f"Files ({comp.file_count})"):
            for f in comp.files:
                st.markdown(f"- `{f.get('path', '?')}` — {f.get('role', '')}")


def _wiz_step_understand(project: str) -> None:
    """Step 3 — deep comprehension of the source before generation."""
    st.subheader("Step 3 · Understand")
    st.caption("QAX reads the whole source and maps the app before generating — "
               "so the tests go deeper. Optional, but recommended.")

    choice = st.session_state.get("src_type", list(_INPUT_TYPES)[0])
    meta = _INPUT_TYPES.get(choice, {})
    adapter = meta.get("adapter", "file")
    source = (st.session_state.get("wiz_uploaded_tmp")
              if meta.get("field") == "file" and st.session_state.get("wiz_uploaded_tmp")
              else st.session_state.get("src_val", ""))
    ext = st.session_state.get("cfg_ext") or None

    if st.button("🧠 Understand the source", type="primary", key="wiz_understand_run"):
        if not source:
            st.warning("No source — go back to step 2.")
        else:
            _run_wizard_comprehension(project, adapter, source, ext)

    comp = st.session_state.get("comprehension")
    if comp:
        _wiz_render_comprehension(comp)

    st.divider()
    b1, _, b2 = st.columns([1, 2, 1])
    with b1:
        if st.button("← Back", use_container_width=True, key="wiz_back_3"):
            _wiz_go(2)
    with b2:
        _lbl = "Next: Generate →" if comp else "Skip to Generate →"
        if st.button(_lbl, use_container_width=True, key="wiz_next_3"):
            _wiz_go(4)


def _run_wizard_execution(results: list[dict], base_url: str, mode: str) -> None:
    """Execute the generated tests against a live URL and store the result."""
    from services.context import QAXContext
    from services.execution_service import ExecutionService

    ctx = QAXContext.from_env(dry_run=st.session_state.get("cfg_dry", True))
    with st.spinner(f"Running {len(results)} test(s) against {base_url}…"):
        try:
            result = ExecutionService(ctx).execute(
                results, mode=mode, base_url=base_url, stop_on_fail=False)
        except Exception as exc:
            st.error(f"Execution error: {exc}")
            return

    st.session_state["execution_result"] = result
    if result.error:
        st.warning(result.error)
    else:
        st.toast(f"Ran {result.total} — {result.passed} passed, {result.failed} failed",
                 icon="▶️")


def _wiz_render_execution(result) -> None:
    """Render execution metrics and per-test results."""
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Total", result.total)
    m2.metric("Passed", result.passed)
    m3.metric("Failed", result.failed)
    m4.metric("Pass rate", f"{result.pass_rate:.0f}%")
    st.divider()
    for r in result.results:
        status = r.get("status", "?")
        icon = {"PASS": "✅", "FAIL": "❌", "ERROR": "⚠️", "SKIP": "➖"}.get(status, "•")
        tc_id = r.get("test_case_id", r.get("id", "?"))
        title = str(r.get("title", ""))[:55]
        st.markdown(f"{icon} **[{status}]** {tc_id} — {title}")
        if status in ("FAIL", "ERROR"):
            for step in r.get("steps", []):
                if step.get("status") in ("FAIL", "ERROR"):
                    st.caption(f"↳ step {step.get('step_number', '?')}: "
                               f"{str(step.get('error', ''))[:110]}")
            if r.get("error"):
                st.caption(f"↳ {str(r['error'])[:130]}")


def _wiz_step_execute() -> None:
    """Step 6 — run the generated tests against a live app."""
    st.subheader("Step 6 · Execute (E2E)")
    results = st.session_state.get("analyze_results") or []
    if not results:
        st.info("Generate test cases first (step 4).")
        st.divider()
        if st.button("← Back", use_container_width=True, key="wiz_back_6"):
            _wiz_go(5)
        return

    st.caption(f"{len(results)} test case(s) ready to run against a live app.")
    st.session_state.setdefault("wiz_base_url", "http://localhost:8000")
    st.session_state.setdefault("wiz_exec_mode", "api")
    st.text_input("Live app URL", key="wiz_base_url",
                  help="The running app to test against (e.g. a staging URL or localhost).")
    st.radio("Mode", ["api", "ui"], key="wiz_exec_mode", horizontal=True,
             help="api = hit HTTP endpoints directly (no browser) · "
                  "ui = Playwright browser (needs Python 3.11/3.12).")

    if st.button("▶️ Run tests", type="primary", key="wiz_exec_run"):
        _run_wizard_execution(results, st.session_state.get("wiz_base_url", ""),
                              st.session_state.get("wiz_exec_mode", "api"))

    ex = st.session_state.get("execution_result")
    if ex:
        _wiz_render_execution(ex)

    st.divider()
    b1, _, b2 = st.columns([1, 2, 1])
    with b1:
        if st.button("← Back", use_container_width=True, key="wiz_back_6"):
            _wiz_go(5)
    with b2:
        if st.button("Next: Bug reports →", use_container_width=True,
                     key="wiz_next_6", disabled=not ex):
            _wiz_go(7)


def _run_wizard_bugreport(execution) -> None:
    """Build a standalone HTML report from the execution result."""
    from services.context import QAXContext
    from services.reporting_service import ReportingService

    ctx = QAXContext.from_env()
    with st.spinner("Building the report…"):
        rep = ReportingService(ctx).report(execution)
    if rep.error:
        st.warning(rep.error)
    elif rep.html_path:
        st.session_state["report_path"] = rep.html_path
        st.toast("Report ready", icon="📄")


def _run_wizard_critic() -> None:
    """Run the Critic over the execution result — separate real bugs from broken tests."""
    from services.context import QAXContext
    from services.critic_service import CriticService

    ex = st.session_state.get("execution_result")
    results = st.session_state.get("analyze_results") or []
    if not ex:
        return
    ctx = QAXContext.from_env()
    try:
        with st.spinner("Investigating failures (verify, don't trust)…"):
            crit = CriticService(ctx).review(ex, results)
    except Exception as exc:
        st.error(f"Critic error: {exc}")
        return
    st.session_state["critic_result"] = crit
    if crit.error:
        st.warning(crit.error)
    else:
        st.toast(crit.summary(), icon="🕵️")


def _wiz_step_bugreports() -> None:
    """Step 7 — investigate failures, then report only the real bugs."""
    st.subheader("Step 7 · Bug reports")
    ex = st.session_state.get("execution_result")
    if not ex:
        st.info("Run the tests first (step 6).")
    else:
        failed = [r for r in ex.results if r.get("status") in ("FAIL", "ERROR")]
        st.caption(f"{ex.failed} failing / {ex.total} test(s) — "
                   f"{len(failed)} to investigate.")

        c1, c2 = st.columns(2)
        with c1:
            if st.button("🕵️ Investigate failures", type="primary",
                         key="wiz_critic_run", disabled=not failed,
                         help="The Critic checks each failure: real app bug, or the "
                              "test's own fault? Only real bugs should be reported."):
                _run_wizard_critic()
        with c2:
            if st.button("📄 Build bug report", key="wiz_report_run",
                         disabled=not failed):
                _run_wizard_bugreport(ex)

        crit = st.session_state.get("critic_result")
        if crit and not crit.error:
            m1, m2, m3 = st.columns(3)
            m1.metric("🐞 Real bugs", len(crit.real_bugs))
            m2.metric("🔧 Broken tests", len(crit.broken_tests))
            m3.metric("❓ Uncertain", len(crit.uncertain))
            st.divider()

            if crit.real_bugs:
                st.markdown("#### 🐞 Real bugs — worth reporting")
                for v in crit.real_bugs:
                    st.markdown(f"**{str(v.title)[:70]}**")
                    st.caption(f"↳ why: {str(v.reason)[:180]}")
                    if v.suggestion:
                        st.caption(f"↳ reproduce: {str(v.suggestion)[:180]}")
            if crit.broken_tests:
                with st.expander(f"🔧 {len(crit.broken_tests)} broken test(s) — "
                                 f"not app bugs (the test's own fault)"):
                    for v in crit.broken_tests:
                        st.markdown(f"**{str(v.title)[:70]}**")
                        st.caption(f"↳ {str(v.reason)[:180]}")
            if crit.uncertain:
                with st.expander(f"❓ {len(crit.uncertain)} uncertain"):
                    for v in crit.uncertain:
                        st.caption(f"{str(v.title)[:70]} — {str(v.reason)[:120]}")
        else:
            # Not yet investigated — show raw failures.
            for r in failed:
                st.markdown(f"**⚠️ {r.get('test_case_id', '?')} — "
                            f"{str(r.get('title', ''))[:60]}**")
                for step in r.get("steps", []):
                    if step.get("status") in ("FAIL", "ERROR"):
                        st.caption(f"↳ step {step.get('step_number', '?')}: "
                                   f"{str(step.get('error', ''))[:130]}")
                if r.get("error"):
                    st.caption(f"↳ {str(r['error'])[:150]}")

        path = st.session_state.get("report_path")
        if path and Path(path).exists():
            try:
                st.download_button("⬇️ Download HTML report",
                                   data=Path(path).read_text(encoding="utf-8"),
                                   file_name="qax_report.html", mime="text/html",
                                   use_container_width=True)
            except Exception:
                st.caption(f"Report saved: {path}")

    st.divider()
    b1, _, b2 = st.columns([1, 2, 1])
    with b1:
        if st.button("← Back", use_container_width=True, key="wiz_back_7"):
            _wiz_go(6)
    with b2:
        if st.button("↺ Start over", use_container_width=True, key="wiz_restart2"):
            for _k in ("analyze_results", "wiz_uploaded_tmp", "comprehension",
                       "repo_context", "execution_result", "report_path",
                       "critic_result"):
                st.session_state[_k] = None
            _wiz_go(1)


def _run_wizard_generation(project: str, adapter: str, source: str, ext: str | None) -> None:
    """Call GenerationService.generate_from_source and store results in session."""
    from services.context import QAXContext
    from services.generation_service import GenerationOptions, GenerationService

    _tt = st.session_state.get("cfg_test_type", "auto")
    opts = GenerationOptions(
        user_story=st.session_state.get("cfg_story") or None,
        test_type=_tt if _tt and _tt != "auto" else None,
        include_git=st.session_state.get("cfg_git", False),
        include_bugs=st.session_state.get("cfg_bugs", False),
        auto_accept=st.session_state.get("cfg_auto", False),
        repo_context=st.session_state.get("repo_context") or None,
    )
    ctx = QAXContext.from_env(project=project, dry_run=st.session_state.get("cfg_dry", True))

    steps = _wiz_new_steps()
    ph = st.empty()
    ph.markdown(_wiz_render_steps(steps), unsafe_allow_html=True)
    reporter = _UIProgressReporter(steps, ph, _wiz_render_steps)

    try:
        result = GenerationService(ctx).generate_from_source(
            source, input_type=adapter, options=opts, ext=ext, progress=reporter)
    except Exception as exc:
        st.error(f"Generation error: {exc}")
        return

    for s in steps.values():
        s["done"] = True
    ph.markdown(_wiz_render_steps(steps), unsafe_allow_html=True)

    results = result.test_cases
    st.session_state["analyze_results"] = results
    if result.error and not results:
        st.toast("No test cases — see message", icon="⚠️")
        st.warning(result.error)
    elif results:
        st.session_state["onboarded"] = True
        st.toast(f"✅ Generated {len(results)} test case(s)", icon="🤖")
        try:
            _ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            _out = QAX_ROOT / "output" / f"test_cases_{_ts}.json"
            _out.parent.mkdir(parents=True, exist_ok=True)
            _out.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
            load_all_test_cases.clear()
        except Exception:
            pass
    else:
        st.toast("No valid test cases produced", icon="⚠️")


def _wiz_render_results(results: list[dict], dry_run: bool) -> None:
    """Render metric cards + per-test-case cards with accept/reject/push."""
    avg_conf = (sum(t.get("confidence_score", 0) for t in results) / len(results)) if results else 0
    m1, m2, m3 = st.columns(3)
    m1.metric("Valid", len(results))
    m2.metric("Avg confidence", f"{avg_conf:.1f}/10")
    m3.metric("Modules", len({t.get("module", "") for t in results}))
    st.divider()

    for i, tc in enumerate(results):
        title = tc.get("title", "Untitled")
        tc_id = tc.get("test_case_id", f"TC-{i + 1:04d}")
        module = tc.get("module", "N/A")
        tt = tc.get("test_type", "functional")
        score = float(tc.get("confidence_score", 0))
        needs_rv = tc.get("needs_review", False)
        _cls = "tc-high" if score >= 8 else ("tc-medium" if score >= 6 else "tc-low")
        st.markdown(
            f'<div class="qax-card {_cls}">'
            f'<div class="qax-card-title">{tc_id} — {title}</div>'
            f'<div style="margin-bottom:6px">{badge(module, "purple")}{badge(tt, "teal")}'
            f'{badge(f"{score}/10", score_badge_class(score))}'
            + (f'{badge("⚠ Review", "amber")}' if needs_rv else "")
            + f'</div>{confidence_bar(score)}</div>',
            unsafe_allow_html=True,
        )
        with st.expander(f"View steps — {tc_id}"):
            for step in tc.get("manual_steps", []):
                st.markdown(f"**{step.get('step_number', '?')}.** {step.get('action', '')}")
                if step.get("expected_result"):
                    st.caption(f"Expected: {step['expected_result']}")
        a1, a2, a3 = st.columns(3)
        with a1:
            if st.button("✓ Accept", key=f"wacc_{i}", use_container_width=True):
                try:
                    from core.feedback_store import FeedbackStore
                    FeedbackStore().record_decision(tc, action="accepted")
                    st.toast(f"Accepted {tc_id}", icon="✅")
                    get_feedback_stats.clear()
                except Exception as e:
                    st.toast(f"Could not record: {e}", icon="❌")
        with a2:
            if st.button("✗ Reject", key=f"wrej_{i}", use_container_width=True):
                try:
                    from core.feedback_store import FeedbackStore
                    FeedbackStore().record_decision(tc, action="rejected")
                    st.toast(f"Rejected {tc_id}", icon="❌")
                    get_feedback_stats.clear()
                except Exception as e:
                    st.toast(f"Could not record: {e}", icon="❌")
        with a3:
            if st.button("↑ Push to Xray", key=f"wpush_{i}", use_container_width=True,
                         disabled=dry_run,
                         help="Turn off DRY RUN in step 3 to push"):
                try:
                    from core.xray_client import XrayClient
                    res = XrayClient().push_batch([tc])
                    pushed = res.get("summary", {}).get("pushed_count", 0)
                    st.toast(f"Pushed {pushed} to Xray", icon="⬆️")
                except Exception as e:
                    st.toast(f"Push failed: {e}", icon="❌")
        st.markdown('<div style="height:4px"></div>', unsafe_allow_html=True)


def _wiz_step_generate(project: str) -> None:
    """Step 4 — generate and review."""
    st.subheader("Step 4 · Generate & review")
    choice = st.session_state.get("src_type", list(_INPUT_TYPES)[0])
    meta = _INPUT_TYPES.get(choice, {})
    adapter = meta.get("adapter", "file")
    source = (st.session_state.get("wiz_uploaded_tmp")
              if meta.get("field") == "file" and st.session_state.get("wiz_uploaded_tmp")
              else st.session_state.get("src_val", ""))
    ext = st.session_state.get("cfg_ext") or None

    _map = "  ·  🧠 using app map" if st.session_state.get("repo_context") else ""
    st.caption(f"Source: {choice} · project: {project or '(default)'}{_map}")
    _wiz_options_expander()
    if st.button("🤖 Generate test cases", type="primary", key="wiz_run"):
        if not source:
            st.warning("No source — go back to step 2.")
        else:
            _run_wizard_generation(project, adapter, source, ext)

    results = st.session_state.get("analyze_results") or []
    if results:
        _wiz_render_results(results, st.session_state.get("cfg_dry", True))

    _wiz_nav(4, back_to=3, next_to=5, next_label="Next: Export →",
             next_disabled=not results)


def _run_wizard_export(results: list[dict], target: str, project: str, dry: bool) -> None:
    """Export via ExportService and toast the outcome."""
    from services.context import QAXContext
    from services.export_service import ExportService

    ctx = QAXContext.from_env(project=project, dry_run=dry)
    res = ExportService(ctx).export(results, target=target)
    if res.success:
        st.toast(f"Exported {res.exported_count} → {target}", icon="✅")
        if res.references:
            st.success("Saved: " + ", ".join(str(r) for r in res.references[:3]))
    else:
        st.error(res.error or "Export failed")


def _wiz_step_export() -> None:
    """Step 5 — export the results."""
    st.subheader("Step 5 · Export")
    results = st.session_state.get("analyze_results") or []
    if not results:
        st.info("Nothing to export yet — generate in step 4 first.")
    else:
        dry = st.session_state.get("cfg_dry", True)
        project = st.session_state.get("active_project", "")
        st.caption(f"{len(results)} test case(s) ready. "
                   + ("DRY RUN is on — Jira/Xray will simulate." if dry else "DRY RUN is off."))
        c1, c2, c3, c4 = st.columns(4)
        with c1:
            if st.button("Jira / Xray", use_container_width=True, key="wiz_exp_xray"):
                _run_wizard_export(results, "xray", project, dry)
        with c2:
            if st.button("Excel", use_container_width=True, key="wiz_exp_excel"):
                _run_wizard_export(results, "excel", project, dry)
        with c3:
            if st.button("CSV", use_container_width=True, key="wiz_exp_csv"):
                _run_wizard_export(results, "csv", project, dry)
        with c4:
            st.download_button("Markdown", data=build_markdown_export(results),
                               file_name="test_cases.md", mime="text/markdown",
                               use_container_width=True)

    _wiz_nav(5, back_to=4, next_to=6, next_label="Next: Execute →")


def render_analyze_wizard(project: str) -> None:
    """Render the whole Analyze wizard for the given active project."""
    step = st.session_state.setdefault("wiz_step", 1)
    _wizard_stepper(step)
    if step == 1:
        _wiz_step_project()
    elif step == 2:
        _wiz_step_source(project)
    elif step == 3:
        _wiz_step_understand(project)
    elif step == 4:
        _wiz_step_generate(project)
    elif step == 5:
        _wiz_step_export()
    elif step == 6:
        _wiz_step_execute()
    else:
        _wiz_step_bugreports()


_ap = st.session_state.get("active_project", os.getenv("DEFAULT_PROJECT", "example")).upper()
tab1, tab2, tab3, tab4 = st.tabs([
    f"🔍 Analyze · {_ap}",
    f"▶ Run · {_ap}",
    "📊 Dashboard",
    "⚙️ Settings",
])


# ══════════════════════════════════════════════════════════════════════════════
# TAB 1 — ANALYZE
# ══════════════════════════════════════════════════════════════════════════════

with tab1:
    project = st.session_state.get("active_project", os.getenv("DEFAULT_PROJECT", "example"))

    # Reset wizard results when the active project changes
    if st.session_state.get("last_project") != project:
        st.session_state["analyze_results"] = None
        st.session_state["last_project"] = project

    render_analyze_wizard(project)


# ══════════════════════════════════════════════════════════════════════════════
# TAB 2 — RUN
# ══════════════════════════════════════════════════════════════════════════════

with tab2:
    project = st.session_state.get("active_project", os.getenv("DEFAULT_PROJECT", "example"))
    st.subheader("Select run mode")

    # ── Mode cards (visual) with credential awareness ──────────────────────
    _run_conn  = get_connection_status()
    _jira_ok   = _run_conn["jira"]
    _claude_ok = _run_conn["claude"]

    _modes = [
        # (icon, label, desc, key, needs_jira)
        ("🐛", "Bugs",       "Generate tests from resolved Jira bugs",    "bugs",       True),
        ("💨", "Smoke",      "Critical path — quick confidence check",    "smoke",      False),
        ("🔄", "Regression", "Full suite against the last release",       "regression", False),
        ("🚀", "Release",    "End-to-end test plan for a version tag",    "release",    True),
    ]
    mode_cols = st.columns(4)
    for col, (icon, label, desc, _, needs_jira) in zip(mode_cols, _modes):
        _disabled = needs_jira and not _jira_ok
        _card_cls = "mode-card mode-card-disabled" if _disabled else "mode-card"
        _warn_html = (
            '<div class="mode-cred-warn">⚠ Jira not configured</div>'
            if _disabled else ""
        )
        with col:
            st.markdown(
                f'<div class="{_card_cls}">'
                f'<div class="mode-card-icon">{icon}</div>'
                f'<div class="mode-card-title">{label}</div>'
                f'<div class="mode-card-desc">{desc}</div>'
                f'{_warn_html}'
                f'</div>',
                unsafe_allow_html=True,
            )

    mode_label = st.radio(
        "Mode",
        ["🐛 Bugs", "💨 Smoke", "🔄 Regression", "🚀 Release"],
        horizontal=True,
        label_visibility="collapsed",
        key="run_mode",
    )
    selected_mode = mode_label.split()[-1].lower()  # "bugs" / "smoke" / "regression" / "release"

    # ── Dynamic extra inputs ───────────────────────────────────────────────
    extra_args: list[str] = []
    if selected_mode == "bugs":
        bug_ids = st.text_input(
            "Bug IDs (comma-separated)",
            placeholder="PROJ-101,PROJ-205,PROJ-306",
            key="run_bug_ids",
        )
        if bug_ids:
            extra_args += ["--bugs", bug_ids]
    elif selected_mode == "release":
        version = st.text_input(
            "Release version",
            placeholder="12.28.5",
            key="run_version",
        )
        if version:
            extra_args += ["--version", version]

    # ── Options row ────────────────────────────────────────────────────────
    ro1, ro2, ro3 = st.columns(3)
    with ro1:
        dry_run_run = st.toggle("DRY RUN",  value=True, key="run_dryrun")
    with ro2:
        upload_run  = st.toggle("Upload to Xray", value=False, key="run_upload")
    with ro3:
        headless    = st.toggle("Headless",  value=True, key="run_headless")

    run_btn = st.button(
        "▶ Run Pipeline",
        type="primary",
        use_container_width=True,
        key="run_pipeline",
    )

    if run_btn:
        run_args = [
            "run",
            "--mode", selected_mode,
            "--project", project,
        ] + extra_args

        if dry_run_run:
            run_args.append("--dry-run")
        if upload_run:
            run_args.append("--upload")
        if headless:
            run_args.append("--headless")

        run_terminal = st.empty()

        with st.status(f"Running {selected_mode} pipeline…", expanded=True) as status_box:
            st.write("🔗 Connecting to Jira…")
            time.sleep(0.3)
            st.write("🤖 Generating test cases…")
            rc, output = run_qax_command(run_args, stream_to=run_terminal)
            if rc == 0:
                status_box.update(label="✅ Pipeline complete", state="complete")
                st.write("✓ Done")
            else:
                status_box.update(label="❌ Pipeline failed", state="error")
                st.write("✗ Error — see output above")

        st.session_state["run_output"] = output
        st.session_state["run_rc"]     = rc

        # Load & display results
        load_all_test_cases.clear()
        run_results = load_latest_test_cases()
        if run_results:
            st.session_state["run_results"] = run_results

    # ── Show run results ───────────────────────────────────────────────────
    run_results: list[dict] = st.session_state.get("run_results", [])
    if run_results:
        st.divider()
        total   = len(run_results)
        passed  = sum(1 for t in run_results if t.get("confidence_score", 0) >= 7)
        rate    = passed / total * 100 if total else 0

        st.markdown(f"**Results — {total} test cases generated**")
        st.progress(int(rate) / 100, text=f"Quality rate: {rate:.0f}%")

        # Results dataframe
        try:
            import pandas as pd
            df_data = [
                {
                    "ID":         t.get("test_case_id", ""),
                    "Title":      t.get("title", "")[:50],
                    "Module":     t.get("module", ""),
                    "Type":       t.get("test_type", ""),
                    "Confidence": t.get("confidence_score", 0),
                    "Review":     "⚠" if t.get("needs_review") else "✓",
                }
                for t in run_results
            ]
            df = pd.DataFrame(df_data)
            st.dataframe(
                df,
                use_container_width=True,
                hide_index=True,
                column_config={
                    "Confidence": st.column_config.ProgressColumn(
                        "Confidence", min_value=0, max_value=10
                    )
                },
            )
        except ImportError:
            for tc in run_results[:20]:
                st.markdown(
                    f"- **{tc.get('test_case_id','')}** {tc.get('title','')[:60]} "
                    f"({tc.get('confidence_score',0)}/10)"
                )


# ══════════════════════════════════════════════════════════════════════════════
# TAB 3 — DASHBOARD
# ══════════════════════════════════════════════════════════════════════════════

with tab3:
    project = st.session_state.get("active_project", os.getenv("DEFAULT_PROJECT", "example"))
    st.subheader(f"📊 Dashboard — {project.upper()}")

    try:
        import plotly.express as px
        import plotly.graph_objects as go
        PLOTLY_OK = True
    except ImportError:
        PLOTLY_OK = False

    all_cases = load_all_test_cases()
    fb_stats  = get_feedback_stats()
    cache_st  = get_cache_stats()
    sessions  = get_recent_sessions(limit=10)

    total_gen   = len(all_cases)
    total_acc   = fb_stats.get("accepted", 0)
    acc_rate    = fb_stats.get("acceptance_rate", 0.0)
    avg_conf    = (
        sum(t.get("confidence_score", 0) for t in all_cases) / total_gen
        if total_gen else 0.0
    )
    hours_saved = round(total_gen * 0.25, 1)  # ~15 min per manual test case

    # ── Empty state — shown when no data exists ───────────────────────────
    if not total_gen and not sessions:
        st.markdown(
            '<div class="empty-state" style="padding:64px 24px;margin:16px 0">'
            '<div style="font-size:3rem;margin-bottom:14px">📊</div>'
            '<div style="font-size:1.1rem;font-weight:700;color:#9ca3af;margin-bottom:8px">'
            'No data yet'
            '</div>'
            '<div style="font-size:0.85rem;color:#6b7280;margin-bottom:20px">'
            'Generate your first test cases — then charts, trends, and metrics appear here.'
            '</div>'
            '</div>',
            unsafe_allow_html=True,
        )
        if st.button("→ Go to Analyze", type="primary", key="dash_goto_analyze"):
            st.info("Click the 🔍 Analyze tab above to get started.")
        st.stop()

    # ── Top metrics ────────────────────────────────────────────────────────
    dm1, dm2, dm3, dm4, dm5 = st.columns(5)
    dm1.metric("Generated",      total_gen)
    dm2.metric("Accepted",        total_acc)
    dm3.metric("Acceptance Rate", f"{acc_rate:.0f}%")
    dm4.metric("Hours Saved",     f"{hours_saved}h")
    dm5.metric("Avg Confidence",  f"{avg_conf:.1f}/10")
    st.caption(f"Showing data for: {project}")

    st.divider()

    # ── Charts ─────────────────────────────────────────────────────────────
    chart_l, chart_r = st.columns(2)

    # ── Left: 7-day trend ──────────────────────────────────────────────────
    with chart_l:
        st.markdown("**7-Day Generation Trend**")
        if PLOTLY_OK:
            # Build date → count from output file timestamps
            output_dir = QAX_ROOT / "output"
            date_counts: dict[str, int] = defaultdict(int)
            today = datetime.now(timezone.utc).date()
            for d in range(7):
                day = (today - timedelta(days=6 - d)).isoformat()
                date_counts[day] = 0  # ensure all 7 days present

            for f in output_dir.glob("test_cases_*.json"):
                # filename: test_cases_YYYYMMDD_HHMMSS.json
                stem = f.stem  # test_cases_20260101_123456
                parts = stem.split("_")
                if len(parts) >= 3:
                    try:
                        date_str = f"{parts[2][:4]}-{parts[2][4:6]}-{parts[2][6:8]}"
                        if date_str in date_counts:
                            try:
                                tc_data = json.loads(f.read_text(encoding="utf-8"))
                                date_counts[date_str] += len(tc_data) if isinstance(tc_data, list) else 0
                            except Exception:
                                pass
                    except Exception:
                        pass

            trend_data = [
                {"date": d, "generated": c}
                for d, c in sorted(date_counts.items())
            ]
            if trend_data:
                try:
                    import pandas as pd
                    tdf = pd.DataFrame(trend_data)
                    fig = px.line(
                        tdf, x="date", y="generated",
                        markers=True,
                        color_discrete_sequence=["#7c3aed"],
                        labels={"generated": "Test Cases", "date": ""},
                    )
                    fig.update_layout(
                        paper_bgcolor="rgba(0,0,0,0)",
                        plot_bgcolor="rgba(0,0,0,0)",
                        font_color="#f1f5f9",
                        margin=dict(l=0, r=0, t=10, b=0),
                        showlegend=False,
                        xaxis=dict(showgrid=False, color="#6b7280"),
                        yaxis=dict(gridcolor="#1f2937", color="#6b7280"),
                        height=240,
                    )
                    fig.update_traces(line_width=2.5, marker_size=7)
                    st.plotly_chart(fig, use_container_width=True)
                except ImportError:
                    st.info("Install pandas for trend chart: pip install pandas")
        else:
            st.info("Install plotly for charts: pip install plotly")

    # ── Right: module acceptance bar ───────────────────────────────────────
    with chart_r:
        st.markdown("**Test Cases by Module**")
        if PLOTLY_OK and all_cases:
            module_counts: dict[str, int] = defaultdict(int)
            for tc in all_cases:
                mod = tc.get("module") or "Unknown"
                module_counts[mod] += 1

            try:
                import pandas as pd
                mdf = pd.DataFrame([
                    {"module": m, "count": c}
                    for m, c in sorted(module_counts.items(), key=lambda x: x[1], reverse=True)
                ][:10])  # top 10 modules

                fig2 = px.bar(
                    mdf, x="count", y="module",
                    orientation="h",
                    color="count",
                    color_continuous_scale=["#1f2937", "#7c3aed"],
                    labels={"count": "Test Cases", "module": ""},
                )
                fig2.update_layout(
                    paper_bgcolor="rgba(0,0,0,0)",
                    plot_bgcolor="rgba(0,0,0,0)",
                    font_color="#f1f5f9",
                    margin=dict(l=0, r=0, t=10, b=0),
                    showlegend=False,
                    coloraxis_showscale=False,
                    xaxis=dict(showgrid=False, color="#6b7280"),
                    yaxis=dict(gridcolor="rgba(0,0,0,0)", color="#f1f5f9"),
                    height=240,
                )
                st.plotly_chart(fig2, use_container_width=True)
            except ImportError:
                for mod, cnt in sorted(module_counts.items(), key=lambda x: x[1], reverse=True)[:8]:
                    st.markdown(f"- **{mod}**: {cnt}")
        elif not all_cases:
            st.info("No test cases generated yet.")

    st.divider()

    # ── Recent activity ─────────────────────────────────────────────────────
    act_l, act_r = st.columns(2)

    with act_l:
        st.markdown("**Recent Sessions**")
        if sessions:
            try:
                import pandas as pd
                sdf = pd.DataFrame([
                    {
                        "Session":  s.get("session_id", "")[-15:],
                        "Mode":     s.get("mode", "").upper(),
                        "Valid":    s.get("total_valid", 0),
                        "Time":     (s.get("timestamp", "")[:16]).replace("T", " "),
                    }
                    for s in sessions
                ])
                st.dataframe(sdf, use_container_width=True, hide_index=True)
            except ImportError:
                for s in sessions[:5]:
                    st.markdown(f"- `{s.get('mode','?')}` — {s.get('total_valid',0)} valid")
        else:
            st.caption("No sessions yet. Run the pipeline first.")

    with act_r:
        st.markdown("**Module Breakdown (Feedback)**")
        by_module = fb_stats.get("by_module", {})
        if by_module:
            try:
                import pandas as pd
                bdf = pd.DataFrame([
                    {
                        "Module":   mod,
                        "Accepted": data.get("accepted", 0),
                        "Total":    data.get("count", 0),
                        "Rate %":   data.get("rate", 0.0),
                    }
                    for mod, data in by_module.items()
                ])
                st.dataframe(bdf, use_container_width=True, hide_index=True)
            except ImportError:
                for mod, data in by_module.items():
                    st.markdown(f"- **{mod}**: {data.get('rate',0)}%")
        else:
            st.caption("No feedback recorded yet. Accept or reject test cases first.")

    st.divider()

    # ── Cache stats ─────────────────────────────────────────────────────────
    st.markdown("**Cache**")
    cs1, cs2, cs3 = st.columns([1, 1, 2])
    cs1.metric("Cached files", cache_st.get("count", 0))
    cs2.metric("Cache size",   f"{cache_st.get('size_mb', 0.0)} MB")
    with cs3:
        if st.button("🗑️ Clear Cache", key="dash_clear_cache"):
            try:
                from core.cache_manager import CacheManager
                deleted = CacheManager().clear()
                st.success(f"Cleared {deleted} cached file(s)")
                get_cache_stats.clear()
            except Exception as e:
                st.error(f"Could not clear cache: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# TAB 4 — SETTINGS
# ══════════════════════════════════════════════════════════════════════════════

with tab4:
    project = st.session_state.get("active_project", os.getenv("DEFAULT_PROJECT", "example"))

    # ── Setup Progress bar ─────────────────────────────────────────────────
    _req_cfg = [
        ("ANTHROPIC_API_KEY",  "Claude API Key"),
        ("JIRA_BASE_URL",      "Jira URL"),
        ("JIRA_EMAIL",         "Jira Email"),
        ("JIRA_API_TOKEN",     "Jira Token"),
        ("XRAY_CLIENT_ID",     "Xray Client ID"),
        ("XRAY_CLIENT_SECRET", "Xray Secret"),
        ("JIRA_PROJECT_KEY",   "Project Key"),
    ]
    _cfg_done  = sum(1 for k, _ in _req_cfg if os.getenv(k, ""))
    _cfg_total = len(_req_cfg)
    _cfg_pct   = int(_cfg_done / _cfg_total * 100)
    _cfg_color = "#22c55e" if _cfg_pct >= 85 else ("#f59e0b" if _cfg_pct >= 50 else "#ef4444")
    _cfg_label = "All set! 🎉" if _cfg_pct == 100 else f"{_cfg_done}/{_cfg_total} credentials configured"

    st.markdown(
        f'<div style="margin-bottom:20px;padding:14px 18px;background:#111827;'
        f'border:1px solid #1f2937;border-radius:10px">'
        f'<div style="display:flex;justify-content:space-between;'
        f'font-size:0.82rem;color:#9ca3af;margin-bottom:6px">'
        f'<span style="font-weight:600;color:#f1f5f9">⚙️ Setup Progress</span>'
        f'<span style="color:{_cfg_color}">{_cfg_pct}% — {_cfg_label}</span>'
        f'</div>'
        f'<div class="setup-progress">'
        f'<div class="setup-fill" style="width:{_cfg_pct}%"></div>'
        f'</div>'
        f'</div>',
        unsafe_allow_html=True,
    )

    # ── Connections ────────────────────────────────────────────────────────
    st.markdown("### 🔌 Connections")

    env_vars = [
        ("ANTHROPIC_API_KEY",    "Claude API Key",         True),
        ("JIRA_BASE_URL",        "Jira Base URL",          False),
        ("JIRA_EMAIL",           "Jira Email",             False),
        ("JIRA_API_TOKEN",       "Jira API Token",         True),
        ("XRAY_CLIENT_ID",       "Xray Client ID",         True),
        ("XRAY_CLIENT_SECRET",   "Xray Client Secret",     True),
        ("JIRA_PROJECT_KEY",     "Jira Project Key",       False),
    ]
    for key, label, is_secret in env_vars:
        val = os.getenv(key, "")
        display = mask_secret(val) if is_secret else (val or "(not set)")
        status_icon = "✅" if val else "❌"
        st.markdown(
            f'<div class="env-row">'
            f'<span>{status_icon} <strong>{label}</strong></span>'
            f'<span class="env-val">{display}</span>'
            f'</div>',
            unsafe_allow_html=True,
        )

    st.markdown("")
    if st.button("🔌 Test Connections", key="test_conn_btn"):
        conn_terminal = st.empty()
        with st.spinner("Testing…"):
            _, conn_output = run_qax_command(
                ["check-connection"],
                stream_to=conn_terminal,
            )

    st.divider()

    # ── Projects ────────────────────────────────────────────────────────────
    st.markdown("### 🗂️ Projects")

    proj_data = []
    for proj in get_projects():
        proj_dir = QAX_ROOT / "config" / "projects" / proj
        ctx_file = proj_dir / "context.md"
        sel_file = proj_dir / "selectors.json"

        ctx_ok = ctx_file.exists() and ctx_file.stat().st_size > 50
        try:
            sel_json = json.loads(sel_file.read_text()) if sel_file.exists() else {}
            # Count TODO values
            todo_count = sum(
                1 for k, v in sel_json.items()
                if isinstance(v, dict)
                for val in v.values()
                if isinstance(val, str) and "TODO" in val
            )
            sel_status = f"⚠️ {todo_count} TODO" if todo_count else "✅ OK"
        except Exception:
            sel_status = "❓ Error"

        proj_data.append({
            "Project":   f"{proj} ● Active" if proj == project else proj,
            "Context":   "✅ Filled" if ctx_ok else "⚠️ Empty",
            "Selectors": sel_status,
        })

    try:
        import pandas as pd
        st.dataframe(pd.DataFrame(proj_data), use_container_width=True, hide_index=True)
    except ImportError:
        for row in proj_data:
            st.markdown(f"- **{row['Project']}** | {row['Context']} | {row['Selectors']}")

    # ── Active project config summary ──────────────────────────────────────
    _ctx_path = str(QAX_ROOT / "config" / "projects" / project / "context.md")
    _sel_path = str(QAX_ROOT / "config" / "projects" / project / "selectors.json")
    _ctx_size  = os.path.getsize(_ctx_path) if os.path.exists(_ctx_path) else 0
    _todo_cnt  = count_todos(_sel_path)
    st.info(
        f"Active: **{project}** | "
        f"Context: {_ctx_size:,} bytes | "
        f"Selectors TODO: {_todo_cnt}"
    )

    with st.expander("➕ Create New Project"):
        new_proj_name = st.text_input("Project name (lowercase)", key="new_proj_name")
        if st.button("Create Project", key="create_proj_btn"):
            if new_proj_name:
                with st.spinner(f"Creating {new_proj_name}…"):
                    rc, out = run_qax_command(["init-project", "--name", new_proj_name])
                if rc == 0:
                    st.success(f"✅ Project '{new_proj_name}' created!")
                    get_projects.clear()
                    st.rerun()
                else:
                    st.error(f"Failed to create project.\n```\n{out[:300]}\n```")
            else:
                st.warning("Please enter a project name.")

    st.divider()

    # ── Model config ─────────────────────────────────────────────────────────
    st.markdown("### 🤖 Model Configuration")

    heavy = os.getenv("CLAUDE_MODEL_HEAVY", "claude-sonnet-4-5")
    light = os.getenv("CLAUDE_MODEL_LIGHT", "claude-haiku-4-5-20251001")
    dry   = os.getenv("DRY_RUN", "true")

    mc1, mc2, mc3 = st.columns(3)
    mc1.metric("Heavy Model", heavy)
    mc2.metric("Light Model", light)
    mc3.metric("DRY_RUN",     dry)

    st.info("Edit `.env` in the QAX root to change model names or toggle DRY_RUN.")

    st.divider()

    # ── Danger zone ────────────────────────────────────────────────────────
    st.markdown("### ⚠️ Danger Zone")
    with st.container(border=True):
        st.error("These actions are irreversible. Use with caution.")
        dz1, dz2 = st.columns(2)

        with dz1:
            if st.button("🗑️ Clear Cache", key="settings_clear_cache"):
                st.session_state["confirm_clear_cache"] = True
            if st.session_state.get("confirm_clear_cache"):
                st.warning("Are you sure? This will clear all cached results.")
                col_yes, col_no = st.columns(2)
                if col_yes.button("Yes, clear", key="confirm_clear_yes"):
                    try:
                        from core.cache_manager import CacheManager
                        deleted = CacheManager().clear()
                        st.success(f"Cleared {deleted} cached file(s)")
                        get_cache_stats.clear()
                        st.session_state.pop("confirm_clear_cache", None)
                    except Exception as e:
                        st.error(str(e))
                if col_no.button("Cancel", key="confirm_clear_no"):
                    st.session_state.pop("confirm_clear_cache", None)
                    st.rerun()

        with dz2:
            if st.button("⚠️ Reset Feedback DB", key="settings_reset_feedback"):
                st.session_state["confirm_reset_fb"] = True
            if st.session_state.get("confirm_reset_fb"):
                st.warning("This will permanently delete all feedback records.")
                col_yes2, col_no2 = st.columns(2)
                if col_yes2.button("Yes, reset", key="confirm_fb_yes"):
                    try:
                        db_path = QAX_ROOT / "output" / "feedback.db"
                        if db_path.exists():
                            db_path.unlink()
                        st.success("Feedback database reset.")
                        get_feedback_stats.clear()
                        st.session_state.pop("confirm_reset_fb", None)
                    except Exception as e:
                        st.error(str(e))
                if col_no2.button("Cancel", key="confirm_fb_no"):
                    st.session_state.pop("confirm_reset_fb", None)
                    st.rerun()
