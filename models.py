from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class TrackAnalysis:
    file_path: Path
    title: str
    artist: str
    bpm: float | None
    duration_seconds: float
    trim_start_seconds: float
    trim_end_seconds: float
    musical_key: str | None = None
    harmonic_key: str | None = None

    @property
    def trimmed_duration_seconds(self) -> float:
        return max(0.0, self.trim_end_seconds - self.trim_start_seconds)


@dataclass(slots=True)
class TimelineEntry:
    absolute_start_seconds: float
    track: TrackAnalysis


@dataclass(slots=True)
class VideoAnalysis:
    file_path: Path
    duration_seconds: float
    width: int | None
    height: int | None
    frame_rate: float | None

    @property
    def playable_duration_seconds(self) -> float:
        return max(0.0, self.duration_seconds)
