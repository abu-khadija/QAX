"""
core/ticket_repro.py — does the bug in this ticket still happen? Run it and say so, with proof
=============================================================================================
A bug ticket (Jira, Linear, a pasted report) usually has a title, "Steps to reproduce", "Expected" and
"Actual". QAX turns it into a case, runs it in a real browser on the app, and gives one of:

    REPRODUCED       the steps ran and the app did NOT do what is expected (or showed the reported symptom)
    NOT REPRODUCED   the steps ran and the app did what is expected — fixed, or it needs other conditions
    COULD NOT RUN    a step could not be done as written (QAX names it) — reword it, nothing is concluded
    NOT DECIDABLE    the steps ran, but neither "Expected" nor "Actual" says something QAX can check
                     (a quoted text, a page/URL) — add one, or let the AI writer turn it into a check

No AI is needed when the ticket names its check: a quoted text ('Saved', "Telephone must be …") or a
page/URL (/admin/login/). The plain-language steps run through the engine's step reader, which refuses
a step it cannot place instead of guessing. An optional `writer` (an AI case writer) may supply the
case for a ticket written loosely — its case still runs and is judged by the same deterministic rules.
Never raises.
"""

from __future__ import annotations

import re

_HEAD = re.compile(r"^\s*(?:#+\s*|\*\*)?\s*(steps(?: to reproduce)?|str|repro(?:duction)?(?: steps)?|how to reproduce|"
                   r"expected(?: result| behaviou?r)?|actual(?: result| behaviou?r)?|what happens|what should happen|"
                   r"environment|notes?)\s*(?:\*\*)?\s*:?\s*(.*)$", re.I)
_ITEM = re.compile(r"^\s*(?:\d+[.)]|[-*•]|step\s*\d+\s*[:.)-])\s*(.+)$", re.I)
_QUOTED = re.compile(r"['\"“”‘’`]([^'\"“”‘’`]{2,200})['\"“”‘’`]")
_PATH = re.compile(r"(?<![\w/])(/[A-Za-z0-9_\-./?=&%#]*[A-Za-z0-9_/])")
_NEG = re.compile(r"\b(not|no longer|never|doesn't|does not|isn't|is not|without)\b", re.I)


def parse(text: str) -> dict:
    """
    Read a ticket's parts.

    Returns:
        dict: {"title", "steps": [str], "expected": str, "actual": str}
    """
    lines = (text or "").replace("\r", "").split("\n")
    title, section = "", "body"
    parts = {"steps": [], "expected": [], "actual": [], "body": []}
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        m = _HEAD.match(line)
        if m:
            head = m.group(1).lower()
            section = ("steps" if head.startswith(("step", "str", "repro", "how")) else
                       "expected" if head.startswith(("expected", "what should")) else
                       "actual" if head.startswith(("actual", "what happens")) else "body")
            rest = m.group(2).strip()
            if rest:
                parts[section].append(rest)
            continue
        if not title and section == "body":
            title = re.sub(r"^\s*(?:#+|\*\*|summary:|title:)\s*", "", line, flags=re.I).strip("* ")
            continue
        if section == "steps":
            it = _ITEM.match(line)
            parts["steps"].append(it.group(1).strip() if it else line)
        else:
            parts[section].append(line)
    return {"title": title, "steps": [s for s in parts["steps"] if s],
            "expected": " ".join(parts["expected"]).strip(), "actual": " ".join(parts["actual"]).strip()}


def _check_from(sentence: str) -> "tuple[str, str]":
    """
    A checkable step from an Expected/Actual sentence, or ("", why not).
    A quoted text → 'Verify that "X" is shown' (or not shown); a path → 'Verify the URL contains /x'.
    """
    s = sentence or ""
    if not s.strip():
        return "", "empty"
    neg = bool(_NEG.search(s))
    q = _QUOTED.findall(s)
    if q:
        return (f'Verify that "{q[0]}" is {"not " if neg else ""}shown'), ""
    p = _PATH.findall(s)
    if p and re.search(r"\b(url|redirect\w*|page|open\w*|lands?|taken|go(?:es)?|navigat\w*|sent)\b", s, re.I):
        return f'Verify that the URL contains "{p[0]}"', ""
    return "", "it names no quoted text and no page path QAX can check"


