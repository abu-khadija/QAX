"""
core/fix_brief.py — a failed case as a brief a coding agent can act on
======================================================================
When QAX proves a case fails because of the app, the developer (or their coding agent — Claude Code,
Codex, Cursor) needs the same things a good bug report gives a person, in a form it can act on:

  * what the user did, step by step, and what was observed at each step;
  * the failing step: expected vs observed;
  * the evidence: failed requests from the network log (HAR), page errors, screenshot / trace paths;
  * where to look: files of the app that declare the failing page's route (hints, not proof);
  * how to verify the fix: the exact case and the command that re-runs it;
  * the rule: fix the app, not the test — the case's expected result is the requirement.

Deterministic — no AI writes or judges anything here. Secrets: step values are already masked by the
engine; any QAX_VAR_* value that still appears anywhere is replaced. Never raises.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from urllib.parse import urlparse

_SKIP_DIRS = {".git", "node_modules", "target", "build", "dist", ".venv", "venv", "__pycache__", ".next", "vendor",
              "bin", "obj", "coverage", ".idea", ".gradle"}
_CODE_EXT = {".py", ".java", ".kt", ".js", ".jsx", ".ts", ".tsx", ".go", ".rb", ".php", ".cs", ".cshtml", ".html",
             ".htm", ".vue", ".svelte", ".jinja", ".j2", ".erb", ".twig"}


def _mask(text: str) -> str:
    """Replace any QAX_VAR_* secret value that reached this text."""
    out = str(text or "")
    for k, v in os.environ.items():
        if k.startswith("QAX_VAR_") and v and len(v) >= 3:
            out = out.replace(v, "•••")
    return out


def _failed_requests(har_path: str, limit: int = 8) -> list:
    """Requests that failed or answered >= 400, from a QAX network log (HAR 1.2)."""
    try:
        har = json.loads(Path(har_path).read_text(encoding="utf-8"))
        out = []
        for e in (har.get("log") or {}).get("entries") or []:
            st = (e.get("response") or {}).get("status", 0)
            if st >= 400 or st == 0:
                req = e.get("request") or {}
                out.append(f"{req.get('method', '?')} {urlparse(req.get('url', '')).path or req.get('url', '')} → "
                           + (str(st) if st else "no response"))
        return out[:limit]
    except Exception:  # noqa: BLE001
        return []


def where_to_look(repo: str, page_path: str, limit: int = 6) -> list:
    """
    Files of the app behind the failing page, best first — hints for where to start reading, never a
    claim about the cause:
      3 · declares the page's route (urls.py, @GetMapping("/tags"), a router entry);
      2 · a folder or file named exactly like the page (templates/tags/index.html, TagsController);
      1 · a path word in common with the page;
    tests, migrations, fixtures and e2e folders rank last (they describe the app; they are not it).
    """
    if not repo or not page_path or not Path(repo).is_dir():
        return []
    from core.change_selection import _norm_path, _page_words, _words
    target = _norm_path(page_path)
    segs = [x for x in target.strip("/").split("/") if x and x != "{id}"]
    pw = _page_words(target)
    scored, seen = [], 0
    root = Path(repo)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")]
        for fn in filenames:
            p = Path(dirpath) / fn
            if p.suffix.lower() not in _CODE_EXT:
                continue
            seen += 1
            if seen > 5000:
                break
            rel = str(p.relative_to(root))
            low = rel.lower()
            parts = re.split(r"[/\\._-]+", low)
            try:
                text = p.read_text(encoding="utf-8", errors="ignore")[:200_000]
            except Exception:  # noqa: BLE001
                continue
            literals = {_norm_path(a or b) for a, b in re.findall(
                r"""(?:["'`])(/[A-Za-z0-9_\-./{}<>:]*)(?:["'`])|(?:path|re_path|url)\(\s*r?["']\^?([A-Za-z0-9_\-./<>:{}]+)""", text)}
            score, why = 0, ""
            if target != "/" and target in literals:
                score, why = 3, f"declares the route {target}"
            elif segs and any(seg.lower() in parts for seg in segs):
                score, why = 2, f"named after the page ('{next(x for x in segs if x.lower() in parts)}')"
            elif pw and (pw & _words(rel.rsplit(".", 1)[0])):
                score, why = 1, f"about '{sorted(pw & _words(rel.rsplit('.', 1)[0]))[0]}'"
            if not score:
                continue
            stem = p.stem.lower()
            if score == 2 and (stem in ("index", "list") or stem in [x.lower() for x in segs]
                               or stem.startswith(segs[-1].lower() if segs else "\0")):
                score += 0.5          # the page's own view/template: /tags → tags/index.html, views/tags.py
            if re.search(r"(^|/)(tests?|spec|specs|e2e|tests_e2e|migrations|fixtures|__tests__)(/|$)|_test\.|\.spec\.|test_",
                         low):
                score -= 3
            scored.append((score, rel, why))
    scored.sort(key=lambda t: (-t[0], len(t[1])))
    return [f"{rel} — {why}" for _sc, rel, why in scored[:limit]]


def build(result: dict, case: "dict | None" = None, *, base_url: str = "", project: str = "", repo: str = "",
          cases_file: str = "") -> str:
    """
    The brief for one non-passing case, as Markdown.

    Args:
        result: the executor's result for the case (status, steps, cause, network_log, …).
        case: the case as it was run (its automation_steps and expected outcome), when available.
        base_url, project: where it ran (for the re-run command).
        repo: the app's checkout — enables "where to look".
        cases_file: the file holding the case (for the re-run command).
    """
    try:
        cid = str(result.get("test_case_id", "?"))
        title = str(result.get("title") or (case or {}).get("title") or "")
        steps = result.get("steps") or []
        bad = next((s for s in steps if s.get("status") not in ("PASS", "HEALED")), {})
        cause = result.get("cause") or "unclear"
        outcome = {"FAIL": "failed", "ERROR": "could not run", "SKIP": "did not run", "PASS": "passed"}.get(
            str(result.get("status")), str(result.get("status")))
        lines = [f"# Fix: {title} ({cid})", "",
                 f"QAX ran this case in a real browser against `{base_url or 'the app'}` and it **{outcome}** — "
                 f"cause: **{cause}** ({result.get('cause_why') or 'see the steps'}).", ""]
        if cause in ("test", "environment"):
            lines += ["> This is not a bug in the app: the test or the environment needs fixing first "
                      "(see the failing step). Do not change application code for it.", ""]
        lines += ["## What the user did", ""]
        for s in steps:
            mark = {"PASS": "✓", "HEALED": "✓ (healed)", "FAIL": "✗", "ERROR": "✗ (could not run)",
                    "SKIP": "– (not run)"}.get(s.get("status"), "?")
            lines.append(f"{s.get('step_number', '?')}. {mark} {s.get('action_type') or ''} "
                         f"{s.get('selector_used') or ''} — {str(s.get('actual_result') or '')[:200]}".rstrip())
        lines += ["", "## Expected vs observed", ""]
        if bad:
            spec = next((x for x in (case or {}).get("automation_steps") or []
                         if x.get("step_number") == bad.get("step_number")), {})
            expected = (f"{spec.get('assertion_type', '')} {spec.get('assertion_value', '')}".strip()
                        if spec else "") or "the step succeeds"
            lines += [f"- **Step {bad.get('step_number')}** expected: `{expected}`",
                      f"- observed: {str(bad.get('actual_result') or bad.get('error') or '')[:400]}"]
        else:
            lines.append(f"- {result.get('error') or 'no failing step recorded'}")
        lines += ["", "## Evidence", ""]
        reqs = _failed_requests(result.get("network_log") or "") if result.get("network_log") else []
        if reqs:
            lines += ["Failed requests (network log):", ""] + [f"- `{r}`" for r in reqs] + [""]
        errs = result.get("page_errors") or []
        if errs:
            lines += ["Page errors:", ""] + [f"- {e.get('kind', '')}: {str(e.get('text', ''))[:200]}" for e in errs[:6]] + [""]
        for label, key in (("Screenshot", "screenshot"), ("Network log (HAR, secrets removed)", "network_log"),
                           ("Playwright trace", "trace")):
            if result.get(key):
                lines.append(f"- {label}: `{result[key]}`")
        if bad.get("screenshot") and not result.get("screenshot"):
            lines.append(f"- Screenshot of the failing step: `{bad['screenshot']}`")
        page_url = ""
        for s in reversed(steps[: (steps.index(bad) + 1) if bad in steps else len(steps)]):
            m = re.search(r"https?://[^\s'\"]+", str(s.get("actual_result") or ""))
            if m:
                page_url = m.group(0)
                break
        hints = where_to_look(repo, urlparse(page_url).path if page_url else "")
        lines += ["", "## Where to look (hints, not proof)", ""]
        lines += [f"- {h}" for h in hints] if hints else \
            ["- " + ("no file of the app mentions this page's route" if repo else
                     "give QAX the app's checkout (`--repo`) to list the files behind this page")]
        rerun = (f"python main.py execute --file {cases_file or 'cases.json'}"
                 + (f" --project {project}" if project else "") + (f" --base-url {base_url}" if base_url else "")
                 + f" --only {cid} --yes")
        lines += ["", "## Verify the fix", "", "Re-run exactly this case (it must PASS, and nothing else may start failing):",
                  "", f"```bash\n{rerun}\n```"]
        if case:
            shown = {k: v for k, v in case.items() if k in ("test_case_id", "title", "automation_steps", "expected_outcome")}
            lines += ["", "<details><summary>The case as QAX ran it</summary>", "",
                      "```json", json.dumps(shown, indent=2)[:6000], "```", "</details>"]
        lines += ["", "## Rules", "",
                  "- Fix the application, not the test: the case's expected result is the requirement.",
                  "- If you believe the requirement itself is wrong, say so and stop — do not edit the case.",
                  "- Values shown as ••• are secrets QAX removed; they come from QAX_VAR_* variables where QAX runs."]
        return _mask("\n".join(lines)) + "\n"
    except Exception as exc:  # noqa: BLE001
        return f"# Fix brief unavailable\n\nQAX could not build it: {type(exc).__name__}\n"
