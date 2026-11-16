(() => {
  "use strict";
  const app = document.getElementById("swipe-app");
  if (!app || !window.MatcharrAPI || !window.MatcharrSwipeLogic) return;
  const { decisionForKey, gestureDecision, createIdempotencyKey } = window.MatcharrSwipeLogic;

  const roomId = app.dataset.roomId;
  const sessionId = app.dataset.sessionId;
  const distanceThreshold = Number(app.dataset.distanceThreshold || 100);
  const velocityThreshold = Number(app.dataset.velocityThreshold || 0.5);
  const pollInterval = Number(app.dataset.pollInterval || 4000);
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
  const seenMatches = new Set(JSON.parse(app.dataset.initialMatchIds || "[]"));
  const matchQueue = [];
  let currentMatchNotification = null;
  let sessionCompleted = false;

  function resultsUrl() {
    return `/rooms/${roomId}/matches?session_id=${encodeURIComponent(sessionId)}`;
  }

  function finishSessionFlow() {
    sessionCompleted = true;
    setEnabled(false);
    if (currentMatchNotification) {
      matchContinueButton.textContent = "See all matches";
      matchStopButton.disabled = true;
      matchStopButton.textContent = "Session complete";
    }
    if (!currentMatchNotification && matchQueue.length === 0) {
      window.location.assign(resultsUrl());
    }
  }

  function showNextMatchNotification() {
    if (currentMatchNotification || matchQueue.length === 0) {
      if (sessionCompleted && !currentMatchNotification) window.location.assign(resultsUrl());
      return;
    }
    currentMatchNotification = matchQueue.shift();
    const match = currentMatchNotification.match;
    matchTitle.textContent = match.title;
    matchYear.textContent = match.release_year || "";
    matchPoster.src = match.poster_url;
    matchPoster.alt = `Poster for ${match.title}`;
    matchContinueButton.textContent = sessionCompleted ? "See all matches" : "Continue swiping";
    matchStopButton.disabled = sessionCompleted;
    matchStopButton.textContent = sessionCompleted ? "Session complete" : "Vote to stop";
    matchDialog.showModal();
    announce(`A match has been found: ${match.title}`);
  }

  function queueMatchNotification(match, advanceAfter) {
    if (seenMatches.has(match.id)) {
      if (currentMatchNotification?.match.id === match.id) {
        currentMatchNotification.advanceAfter ||= advanceAfter;
      } else {
        const queued = matchQueue.find((item) => item.match.id === match.id);
        if (queued) queued.advanceAfter ||= advanceAfter;
      }
      return;
    }
    seenMatches.add(match.id);
    matchQueue.push({ match, advanceAfter });
    showNextMatchNotification();
  }

  function dismissMatchNotification(advance = true) {
    const notification = currentMatchNotification;
    currentMatchNotification = null;
    matchDialog.close();
    if (matchQueue.length) {
      showNextMatchNotification();
    } else if (sessionCompleted) {
      window.location.assign(resultsUrl());
    } else if (advance && notification?.advanceAfter) {
      currentMovie = null;
      loadNext();
    }
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
    }
    undoButton.disabled = progress.decided < 1 || progress.session_status !== "active";
    if (progress.members) {
      memberList.innerHTML = progress.members
        .map((member) => `<li><span class="avatar">${escapeText(member.display_name.slice(0, 1).toUpperCase())}</span><span><strong>${escapeText(member.display_name)}</strong><small>${member.decided}/${member.total}</small></span></li>`)
        .join("");
    }
  }

  async function loadNext() {
    setEnabled(false);
    announce("Loading next movie");
    stage.innerHTML = '<div class="loading-card" role="status">Loading your next movie…</div>';
    try {
      const payload = await window.MatcharrAPI.get(
        `/api/rooms/${roomId}/sessions/${sessionId}/next-movie`
      );
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
    if (locked || !currentMovie || !card) return;
    setEnabled(false);
    const movieAtSubmission = currentMovie;
    card.classList.add(direction > 0 ? "fly-right" : "fly-left");
    announce(decision === "like" ? "Movie liked" : "Movie skipped");
    try {
      const idempotencyKey = createIdempotencyKey(globalThis.crypto);
      const result = await window.MatcharrAPI.post(
        `/api/rooms/${roomId}/sessions/${sessionId}/swipes`,
        { movie_id: movieAtSubmission.id, decision, idempotency_key: idempotencyKey }
      );
      await new Promise((resolve) => window.setTimeout(resolve, 280));
      if (result.matched && result.match) {
        updateProgress(result.progress);
        queueMatchNotification(result.match, true);
        if (result.session_completed) finishSessionFlow();
        return;
      }
      if (result.session_completed) {
        updateProgress(result.progress);
        finishSessionFlow();
        return;
      }
      currentMovie = null;
      updateProgress(result.progress);
      await loadNext();
    } catch (error) {
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

  async function pollStatus() {
    try {
      const status = await window.MatcharrAPI.get(
        `/api/rooms/${roomId}/sessions/${sessionId}/status`
      );
      updateProgress(status.progress);
      for (const match of status.matches || []) {
        if (!seenMatches.has(match.id)) {
          queueMatchNotification(match, false);
        }
      }
      if (status.session.status !== "active") finishSessionFlow();
    } catch (error) {
      if (error.status === 404) {
        window.location.assign(`/rooms/${roomId}`);
        return;
      }
      // Polling is best-effort; swiping remains available during brief outages.
    }
  }

  async function submitFinishVote() {
    if (finishVoteButton.disabled) return;
    finishVoteButton.disabled = true;
    finishVoteButton.textContent = "Submitting vote…";
    try {
      const result = await window.MatcharrAPI.post(
        `/api/rooms/${roomId}/sessions/${sessionId}/finish-vote`,
        {}
      );
      finishVoteStatus.textContent =
        `${result.finish_votes} of ${result.eligible_members} voted to stop`;
      if (result.completed) {
        announce("Everyone agreed to stop this session");
        finishSessionFlow();
        return true;
      }
      finishVoteButton.textContent = "Stop vote submitted";
      announce("Your vote to stop was recorded");
      return false;
    } catch (error) {
      finishVoteButton.disabled = false;
      finishVoteButton.textContent = "Vote to stop this session";
      announce(error.message);
      return false;
    }
  }

  async function undoLastSwipe() {
    if (undoButton.disabled) return;
    undoButton.disabled = true;
    try {
      const result = await window.MatcharrAPI.post(
        `/api/rooms/${roomId}/sessions/${sessionId}/undo`,
        {}
      );
      currentMovie = null;
      updateProgress(result.progress);
      announce(`Undid your last ${result.undone.decision}`);
      await loadNext();
    } catch (error) {
      announce(error.message);
      undoButton.disabled = false;
    }
  }

  skipButton.addEventListener("click", () => submitDecision("skip", -1));
  likeButton.addEventListener("click", () => submitDecision("like", 1));
  detailsButton.addEventListener("click", openDetails);
  finishVoteButton.addEventListener("click", submitFinishVote);
  undoButton.addEventListener("click", undoLastSwipe);
  matchContinueButton.addEventListener("click", () => dismissMatchNotification(true));
  matchStopButton.addEventListener("click", () => {
    dismissMatchNotification(true);
    submitFinishVote();
  });
  matchDialog.addEventListener("cancel", (event) => event.preventDefault());
  dialogClose.addEventListener("click", () => dialog.close());
  document.addEventListener("keydown", (event) => {
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
  pollStatus();
  window.setInterval(pollStatus, pollInterval);
})();
