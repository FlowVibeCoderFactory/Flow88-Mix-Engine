from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from analyzer import (
    SUPPORTED_AUDIO_SUFFIXES,
    AudioInputFile,
    AudioInputDiscovery,
    AudioLibraryScan,
    discover_audio_input,
    scan_audio_library,
)
from file_manager import (
    FileManagerError,
    delete_managed_file,
    describe_managed_file,
    ensure_managed_dir,
    list_managed_files,
    rename_managed_file,
    require_existing_file,
    resolve_managed_path,
)
from main import (
    INPUT_DIR,
    LEGACY_MASTER_MIX_FILENAME,
    MASTER_MIX_FILENAME,
    OUTPUT_DIR,
    TRACKLIST_FILENAME,
    VIDEO_INPUT_DIR,
    ensure_runtime_directories,
)
from mixer import DEFAULT_CROSSFADE_SECONDS, build_timeline, render_mix
from models import TrackAnalysis, VideoAnalysis
from render_logging import close_render_logger, create_render_logger, log_structured
from runtime_config import get_runtime_settings
from tracklist import write_tracklist
from project_persistence import (
    autosave_project_path,
    ensure_projects_dir,
    list_projects,
    load_project,
    resolve_project_path,
    save_project,
    user_projects_dir,
)
from video_processor import (
    DEFAULT_RENDER_PROFILE,
    DEFAULT_TRANSITION_CURVE,
    DEFAULT_TRANSITION_DURATION_SECONDS,
    DEFAULT_TRANSITION_ENABLED,
    DEFAULT_TRANSITION_TYPE,
    PREVIEW_OUTPUT_FILENAME,
    RENDER_PROFILES,
    analyze_video_directory,
    get_ffmpeg_capabilities,
    run_render_preflight,
    render_final_video,
)


class TrackDTO(BaseModel):
    file_name: str
    title: str
    artist: str
    bpm: float | None
    musical_key: str | None
    harmonic_key: str | None
    duration: float
    duration_seconds: float
    trim_start_seconds: float
    trim_end_seconds: float


class TrackListResponse(BaseModel):
    tracks: list[TrackDTO]


class VideoDTO(BaseModel):
    file_name: str
    duration_seconds: float
    width: int | None
    height: int | None
    frame_rate: float | None


class VideoListResponse(BaseModel):
    videos: list[VideoDTO]


class MixRequest(BaseModel):
    tracks: list[str] = Field(..., min_length=1)


class MixResponse(BaseModel):
    mix_output_path: str
    tracklist_output_path: str
    tracks_rendered: int


class VideoItemDTO(BaseModel):
    file_name: str
    loop_count: int = Field(1, ge=1)


class TransitionConfigDTO(BaseModel):
    enabled: bool = Field(DEFAULT_TRANSITION_ENABLED)
    type: str = Field(DEFAULT_TRANSITION_TYPE)
    duration: float = Field(DEFAULT_TRANSITION_DURATION_SECONDS, ge=0.2, le=3.0)
    curve: str = Field(DEFAULT_TRANSITION_CURVE)


class GenerateVideoRequest(BaseModel):
    items: list[VideoItemDTO] | None = None
    render_profile: str = Field(DEFAULT_RENDER_PROFILE)
    transition: TransitionConfigDTO | None = None
    videos: list[str] | None = None
    loop_counts: dict[str, int] = Field(default_factory=dict)


class GenerateVideoJobResponse(BaseModel):
    job_id: str


class RenderPreflightResponse(BaseModel):
    ok: bool
    mode: str
    fps: int
    resolution: list[int]
    target_duration_seconds: float
    scene_count: int
    resolution_variants: int
    codec_variants: int
    transition_enabled: bool
    transition_type: str
    transition_duration_seconds: float
    transition_curve: str
    log_path: str


@dataclass(slots=True)
class JobProgress:
    status: str  # queued|running|done|error
    percent: float
    eta_seconds: float | None
    message: str | None
    output_path: str | None
    encoder: str | None
    log_path: str | None


class JobProgressResponse(BaseModel):
    status: str
    percent: float
    eta_seconds: float | None
    message: str | None
    output_path: str | None
    encoder: str | None
    log_path: str | None


class VideoRenderProfilesResponse(BaseModel):
    default_profile: str
    profiles: dict[str, dict[str, Any]]


class ProjectRenderSettingsDTO(BaseModel):
    render_profile: str = Field(DEFAULT_RENDER_PROFILE)
    interface_scale: float = Field(1.1, ge=0.9, le=1.5)


class ProjectFileDTO(BaseModel):
    format: str = Field("flowmix")
    version: int = Field(1)
    ordered_clips: list[str] = Field(default_factory=list)
    loop_counts: list[int] = Field(default_factory=list)
    transition: TransitionConfigDTO = Field(default_factory=TransitionConfigDTO)
    audio_file: str = Field("")
    render_settings: ProjectRenderSettingsDTO = Field(default_factory=ProjectRenderSettingsDTO)
    saved_at: str | None = None


class SaveProjectRequest(BaseModel):
    path: str | None = None
    project: ProjectFileDTO
    autosave: bool = False


class SaveProjectResponse(BaseModel):
    path: str
    file_name: str
    autosave: bool
    project_dir: str


class LoadProjectRequest(BaseModel):
    path: str


class LoadProjectResponse(BaseModel):
    path: str
    project: ProjectFileDTO


class ProjectListItemDTO(BaseModel):
    file_name: str
    path: str
    modified_at: str


