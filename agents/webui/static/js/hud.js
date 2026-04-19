// hud.js —— 顶部 ctx 用量进度条。
const TOKEN_THRESHOLD_DEFAULT = 100000;

function colorFor(pct) {
  if (pct <= 50) return "var(--bar-low)";
  if (pct <= 80) return "var(--bar-mid)";
  return "var(--bar-high)";
}

function fmt(n) { return n.toLocaleString("en-US"); }

export const hud = {
  update(u) {
    if (!u) return;
    const bar = document.getElementById("hudBar");
    const pctEl = document.getElementById("hudPct");
    const tokEl = document.getElementById("hudTokens");
    const delEl = document.getElementById("hudDelta");
    const total = u.token_threshold || TOKEN_THRESHOLD_DEFAULT;
    const pct = Math.min(100, u.ctx_percent || 0);
    bar.style.width = `${pct}%`;
    bar.style.background = colorFor(pct);
    pctEl.textContent = `${pct.toFixed(1)}%`;
    tokEl.textContent = `${fmt(u.last_total_prompt || 0)} / ${fmt(total)}`;
    const parts = [];
    if (u.last_input_tokens) parts.push(`Δin ${fmt(u.last_input_tokens)}`);
    if (u.last_output_tokens) parts.push(`Δout ${fmt(u.last_output_tokens)}`);
    delEl.textContent = parts.length ? "· " + parts.join(" ") : "";
  },
  reset() {
    this.update({ ctx_percent: 0, last_total_prompt: 0, token_threshold: TOKEN_THRESHOLD_DEFAULT });
    const delEl = document.getElementById("hudDelta");
    if (delEl) delEl.textContent = "";
  },
};
