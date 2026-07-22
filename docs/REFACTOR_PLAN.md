# QAX — خطة فك القلب (Service Layer) + تكاليف التشغيل

> الهدف النهائي: SaaS. المكسب في النص: أداة freelancing شغّالة.
> المفتاح: **فك القلب مرة واحدة** يخدم CLI + UI + MCP + SaaS.

---

## 1. المشكلة (باختصار)

منطق الشغل متشابك مع الـ CLI:
- الأوامر في `main.py` (2499 سطر) + `core/session_manager.py` بيعملوا `print()` / `input()` / `sys.exit()` / `os.environ`.
- الواجهة `ui/app.py` **بتشغّل `python main.py` كـ subprocess وبتقرا النص المطبوع** (سطر 314 و 843) — مش بتنادي القلب مباشرة.

**النتيجة:** UI عمياء، MCP مستحيل، SaaS مستحيل.

## 2. المبدأ الحاكم

كل قدرة تبقى **دالة نضيفة**: تاخد باراميتر، ترجّع بيانات (dataclass/dict).
**ممنوع جوه أي service:** `print` · `input` · `sys.exit` · `os.environ` كـ state · قراءة argparse.

- التقدّم (progress) → عن طريق **callback/event**، مش `print`.
- الأخطاء → **exceptions مصنّفة** أو `result.error`، مش `sys.exit`.
- القرارات التفاعلية (تأكيد/قبول) → **باراميترات** (`auto_confirm`, `auto_accept`).
- الإعدادات والمفاتيح → **object `QAXContext`** يتمرّر للـ service (بدل `load_dotenv` في كل مكان). ده اللي هيتحوّل لاحقًا لـ per-tenant في SaaS.
- `dry_run` → **باراميتر صريح** في كل call (مش `os.environ["DRY_RUN"]`).

---

## 3. الـ Services المطلوبة (خريطة النقل)

| Service | بياخد | بيرجّع | بيلفّ (موجود حاليًا فين) |
|---|---|---|---|
| `RepoComprehensionService` *(جديد)* | repo/folder | فهم: flows, entry points, صفحات | — (قدرة جديدة) |
| `GenerationService` | source + options + context | `GenerationResult{test_cases, summary, events}` | `main.py:run_analyze_command` + `_process_single_file` + `pipeline._run_generation` + `SmartChecker` + `ClaudeClient` + `validate_batch` |
| `ExportService` | test_cases + target + dry_run | `ExportResult{refs, counts, error}` | `core/adapters/*` (نضيف بالفعل — بس facade) |
| `ExecutionService` | test_cases + url + mode | `ExecutionResult{passed, failed, results}` | `execution/playwright_runner` + `test_executor` + `api_runner` |
| `ReportingService` | execution_result + format | bug report (HTML/PDF/Jira) | `core/bug_reporter` + `execution/results_reporter` |
| `DiscoveryService` | url + page | selectors dict | `core/dom_analyzer` (`run_discover_command`) |
| `FeedbackService` | decision / query | stats dict | `core/feedback_store` |
| `ConnectionService` | context | حالة كل خدمة | `main.py:run_check_connection_command` |

> ملاحظة: `core/session_manager.py` فيه `run_generate/run_smoke/run_regression/...` — دي orchestration هتتنقل جوا `GenerationService` + orchestrator رفيّع، والـ session tracking يبقى concern منفصل.

---

## 4. إصلاحات عرضية (Cross-cutting) — تتعمل مع Phase 0

1. **`QAXContext`**: object فيه `anthropic_key`, `jira_creds`, `xray_creds`, `project`, `dry_run`. يتبني مرة ويتمرّر. (single-tenant دلوقتي من `.env`؛ multi-tenant بعدين من DB).
2. **`ProgressReporter`**: interface بسيط `emit(step, status, detail)`. الـ CLI ينفّذه بـ print، الـ UI بـ progress bar، الـ MCP/SaaS يخزّنه كـ events.
3. **Result dataclasses**: `GenerationResult`, `ExportResult`, `ExecutionResult` — types واضحة بدل dicts مبعثرة.
4. **Typed exceptions**: `QAXConfigError`, `QAXGenerationError`, `QAXExecutionError` — بدل `sys.exit`.

---

## 5. المراحل (بالترتيب، كل مرحلة قابلة للتسليم لوحدها)

### Phase 0 — السقالة (Scaffolding)
- إنشاء `services/` package + `QAXContext` + `ProgressReporter` + result dataclasses + exceptions.
- **مفيش نقل منطق لسه.** مجرد الهيكل.
- **معيار القبول:** الكود بيمشي زي ما هو، الحاجات الجديدة موجودة ومختبَرة بـ unit test فاضي.

### Phase 1 — GenerationService (الإثبات) ⭐
- نقل orchestration التوليد من `run_analyze_command` + `pipeline._run_generation` لـ `GenerationService.generate(source, options, context, progress)`.
- ترجّع `GenerationResult` — **من غير طباعة**.
- `main.py analyze` يبقى: parse → `GenerationService.generate()` → يطبع النتيجة.
- **معيار القبول:** `python3 main.py analyze --file X --dry-run` يطلّع **نفس** الناتج بالظبط زي قبل الفك (parity test).

