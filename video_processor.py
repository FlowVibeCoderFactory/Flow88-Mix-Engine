from __future__ import annotations

import hashlib
import json
import logging
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from models import VideoAnalysis
from render_logging import log_structured


SUPPORTED_VIDEO_SUFFIXES = {".mp4", ".mov", ".mkv", ".webm", ".m4v"}
SEAMLESS_LOOP_CROSSFADE_SECONDS = 1.5
DEFAULT_AUDIO_CODEC = "pcm_s24le"
DEFAULT_VIDEO_CODEC = "libx264"
MAX_SCENE_SEGMENTS = 600
PREVIEW_LOOP_FILENAME = "loop_preview.mp4"
CHUNK_SIZE = 6
CHUNK_CONCAT_FILENAME = "chunks.txt"
LOOP_FILTER_SCRIPT_FILENAME = "loop_filter.txt"
FINAL_FILTER_SCRIPT_FILENAME = "final_filter.txt"
COMPOSITE_FILTER_SCRIPT_FILENAME = "composite_filter.txt"
RENDER_STATE_FILENAME = "render_state.json"
MAX_PREVIEW_TIMELINE_SECONDS = 60.0
PREVIEW_OUTPUT_FILENAME = "output_preview.mp4"
DEFAULT_RENDER_PROFILE = "balanced"
RENDER_PROFILES = {
    "preview": {
        "fps": 12,
        "resolution": (640, 360),
        "nvenc_preset": "p1",
        "cq": 35,
        "crossfade": 0.5,
    },
    "performance": {
        "fps": 24,
        "resolution": (2560, 1440),
        "nvenc_preset": "p3",
        "cq": 23,
        "crossfade": 1.0,
    },
    "balanced": {
        "fps": 30,
        "resolution": (3840, 2160),
        "nvenc_preset": "p5",
        "cq": 19,
        "crossfade": 2.0,
    },
    "quality": {
        "fps": 30,
        "resolution": (3840, 2160),
        "nvenc_preset": "p7",
        "cq": 16,
        "crossfade": 2.0,
    },
}


def _resolve_render_profile(mode: str) -> tuple[str, dict[str, int | float | str | tuple[int, int]]]:
    normalized_mode = mode.lower().strip()
    profile = RENDER_PROFILES.get(normalized_mode)
    if profile is None:
        raise ValueError(
            f"Unknown render profile '{mode}'. Available profiles: {', '.join(sorted(RENDER_PROFILES))}"
        )
    return normalized_mode, profile


@dataclass(slots=True)
class SceneSegment:
    file_path: Path
    duration_seconds: float
    loop_count: int = 1


@dataclass(slots=True)
class RenderProgressState:
    total_duration_seconds: float
    start_time: float
    last_logged_percent: float = -1.0


@dataclass(slots=True, frozen=True)
class RenderSettings:
    mode: str
    width: int
    height: int
    fps: int
    crossfade_seconds: float
    nvenc_preset: str
    cq: int
    enable_scaling: bool = True
    enable_padding: bool = True
    enable_color_conversion: bool = True
    preview_timeline_limit_seconds: float | None = None
    cpu_preset: str = "medium"
    cpu_crf: int = 18


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


