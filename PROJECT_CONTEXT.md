# Project Context

## Overview

Flow88 Mix Engine is a browser-driven audio mix and video mastering tool.

- Audio side: scan source tracks, extract duration and tags, estimate BPM/key, order the queue, and render a single mixed WAV plus tracklist.
- Video side: scan source clips, let the user order clips and loop counts, run a preflight check, then render either a preview or a final mastered video against the mixed audio.

The practical problem it solves is moving the heavy media work off a user desktop and onto one machine that has FFmpeg, storage, and optionally NVIDIA video encoding. The current repo supports local use, but the active deployment shape is server-first and fits a DGX Spark style box well.

Compared with the original/local workflow, the DGX Spark edition is:

- headless by default
- Dockerized
- served through FastAPI on port `8000`
- designed around host-mounted runtime folders under `/srv/flow88/*`
- usable from another machine through a browser instead of a local desktop wrapper

## Current Deployment Model

The current primary entrypoint is [`server.py`](/home/xxfactionsxx/Flow88-Mix-Engine/server.py). It serves the frontend, exposes the API, manages files, and launches background video render jobs.

The DGX Spark deployment is defined by [`docker-compose.yml`](/home/xxfactionsxx/Flow88-Mix-Engine/docker-compose.yml):

- one service: `flow88`
- container port `8000`
- bind mounts:
  - `/srv/flow88/input`
  - `/srv/flow88/output`
  - `/srv/flow88/projects`
  - `/srv/flow88/logs`
- `gpus: all`
- `NVIDIA_DRIVER_CAPABILITIES=compute,video,utility`
- healthcheck against `GET /health`

The old local/desktop path still exists through [`desktop_app.py`](/home/xxfactionsxx/Flow88-Mix-Engine/desktop_app.py), but it is optional and not the main DGX workflow.

## Main Components

- [`server.py`](/home/xxfactionsxx/Flow88-Mix-Engine/server.py): FastAPI app, DTOs, routes, job polling, diagnostics, and static frontend serving.
- [`frontend/index.html`](/home/xxfactionsxx/Flow88-Mix-Engine/frontend/index.html), [`frontend/app.js`](/home/xxfactionsxx/Flow88-Mix-Engine/frontend/app.js), [`frontend/styles.css`](/home/xxfactionsxx/Flow88-Mix-Engine/frontend/styles.css): single-page browser UI.
- [`analyzer.py`](/home/xxfactionsxx/Flow88-Mix-Engine/analyzer.py): audio discovery, metadata, BPM/key analysis, supported/rejected file reporting.
- [`mixer.py`](/home/xxfactionsxx/Flow88-Mix-Engine/mixer.py): audio timeline math and final WAV render with FFmpeg.
- [`video_processor.py`](/home/xxfactionsxx/Flow88-Mix-Engine/video_processor.py): video probing, transition graph building, preflight, preview/final render, muxing, NVENC detection.
- [`project_persistence.py`](/home/xxfactionsxx/Flow88-Mix-Engine/project_persistence.py): `.flowmix` save/load/autosave.
- [`file_manager.py`](/home/xxfactionsxx/Flow88-Mix-Engine/file_manager.py): safe upload/list/rename/delete/download logic for managed folders.
- [`runtime_config.py`](/home/xxfactionsxx/Flow88-Mix-Engine/runtime_config.py): env-driven paths, host/port, CORS, upload size.
- [`render_logging.py`](/home/xxfactionsxx/Flow88-Mix-Engine/render_logging.py): per-render log files and structured events.

## Runtime Directories

Default local paths come from [`runtime_config.py`](/home/xxfactionsxx/Flow88-Mix-Engine/runtime_config.py), but the DGX compose file overrides them to `/srv/flow88/*`.

- `input/` or `/srv/flow88/input`: source audio
- `input/videos/` or `/srv/flow88/input/videos`: source video clips
- `output/` or `/srv/flow88/output`: rendered mix, preview, final video, and temporary render workspace
- projects dir or `/srv/flow88/projects`: saved `.flowmix` files and autosave
- `logs/` or `/srv/flow88/logs`: per-render log files

Important caveat: the repo still contains `docker-data/` from earlier/local workflows, but the current compose file binds absolute host paths under `/srv/flow88/*`.

## Data Flow

1. Audio files land in the managed input directory.
2. `GET /tracks` scans the directory, marks unsupported files, and analyzes supported files.
3. The browser UI lets the user reorder or sort the audio queue.
4. `POST /mix` renders `final_mix.wav` and writes `tracklist.txt`.
5. Video files land in `input/videos`.
6. `GET /videos` probes each clip and returns duration, resolution, and frame rate.
7. The UI builds a video queue with loop counts, transition settings, and a render tier.
8. Project state is saved as `.flowmix` JSON through the project endpoints.

## Render/Preview Flow

Audio render:

- input tracks are concatenated through FFmpeg
- trim bounds come from analysis
- transitions use `acrossfade`
- final normalization uses `loudnorm`
- output is `final_mix.wav`

Video render:

- preflight checks that the mixed audio exists and the clip set is renderable
- final renders build seamless loop clips, expand the queue into scenes, and chunk the scene list for memory safety
- each chunk is rendered, then the chunks are crossfaded together, then audio is muxed once
- preview renders use the `preview` profile, cap the timeline to 60 seconds, and use CPU encoding

## Networking / Access

The browser UI is served by the same FastAPI app at `GET /`, so the normal DGX shape is:

- user browser -> `http://DGX_HOST:8000`
- same server serves HTML/CSS/JS
- same server handles API calls and background render jobs
- same server reads and writes the mounted runtime folders

File sharing options fit around that core:

- Browser UI: built-in upload, rename, delete, download, and project management
- Docker bind mounts: connect the container to host storage
- Samba/SMB: not implemented in this repo, but if the host exports `/srv/flow88/input` or `/srv/flow88/output`, it becomes the bulk file transfer path
- LAN/Tailscale: also external to the app; if port `8000` and any shared folders are reachable, the UI works the same way

The old `open-*` routes remain for local desktop-style runs, but on Linux/DGX they intentionally fail and tell the user to use mounted folders instead.

## Current Known Issues

- There is no automated test suite in the repo today.
- Preview mode is intentionally limited and CPU-only.
- The `performance` render profile hard-requires working NVENC; the API rejects it if the runtime probe fails.
- Non-default transition names are syntax-checked, but success still depends on the FFmpeg build supporting that transition.
- The repo mixes current DGX/server files with older local/desktop artifacts, which can confuse first-time operators.
- Final video renders clean chunk outputs, but the temporary render workspace under `output/video_work/` can still accumulate intermediate cache files.

## Next Best Improvements

- Add a small end-to-end test pass for `GET /tracks`, `POST /mix`, preflight, and one preview render.
- Remove or clearly separate older local-only scaffolding from the DGX path.
- Add explicit cleanup for `video_work/` cache artifacts.
- Add a real deployment note for Samba/Tailscale host setup instead of leaving it implicit.
- Capture task-focused UI screenshots and replace the generic overview shots.

## Verification Status

Verified in this handoff pass:

- Repo structure and runtime files were inspected directly.
- `python3 -m compileall` completed successfully for the repo.
- `docker compose config` resolves the DGX Spark compose file and confirms bind mounts to `/srv/flow88/*` plus `gpus: all`.
- Existing screenshot files are present in `docs/screenshots/`.

Not yet verified in this handoff pass:

- live container build and startup
- end-to-end render with real media
- actual FFmpeg availability inside a running container
- actual NVENC runtime success on the target DGX box
- Samba or Tailscale host configuration
