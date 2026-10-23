#!/usr/bin/env python3
"""QAX guard: enforces docs/HANDOVER.md section 8 as a Claude Code PreToolUse hook.

Reads the hook payload (JSON) on stdin and answers with a permission decision:
  * deny  - the rule has no exceptions (non-local targets, live Jira/Xray writes, DRY_RUN off);
  * ask   - the owner must say yes first (git push, merge, a non-local page in a browser,
            changing this guard or the settings that load it);
  * silence (exit 0, no output) - not our concern, the normal permission flow decides.

An internal error never allows silently: it answers "ask" so a person looks.
Known limits (a hook reads the command text, not what a script does): a URL built at run time
inside a script file, or hidden by string concatenation, is not seen.
"""
import ipaddress
import json
import re
import shlex
import sys
from urllib.parse import urlsplit

LOCAL_NAMES = {"localhost", "0.0.0.0"}
# Hosts a plain download/API command may talk to. NOT applied to QAX, python or scanners.
INFRA_HOSTS = {
    "github.com", "api.github.com", "raw.githubusercontent.com", "objects.githubusercontent.com",
    "pypi.org", "files.pythonhosted.org", "registry.npmjs.org", "api.nuget.org",
    "cdn.playwright.dev", "playwright.azureedge.net", "playwright.download.prss.microsoft.com",
    "formulae.brew.sh", "ghcr.io",
}
FETCH_HEADS = {"curl", "wget", "http", "https", "httpie", "xh"}
SCANNER_HEADS = {
    "nc", "ncat", "telnet", "nmap", "nikto", "sqlmap", "ffuf", "gobuster", "wfuzz", "hydra",
    "masscan", "nuclei", "whatweb", "dirb", "zap-cli", "wpscan",
}
RUNNER_HEADS = {"node", "npx", "playwright", "make", "qax", "bun", "deno", "ruby", "perl", "php"}
WRAPPERS = {"xargs", "sudo", "env", "nohup", "time", "command", "nice", "exec", "timeout", "watch", "builtin"}
SHELLS = {"bash", "sh", "zsh", "dash"}
KNOWN_HEADS = {"git", "gh", "python"} | FETCH_HEADS | SCANNER_HEADS | RUNNER_HEADS | SHELLS
WRITE_HEADS = {"rm", "mv", "cp", "sed", "tee", "ln", "chmod", "chown", "truncate", "dd", "install",
               "awk", "perl", "python", "ruby", "node", "unlink", "rsync"}
READ_ONLY_HEADS = {"grep", "rg", "egrep", "fgrep", "cat", "less", "more", "head", "tail", "diff", "git", "ls", "wc", "jq"}
FILE_EXTS = {
    "json", "txt", "html", "htm", "xml", "csv", "log", "py", "md", "zip", "png", "jpg", "har",
    "yaml", "yml", "xlsx", "sh", "js", "ts", "toml", "ini", "env", "db", "sql", "pdf", "gz", "tar",
}
URL_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9+.-]*://[^\s'\"<>)]+")
BARE_HOST_RE = re.compile(r"^(?:[\w-]+\.)+([a-zA-Z]{2,})(?::\d+)?(?:/\S*)?$")
IP_HOST_RE = re.compile(r"^(?:localhost|\d{1,3}(?:\.\d{1,3}){3})(?::\d+)?(?:/\S*)?$")
TARGET_FLAG_RE = re.compile(r"^--(?:target|url|base-url|base_url|target-url|host|from-url|spec)(?:=(.*))?$")
DRY_OFF_RE = re.compile(r"DRY_RUN\s*=\s*['\"]?(?:false|0|no|off)\b", re.I)
ENV_PATH_RE = re.compile(r"(?:^|[\s/'\"])\.env(?![\w.])")
JIRA_WRITE_RE = re.compile(
    r"^mcp__.*(?:createJiraIssue|editJiraIssue|addOrEditJiraIssueComment|transitionJiraIssue|"
    r"createConfluenceContent|updateConfluenceContent|executeWrite|executeDestructive)$"
)
BROWSER_NAV_RE = re.compile(r"^mcp__.*__(?:navigate|preview_start)$")
GUARD_PATHS = (".claude/hooks/", ".claude/settings.json", ".claude/settings.local.json")