def _run_command(command: list[str], error_prefix: str, logger: logging.Logger | None = None) -> str:
    if command and command[0] == "ffmpeg":
        log_structured(logger, "ffmpeg_call", command=command, stage=error_prefix)
    try:
        result = subprocess.run(command, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        if command and command[0] == "ffmpeg" and logger is not None:
            logger.exception("Render failed")
        stderr = exc.stderr.strip() if exc.stderr else ""
        raise RuntimeError(f"{error_prefix}: {stderr}") from exc

    return (result.stdout or "") + (result.stderr or "")


def _report_render_progress(
    progress_state: RenderProgressState,
    processed_seconds: float,
    on_progress: Callable[[float, float], None] | None = None,
) -> None:
    if progress_state.total_duration_seconds <= 0:
        return

    clamped_seconds = min(progress_state.total_duration_seconds, max(0.0, processed_seconds))
    progress = clamped_seconds / progress_state.total_duration_seconds

    if (
        progress_state.last_logged_percent >= 0.0
        and progress < 1.0
        and progress - progress_state.last_logged_percent < 0.005
    ):
        return

    elapsed_seconds = max(0.0, time.time() - progress_state.start_time)
    eta_seconds = 0.0
    if progress > 0:
        eta_seconds = max(0.0, (elapsed_seconds / progress) - elapsed_seconds)

    if on_progress is not None:
        try:
            on_progress(progress, eta_seconds)
        except Exception:
            pass

    print(f"Progress: {progress * 100:.1f}% | ETA: {eta_seconds / 60:.1f} min", flush=True)
    progress_state.last_logged_percent = progress


def _run_ffmpeg_with_progress(
    command: list[str],
    error_prefix: str,
    progress_state: RenderProgressState,
    progress_offset_seconds: float,
    progress_span_seconds: float,
    on_progress: Callable[[float, float], None] | None = None,
    logger: logging.Logger | None = None,
) -> None:
    log_structured(logger, "ffmpeg_call", command=command, stage=error_prefix)
    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
    except OSError as exc:
        if logger is not None:
            logger.exception("Render failed")
        raise RuntimeError(f"{error_prefix}: {exc}") from exc

    if process.stdout is None:
        raise RuntimeError(f"{error_prefix}: unable to stream ffmpeg output.")

    error_tail: list[str] = []

    for raw_line in process.stdout:
        line = raw_line.strip()
        if not line:
            continue

        error_tail.append(line)
        if len(error_tail) > 25:
            error_tail.pop(0)

        if not line.startswith("out_time_ms="):
            continue

        out_time_text = line.split("=", 1)[1].strip()
        try:
            out_seconds = int(out_time_text) / 1_000_000
        except ValueError:
            continue

        processed_seconds = progress_offset_seconds + min(progress_span_seconds, max(0.0, out_seconds))
        _report_render_progress(progress_state, processed_seconds, on_progress=on_progress)

    process.wait()
    _report_render_progress(
        progress_state,
        progress_offset_seconds + progress_span_seconds,
        on_progress=on_progress,
    )

    if process.returncode != 0:
        details = " | ".join(error_tail[-5:]) if error_tail else "ffmpeg exited with no output."
        raise RuntimeError(f"{error_prefix}: {details}")


def _write_filter_script(filter_complex: str, script_path: Path) -> None:
    script_path.write_text(filter_complex, encoding="utf-8")


def _loop_cache_path(input_path: Path, cache_dir: Path) -> Path:
    return cache_dir / f"{input_path.stem}_loop.mp4"


def _escape_concat_path(file_path: Path) -> str:
    absolute_posix = str(file_path.resolve()).replace("\\", "/")
    return absolute_posix.replace("'", "\\'")


def _write_concat_inputs_file(input_paths: list[Path], concat_file_path: Path) -> None:
    concat_lines = [f"file '{_escape_concat_path(path)}'" for path in input_paths]
    concat_file_path.write_text("\n".join(concat_lines) + "\n", encoding="utf-8")


def _chunk_list(items: list, size: int) -> list[list]:
    if size <= 0:
        raise ValueError("Chunk size must be greater than zero.")
    return [items[index : index + size] for index in range(0, len(items), size)]


def _assembled_duration_seconds(scenes: list[SceneSegment], crossfade_seconds: float) -> float:
    if not scenes:
        return 0.0

    total_duration = scenes[0].duration_seconds
    for scene in scenes[1:]:
        total_duration += max(0.001, scene.duration_seconds - crossfade_seconds)
    return max(0.0, total_duration)


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
    return DEFAULT_VIDEO_CODEC


def detect_gpu_pipeline() -> bool:
    try:
        output = _run_command(["ffmpeg", "-hide_banner", "-hwaccels"], "Failed to query hwaccels")
        return "cuda" in output.lower()
    except Exception:
        return False


def make_seamless_loop_clip(
    input_clip_path: Path,
    output_clip_path: Path,
    crossfade_seconds: float = SEAMLESS_LOOP_CROSSFADE_SECONDS,
    logger: logging.Logger | None = None,
) -> VideoAnalysis:
    cache_dir = output_clip_path.parent
    cache_dir.mkdir(parents=True, exist_ok=True)
    looped_path = _loop_cache_path(input_clip_path, cache_dir)
    preview_path = cache_dir / PREVIEW_LOOP_FILENAME

    if looped_path.exists():
        shutil.copy2(looped_path, preview_path)
        log_structured(
            logger,
            "seamless_loop_cache_hit",
            input_clip=str(input_clip_path.resolve()),
            cached_clip=str(looped_path.resolve()),
        )
        return analyze_video(looped_path)

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
        "split=2[first_raw][second_raw];"
        f"[first_raw]trim=start=0:end={midpoint_seconds:.6f},setpts=PTS-STARTPTS[first_half];"
        f"[second_raw]trim=start={midpoint_seconds:.6f}:end={clip_duration:.6f},setpts=PTS-STARTPTS[second_half];"
        f"[second_half][first_half]xfade=transition=fade:duration={crossfade_seconds:.6f}:offset={xfade_offset:.6f},"
        "format=yuv420p,setsar=1"
    )
    filter_script_path = (output_clip_path.parent / LOOP_FILTER_SCRIPT_FILENAME).resolve()
    _write_filter_script(filter_complex, filter_script_path)

    command = [
        "ffmpeg",
        "-y",
        "-i",
        str(input_clip_path),
        "-filter_script:v",
        str(filter_script_path),
        "-an",
        "-c:v",
        DEFAULT_VIDEO_CODEC,
        "-preset",
        "medium",
        "-crf",
        "18",
        str(looped_path),
    ]

    try:
        _run_command(
            command,
            error_prefix=f"Failed to build seamless loop for '{input_clip_path.name}'",
            logger=logger,
        )
    finally:
        if filter_script_path.exists():
            filter_script_path.unlink(missing_ok=True)

    shutil.copy2(looped_path, preview_path)
    return analyze_video(looped_path)


