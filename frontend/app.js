const apiBase = window.location.origin.startsWith("http")
    ? window.location.origin
    : "http://localhost:8000";

const trackListBody = document.getElementById("track-list-body");
const refreshButton = document.getElementById("refresh-button");
const renderButton = document.getElementById("render-button");
const openOutputButton = document.getElementById("open-output-button");
const sortBpmButton = document.getElementById("sort-bpm-button");
const sortKeyButton = document.getElementById("sort-key-button");
const sortAzButton = document.getElementById("sort-az-button");
const statusElement = document.getElementById("status");

const state = {
    tracks: [],
    sortDirection: {
        bpm: 1,
        key: 1,
        title: 1
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

function setStatus(message, type = "") {
    statusElement.textContent = message;
    statusElement.classList.remove("success", "error");
    if (type) {
        statusElement.classList.add(type);
    }
}

function formatBpm(bpm) {
    if (bpm === null || bpm === undefined || Number.isNaN(Number(bpm))) {
        return "--";
    }
    return Math.round(Number(bpm));
}

function compareText(valueA, valueB) {
    const left = String(valueA ?? "");
    const right = String(valueB ?? "");
    return left.localeCompare(right, undefined, { sensitivity: "base" });
}

function renderTracks() {
    trackListBody.innerHTML = "";

    if (state.tracks.length === 0) {
        setStatus("No tracks found in /input.", "error");
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

            const [movedTrack] = state.tracks.splice(fromIndex, 1);
            state.tracks.splice(toIndex, 0, movedTrack);
            renderTracks();
        });

        trackListBody.appendChild(row);
    });

    setStatus(`Loaded ${state.tracks.length} tracks. Drag, sort, then render.`);
}

async function fetchTracks() {
    setStatus("Loading track metadata...");

    try {
        const response = await fetch(`${apiBase}/tracks`);
        const payload = await response.json();
        if (!response.ok) {
            throw new Error(payload.detail || "Failed to load tracks.");
        }

        state.tracks = payload.tracks ?? [];
        renderTracks();
    } catch (error) {
        setStatus(error.message, "error");
    }
}

async function renderMix() {
    if (state.tracks.length === 0) {
        setStatus("Track queue is empty.", "error");
        return;
    }

    renderButton.disabled = true;
    setStatus("Rendering mix...");

    try {
        const response = await fetch(`${apiBase}/mix`, {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
            },
            body: JSON.stringify({
                tracks: state.tracks.map((track) => track.file_name),
            }),
        });

        const payload = await response.json();
        if (!response.ok) {
            throw new Error(payload.detail || "Mix render failed.");
        }

        setStatus(`Render complete: ${payload.mix_output_path}`, "success");
    } catch (error) {
        setStatus(error.message, "error");
    } finally {
        renderButton.disabled = false;
    }
}

async function openOutputFolder() {
    setStatus("Opening output folder...");

    try {
        const response = await fetch(`${apiBase}/open-output`);
        const payload = await response.json();
        if (!response.ok) {
            throw new Error(payload.detail || "Failed to open output folder.");
        }

        setStatus(`Opened output folder: ${payload.output_dir}`, "success");
    } catch (error) {
        setStatus(error.message, "error");
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

function camelotToSortable(key) {
    if (!key) return { num: 99, type: "Z" };

    const match = key.match(/^(\d+)([AB])$/i);
    if (!match) return { num: 99, type: "Z" };

    return {
        num: Number(match[1]),
        type: match[2].toUpperCase()
    };
}

function updateSortButtonLabel(button, label, direction) {
    const arrow = direction === 1 ? "▲" : "▼";
    button.textContent = `${label} ${arrow}`;
}

refreshButton.addEventListener("click", fetchTracks);
renderButton.addEventListener("click", renderMix);
openOutputButton.addEventListener("click", openOutputFolder);
sortBpmButton.addEventListener("click", sortByBpm);
sortKeyButton.addEventListener("click", sortByKey);
sortAzButton.addEventListener("click", sortByTitle);
window.addEventListener("load", () => {
    updateSortButtonLabel(sortBpmButton, "Sort BPM", state.sortDirection.bpm);
    updateSortButtonLabel(sortKeyButton, "Sort Key", state.sortDirection.key);
    updateSortButtonLabel(sortAzButton, "Sort A-Z", state.sortDirection.title);
    fetchTracks();
});
