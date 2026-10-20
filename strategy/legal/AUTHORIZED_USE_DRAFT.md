# QAX — Authorized-Use Terms (DRAFT for counsel — not in force)

*Engineering draft written 2026-10-20 so counsel has a starting point (review item A3 in
`strategy/intel/COMPANY_REVIEW_2026-10-20.md`). It is not legal advice and not published anywhere.
Every clause needs counsel's review for the jurisdictions QAX will be offered in.*

## 1. What QAX does to a target
QAX sends requests to the application the operator names: it reads pages, fills and submits forms,
sends malformed and adversarial input, and — unless read-only — creates, changes and deletes test
records. It can file issues to the operator's Jira/Xray when the operator enables live writes.

## 2. Authorization (operator's warranty)
Before each run that sends requests to a target, the operator confirms that they own the target or
hold written permission from its owner to test it in this way, within the scope (hosts, environments,
time window) of that permission. QAX records this confirmation in the run record. The operator must
not point QAX at production systems unless the permission explicitly covers them.

## 3. Prohibited uses
Testing systems without authorization; denial-of-service or load beyond the agreed scope; bypassing
access controls, CAPTCHAs or rate limits of systems the operator does not own; collecting personal
data beyond what the test needs; using findings to attack a third party.

## 4. Data
- Self-hosted: target data, evidence and credentials stay on the operator's machines.
- AI provider: only when the operator uses AI features (writing test cases, deep scan) is content sent
  to the configured AI provider — the story, code or API surface given. Quick scans, app reading and
  test runs send nothing to an AI provider. The operator is responsible for having the right to send it.
- Jira/Xray: data leaves the machine only when the operator enables live export and confirms.

## 5. Results
QAX reports what it observed in the operator's environment at that time. A PASS proves the checked
behaviour on that run only; QAX does not warrant the absence of other defects or vulnerabilities.

## 6. Open questions for counsel
1. Licence model (A2) and whether these terms sit in the licence, a separate AUP, or both.
2. Liability cap and indemnity for misuse by the operator.
3. Responsible-disclosure wording for bugs found in third-party/open-source software.
4. Export-control screening for a security testing tool.
5. Employment/IP: confirmation that QAX is free of employer IP (A1) before any company beta.