def _build_scene_sequence(
    seamless_clips: list[tuple[VideoAnalysis, int]],
    target_duration_seconds: float,
    crossfade_seconds: float,
) -> list[SceneSegment]:
    if not seamless_clips:
        raise ValueError("No seamless clips provided for scene assembly.")

    ordered_looped_clips: list[tuple[VideoAnalysis, int]] = []
    for clip, loop_count in seamless_clips:
        if loop_count <= 0:
            raise ValueError(f"Invalid loop count for clip '{clip.file_path.name}': {loop_count}")
        for _ in range(loop_count):
            ordered_looped_clips.append((clip, loop_count))

    if not ordered_looped_clips:
        raise ValueError("No seamless clips available after applying loop counts.")

    sequence: list[SceneSegment] = []
    assembled_duration = 0.0
    clip_index = 0

    while assembled_duration < target_duration_seconds + crossfade_seconds:
        clip, loop_count = ordered_looped_clips[clip_index % len(ordered_looped_clips)]
        clip_duration = clip.playable_duration_seconds
        if clip_duration <= crossfade_seconds:
            raise ValueError(
                f"Clip '{clip.file_path.name}' is too short for {crossfade_seconds:.1f}s scene crossfades."
            )

        sequence.append(
            SceneSegment(
                file_path=clip.file_path,
                duration_seconds=clip_duration,
                loop_count=loop_count,
            )
        )

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
    settings: RenderSettings,
) -> str:
    filter_parts: list[str] = []
    for index, scene in enumerate(scenes):
        chain_parts = [
            "setpts=PTS-STARTPTS",
            f"trim=duration={scene.duration_seconds:.6f}",
            f"fps={settings.fps}",
        ]
        if settings.enable_scaling:
            chain_parts.append(
                f"scale={settings.width}:{settings.height}:force_original_aspect_ratio=decrease"
            )
        if settings.enable_padding:
            chain_parts.append(f"pad={settings.width}:{settings.height}:(ow-iw)/2:(oh-ih)/2:color=black")
        if settings.enable_color_conversion:
            chain_parts.extend(["format=yuv420p", "setsar=1"])

        filter_parts.append(f"[{index}:v]{','.join(chain_parts)}[v{index}]")

    if len(scenes) == 1:
        filter_parts.append(f"[v0]trim=duration={target_duration_seconds:.6f},setpts=PTS-STARTPTS[vout]")
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


def _build_composite_filtergraph(
    scenes: list[SceneSegment],
    crossfade_seconds: float,
    target_duration_seconds: float,
    settings: RenderSettings,
    use_cuda: bool = True,
) -> str:
    if not scenes:
        raise ValueError("Cannot build a composite filtergraph without scenes.")

    filter_parts: list[str] = []
    start_offsets: list[float] = []
    running_offset = 0.0

    for index, scene in enumerate(scenes):
        start_offsets.append(max(0.0, running_offset))
        if index < len(scenes) - 1:
            running_offset += max(0.001, scene.duration_seconds - crossfade_seconds)

    if use_cuda:
        filter_parts.append(
            f"color=c=black:s={settings.width}x{settings.height}:r={settings.fps}:d={target_duration_seconds:.6f},"
            "format=rgba,hwupload_cuda[base]"
        )
    else:
        filter_parts.append(
            f"color=c=black:s={settings.width}x{settings.height}:r={settings.fps}:d={target_duration_seconds:.6f},"
            "format=rgba[base]"
        )

    for index, scene in enumerate(scenes):
        scene_duration = max(0.001, scene.duration_seconds)
        scene_chain = f"[{index}:v]fps={settings.fps},"

        if use_cuda:
            scene_chain += (
                f"scale_cuda={settings.width}:{settings.height}:force_original_aspect_ratio=decrease,"
                "hwdownload,format=rgba,"
            )
        else:
            scene_chain += (
                f"scale={settings.width}:{settings.height}:force_original_aspect_ratio=decrease,"
                "format=rgba,"
            )

        scene_chain += f"pad={settings.width}:{settings.height}:(ow-iw)/2:(oh-ih)/2:color=black,"

        if index > 0:
            scene_chain += f"fade=t=in:st=0:d={crossfade_seconds:.6f}:alpha=1,"
        if index < len(scenes) - 1:
            fade_out_start = max(0.0, scene_duration - crossfade_seconds)
            scene_chain += f"fade=t=out:st={fade_out_start:.6f}:d={crossfade_seconds:.6f}:alpha=1,"

        scene_chain += f"setpts=PTS-STARTPTS+{start_offsets[index]:.6f}/TB"
        if use_cuda:
            scene_chain += ",hwupload_cuda"

        scene_chain += f"[v{index}]"
        filter_parts.append(scene_chain)

    current_label = "base"
    for index in range(len(scenes)):
        output_label = f"cmp{index}"
        if use_cuda:
            filter_parts.append(f"[{current_label}][v{index}]overlay_cuda=x=0:y=0[{output_label}]")
        else:
            filter_parts.append(f"[{current_label}][v{index}]overlay=x=0:y=0:format=auto[{output_label}]")
        current_label = output_label

    if use_cuda:
        filter_parts.append(
            f"[{current_label}]hwdownload,format=yuv420p,trim=duration={target_duration_seconds:.6f},setpts=PTS-STARTPTS[vout]"
        )
    else:
        filter_parts.append(
            f"[{current_label}]format=yuv420p,trim=duration={target_duration_seconds:.6f},setpts=PTS-STARTPTS[vout]"
        )

    return ";".join(filter_parts)


