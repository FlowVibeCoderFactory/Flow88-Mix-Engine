from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000
DEFAULT_MAX_UPLOAD_SIZE_BYTES = 8 * 1024 * 1024 * 1024


def _resolve_path(raw_value: str | None, default_path: Path) -> Path:
    if raw_value and raw_value.strip():
        return Path(raw_value.strip()).expanduser().resolve()
    return default_path.expanduser().resolve()


def _parse_cors_origins(raw_value: str | None) -> tuple[str, ...]:
    if raw_value is None:
        return ("*",)

    normalized = [item.strip() for item in raw_value.split(",") if item.strip()]
    if not normalized:
        return ("*",)
    return tuple(normalized)


def _parse_port(raw_value: str | None) -> int:
    if raw_value is None or not raw_value.strip():
        return DEFAULT_PORT

    try:
        parsed = int(raw_value)
    except ValueError:
        return DEFAULT_PORT

    if parsed <= 0 or parsed > 65535:
        return DEFAULT_PORT
    return parsed


def _parse_positive_int(raw_value: str | None, default_value: int) -> int:
    if raw_value is None or not raw_value.strip():
        return default_value

    try:
        parsed = int(raw_value)
    except ValueError:
        return default_value

    if parsed <= 0:
        return default_value
    return parsed


@dataclass(frozen=True, slots=True)
class RuntimeSettings:
    project_root: Path
    frontend_dir: Path
    input_dir: Path
    video_input_dir: Path
    output_dir: Path
    logs_dir: Path
    cors_origins: tuple[str, ...]
    cors_allow_credentials: bool
    host: str
    port: int
    max_upload_size_bytes: int


@lru_cache(maxsize=1)
def get_runtime_settings() -> RuntimeSettings:
    project_root = Path(__file__).resolve().parent
    input_dir = _resolve_path(os.environ.get("FLOW88_INPUT_DIR"), project_root / "input")
    output_dir = _resolve_path(os.environ.get("FLOW88_OUTPUT_DIR"), project_root / "output")
    logs_dir = _resolve_path(os.environ.get("FLOW88_LOGS_DIR"), project_root / "logs")
    cors_origins = _parse_cors_origins(os.environ.get("FLOW88_CORS_ORIGINS"))

    return RuntimeSettings(
        project_root=project_root,
        frontend_dir=(project_root / "frontend").resolve(),
        input_dir=input_dir,
        video_input_dir=(input_dir / "videos").resolve(),
        output_dir=output_dir,
        logs_dir=logs_dir,
        cors_origins=cors_origins,
        cors_allow_credentials="*" not in cors_origins,
        host=(os.environ.get("FLOW88_HOST") or DEFAULT_HOST).strip() or DEFAULT_HOST,
        port=_parse_port(os.environ.get("FLOW88_PORT")),
        max_upload_size_bytes=_parse_positive_int(
            os.environ.get("FLOW88_MAX_UPLOAD_SIZE_BYTES"),
            DEFAULT_MAX_UPLOAD_SIZE_BYTES,
        ),
    )
