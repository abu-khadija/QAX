"""
code_reader.py — Source Code File Reader
==========================================
Reads source code files from the filesystem, extracts metadata,
detects the likely EXAMPLE module from the folder path, and provides
utilities to split large files and extract individual functions.
"""

import logging
import re
from pathlib import Path

# ── Logging setup ─────────────────────────────────────────────────────────────
logger = logging.getLogger(__name__)

# ── Module detection: map folder name keywords → module names (a generic default) ──
# Extend this dict with your application's folder naming patterns
MODULE_FOLDER_MAP = {
    "incident": "Incident",
    "incidents": "Incident",
    "audit": "Audit",
    "audits": "Audit",
    "investigation": "Investigation",
    "investigations": "Investigation",
    "capa": "CAPA",
    "corrective": "CAPA",
    "preventive": "CAPA",
    "training": "Training",
    "competency": "Training",
    "document": "Document Control",
    "documents": "Document Control",
    "doc_control": "Document Control",
    "risk": "Risk Assessment",
    "risks": "Risk Assessment",
    "admin": "Administration",
    "administration": "Administration",
    "dashboard": "Dashboard",
    "report": "Reports",
    "reports": "Reports",
}

# ── File extensions that are worth analyzing ──────────────────────────────────
ANALYZABLE_EXTENSIONS = {
    ".py", ".js", ".ts", ".jsx", ".tsx",
    ".java", ".cs", ".php", ".rb", ".go",
    ".vue", ".svelte"
}

# ── Directories that never hold the project's own source (vendored/build/tooling) ─
SKIP_DIRS = {".git", "node_modules", "venv", ".venv", ".venv-exec", "__pycache__",
             "dist", "build", ".next", ".pytest_cache", "site-packages", "migrations",
             "vendor", "target", "bin", "obj", ".gradle", ".idea",
             # generated/collected front-end assets (e.g. Django `collectstatic`)
             "staticfiles", "bower_components", "jspm_packages", "coverage", "htmlcov",
             ".nuxt", ".svelte-kit"}

# Generated files that are never the project's own source, whatever their extension.
_GENERATED_SUFFIXES = (".min.js", ".min.css", ".bundle.js", ".chunk.js", ".map")


def is_generated_file(name: str) -> bool:
    """True for minified/bundled/source-map files (not worth analyzing or counting)."""
    return str(name).lower().endswith(_GENERATED_SUFFIXES)


def detect_dominant_extension(folder: str) -> str | None:
    """
    The analyzable source extension with the most files under `folder` — QAX's
    language auto-detection for folder mode when no --ext is given. Vendored/build
    dirs (SKIP_DIRS) and hidden dirs are not counted, so node_modules can't make a
    Python repo look like JavaScript. Ties break alphabetically (deterministic).
    Returns None when the folder holds no analyzable source.
    """
    import os
    from collections import Counter
    counts: Counter = Counter()
    for root, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
        for f in files:
            ext = os.path.splitext(f)[1].lower()
            if ext in ANALYZABLE_EXTENSIONS and not is_generated_file(f):
                counts[ext] += 1
    if not counts:
        return None
    return max(sorted(counts), key=lambda e: counts[e])


# ── Patterns that indicate a file is NOT testable (config/types only) ─────────
NON_TESTABLE_PATTERNS = [
    r"^\s*[A-Z_]+ = .+$",          # All-caps constants only
    r"^(import|from|require)\s+",   # Pure import file
    r"interface\s+\w+\s*\{",        # TypeScript interface-only files
    r"type\s+\w+\s*=",              # TypeScript type alias
]


# ─────────────────────────────────────────────────────────────────────────────
# File Reader
# ─────────────────────────────────────────────────────────────────────────────

