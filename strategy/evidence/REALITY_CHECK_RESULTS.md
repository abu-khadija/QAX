# QAX Reality-Check — external stacks (2026-10-10)

Running QAX against real, external codebases on stacks it was never tuned on, to
measure false positives, recall, and generalization. All local, every finding
verified, no disclosure.

Toolchain reality on this machine: Node ✓, Java ✓, Rust ✓, Go (install), Ruby 2.6
only (3.4 needed → railsgoat skipped), Docker ✗ (docker-only apps skipped).

---

## T1 — Node / Express / Prisma (RealWorld "Conduit" backend)

- **Repo:** gothinkster/node-express-realworld-example-app (well-written, not a
  deliberately-vulnerable app → a false-positive test).
- **Stack:** TypeScript + Express + Prisma. New language for QAX.
- **Setup:** swapped Prisma datasource Postgres→SQLite (schema uses only relations,
  so portable via `prisma db push`); seeded; served on :3000. API base `/api`, no
  OpenAPI (QAX modeled it from source).

### Result (after fixes below)
- **Confirmed P1/P2: 0** — zero false alarms on well-written code in a new language.
- **Leads (P3): 1** — missing response security headers (legitimate, minor).
- Auth works (QAX self-registered: created 3 test records), stable across 3 runs.

### Gaps the reality-check surfaced in QAX — and fixed (commit 411986a)
1. **Wrapped request bodies unsupported.** RealWorld wraps every body in
   `{"user":{...}}` / `{"article":{...}}`. QAX sent flat bodies → 422 → could not
   register → every authenticated probe silently skipped. Before the fix, "0
   findings" was really "0 coverage". Fixed with `nest_body()` (dotted field names →
   nested objects) applied to register/login/object-creation, plus recursive token
   extraction. Now works on RealWorld / JSON:API / Rails-wrapped APIs.
2. **`register_body` type heuristic bug.** It read the field `image` as a number
   (the word ends in "age"), sent `image: 1`, and tripped an unhandled Prisma 500 on
   the target — which also broke QAX's own registration. Fixed: match numeric
   keywords on the leaf name with word boundaries, and fill image/url/avatar fields
   with a real URL string.

### Honest takeaways
- **FP result is strong**: a full authenticated scan of a well-written app in an
  unfamiliar language produced zero false P1/P2 and one real minor lead.
- The reality-check's real value here was **finding two concrete, fixable product
  gaps** that would have blocked QAX on a large class of real-world APIs.
- Caveat: the Postgres→SQLite swap is ours; it did not affect the findings once auth
  worked (the earlier crash-fuzz 5xx was the image=1 bug, now gone).

---

## T3 — Go / Gin / gorm (RealWorld backend)

- **Repo:** gothinkster/golang-gin-realworld-example-app (well-written; SQLite via
  gorm, no external services → boots with `go run hello.go`). Served on :3100.
- **Note:** go-gin-vul (the known-vulnerable Go target) was **skipped** — it needs
  MySQL **and** Redis and there is no Docker here (boot-cost; see vet-first lesson).
  Swapped in this well-written Go app as a generalization + FP test instead.

### Result
- Modeled **19 endpoints from Go source**, authenticated (created 3), full probe set.
- **Confirmed P1/P2: 0** — zero false alarms on well-written Go.
- **Leads (P3): 2** — no login rate-limiting, missing security headers (both legit).

### Gap surfaced + fixed (commit 2079e07)
- **QAX couldn't read non-Python/JS source at all.** The model builder's extension
  allow-list was `{.py .ts .tsx .js .jsx}`, so a Go repo gave "No source files found"
  and scanned nothing — despite QAX being advertised as language-generic. Widened to
  Go/Ruby/Java/Kotlin/PHP/C#/Rust/Scala/Elixir, and added vendor/target/bin/obj skips.

### Honest caveat
- Model-building is LLM-based and **non-deterministic**: a fresh (uncached) build
  sometimes modeled the login fields worse and auth failed (created 0). With a good
  cached model, auth works and the result is stable (0 FP, 2 leads). Model quality
  variance is a real robustness risk worth tracking.

---

## Summary so far (2 stacks, 3 QAX gaps found + fixed)

| Target | Stack | Confirmed FP | Leads | Auth worked |
|---|---|---|---|---|
| T1 | Node/Express/Prisma | 0 | 1 | yes (3) |
| T3 | Go/Gin/gorm | 0 | 2 | yes (3) |

