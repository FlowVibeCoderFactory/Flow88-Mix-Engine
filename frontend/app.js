const apiBase = window.location.origin.startsWith("http")
    ? window.location.origin
    : "http://localhost:8000";
const AUDIO_CROSSFADE_SECONDS = 15.0;
const AUTOSAVE_INTERVAL_MS = 10000;
const DEFAULT_UI_SCALE = 1.1;
const DEFAULT_AUDIO_FILE = "output/final_mix.wav";

const audioTabButton = document.getElementById("audio-tab-button");
const videoTabButton = document.getElementById("video-tab-button");
const audioPanel = document.getElementById("audio-panel");
const videoPanel = document.getElementById("video-panel");
const openProjectButton = document.getElementById("open-project-button");
const saveProjectButton = document.getElementById("save-project-button");
const saveProjectAsButton = document.getElementById("save-project-as-button");
const interfaceScaleSelect = document.getElementById("interface-scale-select");

const trackListBody = document.getElementById("track-list-body");
const videoListBody = document.getElementById("video-list-body");

const refreshAudioButton = document.getElementById("refresh-audio-button");
const refreshVideoButton = document.getElementById("refresh-video-button");
const renderButton = document.getElementById("render-button");
const masterVideoButton = document.getElementById("master-video-button");
const previewVideoButton = document.getElementById("preview-video-button");
const openSourceAudioButton = document.getElementById("open-source-audio-button");
const openSourceVideoButton = document.getElementById("open-source-video-button");
const openOutputAudioButton = document.getElementById("open-output-audio-button");
const openOutputVideoButton = document.getElementById("open-output-video-button");
const renderProfileSelect = document.getElementById("render-profile-select");
const transitionTypeSelect = document.getElementById("transition-type-select");
const transitionDurationRange = document.getElementById("transition-duration-range");
const transitionDurationValue = document.getElementById("transition-duration-value");
const transitionCurveSelect = document.getElementById("transition-curve-select");

const sortBpmButton = document.getElementById("sort-bpm-button");
const sortKeyButton = document.getElementById("sort-key-button");
const sortAzButton = document.getElementById("sort-az-button");
const sortVideoAzButton = document.getElementById("sort-video-az-button");

const audioStatusElement = document.getElementById("audio-status");
const videoStatusElement = document.getElementById("video-status");
const videoEtaElement = document.getElementById("video-eta");
const audioProgressElement = document.getElementById("audio-progress");
const videoProgressElement = document.getElementById("video-progress");
const videoProgressBarElement = document.getElementById("video-progress-bar");
const audioTotalDurationElement = document.getElementById("audio-total-duration");