def read_file(file_path: str) -> dict:
    """
    Read a source code file and return its content with metadata.

    Detects the EXAMPLE module from the folder structure automatically
    so you don't have to specify it manually every time.

    Args:
        file_path (str): Absolute or relative path to the source code file.

    Returns:
        dict: File metadata with these keys:
            - "content" (str): Full file content as a string
            - "file_path" (str): The path as provided (for use in test cases)
            - "file_name" (str): Filename only (e.g., "IncidentForm.js")
            - "extension" (str): File extension (e.g., ".js")
            - "size_lines" (int): Number of lines in the file
            - "module_hint" (str | None): Guessed EXAMPLE module from folder name

    Raises:
        FileNotFoundError: If the file does not exist at the given path.
        UnicodeDecodeError: If the file cannot be decoded as UTF-8.
    """
    path = Path(file_path)

    if not path.exists():
        raise FileNotFoundError(f"Source file not found: {file_path}")

    if not path.is_file():
        raise ValueError(f"Path is not a file: {file_path}")

    # Read file content (try UTF-8, fallback to latin-1 for legacy files)
    try:
        content = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        logger.warning("UTF-8 decode failed for %s, falling back to latin-1", file_path)
        content = path.read_text(encoding="latin-1")

    # Count lines
    lines = content.splitlines()
    size_lines = len(lines)

    # Guess the EXAMPLE module from the folder path
    module_hint = _detect_module_from_path(str(path))

    logger.debug("Read file: %s (%d lines, module_hint=%s)", path.name, size_lines, module_hint)

    return {
        "content": content,
        "file_path": str(file_path),   # Keep original path for traceability
        "file_name": path.name,
        "extension": path.suffix.lower(),
        "size_lines": size_lines,
        "module_hint": module_hint,
    }


# ── Keywords that mark natural code boundaries (split BEFORE these lines) ─────
BOUNDARY_KEYWORDS = [
    "public ", "private ", "protected ", "internal ",
    "void ", "async ", "function ", "def ", "class ",
    "static ", "override ", "virtual ", "abstract ",
]

# ── Overlap and minimum chunk settings ────────────────────────────────────────
CHUNK_OVERLAP_LINES = 30    # Lines of previous chunk included as context
MIN_CHUNK_LINES = 50        # Merge final chunk if smaller than this


# ─────────────────────────────────────────────────────────────────────────────
# Large File Chunker
# ─────────────────────────────────────────────────────────────────────────────