def decide(decision, reason):
    """Print a PreToolUse permission decision and stop."""
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": decision,
        "permissionDecisionReason": "QAX guard: " + reason,
    }}))
    sys.exit(0)


def is_local(host):
    """True only for this machine: localhost, a loopback IP, *.localhost, *.test."""
    host = (host or "").lower().strip("[]")
    if host in LOCAL_NAMES or host.endswith(".localhost") or host.endswith(".test"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def host_of(value):
    """Host named by a URL or a bare 'host[:port][/path]' string, or None."""
    value = value.strip()
    if "://" in value:
        return urlsplit(value).hostname
    m = BARE_HOST_RE.match(value)
    if m and m.group(1).lower() not in FILE_EXTS:
        return urlsplit("//" + value).hostname
    if IP_HOST_RE.match(value):
        return urlsplit("//" + value).hostname
    return None


def split_segments(command):
    """Split a shell command into segments of tokens (quotes respected). None if unparseable."""
    try:
        lex = shlex.shlex(command, posix=True, punctuation_chars=";&|()\n")
        lex.whitespace = " \t\r"
        lex.whitespace_split = True
        tokens = list(lex)
    except ValueError:
        return None
    segments, current = [], []
    for tok in tokens:
        if tok and set(tok) <= set(";&|()\n"):
            if current:
                segments.append(current)
            current = []
        else:
            current.append(tok)
    if current:
        segments.append(current)
    return segments


def head_index(seg):
    """Index of the command word: leading VAR=value assignments are skipped."""
    i = 0
    while i < len(seg) and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", seg[i]):
        i += 1
    return i


def base_name(word):
    """Basename of a command word; python3.12 and friends become 'python'."""
    name = word.rsplit("/", 1)[-1]
    return "python" if re.match(r"^python[0-9.]*$", name) else name


def git_subcommand(args):
    """First non-option word after git, skipping the value of options that take one."""
    skip_next = False
    for a in args:
        if skip_next:
            skip_next = False
        elif a in ("-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path"):
            skip_next = True
        elif not a.startswith("-"):
            return a
    return ""


def check_dry_run(seg, head, args):
    """DRY_RUN switched off - by assignment, export, or a write to .env."""
    i = head_index(seg)
    if any(DRY_OFF_RE.search(t) for t in seg[:i]):
        decide("deny", "DRY_RUN is off for this command: live writes to Jira/Xray are not allowed (HANDOVER section 8).")
    if head in ("export", "declare", "typeset", "env", "set") and any(DRY_OFF_RE.search(t) for t in args):
        decide("deny", "DRY_RUN is being switched off: live writes to Jira/Xray are not allowed (HANDOVER section 8).")
    redirect = any(t in (">", ">>", "1>", "2>") or t.startswith(">") for t in seg)
    if any(ENV_PATH_RE.search(t) for t in seg) and any(DRY_OFF_RE.search(t) for t in seg):
        if redirect or head not in READ_ONLY_HEADS:
            decide("deny", "this writes DRY_RUN=false into .env: live Jira/Xray writes are not allowed (HANDOVER section 8).")


def check_guard_paths(seg, head):
    """Changing the guard or its settings (rm/mv/sed/redirect/script) needs the owner's yes."""
    if not any(g in t for t in seg for g in GUARD_PATHS):
        return
    redirect = any(t in (">", ">>") or t.startswith(">") for t in seg)
    if redirect or head in WRITE_HEADS:
        decide("ask", "this changes the guard or the settings that load it; the owner confirms.")


def check_segment(seg):
    """Apply every Bash rule to one command segment."""
    i = head_index(seg)
    if i >= len(seg):
        check_dry_run(seg, "", [])
        return
    head, args = base_name(seg[i]), seg[i + 1:]
    check_dry_run(seg, head, args)
    check_guard_paths(seg, head)

    if head in SHELLS and "-c" in args:
        k = args.index("-c")
        if k + 1 < len(args):
            check_bash(args[k + 1])
        return
    if head == "eval":
        check_bash(" ".join(args))
        return
    if head in WRAPPERS:
        for k, a in enumerate(args):
            if base_name(a) in KNOWN_HEADS:
                check_segment(args[k:])
                return
        return

    if head == "git" and git_subcommand(args) == "push":
        decide("ask", "git push needs the owner's yes first (HANDOVER section 8: never push without the owner).")
    if head == "gh" and len(args) >= 2 and args[0] == "pr" and args[1] in ("merge", "create"):
        decide("ask", "gh pr %s is outward-facing; the owner decides (HANDOVER section 8)." % args[1])
    if head == "python" and args[:2] == ["-m", "pip"]:
        return

    fetch, scanner = head in FETCH_HEADS, head in SCANNER_HEADS
    if not (fetch or scanner or head == "python" or head in RUNNER_HEADS):
        return
    values, take_next = [], False
    for a in args:
        if take_next:
            values.append(a)
            take_next = False
            continue
        m = TARGET_FLAG_RE.match(a)
        if m:
            if m.group(1) is not None:
                values.append(m.group(1))
            else:
                take_next = True
            continue
        values.extend(URL_RE.findall(a))
        if (fetch or scanner) and not a.startswith("-"):
            values.append(a)
    for v in values:
        if "$" in v or "`" in v:
            decide("ask", "the target of %s is built from a variable, so it cannot be checked: %s" % (head, v[:80]))
        host = host_of(v)
        if host is None or is_local(host):
            continue
        if fetch and host.lower() in INFRA_HOSTS:
            continue
        decide("deny", "'%s' is not a local app. QAX tests only the local corpus apps "
                       "(localhost / 127.0.0.1) - HANDOVER section 8." % host)


def check_bash(command):
    """Bash tool: push, merge, live writes, non-local targets, changes to the guard."""
    segments = split_segments(command)
    if segments is None:
        if re.search(r"git\s+(?:-\S+\s+\S+\s+)*push\b", command):
            decide("ask", "git push needs the owner's yes first (could not parse the command fully).")
        for url in URL_RE.findall(command):
            host = urlsplit(url).hostname
            if host and not is_local(host) and re.search(r"\b(?:curl|wget|python3?|node|npx|http)\b", command):
                decide("ask", "could not parse this command and it mentions a non-local URL (%s)." % host)
        return
    for seg in segments:
        check_segment(seg)


def check_edit(tool_input):
    """Write/Edit: the guard and its settings need the owner's yes; DRY_RUN=false in .env is denied."""
    path = (tool_input.get("file_path") or tool_input.get("notebook_path") or "").replace("\\", "/")
    base = path.rsplit("/", 1)[-1]
    if any(g in path for g in GUARD_PATHS):
        decide("ask", "this edits the guard or its settings (%s); the owner confirms." % base)
    if base.startswith(".env") and not base.endswith((".example", ".sample", ".template")):
        text = " ".join(str(tool_input.get(k, "")) for k in ("new_string", "content", "new_source"))
        text += " ".join(str(e.get("new_string", "")) for e in tool_input.get("edits") or [] if isinstance(e, dict))
        if DRY_OFF_RE.search(text):
            decide("deny", "this sets DRY_RUN off in %s: live Jira/Xray writes are not allowed (HANDOVER section 8)." % base)


def check_browser(tool_input):
    """Browser navigate/preview_start: a non-local page is probably a test of someone else's site."""
    url = str(tool_input.get("url") or "").strip()
    if not url or url in ("back", "forward"):
        return
    host = host_of(url if "://" in url else url)
    if host and not is_local(host):
        decide("ask", "'%s' is not a local app. Reading docs is fine, testing it is not (HANDOVER section 8); "
                      "confirm this is only reading." % host)


def main():
    """Entry point: route the payload by tool name."""
    try:
        payload = json.load(sys.stdin)
        tool = payload.get("tool_name", "")
        tool_input = payload.get("tool_input") or {}
        if tool == "Bash":
            check_bash(str(tool_input.get("command") or ""))
        elif tool in ("Write", "Edit", "NotebookEdit", "MultiEdit"):
            check_edit(tool_input)
        elif JIRA_WRITE_RE.match(tool):
            decide("deny", "writes to Jira/Confluence are off (HANDOVER section 8: no writes to live Jira or Xray).")
        elif BROWSER_NAV_RE.match(tool):
            check_browser(tool_input)
    except SystemExit:
        raise
    except Exception as exc:  # never allow silently on our own bug
        decide("ask", "the guard hit an internal error (%s); check before continuing." % type(exc).__name__)


if __name__ == "__main__":
    main()
