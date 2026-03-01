from __future__ import annotations

import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from models import VideoAnalysis


SUPPORTED_VIDEO_SUFFIXES = {".mp4", ".mov", ".mkv", ".webm", ".m4v"}
SEAMLESS_LOOP_CROSSFADE_SECONDS = 1.5
SCENE_CROSSFADE_SECONDS = 2.0
TARGET_RENDER_WIDTH = 3840
TARGET_RENDER_HEIGHT = 2160
TARGET_RENDER_FPS = 30
DEFAULT_AUDIO_CODEC = "pcm_s24le"
DEFAULT_VIDEO_CODEC = "libx264"
MAX_SCENE_SEGMENTS = 600


@dataclass(slots=True)
class SceneSegment:
    file_path: Path
    duration_seconds: float


def discover_video_files(input_dir: Path) -> list[Path]:
    if not input_dir.exists():
        return []

    return sorted(
        [
            file_path
            for file_path in input_dir.iterdir()
            if file_path.is_file() and file_path.suffix.lower() in SUPPORTED_VIDEO_SUFFIXES
        ],
        key=lambda path: path.name.lower(),
    )


def _parse_float(value: object) -> float | None:
    if value is None:
        return None

    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None

    if parsed <= 0.0:
        return None
    return parsed


def _parse_int(value: object) -> int | None:
    if value is None:
        return None

    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None

    if parsed <= 0:
        return None
    return parsed


def _parse_fraction(value: object) -> float | None:
    if value is None:
        return None

    text = str(value).strip()
    if not text or text == "0/0":
        return None

    if "/" in text:
        numerator_text, denominator_text = text.split("/", 1)
        try:
            numerator = float(numerator_text)
            denominator = float(denominator_text)
        except ValueError:
            return None

        if denominator == 0:
            return None
        return numerator / denominator

    return _parse_float(text)


def _run_command(command: list[str], error_prefix: str) -> str:
    try:
        result = subprocess.run(command, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.strip() if exc.stderr else ""
        raise RuntimeError(f"{error_prefix}: {stderr}") from exc

    return (result.stdout or "") + (result.stderr or "")


def _require_ffmpeg_tools() -> None:
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg not found in PATH.")
    if shutil.which("ffprobe") is None:
        raise RuntimeError("ffprobe not found in PATH.")


def _probe_media(file_path: Path) -> dict:
    output = _run_command(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_streams",
            "-show_format",
            "-print_format",
            "json",
            str(file_path),
        ],
        error_prefix=f"Failed to probe media file '{file_path.name}'",
    )

    try:
        return json.loads(output)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Failed to parse ffprobe output for '{file_path.name}'.") from exc


def probe_duration_seconds(file_path: Path) -> float:
    media = _probe_media(file_path)
    format_info = media.get("format", {})
    duration_seconds = _parse_float(format_info.get("duration"))
    if duration_seconds is not None:
        return duration_seconds

    streams = media.get("streams", [])
    for stream in streams:
        candidate = _parse_float(stream.get("duration"))
        if candidate is not None:
            return candidate

    raise ValueError(f"Could not determine duration for media file: {file_path}")


def analyze_video(file_path: Path) -> VideoAnalysis:
    media = _probe_media(file_path)
    streams = media.get("streams", [])

    video_stream = next((stream for stream in streams if stream.get("codec_type") == "video"), None)
    if video_stream is None:
        raise ValueError(f"No video stream found in: {file_path}")

    duration_seconds = _parse_float(video_stream.get("duration")) or _parse_float(media.get("format", {}).get("duration"))
    if duration_seconds is None:
        raise ValueError(f"Could not determine duration for: {file_path}")

    width = _parse_int(video_stream.get("width"))
    height = _parse_int(video_stream.get("height"))
    frame_rate = _parse_fraction(video_stream.get("avg_frame_rate")) or _parse_fraction(video_stream.get("r_frame_rate"))

    return VideoAnalysis(
        file_path=file_path,
        duration_seconds=duration_seconds,
        width=width,
        height=height,
        frame_rate=frame_rate,
    )


