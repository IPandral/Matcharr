(() => {
  document.querySelectorAll("[data-mix-controls]").forEach((controls) => {
    const sliders = [...controls.querySelectorAll('input[type="range"]')];
    const total = controls.querySelector("[data-mix-total]");
    const feed = controls.closest("form")?.querySelector("[data-feed-select]");

    const render = () => {
      sliders.forEach((slider) => {
        const output = slider.closest("label")?.querySelector("[data-mix-output]");
        if (output) output.value = slider.value;
      });
      const sum = sliders.reduce((value, slider) => value + Number(slider.value), 0);
      total.textContent = sum > 0
        ? `${sum} total weight · normalized when the session starts`
        : "Move at least one slider above zero";
      controls.classList.toggle("is-inactive", feed?.value !== "mixed");
    };

    sliders.forEach((slider) => slider.addEventListener("input", render));
    feed?.addEventListener("change", render);
    render();
  });
})();