const state = {
    activeTab: "audio",
    tracks: [],
    videos: [],
    videoProfiles: {},
    selectedRenderProfile: "balanced",
    videoTransition: {
        enabled: true,
        type: "fade",
        duration: 1.0,
        curve: "linear"
    },
    videoJobId: null,
    videoJobPollHandle: null,
    nextVideoItemId: 1,
    currentProjectPath: null,
    audioFile: DEFAULT_AUDIO_FILE,
    uiScale: DEFAULT_UI_SCALE,
    autosaveTimerHandle: null,
    autosaveArmed: false,
    availableVideos: [],
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

function closeOpenMenus() {
    document.querySelectorAll(".menu-wrap[open]").forEach((menu) => {
        menu.removeAttribute("open");
    });
}

function parseLoopCount(value) {
    return Math.max(1, Math.floor(Number(value) || 1));
}

function setUiScale(value) {
    const parsed = Number(value);
    const clamped = Math.max(0.9, Math.min(1.5, Number.isFinite(parsed) ? parsed : DEFAULT_UI_SCALE));
    state.uiScale = Math.round(clamped * 100) / 100;
    document.documentElement.style.setProperty("--ui-scale", String(state.uiScale));
    if (interfaceScaleSelect) {
        interfaceScaleSelect.value = String(state.uiScale);
    }
}

function formatBpm(bpm) {
    if (bpm === null || bpm === undefined || Number.isNaN(Number(bpm))) {
        return "--";
    }
    return Math.round(Number(bpm));
}

function formatDuration(seconds) {
    if (seconds === null || seconds === undefined) {
        return "--:--";
    }

    const numeric = Number(seconds);
    if (!Number.isFinite(numeric) || numeric < 0) {
        return "--:--";
    }

    const totalSeconds = Math.floor(Math.max(0, numeric));
    const minutes = String(Math.floor(totalSeconds / 60)).padStart(2, "0");
    const remainder = String(totalSeconds % 60).padStart(2, "0");
    return `${minutes}:${remainder}`;
}

function readTrackDurationSeconds(track) {
    const rawDuration = track?.duration ?? track?.duration_seconds;
    const duration = Number(rawDuration);
    if (!Number.isFinite(duration) || duration < 0) {
        return 0;
    }
    return duration;
}

function readTrackTrimmedDurationSeconds(track) {
    const trimStart = Number(track?.trim_start_seconds);
    const trimEnd = Number(track?.trim_end_seconds);
    if (Number.isFinite(trimStart) && Number.isFinite(trimEnd)) {
        return Math.max(0, trimEnd - trimStart);
    }
    return readTrackDurationSeconds(track);
}

function computeAudioStartTimes(tracks, crossfadeSeconds = AUDIO_CROSSFADE_SECONDS) {
    if (!Array.isArray(tracks) || tracks.length === 0) {
        return [];
    }

    const startTimes = [0];
    let absoluteStart = 0;

    for (let index = 0; index < tracks.length - 1; index += 1) {
        const currentDuration = readTrackTrimmedDurationSeconds(tracks[index]);
        const nextDuration = readTrackTrimmedDurationSeconds(tracks[index + 1]);
        const overlap = Math.max(0, Math.min(crossfadeSeconds, currentDuration, nextDuration));
        absoluteStart += Math.max(0, currentDuration - overlap);
        startTimes.push(Math.max(0, absoluteStart));
    }

    return startTimes;
}

function computeAudioMixLength(tracks, crossfadeSeconds = AUDIO_CROSSFADE_SECONDS) {
    if (!Array.isArray(tracks) || tracks.length === 0) {
        return null;
    }

    const startTimes = computeAudioStartTimes(tracks, crossfadeSeconds);
    const lastTrackDuration = readTrackTrimmedDurationSeconds(tracks[tracks.length - 1]);
    return Math.max(0, startTimes[startTimes.length - 1] + lastTrackDuration);
}

function setAudioTotalDuration(seconds) {
    if (!audioTotalDurationElement) {
        return;
    }
    audioTotalDurationElement.textContent = formatDuration(seconds);
}

function formatFrameRate(value) {
    const numeric = Number(value);
    if (!Number.isFinite(numeric) || numeric <= 0) {
        return "-- fps";
    }
    return `${Math.round(numeric * 10) / 10} fps`;
}

function formatEta(seconds) {
    const numeric = Number(seconds);
    if (!Number.isFinite(numeric) || numeric < 0) {
        return "--";
    }
    const rounded = Math.ceil(numeric);
    const minutes = Math.floor(rounded / 60);
    const remainder = String(rounded % 60).padStart(2, "0");
    return `${minutes}:${remainder}`;
}

function clampPercent(value) {
    const numeric = Number(value);
    if (!Number.isFinite(numeric)) {
        return 0;
    }
    return Math.max(0, Math.min(100, numeric));
}

function createVideoQueueItem(video, loopCount = 1) {
    const parsedLoopCount = parseLoopCount(loopCount);
    return {
        ...video,
        loop_count: parsedLoopCount,
        queue_id: state.nextVideoItemId++
    };
}

function compareText(valueA, valueB) {
    const left = String(valueA ?? "");
    const right = String(valueB ?? "");
    return left.localeCompare(right, undefined, { sensitivity: "base" });
}

function buildVideoPreviewUrl(fileName) {
    return `${apiBase}/input/videos/${encodeURIComponent(fileName)}`;
}

function renderProfileOptions() {
    if (!renderProfileSelect) {
        return;
    }

    let profileNames = Object.keys(state.videoProfiles).filter((profileName) => profileName !== "preview");
    if (profileNames.length === 0) {
        state.videoProfiles = {
            performance: {},
            balanced: {},
            quality: {}
        };
        profileNames = Object.keys(state.videoProfiles);
    }

    renderProfileSelect.innerHTML = "";
    profileNames.forEach((profileName) => {
        const option = document.createElement("option");
        option.value = profileName;
        option.textContent = profileName;
        renderProfileSelect.appendChild(option);
    });

    if (!profileNames.includes(state.selectedRenderProfile)) {
        state.selectedRenderProfile = profileNames.includes("balanced") ? "balanced" : profileNames[0];
    }
    renderProfileSelect.value = state.selectedRenderProfile;
}

function syncTransitionControls() {
    if (transitionTypeSelect) {
        transitionTypeSelect.value = state.videoTransition.type;
    }
    if (transitionCurveSelect) {
        transitionCurveSelect.value = state.videoTransition.curve;
    }
    if (transitionDurationRange) {
        transitionDurationRange.value = String(state.videoTransition.duration);
    }
    if (transitionDurationValue) {
        transitionDurationValue.textContent = `${Number(state.videoTransition.duration).toFixed(1)}s`;
    }
}

function setVideoProgress(percent) {
    const clamped = clampPercent(percent);
    if (videoProgressBarElement) {
        videoProgressBarElement.style.width = `${clamped}%`;
    }
}

function setVideoEta(seconds) {
    if (!videoEtaElement) {
        return;
    }

    if (seconds === null || seconds === undefined || !Number.isFinite(Number(seconds))) {
        videoEtaElement.textContent = "";
        videoEtaElement.classList.add("hidden");
        return;
    }

    videoEtaElement.textContent = `ETA ${formatEta(seconds)} min`;
    videoEtaElement.classList.remove("hidden");
}

function stopVideoJobPolling() {
    if (state.videoJobPollHandle) {
        window.clearInterval(state.videoJobPollHandle);
        state.videoJobPollHandle = null;
    }
    state.videoJobId = null;
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
        setAudioTotalDuration(null);
        setStatus("audio", "No tracks found in /input.", "error");
        return;
    }

    const startTimes = computeAudioStartTimes(state.tracks, AUDIO_CROSSFADE_SECONDS);
    const totalMixDurationSeconds = computeAudioMixLength(state.tracks, AUDIO_CROSSFADE_SECONDS);

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
            <td class="col-start"><span class="badge">${formatDuration(startTimes[index])}</span></td>
            <td class="col-duration"><span class="badge">${formatDuration(readTrackDurationSeconds(track))}</span></td>
        `;

        attachDragHandlers(row, (fromIndex, toIndex) => {
            const [movedTrack] = state.tracks.splice(fromIndex, 1);
            state.tracks.splice(toIndex, 0, movedTrack);
            renderTracks();
        });

        trackListBody.appendChild(row);
    });

    setAudioTotalDuration(totalMixDurationSeconds);

    setStatus("audio", `Loaded ${state.tracks.length} tracks. Drag, sort, then render.`);
}

function renderVideos() {
    videoListBody.innerHTML = "";

    if (state.videos.length === 0) {
        setStatus("video", "Video queue is empty. Refresh to reload clips from /input/videos.", "error");
        return;
    }

    state.videos.forEach((videoItem, index) => {
        const row = document.createElement("tr");
        row.className = "track-row video-row";
        row.draggable = true;
        row.dataset.index = String(index);

        const previewUrl = buildVideoPreviewUrl(videoItem.file_name);
        const resolution = videoItem.width && videoItem.height ? `${videoItem.width}x${videoItem.height}` : "Unknown size";
        row.innerHTML = `
            <td class="col-grip" aria-hidden="true">&#8942;&#8942;</td>
            <td class="col-video-preview">
                <video class="video-thumb" src="${escapeHtml(previewUrl)}" muted playsinline preload="metadata"></video>
            </td>
            <td class="col-video-title">${escapeHtml(videoItem.file_name)}</td>
            <td class="col-video-details">
                <span class="video-meta-stack">
                    <span class="badge">${formatDuration(videoItem.duration_seconds)}</span>
                    <span class="badge">${escapeHtml(resolution)}</span>
                    <span class="badge">${escapeHtml(formatFrameRate(videoItem.frame_rate))}</span>
                </span>
            </td>
            <td class="col-video-loop">
                <input
                    class="video-loop-input"
                    type="number"
                    min="1"
                    step="1"
                    value="${videoItem.loop_count}"
                    aria-label="Loop count for ${escapeHtml(videoItem.file_name)}"
                >
            </td>
            <td class="col-video-actions">
                <span class="video-actions-wrap">
                    <button type="button" class="row-action-button video-clone-button">Clone</button>
                    <button type="button" class="row-action-button video-remove-button">Remove</button>
                </span>
            </td>
        `;

        attachDragHandlers(row, (fromIndex, toIndex) => {
            const [movedVideo] = state.videos.splice(fromIndex, 1);
            state.videos.splice(toIndex, 0, movedVideo);
            renderVideos();
            scheduleAutosave();
        });

        const loopInput = row.querySelector(".video-loop-input");
        loopInput?.addEventListener("change", () => {
            const parsed = parseLoopCount(loopInput.value);
            loopInput.value = String(parsed);
            state.videos[index].loop_count = parsed;
            scheduleAutosave();
        });

        const cloneButton = row.querySelector(".video-clone-button");
        cloneButton?.addEventListener("click", () => {
            const clonedItem = createVideoQueueItem(videoItem, videoItem.loop_count);
            state.videos.splice(index + 1, 0, clonedItem);
            renderVideos();
            scheduleAutosave();
        });

        const removeButton = row.querySelector(".video-remove-button");
        removeButton?.addEventListener("click", () => {
            state.videos.splice(index, 1);
            renderVideos();
            scheduleAutosave();
        });

        videoListBody.appendChild(row);
    });

    setStatus(
        "video",
        `Queue has ${state.videos.length} clip entr${state.videos.length === 1 ? "y" : "ies"}. Drag, clone, set loops, then master video.`
    );
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

        const videoList = payload.videos ?? [];
        state.availableVideos = videoList;
        state.videos = videoList.map((video) => createVideoQueueItem(video, 1));
        renderVideos();
        scheduleAutosave();
    } catch (error) {
        setStatus("video", error.message, "error");
    }
}

async function fetchVideoRenderProfiles() {
    try {
        const response = await fetch(`${apiBase}/video-render-profiles`);
        const payload = await response.json();
        if (!response.ok) {
            throw new Error(payload.detail || "Failed to load video render profiles.");
        }

        state.videoProfiles = payload.profiles ?? {};
        state.selectedRenderProfile = payload.default_profile ?? "balanced";
        renderProfileOptions();
    } catch (error) {
        state.videoProfiles = {
            performance: {},
            balanced: {},
            quality: {}
        };
        state.selectedRenderProfile = "balanced";
        renderProfileOptions();
        setStatus("video", `Render profile list unavailable: ${error.message}`, "error");
    }
}

function buildVideoItemsPayload() {
    return state.videos.map((video) => ({
        file_name: video.file_name,
        loop_count: parseLoopCount(video.loop_count)
    }));
}

function buildTransitionPayload() {
    return {
        enabled: Boolean(state.videoTransition.enabled),
        type: String(state.videoTransition.type || "fade").toLowerCase(),
        duration: Math.max(0.2, Math.min(3.0, Number(state.videoTransition.duration) || 1.0)),
        curve: String(state.videoTransition.curve || "linear").toLowerCase()
    };
}

function buildProjectPayload() {
    return {
        format: "flowmix",
        version: 1,
        ordered_clips: state.videos.map((video) => String(video.file_name || "")),
        loop_counts: state.videos.map((video) => parseLoopCount(video.loop_count)),
        transition: buildTransitionPayload(),
        audio_file: state.audioFile || DEFAULT_AUDIO_FILE,
        render_settings: {
            render_profile: state.selectedRenderProfile || "balanced",
            interface_scale: state.uiScale
        }
    };
}

async function save_project(path) {
    const response = await fetch(`${apiBase}/project/save`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
            path,
            project: buildProjectPayload(),
            autosave: false
        })
    });

    if (!response.ok) {
        const message = await readApiError(response, `Project save failed (HTTP ${response.status}).`);
        throw new Error(message);
    }

    return response.json();
}

async function load_project(path) {
    const response = await fetch(`${apiBase}/project/load`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path })
    });
    if (!response.ok) {
        const message = await readApiError(response, `Project load failed (HTTP ${response.status}).`);
        throw new Error(message);
    }
    return response.json();
}

function applyProjectPayload(project) {
    const orderedClips = Array.isArray(project?.ordered_clips) ? project.ordered_clips : [];
    const loopCounts = Array.isArray(project?.loop_counts) ? project.loop_counts : [];
    const transition = project?.transition || {};
    const renderSettings = project?.render_settings || {};

    if (orderedClips.length !== loopCounts.length) {
        throw new Error("Project file has mismatched clip and loop arrays.");
    }

    const availableByName = new Map(state.availableVideos.map((video) => [video.file_name, video]));
    state.videos = orderedClips.map((fileName, index) => {
        const base = availableByName.get(fileName) || {
            file_name: fileName,
            duration_seconds: 0,
            width: null,
            height: null,
            frame_rate: null
        };
        return createVideoQueueItem(base, parseLoopCount(loopCounts[index]));
    });

    state.videoTransition.type = String(transition.type || "fade").toLowerCase();
    state.videoTransition.curve = String(transition.curve || "linear").toLowerCase();
    state.videoTransition.duration = Math.max(0.2, Math.min(3.0, Number(transition.duration) || 1.0));
    state.videoTransition.enabled = Boolean(transition.enabled ?? true);

    state.audioFile = String(project?.audio_file || DEFAULT_AUDIO_FILE);
    const requestedProfile = String(renderSettings.render_profile || "balanced").toLowerCase();
    state.selectedRenderProfile = requestedProfile;
    setUiScale(renderSettings.interface_scale ?? DEFAULT_UI_SCALE);

    renderProfileOptions();
    syncTransitionControls();
    renderVideos();
}

async function listProjects() {
    const response = await fetch(`${apiBase}/projects`);
    if (!response.ok) {
        const message = await readApiError(response, `Failed to list projects (HTTP ${response.status}).`);
        throw new Error(message);
    }
    return response.json();
}

async function saveProjectAs() {
    try {
        const defaultName = "project.flowmix";
        const requestedName = window.prompt("Save Project As (.flowmix)", defaultName);
        if (!requestedName) {
            return;
        }

        const normalizedName = requestedName.trim().toLowerCase().endsWith(".flowmix")
            ? requestedName.trim()
            : `${requestedName.trim()}.flowmix`;
        const saved = await save_project(normalizedName);
        state.currentProjectPath = saved.path;
        setStatus("video", `Project saved: ${saved.path}`, "success");
    } catch (error) {
        setStatus("video", error.message, "error");
    }
}

async function saveProject() {
    try {
        if (!state.currentProjectPath) {
            await saveProjectAs();
            return;
        }

        const saved = await save_project(state.currentProjectPath);
        state.currentProjectPath = saved.path;
        setStatus("video", `Project saved: ${saved.path}`, "success");
    } catch (error) {
        setStatus("video", error.message, "error");
    }
}

async function openProject() {
    try {
        const listing = await listProjects();
        const projectFiles = listing.projects || [];
        if (projectFiles.length === 0) {
            setStatus("video", `No project files found in ${listing.project_dir}.`, "error");
            return;
        }

        const optionsText = projectFiles.map((item, index) => `${index + 1}. ${item.file_name}`).join("\n");
        const selected = window.prompt(
            `Open Project\n${optionsText}\n\nEnter a number or file name:`,
            projectFiles[0].file_name
        );
        if (!selected) {
            return;
        }

        const parsedIndex = Number(selected);
        let chosenFile = null;
        if (Number.isInteger(parsedIndex) && parsedIndex >= 1 && parsedIndex <= projectFiles.length) {
            chosenFile = projectFiles[parsedIndex - 1];
        } else {
            const normalizedSelection = selected.trim().toLowerCase();
            chosenFile = projectFiles.find((item) => item.file_name.toLowerCase() === normalizedSelection) || null;
        }
        if (!chosenFile) {
            throw new Error("Selected project was not found.");
        }

        const payload = await load_project(chosenFile.file_name);
        applyProjectPayload(payload.project);
        state.currentProjectPath = payload.path;
        setStatus("video", `Project loaded: ${payload.path}`, "success");
    } catch (error) {
        setStatus("video", error.message, "error");
    }
}

async function autosaveProject() {
    if (!state.autosaveArmed) {
        return;
    }
    try {
        await fetch(`${apiBase}/project/save`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                project: buildProjectPayload(),
                autosave: true
            })
        });
    } catch (error) {
        console.error("Autosave failed", error);
    }
}

function scheduleAutosave() {
    void autosaveProject();
}

function startAutosaveLoop() {
    if (state.autosaveTimerHandle) {
        window.clearInterval(state.autosaveTimerHandle);
    }
    state.autosaveArmed = true;
    state.autosaveTimerHandle = window.setInterval(() => {
        void autosaveProject();
    }, AUTOSAVE_INTERVAL_MS);
}

async function maybeRestoreAutosave() {
    try {
        const response = await fetch(`${apiBase}/project/autosave`);
        if (!response.ok) {
            return;
        }

        const payload = await response.json();
        if (!payload?.exists || !payload.project) {
            return;
        }

        const savedAt = payload.project.saved_at ? ` (${payload.project.saved_at})` : "";
        const shouldRestore = window.confirm(`Restore autosaved project${savedAt}?`);
        if (!shouldRestore) {
            return;
        }

        applyProjectPayload(payload.project);
        state.currentProjectPath = null;
        setStatus("video", `Autosave restored from ${payload.path}. Use Save Project As to keep it.`, "success");
    } catch (error) {
        setStatus("video", `Autosave restore failed: ${error.message}`, "error");
    }
}

async function readApiError(response, fallbackMessage) {
    const contentType = response.headers.get("content-type") || "";
    if (contentType.includes("application/json")) {
        const payload = await response.json();
        if (typeof payload?.detail === "string" && payload.detail.trim()) {
            return payload.detail.trim();
        }
    } else {
        const text = (await response.text()).trim();
        if (text) {
            return text;
        }
    }
    return fallbackMessage;
}

async function runRenderPreflight(items, renderProfile, transition) {
    const response = await fetch(`${apiBase}/render-preflight`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
            items,
            render_profile: renderProfile,
            transition
        })
    });

    if (!response.ok) {
        const message = await readApiError(response, `Preflight failed (HTTP ${response.status}).`);
        throw new Error(message);
    }

    return response.json();
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

async function startVideoJob(endpoint, requestBody, queuedLabel) {
    const response = await fetch(`${apiBase}${endpoint}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(requestBody)
    });

    if (!response.ok) {
        const message = await readApiError(response, `Video mastering failed (HTTP ${response.status}).`);
        throw new Error(message);
    }

    const payload = await response.json();
    if (!payload?.job_id) {
        throw new Error("Video job started but no job_id was returned.");
    }

    state.videoJobId = payload.job_id;
    setStatus("video", `${queuedLabel}. Job ID: ${payload.job_id}`);
    await pollVideoJobStatus(payload.job_id);
    if (state.videoJobId === payload.job_id) {
        state.videoJobPollHandle = window.setInterval(() => {
            void pollVideoJobStatus(payload.job_id);
        }, 1000);
    }
}

