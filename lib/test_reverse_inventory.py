"""The deterministic inventory behind `specstride --reverse` (lib/reverse_inventory.py).

Every test copies a committed fixture into tmp_path before touching it.
"""
import json
import os
import shutil
import subprocess

import pytest

import reverse_inventory

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURES = os.path.join(HERE, "fixtures", "reverse")


def copy_fixture(tmp_path, name):
    dest = tmp_path / name
    shutil.copytree(os.path.join(FIXTURES, name), dest, symlinks=True)
    return dest


def mini(tmp_path):
    src = copy_fixture(tmp_path, "src-mini")
    # git does not store an ignored file, so the fixture's ignored file is made here
    (src / "debug.log").write_text("ignored by .gitignore\n")
    return src


def git(src, *args):
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.com")
    subprocess.run(["git", "-C", str(src), *args], check=True, capture_output=True, env=env)


def paths(inventory):
    return [f["path"] for f in inventory["files"]]


def test_two_runs_give_byte_identical_json(tmp_path):
    src = mini(tmp_path)
    first = reverse_inventory.dumps(reverse_inventory.build(str(src)))
    second = reverse_inventory.dumps(reverse_inventory.build(str(src)))
    assert first == second
    data = json.loads(first)
    assert paths(data) == sorted(paths(data))
    assert "time" not in first.lower().replace("runtime", "")


def test_the_cli_writes_json_and_markdown(tmp_path):
    src = mini(tmp_path)
    out, md = tmp_path / "state" / "inventory.json", tmp_path / "state" / "INVENTORY.md"
    rc = reverse_inventory.main([str(src), "--out", str(out), "--md", str(md)])
    assert rc == 0
    assert json.loads(out.read_text())["fingerprint"]
    assert str(out) in md.read_text()


def test_ignore_rules_and_the_deny_list(tmp_path):
    src = mini(tmp_path)
    for name in ("node_modules/x/index.js", "dist/out.js", ".specstride/state.json",
                 "testautomation/p/TEST_PLAN.md", "tally/__pycache__/cli.cpython-313.pyc"):
        (src / name).parent.mkdir(parents=True, exist_ok=True)
        (src / name).write_text("x\n")
    inventory = reverse_inventory.build(str(src))
    assert inventory["walk"] == "walk"
    assert "debug.log" not in paths(inventory)                 # .gitignore'd
    assert not [p for p in paths(inventory)
                if p.split("/")[0] in ("node_modules", "dist", ".specstride", "testautomation")]
    assert "tally/cli.py" in paths(inventory)


def test_git_trees_are_listed_by_git(tmp_path):
    src = mini(tmp_path)
    git(src, "init", "-q")
    git(src, "add", "-A")
    git(src, "commit", "-q", "-m", "init")
    (src / "untracked.py").write_text("print(1)\n")
    inventory = reverse_inventory.build(str(src))
    assert inventory["walk"] == "git"
    assert "untracked.py" in paths(inventory)                   # -o: untracked, not ignored
    assert "debug.log" not in paths(inventory)                  # --exclude-standard
    assert len(inventory["git_head"]) == 40
    assert inventory["git_status"] == ["?? untracked.py"]


def test_binary_and_oversize_files_are_skipped_with_a_reason(tmp_path):
    src = mini(tmp_path)
    (src / "big.txt").write_text("y" * 200)
    inventory = reverse_inventory.build(
        str(src), environ={"SPECSTRIDE_REVERSE_MAX_FILE_BYTES": "150"})
    skipped = {s["path"]: s for s in inventory["skipped"]}
    assert skipped["assets/icon.bin"]["reason"] == "binary"
    assert skipped["big.txt"]["reason"] == "oversize"
    assert skipped["big.txt"]["limit"] == 150
    assert "big.txt" not in paths(inventory)


def test_a_symlink_escaping_the_tree_is_skipped_and_an_inner_one_recorded(tmp_path):
    src = mini(tmp_path)
    os.symlink("tally/cli.py", src / "cli-link.py")
    inventory = reverse_inventory.build(str(src))
    skipped = {s["path"]: s for s in inventory["skipped"]}
    assert skipped["outside-link"]["reason"] == "symlink-escape"
    inner = next(f for f in inventory["files"] if f["path"] == "cli-link.py")
    assert inner["symlink"] is True and inner["target"] == "tally/cli.py"


