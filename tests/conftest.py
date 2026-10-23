"""
tests/conftest.py — the suite never reads the developer's real .env
===================================================================
Regression (2026-10-23): modules call load_dotenv() at import, so the tests ran with the
owner's real Jira / Xray / Anthropic settings. A test that forgot JIRA_CLOUD_ID passed on the
owner's Mac and failed on a clean Windows machine. Each test now sets what it needs.
This runs before any project module is imported.
"""

from __future__ import annotations

import os

import dotenv
import dotenv.main

_SECRET_PREFIXES = ("JIRA_", "XRAY_", "ANTHROPIC_", "OPENAI_", "QAX_VAR_")


def _no_dotenv(*_a, **_k):
    return False


dotenv.load_dotenv = _no_dotenv
dotenv.main.load_dotenv = _no_dotenv
dotenv.find_dotenv = lambda *_a, **_k: ""
dotenv.main.find_dotenv = lambda *_a, **_k: ""

for _name in [n for n in os.environ if n.startswith(_SECRET_PREFIXES)]:
    del os.environ[_name]