class ProjectListResponse(BaseModel):
    project_dir: str
    projects: list[ProjectListItemDTO]


class ManagedFileDTO(BaseModel):
    file_name: str
    size_bytes: int
    modified_at: str
    extension: str
    status: str | None = None
    detail: str | None = None


class ManagedFileListResponse(BaseModel):
    directory: str
    files: list[ManagedFileDTO]


class RenameManagedFileRequest(BaseModel):
    old_name: str
    new_name: str


class ManagedFileActionResponse(BaseModel):
    directory: str
    file_name: str
    message: str
    size_bytes: int | None = None


class AutosaveStatusResponse(BaseModel):
    exists: bool
    path: str
    project: ProjectFileDTO | None = None


class HealthResponse(BaseModel):
    ok: bool
    service: str
    ffmpeg_available: bool
    ffprobe_available: bool
    encoder: str


class DiagnosticsResponse(BaseModel):
    service: str
    server_mode: str
    timestamp: str
    directories: dict[str, str]
    cors_origins: list[str]
    desktop_open_supported: bool
    ffmpeg: dict[str, Any]


LOGGER = logging.getLogger("flow88.server")
if not LOGGER.handlers:
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(logging.Formatter("%(levelname)s:%(name)s:%(message)s"))
    LOGGER.addHandler(stream_handler)
LOGGER.propagate = False
LOGGER.setLevel(logging.INFO)
RUNTIME_SETTINGS = get_runtime_settings()
PROJECT_ROOT = RUNTIME_SETTINGS.project_root
FRONTEND_DIR = RUNTIME_SETTINGS.frontend_dir
SOURCE_AUDIO_DIR = INPUT_DIR
FINAL_VIDEO_OUTPUT_FILENAME = "flow88_final_video.mov"
JOB_PROGRESS: dict[str, JobProgress] = {}
JOB_PROGRESS_LOCK = threading.Lock()
UPLOAD_CHUNK_SIZE_BYTES = 1024 * 1024


def _build_startup_diagnostics() -> dict[str, Any]:
    capabilities = get_ffmpeg_capabilities()
    return {
        "service": "Flow88 Mix Engine",
        "server_mode": "headless-fastapi",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "directories": {
            "project_root": str(PROJECT_ROOT),
            "frontend": str(FRONTEND_DIR),
            "input": str(INPUT_DIR),
            "video_input": str(VIDEO_INPUT_DIR),
            "output": str(OUTPUT_DIR),
            "projects": str(user_projects_dir().resolve()),
            "logs": str(RUNTIME_SETTINGS.logs_dir),
        },
        "cors_origins": list(RUNTIME_SETTINGS.cors_origins),
        "desktop_open_supported": hasattr(os, "startfile"),
        "ffmpeg": capabilities.as_dict(),
    }


def _log_startup_diagnostics() -> None:
    LOGGER.info("startup_diagnostics=%s", json.dumps(_build_startup_diagnostics(), sort_keys=True))


@asynccontextmanager
async def lifespan(_: FastAPI):
    ensure_runtime_directories()
    _log_startup_diagnostics()
    yield


app = FastAPI(title="Flow88 Mix Engine API", version="0.2.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(RUNTIME_SETTINGS.cors_origins),
    allow_credentials=RUNTIME_SETTINGS.cors_allow_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
)
if FRONTEND_DIR.exists():
    app.mount("/frontend", StaticFiles(directory=str(FRONTEND_DIR)), name="frontend")
app.mount("/input/videos", StaticFiles(directory=str(VIDEO_INPUT_DIR), check_dir=False), name="input-videos")


def _track_to_dto(track: TrackAnalysis) -> TrackDTO:
    return TrackDTO(
        file_name=track.file_path.name,
        title=track.title,
        artist=track.artist,
        bpm=track.bpm,
        musical_key=track.musical_key,
        harmonic_key=track.harmonic_key,
        duration=track.duration,
        duration_seconds=track.duration_seconds,
        trim_start_seconds=track.trim_start_seconds,
        trim_end_seconds=track.trim_end_seconds,
    )


def _video_to_dto(video: VideoAnalysis) -> VideoDTO:
    return VideoDTO(
        file_name=video.file_path.name,
        duration_seconds=video.duration_seconds,
        width=video.width,
        height=video.height,
        frame_rate=video.frame_rate,
    )


def _audio_input_file_to_managed_dto(entry: AudioInputFile) -> ManagedFileDTO:
    return ManagedFileDTO(
        file_name=entry.file_path.name,
        size_bytes=entry.size_bytes,
        modified_at=entry.modified_at,
        extension=entry.extension,
        status=entry.status,
        detail=entry.detail,
    )


def _log_audio_discovery(event_name: str, discovery: AudioInputDiscovery) -> None:
    LOGGER.info(
        "%s input_dir=%s total_files=%d supported_files=%d unsupported_files=%d files=%s",
        event_name,
        discovery.input_dir,
        len(discovery.files),
        len(discovery.supported_files),
        len(discovery.unsupported_files),
        [entry.file_path.name for entry in discovery.files],
    )


def _log_audio_scan(scan: AudioLibraryScan) -> None:
    LOGGER.info(
        "audio_track_scan input_dir=%s total_files=%d supported_files=%d track_count=%d rejected_files=%d files=%s tracks=%s rejected=%s",
        scan.discovery.input_dir,
        len(scan.discovery.files),
        len(scan.discovery.supported_files),
        len(scan.tracks),
        len(scan.rejected_files),
        [entry.file_path.name for entry in scan.discovery.files],
        [track.file_path.name for track in scan.tracks],
        [f"{entry.file_path.name}: {entry.detail}" for entry in scan.rejected_files],
    )


