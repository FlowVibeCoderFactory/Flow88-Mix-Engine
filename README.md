# Flow88 Mix Engine

Flow88 Mix Engine is a local-first Python app for audio mixing and video mastering.

- Audio: analyze BPM/key, reorder tracks, and render a continuous mixed WAV.
- Video: queue clips, set loop counts, configure transitions, preflight, and render final/preview video.

## Screenshots

### Desktop Queue

![Flow88 desktop queue](docs/screenshots/app-desktop.png)

### Mobile/Narrow Layout

![Flow88 mobile queue](docs/screenshots/app-mobile.png)

## Features

- Analyze local audio in `input/` (`.mp3`, `.wav`, `.flac`, `.m4a`, `.aac`, `.ogg`)
- Detect BPM, musical key, and Camelot harmonic key
- Drag/drop sorting for audio and video queues
- Audio mix rendering with FFmpeg `acrossfade` + `loudnorm`
- Video rendering from `input/videos/` with configurable transitions
- Transition controls:
- Type dropdown
- Duration slider (`0.2s` to `3.0s`)
- Curve selector (`linear`, `easein`, `easeout`)
- Preview render mode with shorter timeline and 50% transition duration
- Render preflight checks before full video job submission
- Tracklist export to `output/tracklist.txt`

## Requirements

- Python 3.11+
- FFmpeg and FFprobe available in `PATH`
- OS support:
- Web mode: Windows/macOS/Linux
- Desktop wrapper (`pywebview`): best supported on Windows in this repo setup

## Quick Start

1. Create and activate a virtual environment.

```bash
python -m venv .venv
```

Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
```

macOS/Linux:

```bash
source .venv/bin/activate
```

2. Install dependencies.

```bash
pip install -r requirements.txt
```

3. Put source files in:

- Audio: `input/`
- Video: `input/videos/`

4. Start the app.

Web server mode:

```bash
python -m uvicorn server:app --host 127.0.0.1 --port 8000
```

Then open `http://127.0.0.1:8000`.

Desktop window mode:

```bash
python desktop_app.py
```

Windows helper script:

```bat
runMixer.bat
```

## How Audio Rendering Works

1. `analyzer.py` discovers files and extracts tags.
2. `librosa` estimates BPM and key.
3. `mixer.py` builds the audio filter graph.
4. Tracks are chained with `acrossfade`.
5. `loudnorm` is applied.
6. Final WAV and tracklist are written to `output/`.

## How Video Rendering Works

1. Video items are normalized from queue order and loop counts.
2. Scene sequence is expanded to match audio duration.
3. A concat timeline file (`timeline.txt`) is generated.
4. A dynamic transition graph is generated:
- `split -> trim -> setpts -> xfade chain`
5. Xfade offsets are cumulative with overlap subtraction:
- `offset_i = sum(previous clip durations) - (transition_duration * i)`
6. Render runs in chunks to limit memory growth.
7. Chunks are stitched, then audio is muxed once.

Command pattern used by chunk renders:

```text
ffmpeg -f concat -safe 0 -i timeline.txt -filter_complex_script transition_graph.txt -map [vout]
```

Default transition config:

- `enabled: true`
- `type: fade`
- `duration: 1.0`
- `curve: linear`

Preview mode transition duration is automatically reduced by 50%.

## API Endpoints

- `GET /` serves `frontend/index.html`
- `GET /tracks` returns analyzed tracks
- `POST /mix` renders the audio mix
- `GET /videos` returns analyzed videos
- `POST /render-preflight` validates video render inputs and transition graph
- `POST /generate-video` queues final video render
- `POST /generate-preview` queues preview render
- `GET /video-jobs/{job_id}` polls video render progress
- `GET /video-render-profiles` lists render profiles
- `GET /open-output` opens output folder (Windows only)

## Project Layout

```text
frontend/
  index.html
  app.js
  styles.css
analyzer.py
mixer.py
video_processor.py
models.py
tracklist.py
server.py
desktop_app.py
main.py
runMixer.bat
LLM_CONTEXT.md
```

## LLM Context File

Detailed implementation context for AI agents is available in [LLM_CONTEXT.md](LLM_CONTEXT.md).
