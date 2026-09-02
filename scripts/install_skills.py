#!/usr/bin/env python3
"""Inspect and synchronise the repository's first-party Skills.

The files under :mod:`skills/` are source-controlled companion Skills.  This
small, dependency-free helper makes them usable from hosts which keep Skills
in a separate directory (for example Codex or Claude) without requiring a
network connection or a host-specific installer.

The default operation is read-only.  A destination can be inspected with
``check`` and a planned update can be shown with ``sync``; files are only
written when ``--apply`` (and, for an inferred global destination,
``--allow-global``) is supplied.  Existing files changed by a user are never
silently overwritten: use ``--force`` when that is intentional.

The destination contains a small ``.lens-cap-skills.json`` receipt.  It binds
the installed tree to the source manifest, project version, and SHA-256 hash
of every file.  This makes repeated runs idempotent and turns version/content
drift into an explicit, machine-readable result.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "skills" / "manifest.json"
MARKER_NAME = ".lens-cap-skills.json"
MARKER_SCHEMA_VERSION = 1
_SAFE_NAME_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")


class SkillSyncError(ValueError):
    """Raised when a Skill manifest or destination is unsafe or malformed."""


@dataclass(frozen=True)
class SkillInventory:
    """Hash inventory for one source-controlled Skill directory."""

    name: str
    version: str
    source_dir: str
    files: dict[str, str]
    tree_sha256: str

    def public(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "source_dir": self.source_dir,
            "files": dict(self.files),
            "tree_sha256": self.tree_sha256,
        }


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _tree_sha256(files: Mapping[str, str]) -> str:
    """Hash relative names and file hashes in a canonical order.

    Including the names prevents a rename from looking like an unchanged
    directory when two files happen to have the same bytes.  Length prefixes
    avoid ambiguity between adjacent path/hash strings.
    """

    digest = hashlib.sha256()
    for relative, file_hash in sorted(files.items()):
        path_bytes = relative.encode("utf-8")
        hash_bytes = file_hash.encode("ascii")
        digest.update(len(path_bytes).to_bytes(8, "big"))
        digest.update(path_bytes)
        digest.update(len(hash_bytes).to_bytes(8, "big"))
        digest.update(hash_bytes)
    return digest.hexdigest()


def _safe_name(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value or value in {".", ".."}:
        raise SkillSyncError(f"{field} must be a non-empty safe name")
    if any(character not in _SAFE_NAME_CHARS for character in value):
        raise SkillSyncError(f"{field} contains unsafe path characters: {value!r}")
    return value


def _within(root: Path, candidate: Path) -> bool:
    try:
        candidate.relative_to(root)
    except ValueError:
        return False
    return True


def _read_project_version(root: Path) -> str:
    """Read the package version without importing optional dependencies."""

    pyproject = root / "pyproject.toml"
    try:
        import tomllib

        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except (OSError, ValueError, ModuleNotFoundError) as exc:
        raise SkillSyncError(f"cannot read project version from {pyproject}: {exc}") from exc
    project = data.get("project") if isinstance(data, Mapping) else None
    version = project.get("version") if isinstance(project, Mapping) else None
    if not isinstance(version, str) or not version.strip():
        raise SkillSyncError(f"{pyproject} does not declare project.version")
    return version.strip()


def _source_files(source_dir: Path) -> dict[str, str]:
    """Return a deterministic hash map for a Skill directory.

    Symlinks are rejected instead of followed.  This prevents a checkout (or
    a destination supplied by a caller) from escaping the intended tree while
    still keeping the operation portable across Windows and Unix hosts.
    """

    if source_dir.is_symlink():
        raise SkillSyncError(f"refusing symlinked Skill directory: {source_dir}")
    if not source_dir.is_dir():
        raise SkillSyncError(f"Skill directory does not exist: {source_dir}")
    files: dict[str, str] = {}
    for path in sorted(source_dir.rglob("*")):
        if path.is_symlink():
            raise SkillSyncError(f"refusing symlink in Skill directory: {path}")
        if not path.is_file():
            continue
        relative = path.relative_to(source_dir).as_posix()
        files[relative] = _sha256_file(path)
    if not files:
        raise SkillSyncError(f"Skill directory is empty: {source_dir}")
    return files


def _manifest_source_dir(root: Path, manifest_path: Path, relative_path: str) -> Path:
    candidate = (root / relative_path).resolve()
    root_resolved = root.resolve()
    if not _within(root_resolved, candidate):
        raise SkillSyncError(f"manifest path escapes repository root: {relative_path!r}")
    # The primary path is a file (SKILL.md); its parent is the installable
    # Skill directory.  Keeping this derived from the manifest avoids a second
    # list of Skill names that could drift.
    if not candidate.is_file():
        raise SkillSyncError(f"manifest path does not name a file: {manifest_path}: {relative_path!r}")
    source_dir = candidate.parent
    if source_dir == root_resolved:
        raise SkillSyncError(f"manifest Skill path has no containing directory: {relative_path!r}")
    return source_dir


def load_inventory(
    *,
    root: str | Path = ROOT,
    manifest_path: str | Path | None = None,
) -> tuple[dict[str, Any], str, str, dict[str, SkillInventory]]:
    """Load and validate the manifest and return source inventories.

    The tuple is ``(manifest, manifest_sha256, project_version, inventories)``.
    It is intentionally public so CI and host-specific wrappers can perform a
    read-only source check without knowing this script's command-line format.
    """

    root_path = Path(root).expanduser().resolve()
    if manifest_path is not None:
        path = Path(manifest_path).expanduser()
        if not path.is_absolute():
            root_candidate = root_path / path
            cwd_candidate = Path.cwd() / path
            # Prefer a path relative to the explicitly supplied source root;
            # retain cwd-relative CLI behaviour when that spelling is the only
            # existing one.
            path = root_candidate if root_candidate.exists() or not cwd_candidate.exists() else cwd_candidate
    else:
        # Keep an alternate ``--root`` self-contained.  This is useful for a
        # source-distribution smoke test and avoids accidentally inspecting the
        # caller's checkout when a temporary fixture root is supplied.
        path = root_path / "skills" / "manifest.json"
    path = path.resolve()
    try:
        raw = path.read_bytes()
        manifest = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SkillSyncError(f"cannot read Skill manifest {path}: {exc}") from exc
    if not isinstance(manifest, dict):
        raise SkillSyncError("Skill manifest root must be an object")
    if manifest.get("schema_version") != 1:
        raise SkillSyncError(
            f"unsupported Skill manifest schema_version {manifest.get('schema_version')!r}; expected 1"
        )
    entries = manifest.get("skills")
    if not isinstance(entries, list) or not entries:
        raise SkillSyncError("Skill manifest must contain a non-empty skills list")

    project_version = _read_project_version(root_path)
    declared_version = manifest.get("project_version")
    if declared_version is not None and str(declared_version).strip() != project_version:
        raise SkillSyncError(
            "Skill manifest project_version does not match pyproject.toml: "
            f"{declared_version!r} != {project_version!r}"
        )

    inventories: dict[str, SkillInventory] = {}
    for position, entry in enumerate(entries):
        if not isinstance(entry, Mapping):
            raise SkillSyncError(f"skills[{position}] must be an object")
        name = _safe_name(entry.get("name"), f"skills[{position}].name")
        if name in inventories:
            raise SkillSyncError(f"duplicate Skill name in manifest: {name}")
        relative_path = entry.get("path")
        if not isinstance(relative_path, str) or not relative_path.strip():
            raise SkillSyncError(f"skills[{position}].path must be a non-empty relative path")
        path_obj = Path(relative_path)
        if path_obj.is_absolute() or ".." in path_obj.parts:
            raise SkillSyncError(f"skills[{position}].path must stay inside the repository")
        source_dir = _manifest_source_dir(root_path, path, path_obj.as_posix())

        # Localized paths are part of the public manifest contract.  Validate
        # them even though the complete containing directory is copied below.
        localized = entry.get("localized_paths", [])
        if localized is None:
            localized = []
        if not isinstance(localized, list):
            raise SkillSyncError(f"skills[{position}].localized_paths must be a list")
        for localized_path in localized:
            if not isinstance(localized_path, str) or not localized_path.strip():
                raise SkillSyncError(f"skills[{position}].localized_paths contains an invalid path")
            localized_obj = Path(localized_path)
            if localized_obj.is_absolute() or ".." in localized_obj.parts:
                raise SkillSyncError(f"skills[{position}].localized_paths escapes the repository")
            localized_file = (root_path / localized_obj).resolve()
            if not _within(root_path, localized_file) or not localized_file.is_file():
                raise SkillSyncError(f"localized Skill file does not exist: {localized_path!r}")
            if localized_file.parent != source_dir:
                raise SkillSyncError(
                    f"localized Skill file is outside {name}'s directory: {localized_path!r}"
                )

        files = _source_files(source_dir)
        tree_sha256 = _tree_sha256(files)
        declared_tree = entry.get("tree_sha256", entry.get("sha256"))
        if declared_tree is not None and str(declared_tree).lower() != tree_sha256.lower():
            raise SkillSyncError(
                f"Skill {name} source hash drift: declared {declared_tree!r}, actual {tree_sha256}"
            )
        declared_skill_version = entry.get("version")
        if "version" not in entry:
            version = project_version
        elif not isinstance(declared_skill_version, str) or not declared_skill_version.strip():
            raise SkillSyncError(f"skills[{position}].version must be a non-empty string")
        else:
            version = declared_skill_version.strip()
        inventories[name] = SkillInventory(
            name=name,
            version=version,
            source_dir=source_dir.relative_to(root_path).as_posix(),
            files=files,
            tree_sha256=tree_sha256,
        )
    return manifest, _sha256_bytes(raw), project_version, inventories


def resolve_destination(
    destination: str | Path | None = None,
    *,
    environment: str | None = None,
    root: str | Path = ROOT,
) -> Path:
    """Resolve a host Skill directory without creating it.

    ``--dest`` always wins.  Environment defaults are intentionally explicit:
    ``project`` uses ``.agents/skills`` below the checkout, ``codex`` uses
    ``$CODEX_HOME/skills`` (or ``~/.codex/skills``), and ``claude`` uses
    ``$CLAUDE_HOME/skills`` (or ``~/.claude/skills``).  Callers can inspect
    inferred global locations, but the CLI requires ``--allow-global`` before
    applying changes there.
    """

    if destination is not None:
        value = Path(destination).expanduser()
        # ``Path.resolve()`` follows symlinks.  Keep the lexical destination
        # here so inspection/apply can reject a symlink rather than silently
        # writing through it to an unintended host directory.
        return Path(os.path.abspath(os.fspath(value)))
    normalized = (environment or "project").strip().lower().replace("_", "-")
    aliases = {
        "local": "project",
        "repo": "project",
        "repository": "project",
        "codex-home": "codex",
        "claude-code": "claude",
    }
    normalized = aliases.get(normalized, normalized)
    root_path = Path(root).expanduser().resolve()
    if normalized == "project":
        return root_path / ".agents" / "skills"
    if normalized == "codex":
        base = os.environ.get("CODEX_HOME")
        return (Path(base).expanduser() if base else Path.home() / ".codex") / "skills"
    if normalized == "claude":
        base = os.environ.get("CLAUDE_HOME")
        return (Path(base).expanduser() if base else Path.home() / ".claude") / "skills"
    raise SkillSyncError(f"unknown Skill environment {environment!r}; use project, codex, or claude")


def _reject_symlink(path: Path, label: str) -> None:
    if path.is_symlink():
        raise SkillSyncError(f"refusing symlinked {label}: {path}")


def _destination_files(directory: Path) -> dict[str, str]:
    if directory.is_symlink():
        raise SkillSyncError(f"refusing symlinked destination Skill directory: {directory}")
    if not directory.is_dir():
        raise SkillSyncError(f"destination Skill path is not a directory: {directory}")
    files: dict[str, str] = {}
    for path in sorted(directory.rglob("*")):
        if path.is_symlink():
            raise SkillSyncError(f"refusing symlink in destination Skill directory: {path}")
        if path.is_file():
            files[path.relative_to(directory).as_posix()] = _sha256_file(path)
    return files


def _load_marker(destination: Path) -> tuple[dict[str, Any] | None, str | None]:
    marker = destination / MARKER_NAME
    if marker.is_symlink():
        raise SkillSyncError(f"refusing symlinked Skill sync marker: {marker}")
    if not marker.exists():
        return None, None
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return None, f"cannot read marker {marker}: {exc}"
    if not isinstance(data, dict):
        return None, f"marker {marker} is not a JSON object"
    return data, None


def _expected_marker(
    *,
    manifest_sha256: str,
    project_version: str,
    inventories: Mapping[str, SkillInventory],
) -> dict[str, Any]:
    return {
        "schema_version": MARKER_SCHEMA_VERSION,
        "project": "lens-cap-pipeline",
        "project_version": project_version,
        "manifest_sha256": manifest_sha256,
        "skills": {
            name: {
                "version": inventory.version,
                "source_dir": inventory.source_dir,
                "tree_sha256": inventory.tree_sha256,
                "files": dict(inventory.files),
            }
            for name, inventory in sorted(inventories.items())
        },
    }


def inspect_skills(
    destination: str | Path | None = None,
    *,
    environment: str | None = None,
    root: str | Path = ROOT,
    manifest_path: str | Path | None = None,
) -> dict[str, Any]:
    """Return a JSON-ready source/version/content drift report.

    A missing destination is reported as ``missing`` rather than raising.  If
    no destination was requested (plain source check), only manifest/source
    consistency is returned and no host directory is touched.
    """

    root_path = Path(root).expanduser().resolve()
    if manifest_path is None:
        manifest_display_path = root_path / "skills" / "manifest.json"
    else:
        manifest_display_path = Path(manifest_path).expanduser()
        if not manifest_display_path.is_absolute():
            root_candidate = root_path / manifest_display_path
            cwd_candidate = Path.cwd() / manifest_display_path
            manifest_display_path = root_candidate if root_candidate.exists() else cwd_candidate
    manifest, manifest_sha256, project_version, inventories = load_inventory(
        root=root, manifest_path=manifest_path
    )
    report: dict[str, Any] = {
        "status": "passed",
        "operation": "check",
        # Keep a short alias for hosts that display a generic package
        # ``version`` field; ``project_version`` remains the explicit name in
        # the receipt and manifest contract.
        "version": project_version,
        "project_version": project_version,
        "manifest_project_version": manifest.get("project_version", project_version),
        "manifest_sha256": manifest_sha256,
        "manifest_schema_version": manifest.get("schema_version"),
        "manifest_path": str(manifest_display_path.resolve()),
        "skills": {},
    }
    if destination is None and environment is None:
        for name, inventory in sorted(inventories.items()):
            report["skills"][name] = {
                "status": "source-current",
                "version": inventory.version,
                "tree_sha256": inventory.tree_sha256,
                "files": len(inventory.files),
            }
        report["destination"] = None
        report["managed"] = False
        report["drift"] = False
        return report

    target = resolve_destination(destination, environment=environment, root=root)
    # Check the lexical path before ``exists()``: a dangling symlink otherwise
    # looks like a missing destination and could be replaced by an apply run.
    _reject_symlink(target, "Skill destination")
    report["destination"] = str(target)
    report["managed"] = True
    marker, marker_error = _load_marker(target) if target.exists() else (None, None)
    report["marker"] = {"path": str(target / MARKER_NAME), "present": marker is not None, "error": marker_error}
    expected_marker = _expected_marker(
        manifest_sha256=manifest_sha256,
        project_version=project_version,
        inventories=inventories,
    )
    if not target.exists():
        for name, inventory in sorted(inventories.items()):
            report["skills"][name] = {
                "status": "missing",
                "version": inventory.version,
                "actual_version": None,
                "expected_tree_sha256": inventory.tree_sha256,
                "missing": list(sorted(inventory.files)),
                "changed": [],
                "extra": [],
            }
        report["marker"]["version_drift"] = True
        report["marker"]["actual_project_version"] = None
        report["marker"]["expected"] = expected_marker
        report["expected_marker"] = expected_marker
        report["status"] = "drift"
        report["drift"] = True
        return report
    _reject_symlink(target, "Skill destination")
    if not target.is_dir():
        raise SkillSyncError(f"Skill destination is not a directory: {target}")

    marker_version_drift = marker is None or marker_error is not None
    if isinstance(marker, Mapping):
        report["marker"]["actual_project_version"] = marker.get("project_version")
    else:
        report["marker"]["actual_project_version"] = None
    if isinstance(marker, Mapping):
        marker_version_drift = marker_version_drift or any(
            marker.get(key) != expected_marker.get(key)
            for key in ("schema_version", "project", "project_version", "manifest_sha256")
        )
        marker_skills = marker.get("skills")
        marker_version_drift = marker_version_drift or not isinstance(marker_skills, Mapping)
        if isinstance(marker_skills, Mapping):
            marker_version_drift = marker_version_drift or set(marker_skills) != set(expected_marker["skills"])
    report["marker"]["version_drift"] = bool(marker_version_drift)
    for name, inventory in sorted(inventories.items()):
        destination_dir = target / name
        if destination_dir.is_symlink():
            raise SkillSyncError(f"refusing symlinked destination Skill {name}: {destination_dir}")
        if not destination_dir.exists():
            skill_report = {
                "status": "missing",
                "version": inventory.version,
                "actual_version": None,
                "expected_tree_sha256": inventory.tree_sha256,
                "actual_tree_sha256": None,
                "missing": list(sorted(inventory.files)),
                "changed": [],
                "extra": [],
            }
            report["skills"][name] = skill_report
            continue
        _reject_symlink(destination_dir, f"destination Skill {name}")
        actual_files = _destination_files(destination_dir)
        missing = sorted(set(inventory.files) - set(actual_files))
        extra = sorted(set(actual_files) - set(inventory.files))
        changed = sorted(
            relative
            for relative in set(inventory.files).intersection(actual_files)
            if inventory.files[relative].lower() != actual_files[relative].lower()
        )
        marker_skills = marker.get("skills") if isinstance(marker, Mapping) else None
        old_skill = marker_skills.get(name) if isinstance(marker_skills, Mapping) else None
        old_files = old_skill.get("files", {}) if isinstance(old_skill, Mapping) else {}
        managed_changed = sorted(
            relative
            for relative in changed
            if isinstance(old_files, Mapping) and old_files.get(relative) == actual_files[relative]
        )
        unsafe_changed = sorted(set(changed) - set(managed_changed))
        actual_tree = _tree_sha256(actual_files) if actual_files else None
        content_drift = bool(missing or changed or extra)
        skill_marker_drift = marker_version_drift
        if isinstance(old_skill, Mapping):
            skill_marker_drift = skill_marker_drift or any(
                old_skill.get(key) != expected_marker["skills"][name].get(key)
                for key in ("version", "source_dir", "tree_sha256", "files")
            )
        else:
            skill_marker_drift = True
        status = "current" if not content_drift and not skill_marker_drift else "drift"
        report["skills"][name] = {
            "status": status,
            "version": inventory.version,
            "expected_tree_sha256": inventory.tree_sha256,
            "actual_tree_sha256": actual_tree,
            "actual_version": old_skill.get("version") if isinstance(old_skill, Mapping) else None,
            "missing": missing,
            "changed": changed,
            "managed_changed": managed_changed,
            "unsafe_changed": unsafe_changed,
            "extra": extra,
            "marker_drift": skill_marker_drift,
        }
    if marker_version_drift or any(item["status"] != "current" for item in report["skills"].values()):
        report["status"] = "drift"
    report["drift"] = report["status"] == "drift"
    report["expected_marker"] = expected_marker
    return report


def _atomic_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    _reject_symlink(destination.parent, "destination parent")
    fd, temporary_name = tempfile.mkstemp(prefix=f".{destination.name}.", dir=str(destination.parent))
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as output, source.open("rb") as input_handle:
            shutil.copyfileobj(input_handle, output)
            output.flush()
            os.fsync(output.fileno())
        try:
            shutil.copymode(source, temporary, follow_symlinks=False)
        except OSError:
            # Mode preservation is cosmetic; the bytes and atomic replacement
            # are the reproducibility contract on filesystems without chmod.
            pass
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _reject_symlink(path.parent, "marker parent")
    if path.exists():
        _reject_symlink(path, "Skill sync marker")
    encoded = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _remove_empty_parents(path: Path, stop: Path) -> None:
    current = path.parent
    while current != stop and _within(stop, current):
        try:
            current.rmdir()
        except OSError:
            break
        current = current.parent


def sync_skills(
    destination: str | Path | None = None,
    *,
    environment: str | None = None,
    root: str | Path = ROOT,
    manifest_path: str | Path | None = None,
    dry_run: bool = True,
    force: bool = False,
    prune: bool = False,
    allow_global: bool = False,
) -> dict[str, Any]:
    """Plan or apply an idempotent Skill synchronisation.

    ``dry_run=True`` never creates the destination.  In apply mode, files
    changed since the last receipt are protected unless ``force=True``.  Old
    files are removed only when both ``force`` and ``prune`` are supplied.
    """

    if not dry_run and destination is None and environment is not None:
        normalized_environment = environment.strip().lower().replace("_", "-")
        if normalized_environment in {"codex", "codex-home", "claude", "claude-code"} and not allow_global:
            raise SkillSyncError(
                "refusing to write an inferred global destination; pass allow_global=True "
                "or provide an explicit destination"
            )
    if destination is None and environment is None:
        # A sync operation needs a concrete plan even when it remains a dry
        # run.  Keep that plan project-local; global targets require an
        # explicit environment or --dest at the CLI boundary.
        environment = "project"
    report = inspect_skills(
        destination,
        environment=environment,
        root=root,
        manifest_path=manifest_path,
    )
    report["operation"] = "sync"
    report["dry_run"] = bool(dry_run)
    report["force"] = bool(force)
    report["prune"] = bool(prune)
    target = Path(report["destination"])
    expected_marker = report.get("expected_marker")
    if not isinstance(expected_marker, Mapping):
        # This only occurs for an internal source-only report; the fallback is
        # defensive so a caller receives a useful error rather than a KeyError.
        raise SkillSyncError("sync report did not contain an expected marker")

    actions: list[dict[str, Any]] = []
    unsafe: list[str] = []
    marker_info = report.get("marker")
    if isinstance(marker_info, Mapping) and marker_info.get("error"):
        # A corrupt receipt may be user-owned data in a shared host directory;
        # do not silently replace it just because the Skill files happen to
        # match.  A missing receipt is safe to create, while repairing a
        # malformed one requires the same explicit force gate as a local edit.
        if not force:
            unsafe.append(MARKER_NAME)
        actions.append(
            {"skill": "", "path": MARKER_NAME, "action": "replace" if force else "protected"}
        )
    for name, item in sorted(report["skills"].items()):
        missing = list(item.get("missing", []))
        changed = list(item.get("changed", []))
        managed_changed = set(item.get("managed_changed", []))
        unsafe_changed = set(item.get("unsafe_changed", []))
        extra = list(item.get("extra", []))
        for relative in sorted(set(missing) | set(changed)):
            action = "copy" if relative in missing else "replace"
            if relative in unsafe_changed and not force:
                unsafe.append(f"{name}/{relative}")
                action = "protected"
            elif relative in changed and relative not in managed_changed and not force:
                unsafe.append(f"{name}/{relative}")
                action = "protected"
            actions.append({"skill": name, "path": relative, "action": action})
        if extra:
            for relative in extra:
                action = "remove" if force and prune else "preserve"
                if action == "preserve":
                    unsafe.append(f"{name}/{relative} (extra)")
                actions.append({"skill": name, "path": relative, "action": action})

    marker_needs_update = report.get("marker", {}).get("version_drift", True)
    if report["status"] == "passed" and not actions and not marker_needs_update:
        report["status"] = "passed"
        report["actions"] = []
        report["applied"] = False
        report["verification"] = inspect_skills(
            target,
            root=root,
            manifest_path=manifest_path,
        )
        report["drift"] = report["verification"].get("status") != "passed"
        return report
    if dry_run:
        report["status"] = "planned" if not unsafe else "drift"
        report["actions"] = actions
        report["unsafe"] = unsafe
        report["applied"] = False
        report["drift"] = True
        return report
    if unsafe and not force:
        report["status"] = "blocked"
        report["actions"] = actions
        report["unsafe"] = unsafe
        report["applied"] = False
        report["drift"] = True
        return report

    _reject_symlink(target, "Skill destination")
    if target.exists() and not target.is_dir():
        raise SkillSyncError(f"Skill destination is not a directory: {target}")
    target.mkdir(parents=True, exist_ok=True)
    _reject_symlink(target, "Skill destination")
    root_path = Path(root).expanduser().resolve()
    for name, inventory_meta in sorted(expected_marker["skills"].items()):
        source_dir = root_path / inventory_meta["source_dir"]
        destination_dir = target / name
        if destination_dir.is_symlink():
            raise SkillSyncError(f"refusing symlinked destination Skill {name}: {destination_dir}")
        if destination_dir.exists():
            _reject_symlink(destination_dir, f"destination Skill {name}")
            if not destination_dir.is_dir():
                raise SkillSyncError(f"destination Skill path is not a directory: {destination_dir}")
        destination_dir.mkdir(parents=True, exist_ok=True)
        _reject_symlink(destination_dir, f"destination Skill {name}")
        source_files = inventory_meta["files"]
        for relative in sorted(source_files):
            source = source_dir / relative
            destination_file = destination_dir / relative
            _reject_symlink(destination_file, f"destination Skill file {destination_file}")
            if destination_file.is_file() and _sha256_file(destination_file).lower() == str(source_files[relative]).lower():
                continue
            _atomic_copy(source, destination_file)
        if prune:
            # Only files below the managed Skill directory can be removed, and
            # only with explicit --force --prune.  Symlinks are rejected first.
            actual = _destination_files(destination_dir)
            for relative in sorted(set(actual) - set(source_files)):
                stale = destination_dir / relative
                _reject_symlink(stale, f"stale destination Skill file {stale}")
                stale.unlink()
                _remove_empty_parents(stale, destination_dir)

    _atomic_write_json(target / MARKER_NAME, expected_marker)
    report["status"] = "passed"
    report["actions"] = actions
    report["unsafe"] = []
    report["applied"] = True
    # Re-read once after writing.  This catches permission/atomic-write issues
    # and gives callers evidence that the receipt and tree agree.
    verified = inspect_skills(
        target,
        root=root,
        manifest_path=manifest_path,
    )
    report["verification"] = verified
    if verified.get("status") != "passed":
        report["status"] = "failed"
    report["drift"] = report["status"] != "passed"
    return report


# Short aliases keep the helper pleasant to use from small host wrappers and
# make the read-only/check vocabulary discoverable without coupling callers to
# the command-line implementation.
check_skills = inspect_skills
install_skills = sync_skills
check_drift = inspect_skills
sync = sync_skills


def _cli_version() -> str:
    try:
        return _read_project_version(ROOT)
    except SkillSyncError:
        # Keep ``--help``/``--version`` usable when a host copied only this
        # maintenance script; normal checks still fail clearly when the source
        # manifest/project is unavailable.
        return "unknown"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lens-cap-skills",
        description="Check or synchronise repository Skills across Codex/Claude environments",
    )
    parser.add_argument(
        "command",
        nargs="?",
        choices=("check", "doctor", "status", "sync", "install", "update"),
        default=None,
        help="check source/destination (default) or plan/apply a sync",
    )
    parser.add_argument("--check", action="store_true", help="alias for the check command")
    parser.add_argument(
        "--sync", "--install", "--update", "--plan", action="store_true", help="alias for the sync command"
    )
    parser.add_argument(
        "--dest", "--destination", "--target-dir", "--output", type=Path,
        help="explicit Skill destination directory",
    )
    parser.add_argument(
        "--environment",
        "--env",
        "--host",
        "--target",
        choices=("project", "local", "codex", "claude"),
        help="named destination (project, codex, or claude); --dest is safer for automation",
    )
    parser.add_argument("--manifest", type=Path, help="alternate manifest for testing or packaging")
    parser.add_argument(
        "--root", "--source-root", "--source", type=Path,
        help="source checkout root (defaults to this repository)",
    )
    parser.add_argument(
        "--apply",
        "--write",
        action="store_true",
        help="write changes (sync is otherwise a read-only dry-run)",
    )
    parser.add_argument("--yes", action="store_true", help="alias for --apply; useful in scripted runs")
    parser.add_argument("--dry-run", action="store_true", help="force read-only planning")
    parser.add_argument("--force", action="store_true", help="allow replacing user-modified Skill files")
    parser.add_argument("--prune", action="store_true", help="with --force, remove files absent from the source")
    parser.add_argument(
        "--allow-global",
        action="store_true",
        help="allow --apply to an inferred global Codex/Claude destination",
    )
    parser.add_argument("--json", action="store_true", help="emit a machine-readable report")
    parser.add_argument("--version", action="version", version=f"%(prog)s {_cli_version()}")
    return parser


def _print_report(report: Mapping[str, Any], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return
    print(f"status: {report.get('status', 'passed')}")
    if "dry_run" in report:
        print(f"mode: {'dry-run' if report.get('dry_run') else 'apply'}")
    print(f"project_version: {report.get('project_version', 'unknown')}")
    print(f"manifest_sha256: {report.get('manifest_sha256', 'unknown')}")
    if report.get("destination"):
        print(f"destination: {report['destination']}")
    for name, item in sorted((report.get("skills") or {}).items()):
        status = item.get("status", "unknown") if isinstance(item, Mapping) else "unknown"
        print(f"skill {name}: {status}")
        if isinstance(item, Mapping):
            for key in ("missing", "changed", "unsafe_changed", "extra"):
                values = item.get(key)
                if values:
                    print(f"  {key}: {', '.join(str(value) for value in values)}")
    for action in report.get("actions", []):
        if isinstance(action, Mapping):
            print(f"  {action.get('action', 'inspect')}: {action.get('skill', '')}/{action.get('path', '')}")


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.check and args.sync:
        parser.error("--check and --sync are mutually exclusive")
    command = args.command
    if command in {"doctor", "status"}:
        command = "check"
    if command in {"install", "update"}:
        command = "sync"
    if args.check:
        if command and command != "check":
            parser.error("--check conflicts with the positional command")
        command = "check"
    elif args.sync:
        if command and command != "sync":
            parser.error("--sync conflicts with the positional command")
        command = "sync"
    # A write/plan flag is an unambiguous request for the sync operation even
    # when a short host recipe omits the positional command.  A bare
    # ``--dest`` remains a read-only check.
    command = command or (
        "sync" if args.apply or args.yes or args.sync or (args.dry_run and args.dest is not None) else "check"
    )

    root = args.root.expanduser().resolve() if args.root else ROOT
    destination_explicit = args.dest is not None or args.environment is not None
    apply = bool(args.apply or args.yes) and not args.dry_run
    if command == "check":
        # A plain check is source-only and therefore safe in CI.  Supplying a
        # destination/environment turns it into a host drift check.
        try:
            report = inspect_skills(
                args.dest,
                environment=args.environment,
                root=root,
                manifest_path=args.manifest,
            )
        except (SkillSyncError, OSError, ValueError) as exc:
            print(f"lens-cap-skills: error: {exc}", file=sys.stderr)
            return 2
        _print_report(report, as_json=args.json)
        return 0 if report.get("status") in {"passed"} else 1

    if args.prune and not args.force:
        print("lens-cap-skills: error: --prune requires --force", file=sys.stderr)
        return 2
    if apply and not destination_explicit:
        print(
            "lens-cap-skills: error: --apply requires an explicit --dest or --environment; "
            "use --allow-global only for an inferred global target",
            file=sys.stderr,
        )
        return 2
    if apply and args.dest is None and args.environment in {"codex", "claude"} and not args.allow_global:
        print(
            "lens-cap-skills: error: refusing to write an inferred global destination; "
            "pass --dest or --allow-global explicitly",
            file=sys.stderr,
        )
        return 2
    try:
        report = sync_skills(
            args.dest,
            environment=args.environment,
            root=root,
            manifest_path=args.manifest,
            dry_run=not apply,
            force=args.force,
            prune=args.prune,
            allow_global=args.allow_global,
        )
    except (SkillSyncError, OSError, ValueError) as exc:
        print(f"lens-cap-skills: error: {exc}", file=sys.stderr)
        return 2
    _print_report(report, as_json=args.json)
    if report.get("status") in {"passed", "planned"}:
        return 0
    return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
