const apiBase = window.location.origin.startsWith("http")
    ? window.location.origin
    : "http://localhost:8000";

const audioTabButton = document.getElementById("audio-tab-button");
const videoTabButton = document.getElementById("video-tab-button");
const audioPanel = document.getElementById("audio-panel");
const videoPanel = document.getElementById("video-panel");

const trackListBody = document.getElementById("track-list-body");
const videoListBody = document.getElementById("video-list-body");

const refreshAudioButton = document.getElementById("refresh-audio-button");
const refreshVideoButton = document.getElementById("refresh-video-button");
const renderButton = document.getElementById("render-button");
const masterVideoButton = document.getElementById("master-video-button");
const openOutputAudioButton = document.getElementById("open-output-audio-button");
const openOutputVideoButton = document.getElementById("open-output-video-button");

const sortBpmButton = document.getElementById("sort-bpm-button");
const sortKeyButton = document.getElementById("sort-key-button");
const sortAzButton = document.getElementById("sort-az-button");
const sortVideoAzButton = document.getElementById("sort-video-az-button");

const audioStatusElement = document.getElementById("audio-status");
const videoStatusElement = document.getElementById("video-status");
const audioProgressElement = document.getElementById("audio-progress");
const videoProgressElement = document.getElementById("video-progress");

const state = {
    activeTab: "audio",
    tracks: [],
    videos: [],
    sortDirection: {
        bpm: 1,
        key: 1,
        title: 1,
        videoTitle: 1
    }
};

function escapeHtml(value) {
    return String(value)
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#39;");
}

function setStatus(tab, message, type = "") {
    const statusElement = tab === "video" ? videoStatusElement : audioStatusElement;
    statusElement.textContent = message;
    statusElement.classList.remove("success", "error");
    if (type) {
        statusElement.classList.add(type);
    }
}

function setRenderProgress(tab, isVisible) {
    const progressElement = tab === "video" ? videoProgressElement : audioProgressElement;
    progressElement.classList.toggle("hidden", !isVisible);
}

function formatBpm(bpm) {
    if (bpm === null || bpm === undefined || Number.isNaN(Number(bpm))) {
        return "--";
    }
    return Math.round(Number(bpm));
}

function formatDuration(seconds) {
    const numeric = Number(seconds);
    if (!Number.isFinite(numeric) || numeric <= 0) {
        return "--:--";
    }

    const totalSeconds = Math.floor(numeric);
    const minutes = Math.floor(totalSeconds / 60);
    const remainder = String(totalSeconds % 60).padStart(2, "0");
    return `${minutes}:${remainder}`;
}

function formatFrameRate(value) {
    const numeric = Number(value);
    if (!Number.isFinite(numeric) || numeric <= 0) {
        return "-- fps";
    }
    return `${Math.round(numeric * 10) / 10} fps`;
}

function compareText(valueA, valueB) {
    const left = String(valueA ?? "");
    const right = String(valueB ?? "");
    return left.localeCompare(right, undefined, { sensitivity: "base" });
}

function buildVideoPreviewUrl(fileName) {
    return `${apiBase}/input/videos/${encodeURIComponent(fileName)}`;
}

function attachDragHandlers(row, onDropMove) {
    row.addEventListener("dragstart", (event) => {
        event.dataTransfer.effectAllowed = "move";
        event.dataTransfer.setData("text/plain", row.dataset.index);
        row.classList.add("dragging");
    });

    row.addEventListener("dragend", () => {
        row.classList.remove("dragging");
    });

    row.addEventListener("dragover", (event) => {
        event.preventDefault();
        row.classList.add("drag-target");
    });

    row.addEventListener("dragleave", () => {
        row.classList.remove("drag-target");
    });

    row.addEventListener("drop", (event) => {
        event.preventDefault();
        row.classList.remove("drag-target");

        const fromIndex = Number(event.dataTransfer.getData("text/plain"));
        const toIndex = Number(row.dataset.index);
        if (Number.isNaN(fromIndex) || Number.isNaN(toIndex) || fromIndex === toIndex) {
            return;
        }

        onDropMove(fromIndex, toIndex);
    });
}

function renderTracks() {
    trackListBody.innerHTML = "";

    if (state.tracks.length === 0) {
        setStatus("audio", "No tracks found in /input.", "error");
        return;
    }

    state.tracks.forEach((track, index) => {
        const row = document.createElement("tr");
        row.className = "track-row";
        row.draggable = true;
        row.dataset.index = String(index);
        row.innerHTML = `
            <td class="col-grip" aria-hidden="true">&#8942;&#8942;</td>
            <td class="col-title">${escapeHtml(track.title)}</td>
            <td class="col-artist">${escapeHtml(track.artist)}</td>
            <td class="col-key"><span class="badge key">${escapeHtml(track.harmonic_key ?? "--")}</span></td>
            <td class="col-bpm"><span class="badge">${formatBpm(track.bpm)} BPM</span></td>
        `;

        attachDragHandlers(row, (fromIndex, toIndex) => {
            const [movedTrack] = state.tracks.splice(fromIndex, 1);
            state.tracks.splice(toIndex, 0, movedTrack);
            renderTracks();
        });

        trackListBody.appendChild(row);
    });

    setStatus("audio", `Loaded ${state.tracks.length} tracks. Drag, sort, then render.`);
}

