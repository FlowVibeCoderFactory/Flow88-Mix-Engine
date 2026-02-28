from __future__ import annotations
import shutil
import subprocess
from pathlib import Path
from models import TimelineEntry, TrackAnalysis

DEFAULT_CROSSFADE_SECONDS = 15.0
DEFAULT_WAV_CODEC = "pcm_s24le"
DEFAULT_CROSSFADE_CURVE = "tri"

# -----------------------------
# Timeline Logic
# -----------------------------

def compute_transition_durations(track_data: list[TrackAnalysis], crossfade_seconds: float) -> list[float]:
    transition_durations: list[float] = []
    for index in range(len(track_data) - 1):
        current_dur = track_data[index].trimmed_duration_seconds
        next_dur = track_data[index + 1].trimmed_duration_seconds
        transition_durations.append(min(crossfade_seconds, current_dur, next_dur))
    return transition_durations


def build_timeline(
    track_data: list[TrackAnalysis], crossfade_seconds: float = DEFAULT_CROSSFADE_SECONDS
) -> list[TimelineEntry]:
    if not track_data:
        return []

    transition_durations = compute_transition_durations(track_data, crossfade_seconds)
    timeline: list[TimelineEntry] = []
    absolute_start_seconds = 0.0

    for index, track in enumerate(track_data):
        timeline.append(TimelineEntry(absolute_start_seconds=max(0.0, absolute_start_seconds), track=track))
        if index < len(track_data) - 1:
            overlap = max(0.0, transition_durations[index])
            absolute_start_seconds += max(0.0, track.trimmed_duration_seconds - overlap)

    return timeline


# -----------------------------
# Filter Graph Builder
# -----------------------------

def _build_filtergraph(track_data: list[TrackAnalysis], crossfade_seconds: float) -> tuple[str, str]:
    filter_parts: list[str] = []
    
    for index, track in enumerate(track_data):
        trim_start = max(0.0, track.trim_start_seconds)
        trim_end = max(trim_start + 0.001, track.trim_end_seconds)
        filter_parts.append(
            f"[{index}:a]atrim=start={trim_start:.6f}:end={trim_end:.6f},asetpts=PTS-STARTPTS[t{index}]"
        )

    if len(track_data) == 1:
        return ";".join(filter_parts), "[t0]"

    transition_durations = compute_transition_durations(track_data, crossfade_seconds)
    current_label = "t0"
    
    for index in range(1, len(track_data)):
        target_label = f"mix{index}"
        duration = max(0.001, transition_durations[index - 1])
        filter_parts.append(
            f"[{current_label}][t{index}]acrossfade=d={duration:.6f}:c1={DEFAULT_CROSSFADE_CURVE}:c2={DEFAULT_CROSSFADE_CURVE}[{target_label}]"
        )
        current_label = target_label

    return ";".join(filter_parts), f"[{current_label}]"


# -----------------------------
# FFmpeg Runner
# -----------------------------

def _run_ffmpeg(command: list[str], error_prefix: str) -> None:
    try:
        subprocess.run(command, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.strip() if exc.stderr else ""
        raise RuntimeError(f"{error_prefix}: {stderr}") from exc


# -----------------------------
# Mix Renderer
# -----------------------------

def render_mix(
    track_data: list[TrackAnalysis],
    output_path: str | Path,
    crossfade_seconds: float = DEFAULT_CROSSFADE_SECONDS,
) -> Path:

    if not track_data:
        raise ValueError("No tracks available.")

    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg not found in PATH.")

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    filter_complex, final_label = _build_filtergraph(track_data, crossfade_seconds)

    command = ["ffmpeg", "-y"]

    # Use ORIGINAL files (no normalization stage)
    for track in track_data:
        command.extend(["-i", str(track.file_path)])

    # Apply crossfades → THEN loudness normalize final mix only
    command.extend([
        "-filter_complex",
        f"{filter_complex};{final_label}loudnorm=I=-14:LRA=11:TP=-1.0:linear=true[out]",
        "-map", "[out]",
        "-vn",
        "-c:a", DEFAULT_WAV_CODEC,
        str(output),
    ])

    _run_ffmpeg(command, error_prefix="FFmpeg rendering failed")

    return output