def _build_track_scan_error(scan: AudioLibraryScan) -> str:
    input_dir = scan.discovery.input_dir
    if not scan.discovery.files:
        return f"No audio files found in: {input_dir}"

    if scan.rejected_files:
        rejected_preview = "; ".join(
            f"{entry.file_path.name}: {entry.detail}" for entry in scan.rejected_files[:3]
        )
        if scan.discovery.unsupported_files:
            unsupported_preview = ", ".join(
                entry.file_path.name for entry in scan.discovery.unsupported_files[:3]
            )
            return (
                f"Files were found in {input_dir}, but none were usable. "
                f"Rejected during analysis: {rejected_preview}. "
                f"Unsupported files: {unsupported_preview}."
            )
        return f"Files were found in {input_dir}, but audio analysis rejected them: {rejected_preview}"

    unsupported_preview = ", ".join(entry.file_path.name for entry in scan.discovery.unsupported_files[:5])
    supported_suffixes = ", ".join(sorted(SUPPORTED_AUDIO_SUFFIXES))
    return (
        f"Files were found in {input_dir}, but none matched the supported audio types "
        f"({supported_suffixes}). Found: {unsupported_preview}"
    )


def _load_tracks() -> list[TrackAnalysis]:
    ensure_runtime_directories()
    scan = scan_audio_library(INPUT_DIR)
    _log_audio_scan(scan)
    if not scan.tracks:
        raise HTTPException(status_code=404, detail=_build_track_scan_error(scan))
    return scan.tracks


def _load_videos() -> list[VideoAnalysis]:
    ensure_runtime_directories()
    VIDEO_INPUT_DIR.mkdir(parents=True, exist_ok=True)
    videos = analyze_video_directory(VIDEO_INPUT_DIR)
    if not videos:
        raise HTTPException(status_code=404, detail=f"No supported video files found in: {VIDEO_INPUT_DIR.resolve()}")
    return videos


def _performance_profile_ready() -> tuple[bool, str]:
    capabilities = get_ffmpeg_capabilities()
    if capabilities.nvenc_runtime_available:
        return True, "NVENC runtime probe succeeded."
    if capabilities.ffmpeg_path is None:
        return False, "ffmpeg not found in PATH."
    if capabilities.ffprobe_path is None:
        return False, "ffprobe not found in PATH."
    if capabilities.nvenc_available and capabilities.nvenc_runtime_error:
        return False, f"FFmpeg exposes h264_nvenc, but the runtime probe failed: {capabilities.nvenc_runtime_error}"
    if capabilities.nvenc_available and not capabilities.cuda_hwaccel_available:
        return False, "FFmpeg exposes h264_nvenc, but CUDA hwaccel is not reported by this build."
    if capabilities.cuda_hwaccel_available and not capabilities.nvenc_available:
        return False, "CUDA hwaccel is reported, but FFmpeg was built without h264_nvenc."
    return False, "Neither usable NVENC nor CUDA hwaccel is available."


def _ensure_performance_profile_support(render_profile: str) -> None:
    if render_profile != "performance":
        return

    ready, reason = _performance_profile_ready()
    if ready:
        return

    raise HTTPException(
        status_code=400,
        detail=f"Performance render profile requires NVENC on this server. {reason}",
    )


def _resolve_render_audio_input() -> Path:
    ensure_runtime_directories()
    primary_path = OUTPUT_DIR / MASTER_MIX_FILENAME
    if primary_path.exists():
        return primary_path

    legacy_path = OUTPUT_DIR / LEGACY_MASTER_MIX_FILENAME
    if legacy_path.exists():
        return legacy_path

    return primary_path


def _resolve_ordered_tracks(all_tracks: list[TrackAnalysis], ordered_file_names: list[str]) -> list[TrackAnalysis]:
    track_by_file_name = {track.file_path.name: track for track in all_tracks}
    ordered_tracks: list[TrackAnalysis] = []
    seen: set[str] = set()

    for file_name in ordered_file_names:
        if file_name in seen:
            raise HTTPException(status_code=400, detail=f"Duplicate track name in request: {file_name}")
        seen.add(file_name)

        track = track_by_file_name.get(file_name)
        if track is None:
            raise HTTPException(status_code=400, detail=f"Unknown track name: {file_name}")
        ordered_tracks.append(track)

    return ordered_tracks


def _normalize_video_items_request(request: GenerateVideoRequest) -> list[VideoItemDTO]:
    if request.items is not None:
        if not request.items:
            raise HTTPException(status_code=400, detail="The 'items' list must contain at least one clip.")
        return request.items

    legacy_videos = request.videos or []
    if not legacy_videos:
        raise HTTPException(status_code=400, detail="Provide 'items' or legacy 'videos' in the request body.")

    unknown_loop_keys = sorted(set(request.loop_counts) - set(legacy_videos))
    if unknown_loop_keys:
        unknown_list = ", ".join(unknown_loop_keys)
        raise HTTPException(status_code=400, detail=f"Unknown loop_counts video name(s): {unknown_list}")

    normalized_items: list[VideoItemDTO] = []
    for file_name in legacy_videos:
        loop_count = request.loop_counts.get(file_name, 1)
        if loop_count <= 0:
            raise HTTPException(status_code=400, detail=f"Loop count must be positive for video: {file_name}")
        normalized_items.append(VideoItemDTO(file_name=file_name, loop_count=loop_count))

    return normalized_items