def analyze_video_directory(input_dir: Path) -> list[VideoAnalysis]:
    videos: list[VideoAnalysis] = []

    for file_path in discover_video_files(input_dir):
        try:
            videos.append(analyze_video(file_path))
        except Exception as exc:
            print(f"Skipping '{file_path.name}': {exc}", file=sys.stderr)

    return videos


def detect_h264_encoder() -> str:
    encoder_output = _run_command(
        ["ffmpeg", "-hide_banner", "-encoders"],
        error_prefix="Failed to query FFmpeg encoders",
    )

    if "h264_nvenc" in encoder_output:
        return "h264_nvenc"
    if "h264_videotoolbox" in encoder_output:
        return "h264_videotoolbox"
    return DEFAULT_VIDEO_CODEC


def make_seamless_loop_clip(
    input_clip_path: Path,
    output_clip_path: Path,
    crossfade_seconds: float = SEAMLESS_LOOP_CROSSFADE_SECONDS,
) -> VideoAnalysis:
    clip_analysis = analyze_video(input_clip_path)
    clip_duration = clip_analysis.playable_duration_seconds

    if clip_duration <= crossfade_seconds * 2:
        raise ValueError(
            f"Clip '{input_clip_path.name}' is too short for a split-and-crossfade loop: {clip_duration:.2f}s"
        )

    midpoint_seconds = clip_duration / 2.0
    second_half_duration = clip_duration - midpoint_seconds
    xfade_offset = max(0.0, second_half_duration - crossfade_seconds)

    output_clip_path.parent.mkdir(parents=True, exist_ok=True)

    filter_complex = (
        f"[0:v]split=2[first_raw][second_raw];"
        f"[first_raw]trim=start=0:end={midpoint_seconds:.6f},setpts=PTS-STARTPTS[first_half];"
        f"[second_raw]trim=start={midpoint_seconds:.6f}:end={clip_duration:.6f},setpts=PTS-STARTPTS[second_half];"
        f"[second_half][first_half]xfade=transition=fade:duration={crossfade_seconds:.6f}:offset={xfade_offset:.6f},"
        f"format=yuv420p,setsar=1[looped]"
    )

    command = [
        "ffmpeg",
        "-y",
        "-i",
        str(input_clip_path),
        "-filter_complex",
        filter_complex,
        "-map",
        "[looped]",
        "-an",
        "-c:v",
        DEFAULT_VIDEO_CODEC,
        "-preset",
        "medium",
        "-crf",
        "18",
        str(output_clip_path),
    ]

    _run_command(command, error_prefix=f"Failed to build seamless loop for '{input_clip_path.name}'")
    return analyze_video(output_clip_path)


def _build_scene_sequence(
    seamless_clips: list[VideoAnalysis],
    target_duration_seconds: float,
    crossfade_seconds: float,
) -> list[SceneSegment]:
    if not seamless_clips:
        raise ValueError("No seamless clips provided for scene assembly.")

    sequence: list[SceneSegment] = []
    assembled_duration = 0.0
    clip_index = 0

    while assembled_duration < target_duration_seconds + crossfade_seconds:
        clip = seamless_clips[clip_index % len(seamless_clips)]
        clip_duration = clip.playable_duration_seconds
        if clip_duration <= crossfade_seconds:
            raise ValueError(
                f"Clip '{clip.file_path.name}' is too short for {crossfade_seconds:.1f}s scene crossfades."
            )

        sequence.append(SceneSegment(file_path=clip.file_path, duration_seconds=clip_duration))

        if len(sequence) == 1:
            assembled_duration += clip_duration
        else:
            assembled_duration += max(0.001, clip_duration - crossfade_seconds)

        clip_index += 1
        if len(sequence) > MAX_SCENE_SEGMENTS:
            raise RuntimeError("Scene expansion exceeded safety limit while matching audio duration.")

    return sequence


