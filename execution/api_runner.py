"""
execution/api_runner.py — API Execution Runner
===============================================
Executes test cases via HTTP requests instead of a Playwright browser.

Maps automation_steps playwright_action values to HTTP methods:
  navigate / goto            → GET
  click / submit / post      → POST
  fill / type / enter        → accumulate payload for next POST
  assert / verify / check    → validate last response body
  wait                       → time.sleep (capped at 5s)
  anything else              → SKIP (logged, not failed)

This runner is used when --exec-mode api is passed to the pipeline.
It requires APP_BASE_URL in .env (or passed to setup()).

Design:
  - All methods are synchronous (no async/playwright dependency)
  - setup() creates a requests.Session with default JSON headers
  - authenticate() supports Bearer token or basic auth
  - execute_batch() matches the dict shape of PlaywrightRunner results

Usage:
    runner = APIRunner()
    runner.setup(base_url="https://api.example.com")
    runner.authenticate(token="Bearer abc123")
    results = runner.execute_batch(test_cases)
    runner.teardown()
"""

from __future__ import annotations

import logging
import os
import time

logger = logging.getLogger(__name__)

# Sentinel: a response-body path that resolved to nothing (distinct from a real
# value of None), used by the expect_json body assertions.
_MISSING = object()


class APIRunner:
    """
    HTTP-based execution runner for API-mode test case execution.

    Replaces Playwright when test cases target REST endpoints rather than
    a browser UI. Produces the same result dict shape as TestExecutor so
    the pipeline can handle both modes uniformly.

    Attributes:
        base_url (str):    Base URL prepended to relative step values.
        session:           requests.Session shared across all steps in a run.
        auth_token (str):  Bearer token set via authenticate().
        timeout (int):     HTTP request timeout in seconds (default: 30).
    """

    def __init__(self) -> None:
        """
        Initialise the runner. Call setup() before executing any test cases.
        """
        self.base_url:   str        = os.getenv("APP_BASE_URL", "").rstrip("/")
        self.session                = None
        self.session_creds          = False     # True: the session carries a cookie / static headers (see _anonymous_session)
        self.auth_token: str | None = None
        self.timeout:    int        = 30
        # Scheme/header for the `use_auth` token on each step. Default Bearer in the
        # Authorization header; overridden per run for APIs that use another scheme
        # (e.g. Gitea/GitHub `token <sha1>`, or an API key with scheme="" / header="X-API-Key").
        self.auth_scheme: str       = "Bearer"
        self.auth_header: str       = "Authorization"
        # Variables pre-seeded into every test case's store (e.g. a user-supplied
        # token under `token`/`token_a`/`token_b` so coverage templates that would
        # otherwise self-register can run against an app QAX can't register into).
        self.seed_variables: dict   = {}

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    def setup(self, base_url: str | None = None, *,
              auth_scheme: str | None = None, auth_header: str | None = None,
              seed_variables: dict | None = None) -> None:
        """
        Initialise the HTTP session with JSON content-type defaults.

        Args:
            base_url (str | None): Override for APP_BASE_URL env var.
                                    Must include scheme, e.g. "https://api.example.com".
            auth_scheme (str | None): Scheme prefix for the `use_auth` token
                                    (default "Bearer"; "" sends the raw token value).
            auth_header (str | None): Header name for the `use_auth` token
                                    (default "Authorization").
            seed_variables (dict | None): Variables pre-seeded into every test
                                    case's store (e.g. supplied auth tokens).
        """
        if auth_scheme is not None:
            self.auth_scheme = auth_scheme
        if auth_header:
            self.auth_header = auth_header
        if seed_variables:
            self.seed_variables = dict(seed_variables)
        try:
            import requests
            self.base_url = (base_url or self.base_url).rstrip("/")
            self.session  = requests.Session()
            self.session.headers.update({
                "Content-Type": "application/json",
                "Accept":       "application/json",
            })
            logger.info("QAX APIRunner: session ready — base_url=%s", self.base_url)
            print(f"  APIRunner ready: {self.base_url or '(no base URL)'}")
        except ImportError:
            logger.error("QAX APIRunner: requests not installed")
            print("  [QAX] ERROR: requests not installed — run: pip install requests")

    def teardown(self) -> None:
        """
        Close the HTTP session and release resources.
        """
        if self.session:
            try:
                self.session.close()
            except Exception:
                pass
            self.session = None
        logger.info("QAX APIRunner: session closed")

    def authenticate(
        self,
        username: str | None = None,
        password: str | None = None,
        token: str | None = None,
    ) -> bool:
        """
        Apply authentication to the shared session.

        Prefers Bearer token when provided. Falls back to basic auth using
        APP_USERNAME / APP_PASSWORD env vars if individual credentials are
        not supplied.

        Args:
            username (str | None): Basic auth username. Falls back to APP_USERNAME.
            password (str | None): Basic auth password. Falls back to APP_PASSWORD.
            token (str | None): Bearer token string (without "Bearer " prefix).

        Returns:
            bool: True if any auth was applied, False if no credentials available.
        """
        if not self.session:
            logger.warning("QAX APIRunner: authenticate() called before setup()")
            return False

        if token:
            self.auth_token = token
            self.session.headers["Authorization"] = f"Bearer {token}"
            logger.info("QAX APIRunner: Bearer token applied")
            return True

        u = username or os.getenv("APP_USERNAME", "")
        p = password or os.getenv("APP_PASSWORD", "")
        if u and p:
            self.session.auth = (u, p)
            logger.info("QAX APIRunner: basic auth applied for user '%s'", u)
            return True

        logger.warning("QAX APIRunner: no credentials available — proceeding unauthenticated")
        return False

    # ── Step execution ─────────────────────────────────────────────────────────

    def execute_step(self, step: dict) -> dict:
        """
        Execute one automation step as an HTTP operation.

        Action mapping:
          navigate / goto        → GET request to value URL
          fill / type / enter    → Store value; no HTTP call (prep for next POST)
          click / submit / post  → POST request to value URL
          assert / verify / check→ Log assertion (no HTTP call)
          wait                   → Sleep for value seconds (max 5s)
          (anything else)        → SKIP

        Args:
            step (dict): Automation step dict from test case automation_steps.
                Expected keys: playwright_action, value, assertion_type,
                assertion_value, step_number, selector_key.

        Returns:
            dict: Step result containing:
                - step_number   (int)
                - action        (str)
                - status        (PASS / FAIL / SKIP)
                - actual_result (str)
                - error         (str | None)
                - response_code (int | None)
                - duration_ms   (int)
        """
        if not self.session:
            return self._step_error(step, "APIRunner not set up — call setup() first")

        action       = (step.get("playwright_action") or "").strip().lower()
        value        = step.get("value") or step.get("selector_key") or ""
        assert_type  = (step.get("assertion_type") or "").lower()
        assert_val   = step.get("assertion_value") or ""

        result: dict = {
            "step_number":   step.get("step_number", 0),
            "action":        action,
            "status":        "PASS",
            "actual_result": "",
            "error":         None,
            "response_code": None,
            "duration_ms":   0,
        }

        t0 = time.time()
        try:
            if action in ("navigate", "goto", "open"):
                result = self._do_get(result, value, assert_type, assert_val)

            elif action in ("fill", "type", "enter", "set_value"):
                result["actual_result"] = f"Payload value set: {value!r}"
                result["status"]        = "PASS"

            elif action in ("click", "submit", "post", "press"):
                result = self._do_post(result, value, assert_type, assert_val)

            elif action in ("assert", "verify", "check", "expect"):
                # S7: a legacy UI-shaped assertion cannot be evaluated over HTTP here
                # (no response body is fetched for it). Marking it PASS made an
                # un-checked step look verified. Report it honestly as NOT EVALUATED —
                # real assertions live in api_steps (status + expect_json), which ARE
                # checked. Never let a skipped check read as proven coverage.
                result["actual_result"] = (
                    f"Assertion NOT evaluated in API mode (use api_steps): "
                    f"{assert_type}={assert_val!r}"
                )
                result["status"] = "SKIP"

            elif action == "wait":
                secs = min(float(value) if value else 1.0, 5.0)
                time.sleep(secs)
                result["actual_result"] = f"Waited {secs}s"
                result["status"]        = "PASS"

            else:
                result["status"]        = "SKIP"
                result["actual_result"] = f"Action '{action}' skipped in API mode"
                logger.debug("QAX APIRunner: skipped action '%s'", action)

        except Exception as exc:
            result["status"] = "FAIL"
            result["error"]  = str(exc)
            logger.warning("QAX APIRunner: step error — %s", exc)

        result["duration_ms"] = int((time.time() - t0) * 1000)
        return result

    def _do_get(
        self,
        result: dict,
        value: str,
        assert_type: str,
        assert_val: str,
    ) -> dict:
        """
        Execute a GET request and apply optional assertion.

        Args:
            result (dict): Step result dict to populate.
            value (str): URL path or full URL.
            assert_type (str): "url" | "text" | "visible" | "status" | "".
            assert_val (str): Expected value for the assertion.

        Returns:
            dict: Updated result dict.
        """
        url  = self._resolve_url(value)
        resp = self.session.get(url, timeout=self.timeout)
        result["response_code"] = resp.status_code
        result["actual_result"] = f"GET {url} → {resp.status_code}"

        if assert_type == "url":
            if assert_val and assert_val not in resp.url:
                result["status"] = "FAIL"
                result["error"]  = f"URL mismatch: expected '{assert_val}' in '{resp.url}'"
        elif assert_type == "text":
            if assert_val and assert_val not in resp.text:
                result["status"] = "FAIL"
                result["error"]  = f"'{assert_val}' not found in response body"
        elif assert_type == "status":
            try:
                if resp.status_code != int(assert_val):
                    result["status"] = "FAIL"
                    result["error"]  = f"Expected status {assert_val}, got {resp.status_code}"
            except ValueError:
                pass
        elif not resp.ok:
            result["status"] = "FAIL"
            result["error"]  = f"HTTP {resp.status_code}"

        return result

    def _do_post(
        self,
        result: dict,
        value: str,
        assert_type: str,
        assert_val: str,
    ) -> dict:
        """
        Execute a POST request and apply optional assertion.

        Args:
            result (dict): Step result dict to populate.
            value (str): URL path or full URL.
            assert_type (str): "text" | "visible" | "status" | "".
            assert_val (str): Expected value for the assertion.

        Returns:
            dict: Updated result dict.
        """
        url  = self._resolve_url(value) if value else self.base_url
        resp = self.session.post(url, timeout=self.timeout)
        result["response_code"] = resp.status_code
        result["actual_result"] = f"POST {url} → {resp.status_code}"

        if assert_type == "text":
            if assert_val and assert_val not in resp.text:
                result["status"] = "FAIL"
                result["error"]  = f"'{assert_val}' not found in response body"
        elif assert_type == "status":
            try:
                if resp.status_code != int(assert_val):
                    result["status"] = "FAIL"
                    result["error"]  = f"Expected status {assert_val}, got {resp.status_code}"
            except ValueError:
                pass
        elif not resp.ok:
            result["status"] = "FAIL"
            result["error"]  = f"HTTP {resp.status_code}"

        return result

    def _resolve_url(self, value: str) -> str:
        """
        Build an absolute URL from a step value.

        Args:
            value (str): URL path (e.g. "/api/login") or full URL.

        Returns:
            str: Absolute URL.
        """
        if value.startswith("http://") or value.startswith("https://"):
            return value
        return f"{self.base_url}{value}" if self.base_url else value

    def _is_approved_origin(self, url: str) -> bool:
        """
        True when `url` is on the approved target origin (same host + effective port as
        base_url). The egress boundary: QAX only talks to the app it was pointed at, so
        a step carrying an absolute off-origin URL (which would otherwise receive the
        saved bearer token) is refused. With no base_url configured there is nothing to
        anchor against, so a relative path is allowed and an absolute URL is refused.
        """
        from urllib.parse import urlparse
        if not self.base_url:
            return not (url.startswith("http://") or url.startswith("https://"))
        b, u = urlparse(self.base_url), urlparse(url)
        if not u.scheme and not u.netloc:
            return True   # a relative path resolved against base_url — same origin
        defp = {"http": 80, "https": 443}
        bport = b.port or defp.get(b.scheme, 0)
        uport = u.port or defp.get(u.scheme, 0)
        return (u.hostname or "").lower() == (b.hostname or "").lower() and uport == bport

    def _step_error(self, step: dict, message: str) -> dict:
        """
        Build a FAIL step result for infrastructure errors.

        Args:
            step (dict): The step that could not execute.
            message (str): Error description.

        Returns:
            dict: Fully populated FAIL step result.
        """
        return {
            "step_number":   step.get("step_number", 0),
            "action":        step.get("playwright_action", ""),
            "status":        "FAIL",
            "actual_result": "",
            "error":         message,
            "response_code": None,
            "duration_ms":   0,
        }

    # ── API step execution (real HTTP) ──────────────────────────────────────────

    @staticmethod
    def _render(obj, variables: dict):
        """
        Substitute {{name}} placeholders in strings (recursively in dicts/lists).

        {{random}} and any saved variable (e.g. {{token}}) are replaced from the
        variables dict; unknown placeholders are left untouched.

        Args:
            obj: A string, dict, list, or scalar to render.
            variables (dict): Placeholder values.

        Returns:
            The rendered object of the same shape.
        """
        import re

        if isinstance(obj, str):
            # A sole placeholder ("{{my_id}}") returns the RAW value (int/etc.), so a
            # body/assertion value keeps its type — needed to compare an int id.
            sole = re.fullmatch(r"\{\{(\w+)\}\}", obj)
            if sole and variables.get(sole.group(1)) is not None:
                return variables[sole.group(1)]

            def _sub(m):
                val = variables.get(m.group(1))
                return str(val) if val is not None else m.group(0)
            return re.sub(r"\{\{(\w+)\}\}", _sub, obj)
        if isinstance(obj, dict):
            return {k: APIRunner._render(v, variables) for k, v in obj.items()}
        if isinstance(obj, list):
            return [APIRunner._render(v, variables) for v in obj]
        return obj

    def execute_api_step(self, step: dict, variables: dict) -> dict:
        """
        Execute one api_step as a real HTTP request and assert its status.

        api_step shape:
            {step_number, method, path, body?, expected_status,
             save? {var: response_key}, use_auth?}

        Placeholders {{random}} / {{var}} in path and body are rendered from
        ``variables``; ``save`` extracts response JSON fields into ``variables``
        (e.g. an access token) for later steps; ``use_auth`` sends the saved
        token as a Bearer header.

        Args:
            step (dict): The api_step definition.
            variables (dict): Mutable per-test-case variable store.

        Returns:
            dict: Step result (step_number, action, status, actual_result,
                  error, response_code, duration_ms).
        """
        method   = (step.get("method") or "GET").upper()
        path     = self._render(step.get("path", ""), variables)
        body     = self._render(step.get("body"), variables)
        expected = step.get("expected_status")
        save     = step.get("save") or {}
        data     = None                      # parsed JSON body of the response, when there is one

        result = {
            "step_number":   step.get("step_number", 0),
            "action":        f"{method} {path}",
            "status":        "PASS",
            "actual_result": "",
            "error":         None,
            "response_code": None,
            "duration_ms":   0,
        }

        if not self.session:
            return self._step_error(step, "APIRunner not set up — call setup() first")

        # S2 — egress boundary: a step may carry an ABSOLUTE url; QAX must never send
        # a request (let alone the saved bearer token) to a host other than the one it
        # was pointed at. Refuse off-origin requests outright.
        full_url = self._resolve_url(path)
        if not self._is_approved_origin(full_url):
            return self._step_error(
                step, f"off-origin request blocked by QAX egress policy: {full_url} "
                      f"is not on the approved target origin ({self.base_url})")

        headers = {}
        # use_auth: true → the default "token"; use_auth: "token_b" → that named
        # token. Named tokens let one test hold two users at once (userA vs userB)
        # so ownership / IDOR attacks are executable. The saved token is attached ONLY
        # because the origin check above has already confirmed the approved target.
        auth = step.get("use_auth")
        if auth:
            token_var = auth if isinstance(auth, str) else "token"
            tok = variables.get(token_var)
            if tok:
                headers[self.auth_header] = f"{self.auth_scheme} {tok}".strip()

        # Cookie-session / static-header mode: the session carries the operator's cookie and CSRF header, so a step
        # that must be ANONYMOUS (no use_auth: an "unauthenticated request is rejected" test) has to go out on a
        # clean session, or it would be authenticated by the cookie and the app's 2xx would be reported as a missing
        # auth check (a false positive found on CTFd).
        sender = self.session
        if getattr(self, "session_creds", False) and not auth:
            sender = self._anonymous_session()

        t0 = time.time()
        try:
            resp = sender.request(
                method, full_url,
                json=body if isinstance(body, (dict, list)) else None,
                headers=headers, timeout=self.timeout,
            )
            result["response_code"] = resp.status_code
            result["actual_result"] = f"{method} {path} → {resp.status_code}"

            if expected is not None and not self._status_matches(expected, resp.status_code):
                result["status"] = "FAIL"
                result["error"]  = (f"Expected status {expected}, got "
                                    f"{resp.status_code} — {resp.text[:150]}")
            else:
                try:
                    data = resp.json()
                except Exception:
                    data = None
                if save and isinstance(data, dict):
                    for var_name, resp_key in save.items():
                        if resp_key in data:
                            variables[var_name] = data[resp_key]
                # Assert values INSIDE the response body (not just the status) —
                # this is what catches computed/persistence bugs the status hides.
                # Rendered through variables so a control experiment can assert e.g.
                # a record's owner equals the ATTACKER's own id ({{my_id}}).
                if step.get("expect_json"):
                    self._assert_body(result, data, self._render(step["expect_json"], variables))
        except Exception as exc:
            result["status"] = "FAIL"
            result["error"]  = str(exc)

        # CONTROL step: "is this id listed in the body?" — recorded, never asserted. Lets a template tell a
        # private object from one that is shared by design (the other user can list it).
        if step.get("listed_id") is not None:
            try:
                want = str(self._render(step["listed_id"], variables))
                result["listed"] = self._id_in_body(data, want)
            except Exception:
                result["listed"] = None

        result["duration_ms"] = int((time.time() - t0) * 1000)
        return result

    def _anonymous_session(self):
        """A session with the same defaults as `self.session` but NO cookies and none of the operator's headers."""
        anon = getattr(self, "_anon_sess", None)
        if anon is None:
            import requests
            anon = requests.Session()
            anon.headers.update({k: v for k, v in self.session.headers.items()
                                 if k.lower() in ("content-type", "accept", "user-agent")})
            self._anon_sess = anon
            if getattr(self.session, "_qax_throttle", None) is not None:      # keep the 429 behaviour too
                from core.throttle import guard
                guard(anon)
        return anon

    @classmethod
    def _id_in_body(cls, data, want: str) -> bool:
        """True when any object in the JSON `data` has an id-like key whose value equals `want`."""
        keys = ("id", "uuid", "_id", "pk", "key", "slug", "name", "username")
        if isinstance(data, dict):
            if any(k in data and str(data[k]) == want for k in keys):
                return True
            return any(cls._id_in_body(v, want) for v in data.values())
        if isinstance(data, list):
            return any(cls._id_in_body(v, want) for v in data)
        return False

    @staticmethod
    def _status_matches(expected, actual: int) -> bool:
        """
        Whether the actual status satisfies the expectation. `expected` may be a
        single code or a LIST of acceptable codes — needed for security tests where
        the secure response can be any of several (403 OR 404 for a blocked resource).
        """
        try:
            if isinstance(expected, (list, tuple, set)):
                return actual in {int(x) for x in expected}
            return actual == int(expected)
        except (TypeError, ValueError):
            return True

    @staticmethod
    def _assert_body(result: dict, data, expect: dict) -> None:
        """
        Assert expected values inside a JSON response body.

        `expect` maps a dot-path to an expected value; the path walks dict keys
        and list indices, and a trailing `.length` (or `.#`) yields a collection's
        length — e.g. {"total": 5, "total_pages": 3, "items.length": 5}. The first
        mismatch or missing path flips the step to FAIL with a precise message.

        Args:
            result (dict): The step result to mutate on failure.
            data: The parsed JSON response (or None if the body was not JSON).
            expect (dict): path → expected value assertions.
        """
        if not isinstance(expect, dict):
            return
        if data is None:
            result["status"] = "FAIL"
            result["error"] = "Expected a JSON response body to assert on, got none"
            return
        for path, exp_val in expect.items():
            got = APIRunner._resolve_path(data, path)
            if got is _MISSING:
                result["status"] = "FAIL"
                result["error"] = f"Body assertion: path '{path}' not found in response"
                return
            if got != exp_val:
                result["status"] = "FAIL"
                result["error"] = (f"Body assertion failed: {path} expected "
                                   f"{exp_val!r}, got {got!r}")
                return

    @staticmethod
    def _resolve_path(data, path):
        """Walk a dot-path into a JSON value; `.length`/`.#` gives a length."""
        cur = data
        for part in str(path).split("."):
            if part in ("length", "#"):
                return len(cur) if hasattr(cur, "__len__") else _MISSING
            if isinstance(cur, list):
                try:
                    cur = cur[int(part)]
                except (ValueError, IndexError):
                    return _MISSING
            elif isinstance(cur, dict):
                if part in cur:
                    cur = cur[part]
                else:
                    return _MISSING
            else:
                return _MISSING
        return cur

    # ── Test case / batch execution ────────────────────────────────────────────

    def execute_test_case(self, test_case: dict) -> dict:
        """
        Execute all steps of a single test case.

        Stops on the first FAIL step (consistent with Playwright executor
        behaviour and stop_on_fail=True semantics).

        Args:
            test_case (dict): QAX test case dict with automation_steps or
                              manual_steps as fallback.

        Returns:
            dict: Test case result containing:
                - test_case_id  (str)
                - title         (str)
                - status        (PASS | FAIL)
                - mode          ("api")
                - duration_seconds (float)
                - steps_results (list[dict])
                - bug_reported  (bool)
                - bug_key       (str | None)
        """
        import uuid

        api_steps = test_case.get("api_steps") or []
        t0           = time.time()
        step_results = []
        overall      = "PASS"

        if api_steps:
            # Real HTTP execution, with a per-test-case variable store so steps
            # can chain (e.g. save a token on login, reuse it on the next call).
            # Pre-seeded variables (e.g. a user-supplied token) are available to
            # every step; a step that saves its own `token` still overrides them.
            variables = {"random": uuid.uuid4().hex[:8], **self.seed_variables}
            for step in api_steps:
                r = self.execute_api_step(step, variables)
                step_results.append(r)
                if r["status"] == "FAIL":
                    overall = "FAIL"
                    break
            # A case proves something only if a step had a condition to check (an
            # expected status or expected body fields). Steps with neither "pass" on
            # any answer — even a 500 — so a case made only of those checked nothing:
            # it is SKIP, never PASS (generated cases sometimes omit expected_status).
            if overall == "PASS" and not any(
                    s.get("expected_status") is not None or s.get("expect_json")
                    for s in api_steps):
                overall = "SKIP"
                step_results.append({
                    "step_number": 0, "action": "verdict", "status": "SKIP",
                    "actual_result": "", "response_code": None, "duration_ms": 0,
                    "error": "No step had an expected status or expected body — nothing was checked",
                })
        else:
            # No API steps → the UI-shaped automation_steps can't be executed
            # meaningfully over HTTP. Mark SKIP rather than a hollow PASS.
            steps = test_case.get("automation_steps") or test_case.get("manual_steps") or []
            if not steps:
                overall = "SKIP"
            else:
                overall = "SKIP"
                step_results.append({
                    "step_number": 0, "action": "api_steps",
                    "status": "SKIP", "actual_result": "",
                    "error": "No api_steps to execute (test is UI-shaped)",
                    "response_code": None, "duration_ms": 0,
                })

        return {
            "test_case_id":     test_case.get("test_case_id", ""),
            "title":            test_case.get("title", ""),
            "status":           overall,
            "mode":             "api",
            "duration_seconds": round(time.time() - t0, 2),
            "steps_results":    step_results,
            "steps":            step_results,
            "bug_reported":     False,
            "bug_key":          None,
        }

    def execute_batch(
        self,
        test_cases: list[dict],
        stop_on_fail: bool = False,
    ) -> dict:
        """
        Execute all test cases sequentially and return a batch summary.

        Return shape matches the PlaywrightRunner/TestExecutor output so the
        pipeline summary code works identically for both execution modes.

        Args:
            test_cases (list[dict]): QAX test case dicts to execute.
            stop_on_fail (bool): If True, stop the batch on first FAIL.

        Returns:
            dict: {
                "total":          int,
                "passed":         int,
                "failed":         int,
                "errors":         int,
                "pass_rate":      float,
                "duration_total": float,
                "results":        list[dict],
                "mode":           "api",
            }
        """
        t0      = time.time()
        results = []
        passed  = failed = errors = setup = 0

        logger.info(
            "QAX APIRunner: executing %d test case(s) in API mode", len(test_cases)
        )

        for tc in test_cases:
            r = self.execute_test_case(tc)
            results.append(r)

            # A coverage template that fails BEFORE its final assertion step
            # (e.g. couldn't create the resource it was going to attack) is
            # INCONCLUSIVE, not a real failure — the security property was never
            # exercised. Don't render it as ❌ or count it as failed; that's the
            # difference between "QAX proved a bug" and "QAX couldn't set the test up".
            setup_failed = (r["status"] in ("FAIL", "ERROR")
                            and self._is_coverage_setup_failure(tc, r))

            if r["status"] == "PASS":
                passed += 1
                icon, label = "✅", "PASS"
            elif setup_failed:
                setup += 1
                icon, label = "◌", "SETUP"
            elif r["status"] == "FAIL":
                failed += 1
                icon, label = "❌", "FAIL"
            else:
                errors += 1
                icon, label = "❌", r["status"]

            print(f"  {icon} [{label}] {r['title'][:55]}")

            if stop_on_fail and r["status"] == "FAIL" and not setup_failed:
                logger.info("QAX APIRunner: stopping batch on first FAIL")
                break

        total    = len(test_cases)
        duration = round(time.time() - t0, 2)
        # Inconclusive (setup-failed) tests don't count toward the pass rate —
        # they neither proved nor disproved the property.
        scored   = passed + failed + errors
        rate     = round(passed / scored * 100 if scored else 0, 1)

        logger.info(
            "QAX APIRunner: batch done — %d passed, %d failed in %.1fs",
            passed, failed, duration,
        )

        return {
            "total":          total,
            "passed":         passed,
            "failed":         failed,
            "errors":         errors,
            "setup":          setup,
            "pass_rate":      rate,
            "duration_total": duration,
            "results":        results,
            "mode":           "api",
        }

    @staticmethod
    def _is_coverage_setup_failure(test_case: dict, result: dict) -> bool:
        """
        True when a coverage-template test failed at a SETUP step rather than at
        its final assertion — the security property was never actually exercised,
        so it is inconclusive, not a proven failure. Mirrors the guard in
        OrchestratorService._confirmed_from_templates so display and verdict agree.
        """
        if test_case.get("attack") != "coverage-template":
            return False
        steps = test_case.get("api_steps") or []
        if not steps:
            return False
        last_no = steps[-1].get("step_number")
        fstep = next((s for s in (result.get("steps") or [])
                      if s.get("status") in ("FAIL", "ERROR")), None)
        return bool(fstep and fstep.get("step_number") != last_no)
