from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass(slots=True, frozen=True)
class ManagedFileEntry:
    file_name: str
    size_bytes: int
    modified_at: str
    extension: str


class FileManagerError(Exception):
    def __init__(self, detail: str, status_code: int = 400) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


def describe_managed_file(path: Path) -> ManagedFileEntry:
    stat = path.stat()
    return ManagedFileEntry(
        file_name=path.name,
        size_bytes=int(stat.st_size),
        modified_at=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
        extension=path.suffix.lower(),
    )


def sanitize_file_name(file_name: str) -> str:
    normalized = str(file_name or "").strip()
    if not normalized:
        raise FileManagerError("File name cannot be empty.", status_code=400)
    if normalized in {".", ".."}:
        raise FileManagerError("File name is invalid.", status_code=400)
    if normalized.endswith((" ", ".")):
        raise FileManagerError("File names cannot end with spaces or periods.", status_code=400)
    if Path(normalized).name != normalized:
        raise FileManagerError("Nested paths are not allowed.", status_code=400)
    if any(char in {"/", "\\"} for char in normalized):
        raise FileManagerError("Path separators are not allowed in file names.", status_code=400)
    if any(ord(char) < 32 for char in normalized):
        raise FileManagerError("Control characters are not allowed in file names.", status_code=400)
    return normalized


def ensure_managed_dir(base_dir: Path) -> Path:
    resolved_dir = base_dir.resolve()
    resolved_dir.mkdir(parents=True, exist_ok=True)
    return resolved_dir


def resolve_managed_path(base_dir: Path, file_name: str) -> Path:
    resolved_dir = ensure_managed_dir(base_dir)
    normalized_name = sanitize_file_name(file_name)
    candidate = (resolved_dir / normalized_name).resolve()
    if not candidate.is_relative_to(resolved_dir):
        raise FileManagerError("Resolved path escapes the managed directory.", status_code=400)
    return candidate


def list_managed_files(base_dir: Path, excluded_names: set[str] | None = None) -> list[ManagedFileEntry]:
    resolved_dir = ensure_managed_dir(base_dir)
    excluded = {name.lower() for name in (excluded_names or set())}
    entries: list[ManagedFileEntry] = []

    for path in resolved_dir.iterdir():
        if not path.is_file():
            continue
        if path.name.startswith(".upload-") and path.name.endswith(".part"):
            continue
        if path.name.lower() in excluded:
            continue

        entries.append(describe_managed_file(path))

    return sorted(entries, key=lambda entry: entry.file_name.lower())


def require_existing_file(base_dir: Path, file_name: str) -> Path:
    target_path = resolve_managed_path(base_dir, file_name)
    if not target_path.exists() or not target_path.is_file():
        raise FileManagerError(f"File not found: {target_path.name}", status_code=404)
    return target_path


def delete_managed_file(base_dir: Path, file_name: str) -> Path:
    target_path = require_existing_file(base_dir, file_name)
    target_path.unlink()
    return target_path


def rename_managed_file(base_dir: Path, old_name: str, new_name: str) -> Path:
    source_path = require_existing_file(base_dir, old_name)
    target_path = resolve_managed_path(base_dir, new_name)

    if source_path.name == target_path.name:
        raise FileManagerError("Old and new file names are the same.", status_code=400)
    if target_path.exists():
        raise FileManagerError(f"Target file already exists: {target_path.name}", status_code=409)

    source_path.replace(target_path)
    return target_path
