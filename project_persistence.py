from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any


PROJECT_FILE_EXTENSION = ".flowmix"
AUTOSAVE_FILE_NAME = f"autosave{PROJECT_FILE_EXTENSION}"
APP_DATA_FOLDER_NAME = "Flow88MixEngine"
PROJECTS_DIR_ENV_VAR = "FLOW88_PROJECTS_DIR"


def _user_data_root() -> Path:
    if os.name == "nt":
        base_dir = os.environ.get("APPDATA")
        if base_dir:
            return Path(base_dir)
        return Path.home() / "AppData" / "Roaming"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support"
    return Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share")))


def user_projects_dir() -> Path:
    configured_dir = os.environ.get(PROJECTS_DIR_ENV_VAR)
    if configured_dir and configured_dir.strip():
        return Path(configured_dir.strip()).expanduser().resolve()
    return _user_data_root() / APP_DATA_FOLDER_NAME / "projects"


def ensure_projects_dir() -> Path:
    projects_dir = user_projects_dir()
    projects_dir.mkdir(parents=True, exist_ok=True)
    return projects_dir


def _coerce_project_file_name(path_or_name: str | Path) -> str:
    text = str(path_or_name).strip()
    if not text:
        raise ValueError("Project path cannot be empty.")

    candidate_name = Path(text).name
    if not candidate_name:
        raise ValueError("Project file name is invalid.")
    if candidate_name in {".", ".."}:
        raise ValueError("Project file name is invalid.")

    if not candidate_name.lower().endswith(PROJECT_FILE_EXTENSION):
        candidate_name = f"{candidate_name}{PROJECT_FILE_EXTENSION}"
    return candidate_name


def resolve_project_path(path_or_name: str | Path) -> Path:
    project_file_name = _coerce_project_file_name(path_or_name)
    return (ensure_projects_dir() / project_file_name).resolve()


def autosave_project_path() -> Path:
    return (ensure_projects_dir() / AUTOSAVE_FILE_NAME).resolve()


def save_project(path: str | Path, payload: dict[str, Any]) -> Path:
    target_path = resolve_project_path(path)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return target_path


def load_project(path: str | Path) -> dict[str, Any]:
    target_path = resolve_project_path(path)
    if not target_path.exists():
        raise FileNotFoundError(f"Project file not found: {target_path}")
    try:
        data = json.loads(target_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Project file is not valid JSON: {target_path.name}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"Project file root must be a JSON object: {target_path.name}")
    return data


def list_projects() -> list[Path]:
    projects_dir = ensure_projects_dir()
    paths = [
        path
        for path in projects_dir.glob(f"*{PROJECT_FILE_EXTENSION}")
        if path.is_file() and path.name.lower() != AUTOSAVE_FILE_NAME.lower()
    ]
    return sorted(paths, key=lambda path: path.name.lower())
