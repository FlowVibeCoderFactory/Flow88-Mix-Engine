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
from models import TrackAnalysis
from tracklist import write_tracklist


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


class MixRequest(BaseModel):
    tracks: list[str] = Field(..., min_length=1)


class MixResponse(BaseModel):
    mix_output_path: str
    tracklist_output_path: str
    tracks_rendered: int


app = FastAPI(title="Flow88 Mix Engine API", version="0.1.0")
PROJECT_ROOT = Path(__file__).resolve().parent
FRONTEND_DIR = PROJECT_ROOT / "frontend"
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
if FRONTEND_DIR.exists():
    app.mount("/frontend", StaticFiles(directory=str(FRONTEND_DIR)), name="frontend")


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


def _load_tracks() -> list[TrackAnalysis]:
    ensure_runtime_directories()
    tracks = analyze_directory(INPUT_DIR)
    if not tracks:
        raise HTTPException(status_code=404, detail=f"No supported audio files found in: {INPUT_DIR.resolve()}")
    return tracks


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
