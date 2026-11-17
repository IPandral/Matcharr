(() => {
  "use strict";
  const app = document.getElementById("swipe-app");
  if (!app || !window.MatcharrAPI || !window.MatcharrSwipeLogic) return;
  const { decisionForKey, gestureDecision, createIdempotencyKey } = window.MatcharrSwipeLogic;

  const roomId = app.dataset.roomId;
  const sessionId = app.dataset.sessionId;
  const distanceThreshold = Number(app.dataset.distanceThreshold || 100);
  const velocityThreshold = Number(app.dataset.velocityThreshold || 0.5);
  const pollInterval = Number(app.dataset.pollInterval);
  const sessionApi = `/api/rooms/${roomId}/sessions/${sessionId}`;
  const stage = document.getElementById("card-stage");
  const skipButton = document.getElementById("skip-button");
  const likeButton = document.getElementById("like-button");
  const detailsButton = document.getElementById("details-button");
  const live = document.getElementById("live-status");
  const dialog = document.getElementById("movie-dialog");
  const dialogContent = document.getElementById("dialog-content");
  const dialogClose = document.getElementById("dialog-close");
  const memberList = document.getElementById("member-progress-list");
  const progressLabel = document.getElementById("progress-label");
  const finishVoteButton = document.getElementById("finish-vote-button");
  const undoButton = document.getElementById("undo-button");
  const finishVoteStatus = document.getElementById("finish-vote-status");
  const matchDialog = document.getElementById("match-notification-dialog");
  const matchPoster = document.getElementById("match-notification-poster");
  const matchTitle = document.getElementById("match-notification-title");
  const matchYear = document.getElementById("match-notification-year");
  const matchContinueButton = document.getElementById("match-continue-button");
  const matchStopButton = document.getElementById("match-stop-button");
  let currentMovie = null;
  let card = null;
  let locked = true;
  let pointer = null;
  let frame = null;
  let latestX = 0;
  const acknowledgmentKey = `matcharr:matches:${app.dataset.membershipId}:${sessionId}`;
  const acknowledgedMatches = new Set();
  try {
    for (const id of JSON.parse(window.sessionStorage.getItem(acknowledgmentKey) || "[]")) {
      acknowledgedMatches.add(id);
    }
  } catch (_) {
    // Match notifications still work when browser storage is unavailable.
  }
  const seenMatches = new Set(acknowledgedMatches);
  const matchQueue = [];
  let currentMatchNotification = null;
  let sessionCompleted = false;
  let undoPending = false;
  let statusWatch;

  function leaveSession(url) {
    if (sessionCompleted) return;
    sessionCompleted = true;
    setEnabled(false);
    finishVoteButton.disabled = true;
    undoButton.disabled = true;
    statusWatch?.stop();
    window.location.replace(url);
  }

  function finishSessionFlow() {
    leaveSession(`/rooms/${roomId}/matches?session_id=${encodeURIComponent(sessionId)}`);
  }

  function showNextMatchNotification() {
    if (sessionCompleted || currentMatchNotification || matchQueue.length === 0) return;
    currentMatchNotification = matchQueue.shift();
    const match = currentMatchNotification;
    matchTitle.textContent = match.title;
    matchYear.textContent = match.release_year || "";
    matchPoster.src = match.poster_url;
    matchPoster.alt = `Poster for ${match.title}`;
    matchStopButton.disabled = finishVoteButton.disabled;
    matchStopButton.textContent = matchStopButton.disabled ? finishVoteButton.textContent : "Vote to stop";
    if (dialog.open) dialog.close();
    matchDialog.showModal();
    announce(`A match has been found: ${match.title}`);
  }

  function queueMatchNotification(match) {
    if (sessionCompleted || seenMatches.has(match.id)) return;
    seenMatches.add(match.id);
    matchQueue.push(match);
    showNextMatchNotification();
  }

  function dismissMatchNotification() {
    const notification = currentMatchNotification;
    if (!notification) return;
    acknowledgedMatches.add(notification.id);
    try {
      window.sessionStorage.setItem(acknowledgmentKey, JSON.stringify([...acknowledgedMatches]));
    } catch (_) {
      // Acknowledgment still applies to the current page.
    }
    currentMatchNotification = null;
    matchDialog.close();
    showNextMatchNotification();
  }

  function announce(message) {
    live.textContent = "";
    window.setTimeout(() => { live.textContent = message; }, 20);
  }

  function escapeText(value) {
    const element = document.createElement("span");
    element.textContent = value ?? "";
    return element.innerHTML;
  }

  function cardMarkup(movie) {
    const genres = movie.genres.slice(0, 3).map(escapeText).join(" · ");
    const poster = movie.poster_url
      ? `<img src="${escapeText(movie.poster_url)}" alt="Poster for ${escapeText(movie.title)}" draggable="false">`
      : `<div class="missing-poster">No poster</div>`;
    return `
      <article class="movie-card" tabindex="0" aria-label="${escapeText(movie.title)}. Swipe right to like or left to skip.">
        ${poster}
        <div class="swipe-stamp like-stamp" aria-hidden="true">LIKE</div>
        <div class="swipe-stamp skip-stamp" aria-hidden="true">SKIP</div>
        <div class="movie-gradient"></div>
        <div class="movie-card-copy">
          <div class="movie-meta"><span>★ ${escapeText(movie.audience_rating ?? "—")}</span><span>${escapeText(movie.duration_minutes ? `${movie.duration_minutes}m` : "Runtime unknown")}</span><span>${escapeText(movie.content_rating || "Unrated")}</span></div>
          <h1>${escapeText(movie.title)}</h1>
          <p>${escapeText(movie.release_year || "")} ${genres ? `· ${genres}` : ""}</p>
          <p class="movie-summary">${escapeText(movie.summary || "No summary available.")}</p>
        </div>
      </article>`;
  }

  function setEnabled(enabled) {
    locked = !enabled;
    skipButton.disabled = !enabled;
    likeButton.disabled = !enabled;
    detailsButton.disabled = !enabled;
  }

  function updateProgress(progress) {
    if (!progress) return;
    const current = Math.min(progress.decided + (currentMovie ? 1 : 0), progress.total);
    progressLabel.textContent = progress.remaining > 0
      ? `Movie ${current} of ${progress.total}`
      : `${progress.total} of ${progress.total} decided`;
    finishVoteStatus.textContent =
      `${progress.finish_votes} of ${progress.eligible_members} voted to stop`;
    if (progress.current_participant_voted) {
      finishVoteButton.disabled = true;
      finishVoteButton.textContent = "Stop vote submitted";
      matchStopButton.disabled = true;
      matchStopButton.textContent = "Stop vote submitted";
    }
    undoButton.disabled = undoPending || progress.decided < 1 || progress.session_status !== "active";
    if (progress.members) {
      memberList.innerHTML = progress.members
        .map((member) => `<li><span class="avatar">${escapeText(member.display_name.slice(0, 1).toUpperCase())}</span><span><strong>${escapeText(member.display_name)}</strong><small>${member.decided}/${member.total}</small></span></li>`)
        .join("");
    }
  }

  async function loadNext() {
    if (sessionCompleted) return;
    setEnabled(false);
    announce("Loading next movie");
    stage.innerHTML = '<div class="loading-card" role="status">Loading your next movie…</div>';
    try {
      const payload = await window.MatcharrAPI.get(
        `${sessionApi}/next-movie`
      );
      if (sessionCompleted) return;
      if (payload.progress.session_status === "completed") {
        finishSessionFlow();
        return;
      }
      currentMovie = payload.movie;
      updateProgress(payload.progress);
      if (!currentMovie) {
        stage.innerHTML = `<div class="finished-card"><span>✓</span><h1>Pool complete</h1><p>You decided on all ${payload.progress?.total ?? ""} movies in this session. Wait for the group or vote to stop.</p></div>`;
        announce("You reached the end of this session's movie pool");
        return;
      }
      stage.innerHTML = cardMarkup(currentMovie);
      card = stage.querySelector(".movie-card");
      bindPointerEvents();
      setEnabled(true);
      announce(`${currentMovie.title} ready`);
    } catch (error) {
      if (sessionCompleted) return;
      stage.innerHTML = `<div class="finished-card"><h1>Couldn’t load a movie</h1><p>${escapeText(error.message)}</p><button class="button button-secondary" id="retry-movie" type="button">Try again</button></div>`;
      document.getElementById("retry-movie")?.addEventListener("click", loadNext);
      announce("Could not load the next movie");
    }
  }

  function paint(x) {
    if (!card) return;
    card.style.transform = `translate3d(${x}px, 0, 0) rotate(${x / 24}deg)`;
    const strength = Math.min(Math.abs(x) / distanceThreshold, 1);
    card.querySelector(".like-stamp").style.opacity = x > 0 ? strength : 0;
    card.querySelector(".skip-stamp").style.opacity = x < 0 ? strength : 0;
  }

  function requestPaint(x) {
    latestX = x;
    if (frame) return;
    frame = requestAnimationFrame(() => {
      paint(latestX);
      frame = null;
    });
  }

  function bindPointerEvents() {
    card.addEventListener("pointerdown", (event) => {
      if (locked || event.button > 0) return;
      pointer = { id: event.pointerId, startX: event.clientX, startY: event.clientY, startTime: performance.now(), dragging: false };
      card.setPointerCapture(event.pointerId);
      card.classList.add("is-dragging");
    });
    card.addEventListener("pointermove", (event) => {
      if (!pointer || event.pointerId !== pointer.id || locked) return;
      const dx = event.clientX - pointer.startX;
      const dy = event.clientY - pointer.startY;
      if (!pointer.dragging && Math.abs(dx) > 8 && Math.abs(dx) > Math.abs(dy)) pointer.dragging = true;
      if (pointer.dragging) {
        event.preventDefault();
        requestPaint(dx);
      }
    });
    card.addEventListener("pointerup", finishPointer);
    card.addEventListener("pointercancel", cancelPointer);
  }

  function finishPointer(event) {
    if (!pointer || event.pointerId !== pointer.id || locked) return;
    const dx = event.clientX - pointer.startX;
    const elapsed = Math.max(performance.now() - pointer.startTime, 1);
    const gesture = gestureDecision(dx, elapsed, distanceThreshold, velocityThreshold);
    pointer = null;
    card.classList.remove("is-dragging");
    if (gesture) submitDecision(gesture, gesture === "like" ? 1 : -1);
    else resetCard();
  }

  function cancelPointer() {
    pointer = null;
    if (card) card.classList.remove("is-dragging");
    resetCard();
  }

  function resetCard() {
    if (!card) return;
    card.classList.add("is-resetting");
    paint(0);
    window.setTimeout(() => card?.classList.remove("is-resetting"), 260);
  }

  async function submitDecision(decision, direction) {
    if (locked || undoPending || !currentMovie || !card) return;
    setEnabled(false);
    const movieAtSubmission = currentMovie;
    card.classList.add(direction > 0 ? "fly-right" : "fly-left");
    announce(decision === "like" ? "Movie liked" : "Movie skipped");
    try {
      const idempotencyKey = createIdempotencyKey(globalThis.crypto);
      const result = await window.MatcharrAPI.post(
        `${sessionApi}/swipes`,
        { movie_id: movieAtSubmission.id, decision, idempotency_key: idempotencyKey }
      );
      if (sessionCompleted) return;
      if (result.session_completed || result.progress.session_status === "completed") {
        finishSessionFlow();
        return;
      }
      if (result.match) {
        queueMatchNotification(result.match);
      } else {
        await new Promise((resolve) => window.setTimeout(resolve, 280));
        if (sessionCompleted) return;
      }
      currentMovie = null;
      updateProgress(result.progress);
      await loadNext();
    } catch (error) {
      if (sessionCompleted) return;
      currentMovie = movieAtSubmission;
      card?.classList.remove("fly-right", "fly-left");
      resetCard();
      setEnabled(true);
      announce(error.message);
    }
  }

  function openDetails() {
    if (locked || !currentMovie) return;
    dialogContent.innerHTML = `
      ${currentMovie.background_url ? `<img src="${escapeText(currentMovie.background_url)}" alt="">` : ""}
      <div class="dialog-copy"><p class="eyebrow">Movie details</p><h2>${escapeText(currentMovie.title)}</h2>
      <p>${escapeText(currentMovie.release_year || "")} · ${escapeText(currentMovie.duration_minutes ? `${currentMovie.duration_minutes} minutes` : "Runtime unknown")}</p>
      <p>${escapeText(currentMovie.summary || "No summary is available.")}</p>
      <p><strong>Genres:</strong> ${currentMovie.genres.map(escapeText).join(", ") || "Unknown"}</p>
      <p><strong>Audience:</strong> ${escapeText(currentMovie.audience_rating ?? "Unknown")} · <strong>Critics:</strong> ${escapeText(currentMovie.critic_rating ?? "Unknown")} · <strong>Rating:</strong> ${escapeText(currentMovie.content_rating || "Unrated")}</p></div>`;
    dialog.showModal();
  }

  function renderStatus(status) {
    if (status.session.status === "completed" || status.progress.session_status === "completed") {
      finishSessionFlow();
      return;
    }
    if (status.session.status !== "active" || status.progress.session_status !== "active") {
      leaveSession(`/rooms/${roomId}`);
      return;
    }
    updateProgress(status.progress);
    for (const match of status.matches) queueMatchNotification(match);
  }

  async function submitFinishVote() {
    if (finishVoteButton.disabled) return;
    finishVoteButton.disabled = true;
    finishVoteButton.textContent = "Submitting vote…";
    matchStopButton.disabled = true;
    matchStopButton.textContent = "Submitting vote…";
    try {
      const result = await window.MatcharrAPI.post(
        `${sessionApi}/finish-vote`,
        {}
      );
      if (sessionCompleted) return;
      finishVoteStatus.textContent =
        `${result.finish_votes} of ${result.eligible_members} voted to stop`;
      if (result.completed) {
        announce("Everyone agreed to stop this session");
        finishSessionFlow();
        return;
      }
      finishVoteButton.textContent = "Stop vote submitted";
      matchStopButton.textContent = "Stop vote submitted";
      announce("Your vote to stop was recorded");
    } catch (error) {
      if (sessionCompleted) return;
      finishVoteButton.disabled = false;
      finishVoteButton.textContent = "Vote to stop this session";
      matchStopButton.disabled = false;
      matchStopButton.textContent = "Vote to stop";
      announce(error.message);
      statusWatch.refresh();
    }
  }

  async function undoLastSwipe() {
    if (undoButton.disabled) return;
    undoPending = true;
    undoButton.disabled = true;
    try {
      const result = await window.MatcharrAPI.post(
        `${sessionApi}/undo`,
        {}
      );
      if (sessionCompleted) return;
      undoPending = false;
      currentMovie = null;
      updateProgress(result.progress);
      announce(`Undid your last ${result.undone.decision}`);
      await loadNext();
    } catch (error) {
      undoPending = false;
      if (sessionCompleted) return;
      announce(error.message);
      undoButton.disabled = false;
    }
  }

  skipButton.addEventListener("click", () => submitDecision("skip", -1));
  likeButton.addEventListener("click", () => submitDecision("like", 1));
  detailsButton.addEventListener("click", openDetails);
  finishVoteButton.addEventListener("click", submitFinishVote);
  undoButton.addEventListener("click", undoLastSwipe);
  matchContinueButton.addEventListener("click", dismissMatchNotification);
  matchStopButton.addEventListener("click", () => {
    submitFinishVote();
    dismissMatchNotification();
  });
  matchDialog.addEventListener("cancel", (event) => event.preventDefault());
  dialogClose.addEventListener("click", () => dialog.close());
  document.addEventListener("keydown", (event) => {
    if (matchDialog.open) return;
    const action = decisionForKey(event.key, event.repeat);
    if (dialog.open) {
      if (action === "close") dialog.close();
      return;
    }
    if (!action || locked) return;
    if (action === "skip") {
      event.preventDefault();
      submitDecision("skip", -1);
    } else if (action === "like") {
      event.preventDefault();
      submitDecision("like", 1);
    } else if (action === "details") {
      event.preventDefault();
      openDetails();
    }
  });

  loadNext();
  statusWatch = window.MatcharrAPI.watch(`${sessionApi}/status`, pollInterval, renderStatus, (error) => {
    if (error.status === 403 || error.status === 404) leaveSession(`/rooms/${roomId}`);
  });
})();
