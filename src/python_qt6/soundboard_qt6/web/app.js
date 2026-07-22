(function () {
  const grid = document.getElementById("remoteGrid");
  const status = document.getElementById("connectionStatus");
  const statusText = document.getElementById("connectionText");
  const fullscreenButton = document.getElementById("fullscreenButton");
  const modeHint = document.getElementById("modeHint");
  const layerModeButton = document.getElementById("layerModeButton");
  const replaceModeButton = document.getElementById("replaceModeButton");
  const stopAllButton = document.getElementById("stopAllButton");
  const padElements = new Map();
  const imageRequests = new WeakMap();
  let socket = null;
  let reconnectTimer = null;
  let focusFullscreenFallback = false;
  let playbackMode = "layer";
  let audioReady = false;
  let activePadIds = new Set();

  function socketUrl() {
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    return `${protocol}//${window.location.host}/ws`;
  }

  function setStatus(text, className) {
    statusText.textContent = text;
    status.className = `status-pill ${className}`;
  }

  function connect() {
    if (socket && (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING)) {
      return;
    }

    setStatus("Connecting", "waiting");
    const nextSocket = new WebSocket(socketUrl());
    socket = nextSocket;

    nextSocket.addEventListener("open", () => {
      if (socket !== nextSocket) {
        return;
      }
      window.clearTimeout(reconnectTimer);
      reconnectTimer = null;
      setStatus("Checking audio", "waiting");
      setControlsEnabled(false);
      send("ping", {});
    });

    nextSocket.addEventListener("close", () => {
      if (socket !== nextSocket) {
        return;
      }
      socket = null;
      audioReady = false;
      activePadIds = new Set();
      setStatus("Offline", "error");
      setControlsEnabled(false);
      updatePadAvailability();
      updateActivePads();
      scheduleReconnect();
    });

    nextSocket.addEventListener("error", () => {
      if (socket !== nextSocket) {
        return;
      }
      setStatus("Reconnecting", "error");
    });

    nextSocket.addEventListener("message", event => {
      if (socket !== nextSocket) {
        return;
      }
      let message = null;
      try {
        message = JSON.parse(event.data);
      } catch {
        return;
      }
      if (message.type === "state_sync") {
        applyRemoteState(message.payload || {});
      } else if (message.type === "playback_state") {
        applyPlaybackState(message.payload || {});
      } else if (message.type === "audio_status") {
        applyAudioStatus(message.payload || {});
      }
    });
  }

  function scheduleReconnect() {
    window.clearTimeout(reconnectTimer);
    reconnectTimer = window.setTimeout(connect, 1500);
  }

  function send(type, payload) {
    if (!socket || socket.readyState !== WebSocket.OPEN) {
      return false;
    }
    try {
      socket.send(JSON.stringify({ type, payload }));
      return true;
    } catch {
      return false;
    }
  }

  function renderPads(pads) {
    if (!pads.length) {
      padElements.clear();
      grid.replaceChildren();
      renderEmptyState();
      return;
    }

    const emptyState = grid.querySelector(".empty-state");
    if (emptyState) {
      emptyState.remove();
    }
    const nextPadIds = new Set();
    pads.forEach(pad => {
      const padId = String(pad.id || "").trim();
      if (!padId || nextPadIds.has(padId)) {
        return;
      }
      nextPadIds.add(padId);
      const label = String(pad.label || pad.id || "Pad");
      let button = padElements.get(padId);
      if (!button) {
        button = createPadButton(padId);
        padElements.set(padId, button);
      }

      button.setAttribute("aria-label", `Play ${label}`);
      const color = normalizeHex(pad.colorHex);
      button.style.setProperty("--pad-color", color);
      button.style.setProperty("--pad-color-dark", shadeColor(color, -24));
      const labelElement = button.querySelector(".pad-label");
      if (labelElement && labelElement.textContent !== label) {
        labelElement.textContent = label;
      }
      updatePadImage(button, pad.imageUrl || "");
      updatePadPlaybackState(button);
      button.disabled = !audioReady;
      grid.appendChild(button);
    });

    for (const [padId, button] of padElements) {
      if (!nextPadIds.has(padId)) {
        imageRequests.delete(button);
        button.remove();
        padElements.delete(padId);
      }
    }
  }

  function createPadButton(padId) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "pad-button";
    button.dataset.padId = padId;
    button.innerHTML = `
      <span class="pad-content">
        <span class="pad-label"></span>
      </span>
    `;
    bindTrigger(button);
    return button;
  }

  function applyRemoteState(state) {
    playbackMode = normalizePlaybackMode(state.playbackMode);
    updatePlaybackModeUI();
    activePadIds = normalizeActivePadIds(state.activePadIds);
    setAudioReady(state.audioReady === true);
    renderPads(Array.isArray(state.pads) ? state.pads : []);
    updateActivePads();
  }

  function applyPlaybackState(state) {
    activePadIds = normalizeActivePadIds(state.activePadIds);
    updateActivePads();
  }

  function applyAudioStatus(state) {
    setAudioReady(state.audioReady === true);
  }

  function normalizeActivePadIds(value) {
    if (!Array.isArray(value)) {
      return new Set();
    }
    return new Set(value.map(String).filter(Boolean));
  }

  function setAudioReady(ready) {
    audioReady = Boolean(ready);
    if (socket && socket.readyState === WebSocket.OPEN) {
      setStatus(audioReady ? "Ready" : "Audio unavailable", audioReady ? "connected" : "error");
    }
    setControlsEnabled(audioReady);
    updatePadAvailability();
  }

  function updatePadAvailability() {
    for (const button of padElements.values()) {
      button.disabled = !audioReady;
    }
  }

  function updateActivePads() {
    for (const button of padElements.values()) {
      updatePadPlaybackState(button);
    }
  }

  function updatePadPlaybackState(button) {
    const isPlaying = activePadIds.has(button.dataset.padId || "");
    button.classList.toggle("is-playing", isPlaying);
    button.setAttribute("aria-busy", String(isPlaying));
  }

  function renderEmptyState() {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    empty.textContent = "Waiting for the desktop app";
    grid.appendChild(empty);
  }

  function bindTrigger(button) {
    const trigger = event => {
      event.preventDefault();
      const sent = send("trigger_pad", { padId: button.dataset.padId || "" });
      if (!sent) {
        flashOffline(button);
        return;
      }

      pulse(button);
      vibrate();
    };

    if (window.PointerEvent) {
      button.addEventListener("pointerdown", event => {
        if (event.pointerType === "mouse" && event.button !== 0) {
          return;
        }
        trigger(event);
      });
      button.addEventListener("click", event => {
        if (event.detail !== 0) {
          event.preventDefault();
          return;
        }
        trigger(event);
      });
    } else {
      button.addEventListener("click", trigger);
    }
  }

  function setPlaybackMode(mode) {
    const normalized = normalizePlaybackMode(mode);
    const previousMode = playbackMode;
    playbackMode = normalized;
    updatePlaybackModeUI();
    if (!send("set_playback_mode", { mode: normalized })) {
      playbackMode = previousMode;
      updatePlaybackModeUI();
      setStatus("Offline", "error");
    }
  }

  function stopAllSounds() {
    if (!send("stop_all", {})) {
      setStatus("Offline", "error");
      return;
    }
    pulse(stopAllButton);
    vibrate();
  }

  function normalizePlaybackMode(mode) {
    return String(mode || "").toLowerCase() === "replace" ? "replace" : "layer";
  }

  function updatePlaybackModeUI() {
    const isReplace = playbackMode === "replace";
    layerModeButton.classList.toggle("is-active", !isReplace);
    replaceModeButton.classList.toggle("is-active", isReplace);
    layerModeButton.setAttribute("aria-pressed", String(!isReplace));
    replaceModeButton.setAttribute("aria-pressed", String(isReplace));
    modeHint.textContent = isReplace ? "One sound at a time" : "Stack sounds together";
  }

  function setControlsEnabled(enabled) {
    layerModeButton.disabled = !enabled;
    replaceModeButton.disabled = !enabled;
    stopAllButton.disabled = !enabled;
  }

  function updatePadImage(button, imageUrl) {
    const url = String(imageUrl || "");
    if (button.dataset.requestedImageUrl === url) {
      return;
    }
    button.dataset.requestedImageUrl = url;

    if (!url) {
      imageRequests.delete(button);
      clearPadImage(button);
      return;
    }
    startPadImageLoad(button, url, 0);
  }

  function startPadImageLoad(button, url, attempt) {
    const requestToken = {};
    imageRequests.set(button, requestToken);
    const image = new Image();
    image.decoding = "async";

    image.addEventListener("load", async () => {
      try {
        if (typeof image.decode === "function") {
          await image.decode();
        }
      } catch {
        // A completed image can still be used when decode() is unsupported or interrupted.
      }
      if (imageRequests.get(button) !== requestToken || button.dataset.requestedImageUrl !== url) {
        return;
      }
      button.style.setProperty("--pad-image", cssImageUrl(url));
      button.classList.add("has-image");
    }, { once: true });

    image.addEventListener("error", () => {
      if (imageRequests.get(button) !== requestToken || button.dataset.requestedImageUrl !== url) {
        return;
      }
      if (attempt === 0) {
        window.setTimeout(() => {
          if (imageRequests.get(button) === requestToken && button.dataset.requestedImageUrl === url) {
            startPadImageLoad(button, url, 1);
          }
        }, 700);
        return;
      }
      clearPadImage(button);
    }, { once: true });

    image.src = url;
  }

  function clearPadImage(button) {
    button.classList.remove("has-image");
    button.style.removeProperty("--pad-image");
  }

  function pulse(button) {
    button.classList.remove("is-triggered");
    window.requestAnimationFrame(() => {
      button.classList.add("is-triggered");
      window.setTimeout(() => button.classList.remove("is-triggered"), 180);
    });
  }

  function flashOffline(button) {
    button.classList.remove("is-offline");
    window.requestAnimationFrame(() => {
      button.classList.add("is-offline");
      window.setTimeout(() => button.classList.remove("is-offline"), 260);
    });
  }

  function vibrate() {
    if ("vibrate" in navigator) {
      navigator.vibrate(18);
    }
  }

  async function toggleFullscreen() {
    const fullscreenElement = currentFullscreenElement();
    try {
      if (fullscreenElement) {
        await exitFullscreen();
        focusFullscreenFallback = false;
      } else if (canRequestFullscreen(document.documentElement)) {
        await requestFullscreen(document.documentElement);
        focusFullscreenFallback = false;
      } else {
        focusFullscreenFallback = !focusFullscreenFallback;
        document.body.classList.toggle("is-focus-fullscreen", focusFullscreenFallback);
        window.scrollTo({ top: 0, left: 0, behavior: "smooth" });
      }
    } catch {
      focusFullscreenFallback = !focusFullscreenFallback;
      document.body.classList.toggle("is-focus-fullscreen", focusFullscreenFallback);
    }
    updateFullscreenButton();
  }

  function currentFullscreenElement() {
    return document.fullscreenElement || document.webkitFullscreenElement || null;
  }

  function canRequestFullscreen(element) {
    return Boolean(element.requestFullscreen || element.webkitRequestFullscreen);
  }

  function requestFullscreen(element) {
    const request = element.requestFullscreen || element.webkitRequestFullscreen;
    return Promise.resolve(request.call(element));
  }

  function exitFullscreen() {
    const exit = document.exitFullscreen || document.webkitExitFullscreen;
    if (!exit) {
      return Promise.resolve();
    }
    return Promise.resolve(exit.call(document));
  }

  function updateFullscreenButton() {
    const isActive = Boolean(currentFullscreenElement()) || focusFullscreenFallback;
    fullscreenButton.classList.toggle("is-active", isActive);
    fullscreenButton.setAttribute("aria-label", isActive ? "Exit fullscreen" : "Enter fullscreen");
    fullscreenButton.title = isActive ? "Exit fullscreen" : "Fullscreen";
  }

  function shadeColor(hex, amount) {
    const normalized = normalizeHex(hex).replace("#", "");
    const value = Number.parseInt(normalized, 16);
    const r = Math.max(0, Math.min(255, (value >> 16) + amount));
    const g = Math.max(0, Math.min(255, ((value >> 8) & 255) + amount));
    const b = Math.max(0, Math.min(255, (value & 255) + amount));
    return `rgb(${r}, ${g}, ${b})`;
  }

  function normalizeHex(hex) {
    const value = String(hex || "").trim();
    return /^#[0-9a-f]{6}$/i.test(value) ? value : "#3A4A63";
  }

  function cssImageUrl(url) {
    return `url("${encodeURI(url).replaceAll('"', "%22")}")`;
  }

  window.setInterval(() => send("ping", {}), 15000);
  fullscreenButton.addEventListener("click", toggleFullscreen);
  layerModeButton.addEventListener("click", () => setPlaybackMode("layer"));
  replaceModeButton.addEventListener("click", () => setPlaybackMode("replace"));
  stopAllButton.addEventListener("click", stopAllSounds);
  document.addEventListener("fullscreenchange", updateFullscreenButton);
  document.addEventListener("webkitfullscreenchange", updateFullscreenButton);
  setControlsEnabled(false);
  updatePlaybackModeUI();
  updateFullscreenButton();
  connect();
})();