_PRECOND = re.compile(r"^\s*(make sure|ensure|be|as an?)\b.*\b(not logged in|logged out|anonymous|signed out|"
                      r"not signed in|incognito|private window|no account)\b|^\s*(log ?out|sign ?out)\b(?!.*\bthen\b)",
                      re.I)


def build(ticket: dict, *, case_id: str = "REPRO") -> dict:
    """
    The case(s) to run for a parsed ticket.

    Returns:
        dict: {"expected_case": case | None, "symptom_case": case | None, "why_not": [str]}
    """
    why, notes, steps = [], [], []
    for st in ticket.get("steps") or []:
        if _PRECOND.search(st):
            notes.append(f"'{st}' — met: every case starts in a fresh, logged-out browser")
        else:
            steps.append(st)
    exp_check, why_e = _check_from(ticket.get("expected", ""))
    sym_check, why_s = _check_from(ticket.get("actual", ""))
    if not steps:
        why.append("the ticket has no steps to reproduce")
    if not exp_check:
        why.append(f"Expected: {why_e}")
    if not sym_check:
        why.append(f"Actual: {why_s}")

    def case(suffix, check):
        all_steps = steps + [check]
        return {"test_case_id": f"{case_id}-{suffix}", "title": f"{ticket.get('title') or 'Reported bug'} ({suffix.lower()})",
                "manual_steps": [{"step_number": i, "action": a} for i, a in enumerate(all_steps, 1)],
                "source": "ticket", "check_step": len(all_steps)}
    return {"expected_case": case("EXPECTED", exp_check) if steps and exp_check else None,
            "symptom_case": case("SYMPTOM", sym_check) if steps and sym_check else None, "why_not": why,
            "notes": notes}


def verdict(expected_result: "dict | None", symptom_result: "dict | None", built: dict) -> dict:
    """
    The decision from the run(s). A check only counts when every step before it ran (PASS).

    Returns:
        dict: {"verdict", "why", "step"?}
    """
    def before_check(r):
        """(all steps before the check passed, the check's status, the first step that did not run)."""
        steps = (r or {}).get("steps") or []
        if not steps:
            return False, None, None
        pre, chk = steps[:-1], steps[-1]
        bad = next((s for s in pre if s.get("status") not in ("PASS", "HEALED")), None)
        return bad is None, chk.get("status"), bad

    for r in (expected_result, symptom_result):
        ok, _chk, bad = before_check(r)
        if r is not None and not ok and bad is not None:
            return {"verdict": "COULD NOT RUN", "step": bad.get("step_number"),
                    "why": f"step {bad.get('step_number')} could not be done as written — "
                           f"{str(bad.get('actual_result') or bad.get('error') or '')[:200]}"}
    e_ok, e_chk, _ = before_check(expected_result)
    s_ok, s_chk, _ = before_check(symptom_result)
    if expected_result is not None and e_ok:
        if e_chk == "FAIL":
            extra = " — and the reported symptom is there" if (symptom_result is not None and s_chk == "PASS") else ""
            return {"verdict": "REPRODUCED", "why": "the steps ran and the expected result did not hold" + extra}
        if e_chk == "PASS":
            return {"verdict": "NOT REPRODUCED", "why": "the steps ran and the app did what the ticket expects"}
    if symptom_result is not None and s_ok:
        if s_chk == "PASS":
            return {"verdict": "REPRODUCED", "why": "the steps ran and the reported symptom is on the page"}
        if s_chk == "FAIL":
            return {"verdict": "NOT REPRODUCED", "why": "the steps ran and the reported symptom did not appear"}
    if expected_result is None and symptom_result is None:
        return {"verdict": "NOT DECIDABLE", "why": "; ".join(built.get("why_not") or ["nothing to check"])
                + " — quote the text the app should (or should not) show, give the page it should open, or turn on the AI writer (--ai-writer)"}
    return {"verdict": "COULD NOT RUN", "why": "the check itself could not be evaluated"}


