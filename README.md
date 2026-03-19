# Flow88 Mix Engine

Flow88 Mix Engine is a browser-based audio mix and video mastering tool. The current repo supports local use, but the active deployment model is a headless FastAPI server running on a DGX Spark style machine and serving the UI over the network.

For a quick handoff, start with [PROJECT_CONTEXT.md](PROJECT_CONTEXT.md) and [PROJECT_TREE.md](PROJECT_TREE.md).

## What This Project Is

This project lets a user:

- analyze source audio files for BPM, key, duration, and trim bounds
- order a mix queue and render a single mastered WAV plus tracklist
- build a video queue from source clips, set loop counts and transitions, run preflight, and render a preview or final video
- manage input, output, and project files from the browser

## Key Features

- FastAPI backend with the frontend served from the same app
- Audio analysis with `ffprobe`, `mutagen`, and `librosa`
- Audio mix render with FFmpeg `acrossfade` plus `loudnorm`
- Video queue with drag/drop ordering, loop counts, and transition controls
- Preview render, final render, and background job polling
- `.flowmix` save/load/autosave support
- Managed browser file operations for input, video input, output, and projects
- Health and diagnostics endpoints for deployment checks
- NVENC probing with CPU fallback

## DGX Spark / Headless Deployment Model

The DGX Spark version is server-first:

- the DGX box runs the container
- FastAPI serves both the API and the browser UI on port `8000`
- runtime data lives on the host under `/srv/flow88/*`
- users connect from another machine through a browser
- Samba, Tailscale, and any other file-sharing or remote-access layer live outside the app

This is different from the older local mode, which still exists through `desktop_app.py` and local default folders.

## Architecture Summary

- `server.py` is the main entrypoint and API layer.
- `frontend/` contains the single-page UI.
- `analyzer.py` scans and analyzes audio.
- `mixer.py` builds the audio timeline and renders `final_mix.wav`.
- `video_processor.py` handles video probing, transition graphs, preflight, chunked rendering, muxing, and NVENC checks.
- `project_persistence.py` stores `.flowmix` project files and autosave.
- `file_manager.py` keeps browser file operations inside managed directories.
- `runtime_config.py` reads environment variables for paths, host/port, CORS, and upload limits.

## Folder Layout Summary

- `frontend/`: browser UI
- `requirements/`: split dependency sets for base, server, and desktop
- `docs/screenshots/`: current generic UI screenshots
- `docker-data/`: repo-local sample runtime folders from older/local workflows
- `/srv/flow88/input`: DGX audio input
- `/srv/flow88/input/videos`: DGX video input
- `/srv/flow88/output`: renders and temporary video work files
- `/srv/flow88/projects`: saved `.flowmix` files
- `/srv/flow88/logs`: render logs

Important: the current `docker-compose.yml` binds absolute host paths under `/srv/flow88/*`. The repo-local `docker-data/` folders are not the active compose mount points.

## Setup

Local Python setup:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python3 server.py
```

Then open `http://127.0.0.1:8000`.

Optional local desktop wrapper:

```bash
pip install -r requirements/desktop.txt
python3 desktop_app.py
```

Useful environment variables:

- `FLOW88_INPUT_DIR`
- `FLOW88_OUTPUT_DIR`
- `FLOW88_PROJECTS_DIR`
- `FLOW88_LOGS_DIR`
- `FLOW88_CORS_ORIGINS`
- `FLOW88_HOST`
- `FLOW88_PORT`
- `FLOW88_MAX_UPLOAD_SIZE_BYTES`

Defaults:

- input: `./input`
- video input: `./input/videos`
- output: `./output`
- logs: `./logs`
- projects: user app-data path unless overridden
- max upload size: `8 GiB`

## Docker Run Instructions

Create the host folders the compose file expects:

```bash
sudo mkdir -p /srv/flow88/input/videos /srv/flow88/output /srv/flow88/projects /srv/flow88/logs
```

Build and start:

```bash
docker compose build
FLOW88_CORS_ORIGINS=http://YOUR-PC-IP:8000 docker compose up -d
```

