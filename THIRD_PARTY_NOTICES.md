# Third-party software shipped with QAX

| Component | Version | Licence | Where | Modified? | Source |
|---|---|---|---|---|---|
| axe-core (Deque Systems, Inc.) | 4.13.0 | MPL-2.0 (bundles MIT/ISC parts — see its LICENSE-3RD-PARTY.txt) | `core/vendor/axe-core/` | **No** — the published `axe.min.js`, byte for byte | npm `axe-core@4.13.0`, integrity `sha512-UzGt8zg7Ny8djbYMhxl2zuEevVa7r2gJjYY5Lwr1xM7+XU2nd6CkIWFTVcCIbAP63vSz71NaVyyuSk9lHKcy0A==` (verified against the registry when vendored, 2026-10-20) |

**What MPL-2.0 asks of QAX (plain-language summary, not legal advice):** keep the licence and copyright
notices with the file (done: `core/vendor/axe-core/LICENSE*`), tell recipients where the source is (above), and
publish the source of any change made **to the axe-core files themselves**. QAX's own code is not affected —
MPL-2.0 is file-level. QAX does not modify axe-core; `tests/test_hardening_round.py` fails if the file changes.

axe-core is used only when the operator sets `QAX_A11Y=axe`; it runs inside the page under test and sends
nothing anywhere.

Development-only tools (not shipped): pip-audit (Apache-2.0) via the `dev` extra / `make audit-deps`.
