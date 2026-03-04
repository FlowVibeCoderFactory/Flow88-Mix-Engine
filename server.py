from __future__ import annotations

import os
import threading
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from analyzer import analyze_directory
from main import (
    INPUT_DIR,
    MASTER_MIX_FILENAME,
    OUTPUT_DIR,
    TRACKLIST_FILENAME,
    ensure_runtime_directories,
)
from mixer import DEFAULT_CROSSFADE_SECONDS, build_timeline, render_mix
from models import TrackAnalysis, VideoAnalysis
from render_logging import close_render_logger, create_render_logger, log_structured
from tracklist import write_tracklist
from video_processor import (
    DEFAULT_RENDER_PROFILE,
    DEFAULT_TRANSITION_CURVE,
    DEFAULT_TRANSITION_DURATION_SECONDS,
    DEFAULT_TRANSITION_ENABLED,
    DEFAULT_TRANSITION_TYPE,
    PREVIEW_OUTPUT_FILENAME,
    RENDER_PROFILES,
    analyze_video_directory,
    detect_gpu_pipeline,
    detect_h264_encoder,
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


app = FastAPI(title="Flow88 Mix Engine API", version="0.1.0")
PROJECT_ROOT = Path(__file__).resolve().parent
FRONTEND_DIR = PROJECT_ROOT / "frontend"
SOURCE_AUDIO_DIR = INPUT_DIR
VIDEO_INPUT_DIR = INPUT_DIR / "videos"
FINAL_VIDEO_AUDIO_FILENAME = "final_mix.wav"
FINAL_VIDEO_OUTPUT_FILENAME = "flow88_final_video.mov"
JOB_PROGRESS: dict[str, JobProgress] = {}
JOB_PROGRESS_LOCK = threading.Lock()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
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


def _load_tracks() -> list[TrackAnalysis]:
    ensure_runtime_directories()
    tracks = analyze_directory(INPUT_DIR)
    if not tracks:
        raise HTTPException(status_code=404, detail=f"No supported audio files found in: {INPUT_DIR.resolve()}")
    return tracks


def _load_videos() -> list[VideoAnalysis]:
    ensure_runtime_directories()
    VIDEO_INPUT_DIR.mkdir(parents=True, exist_ok=True)
    videos = analyze_video_directory(VIDEO_INPUT_DIR)
    if not videos:
        raise HTTPException(status_code=404, detail=f"No supported video files found in: {VIDEO_INPUT_DIR.resolve()}")
    return videos


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

    if render_profile == "performance":
        try:
            has_nvenc = detect_gpu_pipeline() and detect_h264_encoder() == "h264_nvenc"
        except RuntimeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not has_nvenc:
            raise HTTPException(
                status_code=400,
                detail="Performance render profile requires CUDA/NVENC GPU support, but no compatible GPU was detected.",
            )

    all_videos = _load_videos()
    ordered_video_paths = _resolve_video_items(all_videos, requested_items)

    audio_input_path = OUTPUT_DIR / FINAL_VIDEO_AUDIO_FILENAME
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

    if render_profile == "performance":
        try:
            has_nvenc = detect_gpu_pipeline() and detect_h264_encoder() == "h264_nvenc"
        except RuntimeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not has_nvenc:
            raise HTTPException(
                status_code=400,
                detail="Performance render profile requires CUDA/NVENC GPU support, but no compatible GPU was detected.",
            )

    all_videos = _load_videos()
    ordered_video_paths = _resolve_video_items(all_videos, requested_items)

    audio_input_path = OUTPUT_DIR / FINAL_VIDEO_AUDIO_FILENAME
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

    audio_input_path = OUTPUT_DIR / FINAL_VIDEO_AUDIO_FILENAME
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


@app.get("/open-output")
def open_output() -> dict[str, str]:
    ensure_runtime_directories()
    resolved_output_dir = OUTPUT_DIR.resolve()

    try:
        os.startfile(str(resolved_output_dir))
    except AttributeError as exc:
        raise HTTPException(status_code=501, detail="Opening folders is only supported on Windows.") from exc
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
        raise HTTPException(status_code=501, detail="Opening folders is only supported on Windows.") from exc
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
        raise HTTPException(status_code=501, detail="Opening folders is only supported on Windows.") from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Failed to open source video directory: {exc}") from exc

    return {"status": "ok", "video_source_dir": str(resolved_video_dir)}
