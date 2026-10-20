"""
feedback_store.py — Human Feedback Loop Storage
=================================================
Records QA analyst decisions (accept/reject/modify) on AI-generated
test cases using a local SQLite database. This data is used to:
  1. Track acceptance rates per module
  2. Identify patterns in what gets rejected
  3. Feed into the prompt optimizer to improve future generations

Database file: output/feedback.db (auto-created on first run)
No installation needed — uses Python's built-in sqlite3 module.
"""

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

# ── Logging setup ─────────────────────────────────────────────────────────────
logger = logging.getLogger(__name__)

# ── Database location ─────────────────────────────────────────────────────────
from core.paths import output_dir  # noqa: E402 — never site-packages on a wheel install
DB_PATH = output_dir() / "feedback.db"

# ── Minimum reviews needed before pattern analysis is meaningful ───────────────
MIN_REVIEWS_FOR_PATTERNS = 20


class FeedbackStore:
    """
    SQLite-backed store for human QA feedback on AI-generated test cases.

    Records every accept/reject/modify decision and provides aggregated
    statistics that can be used to optimize the AI prompt over time.

    Usage:
        store = FeedbackStore()
        store.record_decision(test_case_dict, action="accepted")
        stats = store.get_stats()
        summary = store.get_patterns_summary()
    """

    def __init__(self, db_path: str | None = None):
        """
        Initialize the feedback store and create the database table if needed.

        Args:
            db_path (str | None): Custom path for the SQLite database file.
                                  Defaults to output/feedback.db.
        """
        self.db_path = Path(db_path) if db_path else DB_PATH

        # Ensure the output directory exists
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        # Initialize database schema on first run
        self._init_db()
        logger.debug("FeedbackStore initialized at %s", self.db_path)

    def _init_db(self) -> None:
        """
        Create the feedback and meta tables if they don't already exist.
        Safe to call multiple times — uses CREATE TABLE IF NOT EXISTS.
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS feedback (
                    id               INTEGER PRIMARY KEY AUTOINCREMENT,
                    test_case_id     TEXT NOT NULL,
                    title            TEXT,
                    action           TEXT NOT NULL CHECK(action IN ('accepted', 'rejected', 'modified')),
                    module           TEXT,
                    confidence_score INTEGER,
                    original_json    TEXT NOT NULL,
                    modified_json    TEXT,
                    timestamp        TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS meta (
                    key   TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
            """)
            conn.commit()
        logger.debug("Database schema initialized")

    def record_decision(
        self,
        test_case: dict,
        action: str,
        modified: dict | None = None,
    ) -> None:
        """
        Save a QA analyst's decision on a test case to the database.

        Args:
            test_case (dict): The original AI-generated test case dictionary.
            action (str): The decision made — one of: 'accepted', 'rejected', 'modified'.
            modified (dict | None): If action='modified', the edited version of the test case.
                                     None for accept/reject decisions.

        Raises:
            ValueError: If action is not one of the valid values.
        """
        valid_actions = ("accepted", "rejected", "modified")
        if action not in valid_actions:
            raise ValueError(
                f"Invalid action '{action}'. Must be one of: {', '.join(valid_actions)}"
            )

        # Serialize test case dicts to JSON strings for storage
        original_json = json.dumps(test_case, ensure_ascii=False)
        modified_json = json.dumps(modified, ensure_ascii=False) if modified else None

        timestamp = datetime.now(timezone.utc).isoformat()

        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO feedback
                    (test_case_id, title, action, module, confidence_score,
                     original_json, modified_json, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    test_case.get("test_case_id", "UNKNOWN"),
                    test_case.get("title", ""),
                    action,
                    test_case.get("module", ""),
                    test_case.get("confidence_score"),
                    original_json,
                    modified_json,
                    timestamp,
                )
            )
            conn.commit()

        logger.info(
            "Recorded decision: %s → %s (module: %s)",
            test_case.get("test_case_id", "UNKNOWN"), action, test_case.get("module", "?")
        )

    def get_stats(self) -> dict:
        """
        Return aggregated statistics across all recorded feedback decisions.

        Returns:
            dict: Statistics dictionary with these keys:
                - total_reviewed (int): Total decisions recorded
                - accepted (int): Count of accepted test cases
                - rejected (int): Count of rejected test cases
                - modified (int): Count of modified test cases
                - acceptance_rate (float): Percentage accepted (0.0–100.0)
                - by_module (dict): Per-module breakdown with rate and count
                - avg_confidence_accepted (float): Avg confidence of accepted cases
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row  # Access columns by name

            # ── Overall counts ────────────────────────────────────────────────
            row = conn.execute("""
                SELECT
                    COUNT(*) as total,
                    SUM(CASE WHEN action = 'accepted' THEN 1 ELSE 0 END) as accepted,
                    SUM(CASE WHEN action = 'rejected' THEN 1 ELSE 0 END) as rejected,
                    SUM(CASE WHEN action = 'modified' THEN 1 ELSE 0 END) as modified,
                    AVG(CASE WHEN action = 'accepted' THEN confidence_score END) as avg_conf_accepted
                FROM feedback
            """).fetchone()

            total = row["total"] or 0
            accepted = row["accepted"] or 0
            rejected = row["rejected"] or 0
            modified = row["modified"] or 0
            avg_conf = round(row["avg_conf_accepted"] or 0.0, 2)
            acceptance_rate = round((accepted / total * 100) if total > 0 else 0.0, 1)

            # ── Per-module breakdown ──────────────────────────────────────────
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
                mod = mrow["module"]
                mod_total = mrow["total"]
                mod_accepted = mrow["accepted"] or 0
                by_module[mod] = {
                    "count": mod_total,
                    "accepted": mod_accepted,
                    "rate": round((mod_accepted / mod_total * 100) if mod_total > 0 else 0.0, 1),
                }

        return {
            "total_reviewed": total,
            "accepted": accepted,
            "rejected": rejected,
            "modified": modified,
            "acceptance_rate": acceptance_rate,
            "by_module": by_module,
            "avg_confidence_accepted": avg_conf,
        }

    def get_patterns_summary(self) -> str:
        """
        Generate a text summary of feedback patterns for use as input to
        the prompt optimizer. Returns early if not enough data yet.

        Returns:
            str: Human-readable summary of acceptance patterns, or a message
                 indicating not enough data has been collected yet.
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            total = conn.execute("SELECT COUNT(*) as n FROM feedback").fetchone()["n"]

        if total < MIN_REVIEWS_FOR_PATTERNS:
            remaining = MIN_REVIEWS_FOR_PATTERNS - total
            return (
                f"Not enough data yet. "
                f"{total} decisions recorded — need {remaining} more before pattern analysis."
            )

        stats = self.get_stats()
        lines = [
            f"Feedback Analysis Summary ({stats['total_reviewed']} decisions recorded)",
            f"Overall acceptance rate: {stats['acceptance_rate']}%",
            f"Average confidence score of accepted cases: {stats['avg_confidence_accepted']}/10",
            "",
            "Per-module breakdown:",
        ]

        for module, data in stats["by_module"].items():
            lines.append(
                f"  {module}: {data['rate']}% acceptance rate "
                f"({data['accepted']}/{data['count']} accepted)"
            )

        # ── Find low-acceptance modules for targeted prompt improvements ──────
        low_acceptance = [
            (mod, data) for mod, data in stats["by_module"].items()
            if data["count"] >= 3 and data["rate"] < 60
        ]
        if low_acceptance:
            lines.append("")
            lines.append("Modules with low acceptance rates (need prompt improvement):")
            for mod, data in low_acceptance:
                lines.append(f"  {mod}: {data['rate']}% — review test case quality")

        return "\n".join(lines)

    def get_recent_rejections(self, limit: int = 10) -> list[dict]:
        """
        Return the most recently rejected test cases for inspection.

        Args:
            limit (int): Maximum number of rejections to return.

        Returns:
            list[dict]: List of rejection records with test_case_id, title, module, timestamp.
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT test_case_id, title, module, confidence_score, timestamp
                FROM feedback
                WHERE action = 'rejected'
                ORDER BY timestamp DESC
                LIMIT ?
                """,
                (limit,)
            ).fetchall()

        return [dict(row) for row in rows]

    def should_trigger_optimization(self) -> bool:
        """
        Check whether prompt optimization should be suggested to the user.

        Returns True when:
          - Total reviews >= 50 (enough data to be meaningful), AND
          - The last optimization run was > 7 days ago (or never run).

        Returns:
            bool: True if the user should be prompted to run --optimize.
        """
        try:
            stats = self.get_stats()
            if stats["total_reviewed"] < 50:
                return False

            with sqlite3.connect(self.db_path) as conn:
                row = conn.execute(
                    "SELECT value FROM meta WHERE key = 'last_optimization'"
                ).fetchone()

            if row is None:
                return True   # Never optimized — trigger

            last_opt = datetime.fromisoformat(row[0])
            now = datetime.now(timezone.utc)
            days_since = (now - last_opt.replace(tzinfo=timezone.utc)).days
            return days_since >= 7

        except Exception as e:
            logger.debug("should_trigger_optimization error (non-fatal): %s", e)
            return False

    def mark_optimization_done(self) -> None:
        """
        Record the current timestamp as the last prompt optimization run.
        Called after --optimize suggestions are applied to avoid re-triggering
        the tip on every subsequent feedback entry.
        """
        now = datetime.now(timezone.utc).isoformat()
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO meta (key, value) VALUES ('last_optimization', ?)",
                    (now,)
                )
                conn.commit()
            logger.info("Optimization timestamp recorded: %s", now)
        except Exception as e:
            logger.debug("mark_optimization_done error (non-fatal): %s", e)

    def find_by_id(self, test_case_id: str) -> dict | None:
        """
        Look up a test case in the feedback store by its ID.

        Args:
            test_case_id (str): The test_case_id to look up (e.g., 'TC-TRK-0001').

        Returns:
            dict | None: The most recent feedback record for this ID, or None if not found.
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                """
                SELECT * FROM feedback
                WHERE test_case_id = ?
                ORDER BY timestamp DESC
                LIMIT 1
                """,
                (test_case_id,)
            ).fetchone()

        if row is None:
            return None

        result = dict(row)
        # Deserialize JSON fields
        if result.get("original_json"):
            result["original"] = json.loads(result["original_json"])
        if result.get("modified_json"):
            result["modified"] = json.loads(result["modified_json"])
        return result