_WRITER_SYSTEM = """You turn ONE bug ticket into ONE browser test case that reproduces it. Reply with a single JSON object and nothing else:
{"title": "...", "automation_steps": [ {"step_number": 1, "playwright_action": "navigate", "selector_key": "page", "value": "/login"}, ... ]}

Rules:
- Follow the ticket's own steps, in order. Every case starts in a fresh, signed-out browser: if the steps need a signed-in user,
  the FIRST steps navigate to the login page, fill the credentials the ticket (or the context) gives, and click the login button.
  Never put a password in the case when the ticket does not give one: use {{env:QAX_VAR_PASSWORD}}.
- One action per step. playwright_action is one of: navigate, click, fill, select, hover, wait, assert.
  navigate: selector_key "page", value = the path. fill: selector_key + value. click: selector_key. Do NOT put an assertion on a click, fill or navigate step.
- Use only selector_key names from the OBSERVED UI SURFACE below, plus "page". Do not invent pages or elements.
- The LAST step, and only the last, is the check: playwright_action "assert" with assertion_type "text" (or "url") and the assertion_value
  that states what SHOULD happen according to the ticket's Expected result, never the bug itself. Use selector_key "page" to look for a text anywhere on the page.
- If the ticket is too vague to write a check, reply {"title": "", "automation_steps": []}."""

_WRITER_ACTIONS = {"navigate", "click", "fill", "select", "hover", "wait", "assert"}


