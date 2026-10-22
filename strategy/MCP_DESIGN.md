# QAX MCP / Claude Code Plugin — مسودة تصميم (DRAFT)

**الحالة:** مسودة للمناقشة. مش خطة موازية: `strategy/PLAN.md` هو المرجع الوحيد للـ phases. الملف ده بيحدد *إيه* هنبني ولما نيجي نبنيه *فين* يتحط في الـ PLAN.
**التاريخ:** 2026-10-22

## 1. الفكرة في جملة
الـ coding agent (Claude Code / Codex / Kiro) هو اللي **بيحرّك** الشغل (يشغّل scan، يصلّح، يعيد scan)، و QAX هو اللي **بيحكم** (probes deterministic، ما نبلّغش إلا اللي اتثبت). الـ agent ما بيقررش إيه بق وإيه لأ.

```
Developer ⇄ Coding agent ──MCP──▶ qax-mcp (طبقة رفيعة) ──▶ CLI/services الموجودة
                                         │
                      scan → findings (CONFIRMED / NOT TESTED) → run record
```

## 2. مبادئ (مش قابلة للتفاوض)
1. **الحكم للـ probes**: الـ tools بترجّع findings جاهزة بالـ evidence. الـ agent يقرأ ويصلّح، ما يعدّلش verdict.
2. **أمان افتراضي**: `read_only=true` و DRY_RUN افتراضي. أي كتابة (Jira، scan غير read-only) محتاجة تأكيد صريح.
3. **Authorization صريح**: كل scan محتاج `authorized=true` + الـ target في allowlist يحددها المستخدم في config (نفس فكرة `--authorized` الموجودة).
4. **الصدق في التغطية**: النتيجة دايماً فيها `not_tested` و`coverage_gaps` و`left_on_target`. الـ agent ما يقولش "نضيف" وفي حاجة not tested.
5. **صفر AI calls في الـ quick scan** (زي القاعدة الحالية). أي tool بتستخدم AI لازم تعلن ده في الـ metadata.
6. **Additive only**: الـ MCP wrapper فوق الكود الموجود. ما نكسرش أي command (Key Rule 11).
7. **Secrets**: التوكنز بتيجي من `.env` / env vars، ما بتعدّيش كـ arguments ولا بتظهر في الـ logs.

## 3. الـ Tools المقترحة

| Tool | بيعمل إيه | Side effects | Default |
|---|---|---|---|
| `qax_plan(url, spec?, endpoints?)` | معاينة: endpoints المكتشفة، probes، كتابات متوقعة، prerequisites ناقصة | لا شيء | — |
| `qax_scan(url, authorized, read_only=true, spec?, auth_profile?)` | يشغّل الـ scan ويرجّع `run_id` + ملخص | requests على الـ target فقط | read-only |
| `qax_get_findings(run_id, state?)` | findings بالـ evidence والـ class والـ severity | لا شيء | CONFIRMED فقط |
| `qax_get_run_record(run_id)` | الـ run record الكامل (coverage, not tested, request counts) | لا شيء | — |
| `qax_rescan(run_id, finding_ids?)` | يعيد التحقق من findings معينة بعد الإصلاح (حلقة fix→prove) | requests للـ target | read-only |
| `qax_diff(run_a, run_b)` | جديد / اتصلّح / لسه موجود | لا شيء | — |
| `qax_jira_preview(run_id, finding_ids)` | يعرض الـ payloads اللي هتتكتب | لا شيء | dry-run |
| `qax_jira_file(run_id, finding_ids, confirm_token)` | يكتب tickets | **كتابة خارجية** | يرفض بدون `confirm_token` من preview |
| `qax_generate_tests(source, kind)` | test cases من story/code (on-ramp) | AI call (معلن) | memory فقط |

**مؤجل عمداً** من V1: `verify_fixes` (live writes)، Xray، الـ PR gate (لسه ما اتجربش على PRs حقيقية).

### الحلقة الأساسية
```
agent: qax_plan → qax_scan → qax_get_findings
agent: يصلّح الكود
agent: qax_rescan(run_id, finding_ids) → "FIXED" أو "STILL REPRODUCES"
agent: qax_jira_preview → (المستخدم يوافق) → qax_jira_file
```

## 4. الـ Guardrails
- **Allowlist للـ targets** في config (مثلاً `localhost`, `*.staging.example`). أي URL برا الـ allowlist → رفض.
- **Rate/volume caps** نفس طبقة `core/throttle.py`؛ الـ agent ما يقدرش يرفعها.
- **Confirm token** لأي كتابة خارجية: بيتولّد من preview محدد، صالح لمدة قصيرة وللـ payload ده بس.
- **Prompt injection**: محتوى الـ target (response bodies، error pages) بيرجع للـ agent كـ *data* مع تعليم صريح `untrusted`. الـ evidence تتقص لحجم محدد.
- **Hosted mode مش في النطاق**: الـ MCP هنا يشتغل local/self-hosted. أي استخدام من infra تبعنا بيستنى Phase 4 (SSRF gate).
- الـ transport: stdio local في V1 (مفيش network listener).

## 5. التنفيذ (تقديري)
- مجلد جديد `mcp/` (server رفيع) بيستدعي نفس الـ services اللي بتستخدمها `main.py` و`web/server.py`. مفيش منطق جديد للـ scanning.
- `run_id` يربط بـ run history الموجود (Past scans في الـ UI).
- Claude Code plugin = `plugin.json` + إعداد الـ MCP server + skill صغير ("اشتغل بحلقة scan→fix→rescan").
- الاختبارات: contract tests للـ tools + اختبار إن الـ allowlist والـ confirm token بيرفضوا + إن `qax_scan` ما بيضيفش write requests في read-only (نفس gate الـ golden).
- التقدير: **2–4 أيام** للـ V1 (tools القراءة + scan + rescan)؛ Jira write والـ plugin packaging +1–2 يوم.

## 6. أسئلة مفتوحة (محتاجة قرارك)
1. هل الـ run بتاع الـ scan sync ولا async؟ (scan طويل → الأفضل async مع `qax_scan_status`).
2. هل نكشف `qax_generate_tests` في V1 ولا نكتفي بالـ scan (الـ wedge)؟ توصيتي: لا، scan فقط في V1.
3. التوزيع: plugin في marketplace ولا مجرد `claude mcp add`؟ توصيتي: الثاني أولاً.
4. هل في ناس في demos Phase 0 طلبت حاجة زي كده؟ لو لا، نأجّل لـ بعد Phase 2.

## 7. مكانه في الـ PLAN (اقتراح، يتحط في الـ decision log لو اتوافق عليه)
- **ما يبدأش قبل:** إغلاق Phase 0 (demos) + قرار من الأدلة إنه مطلوب.
- **أنسب توقيت:** جوه Phase 2 (alpha، 16 نوفمبر → 31 ديسمبر) كـ "distribution experiment" لـ 1–2 من المشاركين اللي أصلاً بيستخدموا coding agent.
- **معيار النجاح:** مشارك يكمل حلقة scan→fix→rescan من جوه الـ agent بدون مساعدتنا، والـ run record بيطابق عدّاد الـ requests في السيرفر.
