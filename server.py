from __future__ import annotations

import os
from pathlib import Path

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
from tracklist import write_tracklist
from video_processor import analyze_video_directory, render_final_video


class TrackDTO(BaseModel):
    file_name: str
    title: str
    artist: str
    bpm: float | None
    musical_key: str | None
    harmonic_key: str | None
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


class GenerateVideoRequest(BaseModel):
    videos: list[str] = Field(..., min_length=1)


class GenerateVideoResponse(BaseModel):
    video_output_path: str
    audio_input_path: str
    clips_requested: int
    encoder: str


app = FastAPI(title="Flow88 Mix Engine API", version="0.1.0")
PROJECT_ROOT = Path(__file__).resolve().parent
FRONTEND_DIR = PROJECT_ROOT / "frontend"
SOURCE_AUDIO_DIR = INPUT_DIR
VIDEO_INPUT_DIR = INPUT_DIR / "videos"
FINAL_VIDEO_AUDIO_FILENAME = "final_mix.wav"
FINAL_VIDEO_OUTPUT_FILENAME = "flow88_final_video.mov"
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


def _resolve_ordered_videos(all_videos: list[VideoAnalysis], ordered_file_names: list[str]) -> list[VideoAnalysis]:
    video_by_file_name = {video.file_path.name: video for video in all_videos}
    ordered_videos: list[VideoAnalysis] = []
    seen: set[str] = set()

    for file_name in ordered_file_names:
        if file_name in seen:
            raise HTTPException(status_code=400, detail=f"Duplicate video name in request: {file_name}")
        seen.add(file_name)

        video = video_by_file_name.get(file_name)
        if video is None:
            raise HTTPException(status_code=400, detail=f"Unknown video name: {file_name}")
        ordered_videos.append(video)

    return ordered_videos


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

    try:
        rendered_mix_path = render_mix(
            ordered_tracks,
            output_path=mix_output_path,
            crossfade_seconds=DEFAULT_CROSSFADE_SECONDS,
        )
        rendered_tracklist_path = write_tracklist(timeline, output_path=tracklist_output_path)
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return MixResponse(
        mix_output_path=str(Path(rendered_mix_path).resolve()),
        tracklist_output_path=str(Path(rendered_tracklist_path).resolve()),
        tracks_rendered=len(ordered_tracks),
    )


@app.post("/generate-video", response_model=GenerateVideoResponse)
def post_generate_video(request: GenerateVideoRequest) -> GenerateVideoResponse:
    all_videos = _load_videos()
    ordered_videos = _resolve_ordered_videos(all_videos, request.videos)

    audio_input_path = OUTPUT_DIR / FINAL_VIDEO_AUDIO_FILENAME
    if not audio_input_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Audio source not found: {audio_input_path.resolve()}",
        )

    output_video_path = OUTPUT_DIR / FINAL_VIDEO_OUTPUT_FILENAME

    try:
        rendered_output_path, encoder = render_final_video(
            audio_mix_path=audio_input_path,
            ordered_video_paths=[video.file_path for video in ordered_videos],
            output_path=output_video_path,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return GenerateVideoResponse(
        video_output_path=str(Path(rendered_output_path).resolve()),
        audio_input_path=str(audio_input_path.resolve()),
        clips_requested=len(ordered_videos),
        encoder=encoder,
    )


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