**Three real QAX gaps the reality-check caught and fixed**: wrapped request bodies,
the `register_body` type heuristic (`image`→number), and the Python/JS-only source
reader. Each would have silently blocked QAX on a large class of real apps. The
headline: on two unfamiliar, well-written stacks QAX produced **zero false P1/P2**
and only legitimate low-severity leads — strong "doesn't cry wolf" evidence.

## T2 — Java / Spring Boot (RealWorld backend)

- **Repo:** gothinkster/spring-boot-realworld-example-app (Spring Boot 2.6, SQLite via
  Flyway, GraphQL + REST). Built with the already-installed JDK 17 (Gradle 7.4);
  booted on :8080 in ~2s. Every endpoint is auth-gated (even `/api/tags` → 401).

### Result (after the gap-4 fix below)
- **The `.java` source reader works**: QAX modeled **19 endpoints** from Java source.
- **Auth now works (created 2)** and the full probe set runs: **0 confirmed P1/P2**,
  1 legit lead (missing CSP). Stable across runs.

### Gap #4 surfaced — and fixed (commit 21a6cf9)
- **Base-path + body-wrapper recovery when there is no OpenAPI.** This app has no
  springdoc, is served with controllers at root, wraps bodies (`UNWRAP_ROOT_VALUE`),
  and — crucially — uses Spring Security, which answers **401 for unmatched paths**.
  - First, QAX misread the model path and a naive live check (401 ≠ 404 was treated as
    "route exists") into adding a wrong `/api` prefix. Fixed: existence now requires a
    *definitive* handler response — 401/403 are ambiguous and don't count.
  - Second, the Java model captured the register fields FLAT (not `user.*`), so even at
    the right path the body was wrong. Fixed: `_auth_token` retries registration
    WRAPPED under the resource name (`{"user":{...}}`) when the flat body is rejected,
    and applies the same wrapper to login.
- Net: QAX recovers the real mount point and body shape from the live app, with no
  spec — so it now authenticates and scans wrapped/prefixed APIs (RealWorld, JSON:API,
  Spring) that it previously couldn't reach.

---

## Reality-Check conclusion (3 stacks, all fully scanned)

| Target | Stack | Source modeled | Auth worked | False P1/P2 | Leads |
|---|---|---|---|---|---|
| T1 | Node/Express/Prisma | ✓ 12 | ✓ (3) | **0** | 1 (legit) |
| T3 | Go/Gin/gorm | ✓ 19 | ✓ (3) | **0** | 2 (legit) |
| T2 | Java/Spring Boot | ✓ 19 | ✓ (2) | **0** | 1 (legit) |

