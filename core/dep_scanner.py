"""
dep_scanner.py — dependency vulnerability & license scanner
===========================================================
A deterministic (zero-LLM) supply-chain angle for QAX: read a project's declared
dependencies, ask OSV.dev whether any pinned version has a known CVE, and flag
copyleft / non-commercial licenses that matter before a product ships commercially.

Two jobs in one:
  • security — known-vulnerable dependency versions (OSV.dev), and
  • licensing — GPL/AGPL/LGPL/non-commercial packages that can block commercial use.

Design rules (consistent with QAX):
  • Never raises — network/parse failures are captured in `errors`, never thrown.
  • Parsing is pure and offline-testable; the network calls take an injectable
    `session`, so tests feed canned responses and no live server is needed.
  • Only PINNED versions are checked against OSV (an unpinned range can't be
    matched to a specific advisory) — keeps false positives at zero.

Usage:
    from core.dep_scanner import scan_dependencies
    report = scan_dependencies("/path/to/repo")          # online by default
    for v in report["vulns"]:
        print(v["name"], v["version"], v["ids"])
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

OSV_BATCH_URL = "https://api.osv.dev/v1/querybatch"
PYPI_JSON_URL = "https://pypi.org/pypi/{name}/{version}/json"

# Licenses that can block or complicate commercial/closed-source distribution.
# Matched case-insensitively against a package's declared license + classifiers.
# GPL-family names are matched as PREFIXES (GPLv3, GPL-3.0, AGPLv3, LGPL-2.1 …),
# so no trailing word boundary; the spelled-out names keep theirs.
_COPYLEFT = re.compile(r"\b(?:AGPL|LGPL|GPL|GNU General Public|GNU Affero|"
                       r"Lesser General Public|Mozilla Public|\bMPL\b|EUPL|CDDL|"
                       r"SSPL|Server Side Public)", re.I)
_NONCOMMERCIAL = re.compile(r"non[- ]?commercial|CC[- ]BY[- ]NC|Prosperity|Commons Clause", re.I)


# ── parsing (pure) ────────────────────────────────────────────────────────────

def parse_requirements(text: str) -> list[dict]:
    """
    Parse a pip requirements.txt into [{name, version|None, ecosystem:'PyPI'}].

    Handles `name==1.2.3`, `name>=1.0`, extras (`name[extra]==1`), environment
    markers (`; python_version<'3.12'`), and inline comments; skips blank lines,
    comments, `-r`/`-e`/option lines, URLs and VCS installs. `version` is the exact
    pin for `==`/`===` and None otherwise (ranges aren't matched to advisories).
    """
    out: list[dict] = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        line = line.split("#", 1)[0].strip()          # drop inline comment
        line = line.split(";", 1)[0].strip()           # drop env marker
        if not line or "://" in line or "@" in line:   # URL / VCS / direct ref
            continue
        m = re.match(r"^([A-Za-z0-9._-]+)\s*(?:\[[^\]]*\])?\s*(===|==|>=|<=|~=|>|<|!=)?\s*([A-Za-z0-9._*+!-]+)?",
                     line)
        if not m:
            continue
        name, op, ver = m.group(1), m.group(2), m.group(3)
        pinned = ver if op in ("==", "===") and ver and "*" not in ver else None
        out.append({"name": name, "version": pinned, "ecosystem": "PyPI"})
    return out


def parse_package_json(text: str) -> list[dict]:
    """
    Parse a package.json's dependencies + devDependencies into
    [{name, version|None, ecosystem:'npm'}]. A caret/tilde/range version is left
    unpinned (None); only an exact version string is treated as a pin.
    """
    out: list[dict] = []
    try:
        data = json.loads(text or "{}")
    except Exception:
        return out
    for section in ("dependencies", "devDependencies"):
        for name, spec in (data.get(section) or {}).items():
            spec = str(spec).strip()
            pinned = spec if re.fullmatch(r"\d+\.\d+\.\d+[\w.+-]*", spec) else None
            out.append({"name": name, "version": pinned, "ecosystem": "npm"})
    return out


def collect_dependencies(repo_path: str) -> list[dict]:
    """Find and parse the dependency manifests under a repo (requirements*.txt, package.json)."""
    root = Path(repo_path)
    deps: list[dict] = []
    if not root.exists():
        return deps
    try:
        for req in sorted(root.glob("requirements*.txt")):
            deps.extend(parse_requirements(req.read_text(encoding="utf-8", errors="ignore")))
        pkg = root / "package.json"
        if pkg.exists():
            deps.extend(parse_package_json(pkg.read_text(encoding="utf-8", errors="ignore")))
    except Exception as exc:  # pragma: no cover - fs edge
        logger.warning("dep collect failed: %s", exc)
    # De-dupe on (name, ecosystem), preferring a pinned version if we saw one.
    merged: dict = {}
    for d in deps:
        key = (d["name"].lower(), d["ecosystem"])
        if key not in merged or (d["version"] and not merged[key]["version"]):
            merged[key] = d
    return list(merged.values())


# ── network (injectable, graceful) ────────────────────────────────────────────

def _session(session=None):
    if session is not None:
        return session
    import requests
    return requests.Session()


def check_vulnerabilities(deps: list[dict], *, session=None, errors: list | None = None) -> list[dict]:
    """
    Ask OSV.dev (batch) whether any PINNED dependency version has a known advisory.
    Returns [{name, version, ecosystem, ids:[...]}] for the vulnerable ones. Any
    network error is appended to `errors` and yields an empty result — never raises.
    """
    pinned = [d for d in deps if d.get("version")]
    if not pinned:
        return []
    sess = _session(session)
    queries = [{"package": {"name": d["name"], "ecosystem": d["ecosystem"]}, "version": d["version"]}
               for d in pinned]
    try:
        r = sess.post(OSV_BATCH_URL, json={"queries": queries}, timeout=30)
        results = (r.json() or {}).get("results", [])
    except Exception as exc:
        if errors is not None:
            errors.append(f"OSV query failed: {exc}")
        return []
    out: list[dict] = []
    for d, res in zip(pinned, results):
        vulns = (res or {}).get("vulns") or []
        if vulns:
            out.append({"name": d["name"], "version": d["version"], "ecosystem": d["ecosystem"],
                        "ids": [v.get("id") for v in vulns if v.get("id")]})
    return out


def check_licenses(deps: list[dict], *, session=None, errors: list | None = None) -> list[dict]:
    """
    Flag PyPI packages whose declared license/classifiers are copyleft or
    non-commercial. Returns [{name, version, license, kind}]. npm is skipped (no
    registry call here). Network errors are captured per-package, never raised.
    """
    sess = _session(session)
    out: list[dict] = []
    for d in deps:
        if d["ecosystem"] != "PyPI" or not d.get("version"):
            continue
        url = PYPI_JSON_URL.format(name=d["name"], version=d["version"])
        try:
            info = (sess.get(url, timeout=20).json() or {}).get("info", {}) or {}
        except Exception as exc:
            if errors is not None:
                errors.append(f"PyPI lookup failed for {d['name']}: {exc}")
            continue
        blob = " ".join([str(info.get("license") or "")] + list(info.get("classifiers") or []))
        kind = None
        if _NONCOMMERCIAL.search(blob):
            kind = "non-commercial"
        elif _COPYLEFT.search(blob):
            kind = "copyleft"
        if kind:
            lic = (info.get("license") or "").strip()
            if not lic:
                lic = next((c.split("::")[-1].strip() for c in info.get("classifiers", [])
                            if c.startswith("License")), "see classifiers")
            out.append({"name": d["name"], "version": d["version"], "license": lic[:80], "kind": kind})
    return out


# ── orchestration ─────────────────────────────────────────────────────────────

def scan_dependencies(repo_path: str, *, session=None, online: bool = True) -> dict:
    """
    Scan a repo's dependencies for known CVEs and license risks.

    Args:
        repo_path (str): Project root containing requirements*.txt / package.json.
        session: Optional injectable HTTP session (for tests); real requests by default.
        online (bool): When False, only parse manifests — no network, no vuln/license data.

    Returns:
        dict: {deps, vulns, license_flags, errors, summary} — never raises.
    """
    errors: list[str] = []
    deps = collect_dependencies(repo_path)
    vulns: list[dict] = []
    licenses: list[dict] = []
    if online and deps:
        vulns = check_vulnerabilities(deps, session=session, errors=errors)
        licenses = check_licenses(deps, session=session, errors=errors)
    pinned = sum(1 for d in deps if d.get("version"))
    return {
        "deps": deps,
        "vulns": vulns,
        "license_flags": licenses,
        "errors": errors,
        "summary": {
            "total": len(deps), "pinned": pinned,
            "vulnerable": len(vulns), "license_risks": len(licenses),
        },
    }


if __name__ == "__main__":  # pragma: no cover - manual CLI
    import sys
    report = scan_dependencies(sys.argv[1] if len(sys.argv) > 1 else ".")
    print(json.dumps(report, indent=2))