def chunk_large_file(
    content: str,
    max_lines: int = 200,
    file_name: str = "unknown",
) -> list[dict]:
    """
    Split a large file into overlapping chunks for Claude analysis.

    Improvements over naive line-count splitting:
      1. OVERLAP: Each chunk includes the last 30 lines of the previous chunk
         as context so Claude understands code that spans a boundary.
      2. MINIMUM SIZE: Chunks smaller than 50 lines are merged into the
         previous chunk to avoid tiny, unanalyzable fragments.
      3. SMART SPLIT: Splits at natural code boundaries (public/private/
         function/class keywords) rather than blank lines alone.
      4. CONTEXT HEADER: Each chunk includes metadata so Claude knows
         exactly what part of the file it is analyzing.

    Args:
        content (str): Full file content to split.
        max_lines (int): Maximum NEW lines per chunk (excluding overlap). Default: 200.
        file_name (str): File name for the context header. Default: "unknown".

    Returns:
        list[dict]: List of chunk dicts, each with:
            - "content" (str): Chunk code with context header prepended
            - "start_line" (int): First line number of this chunk (1-based)
            - "end_line" (int): Last line number of this chunk (1-based)
            - "last_method" (str): Name of last method/function seen in chunk
            - "chunk_index" (int): 1-based chunk number
            - "total_chunks" (int): Total chunk count (filled after all chunks built)
    """
    lines = content.splitlines()
    total_lines = len(lines)

    # If file fits in one chunk, return it directly with a simple header
    if total_lines <= max_lines:
        header = (
            f"# This is chunk 1 of 1 from file: {file_name}\n"
            f"# Full file has {total_lines} total lines.\n"
        )
        return [{
            "content": header + content,
            "start_line": 1,
            "end_line": total_lines,
            "last_method": _find_last_method(lines),
            "chunk_index": 1,
            "total_chunks": 1,
        }]

    # ── Step 1: Determine raw split points ────────────────────────────────────
    split_points = _find_split_points(lines, max_lines)

    # ── Step 2: Build raw chunks from split points ─────────────────────────────
    raw_chunks = []
    for i, start in enumerate(split_points):
        end = split_points[i + 1] if i + 1 < len(split_points) else total_lines
        raw_chunks.append((start, end))

    # ── Step 3: Merge tiny final chunks ───────────────────────────────────────
    # If the last chunk is too small, merge it with the second-to-last
    merged_chunks = list(raw_chunks)
    if len(merged_chunks) >= 2:
        last_start, last_end = merged_chunks[-1]
        last_size = last_end - last_start
        if last_size < MIN_CHUNK_LINES:
            logger.debug(
                "Merging tiny final chunk (%d lines) with previous chunk", last_size
            )
            prev_start, _ = merged_chunks[-2]
            merged_chunks[-2] = (prev_start, last_end)
            merged_chunks.pop()

    # ── Step 4: Build final chunk dicts with overlap + context headers ─────────
    result_chunks = []
    for i, (start, end) in enumerate(merged_chunks):
        chunk_index = i + 1

        # Add overlap: include last CHUNK_OVERLAP_LINES of the previous chunk
        overlap_lines = []
        overlap_label = ""
        if i > 0:
            prev_start, prev_end = merged_chunks[i - 1]
            overlap_start = max(prev_start, prev_end - CHUNK_OVERLAP_LINES)
            overlap_lines = lines[overlap_start:prev_end]
            overlap_label = (
                f"# Context from previous chunk "
                f"(lines {overlap_start + 1}–{prev_end}):\n"
            )

        # Find the last method/function name in the previous chunk (for header)
        prev_last_method = ""
        if i > 0:
            prev_start, prev_end = merged_chunks[i - 1]
            prev_last_method = _find_last_method(lines[prev_start:prev_end])

        # Build context header so Claude knows its position in the file
        header_lines = [
            f"# This is chunk {chunk_index} of {len(merged_chunks)} from file: {file_name}",
            f"# Full file has {total_lines} total lines.",
            f"# This chunk covers lines {start + 1}–{end} of {total_lines}.",
        ]
        if i > 0 and prev_last_method:
            header_lines.append(
                f"# Previous chunk ended at line {merged_chunks[i-1][1]} "
                f"and covered: {prev_last_method}"
            )
        header = "\n".join(header_lines) + "\n\n"

        # Assemble: header + overlap section + main chunk content
        chunk_lines = []
        if overlap_lines:
            chunk_lines.append(overlap_label)
            chunk_lines.extend(overlap_lines)
            chunk_lines.append("")   # Blank separator line
            chunk_lines.append("# Main chunk content:")

        chunk_lines.extend(lines[start:end])
        chunk_content = header + "\n".join(chunk_lines)

        result_chunks.append({
            "content": chunk_content,
            "start_line": start + 1,
            "end_line": end,
            "last_method": _find_last_method(lines[start:end]),
            "chunk_index": chunk_index,
            "total_chunks": len(merged_chunks),
        })

    logger.debug(
        "Split '%s' into %d chunks (max_lines=%d, overlap=%d)",
        file_name, len(result_chunks), max_lines, CHUNK_OVERLAP_LINES
    )
    return result_chunks


