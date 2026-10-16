# Security

## Reporting a vulnerability in QAX

Please report security problems in QAX itself privately, not in a public issue. Use GitHub's
**"Report a vulnerability"** (private vulnerability reporting) on this repository, or contact the
maintainer directly. Include the QAX version and commit (`python main.py --version`), what you ran, and
what happened. You will get an acknowledgement, and a fix or an explanation, as soon as possible.

## Using QAX safely

QAX sends real, adversarial requests to the app you point it at.

- **Only scan apps you own or are explicitly allowed to test.** The scan refuses to start without
  `--authorized`, which is your confirmation.
- **Use a staging or test environment, never production.**
- **Start with `--read-only`.** It sends only GET/HEAD/OPTIONS requests; this is enforced on the HTTP
  session and checked in the release gate by counting requests on the server.
- Without `--read-only`, some checks create small test records. QAX deletes what it can and lists
  everything it could not delete under LEFT ON THE TARGET.
- A scan sends many requests in a short time. Tell whoever runs the environment before you scan.

## Where your data goes

- QAX runs on your machine. It sends scan requests only to the target's own origin, and never sends a
  supplied token to another host.
- A **quick scan makes no call to any AI provider**, even when an API key is configured (checked in the
  release gate).
- A **deep scan** (without `--quick`) uses the Anthropic API. It sends the app's API surface, the
  generated tests and their results, and your source code if you pass `--repo`.
- Credentials are read from the command line or `.env` and are not written to scan results.
  Saved evidence is redacted, but review an `--out` file before you share it.
- Jira writes happen only with `--live` (or `--apply --yes` for verify-fixes). The default is a dry run.