**Verdict.** Across **three unfamiliar, well-written stacks** (Node, Go, Java) QAX
modeled the API from source, authenticated, ran its full probe set, and produced
**zero false P1/P2** — only legitimate low-severity leads. Strong "doesn't cry wolf,
generalises across stacks" evidence. The exercise's biggest payoff was finding and
fixing **four concrete gaps**, each of which was silently limiting QAX on real-world
APIs:
1. wrapped request bodies (`{"user":{...}}`),
2. the `register_body` type heuristic (`image`→number),
3. the Python/JS-only source reader (couldn't read Go/Java/etc.),
4. base-path + body-wrapper recovery when there is no OpenAPI.

**Honest limitations.** (1) LLM model-building is non-deterministic — a weak model can
break auth; always confirm `created_records > 0`. (2) Targets were well-written
RealWorld apps (good FP tests) — see the recall run below for the complementary test.

---

## Recall run — go-gin-vul (Go/Gin deliberately-vulnerable range)

Booted go-gin-vul (MySQL + Redis installed via brew). It exposes `/api/vul/*`
(vulnerable) and `/api/safe/*` (fixed twins). Truth: `evals/truth/go-gin-vul.json`.

**Important context:** the Anthropic API credit balance ran out mid-run, so QAX's
LLM model-building failed and its Swagger spec 500s — the normal auto-modeling path
was dead. The probes are zero-LLM, so recall was measured by feeding QAX the vuln
endpoints directly and running `ProbeService`.

### Result: 1/13 caught, **0 false positives** (no `/safe/` twin flagged)

| Vuln class | Caught? | Why |
|---|---|---|
| open-redirect | ✅ | open_redirect_probe matched `/redirect` + the `redirect` param |
| sql-injection | ❌ | blind/boolean SQLi: `/sql/login` returns 403 for benign AND quote (no status/error signal); `/sqli/byid` 500s on benign input (no valid control). QAX needs a clean control + a status/SQL-error signal — content-differential blind SQLi is a gap |
| path-traversal | ❌ | the endpoints take the payload in a **query** param (`?filename=`); QAX's probe only tests **path** params ({id}) — fixable |
| cors | ❌ | cors_probe only tests `/`, not per-endpoint — fixable |
| command-injection | ❌ | no probe |
| ssrf | ❌ | no probe |
| xss / stored-xss | ❌ | no probe |
| zip-slip | ❌ | no probe |

### Prioritized probe backlog (from this real recall test)
1. ~~Extend path-traversal to query params~~ ✅ **done** — catches `/api/vul/read`.
2. ~~Extend CORS to per-endpoint, not just `/`~~ ✅ **done** — catches `/api/vul/cors2`.
3. **Blind/boolean SQLi via response-content differential** (medium; catches same-status SQLi).
4. **New probes**: command-injection, SSRF, reflected+stored XSS, zip-slip (the classes QAX has no probe for).

### After the two quick-win probe upgrades (same target, re-measured)
Detection rose from **1 → 3 vuln classes**, still **0 false positives** (no `/safe/`
twin flagged):

| Class | Before | After | How |
|---|---|---|---|
| open-redirect | ✅ | ✅ | open_redirect_probe |
| path-traversal | ❌ | ✅ **(new)** | now probes file-ish **query** params + absolute paths → caught `?filename=/etc/passwd` |
| cors | ❌ | ✅ **(new)** | now probes per-endpoint → caught reflected Origin on `/cors2` (P3; bare `*` on `/cors1` left unflagged on purpose — often intentional) |

### After building the new probes (command-injection, SSRF, XSS)

Detection climbed to **6 of 9 vuln classes, still 0 false positives** (no `/safe/`
twin flagged). The full journey on this target: **1 → 3 → 6 classes**.

| Class | Caught | Probe |
|---|---|---|
| open-redirect | ✅ | open-redirect |
| path-traversal | ✅ | path-traversal (query-param + absolute-path) |
| cors | ✅ | cors (per-endpoint, P3) |
| command-injection | ✅ **new** | command-injection (echo-token / sleep, P1) — caught cmd1 & cmd2 |
| ssrf | ✅ **new** | ssrf (out-of-band callback, P2) |
| xss (reflected) | ✅ **new** | xss (unencoded markup in HTML, P2) |
| sql-injection | ❌ | blind: same-status / benign-500 → needs a response-content differential |
| stored-xss | ❌ | needs write→read pairing (no stored-XSS probe yet) |
| zip-slip | ❌ | niche; hard to confirm deterministically without filesystem access |

Commits: `d5a8edb` (command-injection), `067f726` (SSRF), `c65375c` (XSS), plus
`2ec0346` (path-traversal query params + per-endpoint CORS).

**Remaining backlog:** blind/boolean SQLi (content-differential), stored XSS
(write→read), zip-slip. **Precision held at 0 false positives throughout** — every
new probe keeps a control (unique token, OOB callback, HTML-context check) so it only
fires on proof.

### Backlog continued: blind SQLi + stored XSS

- **blind SQLi** — added a status-differential proof to injection detection (a TRUE
  tautology flips the status to success while benign + a matched FALSE stay failing).
  Unit-validated. go-gin-vul's own SQLi endpoints couldn't validate it live — the login
  uses a `count==1` check and the by-id endpoint 500s on all input (target quirks).
- **stored XSS** — new P1 probe: write a markup token into each free-text field, read
  the collection back, confirm only on verbatim/unencoded reflection in HTML. Validated
  live: caught `/api/vul/addcomments` → `/api/vul/getcomments` (the `content` field).

**Vuln classes QAX can now detect on go-gin-vul (each validated in a focused run):**
open-redirect, path-traversal, CORS, command-injection, SSRF, reflected XSS, stored
XSS — **7 of 9**. Still open: blind SQLi (uncatchable on this target's broken
endpoints) and zip-slip (not built; niche, hard to confirm without filesystem access).
A single full ProbeService scan can't demonstrate all 7 at once here because go-gin-vul's
SQLi login handler does `defer db.Close()` on the shared connection, breaking later
probes — a target bug, not a QAX one.

Probe commits: `d5a8edb` cmd-injection, `067f726` SSRF, `c65375c` reflected-XSS,
`c2fe0e5` blind-SQLi, `42b81bb` stored-XSS, `2ec0346` path-traversal+CORS.

**Honest verdict on recall:** QAX's deterministic probes are precise (0 FP, no safe
twin flagged) but **narrow** — they caught 1 of 9 vuln classes on this target. The
strength remains "proves what it finds without crying wolf"; the gap is **breadth of
vuln classes**, now backed by a concrete, prioritized list. (Prior sessions' two real
P1 finds on real OSS apps remain valid recall evidence for the classes QAX does cover.)
