"""
dashboard/ — QAX Web Dashboard
==========================================
Prompt 6: Dashboard + CI/CD Layer.

Provides a real-time web dashboard for the QAX.
Shows acceptance stats, module breakdowns, 7-day trends,
recent feedback activity, and session history.

Requires Flask:
    pip install flask

Usage:
    python main.py dashboard
    # Opens http://localhost:5000 automatically

Or programmatically:
    from dashboard.app import DashboardApp
    app = DashboardApp()
    app.start(port=5000)
"""

try:
    from dashboard.app import DashboardApp
    __all__ = ["DashboardApp"]
except ImportError:
    __all__ = []