def _find_split_points(lines: list[str], max_lines: int) -> list[int]:
    """
    Calculate the line indices where the file should be split into chunks.

    Tries to split at natural code boundaries (method/function declarations)
    within the last 20% of each chunk window. Falls back to blank lines,
    then hard line-count boundaries.

    Args:
        lines (list[str]): All lines of the file.
        max_lines (int): Target maximum lines per chunk.

    Returns:
        list[int]: List of start indices for each chunk (0-based).
    """
    total_lines = len(lines)
    split_points = [0]   # Always start from line 0
    current = 0

    while current < total_lines:
        target_end = current + max_lines
        if target_end >= total_lines:
            break   # Last chunk — no need to split further

        # ── Search window: last 20% of the chunk (where we prefer to split) ──
        search_start = current + int(max_lines * 0.8)
        search_end = min(target_end, total_lines)
        found_split = None

        # Priority 1: Find a boundary keyword line (method/function declaration)
        for i in range(search_start, search_end):
            stripped = lines[i].strip()
            if any(stripped.startswith(kw) for kw in BOUNDARY_KEYWORDS):
                found_split = i
                break

        # Priority 2: Find a blank line near the target
        if found_split is None:
            for i in range(target_end, search_start, -1):
                if i < total_lines and not lines[i].strip():
                    found_split = i + 1   # Split after the blank line
                    break

        # Priority 3: Hard split at max_lines
        if found_split is None:
            found_split = target_end

        split_points.append(found_split)
        current = found_split

    return split_points


def _find_last_method(lines: list[str]) -> str:
    """
    Find the name of the last method/function defined in a list of lines.

    Scans backwards through the lines looking for a boundary keyword
    and extracts the method/function name for use in chunk context headers.

    Args:
        lines (list[str]): Lines to scan (typically from a single chunk).

    Returns:
        str: Method/function name if found, empty string otherwise.
    """
    for line in reversed(lines):
        stripped = line.strip()
        for keyword in BOUNDARY_KEYWORDS:
            if keyword in stripped:
                # Extract name: find first word after the keyword sequence
                # Example: "public async Task<JsonResult> SaveQuestions("
                # → extract "SaveQuestions"
                try:
                    # Remove access modifiers and get to the actual name
                    after = stripped
                    for mod in ["public", "private", "protected", "internal",
                                "static", "async", "override", "virtual",
                                "abstract", "void", "Task", "IActionResult"]:
                        after = after.replace(mod + " ", " ").strip()
                    # First non-empty token is now the return type or name
                    # Extract what looks like a method name (before `(`)
                    name_part = after.split("(")[0].strip()
                    tokens = name_part.split()
                    if tokens:
                        # Last token is the method name (after return type)
                        return tokens[-1].strip("<>[]")
                except Exception:
                    pass
    return ""


# ─────────────────────────────────────────────────────────────────────────────
# Function Extractor
# ─────────────────────────────────────────────────────────────────────────────


# ─────────────────────────────────────────────────────────────────────────────
# Testability Checker
# ─────────────────────────────────────────────────────────────────────────────

def is_testable(content: str) -> bool:
    """
    Perform a quick check to determine if a file contains testable logic.

    Returns False for files that are purely config, type definitions,
    constants, or import-only files — these would produce low-quality tests.

    Args:
        content (str): The file content to check.

    Returns:
        bool: True if the file likely contains testable business logic,
              False if it should be skipped.
    """
    if not content or not content.strip():
        return False

    lines = [l.strip() for l in content.splitlines() if l.strip()]
    total_lines = len(lines)

    if total_lines < 5:
        # Too short to contain meaningful testable logic
        return False

    # ── Positive signals: these indicate testable code ────────────────────────
    testable_patterns = [
        r"\bdef\s+\w+",                         # Python function
        r"\bfunction\s+\w+",                    # JS/TS function
        r"\bclass\s+\w+",                       # Class definition
        r"\bif\b.+:",                           # Conditional logic
        r"\bif\s*\(",                           # Conditional logic (JS/Java)
        r"\breturn\b",                          # Return statement
        r"throw\s+new\s+\w+",                  # Exception throwing
        r"raise\s+\w+",                         # Python exception
        r"\bvalidat",                           # Validation logic
        r"\bpermission",                        # Permission check
        r"\brequired\b",                        # Required field logic
    ]

    positive_count = 0
    for line in lines:
        for pattern in testable_patterns:
            if re.search(pattern, line, re.IGNORECASE):
                positive_count += 1
                break  # Count each line only once

    # ── Negative signals: if >80% of lines are these, skip the file ──────────
    non_code_patterns = [
        r"^import\s+",
        r"^from\s+.+\s+import",
        r"^#",
        r"^//",
        r"^\*",
        r"^$",
    ]

    non_code_count = 0
    for line in lines:
        for pattern in non_code_patterns:
            if re.match(pattern, line):
                non_code_count += 1
                break

    # File is testable if it has meaningful code lines
    code_line_ratio = (total_lines - non_code_count) / total_lines if total_lines > 0 else 0
    has_logic = positive_count >= 2

    result = has_logic and code_line_ratio > 0.3
    logger.debug(
        "Testability check: %s (positive_signals=%d, code_ratio=%.2f)",
        "TESTABLE" if result else "SKIP", positive_count, code_line_ratio
    )
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Dependency Extractor
# ─────────────────────────────────────────────────────────────────────────────

