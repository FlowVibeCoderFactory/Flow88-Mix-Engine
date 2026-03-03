# LLM Context: Flow88 Mix Engine

## Project Goal

Flow88 Mix Engine is a local-first audio and video mastering tool.

- Analyze local audio from `input/` and produce a DJ-style mixed WAV.
- Build a video timeline from clips in `input/videos/`.
- Render final video with configurable clip transitions.
- Write outputs to `output/`.

## Main Execution Paths

### 1) API + Frontend (primary UX)

- Entrypoint: `server.py`
- UI: `frontend/index.html`, `frontend/app.js`, `frontend/styles.css`
- Audio flow:
- `GET /tracks` -> queue/sort -> `POST /mix`
- Video flow:
- `GET /videos` -> queue/reorder/loop -> `POST /render-preflight` -> `POST /generate-video` or `POST /generate-preview`
- Video job polling:
- `GET /video-jobs/{job_id}`

### 2) Desktop Wrapper

- Entrypoint: `desktop_app.py`
- Starts uvicorn in a background thread
- Opens embedded webview at `http://127.0.0.1:8000`

### 3) CLI Audio Pipeline

- Entrypoint: `main.py`
- Runs analyze -> timeline -> render -> tracklist without frontend

## Code Map

- `analyzer.py`
- Audio file discovery and metadata extraction
- BPM/key analysis and harmonic key mapping
- `mixer.py`
- Audio timeline and FFmpeg `acrossfade` + `loudnorm` graph
- `video_processor.py`
- Scene expansion, transition graph generation, preflight checks, chunk render, final mux
- `server.py`
- FastAPI routes, DTO validation, video job lifecycle
- `frontend/app.js`
- Queue state, drag/drop, transition controls, API calls
- `tracklist.py`
- Tracklist timestamp formatting and write
- `models.py`
- `TrackAnalysis`, `TimelineEntry`, `VideoAnalysis`

## Data Contracts

### Audio DTOs

- `TrackDTO`
- `TrackListResponse`
- `MixRequest`
- `MixResponse`

### Video DTOs (`server.py`)

- `VideoItemDTO`
- `TransitionConfigDTO`
- `GenerateVideoRequest`
- `RenderPreflightResponse`
- `GenerateVideoJobResponse`
- `JobProgressResponse`

`GenerateVideoRequest.transition` fields:

- `enabled: bool`
- `type: str`
- `duration: float` (`0.2` to `3.0`)
- `curve: "linear" | "easein" | "easeout"`

## Rendering Behavior

### Audio

- Default audio crossfade: `15.0s` (`DEFAULT_CROSSFADE_SECONDS`)
- Render chain: trimmed track streams -> `acrossfade` chain -> `loudnorm`
- Output codec: `pcm_s24le`

### Video

- Default transition:
- `enabled=true`
- `type=fade`
- `duration=1.0`
- `curve=linear`
- Preview mode applies a 50% duration reduction to transition duration.
- Filter graph generation is dynamic:
- `split -> trim -> setpts -> (fps/scale/pad/format) -> xfade chain`
- Xfade offsets use:
- `offset_i = sum(previous clip durations) - (transition_duration * i)`
- If transitions are disabled, filter graph uses `concat=n=...:v=1:a=0`.
- Render command pattern:
- `ffmpeg -f concat -safe 0 -i timeline.txt -filter_complex_script transition_graph.txt -map [vout] ...`
- CPU and NVENC paths render in chunks (`CHUNK_SIZE=6`) to bound memory use.

## Preflight Behavior

Video preflight validates:

- Stream presence, duration sanity, codec/resolution consistency
- Transition compatibility with scene durations
- Assembled timeline length against target duration
- Concat input dry-run
- Transition graph dry-run (separate FFmpeg validation)

## Frontend Notes

- Video Master controls include:
- Transition type dropdown
- Transition duration slider (`0.2s` to `3.0s`)
- Transition curve selector (`linear`, `easein`, `easeout`)
- Transition payload is sent for preflight, master render, and preview render.

## External Dependencies and Runtime Assumptions

- `ffmpeg` and `ffprobe` in `PATH`
- Python packages from `requirements.txt`
- Input paths: `input/`, `input/videos/`
- Output path: `output/`
- `open-*` folder routes are Windows-specific (`os.startfile`)

## Known Gaps / Risks

- No automated tests currently
- FFmpeg transition names are syntax-validated in API but can still fail if unsupported by local FFmpeg build
- Large media sets can still create long render times

## Recommended Change Workflow for LLMs

1. Keep frontend payload shape and backend DTOs synchronized.
2. When changing transitions, update both graph generation and preflight validation.
3. Preserve concat timeline + single input stream assumptions for memory safety.
4. Avoid committing generated media/caches.

## Useful Commands

Install:

```bash
pip install -r requirements.txt
```

Run API:

```bash
python -m uvicorn server:app --host 127.0.0.1 --port 8000
```

Run desktop app:

```bash
python desktop_app.py
```

Run CLI audio pipeline:

```bash
python main.py
```