def _resolve_video_items(all_videos: list[VideoAnalysis], items: list[VideoItemDTO]) -> list[tuple[Path, int]]:
    video_index = {video.file_path.name: video for video in all_videos}
    ordered_paths: list[tuple[Path, int]] = []

    for item in items:
        video = video_index.get(item.file_name)
        if video is None:
            raise HTTPException(status_code=400, detail=f"Unknown video name: {item.file_name}")
        ordered_paths.append((video.file_path, item.loop_count))

    return ordered_paths


def _normalize_render_profile(profile_name: str) -> str:
    normalized = profile_name.strip().lower()
    if normalized not in RENDER_PROFILES:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown render_profile '{profile_name}'. Available: {', '.join(sorted(RENDER_PROFILES))}",
        )
    return normalized


def _normalize_transition_request(transition: TransitionConfigDTO | None) -> dict[str, object]:
    source = transition or TransitionConfigDTO()
    transition_type = source.type.strip().lower() or DEFAULT_TRANSITION_TYPE
    if not transition_type or any((not char.isalnum()) and char != "_" for char in transition_type):
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported transition type '{source.type}'. Use letters, numbers, and underscores only.",
        )
    curve = source.curve.strip().lower() or DEFAULT_TRANSITION_CURVE
    if curve not in {"linear", "easein", "easeout"}:
        raise HTTPException(status_code=400, detail=f"Unsupported transition curve '{source.curve}'.")

    return {
        "enabled": bool(source.enabled),
        "type": transition_type,
        "duration": float(source.duration),
        "curve": curve,
    }


def _normalize_project_payload(project: ProjectFileDTO) -> dict[str, Any]:
    ordered_clips = [str(file_name).strip() for file_name in project.ordered_clips]
    if any(not file_name for file_name in ordered_clips):
        raise HTTPException(status_code=400, detail="Project ordered_clips contains an empty file name.")

    loop_counts = [int(loop_count) for loop_count in project.loop_counts]
    if len(ordered_clips) != len(loop_counts):
        raise HTTPException(status_code=400, detail="Project ordered_clips and loop_counts length mismatch.")
    if any(loop_count <= 0 for loop_count in loop_counts):
        raise HTTPException(status_code=400, detail="Project loop_counts must be positive integers.")

    transition = _normalize_transition_request(project.transition)
    render_profile = _normalize_render_profile(project.render_settings.render_profile)
    interface_scale = float(project.render_settings.interface_scale)
    if interface_scale < 0.9 or interface_scale > 1.5:
        raise HTTPException(status_code=400, detail="Interface scale must be between 0.9 and 1.5.")

    saved_at = datetime.now(timezone.utc).isoformat()
    return {
        "format": "flowmix",
        "version": 1,
        "ordered_clips": ordered_clips,
        "loop_counts": loop_counts,
        "transition": transition,
        "audio_file": str(project.audio_file or "").strip(),
        "render_settings": {
            "render_profile": render_profile,
            "interface_scale": interface_scale,
        },
        "saved_at": saved_at,
    }


