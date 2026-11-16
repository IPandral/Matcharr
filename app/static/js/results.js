(() => {
  "use strict";
  const app = document.getElementById("results-app");
  if (!app || !window.MatcharrAPI) return;
  const roomId = app.dataset.roomId;
  const sessionId = app.dataset.sessionId;

  function render(result) {
    const grid = app.querySelector(".match-grid");
    result.matches.forEach((match, index) => {
      const card = app.querySelector(`[data-match-id="${CSS.escape(match.id)}"]`);
      if (!card) return;
      card.querySelector(".rank-badge").textContent = `#${index + 1}`;
      card.querySelector(".vote-count").textContent = match.final_votes;
      card.querySelector(".vote-label").textContent =
        result.current_vote === match.id ? "Your pick" : "Vote for this";
      grid.appendChild(card);
    });
    const progress = document.getElementById("final-vote-progress");
    if (progress) {
      progress.textContent = result.winner
        ? `Tonight’s winner: ${result.winner.title}`
        : `${result.votes_cast} of ${result.eligible_members} final votes cast.`;
    }
    if (result.winner && !document.getElementById("winner-banner")) window.location.reload();
  }

  app.addEventListener("click", async (event) => {
    const button = event.target.closest(".final-vote-button");
    if (!button) return;
    button.disabled = true;
    try {
      render(await window.MatcharrAPI.post(
        `/api/rooms/${roomId}/sessions/${sessionId}/final-vote`,
        { match_id: button.dataset.matchId }
      ));
    } finally {
      button.disabled = false;
    }
  });

  window.setInterval(async () => {
    try {
      render(await window.MatcharrAPI.get(
        `/api/rooms/${roomId}/sessions/${sessionId}/results`
      ));
    } catch (_) {
      // Results polling is best-effort.
    }
  }, 4000);
})();
