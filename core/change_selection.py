"""
core/change_selection.py — run what a change can break
======================================================
Given the files a change touched (a git diff), QAX works out which pages of the app they
can affect and selects the cases that visit those pages — instead of running everything on
every pull request, and instead of guessing.

How a changed file maps to pages (deterministic, explainable — every selection has reasons):
  1. route literals in the file ("/owners/new", @GetMapping("/vets"), path("owners/<id>/"))
     matched against the app map's page templates ("/owners/{id}/edit");
  2. the file's own path words ("owner", "vet", "basket" — singular or plural) matched
     against page path segments;
  3. a SHARED file (layout/base template, global CSS/JS, settings, dependencies, build file,
     i18n messages used everywhere) → every page: run everything, and say why.
How a case maps to pages: its navigate steps, and the map sections its element names come from.

Safety rules: a change QAX cannot place on any page → run everything ("unknown impact");
cases marked smoke (test_type/tags) always run. Never raises.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

_SHARED = re.compile(r"(^|/)(layout|base|main|app|index|header|footer|nav|navbar|menu|_layout|fragments/layout|"
                     r"application|settings|config|urls|routes|router|messages)([._-][^/]*)?\.[a-z]+$|"
                     r"(^|/)(pom\.xml|build\.gradle|package\.json|package-lock\.json|requirements[^/]*\.txt|"
                     r"pyproject\.toml|go\.mod|Dockerfile|\.csproj|appsettings[^/]*\.json)$|\.(css|scss|less)$",
                     re.I)
_ROUTE = re.compile(r"""(?:["'`])(/[A-Za-z0-9_\-./{}<>:]*)(?:["'`])|(?:path|re_path|url)\(\s*r?["']\^?([A-Za-z0-9_\-./<>:{}]+)""")
_STOP = {"src", "main", "java", "org", "com", "app", "apps", "lib", "web", "static", "templates", "resources",
         "test", "tests", "spec", "views", "view", "controllers", "controller", "models", "model", "pages",
         "components", "component", "html", "py", "ts", "js", "cs", "cshtml", "jsx", "tsx", "springframework",
         "samples", "service", "services", "repository", "impl", "index", "form", "list", "detail", "details",
         "core", "api", "public", "private", "src", "infrastructure", "applicationcore", "petclinic", "system"}


def changed_files(repo: str, base: str, head: str = "") -> "tuple[list, str]":
    """Files changed between two git refs (head empty = the working tree). Returns (files, error)."""
    try:
        args = ["git", "-C", repo, "diff", "--name-only", base] + ([head] if head else [])
        r = subprocess.run(args, capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            return [], (r.stderr or "git diff failed").strip()[:200]
        return [x.strip() for x in r.stdout.splitlines() if x.strip()], ""
    except Exception as exc:  # noqa: BLE001
        return [], str(exc)[:200]


def _norm_path(p: str) -> str:
    """'/owners/{ownerId}/edit', '/owners/<int:id>/edit/', 'owners/:id' → '/owners/{id}/edit'."""
    p = "/" + str(p or "").strip().lstrip("^").rstrip("$").strip("/")
    p = re.sub(r"\{[^}]*\}|<[^>]*>|:[A-Za-z_]+|\d+", "{id}", p)
    return p.split("?")[0].split("#")[0] or "/"


def _words(text: str) -> set:
    """Path words, singular and plural ('owners' → {'owners', 'owner'})."""
    out = set()
    for w in re.findall(r"[a-z]{3,}", re.sub(r"([a-z])([A-Z])", r"\1 \2", str(text)).lower()):
        if w in _STOP:
            continue
        out.add(w)
        out.add(w[:-1] if w.endswith("s") else w + "s")
    return out


def _page_words(page: str) -> set:
    return _words(page.replace("{id}", " "))


def affected_pages(files: list, repo: str, pages: list) -> "tuple[dict, list, bool]":
    """
    Map changed files to app-map pages.

    Returns:
        ({page: [reasons]}, [unplaced files], shared: bool)
    """
    hit: dict = {}
    unplaced, shared = [], False
    tpl = {p: _norm_path(p.split("#")[-1] if "#/" in p else p) for p in pages}
    for f in files:
        if _SHARED.search(f):
            shared = True
            hit.setdefault("*", []).append(f"{f} is shared by every page")
            continue
        placed = False
        try:
            text = (Path(repo) / f).read_text(encoding="utf-8", errors="ignore")[:200_000] if repo else ""
        except Exception:  # noqa: BLE001
            text = ""
        routes = {_norm_path(a or b) for a, b in _ROUTE.findall(text)} if text else set()
        for page, t in tpl.items():
            if t in routes or any(r != "/" and (t == r or t.startswith(r.rstrip("/") + "/")) for r in routes):
                hit.setdefault(page, []).append(f"{f} declares the route {t}")
                placed = True
        fw = _words(f.rsplit(".", 1)[0])
        for page in pages:
            pw = _page_words(page)
            common = fw & pw
            if common:
                hit.setdefault(page, []).append(f"{f} is about '{sorted(common)[0]}'")
                placed = True
        if not placed:
            unplaced.append(f)
    return hit, unplaced, shared


def _case_pages(case: dict, pages: list, sections: dict) -> set:
    """The map pages a case visits: navigate targets and the sections its element names live in."""
    out = set()
    tpl = {p: _norm_path(p) for p in pages}
    for st in case.get("automation_steps") or []:
        act = str(st.get("playwright_action") or "").lower()
        if act in ("navigate", "goto", "open", "visit"):
            target = _norm_path(str(st.get("value") or "").split("#")[0] or "/")
            for page, t in tpl.items():
                if t == target or (target != "/" and t.startswith(target.rstrip("/") + "/")):
                    out.add(page)
        key = str(st.get("selector_key") or "")
        for page in sections.get(key, []):
            out.add(page)
    return out


def select(cases: list, files: list, repo: str, project_dir: "str | Path") -> dict:
    """
    Choose the cases a change can affect.

    Returns:
        dict: {"selected": [{"id", "reasons"}], "skipped": [{"id", "reason"}], "run_all": bool,
               "why": str, "affected_pages": {page: reasons}, "unplaced_files": [...]}
    """
    project_dir = Path(project_dir)
    try:
        amap = json.loads((project_dir / "app_map.json").read_text(encoding="utf-8"))
        pages = [p.get("path", "") for p in amap.get("pages") or [] if p.get("path")]
    except Exception:  # noqa: BLE001
        pages = []
    sections: dict = {}
    try:
        sel = json.loads((project_dir / "selectors.json").read_text(encoding="utf-8"))
        slug = {re.sub(r"[^a-z0-9]+", "_", p.lower()).strip("_") or "home": p for p in pages}
        for sec, keys in sel.items():
            if isinstance(keys, dict) and sec in slug:
                for k in keys:
                    sections.setdefault(k, []).append(slug[sec])
    except Exception:  # noqa: BLE001
        pass
    ids = lambda c: str(c.get("test_case_id", "?"))  # noqa: E731
    everything = lambda why: {"selected": [{"id": ids(c), "reasons": [why]} for c in cases], "skipped": [],  # noqa: E731
                              "run_all": True, "why": why, "affected_pages": {}, "unplaced_files": []}
    if not files:
        return everything("no changed files given — running every case")
    if not pages:
        return everything("no app map for the project — QAX cannot place the change, running every case")
    hit, unplaced, shared = affected_pages(files, repo, pages)
    if shared:
        return dict(everything("a shared file changed (" + "; ".join(hit.get("*", [])[:3]) + ")"),
                    affected_pages=hit)
    if unplaced:
        return dict(everything("unknown impact: " + ", ".join(unplaced[:5]) + " could not be placed on any page"),
                    affected_pages=hit, unplaced_files=unplaced)
    selected, skipped = [], []
    for c in cases:
        smoke = "smoke" in (str(c.get("test_type", "")) + " " + " ".join(c.get("tags") or [])).lower()
        cp = _case_pages(c, pages, sections)
        why = [f"visits {p} — " + "; ".join(hit[p][:2]) for p in sorted(cp & set(hit))]
        if why or smoke:
            selected.append({"id": ids(c), "reasons": why or ["smoke case — always runs"]})
        elif not cp:
            selected.append({"id": ids(c), "reasons": ["QAX cannot tell which pages this case visits — it runs"]})
        else:
            skipped.append({"id": ids(c), "reason": "visits only " + ", ".join(sorted(cp)) + " — not affected"})
    return {"selected": selected, "skipped": skipped, "run_all": False,
            "why": f"{len(selected)} of {len(cases)} case(s) visit a page the change can affect",
            "affected_pages": hit, "unplaced_files": []}
