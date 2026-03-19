FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    FLOW88_INPUT_DIR=/srv/flow88/input \
    FLOW88_OUTPUT_DIR=/srv/flow88/output \
    FLOW88_PROJECTS_DIR=/srv/flow88/projects \
    FLOW88_LOGS_DIR=/srv/flow88/logs \
    FLOW88_CORS_ORIGINS=*

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements ./requirements
COPY requirements.txt ./

RUN python -m pip install --upgrade pip setuptools wheel \
    && python -m pip install -r requirements/server.txt

COPY . .

RUN mkdir -p /srv/flow88/input/videos /srv/flow88/output /srv/flow88/projects /srv/flow88/logs

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=5 \
    CMD python -c "import json, sys, urllib.request; data = json.load(urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)); sys.exit(0 if data.get('ok') else 1)"

CMD ["python", "-m", "uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000"]
