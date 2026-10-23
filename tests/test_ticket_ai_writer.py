"""
test_ticket_ai_writer.py — the AI case writer for a ticket written loosely
==========================================================================
reproduce() accepted a `writer` but nothing ever passed one, while the verdict text told the user "or let the AI
writer turn it into a check". It is now a real, opt-in writer (CLI --ai-writer, the web checkbox). What it returns is
only ever a case: it runs in the same engine, and an unusable reply is None (NOT DECIDABLE), never a guess at a verdict.
"""
import json
from types import SimpleNamespace

import pytest

from core import ticket_repro as tr

LOOSE = "Empty notes can be added\nWhen I press Add note without typing anything a blank item appears. It should tell me."
GOOD = {"title": "Empty note is rejected", "automation_steps": [
    {"step_number": 1, "playwright_action": "navigate", "selector_key": "page", "value": "/login"},
    {"step_number": 2, "playwright_action": "fill", "selector_key": "username", "value": "demo"},
    {"step_number": 3, "playwright_action": "click", "selector_key": "add_note_button"},
    {"step_number": 4, "playwright_action": "assert", "selector_key": "page", "assertion_type": "text",
     "assertion_value": "Note cannot be empty"}]}


class FakeClient:
    def __init__(self, reply):
        self.reply, self.sent = reply, []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kw):
        self.sent.append(kw)
        return SimpleNamespace(content=[SimpleNamespace(text=self.reply)])


def _case(reply):
    return tr.ai_writer("http://x", "", client=FakeClient(reply))(LOOSE)


def test_a_good_reply_becomes_a_case_that_ends_with_its_one_check():
    c = _case(json.dumps(GOOD))
    assert c["check_step"] == 4 and c["source"] == "ticket-ai"
    assert c["automation_steps"][-1]["playwright_action"] == "assert"
    assert all(s["assertion_type"] == "visible" and s["assertion_value"] == ""      # the schema's "no check" placeholder
               for s in c["automation_steps"][:-1])


def test_a_fenced_reply_is_read_too():
    assert _case("```json\n" + json.dumps(GOOD) + "\n```") is not None


@pytest.mark.parametrize("bad", [
    "I cannot do that",                                                     # not JSON
    json.dumps({"title": "", "automation_steps": []}),                      # the writer's own "too vague"
    json.dumps({"automation_steps": GOOD["automation_steps"][:1]}),         # one step only
    json.dumps({"automation_steps": [dict(GOOD["automation_steps"][0], playwright_action="eval"), GOOD["automation_steps"][-1]]}),
    json.dumps({"automation_steps": GOOD["automation_steps"][:-1]}),        # no final assert
    json.dumps({"automation_steps": [dict(GOOD["automation_steps"][-1], step_number=1)] + GOOD["automation_steps"]}),   # a check before the last step
    json.dumps({"automation_steps": GOOD["automation_steps"][:-1] + [dict(GOOD["automation_steps"][-1], assertion_value="")]}),
])
def test_an_unusable_reply_is_no_case(bad):
    assert _case(bad) is None


def test_the_ticket_and_the_elements_go_to_the_ai_only_when_asked(monkeypatch, tmp_path):
    fake = FakeClient(json.dumps(GOOD))
    monkeypatch.setattr("core.app_mapper.load_map", lambda project: SimpleNamespace(as_context=lambda: "PAGE /dashboard  add_note_button: #add-note-btn"))
    tr.ai_writer("http://x", "shop", client=fake)(LOOSE)
    sent = fake.sent[0]["messages"][0]["content"]
    assert "Empty notes can be added" in sent and "add_note_button" in sent


def test_without_an_ai_provider_the_writer_gives_nothing(monkeypatch):
    for k in ("ANTHROPIC_API_KEY", "QAX_AI_PROVIDER", "QAX_AI_MODEL"):
        monkeypatch.delenv(k, raising=False)
    assert tr.ai_writer("http://x", "")(LOOSE) is None


def _runner(status):
    def run(cases):
        steps = [{"step_number": s["step_number"], "status": "PASS"} for s in cases[0]["automation_steps"]]
        steps[-1]["status"] = status
        return [{"test_case_id": cases[0]["test_case_id"], "status": status, "steps": steps}]
    return run