def _render_scene_chunk(
    chunk_scenes: list[SceneSegment],
    chunk_index: int,
    temporary_dir: Path,
    encoder: str,
    settings: RenderSettings,
    chunk_target_duration: float,
    progress_state: RenderProgressState,
    progress_offset_seconds: float,
    scene_crossfade_seconds: float,
    on_progress: Callable[[float, float], None] | None,
    logger: logging.Logger | None = None,
    final_filter_filename: str = FINAL_FILTER_SCRIPT_FILENAME,
) -> Path:
    if not chunk_scenes:
        raise ValueError("Cannot render an empty scene chunk.")
    if chunk_target_duration <= 0:
        raise ValueError("Chunk target duration must be positive.")

    output_chunk_path = temporary_dir / f"chunk_{chunk_index:04d}.mp4"
    filter_script_path = (temporary_dir / f"chunk_{chunk_index:04d}_{final_filter_filename}").resolve()
    filter_complex = _build_scene_filtergraph(
        scenes=chunk_scenes,
        target_duration_seconds=chunk_target_duration,
        crossfade_seconds=scene_crossfade_seconds,
        settings=settings,
    )
    _write_filter_script(filter_complex, filter_script_path)

    command: list[str] = ["ffmpeg", "-y"]
    for scene in chunk_scenes:
        command.extend(["-i", str(scene.file_path)])

    command.extend(
        [
            "-filter_complex_script",
            str(filter_script_path),
            "-map",
            "[vout]",
            "-an",
            "-r",
            str(settings.fps),
        ]
    )
    if settings.enable_color_conversion:
        command.extend(["-pix_fmt", "yuv420p"])

    if encoder == "h264_nvenc":
        command.extend(
            [
                "-c:v",
                "h264_nvenc",
                "-preset",
                settings.nvenc_preset,
                "-rc",
                "vbr",
                "-cq",
                str(settings.cq),
                "-b:v",
                "0",
            ]
        )
    else:
        command.extend(
            [
                "-c:v",
                DEFAULT_VIDEO_CODEC,
                "-preset",
                settings.cpu_preset,
                "-crf",
                str(settings.cpu_crf),
            ]
        )

    command.extend(["-progress", "pipe:1", "-nostats", str(output_chunk_path)])

    try:
        try:
            _run_ffmpeg_with_progress(
                command=command,
                error_prefix=f"Failed to render scene chunk {chunk_index}",
                progress_state=progress_state,
                progress_offset_seconds=progress_offset_seconds,
                progress_span_seconds=chunk_target_duration,
                on_progress=on_progress,
                logger=logger,
            )
        except Exception:
            if logger is not None:
                logger.exception("Render failed")
            raise
    finally:
        if filter_script_path.exists():
            filter_script_path.unlink(missing_ok=True)

    return output_chunk_path


def _render_gpu_composite(
    scenes: list[SceneSegment],
    audio_mix_path: Path,
    output_path: Path,
    temporary_dir: Path,
    target_duration_seconds: float,
    settings: RenderSettings,
    progress_state: RenderProgressState,
    scene_crossfade_seconds: float,
    on_progress: Callable[[float, float], None] | None,
    logger: logging.Logger | None = None,
) -> None:
    if not scenes:
        raise ValueError("Cannot render GPU composite output without scenes.")

    filter_script_path = (temporary_dir / COMPOSITE_FILTER_SCRIPT_FILENAME).resolve()
    filter_complex = _build_composite_filtergraph(
        scenes=scenes,
        crossfade_seconds=scene_crossfade_seconds,
        target_duration_seconds=target_duration_seconds,
        settings=settings,
        use_cuda=True,
    )
    _write_filter_script(filter_complex, filter_script_path)

    command: list[str] = ["ffmpeg", "-y"]
    for scene in scenes:
        command.extend(["-hwaccel", "cuda", "-hwaccel_output_format", "cuda", "-i", str(scene.file_path)])

    audio_index = len(scenes)
    command.extend(
        [
            "-i",
            str(audio_mix_path),
            "-filter_complex_script",
            str(filter_script_path),
            "-map",
            "[vout]",
            "-map",
            f"{audio_index}:a:0",
            "-r",
            str(settings.fps),
        ]
    )
    if settings.enable_color_conversion:
        command.extend(["-pix_fmt", "yuv420p"])
    command.extend(
        [
            "-c:v",
            "h264_nvenc",
            "-preset",
            settings.nvenc_preset,
            "-rc",
            "vbr",
            "-cq",
            str(settings.cq),
            "-b:v",
            "0",
            "-c:a",
            DEFAULT_AUDIO_CODEC,
            "-shortest",
            "-progress",
            "pipe:1",
            "-nostats",
            str(output_path),
        ]
    )

    try:
        try:
            _run_ffmpeg_with_progress(
                command=command,
                error_prefix="GPU composite render failed",
                progress_state=progress_state,
                progress_offset_seconds=0.0,
                progress_span_seconds=target_duration_seconds,
                on_progress=on_progress,
                logger=logger,
            )
        except Exception:
            if logger is not None:
                logger.exception("Render failed")
            raise
    finally:
        if filter_script_path.exists():
            filter_script_path.unlink(missing_ok=True)