def _case_from_ai(raw: str) -> "dict | None":
    """The case in the AI's reply, or None when it is not usable: a JSON object, known actions, a last `assert` with a value."""
    import json
    text = (raw or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    i, j = text.find("{"), text.rfind("}")
    if i < 0 or j <= i:
        return None
    try:
        case = json.loads(text[i:j + 1])
    except ValueError:
        return None
    steps = case.get("automation_steps") if isinstance(case, dict) else None
    if not isinstance(steps, list) or len(steps) < 2 or not all(isinstance(x, dict) for x in steps):
        return None
    clean = []
    for n, x in enumerate(steps, 1):
        action = str(x.get("playwright_action") or "").strip().lower()
        if action not in _WRITER_ACTIONS or not str(x.get("selector_key") or "").strip():
            return None
        step = {"step_number": n, "playwright_action": action, "selector_key": str(x["selector_key"]).strip(),
                "value": str(x.get("value") or "")}
        if action == "assert":
            step.update(assertion_type=str(x.get("assertion_type") or "text").strip().lower(),
                        assertion_value=str(x.get("assertion_value") or ""))
        else:
            step.update(assertion_type="visible", assertion_value="")        # the schema's "no check" placeholder
        clean.append(step)
    last = clean[-1]
    if last["playwright_action"] != "assert" or not last["assertion_value"].strip():
        return None
    if any(c["playwright_action"] == "assert" for c in clean[:-1]):
        return None                                                          # the verdict reads the LAST step as the check
    return {"title": str(case.get("title") or "Reported bug (AI-written)")[:120], "automation_steps": clean,
            "manual_steps": [{"step_number": c["step_number"], "action": f"{c['playwright_action']} {c['selector_key']} {c['value']}".strip()}
                             for c in clean], "source": "ticket-ai", "check_step": len(clean)}


def ai_writer(base_url: str, project: str = "", *, client=None):
    """
    The optional AI case writer for a ticket written loosely: `writer(ticket_text) -> case | None`.

    Opt-in: the ticket text and the app's element names (from the saved map of `project`, when there is one) go to the
    configured AI provider. The case it returns runs in the same engine and is judged by the same deterministic rules as
    a case built from the ticket itself; a reply that is not a usable case is None (the verdict stays NOT DECIDABLE).

    Args:
        base_url (str): The app (kept for symmetry with reproduce; the map, not the app, is read here).
        project (str): QAX project whose saved app map names the pages and elements.
        client: Injected AI client for tests (default: the configured provider).
    """
    def write(ticket_text: str):
        write.last_error = ""
        try:
            from core.ai_provider import ai_configured, make_ai_client
            if client is None and not ai_configured():
                write.last_error = "no AI provider is configured (ANTHROPIC_API_KEY, or QAX_AI_PROVIDER=openai with QAX_AI_MODEL)"
                return None
            surface = ""
            if project:
                from core.app_mapper import load_map
                m = load_map(project)
                surface = m.as_context() if m else ""
            user = f"BUG TICKET:\n{(ticket_text or '')[:6000]}\n\n{surface or 'No saved map: use selector_key \"page\" and the paths in the ticket.'}"
            resp = (client or make_ai_client()).messages.create(
                model="", max_tokens=2500, system=_WRITER_SYSTEM, messages=[{"role": "user", "content": user}])
            case = _case_from_ai(resp.content[0].text)
            if case is None:
                write.last_error = "the AI's reply was not a usable case (it must end with one check and use only known actions and elements)"
            return case
        except Exception as exc:  # noqa: BLE001 - never raise: no case → NOT DECIDABLE, but say why
            from core.public_error import clean
            write.last_error = clean(str(exc)) or type(exc).__name__
            return None
    write.last_error = ""
    return write


def reproduce(ticket_text: str, base_url: str, project: str = "", *, writer=None, run=None, repo: str = "") -> dict:
    """
    Parse → build → run → decide. `run(cases) -> [results]` (default: the browser engine with
    APP_BASE_URL/DEFAULT_PROJECT set). `writer(ticket_text) -> case | None` is an optional AI case writer used
    only when the ticket's own steps/checks are not enough; its case is judged by the same rules.

    Returns:
        dict: {"verdict", "why", "ticket", "cases", "results", "fix_brief"?}
    """
    t = parse(ticket_text)
    built = build(t)
    cases = [c for c in (built["expected_case"], built["symptom_case"]) if c]
    used_writer = False
    if not cases and writer is not None:
        try:
            c = writer(ticket_text)
            if isinstance(c, dict) and (c.get("automation_steps") or c.get("manual_steps")):
                c = dict(c, test_case_id="REPRO-EXPECTED")
                cases, used_writer = [c], True
                built = dict(built, expected_case=c)
        except Exception:  # noqa: BLE001
            pass
    if not cases:
        v = verdict(None, None, built)
        out = dict(v, ticket=t, cases=[], results=[])
        err = getattr(writer, "last_error", "") if writer is not None else ""
        if err:
            out["notes"] = [f"The AI writer could not write a check: {err}"]
            out["why"] = v["why"] + f" (the AI writer was asked and could not help: {err})"
        return out
    results = (run or _run_default(base_url, project))(cases)
    by = {str(r.get("test_case_id")): r for r in results or []}
    exp_r = by.get("REPRO-EXPECTED") if built.get("expected_case") else None
    sym_r = by.get("REPRO-SYMPTOM") if built.get("symptom_case") else None
    v = verdict(exp_r, sym_r, built)
    out = dict(v, ticket=t, cases=cases, results=results, ai_written=used_writer, notes=list(built.get("notes", [])))
    if used_writer:
        # The AI chose the case, and maybe the wording it looks for. A wording the ticket never quoted is a guess:
        # a fixed app can then look REPRODUCED. Say so next to the verdict instead of hiding it.
        text = str(cases[0]["automation_steps"][-1].get("assertion_value") or "")
        grounded = bool(text) and text.lower() in (ticket_text or "").lower()
        out["ai_check_text"], out["ai_check_in_ticket"] = text, grounded
        out["notes"].append(f'The check was written by the AI: it looks for "{text}"'
                            + ("" if grounded else " — a wording the ticket does not quote, so it is the AI's own guess"))
        if not grounded and v["verdict"] == "REPRODUCED":
            out["why"] = v["why"] + " (but the text it looked for is the AI's guess: confirm the wording before trusting this)"
    if v["verdict"] == "REPRODUCED":
        from core.fix_brief import build as brief
        r = exp_r or sym_r
        out["fix_brief"] = brief(dict(r, cause="app", cause_why="reproduced from the ticket"),
                                 next((c for c in cases if c["test_case_id"] == r.get("test_case_id")), None),
                                 base_url=base_url, project=project, repo=repo)
    return out


def _run_default(base_url: str, project: str):
    """Run cases in the browser engine with the app and project set (restored afterwards)."""
    def run(cases):
        import os
        keys = ("APP_BASE_URL", "DEFAULT_PROJECT", "QAX_AI_HEAL", "AUTO_BUG_REPORT")
        saved = {k: os.environ.get(k) for k in keys}
        try:
            os.environ.update(APP_BASE_URL=base_url, QAX_AI_HEAL="false", AUTO_BUG_REPORT="false")
            if project:
                os.environ["DEFAULT_PROJECT"] = project
            from services.execution_service import ExecutionService
            res = ExecutionService().execute(cases, mode="ui", headless=True)
            return (res.raw or {}).get("results", [])
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
    return run
