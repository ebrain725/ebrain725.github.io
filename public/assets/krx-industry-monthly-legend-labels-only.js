"use strict";

(() => {
  function removeLegendValues() {
    const legend = document.getElementById("industryCumulativeLegend");
    if (!legend) return;

    legend.querySelectorAll(".industry-cumulative-legend-item em").forEach((value) => value.remove());
    legend.querySelectorAll(".industry-cumulative-legend-item").forEach((item) => {
      item.style.gridTemplateColumns = "22px auto";
    });
  }

  function init() {
    const legend = document.getElementById("industryCumulativeLegend");
    if (!legend) return;

    removeLegendValues();
    const observer = new MutationObserver(removeLegendValues);
    observer.observe(legend, { childList: true, subtree: true });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init, { once: true });
  } else {
    init();
  }
})();
