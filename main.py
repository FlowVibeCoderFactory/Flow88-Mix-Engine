from pathlib import Path

from analyzer import analyze_directory
from mixer import DEFAULT_CROSSFADE_SECONDS, build_timeline, render_mix
from project_persistence import ensure_projects_dir
from runtime_config import get_runtime_settings
from tracklist import write_tracklist


RUNTIME_SETTINGS = get_runtime_settings()
INPUT_DIR = RUNTIME_SETTINGS.input_dir
VIDEO_INPUT_DIR = RUNTIME_SETTINGS.video_input_dir
OUTPUT_DIR = RUNTIME_SETTINGS.output_dir
MASTER_MIX_FILENAME = "final_mix.wav"
LEGACY_MASTER_MIX_FILENAME = "flow88_master_mix.wav"
TRACKLIST_FILENAME = "tracklist.txt"


def ensure_runtime_directories() -> None:
    INPUT_DIR.mkdir(parents=True, exist_ok=True)
    VIDEO_INPUT_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    RUNTIME_SETTINGS.logs_dir.mkdir(parents=True, exist_ok=True)
    ensure_projects_dir()


def run_pipeline() -> tuple[Path, Path] | None:
    ensure_runtime_directories()

    analyzed_tracks = analyze_directory(INPUT_DIR)
    if not analyzed_tracks:
        print(f"No supported audio files found in: {INPUT_DIR.resolve()}")
        return None

    timeline = build_timeline(analyzed_tracks, crossfade_seconds=DEFAULT_CROSSFADE_SECONDS)
    mix_output_path = OUTPUT_DIR / MASTER_MIX_FILENAME
    render_mix(
        analyzed_tracks,
        output_path=mix_output_path,
        crossfade_seconds=DEFAULT_CROSSFADE_SECONDS,
    )

    tracklist_output_path = OUTPUT_DIR / TRACKLIST_FILENAME
    write_tracklist(timeline, output_path=tracklist_output_path)

    return mix_output_path, tracklist_output_path


def main() -> None:
    pipeline_output = run_pipeline()
    if pipeline_output is None:
        return

    mix_output_path, tracklist_output_path = pipeline_output
    print(f"Mix rendered: {mix_output_path.resolve()}")
    print(f"Tracklist generated: {tracklist_output_path.resolve()}")


if __name__ == "__main__":
    main()
