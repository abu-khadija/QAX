"""
app.py — QAX Web Dashboard
======================================
Flask-based web dashboard for real-time monitoring of the QA agent's
output: acceptance rates, module coverage, 7-day trends, and sessions.

Data sources:
  - FeedbackStore   → SQLite output/feedback.db (all feedback decisions)
  - output/sessions/ → JSON session files (run history)

Routes:
  GET /            — HTML dashboard
  GET /api/stats   — JSON: aggregate stats, trend, module breakdown
  GET /api/recent  — JSON: last 20 feedback records
  GET /api/sessions — JSON: recent session summaries

All routes return safe defaults and never crash.
DRY_RUN has no effect on the dashboard (read-only, no API calls).

Requirements:
    pip install flask
"""

import json
import logging
import os
import sqlite3
import webbrowser
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Timer

logger = logging.getLogger(__name__)

# ── Flask availability guard ──────────────────────────────────────────────────
try:
    from flask import Flask, jsonify, render_template
    FLASK_AVAILABLE = True
except ImportError:
    FLASK_AVAILABLE = False
    Flask = None  # type: ignore[assignment,misc]
    logger.warning(
        "Flask is not installed. Dashboard is unavailable. "
        "Install with: pip install flask"
    )

# ── Default paths ─────────────────────────────────────────────────────────────
DEFAULT_DB_PATH       = "output/feedback.db"
DEFAULT_SESSIONS_PATH = "output/sessions"
DEFAULT_PORT          = 5000