async function generateVideo() {
    if (state.videos.length === 0) {
        setStatus("video", "Video queue is empty.", "error");
        return;
    }

    stopVideoJobPolling();
    masterVideoButton.disabled = true;
    if (previewVideoButton) {
        previewVideoButton.disabled = true;
    }
    openOutputVideoButton.disabled = true;
    setVideoProgress(0);
    setVideoEta(null);
    setRenderProgress("video", true);
    setStatus("video", "Running render preflight...");

    try {
        const items = buildVideoItemsPayload();
        const transition = buildTransitionPayload();
        const preflight = await runRenderPreflight(items, state.selectedRenderProfile, transition);
        setStatus(
            "video",
            `Preflight passed (${preflight.scene_count} scenes, ${Math.round(preflight.target_duration_seconds)}s). Queueing render...`
        );

        await startVideoJob(
            "/generate-video",
            {
                items,
                render_profile: state.selectedRenderProfile,
                transition
            },
            "Render started"
        );
    } catch (error) {
        stopVideoJobPolling();
        masterVideoButton.disabled = false;
        if (previewVideoButton) {
            previewVideoButton.disabled = false;
        }
        openOutputVideoButton.disabled = false;
        setRenderProgress("video", false);
        setVideoEta(null);
        setStatus("video", error.message, "error");
    }
}