def _load_project_payload(path: str | Path) -> tuple[Path, ProjectFileDTO]:
    try:
        resolved_path = resolve_project_path(path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        loaded_payload = load_project(resolved_path.name)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        project = ProjectFileDTO.model_validate(loaded_payload)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid .flowmix payload: {exc}") from exc

    if len(project.ordered_clips) != len(project.loop_counts):
        raise HTTPException(status_code=400, detail="Project ordered_clips and loop_counts length mismatch.")
    if any(int(loop_count) <= 0 for loop_count in project.loop_counts):
        raise HTTPException(status_code=400, detail="Project loop_counts must be positive integers.")

    return resolved_path, project


def _managed_file_to_dto(entry) -> ManagedFileDTO:
    return ManagedFileDTO(
        file_name=entry.file_name,
        size_bytes=entry.size_bytes,
        modified_at=entry.modified_at,
        extension=entry.extension,
    )


def _raise_file_http_error(exc: FileManagerError) -> None:
    raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


def _projects_base_dir() -> Path:
    return ensure_projects_dir().resolve()


def _list_managed_directory(base_dir: Path) -> ManagedFileListResponse:
    resolved_dir = ensure_managed_dir(base_dir)
    files = list_managed_files(resolved_dir)
    return ManagedFileListResponse(
        directory=str(resolved_dir),
        files=[_managed_file_to_dto(entry) for entry in files],
    )


def _list_audio_input_directory(base_dir: Path) -> ManagedFileListResponse:
    resolved_dir = ensure_managed_dir(base_dir)
    discovery = discover_audio_input(resolved_dir)
    _log_audio_discovery("audio_input_file_manager_list", discovery)
    return ManagedFileListResponse(
        directory=str(discovery.input_dir),
        files=[_audio_input_file_to_managed_dto(entry) for entry in discovery.files],
    )


async def _save_uploaded_file(base_dir: Path, upload: UploadFile) -> ManagedFileDTO:
    resolved_dir = ensure_managed_dir(base_dir)
    temp_path = resolved_dir / f".upload-{uuid.uuid4().hex}.part"

    try:
        if not upload.filename:
            raise FileManagerError("Upload must include a file name.", status_code=400)

        target_path = resolve_managed_path(resolved_dir, upload.filename)
        if target_path.exists():
            raise FileManagerError(f"File already exists: {target_path.name}", status_code=409)

        total_bytes = 0
        with temp_path.open("xb") as temp_file:
            while True:
                chunk = await upload.read(UPLOAD_CHUNK_SIZE_BYTES)
                if not chunk:
                    break

                total_bytes += len(chunk)
                if total_bytes > RUNTIME_SETTINGS.max_upload_size_bytes:
                    raise FileManagerError(
                        f"Upload exceeds the {RUNTIME_SETTINGS.max_upload_size_bytes} byte limit.",
                        status_code=413,
                    )
                temp_file.write(chunk)

        temp_path.replace(target_path)
        return _managed_file_to_dto(describe_managed_file(target_path))
    except FileManagerError:
        if temp_path.exists():
            temp_path.unlink(missing_ok=True)
        raise
    except OSError as exc:
        if temp_path.exists():
            temp_path.unlink(missing_ok=True)
        raise FileManagerError(f"Failed to save upload: {exc}", status_code=500) from exc
    finally:
        await upload.close()


def _delete_managed_directory_file(base_dir: Path, file_name: str) -> ManagedFileActionResponse:
    try:
        resolved_dir = ensure_managed_dir(base_dir)
        deleted_path = delete_managed_file(resolved_dir, file_name)
    except FileManagerError as exc:
        _raise_file_http_error(exc)

    return ManagedFileActionResponse(
        directory=str(resolved_dir),
        file_name=deleted_path.name,
        message="File deleted.",
    )


def _rename_managed_directory_file(base_dir: Path, request: RenameManagedFileRequest) -> ManagedFileActionResponse:
    try:
        resolved_dir = ensure_managed_dir(base_dir)
        renamed_path = rename_managed_file(resolved_dir, request.old_name, request.new_name)
    except FileManagerError as exc:
        _raise_file_http_error(exc)

    return ManagedFileActionResponse(
        directory=str(resolved_dir),
        file_name=renamed_path.name,
        message="File renamed.",
    )


def _download_managed_directory_file(base_dir: Path, file_name: str) -> FileResponse:
    try:
        resolved_dir = ensure_managed_dir(base_dir)
        target_path = require_existing_file(resolved_dir, file_name)
    except FileManagerError as exc:
        _raise_file_http_error(exc)

    return FileResponse(
        target_path,
        filename=target_path.name,
        content_disposition_type="attachment",
    )


def _update_job_progress(job_id: str, **updates: Any) -> None:
    with JOB_PROGRESS_LOCK:
        progress = JOB_PROGRESS.get(job_id)
        if progress is None:
            return
        for field_name, field_value in updates.items():
            setattr(progress, field_name, field_value)


def _read_job_progress(job_id: str) -> JobProgress:
    with JOB_PROGRESS_LOCK:
        progress = JOB_PROGRESS.get(job_id)
        if progress is None:
            raise HTTPException(status_code=404, detail=f"Unknown video job ID: {job_id}")
        return JobProgress(**asdict(progress))


def _run_video_render_job(
    job_id: str,
    audio_input_path: Path,
    ordered_video_paths: list[tuple[Path, int]],
    output_video_path: Path,
    render_profile: str,
    transition_config: dict[str, object],
) -> None:
    logger, log_path = create_render_logger()
    resolved_log_path = str(log_path.resolve())
    try:
        _update_job_progress(
            job_id,
            status="running",
            percent=0.0,
            eta_seconds=None,
            message="Starting render pipeline...",
            output_path=None,
            encoder=None,
            log_path=resolved_log_path,
        )
        log_structured(
            logger,
            "video_job_start",
            job_id=job_id,
            render_profile=render_profile,
            output_path=str(output_video_path.resolve()),
        )

        def on_progress(percent_0_1: float, eta_seconds: float) -> None:
            bounded_percent = max(0.0, min(1.0, percent_0_1)) * 100.0
            _update_job_progress(
                job_id,
                status="running",
                percent=bounded_percent,
                eta_seconds=max(0.0, eta_seconds),
                message="Rendering video...",
                log_path=resolved_log_path,
            )

        try:
            rendered_output_path, encoder = render_final_video(
                audio_mix_path=audio_input_path,
                ordered_video_paths=ordered_video_paths,
                output_path=output_video_path,
                render_profile=render_profile,
                transition_config=transition_config,
                on_progress=on_progress,
                logger=logger,
            )
        except Exception as exc:  # Keep job lifecycle resilient to all runtime issues.
            logger.exception("Render failed")
            _update_job_progress(
                job_id,
                status="error",
                eta_seconds=None,
                message=f"{exc} (log: {resolved_log_path})",
                log_path=resolved_log_path,
            )
            return

        _update_job_progress(
            job_id,
            status="done",
            percent=100.0,
            eta_seconds=0.0,
            message="Video render complete.",
            output_path=str(Path(rendered_output_path).resolve()),
            encoder=encoder,
            log_path=resolved_log_path,
        )
        log_structured(
            logger,
            "video_job_complete",
            job_id=job_id,
            output_path=str(Path(rendered_output_path).resolve()),
            encoder=encoder,
        )
    finally:
        close_render_logger(logger)


@app.get("/", include_in_schema=False)
def get_index() -> FileResponse:
    index_path = FRONTEND_DIR / "index.html"
    if not index_path.exists():
        raise HTTPException(status_code=404, detail=f"Frontend not found at: {index_path}")
    return FileResponse(index_path)


@app.get("/health", response_model=HealthResponse)
def get_health() -> HealthResponse:
    ensure_runtime_directories()
    capabilities = get_ffmpeg_capabilities()
    return HealthResponse(
        ok=True,
        service="Flow88 Mix Engine",
        ffmpeg_available=capabilities.ffmpeg_path is not None,
        ffprobe_available=capabilities.ffprobe_path is not None,
        encoder=capabilities.preferred_h264_encoder,
    )


@app.get("/diagnostics", response_model=DiagnosticsResponse)
def get_diagnostics() -> DiagnosticsResponse:
    diagnostics = _build_startup_diagnostics()
    return DiagnosticsResponse(**diagnostics)


@app.get("/api/files/input", response_model=ManagedFileListResponse)
def get_input_files() -> ManagedFileListResponse:
    return _list_audio_input_directory(INPUT_DIR)


@app.post("/api/files/input/upload", response_model=ManagedFileActionResponse)
async def post_input_upload(file: UploadFile = File(...)) -> ManagedFileActionResponse:
    try:
        uploaded_file = await _save_uploaded_file(INPUT_DIR, file)
    except FileManagerError as exc:
        _raise_file_http_error(exc)

    LOGGER.info(
        "audio_input_upload input_dir=%s file_name=%s size_bytes=%d",
        INPUT_DIR.resolve(),
        uploaded_file.file_name,
        uploaded_file.size_bytes or 0,
    )

    return ManagedFileActionResponse(
        directory=str(INPUT_DIR.resolve()),
        file_name=uploaded_file.file_name,
        message="Upload complete.",
        size_bytes=uploaded_file.size_bytes,
    )


@app.delete("/api/files/input/{filename}", response_model=ManagedFileActionResponse)
def delete_input_file(filename: str) -> ManagedFileActionResponse:
    return _delete_managed_directory_file(INPUT_DIR, filename)


@app.post("/api/files/input/rename", response_model=ManagedFileActionResponse)
def post_input_rename(request: RenameManagedFileRequest) -> ManagedFileActionResponse:
    return _rename_managed_directory_file(INPUT_DIR, request)


@app.get("/api/files/input/videos", response_model=ManagedFileListResponse)
def get_input_video_files() -> ManagedFileListResponse:
    return _list_managed_directory(VIDEO_INPUT_DIR)


@app.post("/api/files/input/videos/upload", response_model=ManagedFileActionResponse)
async def post_input_video_upload(file: UploadFile = File(...)) -> ManagedFileActionResponse:
    try:
        uploaded_file = await _save_uploaded_file(VIDEO_INPUT_DIR, file)
    except FileManagerError as exc:
        _raise_file_http_error(exc)

    return ManagedFileActionResponse(
        directory=str(VIDEO_INPUT_DIR.resolve()),
        file_name=uploaded_file.file_name,
        message="Upload complete.",
        size_bytes=uploaded_file.size_bytes,
    )


@app.delete("/api/files/input/videos/{filename}", response_model=ManagedFileActionResponse)
def delete_input_video_file(filename: str) -> ManagedFileActionResponse:
    return _delete_managed_directory_file(VIDEO_INPUT_DIR, filename)


@app.post("/api/files/input/videos/rename", response_model=ManagedFileActionResponse)
def post_input_video_rename(request: RenameManagedFileRequest) -> ManagedFileActionResponse:
    return _rename_managed_directory_file(VIDEO_INPUT_DIR, request)


@app.get("/api/files/output", response_model=ManagedFileListResponse)
def get_output_files() -> ManagedFileListResponse:
    return _list_managed_directory(OUTPUT_DIR)


@app.get("/api/files/output/{filename}/download")
def download_output_file(filename: str) -> FileResponse:
    return _download_managed_directory_file(OUTPUT_DIR, filename)


@app.delete("/api/files/output/{filename}", response_model=ManagedFileActionResponse)
def delete_output_file(filename: str) -> ManagedFileActionResponse:
    return _delete_managed_directory_file(OUTPUT_DIR, filename)


@app.post("/api/files/output/rename", response_model=ManagedFileActionResponse)
def post_output_rename(request: RenameManagedFileRequest) -> ManagedFileActionResponse:
    return _rename_managed_directory_file(OUTPUT_DIR, request)


@app.get("/api/files/projects", response_model=ManagedFileListResponse)
def get_project_files() -> ManagedFileListResponse:
    return _list_managed_directory(_projects_base_dir())


@app.get("/api/files/projects/{filename}/download")
def download_project_file(filename: str) -> FileResponse:
    return _download_managed_directory_file(_projects_base_dir(), filename)


@app.delete("/api/files/projects/{filename}", response_model=ManagedFileActionResponse)
def delete_project_file(filename: str) -> ManagedFileActionResponse:
    return _delete_managed_directory_file(_projects_base_dir(), filename)


@app.post("/api/files/projects/rename", response_model=ManagedFileActionResponse)
def post_project_rename(request: RenameManagedFileRequest) -> ManagedFileActionResponse:
    return _rename_managed_directory_file(_projects_base_dir(), request)


@app.get("/tracks", response_model=TrackListResponse)
def get_tracks() -> TrackListResponse:
    tracks = _load_tracks()
    return TrackListResponse(tracks=[_track_to_dto(track) for track in tracks])


@app.get("/videos", response_model=VideoListResponse)
def get_videos() -> VideoListResponse:
    videos = _load_videos()
    return VideoListResponse(videos=[_video_to_dto(video) for video in videos])


@app.post("/mix", response_model=MixResponse)
def post_mix(request: MixRequest) -> MixResponse:
    all_tracks = _load_tracks()
    ordered_tracks = _resolve_ordered_tracks(all_tracks, request.tracks)

    timeline = build_timeline(ordered_tracks, crossfade_seconds=DEFAULT_CROSSFADE_SECONDS)
    mix_output_path = OUTPUT_DIR / MASTER_MIX_FILENAME
    tracklist_output_path = OUTPUT_DIR / TRACKLIST_FILENAME
    logger, log_path = create_render_logger()
    resolved_log_path = str(log_path.resolve())
    log_structured(
        logger,
        "audio_job_start",
        track_sequence=[track.file_path.name for track in ordered_tracks],
        output_path=str(mix_output_path.resolve()),
    )

    try:
        rendered_mix_path = render_mix(
            ordered_tracks,
            output_path=mix_output_path,
            crossfade_seconds=DEFAULT_CROSSFADE_SECONDS,
            logger=logger,
        )
        rendered_tracklist_path = write_tracklist(timeline, output_path=tracklist_output_path)
    except RuntimeError as exc:
        logger.exception("Render failed")
        raise HTTPException(status_code=500, detail=f"{exc} (log: {resolved_log_path})") from exc
    finally:
        close_render_logger(logger)

    return MixResponse(
        mix_output_path=str(Path(rendered_mix_path).resolve()),
        tracklist_output_path=str(Path(rendered_tracklist_path).resolve()),
        tracks_rendered=len(ordered_tracks),
    )


@app.post("/render-preflight", response_model=RenderPreflightResponse)
def post_render_preflight(request: GenerateVideoRequest) -> RenderPreflightResponse:
    requested_items = _normalize_video_items_request(request)
    render_profile = _normalize_render_profile(request.render_profile)
    transition_config = _normalize_transition_request(request.transition)
    _ensure_performance_profile_support(render_profile)
    all_videos = _load_videos()
    ordered_video_paths = _resolve_video_items(all_videos, requested_items)

    audio_input_path = _resolve_render_audio_input()
    if not audio_input_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Audio source not found: {audio_input_path.resolve()}",
        )

    logger, log_path = create_render_logger()
    resolved_log_path = str(log_path.resolve())
    try:
        preflight = run_render_preflight(
            audio_mix_path=audio_input_path,
            ordered_video_paths=ordered_video_paths,
            render_profile=render_profile,
            transition_config=transition_config,
            logger=logger,
        )
    except Exception as exc:
        logger.exception("Render failed")
        raise HTTPException(status_code=400, detail=f"{exc} (log: {resolved_log_path})") from exc
    finally:
        close_render_logger(logger)

    return RenderPreflightResponse(
        ok=bool(preflight["ok"]),
        mode=str(preflight["mode"]),
        fps=int(preflight["fps"]),
        resolution=[int(preflight["resolution"][0]), int(preflight["resolution"][1])],
        target_duration_seconds=float(preflight["target_duration_seconds"]),
        scene_count=int(preflight["scene_count"]),
        resolution_variants=int(preflight["resolution_variants"]),
        codec_variants=int(preflight["codec_variants"]),
        transition_enabled=bool(preflight["transition_enabled"]),
        transition_type=str(preflight["transition_type"]),
        transition_duration_seconds=float(preflight["transition_duration_seconds"]),
        transition_curve=str(preflight["transition_curve"]),
        log_path=resolved_log_path,
    )


