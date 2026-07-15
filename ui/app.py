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

    # Detect project change and clear stale results
    if st.session_state.get("last_project") != project:
        st.session_state["analyze_results"] = None
        st.session_state["last_project"] = project

    # ── Onboarding banner — shown only when no results exist ─────────────
    if not st.session_state.get("onboarded") and not load_all_test_cases():
        st.markdown(
            '<div class="onboarding-banner">'
            '<div style="font-size:2.4rem;margin-bottom:10px">🤖</div>'
            '<div style="font-size:1.25rem;font-weight:800;color:#f1f5f9;margin-bottom:8px">'
            'Welcome to QAX — AI QA Engineer'
            '</div>'
            '<div style="font-size:0.88rem;color:#9ca3af;max-width:500px;margin:0 auto 16px">'
            'Point QAX at any source file and get production-ready test cases in seconds.'
            '</div>'
            '<div style="display:flex;justify-content:center;flex-wrap:wrap">'
            '<span class="onboarding-step">📁 Step 1 · Pick a source file</span>'
            '<span class="onboarding-step">🤖 Step 2 · Click Generate</span>'
            '<span class="onboarding-step">✅ Step 3 · Accept &amp; push to Jira</span>'
            '</div>'
            '</div>',
            unsafe_allow_html=True,
        )

    left, right = st.columns([4, 6])

    # ── LEFT: inputs ───────────────────────────────────────────────────────
    with left:
        st.subheader("Source")

        input_method = st.radio(
            "Input method",
            ["File path", "Upload file"],
            horizontal=True,
            key="analyze_input_method",
        )

        file_path_input = ""
        uploaded_tmp: str | None = None

        if input_method == "File path":
            _known_ext = {"example3": ".ts", "example2": ".cs"}
            _ext = _known_ext.get(project, ".cs")
            file_path_input = st.text_input(
                "File path",
                placeholder=f"C:/project/Controller{_ext}",
                key="analyze_file_path",
            )
        else:
            uploaded = st.file_uploader(
                "Upload source file",
                type=["cs", "py", "js", "ts", "java", "kt", "rb", "go"],
                key="analyze_uploader",
            )
            if uploaded:
                suffix = Path(uploaded.name).suffix
                with tempfile.NamedTemporaryFile(
                    delete=False, suffix=suffix, dir=str(QAX_ROOT / "output")
                ) as tmp:
                    tmp.write(uploaded.getbuffer())
                    uploaded_tmp = tmp.name
                st.success(f"Uploaded: {uploaded.name}")

        user_story = st.text_area(
            "User Story (optional)",
            placeholder=(
                "As a user I want to...\n"
                "Acceptance criteria:\n- ..."
            ),
            height=80,
            key="analyze_story",
        )

        with st.expander("⚙️ Options"):
            oc1, oc2 = st.columns(2)
            with oc1:
                include_git  = st.checkbox("Git history",    key="opt_git")
                include_deps = st.checkbox("Dependencies",   key="opt_deps")
                include_bugs = st.checkbox("Bug patterns",   key="opt_bugs")
            with oc2:
                auto_accept  = st.checkbox("Auto-accept ≥8.5", key="opt_auto")
                natural_out  = st.checkbox("Natural output",   key="opt_natural")
                chunk_size   = st.select_slider(
                    "Chunk size",
                    options=[100, 150, 200, 250, 300, 400, 500],
                    value=300,
                    key="opt_chunk",
                )
            test_type = st.selectbox(
                "Test type",
                ["auto", "regression", "feature", "smoke", "uat"],
                key="opt_testtype",
            )

        dry_run_analyze = st.toggle(
            "DRY RUN",
            value=True,
            help="No changes pushed to Jira/Xray",
            key="analyze_dryrun",
        )

        generate_btn = st.button(
            "🔍 Generate Test Cases",
            type="primary",
            use_container_width=True,
            key="analyze_generate",
        )

    # ── RIGHT: results ─────────────────────────────────────────────────────
    with right:
        if generate_btn:
            effective_path = uploaded_tmp or file_path_input
            if not effective_path:
                st.error("Please provide a file path or upload a file.")
            else:
                # ── Build command args ─────────────────────────────────────
                args = [
                    "analyze",
                    "--file", effective_path,
                    "--project", project,
                    "--chunk-size", str(chunk_size),
                ]
                if dry_run_analyze:
                    args.append("--dry-run")
                if include_git:
                    args.append("--include-git")
                if include_deps:
                    args.append("--include-deps")
                if include_bugs and st.session_state.get("analyze_module"):
                    args += ["--include-bugs", "--module", st.session_state.analyze_module]
                if auto_accept:
                    args.append("--auto-accept")
                if natural_out:
                    args.append("--natural")
                if user_story:
                    args += ["--story", user_story]
                if test_type != "auto":
                    args += ["--test-type", test_type]

                # ── Step-by-step progress + streaming terminal ─────────────
                st.markdown("**Progress**")
                _steps: dict[str, dict] = {
                    "risk":   {"label": "Risk assessment",   "done": False, "icon": "🔍"},
                    "cache":  {"label": "Cache lookup",      "done": False, "icon": "⚡"},
                    "claude": {"label": "Sending to Claude", "done": False, "icon": "🤖"},
                    "valid":  {"label": "Validating output", "done": False, "icon": "✅"},
                }

                def _steps_html(steps: dict) -> str:
                    """Render step-by-step progress as HTML."""
                    html = '<div style="padding:4px 0 10px">'
                    for s in steps.values():
                        cls = "step-done" if s["done"] else "step-pending"
                        mark = "✓" if s["done"] else "○"
                        html += (
                            f'<div class="progress-step {cls}">'
                            f'<span style="width:16px;display:inline-block">{mark}</span>'
                            f'{s["icon"]} {s["label"]}'
                            f'</div>'
                        )
                    html += '</div>'
                    return html

                prog_placeholder = st.empty()
                prog_placeholder.markdown(_steps_html(_steps), unsafe_allow_html=True)
                st.markdown("**Output**")
                terminal_placeholder = st.empty()

                try:
                    # Stream subprocess line-by-line, updating progress steps
                    _cmd = [sys.executable, str(QAX_ROOT / "main.py")] + args
                    _proc = subprocess.Popen(
                        _cmd,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        bufsize=1,
                        cwd=str(QAX_ROOT),
                    )
                    _lines: list[str] = []
                    for _line in _proc.stdout:  # type: ignore[union-attr]
                        _lines.append(_line)
                        # Parse progress signals
                        _ll = _line.lower()
                        if not _steps["risk"]["done"]   and ("risk:" in _ll or "risk level" in _ll):
                            _steps["risk"]["done"] = True
                        if not _steps["cache"]["done"]  and "cache hit" in _ll:
                            _steps["cache"]["done"] = True
                        if not _steps["claude"]["done"] and ("sending to claude" in _ll or "claude" in _ll and "send" in _ll):
                            _steps["claude"]["done"] = True
                        if not _steps["valid"]["done"]  and ("schema pass" in _ll or "valid (" in _ll):
                            _steps["valid"]["done"] = True
                        prog_placeholder.markdown(_steps_html(_steps), unsafe_allow_html=True)
                        terminal_placeholder.markdown(
                            f'<div class="terminal">{"".join(_lines[-60:])}</div>',
                            unsafe_allow_html=True,
                        )
                    _proc.wait()
                    rc     = _proc.returncode
                    output = "".join(_lines)
                    # Mark all steps done on success
                    if rc == 0:
                        for _s in _steps.values():
                            _s["done"] = True
                        prog_placeholder.markdown(_steps_html(_steps), unsafe_allow_html=True)

                    # ── Load results ───────────────────────────────────────
                    load_all_test_cases.clear()
                    results = load_latest_test_cases()
                    st.session_state["analyze_results"] = results
                    st.session_state["analyze_output"]  = output
                    st.session_state["analyze_rc"]      = rc
                    if rc == 0 and results:
                        st.session_state["onboarded"] = True
                        st.toast(f"✅ Generated {len(results)} test case(s)", icon="🤖")
                    elif rc != 0:
                        st.toast("Generation failed — check output for details", icon="❌")
                except Exception as _exc:
                    rc, output = 1, str(_exc)
                    st.session_state["analyze_rc"] = rc
                    st.error(f"Command error: {_exc}")
                finally:
                    if uploaded_tmp and os.path.exists(uploaded_tmp):
                        try:
                            os.unlink(uploaded_tmp)
                        except OSError:
                            pass

        # ── Show results ───────────────────────────────────────────────────
        results: list[dict] = st.session_state.get("analyze_results", [])

        if not results and not generate_btn:
            st.markdown(
                '<div class="empty-state">'
                '<div style="font-size:2.5rem;margin-bottom:12px">🔍</div>'
                '<div style="font-size:1rem;font-weight:600;color:#9ca3af">Run analysis to see results here</div>'
                '<div style="font-size:0.82rem;margin-top:8px">Choose a file and click Generate Test Cases</div>'
                '</div>',
                unsafe_allow_html=True,
            )
        elif results:
            # ── Metric cards ───────────────────────────────────────────────
            auto_acc_count = sum(1 for t in results if t.get("auto_accepted"))
            avg_conf = (
                sum(t.get("confidence_score", 0) for t in results) / len(results)
                if results else 0
            )
            mc1, mc2, mc3, mc4 = st.columns(4)
            mc1.metric("Generated",      len(results))
            mc2.metric("Valid",          len(results))
            mc3.metric("Auto-accepted",  auto_acc_count)
            mc4.metric("Avg Confidence", f"{avg_conf:.1f}/10")

            st.divider()

            # ── Test case cards ────────────────────────────────────────────
            for i, tc in enumerate(results):
                title    = tc.get("title", "Untitled")
                tc_id    = tc.get("test_case_id", f"TC-{i+1:04d}")
                module   = tc.get("module",       "N/A")
                tt       = tc.get("test_type",    "functional")
                risk     = tc.get("risk_level",   "medium")
                score    = float(tc.get("confidence_score", 0))
                needs_rv = tc.get("needs_review", False)
                note     = tc.get("review_note",  "")

                # Determine card border class by confidence score
                _tc_cls = "tc-high" if score >= 8 else ("tc-medium" if score >= 6 else "tc-low")

                # Card header
                st.markdown(
                    f'<div class="qax-card {_tc_cls}">'
                    f'<div class="qax-card-title">{tc_id} — {title}</div>'
                    f'<div style="margin-bottom:6px">'
                    f'{badge(module, "purple")}'
                    f'{badge(tt, "teal")}'
                    f'{badge(risk.upper(), risk_badge_class(risk))}'
                    f'{badge(f"{score}/10", score_badge_class(score))}'
                    + (f'{badge("⚠ Review", "amber")}' if needs_rv else "")
                    + f'</div>'
                    f'{confidence_bar(score)}'
                    f'</div>',
                    unsafe_allow_html=True,
                )

                # Expandable steps
                with st.expander(f"View steps — {tc_id}"):
                    manual = tc.get("manual_steps", [])
                    if manual:
                        st.markdown("**Manual Steps**")
                        for step in manual:
                            sn = step.get("step_number", "?")
                            act = step.get("action", "")
                            exp = step.get("expected_result", "")
                            st.markdown(f"**{sn}.** {act}")
                            if exp:
                                st.caption(f"Expected: {exp}")
                    auto_steps = tc.get("automation_steps", [])
                    if auto_steps:
                        st.markdown("**Automation Steps**")
                        for step in auto_steps:
                            sn  = step.get("step_number", "?")
                            act = step.get("playwright_action", "")
                            sel = step.get("selector_key", "")
                            st.markdown(f"**{sn}.** `{act}` → `{sel}`")
                    if needs_rv and note:
                        st.warning(f"Review note: {note}")

                # Action row — styled buttons via wrapper divs
                ac1, ac2, ac3 = st.columns([1, 1, 1])
                with ac1:
                    st.markdown('<div class="btn-accept-wrap">', unsafe_allow_html=True)
                    if st.button("✓ Accept", key=f"acc_{i}", use_container_width=True):
                        try:
                            from core.feedback_store import FeedbackStore
                            FeedbackStore().record_decision(tc, action="accepted")
                            st.toast(f"✓ Accepted: {tc_id}", icon="✅")
                            get_feedback_stats.clear()
                        except Exception as e:
                            st.toast(f"Could not record: {e}", icon="❌")
                    st.markdown('</div>', unsafe_allow_html=True)
                with ac2:
                    st.markdown('<div class="btn-reject-wrap">', unsafe_allow_html=True)
                    if st.button("✗ Reject", key=f"rej_{i}", use_container_width=True):
                        try:
                            from core.feedback_store import FeedbackStore
                            FeedbackStore().record_decision(tc, action="rejected")
                            st.toast(f"✗ Rejected: {tc_id}", icon="❌")
                            get_feedback_stats.clear()
                        except Exception as e:
                            st.toast(f"Could not record: {e}", icon="❌")
                    st.markdown('</div>', unsafe_allow_html=True)
                with ac3:
                    push_disabled = dry_run_analyze
                    st.markdown('<div class="btn-push-wrap">', unsafe_allow_html=True)
                    if st.button(
                        "↑ Push to Xray",
                        key=f"push_{i}",
                        use_container_width=True,
                        disabled=push_disabled,
                        help="Turn off DRY RUN to enable push" if push_disabled else f"Push {tc_id} to Xray",
                    ):
                        try:
                            from core.xray_client import XrayClient
                            xray = XrayClient()
                            res  = xray.push_batch([tc])
                            pushed = res.get("summary", {}).get("pushed_count", 0)
                            st.toast(f"⬆️ Pushed {pushed} test case(s) to Xray", icon="⬆️")
                        except Exception as e:
                            st.toast(f"Push failed: {e}", icon="❌")
                    st.markdown('</div>', unsafe_allow_html=True)

                st.markdown('<div style="height:4px"></div>', unsafe_allow_html=True)

            st.divider()

            # ── Export row ─────────────────────────────────────────────────
            ex1, ex2 = st.columns(2)
            with ex1:
                md_text = build_markdown_export(results)
                st.download_button(
                    "📄 Export as Markdown",
                    data=md_text,
                    file_name="test_cases.md",
                    mime="text/markdown",
                    use_container_width=True,
                )
            with ex2:
                if st.button("📋 Copy all IDs", use_container_width=True):
                    ids = [tc.get("test_case_id", "") for tc in results if tc.get("test_case_id")]
                    st.code(", ".join(ids))


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
