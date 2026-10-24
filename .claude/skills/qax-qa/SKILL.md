---
name: qax-qa
description: QA QAX's own web UI and pages by driving them in a real browser against a local server. Use after any change to web/, to a static page, or to a user-visible flow.
---

# QAX UI QA

Only local apps. Check first that no old QAX server holds the port (`lsof -nP -iTCP:8900 -sTCP:LISTEN`), then start `qax-web` with the preview tool and stop it when finished.

## For each page changed (and `/`, `/app`, `/setup`, `/how-we-measure`)
1. **Loads:** no console errors (`read_console_messages`), no failed requests (`read_network_requests`).
2. **Content is true:** every number, command and variable on the page matches the code (`tests/` already pins several; read the rest).
3. **Phone width (375px):** `document.documentElement.scrollWidth === clientWidth` (no horizontal scroll), text readable, buttons reachable. Reset the viewport to desktop afterwards.
4. **Dark and light:** both readable; no color that only works in one.
5. **Every control does something:** click each button/link; a copy button shows its result; a form shows its error state with bad input.
6. **Keyboard:** tab order sensible, focus visible.
7. **Slow or failing backend:** stop the dependency or use a bad URL; the page says what happened and never claims a pass.

## Rules
- Never enter real credentials; test values only, on localhost.
- Do not write to Jira/Xray. `DRY_RUN` stays `true`.
- A screenshot proves the look, not the behaviour; read the page text/DOM for behaviour.

## Report
Per issue: page, what you did, what happened, what should have happened, severity. Fix cheap ones now with a test, and say what was not covered.