@app.post("/generate-video", response_model=GenerateVideoJobResponse)
def post_generate_video(request: GenerateVideoRequest) -> GenerateVideoJobResponse:
    requested_items = _normalize_video_items_request(request)
    render_profile = _normalize_render_profile(request.render_profile)
    transition_config = _normalize_transition_request(request.transition)
    _ensure_performance_profile_support(render_profile)
    all_videos = _load_videos()
    ordered_video_paths = _resolve_video_items(all_videos, requested_items)

    audio_input_path = _resolve_render_audio_input()
    if not audio_input_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Audio source not found: {audio_input_path.resolve()}",
        )

    job_id = uuid.uuid4().hex
    output_video_path = OUTPUT_DIR / f"{Path(FINAL_VIDEO_OUTPUT_FILENAME).stem}_{job_id[:8]}.mov"

    with JOB_PROGRESS_LOCK:
        JOB_PROGRESS[job_id] = JobProgress(
            status="queued",
            percent=0.0,
            eta_seconds=None,
            message="Queued",
            output_path=None,
            encoder=None,
            log_path=None,
        )

    worker = threading.Thread(
        target=_run_video_render_job,
        args=(job_id, audio_input_path, ordered_video_paths, output_video_path, render_profile, transition_config),
        daemon=True,
    )
    worker.start()

    return GenerateVideoJobResponse(job_id=job_id)