def _stitch_chunks(
    chunk_paths: list[Path],
    chunk_concat_path: Path,
    stitched_video_path: Path,
    logger: logging.Logger | None = None,
) -> Path:
    if not chunk_paths:
        raise ValueError("No chunks available to stitch.")

    if len(chunk_paths) == 1:
        return chunk_paths[0]

    _write_concat_inputs_file(chunk_paths, chunk_concat_path)

    command = [
        "ffmpeg",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(chunk_concat_path),
        "-c",
        "copy",
        str(stitched_video_path),
    ]
    _run_command(command, error_prefix="Failed to stitch rendered chunks", logger=logger)
    return stitched_video_path


def _mux_audio_once(
    stitched_video_path: Path,
    audio_mix_path: Path,
    output_path: Path,
    logger: logging.Logger | None = None,
) -> None:
    pcm_command = [
        "ffmpeg",
        "-y",
        "-i",
        str(stitched_video_path),
        "-i",
        str(audio_mix_path),
        "-c:v",
        "copy",
        "-c:a",
        DEFAULT_AUDIO_CODEC,
        "-shortest",
        str(output_path),
    ]

    try:
        _run_command(pcm_command, error_prefix="Failed to mux final audio/video output", logger=logger)
        return
    except RuntimeError as pcm_error:
        aac_command = [
            "ffmpeg",
            "-y",
            "-i",
            str(stitched_video_path),
            "-i",
            str(audio_mix_path),
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "320k",
            "-shortest",
            str(output_path),
        ]
        try:
            _run_command(aac_command, error_prefix="Failed to mux final output (AAC fallback)", logger=logger)
        except RuntimeError as fallback_error:
            raise RuntimeError(f"{pcm_error} | Fallback failed: {fallback_error}") from fallback_error


def _normalize_ordered_video_inputs(
    ordered_video_paths: list[Path] | list[tuple[Path, int]],
) -> list[tuple[Path, int]]:
    normalized_videos: list[tuple[Path, int]] = []
    for item in ordered_video_paths:
        if isinstance(item, tuple):
            video_path, loop_count = item
        else:
            video_path, loop_count = item, 1

        if loop_count <= 0:
            raise ValueError(f"Loop count must be positive for clip: {video_path.name}")
        normalized_videos.append((video_path, loop_count))

    return normalized_videos


def _build_render_settings(render_profile: str, scene_crossfade_seconds: float | None) -> RenderSettings:
    mode, profile = _resolve_render_profile(render_profile)
    fps = int(profile["fps"])
    width, height = profile["resolution"]
    crossfade_seconds = float(profile["crossfade"]) if scene_crossfade_seconds is None else float(scene_crossfade_seconds)
    if crossfade_seconds <= 0:
        raise ValueError("Scene crossfade seconds must be greater than zero.")

    preview_mode = mode == "preview"
    return RenderSettings(
        mode=mode,
        width=int(width),
        height=int(height),
        fps=fps,
        crossfade_seconds=crossfade_seconds,
        nvenc_preset=str(profile["nvenc_preset"]),
        cq=int(profile["cq"]),
        enable_scaling=not preview_mode,
        enable_padding=not preview_mode,
        enable_color_conversion=not preview_mode,
        preview_timeline_limit_seconds=MAX_PREVIEW_TIMELINE_SECONDS if preview_mode else None,
        cpu_preset="ultrafast" if preview_mode else "medium",
        cpu_crf=35 if preview_mode else 18,
    )


def _prepare_scene_segments(
    audio_mix_path: Path,
    normalized_video_inputs: list[tuple[Path, int]],
    temporary_dir: Path,
    settings: RenderSettings,
    seamless_crossfade_seconds: float,
    logger: logging.Logger | None = None,
) -> tuple[float, list[SceneSegment]]:
    if not audio_mix_path.exists():
        raise FileNotFoundError(f"Audio mix not found: {audio_mix_path}")

    target_duration_seconds = probe_duration_seconds(audio_mix_path)
    if target_duration_seconds <= 0:
        raise ValueError(f"Invalid audio duration for: {audio_mix_path}")

    if settings.preview_timeline_limit_seconds is not None:
        target_duration_seconds = min(target_duration_seconds, settings.preview_timeline_limit_seconds)

    seamless_clips: list[tuple[VideoAnalysis, int]] = []
    for index, (video_path, loop_count) in enumerate(normalized_video_inputs):
        if not video_path.exists():
            raise FileNotFoundError(f"Video clip not found: {video_path}")

        if settings.mode == "preview":
            seamless_clips.append((analyze_video(video_path), loop_count))
            continue

        seamless_output_path = temporary_dir / f"seamless_{index:04d}.mp4"
        seamless_clip = make_seamless_loop_clip(
            input_clip_path=video_path,
            output_clip_path=seamless_output_path,
            crossfade_seconds=seamless_crossfade_seconds,
            logger=logger,
        )
        seamless_clips.append((seamless_clip, loop_count))

    scenes = _build_scene_sequence(
        seamless_clips=seamless_clips,
        target_duration_seconds=target_duration_seconds,
        crossfade_seconds=settings.crossfade_seconds,
    )
    if not scenes:
        raise RuntimeError("Scene expansion did not produce any renderable scenes.")

    return target_duration_seconds, scenes


