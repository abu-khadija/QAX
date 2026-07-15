"""
core/input_adapters/pr_adapter.py — GitHub PR Input Adapter
============================================================
Fetches changed files from a GitHub Pull Request via the GitHub API,
then returns their content for test case generation.

Only modified/added files are returned. Deleted files are skipped.
Filtered by extension when --ext is provided.

Fallback: if GitHub credentials are missing or the API call fails,
automatically falls back to `git diff HEAD~1 HEAD` locally.

Required env vars (for live mode):
    GITHUB_TOKEN — Personal access token with repo read access
    GITHUB_REPO  — Repository in "owner/repo" format

Usage:
    adapter = PRInputAdapter()
    items   = adapter.fetch("142", ext=".cs")
    # Returns changed .cs files from PR #142
"""

import logging
import os

from core.input_adapters.base import BaseInputAdapter

logger = logging.getLogger(__name__)


class PRInputAdapter(BaseInputAdapter):
    """
    Input adapter that fetches modified files from a GitHub Pull Request.

    Falls back to local git diff when GitHub credentials are not set or
    the API call fails. Never raises — returns [] on complete failure.
    """

    name = "pr"

    def validate(self) -> tuple[bool, str]:
        """
        Check that GitHub credentials are present in the environment.

        Returns:
            tuple[bool, str]: (True, "") if GITHUB_TOKEN and GITHUB_REPO are set,
                              (False, "Missing: ...") otherwise.
        """
        required = ["GITHUB_TOKEN", "GITHUB_REPO"]
        missing  = [k for k in required if not os.getenv(k)]
        if missing:
            return False, f"Missing env vars: {', '.join(missing)}"
        return True, ""

    def fetch(self, source: str, ext: str | None = None, **kwargs) -> list[dict]:
        """
        Fetch changed files from a GitHub PR (with local git diff fallback).

        Args:
            source (str): Pull request number as a string, e.g. "142".
            ext (str | None): Optional extension filter, e.g. ".cs".
            **kwargs: Unused additional options.

        Returns:
            list[dict]: One dict per changed file, or [] on complete failure.
        """
        if not source:
            logger.warning("QAX PRInputAdapter: no PR number provided")
            return []

        valid, err = self.validate()
        if not valid:
            logger.info(
                "QAX PRInputAdapter: GitHub not configured (%s) — using git fallback", err
            )
            print(f"  [QAX] GitHub not configured: {err} — falling back to git diff")
            return self._try_local_git(source, ext)

        return self._fetch_github(source, ext)

    # ── GitHub API ─────────────────────────────────────────────────────────────

    def _fetch_github(self, pr_number: str, ext: str | None) -> list[dict]:
        """
        Fetch PR files via the GitHub REST API v3.

        Args:
            pr_number (str): Pull request number.
            ext (str | None): Extension filter.

        Returns:
            list[dict]: Changed files with their content, or [] on failure.
        """
        try:
            import requests
            import base64
        except ImportError:
            logger.error("QAX PRInputAdapter: requests not installed")
            return self._try_local_git(pr_number, ext)

        token   = os.getenv("GITHUB_TOKEN", "")
        repo    = os.getenv("GITHUB_REPO", "")
        headers = {
            "Authorization": f"token {token}",
            "Accept":        "application/vnd.github.v3+json",
        }
        base = "https://api.github.com"

        try:
            url  = f"{base}/repos/{repo}/pulls/{pr_number}/files"
            resp = requests.get(url, headers=headers, timeout=15)
            if not resp.ok:
                logger.warning(
                    "QAX PRInputAdapter: GitHub API %s — falling back", resp.status_code
                )
                return self._try_local_git(pr_number, ext)

            pr_files = resp.json()
        except Exception as exc:
            logger.warning("QAX PRInputAdapter: GitHub API error — %s — falling back", exc)
            print(f"  [QAX] GitHub error: {exc}")
            return self._try_local_git(pr_number, ext)

        results: list[dict] = []
        for f in pr_files:
            filename = f.get("filename", "")
            status   = f.get("status", "")

            if status == "removed":
                continue
            if ext and not filename.endswith(ext):
                continue

            content = self._fetch_file_content(
                f.get("contents_url", ""), headers, base64
            )
            if content is None:
                # Use patch as fallback if direct content fails
                content = f.get("patch", "")

            results.append({
                "file_path":   filename,
                "content":     content,
                "file_name":   os.path.basename(filename),
                "source_type": self.name,
                "metadata": {
                    "pr_number": pr_number,
                    "repo":      repo,
                    "additions": f.get("additions", 0),
                    "deletions": f.get("deletions", 0),
                    "status":    status,
                },
            })

        print(f"  PR #{pr_number}: {len(results)} changed file(s)")
        logger.info(
            "QAX PRInputAdapter: PR #%s — %d file(s)", pr_number, len(results)
        )
        return results

    def _fetch_file_content(
        self, contents_url: str, headers: dict, base64_mod
    ) -> str | None:
        """
        Download and base64-decode file content from the GitHub contents API.

        Args:
            contents_url (str): GitHub contents API URL for the file.
            headers (dict): Auth headers.
            base64_mod: The base64 module (already imported by caller).

        Returns:
            str | None: Decoded file content, or None on failure.
        """
        if not contents_url:
            return None
        try:
            import requests
            cr  = requests.get(contents_url, headers=headers, timeout=15)
            if cr.ok:
                raw = cr.json().get("content", "")
                # GitHub base64-encodes with newlines — strip before decode
                return base64_mod.b64decode(
                    raw.replace("\n", "")
                ).decode("utf-8", errors="ignore")
        except Exception as exc:
            logger.debug("QAX PRInputAdapter: content fetch failed — %s", exc)
        return None

    # ── Local git fallback ─────────────────────────────────────────────────────

    def _try_local_git(self, pr_or_branch: str, ext: str | None) -> list[dict]:
        """
        Fallback: use `git diff HEAD~1 HEAD` to find locally changed files.

        Args:
            pr_or_branch (str): PR number or branch name (used for logging only).
            ext (str | None): Optional extension filter.

        Returns:
            list[dict]: Changed local files, or [] if git is unavailable.
        """
        import subprocess
        from pathlib import Path

        try:
            result = subprocess.run(
                ["git", "diff", "--name-only", "HEAD~1", "HEAD"],
                capture_output=True,
                text=True,
                timeout=15,
            )
            file_list = [f for f in result.stdout.strip().split("\n") if f]
        except Exception as exc:
            logger.warning("QAX PRInputAdapter: git diff failed — %s", exc)
            return []

        results: list[dict] = []
        for fpath in file_list:
            if ext and not fpath.endswith(ext):
                continue
            if not os.path.exists(fpath):
                continue
            try:
                content = Path(fpath).read_text(encoding="utf-8", errors="ignore")
                results.append({
                    "file_path":   fpath,
                    "content":     content,
                    "file_name":   os.path.basename(fpath),
                    "source_type": "git_diff",
                    "metadata":    {"from_git": True, "ref": pr_or_branch},
                })
            except Exception as exc:
                logger.debug("QAX PRInputAdapter: skipping %s — %s", fpath, exc)

        print(f"  Git diff: {len(results)} changed file(s)")
        logger.info("QAX PRInputAdapter: git diff — %d file(s)", len(results))
        return results
