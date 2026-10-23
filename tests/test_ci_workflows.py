"""
test_ci_workflows.py — the shipped CI templates stay valid and keep their promises
=================================================================================
The release-check workflow is the way a team meets QAX: it must parse, ask for authorization, post ONE
updating comment, upload the proof and fail on anything but GO. A template that silently drifts from
`qax release-check` would only be noticed in a customer's pipeline.
"""

from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

CI = Path(__file__).resolve().parent.parent / "ci"


def _load(name):
    return yaml.safe_load((CI / name).read_text(encoding="utf-8"))


def _steps(wf):
    (job,) = wf["jobs"].values()
    return job["steps"]


def test_every_github_template_is_valid_yaml_with_jobs():
    for f in sorted(CI.glob("github_action*.yml")):
        wf = _load(f.name)
        assert wf.get("jobs"), f.name


def test_release_check_workflow_runs_the_command_with_authorization_and_gates_on_it():
    wf = _load("github_action_release_check.yml")
    # PyYAML reads the bare key `on` as the boolean True
    assert "pull_request" in (wf.get("on") or wf.get(True))
    assert wf["permissions"]["pull-requests"] == "write"
    steps = _steps(wf)
    run = next(s for s in steps if s.get("id") == "rc")
    assert "release-check" in run["run"] and "--authorized" in run["run"]
    assert "--changed-since" in run["run"] and "--pr-comment" in run["run"]
    assert run["env"]["DRY_RUN"] == "true"                       # nothing written to Jira/Xray from CI
    gate = next(s for s in steps if s.get("name") == "Gate on the decision")
    assert gate["if"] == "always()"
    assert "exit 1" in gate["env"].get("CODE", "") + gate["run"]
    for code in ("0)", "1)", "2)"):
        assert code in gate["run"]                              # GO, NO-GO, NOT PROVEN each handled


def test_release_check_workflow_updates_one_comment_and_keeps_the_proof():
    steps = _steps(_load("github_action_release_check.yml"))
    post = next(s for s in steps if "Post the decision" in s.get("name", ""))
    script = post["with"]["script"]
    assert "qax-release-check" in script and "updateComment" in script and "createComment" in script
    assert "deleteComment" not in script                         # edit in place, never a pile of comments
    assert "catch" in script                                     # a fork's read-only token must not break the job
    up = next(s for s in steps if s.get("uses", "").startswith("actions/upload-artifact"))
    assert up["if"] == "always()" and ".qax/output/release/" in up["with"]["path"]


def test_release_check_workflow_can_boot_the_app_before_it_waits_for_it():
    steps = _steps(_load("github_action_release_check.yml"))
    names = [s.get("name", "") for s in steps]
    start = next(i for i, n in enumerate(names) if n.startswith("Start the app under test"))
    wait = next(i for i, n in enumerate(names) if n.startswith("Wait for the app"))
    install = next(i for i, n in enumerate(names) if n.startswith("Install QAX"))
    assert install < start < wait                                 # the app's own deps may come from QAX's install
    assert "QAX_APP_START" in steps[start]["if"] and "nohup" in steps[start]["run"]


def test_this_repos_live_workflow_is_the_template():
    """The copy in .github/workflows (what runs on this repo's PRs) never drifts from the shipped template."""
    live = CI.parent / ".github" / "workflows" / "qax-release-check.yml"
    assert live.read_text(encoding="utf-8") == (CI / "github_action_release_check.yml").read_text(encoding="utf-8")


def test_the_demo_cases_are_healthy_browser_cases_of_the_demo_app():
    import json
    cases = json.loads((CI.parent / "qa" / "qax-cases.json").read_text(encoding="utf-8"))["test_cases"]
    assert [c["test_case_id"] for c in cases] == ["TC-UI-001", "TC-UI-002", "TC-UI-003"]
    assert all(c.get("expected_outcome") == "PASS" and c.get("automation_steps") for c in cases)   # no planted-bug case


def test_release_check_workflow_is_valid_for_the_installed_cli_flags():
    """Every flag the workflow passes exists in `qax release-check`."""
    import re
    text = (CI / "github_action_release_check.yml").read_text(encoding="utf-8")
    cli = (CI.parent / "cli" / "release_cmd.py").read_text(encoding="utf-8")
    used = set(re.findall(r"(?<![\w-])(--[a-z][a-z-]+)", text.split("python main.py release-check", 1)[1].split("echo \"exit_code", 1)[0]))
    assert used, "no flags parsed"
    for flag in used:
        assert f'"{flag}"' in cli, flag
