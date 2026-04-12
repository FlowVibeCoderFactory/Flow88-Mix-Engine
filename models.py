from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from key_utils import format_key_display


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
    duration: float | None = None

    def __post_init__(self) -> None:
        normalized_duration = self.duration if self.duration is not None else self.duration_seconds
        try:
            parsed_duration = float(normalized_duration)
        except (TypeError, ValueError):
            parsed_duration = 0.0
        normalized_duration = max(0.0, parsed_duration)
        self.duration = normalized_duration
        self.duration_seconds = normalized_duration

    @property
    def trimmed_duration_seconds(self) -> float:
        return max(0.0, self.trim_end_seconds - self.trim_start_seconds)

    @property
    def display_key(self) -> str:
        """Combined key display label, e.g. '7A · D minor'."""
        return format_key_display(self.harmonic_key, self.musical_key)


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
