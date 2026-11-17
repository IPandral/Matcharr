(() => {
  "use strict";
  const page = document.querySelector(".room-page");
  if (!page || !window.MatcharrAPI) return;
  const roomId = page.dataset.roomId;
  const lobbyId = page.dataset.lobbyId;
  const sessionId = page.dataset.sessionId;
  const pollInterval = Number(page.dataset.pollInterval);
  const statusUrl = `/api/rooms/${roomId}/status` +
    (sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : "");
  let searchTimer = null;

  function escapeText(value) {
    const element = document.createElement("span");
    element.textContent = value ?? "";
    return element.innerHTML;
  }

  function renderLobby(status) {
    if (!lobbyId || status.lobby?.id !== lobbyId) {
      if (lobbyId || status.lobby) window.location.reload();
      return;
    }
    const members = status.lobby_members || [];
    const ready = members.filter((member) => member.is_ready).length;
    const readyCount = document.getElementById("lobby-ready-count");
    if (readyCount) readyCount.textContent = `${ready}/${members.length} ready`;
    const list = document.getElementById("lobby-member-list");
    if (list) {
      list.innerHTML = members.map((member) => `
        <li><span class="avatar">${escapeText(member.display_name.slice(0, 1).toUpperCase())}</span>
        <div><strong>${escapeText(member.display_name)}</strong><small>${member.is_ready ? "Ready" : "Adding suggestions"}</small></div></li>
      `).join("");
    }
    const launch = document.getElementById("launch-lobby-button");
    if (launch) launch.disabled = members.length === 0 || ready !== members.length;
    const suggestions = document.getElementById("lobby-suggestions");
    if (suggestions) {
      suggestions.innerHTML = (status.suggestions || []).length
        ? status.suggestions.map((movie) => `<li><strong>${escapeText(movie.title)}</strong> <span>${escapeText(movie.release_year || "")}</span></li>`).join("")
        : '<li class="is-muted">No bonus movies yet.</li>';
    }
  }

  function renderStatus(status) {
    if (status.tracked_session?.status === "completed" ||
        (sessionId && status.session?.id === sessionId && status.progress?.session_status === "completed")) {
      statusWatch.stop();
      window.location.replace(`/rooms/${roomId}/matches?session_id=${encodeURIComponent(sessionId)}`);
      return;
    }
    if (status.session?.id !== sessionId) {
      statusWatch.stop();
      if (status.session && status.progress?.session_status === "active") {
        window.location.assign(`/rooms/${roomId}/swipe?session_id=${encodeURIComponent(status.session.id)}`);
      } else {
        window.location.reload();
      }
      return;
    }
    renderLobby(status);
  }

  const readyButton = document.getElementById("lobby-ready-button");
  readyButton?.addEventListener("click", async () => {
    readyButton.disabled = true;
    try {
      await window.MatcharrAPI.post(
        `/api/rooms/${roomId}/lobbies/${lobbyId}/ready`,
        {}
      );
      readyButton.textContent = "Ready";
      statusWatch.refresh();
    } catch (error) {
      readyButton.disabled = false;
      readyButton.textContent = error.message;
    }
  });

  const search = document.getElementById("suggestion-search");
  const results = document.getElementById("suggestion-results");
  search?.addEventListener("input", () => {
    window.clearTimeout(searchTimer);
    const query = search.value.trim();
    if (query.length < 2) {
      results.innerHTML = "";
      return;
    }
    searchTimer = window.setTimeout(async () => {
      try {
        const payload = await window.MatcharrAPI.get(
          `/api/rooms/${roomId}/lobbies/${lobbyId}/movie-search?q=${encodeURIComponent(query)}`
        );
        results.innerHTML = payload.movies.map((movie) => `
          <button type="button" data-movie-id="${escapeText(movie.id)}">
            <strong>${escapeText(movie.title)}</strong><span>${escapeText(movie.release_year || "")}</span>
          </button>
        `).join("") || "<p>No matching movies.</p>";
      } catch (error) {
        results.innerHTML = `<p>${escapeText(error.message)}</p>`;
      }
    }, 250);
  });

  results?.addEventListener("click", async (event) => {
    const button = event.target.closest("[data-movie-id]");
    if (!button) return;
    button.disabled = true;
    try {
      await window.MatcharrAPI.post(
        `/api/rooms/${roomId}/lobbies/${lobbyId}/suggestions`,
        { movie_id: button.dataset.movieId }
      );
      search.value = "";
      results.innerHTML = "";
      readyButton.disabled = false;
      readyButton.textContent = "I’m ready";
      statusWatch.refresh();
    } catch (error) {
      button.disabled = false;
      button.textContent = error.message;
    }
  });

  const copyButton = document.getElementById("copy-quick-join");
  const quickJoinInput = document.getElementById("quick-join-url");
  copyButton?.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(quickJoinInput.value);
      copyButton.textContent = "Copied";
    } catch (_) {
      quickJoinInput.select();
      document.execCommand("copy");
      copyButton.textContent = "Copied";
    }
    window.setTimeout(() => { copyButton.textContent = "Copy link"; }, 1800);
  });

  const statusWatch = window.MatcharrAPI.watch(statusUrl, pollInterval, renderStatus);
})();
