// notify.js —— 右下 toast 通知。
const container = () => document.getElementById("toasts");

let bellCount = 0;
const bellBadge = () => document.getElementById("bellBadge");

export const notify = {
  show({ level = "info", title = "", body = "", duration = 4500 } = {}) {
    const el = document.createElement("div");
    el.className = `toast ${level}`;
    el.innerHTML = `${title ? `<div class="title">${escapeHTML(title)}</div>` : ""}
                    <div class="body">${escapeHTML(body)}</div>`;
    container().appendChild(el);
    setTimeout(() => {
      el.style.transition = "opacity .3s ease";
      el.style.opacity = "0";
      setTimeout(() => el.remove(), 300);
    }, duration);
    bellCount += 1;
    updateBell();
  },

  clearBell() {
    bellCount = 0;
    updateBell();
  },
};

function updateBell() {
  const b = bellBadge();
  if (!b) return;
  if (bellCount > 0) {
    b.classList.remove("is-hidden");
    b.style.display = "";
    b.removeAttribute("hidden");
    b.textContent = String(bellCount);
  } else {
    b.classList.add("is-hidden");
    b.style.display = "none";
  }
}

function escapeHTML(s) {
  return String(s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

document.addEventListener("DOMContentLoaded", () => {
  const bell = document.getElementById("bellBtn");
  if (bell) bell.addEventListener("click", () => notify.clearBell());
});