def _build_render_state_signature(
    chunked_scenes: list[list[SceneSegment]],
    settings: RenderSettings,
    target_duration_seconds: float,
) -> str:
    payload = {
        "mode": settings.mode,
        "fps": settings.fps,
        "resolution": [settings.width, settings.height],
        "crossfade_seconds": settings.crossfade_seconds,
        "target_duration_seconds": round(target_duration_seconds, 6),
        "chunks": [
            [
                {
                    "file": scene.file_path.name,
                    "duration": round(scene.duration_seconds, 6),
                    "loop_count": scene.loop_count,
                }
                for scene in chunk
            ]
            for chunk in chunked_scenes
        ],
    }
    payload_bytes = json.dumps(payload, sort_keys=True).encode("utf-8")
    return hashlib.sha256(payload_bytes).hexdigest()


def _load_render_state(
    state_path: Path,
    expected_signature: str,
    logger: logging.Logger | None = None,
) -> int:
    if not state_path.exists():
        return -1

    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        log_structured(logger, "render_state_invalid", path=str(state_path.resolve()))
        return -1

    if data.get("signature") != expected_signature:
        log_structured(logger, "render_state_signature_mismatch", path=str(state_path.resolve()))
        return -1

    completed_chunk_index = data.get("completed_chunk_index")
    if not isinstance(completed_chunk_index, int):
        return -1
    return max(-1, completed_chunk_index)


def _save_render_state(
    state_path: Path,
    completed_chunk_index: int,
    signature: str,
    logger: logging.Logger | None = None,
) -> None:
    payload = {
        "completed_chunk_index": completed_chunk_index,
        "timestamp": int(time.time()),
        "signature": signature,
    }
    state_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    log_structured(
        logger,
        "render_state_saved",
        path=str(state_path.resolve()),
        completed_chunk_index=completed_chunk_index,
    )


def preflight_render_check(
    scene_segments: list[SceneSegment],
    *,
    settings: RenderSettings,
    target_duration_seconds: float,
    scene_crossfade_seconds: float,
    temporary_dir: Path,
    logger: logging.Logger | None = None,
) -> dict[str, object]:
    if not scene_segments:
        raise ValueError("Preflight failed: no scene segments to validate.")

    issues: list[str] = []
    resolutions: set[tuple[int, int]] = set()
    codecs: set[str] = set()
    missing_streams: list[str] = []
    duration_mismatches: list[str] = []

    for scene in scene_segments:
        if scene.duration_seconds <= 0:
            issues.append(f"Duration consistency error: non-positive duration for {scene.file_path.name}.")
            continue

        media = _probe_media(scene.file_path)
        streams = media.get("streams", [])
        video_stream = next((stream for stream in streams if stream.get("codec_type") == "video"), None)
        if video_stream is None:
            missing_streams.append(scene.file_path.name)
            continue

        width = _parse_int(video_stream.get("width"))
        height = _parse_int(video_stream.get("height"))
        if width is not None and height is not None:
            resolutions.add((width, height))

        codec_name = str(video_stream.get("codec_name") or "").strip()
        if codec_name:
            codecs.add(codec_name)

        probed_duration = _parse_float(video_stream.get("duration")) or _parse_float(media.get("format", {}).get("duration"))
        if probed_duration is not None and abs(probed_duration - scene.duration_seconds) > 1.0:
            duration_mismatches.append(
                f"{scene.file_path.name} (probed={probed_duration:.2f}s scene={scene.duration_seconds:.2f}s)"
            )

    if duration_mismatches:
        issues.append("Duration consistency mismatch: " + ", ".join(duration_mismatches[:5]))

    if any(scene.duration_seconds <= scene_crossfade_seconds for scene in scene_segments):
        issues.append(
            f"Crossfade compatibility failure: at least one scene is shorter than {scene_crossfade_seconds:.2f}s."
        )

    if len(resolutions) > 1:
        issues.append("Resolution mismatches detected across scene inputs.")

    if len(codecs) > 1:
        issues.append("Codec mismatches detected across scene inputs.")

    if missing_streams:
        issues.append("Missing streams: " + ", ".join(missing_streams))

    assembled_duration = _assembled_duration_seconds(scene_segments, scene_crossfade_seconds)
    if assembled_duration + 0.25 < target_duration_seconds:
        issues.append(
            f"Duration consistency failure: assembled timeline {assembled_duration:.2f}s shorter than target {target_duration_seconds:.2f}s."
        )

    if issues:
        raise RuntimeError("Preflight failed: " + " | ".join(issues))

    preflight_filter_script = (temporary_dir / "preflight_filter.txt").resolve()
    filter_complex = _build_scene_filtergraph(
        scenes=scene_segments,
        target_duration_seconds=target_duration_seconds,
        crossfade_seconds=scene_crossfade_seconds,
        settings=settings,
    )
    _write_filter_script(filter_complex, preflight_filter_script)

    command: list[str] = ["ffmpeg", "-v", "error", "-y"]
    for scene in scene_segments:
        command.extend(["-i", str(scene.file_path)])
    command.extend(
        [
            "-filter_complex_script",
            str(preflight_filter_script),
            "-map",
            "[vout]",
            "-f",
            "null",
            "-",
        ]
    )

    try:
        _run_command(command, error_prefix="Preflight dry-run failed", logger=logger)
    finally:
        if preflight_filter_script.exists():
            preflight_filter_script.unlink(missing_ok=True)

    summary = {
        "target_duration_seconds": target_duration_seconds,
        "scene_count": len(scene_segments),
        "resolution_variants": len(resolutions),
        "codec_variants": len(codecs),
    }
    log_structured(logger, "preflight_passed", **summary)
    return summary