def extract_dependencies(
    file_content: str,
    file_path: str,
    project_root: str | None = None,
) -> list[dict]:
    """
    Parse import/require statements and return the content of local dependency files.

    Only follows LOCAL imports (files within the project). Skips:
      - Third-party packages (node_modules, pip packages)
      - Standard library modules (os, sys, json, etc.)
      - Files that cannot be found on disk

    Gives Claude broader context by showing what the analyzed file depends on.
    Limited to 3 dependencies and 100 lines per file to control token usage.

    Args:
        file_content (str): The source code content to extract imports from.
        file_path (str): Absolute path to the source file (used to resolve relative imports).
        project_root (str | None): Root of the project for resolving absolute imports.
                                    If None, uses the file's parent directory.

    Returns:
        list[dict]: Up to 3 local dependency dicts, each with:
            - "file_path" (str): Resolved absolute path to the dependency
            - "file_name" (str): Filename only
            - "content" (str): File content (max 100 lines, truncated if larger)
            - "import_line" (str): The original import statement that referenced this file
    """
    try:
        source_path = Path(file_path).resolve()
        root = Path(project_root).resolve() if project_root else source_path.parent
        ext = source_path.suffix.lower()
        dependencies = []

        # ── Build regex patterns per language ────────────────────────────────
        import_patterns = []

        if ext == ".py":
            # Python: from .module import X  /  from module import X  / import module
            import_patterns = [
                re.compile(r'^from\s+(\.+[\w.]*)\s+import', re.MULTILINE),   # relative
                re.compile(r'^from\s+([\w./]+)\s+import', re.MULTILINE),      # absolute
            ]
        elif ext in (".js", ".ts", ".jsx", ".tsx"):
            # JS/TS: import X from './path'  /  require('./path')
            import_patterns = [
                re.compile(r'''(?:import|from)\s+['"](\./[^'"]+)['"]'''),
                re.compile(r'''(?:import|from)\s+['"](\.\./[^'"]+)['"]'''),
                re.compile(r'''require\s*\(\s*['"](\./[^'"]+)['"]\s*\)'''),
            ]
        elif ext in (".cs", ".java"):
            # C#/Java: using Namespace.Class  /  import com.package.Class
            import_patterns = [
                re.compile(r'^using\s+([\w.]+);', re.MULTILINE),
                re.compile(r'^import\s+([\w.]+);', re.MULTILINE),
            ]

        # ── Find all import matches ───────────────────────────────────────────
        found_imports = []
        for pattern in import_patterns:
            matches = pattern.findall(file_content)
            found_imports.extend(matches)

        # ── Resolve each import to an actual file ─────────────────────────────
        seen_paths = set()   # Avoid duplicate dependencies

        for import_str in found_imports:
            if len(dependencies) >= 3:
                break   # Max 3 dependencies

            dep_path = _resolve_import_to_path(import_str, source_path, root, ext)

            if dep_path is None:
                continue   # Could not resolve — skip

            dep_path_str = str(dep_path)
            if dep_path_str in seen_paths:
                continue   # Already included

            if not dep_path.exists():
                continue   # File not found on disk

            # ── Read the dependency file (max 100 lines) ──────────────────────
            try:
                content = dep_path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, PermissionError):
                try:
                    content = dep_path.read_text(encoding="latin-1")
                except Exception:
                    continue   # Cannot read — skip

            lines = content.splitlines()
            truncated = False
            if len(lines) > 100:
                lines = lines[:100]
                truncated = True
                content = "\n".join(lines) + "\n\n[... truncated at 100 lines ...]"

            seen_paths.add(dep_path_str)
            dependencies.append({
                "file_path": dep_path_str,
                "file_name": dep_path.name,
                "content": content,
                "import_line": import_str,
            })

            logger.debug(
                "Resolved dependency: %s (%d lines%s)",
                dep_path.name, len(lines), ", truncated" if truncated else ""
            )

        logger.debug(
            "Found %d local dependencies for %s",
            len(dependencies), source_path.name
        )
        return dependencies

    except Exception as e:
        # Never crash the main flow — dependencies are optional context
        logger.debug("extract_dependencies failed for %s: %s", file_path, str(e))
        return []


