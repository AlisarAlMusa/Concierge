// IronFit Gym — site interactivity (mobile nav, year stamp, form, active link).
(function () {
  // Mobile nav toggle
  const toggle = document.querySelector(".nav-toggle");
  const links = document.querySelector(".nav-links");
  if (toggle && links) {
    toggle.addEventListener("click", () => {
      links.classList.toggle("open");
      toggle.setAttribute(
        "aria-expanded",
        links.classList.contains("open") ? "true" : "false"
      );
    });
  }

  // Mark the active nav link based on the current page
  const here = location.pathname.split("/").pop() || "index.html";
  document.querySelectorAll(".nav-links a").forEach((a) => {
    const target = a.getAttribute("href");
    if (target === here || (here === "" && target === "index.html")) {
      a.classList.add("active");
    }
  });

  // Year stamp
  const y = document.querySelector("[data-year]");
  if (y) y.textContent = String(new Date().getFullYear());

  // Contact form — demo only. Real visitor enquiries go through the Concierge widget.
  const form = document.querySelector("form.contact-form");
  if (form) {
    form.addEventListener("submit", (ev) => {
      ev.preventDefault();
      const status = form.querySelector("[data-status]");
      if (status) {
        status.textContent =
          "✓ Thanks! Our team will reply within one business day. For instant answers, try the chat in the bottom-right.";
        status.style.color = "#7ee787";
      }
      form.reset();
    });
  }
})();
