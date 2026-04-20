// permission.js —— 权限 ask 模态框。
import { ws } from "./ws.js";

let currentAskId = null;
let timeoutTimer = null;
let timeoutEndTs = 0;

const $ = (id) => document.getElementById(id);

export const permission = {
  init() {
    $("permAllow").addEventListener("click",  () => resolve("allow"));
    $("permAlways").addEventListener("click", () => resolve("always"));
    $("permDeny").addEventListener("click",   () => resolve("deny"));
    document.addEventListener("keydown", (ev) => {
      if (!isVisible($("permModal"))) return;
      // IME 合成中不要触发（万一权限弹出时正在别的地方打字）
      if (ev.isComposing || ev.keyCode === 229) return;
      if (ev.key === "Enter") resolve("allow");
      else if (ev.key === "Escape") resolve("deny");
    });
  },

  show({ ask_id, tool_name, tool_input, timeout_sec } = {}) {
    if (!ask_id) {
      console.warn("[permission] show called without ask_id, ignored");
      return;
    }
    currentAskId = ask_id;
    $("permTool").textContent = tool_name || "?";
    try {
      $("permInput").textContent = JSON.stringify(tool_input || {}, null, 2);
    } catch (_) {
      $("permInput").textContent = String(tool_input);
    }
    const el = $("permModal");
    el.classList.remove("is-hidden");
    el.style.display = "";
    el.removeAttribute("hidden");
    timeoutEndTs = Date.now() + (timeout_sec || 120) * 1000;
    tickTimeout();
  },

  hide() {
    const el = $("permModal");
    el.classList.add("is-hidden");
    el.style.display = "none";
    currentAskId = null;
    if (timeoutTimer) { clearInterval(timeoutTimer); timeoutTimer = null; }
  },
};

function isVisible(el) {
  return el && !el.classList.contains("is-hidden") && el.style.display !== "none";
}

function tickTimeout() {
  if (timeoutTimer) clearInterval(timeoutTimer);
  timeoutTimer = setInterval(() => {
    const left = Math.max(0, Math.round((timeoutEndTs - Date.now()) / 1000));
    $("permTimeout").textContent = `${left}s`;
    if (left <= 0) {
      clearInterval(timeoutTimer);
      timeoutTimer = null;
    }
  }, 500);
}

function resolve(decision) {
  if (!currentAskId) return;
  ws.resolvePermission(currentAskId, decision);
  permission.hide();
}