def run_render_preflight(
    audio_mix_path: Path,
    ordered_video_paths: list[Path] | list[tuple[Path, int]],
    render_profile: str = DEFAULT_RENDER_PROFILE,
    work_dir: Path | None = None,
    seamless_crossfade_seconds: float = SEAMLESS_LOOP_CROSSFADE_SECONDS,
    scene_crossfade_seconds: float | None = None,
    logger: logging.Logger | None = None,
) -> dict[str, object]:
    if not ordered_video_paths:
        raise ValueError("No video clips provided.")

    _require_ffmpeg_tools()
    settings = _build_render_settings(render_profile, scene_crossfade_seconds)
    normalized_video_inputs = _normalize_ordered_video_inputs(ordered_video_paths)

    temporary_dir = work_dir or audio_mix_path.parent / "video_work"
    temporary_dir.mkdir(parents=True, exist_ok=True)

    target_duration_seconds, scenes = _prepare_scene_segments(
        audio_mix_path=audio_mix_path,
        normalized_video_inputs=normalized_video_inputs,
        temporary_dir=temporary_dir,
        settings=settings,
        seamless_crossfade_seconds=seamless_crossfade_seconds,
        logger=logger,
    )

    preflight = preflight_render_check(
        scene_segments=scenes,
        settings=settings,
        target_duration_seconds=target_duration_seconds,
        scene_crossfade_seconds=settings.crossfade_seconds,
        temporary_dir=temporary_dir,
        logger=logger,
    )

    return {
        "ok": True,
        "mode": settings.mode,
        "fps": settings.fps,
        "resolution": [settings.width, settings.height],
        **preflight,
    }


