"""Change-based selection: run what a change can affect; shared or unplaceable changes run everything."""
import json
import subprocess

from core.change_selection import changed_files, select


def _repo(tmp_path):
    r = tmp_path / "app"
    (r / "src/owner").mkdir(parents=True)
    (r / "templates/vets").mkdir(parents=True)
    (r / "templates/fragments").mkdir(parents=True)
    (r / "src/owner/OwnerController.java").write_text('@GetMapping("/owners/{ownerId}/edit") class X {}')
    (r / "templates/vets/vetList.html").write_text("<table></table>")
    (r / "templates/fragments/layout.html").write_text("<html></html>")
    (r / "src/Billing.java").write_text("class Billing {}")
    for c in (["git", "init", "-q"], ["git", "add", "."], ["git", "-c", "user.email=q@x", "-c", "user.name=q", "commit", "-qm", "1"]):
        subprocess.run(c, cwd=r, check=True)
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "app_map.json").write_text(json.dumps({"pages": [{"path": "/owners/find"}, {"path": "/owners/{id}/edit"},
                                                              {"path": "/vets.html"}, {"path": "/"}]}))
    (proj / "selectors.json").write_text(json.dumps({"owners_find": {"lastname": "#lastName"}, "vets_html": {"vet_table": "#vets"}}))
    return r, proj


CASES = [
    {"test_case_id": "O1", "automation_steps": [{"playwright_action": "navigate", "value": "/owners/find"}]},
    {"test_case_id": "O2", "automation_steps": [{"playwright_action": "navigate", "value": "/owners/7/edit"}]},
    {"test_case_id": "V1", "automation_steps": [{"playwright_action": "navigate", "value": "/vets.html"}]},
    {"test_case_id": "V2", "automation_steps": [{"playwright_action": "assert", "selector_key": "vet_table"}]},
    {"test_case_id": "S1", "test_type": "smoke", "automation_steps": [{"playwright_action": "navigate", "value": "/vets.html"}]},
]


def test_a_change_selects_the_cases_that_visit_its_pages(tmp_path):
    r, proj = _repo(tmp_path)
    (r / "templates/vets/vetList.html").write_text("<table>changed</table>")
    files, err = changed_files(str(r), "HEAD")
    assert not err and files == ["templates/vets/vetList.html"]
    s = select(CASES, files, str(r), proj)
    assert {x["id"] for x in s["selected"]} == {"V1", "V2", "S1"} and not s["run_all"]
    assert {x["id"] for x in s["skipped"]} == {"O1", "O2"}
    s = select(CASES, ["src/owner/OwnerController.java"], str(r), proj)
    assert {"O1", "O2"} <= {x["id"] for x in s["selected"]} and "V1" not in {x["id"] for x in s["selected"]}


def test_shared_or_unplaceable_changes_run_everything(tmp_path):
    r, proj = _repo(tmp_path)
    assert select(CASES, ["templates/fragments/layout.html"], str(r), proj)["run_all"]
    assert select(CASES, ["pom.xml"], str(r), proj)["run_all"]
    s = select(CASES, ["src/Billing.java"], str(r), proj)
    assert s["run_all"] and "unknown impact" in s["why"]
    assert select(CASES, [], str(r), proj)["run_all"]
    assert select(CASES, ["x.py"], str(r), tmp_path / "no-map")["run_all"]
