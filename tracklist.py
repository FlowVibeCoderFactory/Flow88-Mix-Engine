from __future__ import annotations

from pathlib import Path

from models import TimelineEntry


def format_timestamp(seconds: float) -> str:
    total_seconds = max(0, int(seconds))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, secs = divmod(remainder, 60)

    if hours > 0:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def build_tracklist_lines(timeline: list[TimelineEntry]) -> list[str]:
    lines: list[str] = []
    for entry in timeline:
        timestamp = format_timestamp(entry.absolute_start_seconds)
        lines.append(f"[{timestamp}] {entry.track.title} – {entry.track.artist}")
    return lines


def write_tracklist(timeline: list[TimelineEntry], output_path: str | Path) -> Path:
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    lines = build_tracklist_lines(timeline)
    output.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")

    return output
