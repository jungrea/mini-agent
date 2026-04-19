// phase.js —— 顶部阶段条：thinking / tool_running / idle。

const $ = (id) => document.getElementById(id);

let startTs = 0;
let tickTimer = null;
let _onStop = null;   // 停止按钮点击回调

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

function ensureMiniStopBtn() {
  const banner = $("phaseBanner");
  if (!banner) return;
  let btn = banner.querySelector(".mini-stop");
  if (btn) return;
  btn = document.createElement("button");
  btn.className = "mini-stop";
  btn.textContent = "■ 停止";
  btn.title = "停止当前对话";
  btn.addEventListener("click", (ev) => {
    ev.stopPropagation();
    if (_onStop) _onStop();
  });
  banner.appendChild(btn);
}

export const phase = {
  /** 可选：注册"停止"按钮点击回调（由 app.js 接线到 api.cancelSession） */
  bindStop(fn) { _onStop = fn; },

  /**
   * 设置当前阶段。
   * @param {"idle"|"thinking"|"tool_running"|"cancelling"} state
   * @param {string} label
   */
  set(state, label = "") {
    const banner = $("phaseBanner");
    const lbl = $("phaseLabel");
    if (!banner || !lbl) return;

    if (state === "idle") {
      banner.classList.add("is-hidden");
      banner.style.display = "none";
      banner.classList.remove("is-tool", "is-cancelling");
      stopTick();
      return;
    }

    banner.classList.remove("is-hidden");
    banner.style.display = "";

    banner.classList.toggle("is-tool", state === "tool_running");
    banner.classList.toggle("is-cancelling", state === "cancelling");

    if (state === "cancelling") {
      lbl.textContent = label || "正在停止…（等 LLM 当前调用返回）";
    } else {
      lbl.textContent = label || (state === "thinking" ? "LLM 思考中…" : "执行工具中…");
    }

    if (!startTs) startTs = Date.now();
    $("phaseElapsed").textContent = "0.0s";
    ensureTick();
    // 确保 mini stop 按钮存在
    ensureMiniStopBtn();
  },

  reset() { this.set("idle"); },
};
