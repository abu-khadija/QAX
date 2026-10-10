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

### Gaps the reality-check surfaced in QAX — and fixed (commit d125319)
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

## T2 — Java / Spring Boot (pending)

## T3 — Go (go-gin-vul, known-vulnerable, recall test) (pending)