@app.post("/generate-preview", response_model=GenerateVideoJobResponse)
def post_generate_preview(request: GenerateVideoRequest) -> GenerateVideoJobResponse:
    requested_items = _normalize_video_items_request(request)
    transition_config = _normalize_transition_request(request.transition)
    all_videos = _load_videos()
    ordered_video_paths = _resolve_video_items(all_videos, requested_items)

    audio_input_path = _resolve_render_audio_input()
    if not audio_input_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Audio source not found: {audio_input_path.resolve()}",
        )

    job_id = uuid.uuid4().hex
    output_video_path = OUTPUT_DIR / PREVIEW_OUTPUT_FILENAME

    with JOB_PROGRESS_LOCK:
        JOB_PROGRESS[job_id] = JobProgress(
            status="queued",
            percent=0.0,
            eta_seconds=None,
            message="Queued preview render",
            output_path=None,
            encoder=None,
            log_path=None,
        )

    worker = threading.Thread(
        target=_run_video_render_job,
        args=(job_id, audio_input_path, ordered_video_paths, output_video_path, "preview", transition_config),
        daemon=True,
    )
    worker.start()

    return GenerateVideoJobResponse(job_id=job_id)


@app.get("/video-jobs/{job_id}", response_model=JobProgressResponse)
def get_video_job(job_id: str) -> JobProgressResponse:
    progress = _read_job_progress(job_id)
    return JobProgressResponse(
        status=progress.status,
        percent=max(0.0, min(100.0, progress.percent)),
        eta_seconds=progress.eta_seconds,
        message=progress.message,
        output_path=progress.output_path,
        encoder=progress.encoder,
        log_path=progress.log_path,
    )


