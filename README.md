# Flow88 Mix Engine

Flow88 Mix Engine is a local-first Python app that analyzes song metadata (BPM + key), lets you reorder tracks in a drag-and-drop UI, and renders one continuous mixed WAV with FFmpeg crossfades.

## Screenshots

### Desktop Queue

![Flow88 desktop queue](docs/screenshots/app-desktop.png)

### Mobile/Narrow Layout

![Flow88 mobile queue](docs/screenshots/app-mobile.png)

## Features

- Analyze local audio files from `input/` (`.mp3`, `.wav`, `.flac`, `.m4a`, `.aac`, `.ogg`)
- Detect BPM, musical key, and Camelot harmonic key
- Drag/drop track ordering in the UI
- Sort by BPM, harmonic key, or title
- Render one continuous master mix with configurable crossfades
- Export tracklist timestamps to `output/tracklist.txt`
- Open output folder from the UI (Windows)

## Requirements

- Python 3.11+
- FFmpeg available in `PATH`
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

3. Put your tracks in `input/`.

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

## How Rendering Works

1. `analyzer.py` discovers audio files and extracts title/artist tags.
2. `librosa` estimates BPM and detects key from chroma profiles.
3. Silence is trimmed logically for transitions.
4. `mixer.py` builds an FFmpeg `filter_complex` graph.
5. Tracks are chained with `acrossfade` and final `loudnorm` is applied.
6. `tracklist.py` writes timeline offsets to `output/tracklist.txt`.

## API Endpoints

- `GET /` serves `frontend/index.html`
- `GET /tracks` returns analyzed tracks
- `POST /mix` accepts ordered file names and renders mix
- `GET /open-output` opens the output folder (Windows only)

## Project Layout

```text
frontend/
  index.html
  app.js
  styles.css
analyzer.py
mixer.py
models.py
tracklist.py
server.py
desktop_app.py
main.py
runMixer.bat
```

## LLM Context File

Detailed implementation context for AI agents is available in [LLM_CONTEXT.md](LLM_CONTEXT.md).
