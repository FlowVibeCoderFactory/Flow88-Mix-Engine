from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import math
import shutil
import subprocess
import sys
from pathlib import Path

import librosa
import numpy as np
from mutagen import File as MutagenFile

from models import TrackAnalysis


SUPPORTED_AUDIO_SUFFIXES = {".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg"}
NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
KEY_PROFILE_MAJOR = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
KEY_PROFILE_MINOR = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])
CAMOLOT_MINOR = {
    "A": "8A",
    "A#": "3A",
    "B": "10A",
    "C": "5A",
    "C#": "12A",
    "D": "7A",
    "D#": "2A",
    "E": "9A",
    "F": "4A",
    "F#": "11A",
    "G": "6A",
    "G#": "1A",
}
CAMOLOT_MAJOR = {
    "A": "11B",
    "A#": "6B",
    "B": "1B",
    "C": "8B",
    "C#": "3B",
    "D": "10B",
    "D#": "5B",
    "E": "12B",
    "F": "7B",
    "F#": "2B",
    "G": "9B",
    "G#": "4B",
}


@dataclass(slots=True, frozen=True)
class AudioInputFile:
    file_path: Path
    size_bytes: int
    modified_at: str
    extension: str
    status: str
    detail: str | None = None


@dataclass(slots=True)
class AudioInputDiscovery:
    input_dir: Path
    files: list[AudioInputFile]
    supported_files: list[AudioInputFile]
    unsupported_files: list[AudioInputFile]


@dataclass(slots=True)
class AudioLibraryScan:
    discovery: AudioInputDiscovery
    tracks: list[TrackAnalysis]
    rejected_files: list[AudioInputFile]


def _describe_input_file(file_path: Path, status: str, detail: str | None = None) -> AudioInputFile:
    stat = file_path.stat()
    return AudioInputFile(
        file_path=file_path,
        size_bytes=int(stat.st_size),
        modified_at=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
        extension=file_path.suffix.lower(),
        status=status,
        detail=detail,
    )


def _is_temporary_upload_file(file_path: Path) -> bool:
    return file_path.name.startswith(".upload-") and file_path.name.endswith(".part")


def discover_audio_input(input_dir: Path) -> AudioInputDiscovery:
    if not input_dir.exists():
        return AudioInputDiscovery(
            input_dir=input_dir.resolve(),
            files=[],
            supported_files=[],
            unsupported_files=[],
        )

    files: list[AudioInputFile] = []
    supported_files: list[AudioInputFile] = []
    unsupported_files: list[AudioInputFile] = []

    for file_path in sorted(
        [path for path in input_dir.iterdir() if path.is_file() and not _is_temporary_upload_file(path)],
        key=lambda path: path.name.lower(),
    ):
        if file_path.suffix.lower() in SUPPORTED_AUDIO_SUFFIXES:
            described = _describe_input_file(file_path, status="supported")
            supported_files.append(described)
        else:
            described = _describe_input_file(
                file_path,
                status="unsupported",
                detail="Unsupported audio extension.",
            )
            unsupported_files.append(described)
        files.append(described)

    return AudioInputDiscovery(
        input_dir=input_dir.resolve(),
        files=files,
        supported_files=supported_files,
        unsupported_files=unsupported_files,
    )


def discover_audio_files(input_dir: Path) -> list[Path]:
    return [entry.file_path for entry in discover_audio_input(input_dir).supported_files]


def _first_tag_value(value: object) -> str | None:
    if isinstance(value, list) and value:
        candidate = str(value[0]).strip()
        return candidate or None
    if isinstance(value, str):
        candidate = value.strip()
        return candidate or None
    return None


def _extract_title_artist(file_path: Path) -> tuple[str, str]:
    fallback_title = file_path.stem
    fallback_artist = file_path.stem

    try:
        audio = MutagenFile(file_path, easy=True)
    except Exception:
        return fallback_title, fallback_artist

    if audio is None or not hasattr(audio, "tags") or audio.tags is None:
        return fallback_title, fallback_artist

    title = _first_tag_value(audio.tags.get("title")) or fallback_title
    artist = _first_tag_value(audio.tags.get("artist")) or fallback_artist
    return title, artist


def _parse_positive_float(value: object) -> float | None:
    if value is None:
        return None

    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None

    if not math.isfinite(parsed) or parsed <= 0.0:
        return None
    return parsed


