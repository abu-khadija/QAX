# Phase 0 demo kit (for the founder)

Goal of Phase 0 (PLAN.md): show QAX to 2–3 QA / dev practitioners and **learn**: is this a recurring, valuable problem?
This is discovery, not a sales pitch. Do not promise features, dates or prices.

## Before each demo (5 minutes)
```bash
cd QAX && . .venv/bin/activate        # or ./.venv-exec/bin/python
python main.py demo --web             # prints an address; open it in a browser
```
Check it loads, then press Ctrl-C when you are done. Everything runs on your own machine (127.0.0.1); no data leaves it.

## The 15-minute flow
1. **The problem (2 min).** Ask first, show second: "How do you test your API's security and access control today?
   With what tools? How long does it take? Where do results get lost?" Write down their words.
2. **Preview (2 min).** In the browser, tick the authorization box and click **Preview plan**. Say: "Before it attacks
   anything it tells you what it found, what it will do, and what it is missing."
3. **Run it (3 min).** Click **Run scan** (read-only is on). While it runs, say: "It only reports what it can prove with
   a request, a response and a control."
4. **One finding in detail (3 min).** Open one confirmed finding. Show the evidence and the fix. Ask: "Would this have
   been useful to you? What would make you not trust it?"
5. **The honest part (2 min).** Scroll to **Not tested**. Say: "A quiet result here means *unknown*, not *safe*." Ask if
   that matches how they think about scanner results.
6. **Jira (1 min, optional).** `python main.py file-findings --from <scan.json> --show-payload` shows the bug it would
   file (nothing is filed).
7. **Close (2 min).** Ask the three questions below. Do not ask for a commitment.

## Ask these (and write down the exact words)
1. Would you run this on one of your own staging apps this week? What stops you?
2. What would make you **not** trust a result?
3. Who decides to buy or adopt a tool like this where you work, and what do they pay for today?

Also useful: "What did you expect it to find that it did not?" and "What did you not understand on the screen?"

## Do not
- Do not demo on a production system, on your employer's systems, or on anything you are not allowed to test.
- Do not promise: dates, pricing, features that are not in the Quickstart, or that it finds "all" bugs.
- Do not count compliments or "interesting" as interest. Count: they run it, they return, they name a budget.
- Do not collect their credentials or tokens. If they want to try it, they run it themselves on their own staging.

## Notes template (one per person)
```
Date / person / role / company size (no names needed if they prefer):
How they test APIs/security today (their words):
Pain they described (their words):
What they trusted:
What they did not trust:
What they asked for:
Would run it on their staging? yes / no / maybe  ->  what stops them:
Who buys such tools, what they pay for today:
Next step they agreed to (if any):
```

## If someone says "I'll try it"
Follow `QUICKSTART.md`. They need read access to the (private) repository, or you share a screen while they run it.
Tell them: authorized staging only, `--read-only` first, and a token for a **test** user. Then send me their notes; each
confusing moment becomes a fix.

## Phase 0 exit (from PLAN.md)
2–3 demos done (or the blocker written down with a dated next attempt), concise notes saved per person, and the next
product question identified from evidence.