async function generatePreview() {
    if (state.videos.length === 0) {
        setStatus("video", "Video queue is empty.", "error");
        return;
    }

    stopVideoJobPolling();
    masterVideoButton.disabled = true;
    if (previewVideoButton) {
        previewVideoButton.disabled = true;
    }
    openOutputVideoButton.disabled = true;
    setVideoProgress(0);
    setVideoEta(null);
    setRenderProgress("video", true);
    setStatus("video", "Queueing preview render...");

    try {
        await startVideoJob(
            "/generate-preview",
            {
                items: buildVideoItemsPayload(),
                render_profile: "preview",
                transition: buildTransitionPayload()
            },
            "Preview started"
        );
    } catch (error) {
        stopVideoJobPolling();
        masterVideoButton.disabled = false;
        if (previewVideoButton) {
            previewVideoButton.disabled = false;
        }
        openOutputVideoButton.disabled = false;
        setRenderProgress("video", false);
        setVideoEta(null);
        setStatus("video", error.message, "error");
    }
}

async function pollVideoJobStatus(jobId) {
    try {
        const response = await fetch(`${apiBase}/video-jobs/${encodeURIComponent(jobId)}`);
        const payload = await response.json();
        if (!response.ok) {
            throw new Error(payload.detail || `Failed to read video job ${jobId}.`);
        }

        const percent = clampPercent(payload.percent);
        setVideoProgress(percent);
        setVideoEta(payload.eta_seconds);

        const status = String(payload.status || "").toLowerCase();
        const message = payload.message || `Job ${status}.`;

        if (status === "queued" || status === "running") {
            setStatus("video", `${message} ${percent.toFixed(1)}%`);
            return;
        }

        stopVideoJobPolling();
        masterVideoButton.disabled = false;
        if (previewVideoButton) {
            previewVideoButton.disabled = false;
        }
        openOutputVideoButton.disabled = false;
        setVideoEta(payload.eta_seconds);

        if (status === "done") {
            setVideoProgress(100);
            setRenderProgress("video", true);
            const outputPath = payload.output_path || "(output path unavailable)";
            const isPreview = String(outputPath).toLowerCase().endsWith("output_preview.mp4");
            setStatus("video", `${isPreview ? "Preview" : "Master video"} complete: ${outputPath}`, "success");
            return;
        }

        const errorMessage = payload.log_path
            ? `${payload.message || "Video mastering failed."} Log: ${payload.log_path}`
            : (payload.message || "Video mastering failed.");
        setRenderProgress("video", false);
        setVideoEta(null);
        setStatus("video", errorMessage, "error");
    } catch (error) {
        stopVideoJobPolling();
        masterVideoButton.disabled = false;
        if (previewVideoButton) {
            previewVideoButton.disabled = false;
        }
        openOutputVideoButton.disabled = false;
        setRenderProgress("video", false);
        setVideoEta(null);
        setStatus("video", error.message, "error");
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

async function openSourceFolder(tab) {
    setStatus(tab, "Opening source folder...");
    const endpoint = tab === "video" ? "/open-video-source" : "/open-audio-source";

    try {
        const response = await fetch(`${apiBase}${endpoint}`);
        const payload = await response.json();
        if (!response.ok) {
            throw new Error(payload.detail || "Failed to open source folder.");
        }

        const folderPath = tab === "video" ? payload.video_source_dir : payload.audio_source_dir;
        setStatus(tab, `Opened source folder: ${folderPath}`, "success");
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
    scheduleAutosave();
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
masterVideoButton.addEventListener("click", generateVideo);
previewVideoButton?.addEventListener("click", generatePreview);
openProjectButton?.addEventListener("click", () => {
    closeOpenMenus();
    void openProject();
});
saveProjectButton?.addEventListener("click", () => {
    closeOpenMenus();
    void saveProject();
});
saveProjectAsButton?.addEventListener("click", () => {
    closeOpenMenus();
    void saveProjectAs();
});
openSourceAudioButton.addEventListener("click", () => openSourceFolder("audio"));
openSourceVideoButton.addEventListener("click", () => openSourceFolder("video"));
openOutputAudioButton.addEventListener("click", () => openOutputFolder("audio"));
openOutputVideoButton.addEventListener("click", () => openOutputFolder("video"));
sortBpmButton.addEventListener("click", sortByBpm);
sortKeyButton.addEventListener("click", sortByKey);
sortAzButton.addEventListener("click", sortByTitle);
sortVideoAzButton.addEventListener("click", sortVideosByTitle);
renderProfileSelect?.addEventListener("change", () => {
    state.selectedRenderProfile = renderProfileSelect.value;
    scheduleAutosave();
});
transitionTypeSelect?.addEventListener("change", () => {
    state.videoTransition.type = String(transitionTypeSelect.value || "fade").toLowerCase();
    syncTransitionControls();
    scheduleAutosave();
});
transitionCurveSelect?.addEventListener("change", () => {
    state.videoTransition.curve = String(transitionCurveSelect.value || "linear").toLowerCase();
    syncTransitionControls();
    scheduleAutosave();
});
transitionDurationRange?.addEventListener("input", () => {
    const parsed = Math.max(0.2, Math.min(3.0, Number(transitionDurationRange.value) || 1.0));
    state.videoTransition.duration = Math.round(parsed * 10) / 10;
    syncTransitionControls();
    scheduleAutosave();
});
interfaceScaleSelect?.addEventListener("change", () => {
    setUiScale(interfaceScaleSelect.value);
    scheduleAutosave();
});

document.addEventListener("click", (event) => {
    const target = event.target;
    if (!(target instanceof Element)) {
        return;
    }
    if (target.closest(".menu-wrap")) {
        return;
    }
    closeOpenMenus();
});

window.addEventListener("load", async () => {
    updateSortButtonLabel(sortBpmButton, "Sort BPM", state.sortDirection.bpm);
    updateSortButtonLabel(sortKeyButton, "Sort Key", state.sortDirection.key);
    updateSortButtonLabel(sortAzButton, "Sort A-Z", state.sortDirection.title);
    updateSortButtonLabel(sortVideoAzButton, "Sort A-Z", state.sortDirection.videoTitle);
    setUiScale(DEFAULT_UI_SCALE);
    renderProfileOptions();
    syncTransitionControls();
    setVideoProgress(0);
    setVideoEta(null);
    setActiveTab("audio");
    await fetchTracks();
    await fetchVideoRenderProfiles();
    await fetchVideos();
    await maybeRestoreAutosave();
    startAutosaveLoop();
});
