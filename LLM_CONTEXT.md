# LLM Context: Flow88 Mix Engine

## Project Goal

Flow88 Mix Engine creates a single continuous DJ-style mix from local tracks. It prioritizes fast local workflow:

- Analyze files from `input/`
- Let user reorder/sort in UI
- Render final WAV + tracklist into `output/`

## Main Execution Paths

### 1) API + Frontend (primary UX)

- Entrypoint: `server.py`
- UI served from `frontend/`
- User actions:
- Load tracks (`GET /tracks`)
- Reorder/sort in browser
- Render (`POST /mix`)

### 2) Desktop Wrapper

- Entrypoint: `desktop_app.py`
- Runs uvicorn in a background thread
- Opens embedded webview at `http://127.0.0.1:8000`

### 3) CLI Pipeline

- Entrypoint: `main.py`
- Runs analyze -> timeline -> render -> tracklist directly without frontend

## Code Map

- `analyzer.py`
- File discovery and metadata extraction via `mutagen`
- BPM/key analysis via `librosa`
- Camelot key mapping
- `mixer.py`
- Transition duration calculation
- Timeline calculation (`build_timeline`)
- FFmpeg filter graph generation (`_build_filtergraph`)
- Mix render (`render_mix`) with final loudness normalization
- `tracklist.py`
- Timestamp formatting
- Tracklist file generation
- `models.py`
- `TrackAnalysis` and `TimelineEntry` dataclasses
- `server.py`
- FastAPI routes and request/response DTOs
- Ordered track validation before rendering
- `frontend/app.js`
- Client state and drag/drop ordering
- Sorting by BPM/key/title
- API calls and status messaging

## Data Contracts

### `TrackAnalysis`

Core analysis payload used across backend.

Fields:

- `file_path: Path`
- `title: str`
- `artist: str`
- `bpm: float | None`
- `duration_seconds: float`
- `trim_start_seconds: float`
- `trim_end_seconds: float`
- `musical_key: str | None`
- `harmonic_key: str | None`

Derived property:

- `trimmed_duration_seconds`

### API DTOs (in `server.py`)

- `TrackDTO`
- `TrackListResponse`
- `MixRequest` (`tracks: list[str]` of file names)
- `MixResponse`

## Rendering Behavior

- Crossfade default: `15.0` seconds (`DEFAULT_CROSSFADE_SECONDS`)
- Transition duration is capped by both adjacent trimmed durations
- FFmpeg graph chain: trimmed track streams -> `acrossfade` chain -> `loudnorm`
- Output format: WAV (`pcm_s24le`)

## External Dependencies and Runtime Assumptions

- `ffmpeg` must be present in system `PATH`
- Python packages from `requirements.txt`
- Input tracks are loaded from repo-local `input/`
- Output files written to repo-local `output/`
- `/open-output` route is Windows-specific (`os.startfile`)

## Frontend Notes

- `state.tracks` stores current order; drag/drop mutates this array
- Sort buttons toggle ascending/descending by flipping `sortDirection` sign
- Harmonic key sort parses keys with `/^(\d+)([AB])$/i`
- Rendering request sends only ordered file names; backend re-resolves full track data

## Known Gaps / Risks

- No automated tests currently
- Heavy audio analysis/rendering runs synchronously in API request lifecycle
- Large input libraries can increase render latency
- Minimal error classification from FFmpeg subprocess failures

## Recommended Change Workflow for LLMs

1. Keep `TrackAnalysis` contract stable unless updating all dependent modules.
2. If adjusting mix logic, update both timeline and FFmpeg graph behavior coherently.
3. Preserve API response shapes expected by `frontend/app.js`.
4. Avoid committing generated media, caches, or local artifacts.

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

Run CLI pipeline:

```bash
python main.py
```