def test_a_loose_ticket_without_a_writer_stays_not_decidable():
    assert tr.reproduce(LOOSE, "http://x")["verdict"] == "NOT DECIDABLE"


def test_with_the_writer_the_same_rules_decide_and_a_guessed_wording_is_flagged():
    w = tr.ai_writer("http://x", "", client=FakeClient(json.dumps(GOOD)))
    r = tr.reproduce(LOOSE, "http://x", writer=w, run=_runner("FAIL"))
    assert r["verdict"] == "REPRODUCED" and r["ai_written"] is True
    assert r["ai_check_in_ticket"] is False and "AI's guess" in r["why"]
    assert any("written by the AI" in n for n in r["notes"])
    ok = tr.reproduce(LOOSE, "http://x", writer=w, run=_runner("PASS"))
    assert ok["verdict"] == "NOT REPRODUCED"


def test_a_wording_the_ticket_quotes_is_not_called_a_guess():
    ticket = LOOSE + '\nExpected: "Note cannot be empty" is shown'
    r = tr.reproduce(ticket, "http://x", writer=None, run=_runner("FAIL"))          # no writer needed: the ticket names it
    assert r["verdict"] in ("REPRODUCED", "COULD NOT RUN", "NOT DECIDABLE")
    w = tr.ai_writer("http://x", "", client=FakeClient(json.dumps(GOOD)))
    text = "Steps:\n1. Press Add note\nExpected: nothing is added\nActual: a blank item\n(the app should say Note cannot be empty)"
    c = w(text)
    assert c is not None and "note cannot be empty" in text.lower()


def test_a_writer_that_raises_is_swallowed():
    class Boom:
        messages = SimpleNamespace(create=lambda **k: (_ for _ in ()).throw(RuntimeError("503")))
    assert tr.ai_writer("http://x", "", client=Boom())(LOOSE) is None


def test_a_failed_ai_call_is_reported_not_swallowed():
    """A busy free tier (429) made the first real run answer a bare NOT DECIDABLE: the user could not tell why."""
    class Boom:
        messages = SimpleNamespace(create=lambda **k: (_ for _ in ()).throw(RuntimeError("Error code: 429 - quota exceeded")))
    w = tr.ai_writer("http://x", "", client=Boom())
    r = tr.reproduce(LOOSE, "http://x", writer=w)
    assert r["verdict"] == "NOT DECIDABLE"
    assert "429" in r["why"] and any("could not write a check" in n for n in r["notes"])


def test_an_unusable_reply_and_a_missing_provider_are_named_too(monkeypatch):
    r = tr.reproduce(LOOSE, "http://x", writer=tr.ai_writer("http://x", "", client=FakeClient("sorry")))
    assert "not a usable case" in r["why"]
    for k in ("ANTHROPIC_API_KEY", "QAX_AI_PROVIDER", "QAX_AI_MODEL"):
        monkeypatch.delenv(k, raising=False)
    r = tr.reproduce(LOOSE, "http://x", writer=tr.ai_writer("http://x", ""))
    assert "no AI provider is configured" in r["why"]


def test_without_a_writer_the_message_is_unchanged():
    r = tr.reproduce(LOOSE, "http://x")
    assert "AI writer was asked" not in r["why"] and "notes" not in r or not r.get("notes")


class SeqClient:
    """Answers with each reply in turn; counts the calls."""
    def __init__(self, *replies):
        self.replies, self.calls = list(replies), 0
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kw):
        self.calls += 1
        return SimpleNamespace(content=[SimpleNamespace(text=self.replies.pop(0))])


def test_one_unusable_reply_gets_one_more_try():
    c = SeqClient("sorry", json.dumps(GOOD))
    assert tr.ai_writer("http://x", "", client=c)(LOOSE) is not None and c.calls == 2


def test_two_unusable_replies_stop_there_and_say_so():
    c = SeqClient("sorry", "still sorry")
    w = tr.ai_writer("http://x", "", client=c)
    assert w(LOOSE) is None and c.calls == 2 and "not a usable case" in w.last_error


def test_a_usable_first_reply_costs_one_call():
    c = SeqClient(json.dumps(GOOD))
    assert tr.ai_writer("http://x", "", client=c)(LOOSE) is not None and c.calls == 1
