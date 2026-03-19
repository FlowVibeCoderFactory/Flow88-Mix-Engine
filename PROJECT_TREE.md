# Project Tree

Meaningful tree only. This omits `.git`, `__pycache__`, and other noise.

```text
Flow88-Mix-Engine/
├── README.md
│   Human-facing onboarding and deployment guide.
├── PROJECT_CONTEXT.md
│   Compact handoff summary of the current system and deployment model.
├── PROJECT_TREE.md
│   Curated repo map for new maintainers.
├── Dockerfile
│   Python 3.11 + FFmpeg image that runs the FastAPI app with Uvicorn.
├── docker-compose.yml
│   DGX Spark service definition with GPU access, bind mounts, and healthcheck.
├── requirements.txt
│   Server dependency entrypoint; forwards to `requirements/server.txt`.
├── requirements/
│   Split dependency sets.
│   ├── base.txt
│   │   Core media-analysis packages.
│   ├── server.txt
│   │   FastAPI server dependencies.
│   └── desktop.txt
│       Optional desktop wrapper dependencies.
├── frontend/
│   Browser UI served by FastAPI.
│   ├── index.html
│   │   Main app shell with Audio Mix, Video Master, and file manager modal.
│   ├── app.js
│   │   Frontend state, API calls, queue logic, autosave, and job polling.
│   └── styles.css
│       Current dark UI styling.
├── server.py
│   FastAPI routes, DTO validation, diagnostics, file APIs, and render job lifecycle.
├── analyzer.py
│   Audio discovery, metadata extraction, BPM/key analysis, and rejection reporting.
├── video_processor.py
│   Video probing, transition graph generation, preflight, chunked rendering, muxing, and NVENC probing.
├── mixer.py
│   Audio timeline math and final mixed WAV render.
├── runtime_config.py
│   Environment-driven runtime paths, host/port, CORS, and upload-size settings.
├── project_persistence.py
│   `.flowmix` save/load/autosave helpers and project directory resolution.
├── render_logging.py
│   Per-render log-file creation and structured logging helpers.
├── file_manager.py
│   Safe list/upload/rename/delete/download helpers scoped to managed directories.
├── main.py
│   CLI entrypoint for the audio-only pipeline.
├── desktop_app.py
│   Optional local desktop wrapper using `pywebview`.
├── models.py
│   Shared dataclasses for analyzed tracks, videos, and timeline entries.
├── tracklist.py
│   Tracklist timestamp formatting and output writing.
├── docs/
│   Repo documentation assets.
│   └── screenshots/
│       Existing generic UI screenshots.
│       ├── app-desktop.png
│       │   Current desktop overview of the app.
│       └── app-mobile.png
│           Current narrow/mobile overview of the app.
├── docker-data/
│   Repo-local sample runtime folders from older/local workflows.
│   ├── input/
│   │   Example local input scaffold.
│   ├── output/
│   │   Example local output scaffold.
│   ├── projects/
│   │   Contains a sample autosave `.flowmix` file.
│   └── logs/
│       Example local log scaffold.
├── LLM_CONTEXT.md
│   Internal AI-oriented project note; useful, but not the primary human handoff doc.
├── desktop_app.spec
│   Packaging spec for the optional desktop wrapper.
├── runMixer.bat
│   Windows helper for local launching.
├── start.bat
│   Windows startup helper.
└── start.sh
    Shell startup helper for local/server launches.
```

Notes:

- The current DGX Compose file binds host folders under `/srv/flow88/*`, not `docker-data/`.
- The browser-first path is the current primary workflow. The desktop wrapper is still present, but secondary.