class DashboardApp:
    """
    Flask web dashboard for the QAX.

    Loads data from the feedback SQLite database and from session JSON files,
    then serves it through a minimal API consumed by the index.html template.

    Usage:
        app = DashboardApp()
        app.start(port=5000)           # blocking — opens browser + runs Flask

        # Or access the Flask app object directly:
        flask_app = app.flask_app
    """

    def __init__(
        self,
        db_path: str = DEFAULT_DB_PATH,
        sessions_path: str = DEFAULT_SESSIONS_PATH,
    ) -> None:
        """
        Initialize the dashboard.

        Args:
            db_path (str): Path to the SQLite feedback database.
                           Default: output/feedback.db
            sessions_path (str): Path to the sessions folder.
                                  Default: output/sessions/
        """
        self.db_path       = db_path
        self.sessions_path = sessions_path

        if not FLASK_AVAILABLE:
            logger.warning("DashboardApp: Flask not installed. start() will fail.")
            self.flask_app = None
            return

        # ── Locate the templates folder next to this file ─────────────────
        template_dir = str(Path(__file__).parent / "templates")

        self.flask_app = Flask(
            __name__,
            template_folder=template_dir,
        )
        # Silence Flask's default request logging to keep terminal clean
        log = logging.getLogger("werkzeug")
        log.setLevel(logging.ERROR)

        self._register_routes()
        logger.info("DashboardApp initialized (db=%s, sessions=%s)", db_path, sessions_path)

    # ─────────────────────────────────────────────────────────────────────────
    # Route registration
    # ─────────────────────────────────────────────────────────────────────────

    def _register_routes(self) -> None:
        """
        Register all Flask URL routes with the Flask app instance.

        Called once in __init__. Adds four routes:
          GET /             — serves the HTML dashboard
          GET /api/stats    — aggregate feedback stats + trends
          GET /api/recent   — last 20 feedback records
          GET /api/sessions — recent session summaries
        """
        app = self.flask_app

        @app.route("/")
        def index():
            """
            Serve the main dashboard HTML page.

            Passes basic stats to the template for server-side rendering of
            the summary cards (so the page has values even before JS loads).

            Returns:
                str: Rendered index.html template.
            """
            try:
                stats = self._get_stats()
                return render_template("index.html", stats=stats)
            except Exception as e:  # noqa: BLE001
                logger.error("Dashboard index error: %s", e)
                return f"<pre>Dashboard error: {e}</pre>", 500

        @app.route("/api/stats")
        def api_stats():
            """
            Return aggregate QA stats as JSON.

            Returns:
                Response: JSON with total_generated, acceptance_rate,
                          time_saved_hours, by_module, trend_7days, etc.
            """
            try:
                return jsonify(self._get_stats())
            except Exception as e:  # noqa: BLE001
                logger.error("/api/stats error: %s", e)
                return jsonify({"error": str(e)}), 500

        @app.route("/api/recent")
        def api_recent():
            """
            Return the last 20 feedback records as a JSON list.

            Returns:
                Response: JSON list of feedback dicts (newest first).
            """
            try:
                return jsonify(self._get_recent_feedback(limit=20))
            except Exception as e:  # noqa: BLE001
                logger.error("/api/recent error: %s", e)
                return jsonify([]), 500

        @app.route("/api/sessions")
        def api_sessions():
            """
            Return recent session summaries as a JSON list.

            Reads the output/sessions/ folder and returns summaries of the
            most recent 20 session files.

            Returns:
                Response: JSON list of session summary dicts.
            """
            try:
                return jsonify(self._get_sessions(limit=20))
            except Exception as e:  # noqa: BLE001
                logger.error("/api/sessions error: %s", e)
                return jsonify([]), 500

    # ─────────────────────────────────────────────────────────────────────────
    # Public: start
    # ─────────────────────────────────────────────────────────────────────────

    def start(self, port: int = DEFAULT_PORT) -> None:
        """
        Start the Flask server and open the dashboard in the browser.

        Opens http://localhost:{port} automatically in the user's default
        browser after a short delay (to let Flask start first), then runs
        the Flask development server in the foreground.

        Args:
            port (int): TCP port to listen on. Default: 5000.

        Returns:
            None. Blocks until Flask is stopped (Ctrl+C).
        """
        if not FLASK_AVAILABLE or not self.flask_app:
            print("\n  ✗ Flask is not installed.")
            print("    Install with: pip install flask")
            print("    Then re-run: python main.py dashboard\n")
            return

        url = f"http://localhost:{port}"
        print(f"\n  QAX — Dashboard")
        print(f"  " + "─" * 40)
        print(f"  URL     : {url}")
        print(f"  DB      : {self.db_path}")
        print(f"  Sessions: {self.sessions_path}/")
        print(f"  " + "─" * 40)
        print(f"  Press Ctrl+C to stop.\n")

        # Open browser after 1 second (gives Flask time to bind the port)
        Timer(1.0, lambda: webbrowser.open(url)).start()

        self.flask_app.run(
            host="127.0.0.1",
            port=port,
            debug=False,   # Keep off — debug mode re-runs __init__ on reload
            use_reloader=False,
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Private: data methods
    # ─────────────────────────────────────────────────────────────────────────

    def _get_stats(self) -> dict:
        """
        Build and return the full stats dict for the /api/stats endpoint.

        Reads from:
          - feedback SQLite DB for decision counts, acceptance rate, module
            breakdown, and 7-day trend
          - output/ JSON files to estimate total_generated count

        The time_saved_hours calculation:
          accepted_count * 15 minutes / 60 = hours saved
          (assumes each accepted test case saves 15 minutes of manual work)

        Returns:
            dict: Stats dict with keys: total_generated, total_accepted,
                  total_rejected, total_modified, acceptance_rate,
                  avg_confidence, time_saved_hours, by_module, trend_7days.
        """
        if not Path(self.db_path).exists():
            return self._empty_stats()

        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row

                # ── Overall counts ─────────────────────────────────────────
                row = conn.execute("""
                    SELECT
                        COUNT(*) as total,
                        SUM(CASE WHEN action = 'accepted' THEN 1 ELSE 0 END) as accepted,
                        SUM(CASE WHEN action = 'rejected' THEN 1 ELSE 0 END) as rejected,
                        SUM(CASE WHEN action = 'modified' THEN 1 ELSE 0 END) as modified,
                        AVG(CASE WHEN action = 'accepted'
                            THEN confidence_score END) as avg_conf
                    FROM feedback
                """).fetchone()

                total    = row["total"] or 0
                accepted = row["accepted"] or 0
                rejected = row["rejected"] or 0
                modified = row["modified"] or 0
                avg_conf = round(row["avg_conf"] or 0.0, 1)
                rate     = round((accepted / total * 100) if total > 0 else 0.0, 1)

                # ── Module breakdown ───────────────────────────────────────
                module_rows = conn.execute("""
                    SELECT
                        module,
                        COUNT(*) as total,
                        SUM(CASE WHEN action = 'accepted' THEN 1 ELSE 0 END) as accepted
                    FROM feedback
                    WHERE module IS NOT NULL AND module != ''
                    GROUP BY module
                    ORDER BY total DESC
                """).fetchall()

                by_module = {}
                for mrow in module_rows:
                    mod       = mrow["module"]
                    mod_total = mrow["total"]
                    mod_acc   = mrow["accepted"] or 0
                    by_module[mod] = {
                        "total":    mod_total,
                        "accepted": mod_acc,
                        "rate":     round((mod_acc / mod_total * 100) if mod_total > 0 else 0.0, 1),
                    }

                # ── 7-day trend ────────────────────────────────────────────
                trend_7days = self._build_trend_7days(conn)

        except Exception as e:  # noqa: BLE001
            logger.error("_get_stats DB error: %s", e)
            return self._empty_stats()

        # ── total_generated: count rows in output/*.json ───────────────────
        total_generated = self._count_generated_cases()

        # ── time saved: 15 min per accepted case ──────────────────────────
        time_saved_hours = round(accepted * 15 / 60, 1)

        return {
            "total_generated":  total_generated,
            "total_accepted":   accepted,
            "total_rejected":   rejected,
            "total_modified":   modified,
            "acceptance_rate":  rate,
            "avg_confidence":   avg_conf,
            "time_saved_hours": time_saved_hours,
            "by_module":        by_module,
            "trend_7days":      trend_7days,
        }

    def _build_trend_7days(self, conn) -> list[dict]:
        """
        Build a 7-entry list of daily generated/accepted counts for the
        last 7 calendar days, suitable for a line chart.

        Queries the feedback table for rows with timestamps in the last
        7 days, grouped by date. Missing days are filled with zeros.

        Args:
            conn: Open sqlite3 connection with row_factory set.

        Returns:
            list[dict]: 7 dicts, each with keys: date (YYYY-MM-DD),
                        generated (int), accepted (int). Ordered oldest → newest.
        """
        today = datetime.now(timezone.utc).date()
        days  = [today - timedelta(days=i) for i in range(6, -1, -1)]  # oldest first

        # Build a lookup from date → {generated, accepted}
        rows = conn.execute("""
            SELECT
                DATE(timestamp) as day,
                COUNT(*) as generated,
                SUM(CASE WHEN action = 'accepted' THEN 1 ELSE 0 END) as accepted
            FROM feedback
            WHERE timestamp >= ?
            GROUP BY day
            ORDER BY day
        """, (days[0].isoformat(),)).fetchall()

        day_map = {row["day"]: row for row in rows}

        trend = []
        for d in days:
            key = d.isoformat()
            row = day_map.get(key)
            trend.append({
                "date":      key,
                "generated": row["generated"] if row else 0,
                "accepted":  row["accepted"]  if row else 0,
            })
        return trend

    def _get_recent_feedback(self, limit: int = 20) -> list[dict]:
        """
        Return the most recent feedback records from the database.

        Args:
            limit (int): Maximum number of records to return.

        Returns:
            list[dict]: Feedback records, newest first, with keys:
                        test_case_id, title, action, module,
                        confidence_score, timestamp.
        """
        if not Path(self.db_path).exists():
            return []
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute("""
                    SELECT test_case_id, title, action, module,
                           confidence_score, timestamp
                    FROM feedback
                    ORDER BY timestamp DESC
                    LIMIT ?
                """, (limit,)).fetchall()
            return [dict(r) for r in rows]
        except Exception as e:  # noqa: BLE001
            logger.error("_get_recent_feedback error: %s", e)
            return []

    def _get_sessions(self, limit: int = 20) -> list[dict]:
        """
        Return recent session summaries from the output/sessions/ folder.

        Each session file is a JSON file saved by SessionManager.save_session().
        Returns the most recent sessions (by filename, which includes a
        timestamp) up to the given limit.

        Args:
            limit (int): Maximum number of sessions to return.

        Returns:
            list[dict]: Session summary dicts, newest first.
        """
        sessions_dir = Path(self.sessions_path)
        if not sessions_dir.exists():
            return []
        try:
            files = sorted(
                sessions_dir.glob("*.json"),
                key=lambda p: p.name,
                reverse=True,
            )[:limit]

            results = []
            for f in files:
                try:
                    with open(f, encoding="utf-8") as fh:
                        data = json.load(fh)
                    results.append({
                        "session_id":       data.get("session_id", f.stem),
                        "mode":             data.get("mode", "?"),
                        "timestamp":        data.get("timestamp", ""),
                        "files_processed":  data.get("results", {}).get("files_processed", 0),
                        "total_generated":  data.get("results", {}).get("total_generated", 0),
                        "total_valid":      data.get("results", {}).get("total_valid", 0),
                        "pushed_count":     data.get("results", {}).get("pushed_count", 0),
                        "duration_seconds": data.get("results", {}).get("duration_seconds", 0),
                        "output_file":      data.get("results", {}).get("output_file", ""),
                    })
                except (json.JSONDecodeError, IOError):
                    continue
            return results
        except Exception as e:  # noqa: BLE001
            logger.error("_get_sessions error: %s", e)
            return []

    def _count_generated_cases(self) -> int:
        """
        Count the total test cases across all output/test_cases_*.json files.

        Returns:
            int: Sum of test cases in all output JSON files, or 0 on error.
        """
        output_dir = Path("output")
        if not output_dir.exists():
            return 0
        total = 0
        try:
            for json_file in output_dir.glob("test_cases_*.json"):
                try:
                    with open(json_file, encoding="utf-8") as f:
                        data = json.load(f)
                    if isinstance(data, list):
                        total += len(data)
                except (json.JSONDecodeError, IOError):
                    continue
        except Exception as e:  # noqa: BLE001
            logger.debug("_count_generated_cases error: %s", e)
        return total

    @staticmethod
    def _empty_stats() -> dict:
        """
        Return a zeroed-out stats dict for when no data is available.

        Returns:
            dict: Stats dict with all numeric fields set to 0.
        """
        return {
            "total_generated":  0,
            "total_accepted":   0,
            "total_rejected":   0,
            "total_modified":   0,
            "acceptance_rate":  0.0,
            "avg_confidence":   0.0,
            "time_saved_hours": 0.0,
            "by_module":        {},
            "trend_7days": [
                {"date": (datetime.now(timezone.utc).date() - timedelta(days=i)).isoformat(),
                 "generated": 0, "accepted": 0}
                for i in range(6, -1, -1)
            ],
        }