def test_file_facts_language_role_and_lines(tmp_path):
    inventory = reverse_inventory.build(str(mini(tmp_path)))
    by_path = {f["path"]: f for f in inventory["files"]}
    assert by_path["tally/cli.py"]["language"] == "python"
    assert by_path["tally/cli.py"]["role"] == "source"
    assert by_path["tally/cli.py"]["lines"] == 42
    assert by_path["tests/test_report.py"]["role"] == "test"
    assert by_path["README.md"]["role"] == "doc"
    assert by_path["pyproject.toml"]["role"] == "config"


def test_project_facts_and_entry_points(tmp_path):
    inventory = reverse_inventory.build(str(mini(tmp_path)))
    project = inventory["project"]
    assert project["frameworks"] == ["pytest"]
    assert {"kind": "console-script", "manifest": "pyproject.toml", "name": "tally",
            "target": "tally.cli:main"} in project["entry_points"]
    assert {"kind": "python-main", "target": "tally/cli.py"} in project["entry_points"]
    assert inventory["specify"]["constitution"] is False
    assert inventory["specify"]["spec_numbers"] == []


def test_existing_speckit_state_is_recorded(tmp_path):
    src = mini(tmp_path)
    (src / ".specify" / "memory").mkdir(parents=True)
    (src / ".specify" / "memory" / "constitution.md").write_text("# c\n")
    (src / ".specify" / "feature.json").write_text('{"feature_directory": "specs/002-x"}')
    for name in ("001-a", "007-b"):
        (src / "specs" / name).mkdir(parents=True)
    facts = reverse_inventory.build(str(src))["specify"]
    assert facts["constitution"] is True
    assert facts["spec_numbers"] == [1, 7]
    assert facts["feature_json"] == {"feature_directory": "specs/002-x"}


def test_pnpm_workspaces_become_units(tmp_path):
    src = copy_fixture(tmp_path, "src-pnpm")
    inventory = reverse_inventory.build(str(src))
    workspaces = inventory["project"]["workspaces"]
    assert [(w["path"], w["name"]) for w in workspaces] == [
        ("packages/cli", "@demo/cli"), ("packages/core", "@demo/core")]
    assert [u["path"] for u in inventory["units"]] == [".", "packages/cli", "packages/core"]
    exports = [e["resolved"] for e in inventory["project"]["entry_points"]
               if e["kind"] == "export"]
    assert "packages/core/dist/index.js" in exports


def test_fingerprint_is_stable_and_moves_on_any_change(tmp_path):
    src = mini(tmp_path)
    base = reverse_inventory.build(str(src))["fingerprint"]
    assert reverse_inventory.build(str(src))["fingerprint"] == base
    (src / "assets" / "icon.bin").write_bytes(b"\0changed")      # a skipped file counts too
    assert reverse_inventory.build(str(src))["fingerprint"] != base


def test_oversize_total_sets_the_flag(tmp_path):
    src = mini(tmp_path)
    inventory = reverse_inventory.build(
        str(src), environ={"SPECSTRIDE_REVERSE_MAX_TOTAL_BYTES": "100"})
    assert inventory["oversize"] is True
    assert reverse_inventory.build(str(src))["oversize"] is False


def test_bad_limits_are_refused(tmp_path):
    with pytest.raises(reverse_inventory.InventoryError):
        reverse_inventory.build(str(mini(tmp_path)),
                                environ={"SPECSTRIDE_REVERSE_MAX_FILE_BYTES": "lots"})


def test_markdown_drops_file_rows_first_and_says_so(tmp_path):
    inventory = reverse_inventory.build(str(mini(tmp_path)))
    full = reverse_inventory.render_md(inventory, "/x/inventory.json", budget=100000)
    assert "| `tally/cli.py` |" in full and "dropped" not in full
    small = reverse_inventory.render_md(inventory, "/x/inventory.json", budget=1200)
    assert "## Manifests" in small and "tally.cli:main" in small
    assert "file rows dropped" in small and "/x/inventory.json" in small