def render_final_video(
    audio_mix_path: Path,
    ordered_video_paths: list[Path] | list[tuple[Path, int]],
    output_path: Path,
    render_profile: str = DEFAULT_RENDER_PROFILE,
    work_dir: Path | None = None,
    seamless_crossfade_seconds: float = SEAMLESS_LOOP_CROSSFADE_SECONDS,
    scene_crossfade_seconds: float | None = None,
    on_progress: Callable[[float, float], None] | None = None,
    keep_intermediate_files: bool = False,
    logger: logging.Logger | None = None,
) -> tuple[Path, str]:
    if not ordered_video_paths:
        raise ValueError("No video clips provided.")

    _require_ffmpeg_tools()
    settings = _build_render_settings(render_profile, scene_crossfade_seconds)
    normalized_video_inputs = _normalize_ordered_video_inputs(ordered_video_paths)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_dir = work_dir or output_path.parent / "video_work"
    temporary_dir.mkdir(parents=True, exist_ok=True)

    target_duration_seconds, scenes = _prepare_scene_segments(
        audio_mix_path=audio_mix_path,
        normalized_video_inputs=normalized_video_inputs,
        temporary_dir=temporary_dir,
        settings=settings,
        seamless_crossfade_seconds=seamless_crossfade_seconds,
        logger=logger,
    )
    log_structured(
        logger,
        "video_render_start",
        active_render_profile=settings.mode,
        clip_sequence=[video_path.name for video_path, _ in normalized_video_inputs],
        loop_counts=[loop_count for _, loop_count in normalized_video_inputs],
        resolution=[settings.width, settings.height],
        target_duration_seconds=target_duration_seconds,
    )
    preflight_render_check(
        scene_segments=scenes,
        settings=settings,
        target_duration_seconds=target_duration_seconds,
        scene_crossfade_seconds=settings.crossfade_seconds,
        temporary_dir=temporary_dir,
        logger=logger,
    )

    progress_state = RenderProgressState(
        total_duration_seconds=target_duration_seconds,
        start_time=time.time(),
    )
    gpu_ready = settings.mode != "preview" and detect_gpu_pipeline() and detect_h264_encoder() == "h264_nvenc"
    log_structured(
        logger,
        "encoder_selection",
        gpu_ready=gpu_ready,
        mode=settings.mode,
    )

    if settings.mode == "performance":
        if not gpu_ready:
            raise RuntimeError("Performance mode requires a CUDA/NVENC GPU, but no compatible GPU was detected.")
        _render_gpu_composite(
            scenes=scenes,
            audio_mix_path=audio_mix_path,
            output_path=output_path,
            temporary_dir=temporary_dir,
            target_duration_seconds=target_duration_seconds,
            settings=settings,
            progress_state=progress_state,
            scene_crossfade_seconds=settings.crossfade_seconds,
            on_progress=on_progress,
            logger=logger,
        )
        log_structured(logger, "video_render_complete", encoder="h264_nvenc", output_path=str(output_path.resolve()))
        return output_path, "h264_nvenc"

    encoder = "h264_nvenc" if gpu_ready else DEFAULT_VIDEO_CODEC
    if encoder == "h264_nvenc":
        try:
            _render_gpu_composite(
                scenes=scenes,
                audio_mix_path=audio_mix_path,
                output_path=output_path,
                temporary_dir=temporary_dir,
                target_duration_seconds=target_duration_seconds,
                settings=settings,
                progress_state=progress_state,
                scene_crossfade_seconds=settings.crossfade_seconds,
                on_progress=on_progress,
                logger=logger,
            )
            log_structured(logger, "video_render_complete", encoder=encoder, output_path=str(output_path.resolve()))
            return output_path, encoder
        except RuntimeError as exc:
            print(f"GPU render failed in '{settings.mode}' mode. Falling back to CPU libx264: {exc}", file=sys.stderr)
            log_structured(logger, "encoder_fallback", from_encoder="h264_nvenc", to_encoder=DEFAULT_VIDEO_CODEC)
            encoder = DEFAULT_VIDEO_CODEC
    else:
        print(f"GPU unavailable. Falling back to CPU libx264 for '{settings.mode}' mode.", file=sys.stderr)

    chunked_scenes = _chunk_list(scenes, CHUNK_SIZE)
    chunk_paths: list[Path] = []
    stitched_video_path: Path | None = None
    chunk_concat_path = temporary_dir / CHUNK_CONCAT_FILENAME
    stitched_intermediate_path = temporary_dir / "stitched_video.mp4"
    state_path = temporary_dir / RENDER_STATE_FILENAME
    state_signature = _build_render_state_signature(chunked_scenes, settings, target_duration_seconds)
    completed_chunk_index = _load_render_state(state_path, state_signature, logger=logger)
    render_completed = False
    remaining_duration_seconds = target_duration_seconds
    processed_duration_seconds = 0.0

    try:
        for chunk_index, chunk_scenes in enumerate(chunked_scenes):
            if remaining_duration_seconds <= 0:
                break

            full_chunk_duration = _assembled_duration_seconds(chunk_scenes, settings.crossfade_seconds)
            chunk_target_duration = min(full_chunk_duration, remaining_duration_seconds)
            if chunk_target_duration <= 0:
                continue

            existing_chunk_path = temporary_dir / f"chunk_{chunk_index:04d}.mp4"
            if chunk_index <= completed_chunk_index and existing_chunk_path.exists():
                chunk_paths.append(existing_chunk_path)
                remaining_duration_seconds -= chunk_target_duration
                processed_duration_seconds += chunk_target_duration
                log_structured(
                    logger,
                    "chunk_resume_skip",
                    chunk_index=chunk_index,
                    chunk_path=str(existing_chunk_path.resolve()),
                )
                continue

            chunk_path = _render_scene_chunk(
                chunk_scenes=chunk_scenes,
                chunk_index=chunk_index,
                temporary_dir=temporary_dir,
                encoder=encoder,
                settings=settings,
                chunk_target_duration=chunk_target_duration,
                progress_state=progress_state,
                progress_offset_seconds=processed_duration_seconds,
                scene_crossfade_seconds=settings.crossfade_seconds,
                on_progress=on_progress,
                logger=logger,
                final_filter_filename=FINAL_FILTER_SCRIPT_FILENAME,
            )
            chunk_paths.append(chunk_path)
            remaining_duration_seconds -= chunk_target_duration
            processed_duration_seconds += chunk_target_duration
            _save_render_state(state_path, completed_chunk_index=chunk_index, signature=state_signature, logger=logger)
            completed_chunk_index = chunk_index

        if not chunk_paths:
            raise RuntimeError("No video chunks were rendered for final assembly.")

        stitched_video_path = _stitch_chunks(
            chunk_paths=chunk_paths,
            chunk_concat_path=chunk_concat_path,
            stitched_video_path=stitched_intermediate_path,
            logger=logger,
        )
        _mux_audio_once(
            stitched_video_path=stitched_video_path,
            audio_mix_path=audio_mix_path,
            output_path=output_path,
            logger=logger,
        )
        render_completed = True
        log_structured(logger, "video_render_complete", encoder=encoder, output_path=str(output_path.resolve()))
    finally:
        if chunk_concat_path.exists():
            chunk_concat_path.unlink(missing_ok=True)

        if render_completed and state_path.exists():
            state_path.unlink(missing_ok=True)
        elif not render_completed:
            log_structured(
                logger,
                "resume_state_retained",
                state_path=str(state_path.resolve()),
                completed_chunk_index=completed_chunk_index,
            )

        if render_completed and not keep_intermediate_files:
            for chunk_path in chunk_paths:
                if chunk_path.exists():
                    chunk_path.unlink(missing_ok=True)

            if stitched_video_path is not None and stitched_video_path.exists() and stitched_video_path not in chunk_paths:
                stitched_video_path.unlink(missing_ok=True)
            elif stitched_video_path is None and stitched_intermediate_path.exists():
                stitched_intermediate_path.unlink(missing_ok=True)

    return output_path, encoder