function renderVideos() {
    videoListBody.innerHTML = "";

    if (state.videos.length === 0) {
        setStatus("video", "No videos found in /input/videos.", "error");
        return;
    }

    state.videos.forEach((video, index) => {
        const row = document.createElement("tr");
        row.className = "track-row";
        row.draggable = true;
        row.dataset.index = String(index);

        const previewUrl = buildVideoPreviewUrl(video.file_name);
        const resolution = video.width && video.height ? `${video.width}x${video.height}` : "Unknown size";
        row.innerHTML = `
            <td class="col-grip" aria-hidden="true">&#8942;&#8942;</td>
            <td class="col-video-preview">
                <video class="video-thumb" src="${escapeHtml(previewUrl)}" muted playsinline preload="metadata"></video>
            </td>
            <td class="col-video-title">${escapeHtml(video.file_name)}</td>
            <td class="col-video-details">
                <span class="video-meta-stack">
                    <span class="badge">${formatDuration(video.duration_seconds)}</span>
                    <span class="badge">${escapeHtml(resolution)}</span>
                    <span class="badge">${escapeHtml(formatFrameRate(video.frame_rate))}</span>
                </span>
            </td>
        `;

        attachDragHandlers(row, (fromIndex, toIndex) => {
            const [movedVideo] = state.videos.splice(fromIndex, 1);
            state.videos.splice(toIndex, 0, movedVideo);
            renderVideos();
        });

        videoListBody.appendChild(row);
    });

    setStatus("video", `Loaded ${state.videos.length} clips. Drag to set scene order, then master video.`);
}

async function fetchTracks() {
    setStatus("audio", "Loading track metadata...");

    try {
        const response = await fetch(`${apiBase}/tracks`);
        const payload = await response.json();
        if (!response.ok) {
            throw new Error(payload.detail || "Failed to load tracks.");
        }

        state.tracks = payload.tracks ?? [];
        renderTracks();
    } catch (error) {
        setStatus("audio", error.message, "error");
    }
}

async function fetchVideos() {
    setStatus("video", "Loading video metadata...");

    try {
        const response = await fetch(`${apiBase}/videos`);
        const payload = await response.json();
        if (!response.ok) {
            throw new Error(payload.detail || "Failed to load videos.");
        }

        state.videos = payload.videos ?? [];
        renderVideos();
    } catch (error) {
        setStatus("video", error.message, "error");
    }
}

async function renderMix() {
    if (state.tracks.length === 0) {
        setStatus("audio", "Track queue is empty.", "error");
        return;
    }

    renderButton.disabled = true;
    setRenderProgress("audio", true);
    setStatus("audio", "Rendering mix...");

    try {
        const response = await fetch(`${apiBase}/mix`, {
            method: "POST",
            headers: {
                "Content-Type": "application/json"
            },
            body: JSON.stringify({
                tracks: state.tracks.map((track) => track.file_name)
            })
        });

        const payload = await response.json();
        if (!response.ok) {
            throw new Error(payload.detail || "Mix render failed.");
        }

        setStatus("audio", `Render complete: ${payload.mix_output_path}`, "success");
    } catch (error) {
        setStatus("audio", error.message, "error");
    } finally {
        renderButton.disabled = false;
        setRenderProgress("audio", false);
    }
}

async function generateVideoMaster() {
    if (state.videos.length === 0) {
        setStatus("video", "Video queue is empty.", "error");
        return;
    }

    masterVideoButton.disabled = true;
    setRenderProgress("video", true);
    setStatus("video", "Mastering video...");

    try {
        const response = await fetch(`${apiBase}/generate-video`, {
            method: "POST",
            headers: {
                "Content-Type": "application/json"
            },
            body: JSON.stringify({
                videos: state.videos.map((video) => video.file_name)
            })
        });

        const payload = await response.json();
        if (!response.ok) {
            throw new Error(payload.detail || "Video mastering failed.");
        }

        setStatus("video", `Master video complete: ${payload.video_output_path}`, "success");
    } catch (error) {
        setStatus("video", error.message, "error");
    } finally {
        masterVideoButton.disabled = false;
        setRenderProgress("video", false);
    }
}

