"use strict";
// Field-guide page: inject the shared sprite sheet, play each preview scene when
// it scrolls into view, and let a click replay a scene from frame 0.  The scene
// animations are gated on the `.in-view` class in rules.css, so toggling it off
// then on restarts the timeline.
(async function () {
  const host = document.getElementById("sprite-host");
  try {
    host.innerHTML = await (await fetch("/static/sprites.svg")).text();
  } catch (e) {
    /* sprites are cosmetic; page still reads fine without them */
  }

  // Give every gallery scene a playhead bar so the loop's start is obvious.
  document.querySelectorAll(".scene-wrap").forEach((wrap) => {
    const bar = document.createElement("div");
    bar.className = "scene-progress";
    bar.setAttribute("aria-hidden", "true");
    bar.innerHTML = "<i></i>";
    wrap.appendChild(bar);
  });

  const scenes = document.querySelectorAll(".scene");
  const io = new IntersectionObserver(
    (entries) => {
      for (const e of entries) {
        if (e.isIntersecting) e.target.classList.add("in-view");
      }
    },
    { threshold: 0.3 }
  );
  scenes.forEach((s) => io.observe(s));

  // Tap a preview to replay it from the start.
  document.querySelectorAll(".scene-wrap").forEach((wrap) => {
    wrap.addEventListener("click", () => {
      const s = wrap.querySelector(".scene");
      if (!s) return;
      s.classList.remove("in-view");
      void s.getBoundingClientRect(); // force reflow so the restart takes
      s.classList.add("in-view");
    });
  });
})();