### Phase 2 — Export + Execution + Reporting Services
- `ExportService` (سهل — adapters نضيفة) + إضافة **CSV adapter** (ناقص حاليًا).
- `ExecutionService` يلفّ Playwright + API runners، بياخد `url` كباراميتر.
- `ReportingService` يلفّ `bug_reporter` + `results_reporter`، ويطلّع **تقرير مستقل (HTML)** مش لـ Jira بس.
- **معيار القبول:** كل service بينادى مباشرة من سكربت اختبار ويرجّع بيانات.

### Phase 3 — تصليح الـ UI (قتل الـ subprocess) ⭐
- الواجهة تنادي الـ services **مباشرة** بدل `subprocess.Popen(main.py)`.
- إعادة تصميمها **wizard خطوة بخطوة**: مصدر → إعداد → مراجعة الاختبارات → اختيار المخرَج → تنفيذ → تقرير.
- **معيار القبول:** توليد كامل من الواجهة من غير أي subprocess، مع عرض بيانات حقيقية.
- **دلوقتي عندك أداة freelancing شغّالة.**

### Phase 4 — RepoComprehensionService (القدرة الجديدة)
- ياخد repo → يفهم الـ workflow ككل (مش ملف ملف) → يغذّي التوليد والتنفيذ.
- **معيار القبول:** repo تجريبي يطلّع ملخص flows معقول.

### Phase 5 — MCP Server *(اختياري)*
- سيرفر رفيّع يلفّ الـ services كـ MCP tools (`qax_generate_tests`, `qax_export_tests`, ...).
- **معيار القبول:** Claude يقدر ينادي أداة ويرجّعله JSON.

### Phase 6 — SaaS *(البناء الكبير، آخر حاجة)*
- Multi-tenancy (`tenant_id` في كل حاجة) + Postgres + object storage.
- Secret vault لكل عميل + Auth/Orgs + Usage metering + Billing.
- FastAPI + job queue (workers) + Playwright معزول (sandboxed).
- Observability (structured logs + Sentry + metrics).

---

## 6. تكاليف التشغيل (تقديرية — تختلف حسب المزوّد والحجم)

> **تكلفة مشتركة في الـ 3**: استهلاك Claude API (متغيّر حسب الاستخدام). الكود بالفعل بيختار Haiku للملفات الصغيرة و Sonnet للكبيرة/الخطيرة لتقليل التكلفة.

### UI (لنفسك / freelancing)
| البند | التكلفة الشهرية |
|---|---|
| تشغيل على جهازك | **$0** |
| أو VPS صغير (لو عايزه أونلاين دايمًا) | ~$5–20 |
| Playwright (محلي) | $0 |
| Claude API | حسب الاستخدام فقط |
| **الإجمالي الثابت** | **$0 – $20/شهر + استهلاك Claude** |

### MCP Server
| البند | التكلفة الشهرية |
|---|---|
| محلي (stdio) | **$0** |
| أو مستضاف (HTTP) على VPS صغير | ~$5–20 |
| Claude API | حسب الاستخدام فقط |
| **الإجمالي الثابت** | **$0 – $20/شهر + استهلاك Claude** |

### SaaS (متعدّد العملاء — منتج حقيقي)
| البند | التكلفة الشهرية (بداية) |
|---|---|
| استضافة التطبيق (API + workers) | $50 – $200 |
| قاعدة بيانات Postgres مُدارة | $20 – $100 |
| Object storage (S3) | $5 – $30 |
| Redis (طابور الشغل) | $10 – $40 |
| **تشغيل المتصفحات (Playwright معزول)** ⚠️ الأغلى | $50 – $300+ |
| Secret management | $5 – $40 |
| Auth (Auth0/Clerk) | $0 – $100+ |
| Monitoring (Sentry/logs) | $0 – $50 |
| Domain / SSL / CDN | $10 – $30 |
| **الإجمالي الثابت (استخدام منخفض)** | **~$200 – $500/شهر** |
| عند التوسّع | يوصل لآلاف الدولارات |
| Claude API | **الآن قد تدفعه أنت** نيابة عن العملاء (إلا لو BYOK) |

> **التكلفة المخفية الأكبر في SaaS**: وقت الهندسة لبناء وصيانة multi-tenancy + الفوترة + الدعم الفني. دي أغلى من الفواتير.

---

## 7. الخلاصة

- **Phase 0 → 3** = أداة freelancing شغّالة + أساس SaaS جاهز. دي أولويتك.
- **Phase 5 (MCP)** = فرع صغير اختياري فوق نفس الأساس.
- **Phase 6 (SaaS)** = الطابق الأخير، لما الأساس يثبت وتكسب من freelancing.
- الفلوس الجادّة تبدأ **فقط** مع SaaS؛ UI و MCP شبه مجانيين.