async function openOutputFolder(tab) {
    setStatus(tab, "Opening output folder...");

    try {
        const response = await fetch(`${apiBase}/open-output`);
        const payload = await response.json();
        if (!response.ok) {
            throw new Error(payload.detail || "Failed to open output folder.");
        }

        setStatus(tab, `Opened output folder: ${payload.output_dir}`, "success");
    } catch (error) {
        setStatus(tab, error.message, "error");
    }
}

function sortByBpm() {
    state.sortDirection.bpm *= -1;

    state.tracks.sort((a, b) => {
        const bpmA = Number(a.bpm);
        const bpmB = Number(b.bpm);

        const normalizedA = Number.isFinite(bpmA) ? bpmA : Number.POSITIVE_INFINITY;
        const normalizedB = Number.isFinite(bpmB) ? bpmB : Number.POSITIVE_INFINITY;

        if (normalizedA !== normalizedB) {
            return (normalizedA - normalizedB) * state.sortDirection.bpm;
        }

        return compareText(a.title, b.title);
    });

    updateSortButtonLabel(sortBpmButton, "Sort BPM", state.sortDirection.bpm);
    renderTracks();
}

function sortByKey() {
    state.sortDirection.key *= -1;

    state.tracks.sort((a, b) => {
        const keyA = camelotToSortable(a.harmonic_key);
        const keyB = camelotToSortable(b.harmonic_key);

        if (keyA.num !== keyB.num) {
            return (keyA.num - keyB.num) * state.sortDirection.key;
        }

        if (keyA.type !== keyB.type) {
            return keyA.type.localeCompare(keyB.type) * state.sortDirection.key;
        }

        return compareText(a.title, b.title);
    });

    updateSortButtonLabel(sortKeyButton, "Sort Key", state.sortDirection.key);
    renderTracks();
}

function sortByTitle() {
    state.sortDirection.title *= -1;

    state.tracks.sort((a, b) =>
        compareText(a.title, b.title) * state.sortDirection.title
    );

    updateSortButtonLabel(sortAzButton, "Sort A-Z", state.sortDirection.title);
    renderTracks();
}

function sortVideosByTitle() {
    state.sortDirection.videoTitle *= -1;

    state.videos.sort((a, b) =>
        compareText(a.file_name, b.file_name) * state.sortDirection.videoTitle
    );

    updateSortButtonLabel(sortVideoAzButton, "Sort A-Z", state.sortDirection.videoTitle);
    renderVideos();
}

function camelotToSortable(key) {
    if (!key) {
        return { num: 99, type: "Z" };
    }

    const match = key.match(/^(\d+)([AB])$/i);
    if (!match) {
        return { num: 99, type: "Z" };
    }

    return {
        num: Number(match[1]),
        type: match[2].toUpperCase()
    };
}

function updateSortButtonLabel(button, label, direction) {
    const arrow = direction === 1 ? "^" : "v";
    button.textContent = `${label} ${arrow}`;
}

function setActiveTab(nextTab) {
    state.activeTab = nextTab;
    const audioActive = nextTab === "audio";

    audioTabButton.classList.toggle("active", audioActive);
    audioTabButton.setAttribute("aria-selected", audioActive ? "true" : "false");
    audioPanel.classList.toggle("active", audioActive);
    audioPanel.hidden = !audioActive;

    videoTabButton.classList.toggle("active", !audioActive);
    videoTabButton.setAttribute("aria-selected", !audioActive ? "true" : "false");
    videoPanel.classList.toggle("active", !audioActive);
    videoPanel.hidden = audioActive;
}

audioTabButton.addEventListener("click", () => setActiveTab("audio"));
videoTabButton.addEventListener("click", () => setActiveTab("video"));
refreshAudioButton.addEventListener("click", fetchTracks);
refreshVideoButton.addEventListener("click", fetchVideos);
renderButton.addEventListener("click", renderMix);
masterVideoButton.addEventListener("click", generateVideoMaster);
openOutputAudioButton.addEventListener("click", () => openOutputFolder("audio"));
openOutputVideoButton.addEventListener("click", () => openOutputFolder("video"));
sortBpmButton.addEventListener("click", sortByBpm);
sortKeyButton.addEventListener("click", sortByKey);
sortAzButton.addEventListener("click", sortByTitle);
sortVideoAzButton.addEventListener("click", sortVideosByTitle);

window.addEventListener("load", () => {
    updateSortButtonLabel(sortBpmButton, "Sort BPM", state.sortDirection.bpm);
    updateSortButtonLabel(sortKeyButton, "Sort Key", state.sortDirection.key);
    updateSortButtonLabel(sortAzButton, "Sort A-Z", state.sortDirection.title);
    updateSortButtonLabel(sortVideoAzButton, "Sort A-Z", state.sortDirection.videoTitle);
    setActiveTab("audio");
    fetchTracks();
    fetchVideos();
});
