"""
services/appmodel_service.py — AppModelService (whole-system comprehension)
===========================================================================
Phase 0 of turning QAX from a per-file test generator into a system-level
reasoner. It reads the ENTIRE repository — routers, config, security, schemas,
models, and the frontend's API layer — and builds one structured AppModel that
every later phase (detectors, red-team, coverage) reasons over.

Why it exists: the worst bug QAX missed on TaskFlow — a hardcoded JWT secret that
lets anyone forge a token — lived in config.py, a file QAX never opened, because
generation was file-by-file. And the long-password 500 lived in a CONTRADICTION
between schemas.py (max 128) and security.py (bcrypt's 72-byte limit). Neither is
visible one file at a time. The AppModel makes the whole system visible at once.

One comprehension call over a bounded digest of the repo. Never prints, never
calls sys.exit(); failures are captured in AppModel.error.

Usage:
    from services.appmodel_service import AppModelService
    model = AppModelService(ctx).build("/path/to/repo")
    print(model.exposed_secrets)     # the hardcoded-secret finding
    print(model.as_context())        # inject into later phases
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path

from services.context import QAXContext
from services.progress import NullProgressReporter, ProgressReporter
from services.results import AppModel

logger = logging.getLogger(__name__)

# Directories that never contain first-party source worth modelling.
_SKIP_DIRS = {".git", "node_modules", "venv", ".venv", ".venv-exec", "__pycache__",
              "dist", "build", ".next", ".pytest_cache", "site-packages", "migrations",
              "vendor", "target", "bin", "obj", ".gradle", ".idea"}
# Extensions we read (backend logic + frontend contract/UI). QAX is language-generic:
# the model is built by an LLM that understands any of these, so the only limit is
# which files we hand it. Backend languages beyond Python/JS let QAX model Go, Java,
# Ruby, PHP, C#, Rust, Kotlin, Scala and Elixir apps too.
_EXTS = {".py", ".ts", ".tsx", ".js", ".jsx",
         ".go", ".rb", ".java", ".kt", ".php", ".cs", ".rs", ".scala", ".ex", ".exs"}

_MAX_FILE_CHARS = 6000      # per-file cap in the digest
_MAX_TOTAL_CHARS = 90_000   # whole-digest cap


class AppModelService:
    """Read a whole repo and produce a structured, cross-file AppModel."""

    def __init__(self, context: QAXContext | None = None) -> None:
        """
        Args:
            context (QAXContext | None): Config/credentials. Built from the
                environment when None.
        """
        self.context = context or QAXContext.from_env()

    def build(
        self,
        repo_path: str,
        *,
        max_files: int = 60,
        use_cache: bool = True,
        progress: ProgressReporter | None = None,
    ) -> AppModel:
        """
        Build a whole-system model of the repo at `repo_path`.

        Args:
            repo_path (str): Root of the repository to model.
            max_files (int): Safety cap on files read into the digest.
            use_cache (bool): Reuse a cached model when the source is unchanged —
                the App Model is the most expensive single call, so re-scans of the
                same code cost nothing.
            progress (ProgressReporter | None): Progress sink.

        Returns:
            AppModel: The structured model. Never raises at runtime.

        Raises:
            QAXConfigError: If the Anthropic API key is missing (only when a fresh
                model must be built).
        """
        prog = progress or NullProgressReporter()
        root = Path(repo_path)
        if not root.exists():
            return AppModel(root=str(repo_path), error=f"Path not found: {repo_path}")

        prog.emit("appmodel", "start", f"reading {root}")
        files = self._collect_files(root, max_files)
        if not files:
            return AppModel(root=str(root), error="No source files found to model.")

        digest = self._build_digest(root, files)

        # Cache on the digest — unchanged source ⇒ no model call.
        cache_key = hashlib.sha256(digest.encode("utf-8")).hexdigest()[:20]
        if use_cache:
            cached = self._load_cache(cache_key)
            if cached is not None:
                prog.emit("appmodel", "ok", f"cache hit ({len(files)} file(s))")
                cached.root = str(root)
                return cached

        self.context.require_anthropic_key()
        prog.emit("appmodel", "info", f"modelling {len(files)} file(s)")

        try:
            data = self._comprehend(digest)
        except Exception as exc:
            logger.error("AppModelService failed: %s", exc)
            prog.emit("appmodel", "error", str(exc))
            return AppModel(root=str(root), file_count=len(files), error=str(exc))

        model = AppModel(
            root=str(root),
            files=data.get("files") or [],
            endpoints=data.get("endpoints") or [],
            entities=data.get("entities") or [],
            config=data.get("config") or [],
            auth=data.get("auth") or {},
            contracts=data.get("contracts") or [],
            risks=data.get("risks") or [],
            file_count=len(files),
        )
        if use_cache:
            self._save_cache(cache_key, model)
        prog.emit("appmodel", "ok",
                  f"{len(model.endpoints)} endpoint(s), {len(model.exposed_secrets)} exposed secret(s)")
        return model

    # ── Cache ─────────────────────────────────────────────────────────────────

    _CACHE_DIR = Path("output/cache")

    @classmethod
    def _cache_path(cls, key: str) -> Path:
        return cls._CACHE_DIR / f"appmodel_{key}.json"

    @classmethod
    def _load_cache(cls, key: str) -> AppModel | None:
        """Load a cached AppModel by digest key, or None on any miss/error."""
        path = cls._cache_path(key)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return AppModel(
                root=data.get("root", ""), files=data.get("files") or [],
                endpoints=data.get("endpoints") or [], entities=data.get("entities") or [],
                config=data.get("config") or [], auth=data.get("auth") or {},
                contracts=data.get("contracts") or [], risks=data.get("risks") or [],
                file_count=data.get("file_count", 0))
        except Exception:
            return None

    @classmethod
    def _save_cache(cls, key: str, model: AppModel) -> None:
        """Persist an AppModel so an unchanged repo is not re-modelled."""
        try:
            cls._CACHE_DIR.mkdir(parents=True, exist_ok=True)
            cls._cache_path(key).write_text(
                json.dumps(model.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as exc:
            logger.debug("AppModel cache save failed (non-fatal): %s", exc)

    # ── Internals ─────────────────────────────────────────────────────────────

    @staticmethod
    def _collect_files(root: Path, max_files: int) -> list[Path]:
        """Walk the repo, keeping first-party source, skipping vendored dirs."""
        out: list[Path] = []
        for p in sorted(root.rglob("*")):
            if not p.is_file() or p.suffix not in _EXTS:
                continue
            if any(part in _SKIP_DIRS for part in p.parts):
                continue
            if p.name.endswith((".test.ts", ".test.tsx", ".spec.ts", ".d.ts")):
                continue
            out.append(p)
            if len(out) >= max_files:
                break
        # Bias toward the security-critical files first if we had to cap.
        priority = ("config", "security", "schema", "model", "deps", "auth", "main", "api")
        out.sort(key=lambda p: 0 if any(k in p.name.lower() for k in priority) else 1)
        return out

    @staticmethod
    def _build_digest(root: Path, files: list[Path]) -> str:
        """Concatenate the files (capped) into one labelled digest."""
        parts: list[str] = []
        total = 0
        for p in files:
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            if len(text) > _MAX_FILE_CHARS:
                text = text[:_MAX_FILE_CHARS] + "\n… (truncated)"
            rel = p.relative_to(root)
            block = f"\n===== FILE: {rel} =====\n{text}\n"
            if total + len(block) > _MAX_TOTAL_CHARS:
                break
            parts.append(block)
            total += len(block)
        return "".join(parts)

    def _comprehend(self, digest: str) -> dict:
        """One Claude call that returns the structured model as a JSON object."""
        import anthropic

        client = anthropic.Anthropic(api_key=self.context.anthropic_key)
        response = client.messages.create(
            model=self.context.model_heavy,
            max_tokens=6000,
            messages=[{"role": "user", "content": self._build_prompt(digest)}],
        )
        return self._parse_json_object(response.content[0].text)

    @staticmethod
    def _build_prompt(digest: str) -> str:
        """Build the whole-system modelling prompt."""
        return (
            "You are a security-minded software architect. Read this ENTIRE application "
            "(multiple files below) and produce ONE structured model of the whole system. "
            "Reason ACROSS files — the most important findings live between them.\n\n"
            "Extract, as a single JSON object with these keys:\n"
            "- \"files\": [{\"path\", \"role\"}]  role ∈ router|config|security|schema|model|"
            "frontend-api|frontend-component|other.\n"
            "- \"endpoints\": [{\"method\", \"path\", \"auth_required\"(bool), \"request_fields\"[], "
            "\"response_fields\"[], \"status_codes\"[], \"file\"}].\n"
            "- \"entities\": [{\"name\", \"fields\":[{\"name\",\"type\",\"constraints\"}], \"file\"}]  "
            "— capture validation constraints (e.g. min_length, max_length) exactly, and note when a "
            "field has NONE while a sibling field does.\n"
            "- \"config\": [{\"key\", \"default\", \"is_secret\"(bool), \"risk\"}]  — for EVERY setting; "
            "set is_secret=true for secrets/keys/passwords, and if such a secret has a hardcoded or "
            "placeholder default value, describe the risk (e.g. 'anyone can forge tokens').\n"
            "- \"auth\": {\"scheme\", \"token_type\", \"secret_source\", \"password_hash\"}.\n"
            "- \"contracts\": [{\"frontend_call\", \"backend_endpoint\", \"mismatch\"}]  — where the "
            "frontend API layer and the backend disagree (a field, a status, a limit); mismatch=\"\" if none.\n"
            "- \"risks\": [ up to 8 short strings ]  — cross-file concerns worth testing hardest, "
            "e.g. a schema that allows longer input than the hashing library accepts.\n\n"
            "APPLICATION:\n" + digest + "\n\n"
            "Return ONLY the JSON object. First character {, last character }. No markdown, no prose."
        )

    @staticmethod
    def _parse_json_object(text: str) -> dict:
        """Extract a JSON object from the response."""
        if not text:
            return {}
        cleaned = text.strip()
        candidates = [cleaned]
        match = re.search(r"\{[\s\S]*\}", cleaned)
        if match:
            candidates.append(match.group(0))
        for candidate in candidates:
            try:
                obj = json.loads(candidate)
                if isinstance(obj, dict):
                    return obj
            except json.JSONDecodeError:
                continue
        logger.warning("AppModelService: could not parse JSON — first 200 chars: %s", cleaned[:200])
        return {}
