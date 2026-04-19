// sessions.js —— 左侧会话列表。
import { api } from "./api.js";

function fmtTs(ts) {
  if (!ts) return "";
  const d = new Date(ts * 1000);
  const now = new Date();
  const sameDay = d.toDateString() === now.toDateString();
  return sameDay ? d.toLocaleTimeString().slice(0, 5) :
    `${d.getMonth() + 1}/${d.getDate()}`;
}

export function makeSessions({ onSelect, onDelete, onRename }) {
  const listEl = document.getElementById("sessionList");
  let currentId = null;

  async function refresh() {
    const { sessions } = await api.listSessions();
    listEl.innerHTML = "";
    if (sessions.length === 0) {
      listEl.innerHTML = `<li style="color:var(--text-2);font-size:12px;padding:8px">
        暂无会话，点击上方「+ 新建」开始
      </li>`;
      return;
    }
    for (const s of sessions) {
      const li = document.createElement("li");
      li.className = "session-item" + (s.id === currentId ? " active" : "");
      li.innerHTML = `
        <div class="session-title">
          <span>${escapeHTML(s.title)}</span>
          <span class="actions">
            <button data-act="rename" title="重命名">✎</button>
            <button data-act="delete" title="删除">✕</button>
          </span>
        </div>
        <div class="session-meta">
          ${escapeHTML(s.mode)} · ${s.message_count} msgs · ${fmtTs(s.updated_at)}
        </div>
      `;
      li.addEventListener("click", (ev) => {
        if (ev.target.dataset.act) return;
        currentId = s.id;
        document.querySelectorAll(".session-item").forEach(x => x.classList.remove("active"));
        li.classList.add("active");
        onSelect(s.id);
      });
      li.querySelector('[data-act="rename"]').addEventListener("click", async (ev) => {
        ev.stopPropagation();
        const newTitle = prompt("新标题：", s.title);
        if (!newTitle) return;
        await onRename(s.id, newTitle);
        refresh();
      });
      li.querySelector('[data-act="delete"]').addEventListener("click", async (ev) => {
        ev.stopPropagation();
        if (!confirm(`确定删除会话「${s.title}」？`)) return;
        await onDelete(s.id);
        if (currentId === s.id) currentId = null;
        refresh();
      });
      listEl.appendChild(li);
    }
  }

  function setCurrent(sid) {
    currentId = sid;
    document.querySelectorAll(".session-item").forEach(x => x.classList.remove("active"));
    refresh();
  }

  function getCurrent() { return currentId; }

  return { refresh, setCurrent, getCurrent };
}

function escapeHTML(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}