def _resolve_import_to_path(
    import_str: str,
    source_path: Path,
    project_root: Path,
    ext: str,
) -> Path | None:
    """
    Try to resolve an import string to an actual file path on disk.

    Args:
        import_str (str): The import string extracted from source (e.g., "./helpers/utils").
        source_path (Path): Absolute path of the file containing the import.
        project_root (Path): Root directory of the project.
        ext (str): File extension of the importing file (e.g., ".cs", ".js").

    Returns:
        Path | None: Resolved absolute path if found, None otherwise.
    """
    try:
        # ── Relative imports (./path or ../path) ──────────────────────────────
        if import_str.startswith("."):
            base = source_path.parent / import_str
            # Try with and without extension
            for candidate in [base, Path(str(base) + ext), Path(str(base) + "/index" + ext)]:
                candidate = candidate.resolve()
                if candidate.exists() and candidate.is_file():
                    return candidate
            return None

        # ── Python relative imports (.module or ..module) ─────────────────────
        if import_str.startswith("."):
            dots = len(import_str) - len(import_str.lstrip("."))
            module_part = import_str.lstrip(".")
            base = source_path.parent
            for _ in range(dots - 1):
                base = base.parent
            if module_part:
                candidate = (base / module_part.replace(".", "/")).with_suffix(".py")
                if candidate.exists():
                    return candidate
            return None

        # ── C# using / Java import: convert dots to path separators ──────────
        if ext in (".cs", ".java"):
            # Only follow if it looks like a local namespace (not System.*, etc.)
            skip_prefixes = (
                "System", "Microsoft", "java.", "javax.", "org.springframework",
                "android.", "com.google", "Newtonsoft", "log4net"
            )
            if any(import_str.startswith(p) for p in skip_prefixes):
                return None

            # Try to find matching file in project root
            parts = import_str.split(".")
            candidate = project_root.joinpath(*parts).with_suffix(ext)
            if candidate.exists():
                return candidate

            # Try just the last component (class name)
            class_name = parts[-1]
            matches = list(project_root.rglob(f"{class_name}{ext}"))
            if matches:
                return matches[0]

        return None

    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Private Helper: Module Detection
# ─────────────────────────────────────────────────────────────────────────────

def _detect_module_from_path(file_path: str) -> str | None:
    """
    Guess the EXAMPLE module name from the folder structure of the file path.

    Splits the path into parts and checks each folder name against
    the MODULE_FOLDER_MAP dictionary.

    Args:
        file_path (str): The file path to inspect.

    Returns:
        str | None: The module name (e.g., "Reports") if detected, None otherwise.
    """
    # Normalize separators and split into path parts
    normalized = file_path.replace("\\", "/").lower()
    parts = normalized.split("/")

    # Check each folder name for a module keyword match
    for part in parts:
        # Strip common suffixes that might be appended (e.g., "incidents-module")
        clean_part = re.sub(r"[-_]?(module|component|service|controller|view|util|helper)$", "", part)
        if clean_part in MODULE_FOLDER_MAP:
            return MODULE_FOLDER_MAP[clean_part]

    return None
