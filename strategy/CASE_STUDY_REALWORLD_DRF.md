# Case study — scanning a real, non-cherry-picked OSS app (RealWorld / Django REST)

**B4 evidence.** QAX scanned a real third-party application we did NOT write or pick
for being vulnerable, black-box (URL only), and we report the honest result —
including a false positive QAX produced and we then fixed.

Date: 2026-10-14.

## Target

- **App:** `Sean-Miningah/realWorld-DjangoRestFramework` — a RealWorld "Conduit"
  backend (a Medium-style blog: users, JWT auth, articles, comments, tags, follows,
  favorites). A normal business API, not a security demo.
- **License:** **MIT** (permissive — chosen deliberately; the app's code was never
  copied into QAX's repo, only run as an external server we sent HTTP to).
- **Stack:** Django 4.2 + Django REST Framework + SimpleJWT + drf-yasg.
- **How it was run:** cloned into a throwaway dir OUTSIDE QAX's repo, booted on
  **SQLite** (a one-line settings override; no Docker, no Postgres) with
  `manage.py runserver`, serving the spec at `/swagger/?format=openapi`.
- **Scan:** `qax scan --url http://127.0.0.1:8000 --authorized --quick` — black-box,
  deterministic, zero functional-generation, ~$0.02, ~23 s. QAX self-registered its
  own test user.

## What happened (honestly)

1. **Grounding gap → fixed.** QAX first recognised no spec: a standard DRF app serves
   its OpenAPI via drf-yasg at `/swagger/?format=openapi`, which wasn't in QAX's
   known spec paths. **Fix:** added the drf-yasg / drf-spectacular paths. QAX then
   grounded the **22 real endpoints** (`/api/articles`, `/api/profiles/{username}`,
   `/api/user`, `/api/articles/{slug}/comments`, …).

2. **A false positive → found and fixed.** The first scan reported **1 P1**:
   "GET /api/articles requires authentication — returned 200 without a token." We
   verified it by hand: `GET /api/articles` with no token returns `200 []` — a
   **legitimately public** list endpoint. The drf-yasg spec declared a **global**
   `security`, which it applies to *every* operation including public reads, and
   QAX's auth-bypass coverage template trusted that flag. **Root-cause fix:** QAX now
   distinguishes a per-operation security requirement (`auth_explicit`) from a
   global-only one, and asserts auth-bypass on a GET only when it is explicitly
   protected (writes are always asserted). This is the documented "don't blindly
   trust the spec's global security" principle, now applied to the coverage layer too.

3. **Re-scan: 0 false positives.** After the fix, the deterministic scan reports:
   - **0 confirmed P1/P2**, **0 AI leads**,
   - **1 P3 lead** — missing security headers (Django serves no `Content-Security-Policy`
     by default). Real, low-severity, honest hygiene — reported as a lead, not a bug.

## Result

| Metric | Value |
|---|---|
| Endpoints grounded | 22 (real DRF API) |
| Confirmed P1/P2 | **0** |
| AI leads | 0 (quick scan, no LLM findings) |
| P3 leads | 1 (missing security headers) |
| **False positives** | **0** (after the fix) |
| Cost / duration | ~$0.02 · ~23 s |

**Read:** on a well-written, normal app QAX stays **quiet** — it did not manufacture
findings. The one thing it initially got wrong (a public endpoint flagged via the
spec's global security) is exactly the kind of cry-wolf a security product must not
do, and scanning a *real* app is what surfaced it. We found it, fixed the root cause,
added a regression test, and the golden-target gate (recall 11/11, FP 0) confirms the
fix didn't weaken real detection. This is the point of QAX: **proof, not noise.**

## Reproduce

```bash
# in a throwaway dir OUTSIDE this repo (do NOT commit the app's code):
git clone --depth 1 https://github.com/Sean-Miningah/realWorld-DjangoRestFramework drf
python3 -m venv venv && ./venv/bin/pip install "setuptools<81" -r <(grep -vi psycopg2 drf/requirements.txt)
printf 'from config.settings import *\nDATABASES={"default":{"ENGINE":"django.db.backends.sqlite3","NAME":"db.sqlite3"}}\nALLOWED_HOSTS=["*"]\n' > drf/config/settings_sqlite.py
(cd drf && ../venv/bin/python manage.py migrate --settings=config.settings_sqlite \
   && DJANGO_SETTINGS_MODULE=config.settings_sqlite ../venv/bin/python manage.py runserver 127.0.0.1:8000 --noreload &)
# then, from the QAX repo:
./.venv-exec/bin/python main.py scan --url http://127.0.0.1:8000 --authorized --quick
```