@app.get("/video-render-profiles", response_model=VideoRenderProfilesResponse)
def get_video_render_profiles() -> VideoRenderProfilesResponse:
    profiles: dict[str, dict[str, Any]] = {}
    for name, profile in RENDER_PROFILES.items():
        width, height = profile["resolution"]
        profiles[name] = {
            "fps": int(profile["fps"]),
            "resolution": [int(width), int(height)],
            "nvenc_preset": str(profile["nvenc_preset"]),
            "cq": int(profile["cq"]),
            "crossfade": float(profile["crossfade"]),
        }

    return VideoRenderProfilesResponse(default_profile=DEFAULT_RENDER_PROFILE, profiles=profiles)


@app.get("/projects", response_model=ProjectListResponse)
def get_projects() -> ProjectListResponse:
    projects_dir = ensure_projects_dir().resolve()
    project_items: list[ProjectListItemDTO] = []
    for path in list_projects():
        stat = path.stat()
        project_items.append(
            ProjectListItemDTO(
                file_name=path.name,
                path=str(path.resolve()),
                modified_at=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
            )
        )

    return ProjectListResponse(
        project_dir=str(projects_dir),
        projects=project_items,
    )


@app.post("/project/save", response_model=SaveProjectResponse)
def post_save_project(request: SaveProjectRequest) -> SaveProjectResponse:
    ensure_projects_dir()
    payload = _normalize_project_payload(request.project)
    if request.autosave:
        target_name = autosave_project_path().name
    else:
        requested_path = str(request.path or "").strip()
        if not requested_path:
            raise HTTPException(status_code=400, detail="Project path is required for manual saves.")
        target_name = requested_path

    try:
        saved_path = save_project(target_name, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return SaveProjectResponse(
        path=str(saved_path.resolve()),
        file_name=saved_path.name,
        autosave=bool(request.autosave),
        project_dir=str(user_projects_dir().resolve()),
    )


@app.post("/project/load", response_model=LoadProjectResponse)
def post_load_project(request: LoadProjectRequest) -> LoadProjectResponse:
    _, project = _load_project_payload(request.path)
    resolved_path = resolve_project_path(request.path)
    return LoadProjectResponse(path=str(resolved_path), project=project)


@app.get("/project/autosave", response_model=AutosaveStatusResponse)
def get_project_autosave() -> AutosaveStatusResponse:
    autosave_path = autosave_project_path()
    if not autosave_path.exists():
        return AutosaveStatusResponse(exists=False, path=str(autosave_path), project=None)

    try:
        _, project = _load_project_payload(autosave_path.name)
    except HTTPException:
        return AutosaveStatusResponse(exists=True, path=str(autosave_path), project=None)
    return AutosaveStatusResponse(exists=True, path=str(autosave_path), project=project)


@app.get("/open-output")
def open_output() -> dict[str, str]:
    ensure_runtime_directories()
    resolved_output_dir = OUTPUT_DIR.resolve()

    try:
        os.startfile(str(resolved_output_dir))
    except AttributeError as exc:
        raise HTTPException(
            status_code=501,
            detail=f"Opening folders is only supported on Windows/local desktop runs. Use the mounted output directory: {resolved_output_dir}",
        ) from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Failed to open output directory: {exc}") from exc

    return {"status": "ok", "output_dir": str(resolved_output_dir)}


@app.get("/open-audio-source")
def open_audio_source() -> dict[str, str]:
    ensure_runtime_directories()
    resolved_audio_dir = SOURCE_AUDIO_DIR.resolve()

    try:
        os.startfile(str(resolved_audio_dir))
    except AttributeError as exc:
        raise HTTPException(
            status_code=501,
            detail=f"Opening folders is only supported on Windows/local desktop runs. Use the mounted audio directory: {resolved_audio_dir}",
        ) from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Failed to open source audio directory: {exc}") from exc

    return {"status": "ok", "audio_source_dir": str(resolved_audio_dir)}


@app.get("/open-video-source")
def open_video_source() -> dict[str, str]:
    ensure_runtime_directories()
    VIDEO_INPUT_DIR.mkdir(parents=True, exist_ok=True)
    resolved_video_dir = VIDEO_INPUT_DIR.resolve()

    try:
        os.startfile(str(resolved_video_dir))
    except AttributeError as exc:
        raise HTTPException(
            status_code=501,
            detail=f"Opening folders is only supported on Windows/local desktop runs. Use the mounted video directory: {resolved_video_dir}",
        ) from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Failed to open source video directory: {exc}") from exc

    return {"status": "ok", "video_source_dir": str(resolved_video_dir)}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=RUNTIME_SETTINGS.host, port=RUNTIME_SETTINGS.port)
