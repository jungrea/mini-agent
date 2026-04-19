// phase.js —— 顶部阶段条：thinking / tool_running / idle。

const $ = (id) => document.getElementById(id);

let startTs = 0;
let tickTimer = null;

function ensureTick() {
  if (tickTimer) return;
  tickTimer = setInterval(() => {
    if (!startTs) return;
    const sec = ((Date.now() - startTs) / 1000).toFixed(1);
    const el = $("phaseElapsed");
    if (el) el.textContent = `${sec}s`;
  }, 100);
}

function stopTick() {
  if (tickTimer) { clearInterval(tickTimer); tickTimer = null; }
  startTs = 0;
}

export const phase = {
  /**
   * 设置当前阶段。
   * @param {"idle"|"thinking"|"tool_running"} state
   * @param {string} label
   */
  set(state, label = "") {
    const banner = $("phaseBanner");
    const lbl = $("phaseLabel");
    if (!banner || !lbl) return;

    if (state === "idle") {
      banner.classList.add("is-hidden");
      banner.style.display = "none";
      banner.classList.remove("is-tool");
      stopTick();
      return;
    }

    banner.classList.remove("is-hidden");
    banner.style.display = "";
    if (state === "tool_running") banner.classList.add("is-tool");
    else banner.classList.remove("is-tool");

    lbl.textContent = label || (state === "thinking" ? "LLM 思考中…" : "执行工具中…");
    if (!startTs) startTs = Date.now();
    $("phaseElapsed").textContent = "0.0s";
    ensureTick();
  },

  reset() { this.set("idle"); },
};
