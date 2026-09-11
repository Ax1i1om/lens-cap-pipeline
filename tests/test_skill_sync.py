"""Cross-host Skill installation and drift checks."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import install_skills  # noqa: E402


def test_managed_skill_yaml_is_forced_to_lf_for_cross_platform_hashes() -> None:
    attributes = (ROOT / ".gitattributes").read_text(encoding="utf-8")

    assert "*.yaml text eol=lf" in attributes
    assert "*.yml text eol=lf" in attributes


def test_source_check_reports_manifest_version_and_hashes() -> None:
    report = install_skills.inspect_skills(root=ROOT)
    assert report["status"] == "passed"
    assert report["version"] == "0.1.0a2"
    assert report["project_version"] == "0.1.0a2"
    assert len(report["manifest_sha256"]) == 64
    assert set(report["skills"]) == {"lens-cap-imagegen", "lens-cap-production"}
    assert all(item["status"] == "source-current" for item in report["skills"].values())


def test_checked_in_project_skill_mirror_is_current() -> None:
    mirror = ROOT / ".agents" / "skills"
    report = install_skills.inspect_skills(mirror, root=ROOT)
    assert report["status"] == "passed"
    assert report["drift"] is False


def test_manifest_rejects_a_non_string_skill_version(tmp_path: Path) -> None:
    manifest = json.loads((ROOT / "skills/manifest.json").read_text(encoding="utf-8"))
    manifest["skills"][0]["version"] = None
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    try:
        install_skills.load_inventory(root=ROOT, manifest_path=path)
    except install_skills.SkillSyncError as exc:
        assert ".version" in str(exc)
    else:
        raise AssertionError("invalid Skill version was accepted")


def test_sync_is_dry_run_by_default_and_idempotent_after_apply(tmp_path: Path) -> None:
    destination = tmp_path / "codex" / "skills"

    planned = install_skills.sync_skills(destination, root=ROOT)
    assert planned["status"] == "planned"
    assert planned["dry_run"] is True
    assert not destination.exists()
    missing_report = install_skills.inspect_skills(destination, root=ROOT)
    assert missing_report["marker"]["version_drift"] is True

    applied = install_skills.sync_skills(destination, root=ROOT, dry_run=False)
    assert applied["status"] == "passed"
    assert applied["applied"] is True
    marker = destination / install_skills.MARKER_NAME
    assert marker.is_file()

    tracked = [
        destination / "lens-cap-imagegen" / "SKILL.md",
        destination / "lens-cap-production" / "agents" / "openai.yaml",
        marker,
    ]
    mtimes = {path: path.stat().st_mtime_ns for path in tracked}
    second = install_skills.sync_skills(destination, root=ROOT, dry_run=False)
    assert second["status"] == "passed"
    assert second["verification"]["status"] == "passed"
    assert {path: path.stat().st_mtime_ns for path in tracked} == mtimes
    checked = install_skills.inspect_skills(destination, root=ROOT)
    assert checked["marker"]["actual_project_version"] == "0.1.0a2"
    assert checked["skills"]["lens-cap-imagegen"]["actual_version"] == "0.1.0a3"


def test_sync_detects_and_protects_user_drift_until_force(tmp_path: Path) -> None:
    destination = tmp_path / "skills"
    install_skills.sync_skills(destination, root=ROOT, dry_run=False)
    skill_file = destination / "lens-cap-imagegen" / "SKILL.md"
    original = skill_file.read_text(encoding="utf-8")
    skill_file.write_text(original + "\nlocal edit\n", encoding="utf-8")

    checked = install_skills.inspect_skills(destination, root=ROOT)
    item = checked["skills"]["lens-cap-imagegen"]
    assert checked["status"] == "drift"
    assert "SKILL.md" in item["unsafe_changed"]

    blocked = install_skills.sync_skills(destination, root=ROOT, dry_run=False)
    assert blocked["status"] == "blocked"
    assert "local edit" in skill_file.read_text(encoding="utf-8")

    repaired = install_skills.sync_skills(destination, root=ROOT, dry_run=False, force=True)
    assert repaired["status"] == "passed"
    assert skill_file.read_text(encoding="utf-8") == original


def test_malformed_receipt_is_reported_as_drift_without_crashing(tmp_path: Path) -> None:
    destination = tmp_path / "skills"
    install_skills.sync_skills(destination, root=ROOT, dry_run=False)
    (destination / install_skills.MARKER_NAME).write_text(
        json.dumps({"schema_version": 1, "skills": []}), encoding="utf-8"
    )
    report = install_skills.inspect_skills(destination, root=ROOT)
    assert report["status"] == "drift"
    assert all(item["status"] == "drift" for item in report["skills"].values())


def test_sync_does_not_replace_a_corrupt_receipt_without_force(tmp_path: Path) -> None:
    destination = tmp_path / "skills"
    install_skills.sync_skills(destination, root=ROOT, dry_run=False)
    marker = destination / install_skills.MARKER_NAME
    marker.write_text("not-json\n", encoding="utf-8")
    blocked = install_skills.sync_skills(destination, root=ROOT, dry_run=False)
    assert blocked["status"] == "blocked"
    assert marker.read_text(encoding="utf-8") == "not-json\n"
    repaired = install_skills.sync_skills(destination, root=ROOT, dry_run=False, force=True)
    assert repaired["status"] == "passed"
    assert json.loads(marker.read_text(encoding="utf-8"))["project_version"] == "0.1.0a2"


def test_cli_apply_requires_explicit_target_and_json_check_is_read_only(tmp_path: Path, capsys) -> None:
    # No target means a source-only check; even an apply request must not infer
    # a global host directory or mutate the checkout.
    assert install_skills.main(["sync", "--apply"]) == 2
    captured = capsys.readouterr()
    assert "explicit --dest or --environment" in captured.err

    destination = tmp_path / "claude-skills"
    assert install_skills.main(["sync", "--dest", str(destination), "--apply", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "passed"
    assert payload["destination"] == str(destination.resolve())


def test_api_apply_requires_global_confirmation_for_host_environment() -> None:
    try:
        install_skills.sync_skills(environment="codex", root=ROOT, dry_run=False)
    except install_skills.SkillSyncError as exc:
        assert "allow_global" in str(exc)
    else:
        raise AssertionError("global API apply was not gated")


def test_cli_flag_aliases_select_sync_without_a_positional_command(tmp_path: Path, capsys) -> None:
    destination = tmp_path / "skills"
    assert install_skills.main(["--dest", str(destination), "--plan", "--json"]) == 0
    planned = json.loads(capsys.readouterr().out)
    assert planned["operation"] == "sync"
    assert planned["dry_run"] is True
    assert not destination.exists()

    assert install_skills.main(["--dest", str(destination), "--install", "--yes", "--json"]) == 0
    applied = json.loads(capsys.readouterr().out)
    assert applied["status"] == "passed"


def test_sync_refuses_a_symlinked_destination(tmp_path: Path) -> None:
    destination = tmp_path / "skills"
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        destination.symlink_to(outside, target_is_directory=True)
    except OSError:
        # Symlinks may be disabled on a restricted Windows runner.
        return
    # A lexical destination is retained specifically so inspection cannot
    # silently follow the link into an unrelated host directory.
    try:
        install_skills.sync_skills(destination, root=ROOT, dry_run=True)
    except install_skills.SkillSyncError as exc:
        assert "symlink" in str(exc).lower()
    else:
        raise AssertionError("symlinked destination was accepted")


def test_sync_refuses_a_dangling_symlink_destination(tmp_path: Path) -> None:
    destination = tmp_path / "skills"
    outside = tmp_path / "missing-target"
    try:
        destination.symlink_to(outside, target_is_directory=True)
    except OSError:
        return
    try:
        install_skills.inspect_skills(destination, root=ROOT)
    except install_skills.SkillSyncError as exc:
        assert "symlink" in str(exc).lower()
    else:
        raise AssertionError("dangling symlink destination was accepted")


def test_legacy_skill_migration_is_recoverable_and_explicit(tmp_path: Path) -> None:
    destination = tmp_path / "skills"
    legacy = destination / "lens-cap-front-image"
    legacy.mkdir(parents=True)
    (legacy / "SKILL.md").write_text("legacy\n", encoding="utf-8")
    planned = install_skills.migrate_legacy_skills(destination, root=ROOT, dry_run=True)
    assert planned["status"] == "planned"
    assert legacy.is_dir()
    blocked = install_skills.main(["migrate", "--dest", str(destination), "--apply", "--json"])
    assert blocked == 2
    assert legacy.is_dir()
    applied = install_skills.main(
        ["migrate", "--dest", str(destination), "--apply", "--force", "--json"]
    )
    assert applied == 0
    assert not legacy.exists()
    backup = destination / ".lens-cap-legacy" / "lens-cap-front-image"
    assert backup.is_dir()
    assert (destination / install_skills.MIGRATION_MARKER_NAME).is_file()


def test_legacy_canonical_collision_requires_explicit_opt_in(tmp_path: Path) -> None:
    destination = tmp_path / "skills"
    canonical = destination / "lens-cap-production"
    canonical.mkdir(parents=True)
    (canonical / "SKILL.md").write_text("old\n", encoding="utf-8")
    report = install_skills.inspect_legacy_skills(destination, root=ROOT)
    assert report["status"] == "passed"  # ambiguous canonical is not moved by default
    report = install_skills.inspect_legacy_skills(destination, root=ROOT, include_canonical=True)
    assert report["status"] == "drift"
    assert report["legacy"][0]["kind"] == "canonical_collision"
