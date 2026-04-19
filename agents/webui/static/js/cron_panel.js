// cron_panel.js —— 左栏下半部的定时任务面板。
import { api } from "./api.js";

const MAX_LOG = 60;

export function makeCronPanel() {
  const listEl = document.getElementById("cronList");
  const logEl  = document.getElementById("cronLog");

  async function refresh() {
    try {
      const { tasks } = await api.listCron();
      listEl.innerHTML = "";
      if (!tasks.length) {
        listEl.innerHTML = `<li style="color:var(--text-2);font-size:12px;padding:6px">
          暂无定时任务（可通过对话让 LLM 调用 cron_create 创建）
        </li>`;
        return;
      }
      for (const t of tasks) {
        const li = document.createElement("li");
        li.className = "cron-item";
        const badges = [];
        badges.push(`<span class="badge-tag">${t.cron_human}</span>`);
        if (t.durable)  badges.push(`<span class="badge-tag durable">durable</span>`);
        if (t.auto_run) badges.push(`<span class="badge-tag auto">auto_run</span>`);
        li.innerHTML = `
          <div class="line1">
            <span class="id-tag">${escapeHTML(t.id)}</span>
            <code style="color:var(--text-2);font-size:11px">${escapeHTML(t.cron)}</code>
            <button class="btn-ghost" title="删除" data-act="del">✕</button>
          </div>
          <div class="preview">${escapeHTML(t.prompt_preview)}</div>
          <div class="badges">${badges.join("")}</div>
        `;
        li.querySelector('[data-act="del"]').addEventListener("click", async (ev) => {
          ev.stopPropagation();
          if (!confirm(`删除定时任务 ${t.id}?`)) return;
          try { await api.deleteCron(t.id); } catch (e) { alert(e.message); }
          refresh();
        });
        listEl.appendChild(li);
      }
    } catch (e) {
      console.warn("cron refresh failed", e);
    }
  }

  function appendLog(text) {
    const li = document.createElement("li");
    const ts = new Date().toLocaleTimeString();
    li.textContent = `[${ts}] ${text}`;
    logEl.prepend(li);
    while (logEl.children.length > MAX_LOG) logEl.removeChild(logEl.lastChild);
  }

  async function fireTest() {
    try {
      await api.fireCronTest("web UI test notification");
      appendLog("手动测试已排队");
    } catch (e) { alert(e.message); }
  }

  document.getElementById("cronRefreshBtn").addEventListener("click", refresh);
  document.getElementById("cronTestBtn").addEventListener("click", fireTest);

  return { refresh, appendLog };
}

function escapeHTML(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}