Open:

```text
http://DGX_SPARK_IP:8000
```

Useful checks:

```bash
docker compose logs --tail 100 flow88
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/diagnostics
```

## LAN / Tailscale / Samba Notes

- LAN access is the simplest path: browse to `http://DGX_HOST:8000`.
- Tailscale works the same way if the node and port are reachable.
- Samba is not configured by this repo, but it fits naturally if the host exports `/srv/flow88/input` and `/srv/flow88/output`.
- Browser upload is fine for small tests. For large media, copying files into the host-mounted folders is the better workflow.

## Input / Output / Projects Workflow

1. Put audio files in the input folder or upload them from `Manage Input`.
2. Put video clips in `input/videos` or upload them from the Video tab.
3. Refresh the Audio Mix tab to analyze tracks.
4. Render the audio mix. This writes `final_mix.wav` and `tracklist.txt`.
5. Refresh the Video Master tab, order clips, set loop counts, pick a render tier, and set transitions.
6. Run a preview or final render.
7. Download final outputs from `Manage Output`.
8. Save queue state as a `.flowmix` project file when needed.

## UI Walkthrough

Audio Mix tab:

- `Refresh` rescans and reanalyzes the audio input directory.
- `Sort BPM`, `Sort Key`, and `Sort A-Z` reorder the track queue.
- Drag rows to set final mix order.
- `Render Mix` produces `final_mix.wav`.
- `Manage Input`, `Manage Output`, and `Manage Projects` open the browser file manager.

Video Master tab:

- `Refresh` rescans the video input directory.
- Drag clips to order the sequence.
- Set loop counts per clip.
- `Render Tier` selects `performance`, `balanced`, or `quality`.
- Transition controls set type, duration, and curve.
- `Generate Preview` runs a short preview job.
- `Master Video` runs preflight and queues the final render.

Project handling:

- `File -> Open Project`
- `File -> Save Project`
- `File -> Save Project As`
- autosave runs in the browser every 10 seconds

## UI Screenshots

Current repo screenshots:

![Desktop overview](docs/screenshots/app-desktop.png)
Current desktop view of the main console.

![Mobile overview](docs/screenshots/app-mobile.png)
Narrow/mobile view of the same UI.

Recommended handoff capture set:

Add screenshots to `/screenshots` using the filenames below.

![Main Mixer](screenshots/main-mixer.png)
Audio Mix tab with analyzed tracks, ordering, and total mix length.

![Manage Input](screenshots/manage-input.png)
File manager opened on the audio input directory.

![Manage Output](screenshots/manage-output.png)
File manager opened on rendered outputs and downloadable files.

![Render Preview](screenshots/render-preview.png)
Video Master tab during or after preview/final render with progress and transition settings visible.

## Troubleshooting

- `GET /health` confirms the service is up.
- `GET /diagnostics` shows directories, FFmpeg paths, encoders, hwaccels, and preferred H.264 encoder.
- If `Manage Input` shows files but the Audio Mix table stays empty, refresh and check whether files are marked unsupported or rejected.
- If `performance` render profile is rejected, the server did not pass the NVENC runtime probe.
- If renders fall back to CPU, inspect `/diagnostics` and the render log path returned by the job status.
- If `open-*` folder routes fail on Linux/DGX, that is expected; use the browser file manager or the mounted host folders instead.
- If browser uploads are slow or large, move media through the host-mounted folders or Samba instead.

## Known Limitations

- No automated tests are included yet.
- Preview renders are CPU-only and capped to a short timeline.
- Transition names are validated lightly; unsupported FFmpeg transitions still fail at render time.
- The repo still contains older local/desktop artifacts alongside the current DGX path.
- Temporary render/cache files can accumulate under `output/video_work/`.

## Next Improvements

- Add a small end-to-end test pass for audio render, preflight, and preview render.
- Clean up or clearly separate older local-only scaffolding from the DGX deployment path.
- Add explicit cleanup for `video_work/`.
- Document Samba/Tailscale host setup outside the app.
- Replace the generic screenshots with task-focused captures.
