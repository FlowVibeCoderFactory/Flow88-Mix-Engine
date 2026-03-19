from __future__ import annotations

import hashlib
import json
import logging
import re
import shlex
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from models import VideoAnalysis
from render_logging import log_structured


SUPPORTED_VIDEO_SUFFIXES = {".mp4", ".mov", ".mkv", ".webm", ".m4v"}
SEAMLESS_LOOP_CROSSFADE_SECONDS = 1.5
DEFAULT_AUDIO_CODEC = "pcm_s24le"
DEFAULT_VIDEO_CODEC = "libx264"
MAX_SCENE_SEGMENTS = 600
PREVIEW_LOOP_FILENAME = "loop_preview.mp4"
CHUNK_SIZE = 9999
CHUNK_CONCAT_FILENAME = "chunks.txt"
LOOP_FILTER_SCRIPT_FILENAME = "loop_filter.txt"
TRANSITION_GRAPH_FILENAME = "transition_graph.txt"
PREFLIGHT_TRANSITION_GRAPH_FILENAME = "preflight_transition_graph.txt"
RENDER_STATE_FILENAME = "render_state.json"
MAX_PREVIEW_TIMELINE_SECONDS = 60.0
PREVIEW_OUTPUT_FILENAME = "output_preview.mp4"
DEFAULT_RENDER_PROFILE = "balanced"
DEFAULT_TRANSITION_ENABLED = True
DEFAULT_TRANSITION_TYPE = "fade"
DEFAULT_TRANSITION_DURATION_SECONDS = 1.0
DEFAULT_TRANSITION_CURVE = "linear"
MIN_TRANSITION_DURATION_SECONDS = 0.2
MAX_TRANSITION_DURATION_SECONDS = 3.0
SUPPORTED_TRANSITION_CURVES = {"linear", "easein", "easeout"}
TRANSITION_TYPE_PATTERN = re.compile(r"^[A-Za-z0-9_]+$")
PREVIEW_XFADE_WIDTH = 1920
PREVIEW_XFADE_HEIGHT = 1080
PREVIEW_XFADE_FPS = 30
PREVIEW_XFADE_PIX_FMT = "yuv420p"
MIN_SCENE_DURATION_EPSILON = 0.001
NVENC_PROBE_WIDTH = 1280
NVENC_PROBE_HEIGHT = 720
NVENC_PROBE_FPS = 30
NVENC_PROBE_FRAMES = 30
NVENC_PROBE_PRESET = "p3"
RENDER_PROFILES = {
    "preview": {
        "fps": 12,
        "resolution": (640, 360),
        "nvenc_preset": "p1",
        "cq": 35,
        "crossfade": 1.0,
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
        "crossfade": 1.0,
    },
    "quality": {
        "fps": 30,
        "resolution": (3840, 2160),
        "nvenc_preset": "p7",
        "cq": 16,
        "crossfade": 1.0,
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
class TransitionConfig:
    enabled: bool = DEFAULT_TRANSITION_ENABLED
    transition_type: str = DEFAULT_TRANSITION_TYPE
    duration_seconds: float = DEFAULT_TRANSITION_DURATION_SECONDS
    curve: str = DEFAULT_TRANSITION_CURVE

    @property
    def overlap_seconds(self) -> float:
        if not self.enabled:
            return 0.0
        return max(0.0, self.duration_seconds)


@dataclass(slots=True, frozen=True)
class RenderSettings:
    mode: str
    width: int
    height: int
    fps: int
    transition: TransitionConfig
    nvenc_preset: str
    cq: int
    enable_scaling: bool = True
    enable_padding: bool = True
    enable_color_conversion: bool = True
    preview_timeline_limit_seconds: float | None = None
    cpu_preset: str = "medium"
    cpu_crf: int = 18

    @property
    def transition_overlap_seconds(self) -> float:
        return self.transition.overlap_seconds


@dataclass(slots=True, frozen=True)
class FFmpegCapabilities:
    ffmpeg_path: str | None
    ffprobe_path: str | None
    encoders: tuple[str, ...]
    hwaccels: tuple[str, ...]
    nvenc_runtime_available: bool
    nvenc_runtime_error: str | None
    nvenc_probe_command: tuple[str, ...] | None
    nvenc_probe_result: str | None

    @property
    def nvenc_available(self) -> bool:
        return "h264_nvenc" in self.encoders

    @property
    def cuda_hwaccel_available(self) -> bool:
        return "cuda" in self.hwaccels

    @property
    def preferred_h264_encoder(self) -> str:
        if self.nvenc_runtime_available:
            return "h264_nvenc"
        return DEFAULT_VIDEO_CODEC

    def as_dict(self) -> dict[str, object]:
        return {
            "ffmpeg_path": self.ffmpeg_path,
            "ffprobe_path": self.ffprobe_path,
            "encoders": list(self.encoders),
            "hwaccels": list(self.hwaccels),
            "nvenc_available": self.nvenc_available,
            "cuda_hwaccel_available": self.cuda_hwaccel_available,
            "nvenc_runtime_available": self.nvenc_runtime_available,
            "nvenc_runtime_error": self.nvenc_runtime_error,
            "nvenc_probe_command": shlex.join(self.nvenc_probe_command) if self.nvenc_probe_command is not None else None,
            "nvenc_probe_result": self.nvenc_probe_result,
            "preferred_h264_encoder": self.preferred_h264_encoder,
        }


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


def _parse_bool(value: object, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return bool(value)


def _normalize_transition_type(value: object) -> str:
    transition_type = str(value or DEFAULT_TRANSITION_TYPE).strip().lower()
    if not transition_type:
        transition_type = DEFAULT_TRANSITION_TYPE
    if not TRANSITION_TYPE_PATTERN.fullmatch(transition_type):
        raise ValueError(
            f"Invalid transition type '{transition_type}'. Use letters, numbers, and underscores only."
        )
    return transition_type


def _normalize_transition_curve(value: object) -> str:
    curve = str(value or DEFAULT_TRANSITION_CURVE).strip().lower()
    if not curve:
        curve = DEFAULT_TRANSITION_CURVE
    if curve not in SUPPORTED_TRANSITION_CURVES:
        raise ValueError(
            f"Invalid transition curve '{curve}'. Supported: {', '.join(sorted(SUPPORTED_TRANSITION_CURVES))}."
        )
    return curve


def _normalize_transition_duration(value: object) -> float:
    duration = _parse_float(value)
    if duration is None:
        raise ValueError("Transition duration must be a positive number.")
    if duration < MIN_TRANSITION_DURATION_SECONDS or duration > MAX_TRANSITION_DURATION_SECONDS:
        raise ValueError(
            f"Transition duration must be between {MIN_TRANSITION_DURATION_SECONDS:.1f}s and "
            f"{MAX_TRANSITION_DURATION_SECONDS:.1f}s."
        )
    return float(duration)


def _normalize_transition_config(transition: TransitionConfig | dict[str, object] | None) -> TransitionConfig:
    if isinstance(transition, TransitionConfig):
        return transition

    if transition is None:
        payload: dict[str, object] = {}
    elif isinstance(transition, dict):
        payload = transition
    else:
        raise ValueError("Transition config must be a dictionary with enabled/type/duration/curve fields.")

    enabled = _parse_bool(payload.get("enabled"), DEFAULT_TRANSITION_ENABLED)
    transition_type = _normalize_transition_type(payload.get("type"))
    duration_seconds = _normalize_transition_duration(payload.get("duration", DEFAULT_TRANSITION_DURATION_SECONDS))
    curve = _normalize_transition_curve(payload.get("curve"))
    return TransitionConfig(
        enabled=enabled,
        transition_type=transition_type,
        duration_seconds=duration_seconds,
        curve=curve,
    )


def _apply_mode_transition_adjustments(transition: TransitionConfig, mode: str) -> TransitionConfig:
    if mode != "preview" or not transition.enabled:
        return transition

    return TransitionConfig(
        enabled=True,
        transition_type=transition.transition_type,
        duration_seconds=max(0.001, transition.duration_seconds * 0.5),
        curve=transition.curve,
    )


def _resolve_xfade_transition_name(transition: TransitionConfig) -> str:
    if transition.transition_type != "fade":
        return transition.transition_type
    if transition.curve == "easein":
        return "fadeslow"
    if transition.curve == "easeout":
        return "fadefast"
    return "fade"


def _run_command(command: list[str], error_prefix: str, logger: logging.Logger | None = None) -> str:
    if command and command[0] == "ffmpeg":
        log_structured(logger, "ffmpeg_call", command=command, stage=error_prefix)
    command_text = shlex.join(command)
    try:
        result = subprocess.run(command, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        if command and command[0] == "ffmpeg" and logger is not None:
            logger.exception("Render failed")
        stderr = exc.stderr.strip() if exc.stderr else ""
        details = stderr or (exc.stdout.strip() if exc.stdout else "") or "command failed with no output."
        raise RuntimeError(f"{error_prefix}: {details} | command: {command_text}") from exc

    return (result.stdout or "") + (result.stderr or "")


def _parse_ffmpeg_encoders(output: str) -> tuple[str, ...]:
    encoders: list[str] = []
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("Encoders:") or line.startswith("------"):
            continue

        parts = line.split()
        if len(parts) >= 2 and set(parts[0]) <= {".", "V", "A", "S", "D", "F"}:
            encoders.append(parts[1])

    return tuple(sorted(set(encoders)))


def _parse_ffmpeg_hwaccels(output: str) -> tuple[str, ...]:
    hwaccels: list[str] = []
    for raw_line in output.splitlines():
        line = raw_line.strip().lower()
        if not line or line.startswith("hardware acceleration methods"):
            continue
        if " " in line or "\t" in line:
            continue
        hwaccels.append(line)

    return tuple(sorted(set(hwaccels)))


def _build_nvenc_probe_command(ffmpeg_path: str) -> list[str]:
    return [
        ffmpeg_path,
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        f"testsrc2=size={NVENC_PROBE_WIDTH}x{NVENC_PROBE_HEIGHT}:rate={NVENC_PROBE_FPS}",
        "-frames:v",
        str(NVENC_PROBE_FRAMES),
        "-vf",
        f"format={PREVIEW_XFADE_PIX_FMT}",
        "-an",
        "-c:v",
        "h264_nvenc",
        "-preset",
        NVENC_PROBE_PRESET,
        "-pix_fmt",
        PREVIEW_XFADE_PIX_FMT,
        "-f",
        "null",
        "-",
    ]


def _probe_nvenc_runtime(ffmpeg_path: str) -> tuple[bool, tuple[str, ...], str | None]:
    command = tuple(_build_nvenc_probe_command(ffmpeg_path))
    try:
        result = subprocess.run(
            list(command),
            check=True,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        return False, command, str(exc)
    except subprocess.CalledProcessError as exc:
        error_text = (exc.stderr or exc.stdout or "").strip()
        return False, command, error_text or "NVENC probe failed."

    output_text = (result.stderr or result.stdout or "").strip()
    return True, command, output_text or "NVENC probe succeeded."


@lru_cache(maxsize=1)
def _get_ffmpeg_capabilities_cached() -> FFmpegCapabilities:
    ffmpeg_path = shutil.which("ffmpeg")
    ffprobe_path = shutil.which("ffprobe")

    encoders: tuple[str, ...] = ()
    hwaccels: tuple[str, ...] = ()
    nvenc_runtime_available = False
    nvenc_runtime_error: str | None = None
    nvenc_probe_command: tuple[str, ...] | None = None
    nvenc_probe_result: str | None = "Skipped: ffmpeg not found."

    if ffmpeg_path is not None:
        nvenc_probe_result = "Skipped: h264_nvenc encoder not detected."
        try:
            encoders_output = _run_command(
                ["ffmpeg", "-hide_banner", "-encoders"],
                error_prefix="Failed to query FFmpeg encoders",
            )
            encoders = _parse_ffmpeg_encoders(encoders_output)
        except RuntimeError:
            encoders = ()

        try:
            hwaccel_output = _run_command(
                ["ffmpeg", "-hide_banner", "-hwaccels"],
                error_prefix="Failed to query FFmpeg hwaccels",
            )
            hwaccels = _parse_ffmpeg_hwaccels(hwaccel_output)
        except RuntimeError:
            hwaccels = ()

        if "h264_nvenc" in encoders:
            nvenc_runtime_available, nvenc_probe_command, nvenc_probe_result = _probe_nvenc_runtime(ffmpeg_path)
            if not nvenc_runtime_available:
                nvenc_runtime_error = nvenc_probe_result

    return FFmpegCapabilities(
        ffmpeg_path=ffmpeg_path,
        ffprobe_path=ffprobe_path,
        encoders=encoders,
        hwaccels=hwaccels,
        nvenc_runtime_available=nvenc_runtime_available,
        nvenc_runtime_error=nvenc_runtime_error,
        nvenc_probe_command=nvenc_probe_command,
        nvenc_probe_result=nvenc_probe_result,
    )


def get_ffmpeg_capabilities(logger: logging.Logger | None = None) -> FFmpegCapabilities:
    capabilities = _get_ffmpeg_capabilities_cached()
    if logger is not None:
        log_structured(logger, "ffmpeg_capabilities", **capabilities.as_dict())
    return capabilities


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
    command_text = shlex.join(command)
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
        raise RuntimeError(f"{error_prefix}: {exc} | command: {command_text}") from exc

    if process.stdout is None:
        raise RuntimeError(f"{error_prefix}: unable to stream ffmpeg output. | command: {command_text}")

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
        raise RuntimeError(f"{error_prefix}: {details} | command: {command_text}")


def _write_filter_script(filter_complex: str, script_path: Path) -> None:
    script_path.write_text(filter_complex, encoding="utf-8")


def _loop_cache_path(input_path: Path, cache_dir: Path, settings: RenderSettings) -> Path:
    return cache_dir / f"{input_path.stem}_{settings.width}x{settings.height}_loop.mp4"


def _escape_concat_path(file_path: Path) -> str:
    absolute_posix = str(file_path.resolve()).replace("\\", "/")
    return absolute_posix.replace("'", "\\'")


def _write_concat_inputs_file(input_paths: list[Path], concat_file_path: Path) -> None:
    concat_lines = [f"file '{_escape_concat_path(path)}'" for path in input_paths]
    concat_file_path.write_text("\n".join(concat_lines) + "\n", encoding="utf-8")


def _build_scene_input_arguments(scene_segments: list[SceneSegment]) -> list[str]:
    if not scene_segments:
        raise ValueError("Cannot build FFmpeg inputs for an empty scene list.")

    input_arguments: list[str] = []
    for scene in scene_segments:
        input_arguments.extend(["-i", str(scene.file_path)])

    return input_arguments


def _chunk_list(items: list, size: int) -> list[list]:
    if size <= 0:
        raise ValueError("Chunk size must be greater than zero.")
    return [items[index : index + size] for index in range(0, len(items), size)]


def _assembled_duration_seconds(scenes: list[SceneSegment], transition_overlap_seconds: float) -> float:
    if not scenes:
        return 0.0

    total_duration = scenes[0].duration_seconds
    for scene in scenes[1:]:
        total_duration += max(0.001, scene.duration_seconds - transition_overlap_seconds)
    return max(0.0, total_duration)


def _require_ffmpeg_tools() -> None:
    capabilities = get_ffmpeg_capabilities()
    if capabilities.ffmpeg_path is None:
        raise RuntimeError("ffmpeg not found in PATH.")
    if capabilities.ffprobe_path is None:
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
    return get_ffmpeg_capabilities().preferred_h264_encoder


def detect_gpu_pipeline() -> bool:
    return get_ffmpeg_capabilities().nvenc_runtime_available


def make_seamless_loop_clip(
    input_clip_path: Path,
    output_clip_path: Path,
    settings: RenderSettings,
    crossfade_seconds: float = SEAMLESS_LOOP_CROSSFADE_SECONDS,
    logger: logging.Logger | None = None,
) -> VideoAnalysis:
    cache_dir = output_clip_path.parent
    cache_dir.mkdir(parents=True, exist_ok=True)
    looped_path = _loop_cache_path(input_clip_path, cache_dir, settings) 
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

    prep_filters = f"fps={settings.fps}"
    if settings.enable_scaling:
        prep_filters += f",scale={settings.width}:{settings.height}:force_original_aspect_ratio=decrease"
    if settings.enable_padding:
        prep_filters += f",pad={settings.width}:{settings.height}:(ow-iw)/2:(oh-ih)/2:color=black"

    filter_complex = (
        "split=2[first_raw][second_raw];"
        f"[first_raw]trim=start=0:end={midpoint_seconds:.6f},setpts=PTS-STARTPTS[first_half];"
        f"[second_raw]trim=start={midpoint_seconds:.6f}:end={clip_duration:.6f},setpts=PTS-STARTPTS[second_half];"
        f"[second_half][first_half]xfade=transition=fade:duration={crossfade_seconds:.6f}:offset={xfade_offset:.6f},"
        f"{prep_filters},format=yuv420p,setsar=1" # <--- INJECTED HERE
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
    transition_overlap_seconds: float,
) -> list[SceneSegment]:
    if not seamless_clips:
        raise ValueError("No seamless clips provided for scene assembly.")
    if transition_overlap_seconds < 0:
        raise ValueError("Transition overlap seconds cannot be negative.")

    validated_clips: list[tuple[VideoAnalysis, int]] = []
    for clip, loop_count in seamless_clips:
        if loop_count <= 0:
            raise ValueError(f"Invalid loop count for clip '{clip.file_path.name}': {loop_count}")
        
        # FIX: We must check the base clip duration, not the multiplied duration, 
        # because every single loop will now have a transition overlap!
        clip_duration = clip.playable_duration_seconds
        if clip_duration <= 0:
            raise ValueError(f"Clip '{clip.file_path.name}' has a non-positive duration.")
        if transition_overlap_seconds > 0 and clip_duration <= transition_overlap_seconds:
            raise ValueError(
                f"Clip '{clip.file_path.name}' is too short for a "
                f"{transition_overlap_seconds:.1f}s transition overlap."
            )
        validated_clips.append((clip, loop_count))

    if not validated_clips:
        raise ValueError("No seamless clips available after applying loop counts.")

    sequence: list[SceneSegment] = []
    assembled_duration = 0.0
    clip_index = 0

    while assembled_duration < target_duration_seconds + transition_overlap_seconds:
        clip, loop_count = validated_clips[clip_index]

        # FIX: Flatten the loops. Instead of 1 scene with a loop_count of 5,
        # we create 5 individual scenes. This forces xfade to run between them!
        for _ in range(loop_count):
            if assembled_duration >= target_duration_seconds + transition_overlap_seconds:
                break

            sequence.append(
                SceneSegment(
                    file_path=clip.file_path,
                    duration_seconds=clip.playable_duration_seconds,
                    loop_count=1, # Hardcode to 1 so FFmpeg doesn't use -stream_loop
                )
            )

            if len(sequence) == 1:
                assembled_duration += clip.playable_duration_seconds
            else:
                assembled_duration += max(0.001, clip.playable_duration_seconds - transition_overlap_seconds)

        clip_index = (clip_index + 1) % len(validated_clips)

    return sequence


def _build_scene_filtergraph(
    scenes: list[SceneSegment],
    target_duration_seconds: float,
    transition: TransitionConfig,
    settings: RenderSettings,
) -> str:
    if not scenes:
        raise ValueError("Cannot build a scene filtergraph without scenes.")

    filter_parts: list[str] = []
    for index, scene in enumerate(scenes):
        chain_parts = [f"trim=duration={scene.duration_seconds:.6f}"]
        if settings.mode == "preview":
            chain_parts.extend(
                [
                    f"scale={PREVIEW_XFADE_WIDTH}:{PREVIEW_XFADE_HEIGHT}:force_original_aspect_ratio=decrease",
                    f"pad={PREVIEW_XFADE_WIDTH}:{PREVIEW_XFADE_HEIGHT}:(ow-iw)/2:(oh-ih)/2:color=black",
                    f"fps={PREVIEW_XFADE_FPS}",
                    f"format={PREVIEW_XFADE_PIX_FMT}",
                    "setsar=1",
                ]
            )
        chain_parts.append("setpts=PTS-STARTPTS")

        source_label = f"{index}:v"
        filter_parts.append(f"[{source_label}]{','.join(chain_parts)}[v{index}]")

    output_chain_parts = [f"trim=duration={target_duration_seconds:.6f}"]
    if settings.mode == "preview":
        output_chain_parts.extend(
            [
                f"scale={settings.width}:{settings.height}:force_original_aspect_ratio=decrease",
                f"pad={settings.width}:{settings.height}:(ow-iw)/2:(oh-ih)/2:color=black",
                f"fps={settings.fps}",
                f"format={PREVIEW_XFADE_PIX_FMT}",
                "setsar=1",
            ]
        )
    output_chain_parts.append("setpts=PTS-STARTPTS")
    output_chain = ",".join(output_chain_parts)

    if len(scenes) == 1:
        filter_parts.append(f"[v0]{output_chain}[vout]")
        return ";".join(filter_parts)

    if not transition.enabled or transition.overlap_seconds <= 0:
        concat_inputs = "".join(f"[v{index}]" for index in range(len(scenes)))
        filter_parts.append(f"{concat_inputs}concat=n={len(scenes)}:v=1:a=0[vcat]")
        filter_parts.append(f"[vcat]{output_chain}[vout]")
        return ";".join(filter_parts)

    transition_name = _resolve_xfade_transition_name(transition)
    timeline_cursor = scenes[0].duration_seconds
    current_label = "v0"

    for index in range(1, len(scenes)):
        next_label = f"x{index}"
        previous_scene = scenes[index - 1]
        current_scene = scenes[index]
        max_transition_duration = min(
            previous_scene.duration_seconds - MIN_SCENE_DURATION_EPSILON,
            current_scene.duration_seconds - MIN_SCENE_DURATION_EPSILON,
        )
        if max_transition_duration <= 0:
            raise ValueError(
                f"Scene transition invalid between '{previous_scene.file_path.name}' and "
                f"'{current_scene.file_path.name}': at least one scene is too short for xfade."
            )
        xfade_duration = min(transition.duration_seconds, max_transition_duration)
        xfade_offset = max(0.0, timeline_cursor - xfade_duration)

        filter_parts.append(
            f"[{current_label}][v{index}]xfade="
            f"transition={transition_name}:"
            f"duration={xfade_duration:.6f}:"
            f"offset={xfade_offset:.6f}"
            f"[{next_label}]"
        )

        current_label = next_label
        timeline_cursor += current_scene.duration_seconds - xfade_duration

    filter_parts.append(f"[{current_label}]{output_chain}[vout]")
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
    transition: TransitionConfig,
    on_progress: Callable[[float, float], None] | None,
    logger: logging.Logger | None = None,
    final_filter_filename: str = TRANSITION_GRAPH_FILENAME,
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
        transition=transition,
        settings=settings,
    )
    _write_filter_script(filter_complex, filter_script_path)

    command: list[str] = [
        "ffmpeg",
        "-y",
    ]
    command.extend(_build_scene_input_arguments(chunk_scenes))
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

    if encoder == "h264_nvenc":
        command.extend(
            [
                "-c:v",
                "h264_nvenc",
                "-preset",
                settings.nvenc_preset,
                "-pix_fmt",
                PREVIEW_XFADE_PIX_FMT,
                "-rc",
                "vbr",
                "-cq",
                str(settings.cq),
                "-b:v",
                "0",
            ]
        )
    else:
        if settings.enable_color_conversion:
            command.extend(["-pix_fmt", PREVIEW_XFADE_PIX_FMT])
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


def _build_render_settings(
    render_profile: str,
    scene_crossfade_seconds: float | None,
    transition_config: TransitionConfig | dict[str, object] | None = None,
) -> RenderSettings:
    mode, profile = _resolve_render_profile(render_profile)
    fps = int(profile["fps"])
    width, height = profile["resolution"]
    if transition_config is None:
        transition = TransitionConfig(duration_seconds=float(profile["crossfade"]))
    else:
        transition = _normalize_transition_config(transition_config)

    if scene_crossfade_seconds is not None:
        overridden_duration = float(scene_crossfade_seconds)
        if overridden_duration <= 0:
            raise ValueError("Scene crossfade seconds must be greater than zero.")
        transition = TransitionConfig(
            enabled=True,
            transition_type=transition.transition_type,
            duration_seconds=overridden_duration,
            curve=transition.curve,
        )

    preview_mode = mode == "preview"
    mode_transition = _apply_mode_transition_adjustments(transition, mode)
    return RenderSettings(
        mode=mode,
        width=int(width),
        height=int(height),
        fps=fps,
        transition=mode_transition,
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
            settings=settings,
            crossfade_seconds=seamless_crossfade_seconds,
            logger=logger,
        )
        seamless_clips.append((seamless_clip, loop_count))

    scenes = _build_scene_sequence(
        seamless_clips=seamless_clips,
        target_duration_seconds=target_duration_seconds,
        transition_overlap_seconds=settings.transition_overlap_seconds,
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
        "transition": {
            "enabled": settings.transition.enabled,
            "type": settings.transition.transition_type,
            "duration_seconds": round(settings.transition.duration_seconds, 6),
            "curve": settings.transition.curve,
        },
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
    target_duration_seconds: float,
    settings: RenderSettings,
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
        if probed_duration is not None:
            expected_scene_duration = probed_duration * scene.loop_count
            if abs(expected_scene_duration - scene.duration_seconds) > 1.0:
                duration_mismatches.append(
                    f"{scene.file_path.name} (probed={probed_duration:.2f}s loops={scene.loop_count} "
                    f"expected={expected_scene_duration:.2f}s scene={scene.duration_seconds:.2f}s)"
                )

    if duration_mismatches:
        issues.append("Duration consistency mismatch: " + ", ".join(duration_mismatches[:5]))

    transition_overlap_seconds = settings.transition_overlap_seconds
    if transition_overlap_seconds > 0 and any(
        scene.duration_seconds <= transition_overlap_seconds for scene in scene_segments
    ):
        issues.append(
            f"Transition compatibility failure: at least one scene is shorter than {transition_overlap_seconds:.2f}s."
        )

    if len(resolutions) > 1:
        issues.append("Resolution mismatches detected across scene inputs.")

    if len(codecs) > 1:
        issues.append("Codec mismatches detected across scene inputs.")

    if missing_streams:
        issues.append("Missing streams: " + ", ".join(missing_streams))

    assembled_duration = _assembled_duration_seconds(scene_segments, transition_overlap_seconds)
    if assembled_duration + 0.25 < target_duration_seconds:
        issues.append(
            f"Duration consistency failure: assembled timeline {assembled_duration:.2f}s shorter than target {target_duration_seconds:.2f}s."
        )

    if issues:
        raise RuntimeError("Preflight failed: " + " | ".join(issues))


    summary = {
        "target_duration_seconds": target_duration_seconds,
        "scene_count": len(scene_segments),
        "resolution_variants": len(resolutions),
        "codec_variants": len(codecs),
        "transition_enabled": settings.transition.enabled,
        "transition_type": settings.transition.transition_type,
        "transition_duration_seconds": settings.transition.duration_seconds,
        "transition_curve": settings.transition.curve,
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
    transition_config: TransitionConfig | dict[str, object] | None = None,
    logger: logging.Logger | None = None,
) -> dict[str, object]:
    if not ordered_video_paths:
        raise ValueError("No video clips provided.")

    _require_ffmpeg_tools()
    settings = _build_render_settings(render_profile, scene_crossfade_seconds, transition_config=transition_config)
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
        target_duration_seconds=target_duration_seconds,
        settings=settings,
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
    ordered_video_paths: list[tuple[Path, int]],
    output_path: Path,
    render_profile: str = DEFAULT_RENDER_PROFILE,
    work_dir: Path | None = None,
    seamless_crossfade_seconds: float = SEAMLESS_LOOP_CROSSFADE_SECONDS,
    scene_crossfade_seconds: float | None = None,
    transition_config: TransitionConfig | dict[str, object] | None = None,
    on_progress: Callable[[float, float], None] | None = None,
    keep_intermediate_files: bool = False,
    logger: logging.Logger | None = None,
) -> tuple[Path, str]:
    if not ordered_video_paths:
        raise ValueError("No video clips provided.")

    _require_ffmpeg_tools()
    settings = _build_render_settings(render_profile, scene_crossfade_seconds, transition_config=transition_config)
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
    
    progress_state = RenderProgressState(
        total_duration_seconds=target_duration_seconds,
        start_time=time.time(),
    )
    capabilities = get_ffmpeg_capabilities(logger=logger)
    if settings.mode == "preview":
        encoder = DEFAULT_VIDEO_CODEC
        encoder_reason = "Preview mode uses CPU encoding for lower overhead."
    elif capabilities.preferred_h264_encoder == "h264_nvenc":
        encoder = "h264_nvenc"
        encoder_reason = "NVENC runtime probe succeeded."
    elif capabilities.nvenc_available and capabilities.nvenc_runtime_error:
        encoder = DEFAULT_VIDEO_CODEC
        encoder_reason = f"NVENC probe failed; falling back to CPU. Details: {capabilities.nvenc_runtime_error}"
    elif capabilities.nvenc_available and not capabilities.cuda_hwaccel_available:
        encoder = DEFAULT_VIDEO_CODEC
        encoder_reason = "FFmpeg reports h264_nvenc, but CUDA hwaccel is unavailable; falling back to CPU."
    elif capabilities.cuda_hwaccel_available and not capabilities.nvenc_available:
        encoder = DEFAULT_VIDEO_CODEC
        encoder_reason = "CUDA hwaccel detected, but h264_nvenc encoder is unavailable; falling back to CPU."
    else:
        encoder = DEFAULT_VIDEO_CODEC
        encoder_reason = "NVENC unavailable; falling back to CPU encoding."
    log_structured(
        logger,
        "video_encoder_selected",
        mode=settings.mode,
        selected_encoder=encoder,
        reason=encoder_reason,
        ffmpeg_path=capabilities.ffmpeg_path,
        ffprobe_path=capabilities.ffprobe_path,
        nvenc_runtime_available=capabilities.nvenc_runtime_available,
        nvenc_probe_command=shlex.join(capabilities.nvenc_probe_command)
        if capabilities.nvenc_probe_command is not None
        else None,
        nvenc_probe_result=capabilities.nvenc_probe_result,
        hwaccels=list(capabilities.hwaccels),
        encoders=list(capabilities.encoders),
    )

    # --- MEMORY SAFETY ---
    # Force a safe chunk size of 50. This keeps RAM usage incredibly low.
    safe_chunk_size = 50 
    chunked_scenes = _chunk_list(scenes, safe_chunk_size)
    
    chunk_paths: list[Path] = []
    stitched_video_path: Path | None = None
    remaining_duration_seconds = target_duration_seconds
    processed_duration_seconds = 0.0

    try:
        # --- PASS 1: RENDER SMALL CHUNKS ---
        for chunk_index, chunk_scenes in enumerate(chunked_scenes):
            if remaining_duration_seconds <= 0:
                break

            full_chunk_duration = _assembled_duration_seconds(chunk_scenes, settings.transition_overlap_seconds)
            chunk_target_duration = min(full_chunk_duration, remaining_duration_seconds)
            if chunk_target_duration <= 0:
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
                transition=settings.transition,
                on_progress=on_progress,
                logger=logger,
                final_filter_filename=f"graph_chunk_{chunk_index}.txt",
            )
            chunk_paths.append(chunk_path)
            
            # Account for the chunk overlap in the remaining duration
            contribution = chunk_target_duration
            if chunk_index > 0:
                contribution -= settings.transition_overlap_seconds
            
            remaining_duration_seconds -= contribution
            processed_duration_seconds += contribution

        if not chunk_paths:
            raise RuntimeError("No video chunks were rendered for final assembly.")

        # --- PASS 2: XFADE THE CHUNKS TOGETHER ---
        if len(chunk_paths) == 1:
            stitched_video_path = chunk_paths[0]
        else:
            # Treat the rendered chunks as new scenes so we can xfade them perfectly
            master_scenes = []
            for cp in chunk_paths:
                master_scenes.append(
                    SceneSegment(
                        file_path=cp,
                        duration_seconds=probe_duration_seconds(cp),
                        loop_count=1
                    )
                )
            
            # Render them together using the exact same smooth transition logic
            stitched_video_path = _render_scene_chunk(
                chunk_scenes=master_scenes,
                chunk_index=9999, # Arbitrary ID for the master pass
                temporary_dir=temporary_dir,
                encoder=encoder,
                settings=settings,
                chunk_target_duration=target_duration_seconds,
                progress_state=progress_state,
                progress_offset_seconds=processed_duration_seconds,
                transition=settings.transition,
                on_progress=None,
                logger=logger,
                final_filter_filename="graph_master.txt",
            )

        _mux_audio_once(
            stitched_video_path=stitched_video_path,
            audio_mix_path=audio_mix_path,
            output_path=output_path,
            logger=logger,
        )
        
        log_structured(logger, "video_render_complete", encoder=encoder, output_path=str(output_path.resolve()))
    finally:
        # Clean up intermediate files so they don't eat your hard drive
        if not keep_intermediate_files:
            for chunk_path in chunk_paths:
                if chunk_path.exists():
                    chunk_path.unlink(missing_ok=True)
            if stitched_video_path and stitched_video_path.exists() and stitched_video_path not in chunk_paths:
                stitched_video_path.unlink(missing_ok=True)

    return output_path, encoder
