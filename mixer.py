from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from models import TimelineEntry, TrackAnalysis


DEFAULT_CROSSFADE_SECONDS = 15.0
DEFAULT_WAV_CODEC = "pcm_s24le"
DEFAULT_CROSSFADE_CURVE = "tri"
CONCAT_INPUTS_FILENAME = "inputs.txt"


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

    if len(track_data) == 1:
        track = track_data[0]
        trim_start = max(0.0, track.trim_start_seconds)
        trim_end = max(trim_start + 0.001, track.trim_end_seconds)
        filter_parts.append(f"[0:a]atrim=start={trim_start:.6f}:end={trim_end:.6f},asetpts=PTS-STARTPTS[t0]")
        return ";".join(filter_parts), "[t0]"

    split_labels = [f"s{index}" for index in range(len(track_data))]
    split_outputs = "".join([f"[{label}]" for label in split_labels])
    filter_parts.append(f"[0:a]asplit={len(track_data)}{split_outputs}")

    absolute_offset_seconds = 0.0
    for index, track in enumerate(track_data):
        trim_start = max(0.0, track.trim_start_seconds)
        trim_end = max(trim_start + 0.001, track.trim_end_seconds)
        absolute_trim_start = absolute_offset_seconds + trim_start
        absolute_trim_end = absolute_offset_seconds + trim_end
        filter_parts.append(
            f"[{split_labels[index]}]atrim=start={absolute_trim_start:.6f}:end={absolute_trim_end:.6f},asetpts=PTS-STARTPTS[t{index}]"
        )
        absolute_offset_seconds += max(0.0, track.duration_seconds)

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


def _escape_concat_path(file_path: Path) -> str:
    absolute_posix = str(file_path.resolve()).replace("\\", "/")
    return absolute_posix.replace("'", "\\'")


def _write_concat_inputs_file(input_paths: list[Path], concat_file_path: Path) -> None:
    concat_lines = [f"file '{_escape_concat_path(path)}'" for path in input_paths]
    concat_file_path.write_text("\n".join(concat_lines) + "\n", encoding="utf-8")


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

    concat_file_path = output.parent / CONCAT_INPUTS_FILENAME
    _write_concat_inputs_file([track.file_path for track in track_data], concat_file_path)

    filter_complex, final_label = _build_filtergraph(track_data, crossfade_seconds)

    command = [
        "ffmpeg",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat_file_path),
        "-filter_complex",
        f"{filter_complex};{final_label}loudnorm=I=-14:LRA=11:TP=-1.0:linear=true[out]",
        "-map",
        "[out]",
        "-vn",
        "-c:a",
        DEFAULT_WAV_CODEC,
        str(output),
    ]

    try:
        _run_ffmpeg(command, error_prefix="FFmpeg rendering failed")
    finally:
        if concat_file_path.exists():
            concat_file_path.unlink(missing_ok=True)

    return output
