(() => {
    "use strict";

    const $ = (sel) => document.querySelector(sel);
    const TOKEN_KEY = "copyserver_token";
    const DEVICE_KEY = "copyserver_device";

    let token = localStorage.getItem(TOKEN_KEY);
    let device = JSON.parse(localStorage.getItem(DEVICE_KEY) || "null");
    let eventSource = null;
    let clips = [];
    let oldestTimestamp = null;

    // --- API helpers ---

    function api(path, opts = {}) {
        const headers = opts.headers || {};
        if (token) headers["Authorization"] = "Bearer " + token;
        return fetch(path, { ...opts, headers });
    }

    function apiJson(path, body) {
        return api(path, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
        });
    }

    // --- Routing ---

    async function init() {
        if (token) {
            const res = await api("/api/auth/verify");
            if (res.ok) {
                device = await res.json();
                showMain();
                return;
            }
            // Token invalid
            localStorage.removeItem(TOKEN_KEY);
            localStorage.removeItem(DEVICE_KEY);
            token = null;
            device = null;
        }
        showPair();
    }

    function showPair() {
        $("#pair-screen").classList.remove("hidden");
        $("#main-screen").classList.add("hidden");
        $("#passphrase-input").focus();
    }

    function showMain() {
        $("#pair-screen").classList.add("hidden");
        $("#main-screen").classList.remove("hidden");
        loadClips();
        connectSSE();
        registerPush();
    }

    // --- Pairing ---

    async function doPair() {
        const passphrase = $("#passphrase-input").value;
        const deviceName = $("#device-name-input").value.trim();
        if (!deviceName) {
            $("#pair-error").textContent = "Enter a device name";
            return;
        }
        const res = await fetch("/api/auth/pair", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ passphrase, deviceName }),
        });
        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            $("#pair-error").textContent = err.detail || "Pairing failed";
            return;
        }
        const data = await res.json();
        token = data.token;
        device = { id: data.id, name: data.name };
        localStorage.setItem(TOKEN_KEY, token);
        localStorage.setItem(DEVICE_KEY, JSON.stringify(device));
        showMain();
    }

    // --- Clips ---

    async function loadClips(append = false) {
        let url = "/api/clips?limit=50";
        if (append && oldestTimestamp) url += "&before=" + oldestTimestamp;
        const res = await api(url);
        if (!res.ok) return;
        const data = await res.json();
        if (append) {
            clips = clips.concat(data);
        } else {
            clips = data;
        }
        if (data.length > 0) {
            oldestTimestamp = data[data.length - 1].created_at;
        }
        renderClips();
        $("#load-more-btn").classList.toggle("hidden", data.length < 50);
    }

    function renderClips() {
        const list = $("#clips-list");
        list.innerHTML = "";
        for (const clip of clips) {
            list.appendChild(createClipEl(clip));
        }
    }

    function createClipEl(clip) {
        const el = document.createElement("div");
        el.className = "clip";
        el.dataset.id = clip.id;

        const time = new Date(clip.created_at * 1000);
        const timeStr = time.toLocaleString(undefined, {
            month: "short", day: "numeric",
            hour: "2-digit", minute: "2-digit",
        });

        let body = "";
        if (clip.type === "text") {
            body = `<div class="clip-content">${escHtml(clip.content)}</div>`;
        } else {
            const isImage = (clip.mimetype || "").startsWith("image/");
            const imgPreview = isImage
                ? `<img class="clip-image-preview" src="/api/clips/${clip.id}/download" alt="${escHtml(clip.filename)}" loading="lazy">`
                : "";
            body = `
                <div class="clip-file">
                    <div class="clip-file-info">
                        <div class="clip-file-name">${escHtml(clip.filename)}</div>
                        <div class="clip-file-size">${formatSize(clip.filesize)}</div>
                    </div>
                </div>
                ${imgPreview}`;
        }

        const copyBtn = clip.type === "text"
            ? `<button class="copy-btn">Copy</button>`
            : `<button class="download-btn">Download</button>`;

        el.innerHTML = `
            <div class="clip-header">
                <span>${escHtml(clip.device_name || "Unknown")}</span>
                <span>${timeStr}</span>
            </div>
            ${body}
            <div class="clip-actions">
                ${copyBtn}
                <button class="delete-btn">Delete</button>
            </div>`;

        // Event listeners
        const copyBtnEl = el.querySelector(".copy-btn");
        if (copyBtnEl) {
            copyBtnEl.addEventListener("click", () => {
                navigator.clipboard.writeText(clip.content).then(() => {
                    copyBtnEl.textContent = "Copied!";
                    setTimeout(() => copyBtnEl.textContent = "Copy", 1500);
                });
            });
        }

        const dlBtn = el.querySelector(".download-btn");
        if (dlBtn) {
            dlBtn.addEventListener("click", () => downloadClip(clip));
        }

        el.querySelector(".delete-btn").addEventListener("click", async () => {
            await api(`/api/clips/${clip.id}`, { method: "DELETE" });
            clips = clips.filter(c => c.id !== clip.id);
            el.remove();
        });

        return el;
    }

    async function downloadClip(clip) {
        const res = await api(`/api/clips/${clip.id}/download`);
        if (!res.ok) return;
        const blob = await res.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = clip.filename || "download";
        a.click();
        URL.revokeObjectURL(url);
    }

    // --- Share ---

    async function shareText() {
        const input = $("#text-input");
        const text = input.value.trim();
        if (!text) return;
        input.disabled = true;
        $("#share-btn").disabled = true;
        const res = await apiJson("/api/clips/text", { content: text });
        input.disabled = false;
        $("#share-btn").disabled = false;
        if (!res.ok) return;
        const clip = await res.json();
        clip.device_name = device.name;
        clips.unshift(clip);
        renderClips();
        input.value = "";
    }

    async function shareFiles(files) {
        for (const file of files) {
            const progress = $("#upload-progress");
            const bar = $("#upload-bar");
            progress.classList.remove("hidden");
            bar.style.width = "0%";

            const formData = new FormData();
            formData.append("file", file);

            // Use XMLHttpRequest for progress
            await new Promise((resolve, reject) => {
                const xhr = new XMLHttpRequest();
                xhr.open("POST", "/api/clips/file");
                xhr.setRequestHeader("Authorization", "Bearer " + token);

                xhr.upload.addEventListener("progress", (e) => {
                    if (e.lengthComputable) {
                        bar.style.width = Math.round((e.loaded / e.total) * 100) + "%";
                    }
                });

                xhr.addEventListener("load", () => {
                    if (xhr.status >= 200 && xhr.status < 300) {
                        const clip = JSON.parse(xhr.responseText);
                        clip.device_name = device.name;
                        clips.unshift(clip);
                        renderClips();
                    }
                    resolve();
                });

                xhr.addEventListener("error", resolve);
                xhr.send(formData);
            });

            progress.classList.add("hidden");
        }
    }

    // --- SSE ---

    function connectSSE() {
        if (eventSource) eventSource.close();
        eventSource = new EventSource("/api/events?token=" + token);

        // We need auth via header, but EventSource doesn't support headers.
        // Workaround: close EventSource and use fetch-based SSE.
        eventSource.close();
        connectSSEFetch();
    }

    async function connectSSEFetch() {
        const dot = $("#connection-dot");
        while (true) {
            try {
                const res = await fetch("/api/events", {
                    headers: { "Authorization": "Bearer " + token },
                });
                if (!res.ok) {
                    dot.className = "dot disconnected";
                    await sleep(3000);
                    continue;
                }
                dot.className = "dot connected";
                dot.title = "Connected";

                const reader = res.body.getReader();
                const decoder = new TextDecoder();
                let buffer = "";

                while (true) {
                    const { done, value } = await reader.read();
                    if (done) break;
                    buffer += decoder.decode(value, { stream: true });

                    const lines = buffer.split("\n");
                    buffer = lines.pop(); // keep incomplete line

                    for (const line of lines) {
                        if (line.startsWith("data: ")) {
                            try {
                                const clip = JSON.parse(line.slice(6));
                                // Avoid duplicates
                                if (!clips.find(c => c.id === clip.id)) {
                                    clips.unshift(clip);
                                    renderClips();
                                }
                            } catch {}
                        }
                    }
                }
            } catch {}

            dot.className = "dot disconnected";
            dot.title = "Disconnected";
            await sleep(3000);
        }
    }

    // --- Push notifications ---

    async function registerPush() {
        if (!("serviceWorker" in navigator) || !("PushManager" in window)) return;

        try {
            const reg = await navigator.serviceWorker.register("/sw.js");
            const keyRes = await api("/api/push/vapid-key");
            const { key } = await keyRes.json();
            if (!key) return;

            const permission = await Notification.requestPermission();
            if (permission !== "granted") return;

            const sub = await reg.pushManager.subscribe({
                userVisibleOnly: true,
                applicationServerKey: urlBase64ToUint8Array(key),
            });
            await api("/api/push/subscribe", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(sub.toJSON()),
            });
        } catch {}
    }

    // --- Devices modal ---

    async function loadDevices() {
        const res = await api("/api/devices");
        if (!res.ok) return;
        const devices = await res.json();
        const list = $("#devices-list");
        list.innerHTML = "";
        for (const d of devices) {
            const seen = new Date(d.last_seen_at * 1000).toLocaleString();
            const isSelf = d.id === device.id;
            const el = document.createElement("div");
            el.className = "device-item";
            el.innerHTML = `
                <div>
                    <div class="device-name">${escHtml(d.name)}${isSelf ? " (this)" : ""}</div>
                    <div class="device-seen">Last seen: ${seen}</div>
                </div>
                ${isSelf ? "" : '<button class="delete-btn">Remove</button>'}`;
            if (!isSelf) {
                el.querySelector(".delete-btn").addEventListener("click", async () => {
                    await api(`/api/devices/${d.id}`, { method: "DELETE" });
                    el.remove();
                });
            }
            list.appendChild(el);
        }
    }

    // --- Drag & drop ---

    function setupDragDrop() {
        const zone = $("#drop-zone");
        const overlay = $("#drop-overlay");
        let dragCount = 0;

        zone.addEventListener("dragenter", (e) => {
            e.preventDefault();
            dragCount++;
            zone.classList.add("dragover");
            overlay.classList.remove("hidden");
        });

        zone.addEventListener("dragleave", () => {
            dragCount--;
            if (dragCount <= 0) {
                dragCount = 0;
                zone.classList.remove("dragover");
                overlay.classList.add("hidden");
            }
        });

        zone.addEventListener("dragover", (e) => e.preventDefault());

        zone.addEventListener("drop", (e) => {
            e.preventDefault();
            dragCount = 0;
            zone.classList.remove("dragover");
            overlay.classList.add("hidden");
            if (e.dataTransfer.files.length) {
                shareFiles(e.dataTransfer.files);
            }
        });
    }

    // --- Paste handler ---

    function setupPaste() {
        document.addEventListener("paste", (e) => {
            // If focus is in textarea, let default behavior handle text paste
            if (document.activeElement === $("#text-input") && !e.clipboardData.files.length) return;

            if (e.clipboardData.files.length) {
                e.preventDefault();
                shareFiles(e.clipboardData.files);
            }
        });
    }

    // --- Event bindings ---

    function bindEvents() {
        // Pair
        $("#pair-btn").addEventListener("click", doPair);
        $("#passphrase-input").addEventListener("keydown", (e) => {
            if (e.key === "Enter") $("#device-name-input").focus();
        });
        $("#device-name-input").addEventListener("keydown", (e) => {
            if (e.key === "Enter") doPair();
        });

        // Share
        $("#share-btn").addEventListener("click", shareText);
        $("#text-input").addEventListener("keydown", (e) => {
            if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) shareText();
        });

        // File input
        $("#file-input").addEventListener("change", (e) => {
            if (e.target.files.length) {
                shareFiles(e.target.files);
                e.target.value = "";
            }
        });

        // Load more
        $("#load-more-btn").addEventListener("click", () => loadClips(true));

        // Devices modal
        $("#devices-btn").addEventListener("click", () => {
            $("#devices-modal").classList.remove("hidden");
            loadDevices();
        });
        $("#close-devices").addEventListener("click", () => {
            $("#devices-modal").classList.add("hidden");
        });
        $("#devices-modal").addEventListener("click", (e) => {
            if (e.target === $("#devices-modal")) {
                $("#devices-modal").classList.add("hidden");
            }
        });
    }

    // --- Utilities ---

    function escHtml(str) {
        const d = document.createElement("div");
        d.textContent = str || "";
        return d.innerHTML;
    }

    function formatSize(bytes) {
        if (!bytes) return "";
        if (bytes < 1024) return bytes + " B";
        if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + " KB";
        if (bytes < 1024 * 1024 * 1024) return (bytes / (1024 * 1024)).toFixed(1) + " MB";
        return (bytes / (1024 * 1024 * 1024)).toFixed(1) + " GB";
    }

    function sleep(ms) {
        return new Promise(r => setTimeout(r, ms));
    }

    function urlBase64ToUint8Array(base64String) {
        const padding = "=".repeat((4 - base64String.length % 4) % 4);
        const base64 = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/");
        const raw = atob(base64);
        const arr = new Uint8Array(raw.length);
        for (let i = 0; i < raw.length; i++) arr[i] = raw.charCodeAt(i);
        return arr;
    }

    // --- Init ---

    bindEvents();
    setupDragDrop();
    setupPaste();
    init();
})();