def _probe_duration_seconds(file_path: Path) -> float:
    if shutil.which("ffprobe") is None:
        raise RuntimeError("ffprobe not found in PATH.")

    command = [
        "ffprobe",
        "-v",
        "error",
        "-show_streams",
        "-show_format",
        "-print_format",
        "json",
        str(file_path),
    ]

    try:
        completed = subprocess.run(command, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.strip() if exc.stderr else "Unknown ffprobe error."
        raise RuntimeError(f"Failed to probe duration for '{file_path.name}': {stderr}") from exc

    try:
        media = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Failed to parse ffprobe output for '{file_path.name}'.") from exc

    format_duration = _parse_positive_float(media.get("format", {}).get("duration"))
    if format_duration is not None:
        return format_duration

    for stream in media.get("streams", []):
        candidate = _parse_positive_float(stream.get("duration"))
        if candidate is not None:
            return candidate

    raise ValueError(f"Could not determine duration for audio file: {file_path.name}")


def _analyze_waveform(
    file_path: Path,
    silence_top_db: float,
    duration_seconds: float,
) -> tuple[float | None, float, float, str | None, str | None]:
    waveform, sample_rate = librosa.load(file_path, sr=None, mono=True)

    if sample_rate <= 0:
        raise ValueError(f"Invalid sample rate for {file_path.name}: {sample_rate}")

    waveform_duration_seconds = float(len(waveform) / sample_rate)
    effective_duration_seconds = max(0.0, min(duration_seconds, waveform_duration_seconds))

    tempo, _ = librosa.beat.beat_track(y=waveform, sr=sample_rate)
    bpm = float(tempo) if tempo is not None and not math.isnan(float(tempo)) else None

    non_silent = librosa.effects.split(waveform, top_db=silence_top_db)
    if len(non_silent) == 0:
        trim_start_seconds = 0.0
        trim_end_seconds = effective_duration_seconds
        key_waveform = waveform
    else:
        trim_start_seconds = float(non_silent[0][0] / sample_rate)
        trim_end_seconds = float(non_silent[-1][1] / sample_rate)
        key_waveform = waveform[non_silent[0][0] : non_silent[-1][1]]

    trim_start_seconds = max(0.0, min(trim_start_seconds, effective_duration_seconds))
    trim_end_seconds = max(trim_start_seconds, min(trim_end_seconds, effective_duration_seconds))

    musical_key, harmonic_key = _detect_harmonic_key(key_waveform, sample_rate)

    return bpm, trim_start_seconds, trim_end_seconds, musical_key, harmonic_key


def _normalize_vector(values: np.ndarray) -> np.ndarray:
    magnitude = float(np.linalg.norm(values))
    if magnitude <= 0:
        return values
    return values / magnitude


def _detect_harmonic_key(waveform: np.ndarray, sample_rate: int) -> tuple[str | None, str | None]:
    if waveform.size == 0:
        return None, None

    chroma = librosa.feature.chroma_stft(y=waveform, sr=sample_rate)
    if chroma.size == 0:
        return None, None

    chroma_mean = np.mean(chroma, axis=1)
    chroma_norm = _normalize_vector(chroma_mean)
    if not np.any(chroma_norm):
        return None, None

    major_profile = _normalize_vector(KEY_PROFILE_MAJOR)
    minor_profile = _normalize_vector(KEY_PROFILE_MINOR)

    best_note_index = 0
    best_mode = "major"
    best_score = float("-inf")

    for note_index in range(12):
        rotated = np.roll(chroma_norm, -note_index)
        major_score = float(np.dot(rotated, major_profile))
        minor_score = float(np.dot(rotated, minor_profile))

        if major_score > best_score:
            best_score = major_score
            best_note_index = note_index
            best_mode = "major"
        if minor_score > best_score:
            best_score = minor_score
            best_note_index = note_index
            best_mode = "minor"

    note_name = NOTE_NAMES[best_note_index]
    musical_key = f"{note_name} {best_mode}"
    harmonic_key = (
        CAMOLOT_MINOR.get(note_name) if best_mode == "minor" else CAMOLOT_MAJOR.get(note_name)
    )
    return musical_key, harmonic_key


def analyze_file(file_path: Path, silence_top_db: float = 60.0) -> TrackAnalysis:
    title, artist = _extract_title_artist(file_path)
    duration_seconds = _probe_duration_seconds(file_path)
    bpm, trim_start_seconds, trim_end_seconds, musical_key, harmonic_key = _analyze_waveform(
        file_path=file_path,
        silence_top_db=silence_top_db,
        duration_seconds=duration_seconds,
    )

    return TrackAnalysis(
        file_path=file_path,
        title=title,
        artist=artist,
        bpm=bpm,
        duration=duration_seconds,
        duration_seconds=duration_seconds,
        trim_start_seconds=trim_start_seconds,
        trim_end_seconds=trim_end_seconds,
        musical_key=musical_key,
        harmonic_key=harmonic_key,
    )


def analyze_directory(input_dir: Path, silence_top_db: float = 60.0) -> list[TrackAnalysis]:
    return scan_audio_library(input_dir=input_dir, silence_top_db=silence_top_db).tracks


def scan_audio_library(input_dir: Path, silence_top_db: float = 60.0) -> AudioLibraryScan:
    discovery = discover_audio_input(input_dir)
    tracks: list[TrackAnalysis] = []
    rejected_files: list[AudioInputFile] = []

    for discovered_file in discovery.supported_files:
        file_path = discovered_file.file_path
        try:
            tracks.append(analyze_file(file_path=file_path, silence_top_db=silence_top_db))
        except Exception as exc:
            rejected_files.append(
                AudioInputFile(
                    file_path=file_path,
                    size_bytes=discovered_file.size_bytes,
                    modified_at=discovered_file.modified_at,
                    extension=discovered_file.extension,
                    status="rejected",
                    detail=str(exc),
                )
            )
            print(f"Skipping '{file_path.name}': {exc}", file=sys.stderr)

    return AudioLibraryScan(
        discovery=discovery,
        tracks=tracks,
        rejected_files=rejected_files,
    )