def _build_scene_filtergraph(
    scenes: list[SceneSegment],
    target_duration_seconds: float,
    crossfade_seconds: float,
) -> str:
    filter_parts: list[str] = []

    for index, scene in enumerate(scenes):
        filter_parts.append(
            f"[{index}:v]settb=AVTB,setpts=PTS-STARTPTS,trim=duration={scene.duration_seconds:.6f},"
            f"fps={TARGET_RENDER_FPS},"
            f"scale={TARGET_RENDER_WIDTH}:{TARGET_RENDER_HEIGHT}:force_original_aspect_ratio=decrease,"
            f"pad={TARGET_RENDER_WIDTH}:{TARGET_RENDER_HEIGHT}:(ow-iw)/2:(oh-ih)/2:color=black,"
            f"format=yuv420p,setsar=1[v{index}]"
        )

    if len(scenes) == 1:
        filter_parts.append(
            f"[v0]trim=duration={target_duration_seconds:.6f},setpts=PTS-STARTPTS[vout]"
        )
        return ";".join(filter_parts)

    running_duration = scenes[0].duration_seconds
    current_label = "v0"

    for index in range(1, len(scenes)):
        next_label = f"x{index}"
        xfade_offset = max(0.0, running_duration - crossfade_seconds)
        filter_parts.append(
            f"[{current_label}][v{index}]xfade=transition=fade:duration={crossfade_seconds:.6f}:offset={xfade_offset:.6f}[{next_label}]"
        )
        running_duration += max(0.001, scenes[index].duration_seconds - crossfade_seconds)
        current_label = next_label

    filter_parts.append(
        f"[{current_label}]trim=duration={target_duration_seconds:.6f},setpts=PTS-STARTPTS[vout]"
    )
    return ";".join(filter_parts)


def render_final_video(
    audio_mix_path: Path,
    ordered_video_paths: list[Path],
    output_path: Path,
    work_dir: Path | None = None,
    seamless_crossfade_seconds: float = SEAMLESS_LOOP_CROSSFADE_SECONDS,
    scene_crossfade_seconds: float = SCENE_CROSSFADE_SECONDS,
) -> tuple[Path, str]:
    if not ordered_video_paths:
        raise ValueError("No video clips provided.")

    _require_ffmpeg_tools()

    if not audio_mix_path.exists():
        raise FileNotFoundError(f"Audio mix not found: {audio_mix_path}")

    target_duration_seconds = probe_duration_seconds(audio_mix_path)
    if target_duration_seconds <= 0:
        raise ValueError(f"Invalid audio duration for: {audio_mix_path}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_dir = work_dir or output_path.parent / "video_work"
    temporary_dir.mkdir(parents=True, exist_ok=True)

    seamless_clips: list[VideoAnalysis] = []
    for index, video_path in enumerate(ordered_video_paths):
        if not video_path.exists():
            raise FileNotFoundError(f"Video clip not found: {video_path}")

        seamless_output_path = temporary_dir / f"seamless_{index:04d}.mp4"
        seamless_clips.append(
            make_seamless_loop_clip(
                input_clip_path=video_path,
                output_clip_path=seamless_output_path,
                crossfade_seconds=seamless_crossfade_seconds,
            )
        )

    scenes = _build_scene_sequence(
        seamless_clips=seamless_clips,
        target_duration_seconds=target_duration_seconds,
        crossfade_seconds=scene_crossfade_seconds,
    )

    encoder = detect_h264_encoder()
    filter_complex = _build_scene_filtergraph(
        scenes=scenes,
        target_duration_seconds=target_duration_seconds,
        crossfade_seconds=scene_crossfade_seconds,
    )

    command: list[str] = ["ffmpeg", "-y"]

    for scene in scenes:
        command.extend(["-i", str(scene.file_path)])

    audio_index = len(scenes)
    command.extend(["-i", str(audio_mix_path)])
    command.extend(
        [
            "-filter_complex",
            filter_complex,
            "-map",
            "[vout]",
            "-map",
            f"{audio_index}:a:0",
            "-r",
            str(TARGET_RENDER_FPS),
            "-pix_fmt",
            "yuv420p",
            "-c:v",
            encoder,
        ]
    )

    if encoder == "h264_nvenc":
        command.extend(["-preset", "p5", "-rc", "vbr", "-cq", "19", "-b:v", "0"])
    elif encoder == "h264_videotoolbox":
        command.extend(["-b:v", "25M", "-allow_sw", "1"])
    else:
        command.extend(["-preset", "medium", "-crf", "18"])

    command.extend(
        [
            "-c:a",
            DEFAULT_AUDIO_CODEC,
            "-shortest",
            str(output_path),
        ]
    )

    _run_command(command, error_prefix="Failed to render final video")
    return output_path, encoder
