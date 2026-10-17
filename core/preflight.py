"""
core/preflight.py — can QAX reach the target at all? (answer BEFORE scanning, out loud)
=======================================================================================
Found by pointing QAX at a dead address and at a self-signed HTTPS staging: both produced "0 confirmed, end-to-end: yes,
exit 0" — a clean-looking scan of an app it never reached. An unreachable target is not a clean bill of health.

`check_reachable` makes ONE safe GET to the base URL. ANY HTTP answer (200, 401, 404, 500) means reachable; only a
transport failure is reported, with what to do about it. Never raises.
"""

from __future__ import annotations

import requests


def check_reachable(base_url: str, *, session=None, verify: bool = True, timeout: float = 10.0) -> str | None:
    """None when the target answered over HTTP(S); otherwise a one-paragraph reason to show the operator."""
    sess = session or requests.Session()
    try:
        sess.get(base_url, timeout=timeout, allow_redirects=False, verify=verify if session is None else sess.verify)
        return None
    except requests.exceptions.SSLError as exc:
        return (f"QAX could not verify the TLS certificate of {base_url} ({_short(exc)}). A staging server with a "
                f"self-signed or internal-CA certificate needs --insecure — use it ONLY for a target you control. "
                f"Otherwise fix the certificate; a scan over an unverified connection proves nothing about a "
                f"man-in-the-middle")
    except requests.exceptions.ConnectTimeout:
        return f"{base_url} did not accept a connection within {timeout:.0f} s (firewall, VPN, or the wrong address?)"
    except requests.exceptions.ReadTimeout:
        return f"{base_url} accepted the connection but did not answer within {timeout:.0f} s"
    except requests.exceptions.ConnectionError as exc:
        txt = str(exc)
        if "Name or service not known" in txt or "nodename nor servname" in txt or "getaddrinfo" in txt:
            return f"the host name in {base_url} could not be resolved (typo, or a VPN/DNS that is not connected?)"
        return (f"nothing is listening at {base_url} (connection refused or reset): is the app running, and is the "
                f"port right?")
    except (requests.exceptions.InvalidURL, requests.exceptions.MissingSchema, requests.exceptions.InvalidSchema) as exc:
        return f"{base_url!r} is not a valid URL ({_short(exc)}); use http://host:port or https://host"
    except Exception as exc:
        return f"QAX could not reach {base_url}: {_short(exc)}"


def _short(exc: Exception, limit: int = 140) -> str:
    """The tail of an exception message: the useful part of urllib3's very long ones."""
    t = " ".join(str(exc).split())
    return t if len(t) <= limit else "…" + t[-limit:]
