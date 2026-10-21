"""
git_analyzer.py — Git Commit History Risk Analyzer
====================================================
Analyzes the git commit history of a source file to assess how
risky/bug-prone it is. Files with many bug-fix commits deserve more
thorough test coverage — especially regression and error-path tests.

Uses Python's subprocess module to run git commands.
Never crashes — returns safe defaults if git is unavailable.
"""

import logging
import re
import subprocess
from pathlib import Path

# ── Logging setup ─────────────────────────────────────────────────────────────
logger = logging.getLogger(__name__)

# ── Keywords that indicate a commit was a bug fix ─────────────────────────────
BUG_FIX_KEYWORDS = ["fix", "bug", "hotfix", "patch", "error", "issue", "defect", "revert"]

# ── Risk level thresholds (based on bug-fix commit count) ─────────────────────
RISK_HIGH_THRESHOLD = 3    # 3+ bug-fix commits → high risk
RISK_MEDIUM_THRESHOLD = 1  # 1-2 bug-fix commits → medium risk

# ── Safe default returned when git is unavailable or fails ────────────────────
SAFE_DEFAULT = {
    "commits": [],
    "risk_level": "low",
    "bug_fix_count": 0,
    "total_commits": 0,
}


class GitAnalyzer:
    """
    Analyzes git commit history for a source file to determine testing risk level.

    A file with many bug-fix commits in its history is more likely to have
    regressions introduced — Claude should generate extra test cases for it.

    Usage:
        analyzer = GitAnalyzer()
        history = analyzer.get_file_history("src/orders/OrderForm.cs")
        if history["risk_level"] == "high":
            print(f"High risk file! {history['bug_fix_count']} bug-fix commits")
    """

    def get_file_history(
        self,
        file_path: str,
        max_commits: int = 10,
    ) -> dict:
        """
        Retrieve git commit history for a file and assess its risk level.

        Runs: git log --follow -n {max_commits} --pretty=format:"%H|%s|%ad|%an" --date=short

        Args:
            file_path (str): Path to the source file (absolute or relative).
            max_commits (int): Maximum number of recent commits to analyze. Default: 10.

        Returns:
            dict: History and risk assessment with these keys:
                - "commits" (list): List of commit dicts:
                    - "hash" (str): Short 7-char commit hash
                    - "message" (str): Commit subject line
                    - "date" (str): Commit date (YYYY-MM-DD)
                    - "author" (str): Commit author name
                - "risk_level" (str): "high", "medium", or "low"
                - "bug_fix_count" (int): Count of commits containing bug-fix keywords
                - "total_commits" (int): Total commits returned
        """
        try:
            # Resolve the file path to absolute for git
            path = Path(file_path).resolve()

            if not path.exists():
                logger.debug("File not found for git analysis: %s", file_path)
                return SAFE_DEFAULT.copy()

            # ── Run git log command ───────────────────────────────────────────
            # --follow: track renames, --pretty: custom format with | separator
            result = subprocess.run(
                [
                    "git", "log",
                    "--follow",
                    f"-n {max_commits}",
                    "--pretty=format:%H|%s|%ad|%an",
                    "--date=short",
                    "--",
                    str(path),
                ],
                capture_output=True,
                text=True,
                timeout=15,   # Don't hang on slow git operations
                cwd=str(path.parent),   # Run from the file's directory
            )

            if result.returncode != 0:
                logger.debug(
                    "git log returned error for %s: %s",
                    file_path, result.stderr.strip()
                )
                return SAFE_DEFAULT.copy()

            raw_output = result.stdout.strip()
            if not raw_output:
                logger.debug("No git history found for %s", file_path)
                return SAFE_DEFAULT.copy()

            # ── Parse git log output ──────────────────────────────────────────
            commits = []
            bug_fix_count = 0

            for line in raw_output.splitlines():
                line = line.strip()
                if not line:
                    continue

                # Split on | separator (our custom format)
                parts = line.split("|", 3)
                if len(parts) < 4:
                    continue

                commit_hash, message, date, author = parts

                # Use only first 7 chars of hash (standard short format)
                short_hash = commit_hash[:7]

                # Check if this commit message contains bug-fix keywords
                message_lower = message.lower()
                is_bug_fix = any(keyword in message_lower for keyword in BUG_FIX_KEYWORDS)
                if is_bug_fix:
                    bug_fix_count += 1

                commits.append({
                    "hash": short_hash,
                    "message": message.strip(),
                    "date": date.strip(),
                    "author": author.strip(),
                })

            # ── Determine risk level ──────────────────────────────────────────
            if bug_fix_count >= RISK_HIGH_THRESHOLD:
                risk_level = "high"
            elif bug_fix_count >= RISK_MEDIUM_THRESHOLD:
                risk_level = "medium"
            else:
                risk_level = "low"

            logger.info(
                "Git history for %s: %d commits, %d bug-fixes → risk: %s",
                Path(file_path).name, len(commits), bug_fix_count, risk_level
            )

            return {
                "commits": commits,
                "risk_level": risk_level,
                "bug_fix_count": bug_fix_count,
                "total_commits": len(commits),
            }

        except FileNotFoundError:
            # git is not installed on this machine
            logger.debug("git command not found — skipping history analysis")
            return SAFE_DEFAULT.copy()

        except subprocess.TimeoutExpired:
            logger.debug("git log timed out for %s", file_path)
            return SAFE_DEFAULT.copy()

        except Exception as e:
            # Never crash the main pipeline — git analysis is optional
            logger.debug("Git analysis failed for %s: %s", file_path, str(e))
            return SAFE_DEFAULT.copy()

    def format_history_for_log(self, history: dict, file_name: str) -> str:
        """
        Format git history as a readable string for console logging.

        Args:
            history (dict): Result from get_file_history().
            file_name (str): Short file name for display.

        Returns:
            str: Formatted multi-line string.
        """
        if not history["commits"]:
            return f"  {file_name}: No git history found"

        risk_icon = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(
            history["risk_level"], "⚪"
        )

        lines = [
            f"  {file_name}: {risk_icon} {history['risk_level'].upper()} RISK "
            f"({history['bug_fix_count']} bug-fixes / {history['total_commits']} commits)"
        ]

        for commit in history["commits"][:5]:   # Show max 5 commits in log
            prefix = "  🐛" if any(k in commit["message"].lower() for k in BUG_FIX_KEYWORDS) else "  📝"
            lines.append(
                f"    {prefix} {commit['hash']} [{commit['date']}] {commit['message']}"
            )

        return "\n".join(lines)
