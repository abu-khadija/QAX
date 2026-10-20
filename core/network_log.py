"""
core/network_log.py — what the browser sent and got while a case ran (HAR, secrets removed)
==========================================================================================
For a case that did not pass, QAX saves its network traffic as a HAR file (HAR 1.2 — opens in
Chrome DevTools, Firefox, Charles, Fiddler): every request with its method, URL, status, timing
and headers, and the start of the response body for failed API/page calls. It is the evidence
for "the button did nothing — what did the server say?".

Secrets never reach the file: Authorization, Cookie, Set-Cookie, API-key and CSRF headers are
replaced, any value of a QAX_VAR_* secret is replaced wherever it appears, request bodies are
not stored (they carry passwords). Files are owner-only (0600). Never raises.
"""

from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

_SECRET_HEADERS = re.compile(r"^(authorization|proxy-authorization|cookie|set-cookie|x-api-key|api-key|"
                             r"x-auth-token|x-csrf-token|x-csrftoken|x-xsrf-token|private-token)$", re.I)


def _secrets() -> list:
    """The values of QAX_VAR_* variables (the secrets cases may type), longest first."""
    vals = [v for k, v in os.environ.items() if k.startswith("QAX_VAR_") and len(v or "") >= 4]
    return sorted(set(vals), key=len, reverse=True)


def redact(text: str, secrets: "list | None" = None) -> str:
    """Replace every known secret value in text."""
    out = str(text or "")
    for v in (secrets if secrets is not None else _secrets()):
        out = out.replace(v, "[redacted]")
    return out


def to_har(entries: list) -> dict:
    """The recorded entries as a HAR 1.2 document, with headers and bodies redacted."""
    secrets = _secrets()
    hdrs = lambda h: [{"name": k, "value": "[redacted]" if _SECRET_HEADERS.match(k) else redact(v, secrets)}  # noqa: E731
                      for k, v in (h or {}).items()]
    out = []
    for e in entries:
        out.append({
            "startedDateTime": e.get("started") or datetime.now(timezone.utc).isoformat(),
            "time": round(float(e.get("ms") or 0), 1),
            "request": {"method": e.get("method", "GET"), "url": redact(e.get("url", ""), secrets),
                        "httpVersion": "HTTP/1.1", "headers": hdrs(e.get("req_headers")), "queryString": [],
                        "cookies": [], "headersSize": -1, "bodySize": -1,
                        "comment": "request body not stored (it can carry passwords)"},
            "response": {"status": int(e.get("status") or 0), "statusText": e.get("failure") or "",
                         "httpVersion": "HTTP/1.1", "headers": hdrs(e.get("resp_headers")), "cookies": [],
                         "content": {"size": -1, "mimeType": e.get("mime", ""),
                                     **({"text": redact(e["body"], secrets)} if e.get("body") else {})},
                         "redirectURL": "", "headersSize": -1, "bodySize": -1},
            "cache": {}, "timings": {"send": 0, "wait": round(float(e.get("ms") or 0), 1), "receive": 0},
            "_resourceType": e.get("type", ""),
        })
    return {"log": {"version": "1.2", "creator": {"name": "QAX", "version": "1"}, "entries": out}}


def save(case_id: str, entries: list, folder: "str | None" = None) -> str:
    """Write the case's HAR under output/network/ (0600). Returns the path ('' when nothing to save)."""
    if not entries:
        return ""
    try:
        d = Path(folder or (Path.cwd() / "output" / "network"))
        d.mkdir(parents=True, exist_ok=True)
        safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(case_id))[:60] or "case"
        path = d / f"{safe}_{time.strftime('%Y%m%d_%H%M%S')}.har"
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            json.dump(to_har(entries), fh, indent=1)
        return str(path.relative_to(Path.cwd())) if str(path).startswith(str(Path.cwd())) else str(path)
    except Exception:  # noqa: BLE001
        return ""
