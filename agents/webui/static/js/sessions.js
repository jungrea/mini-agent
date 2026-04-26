// sessions.js —— 左侧会话列表（按工作区分组折叠）。
//
// 视觉结构：
//   ▾ 📁 .../work/foo            (3)
//       新对话 09:34   default · 28 msgs · 09:38
//       新对话 00:28   default · 34 msgs · 09:32
//   ▸ 📁 .../tmp/bar             (5)
//
// 设计要点：
//   * 分组 key = workdir || workdir_default（即每个会话所在的实际工作目录）
//   * 折叠状态持久化到 localStorage，按 key 保存；首次访问默认展开
//   * 包含当前会话的分组始终展开（避免选中却看不到自己）
//   * 只有一个分组时仍渲染分组头（保持视觉一致；用户也能看到工作目录）

import { api } from "./api.js";

const COLLAPSE_KEY = "mini-agent-workdir-collapsed";

function fmtTs(ts) {
  if (!ts) return "";
  const d = new Date(ts * 1000);
  const now = new Date();
  const sameDay = d.toDateString() === now.toDateString();
  return sameDay ? d.toLocaleTimeString().slice(0, 5) :
    `${d.getMonth() + 1}/${d.getDate()}`;
}

function escapeHTML(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

/** 把绝对路径压成"…/last2"形式，与 topbar 的 workdir-tag 保持一致 */
function shortenPath(p) {
  if (!p) return "";
  const parts = String(p).split("/").filter(Boolean);
  if (parts.length <= 2) return p;
  return ".../" + parts.slice(-2).join("/");
}

/* ===== 折叠状态持久化（localStorage 写不进去就静默） ===== */

function loadCollapsed() {
  try {
    const raw = localStorage.getItem(COLLAPSE_KEY);
    if (!raw) return new Set();
    const arr = JSON.parse(raw);
    return new Set(Array.isArray(arr) ? arr : []);
  } catch (_) { return new Set(); }
}

function saveCollapsed(set) {
  try {
    localStorage.setItem(COLLAPSE_KEY, JSON.stringify([...set]));
  } catch (_) { /* 配额满 / 隐私模式：静默 */ }
}

/* ===== 分组 ===== */

function groupByWorkdir(sessions) {
  // Map 保持插入顺序——首个出现该 workdir 的会话决定该分组的"位次"
  const groups = new Map();
  for (const s of sessions) {
    const key = s.workdir || s.workdir_default || "(unknown)";
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(s);
  }
  return groups;
}

export function makeSessions({ onSelect, onDelete, onRename }) {
  const listEl = document.getElementById("sessionList");
  let currentId = null;
  let collapsed = loadCollapsed();

  async function refresh() {
    const { sessions } = await api.listSessions();
    listEl.innerHTML = "";
    if (sessions.length === 0) {
      listEl.innerHTML = `<li style="color:var(--text-2);font-size:12px;padding:8px">
        暂无会话，点击上方「+ 新建」开始
      </li>`;
      return;
    }

    const groups = groupByWorkdir(sessions);
    // 当前会话所在分组——必须保持展开
    let currentGroupKey = null;
    if (currentId) {
      for (const [key, arr] of groups) {
        if (arr.some(s => s.id === currentId)) { currentGroupKey = key; break; }
      }
    }

    for (const [key, arr] of groups) {
      const isCollapsed = collapsed.has(key) && key !== currentGroupKey;

      const groupLi = document.createElement("li");
      groupLi.className = "wd-group" + (isCollapsed ? " collapsed" : "");
      groupLi.dataset.key = key;

      // ---- 分组头 ----
      const head = document.createElement("div");
      head.className = "wd-group-head";
      head.title = key;
      head.innerHTML = `
        <span class="wd-caret" aria-hidden="true">▾</span>
        <span class="wd-icon" aria-hidden="true">
          <svg viewBox="0 0 16 16" fill="currentColor" xmlns="http://www.w3.org/2000/svg">
            <path d="M1.5 3.5A1.5 1.5 0 0 1 3 2h3.086a1.5 1.5 0 0 1 1.06.44l.915.914A1.5 1.5 0 0 0 9.121 3.8H13a1.5 1.5 0 0 1 1.5 1.5V12A1.5 1.5 0 0 1 13 13.5H3A1.5 1.5 0 0 1 1.5 12V3.5Z"/>
          </svg>
        </span>
        <span class="wd-name"></span>
        <span class="wd-count">${arr.length}</span>
      `;
      head.querySelector(".wd-name").textContent = shortenPath(key);
      head.addEventListener("click", () => {
        const nowCollapsed = !groupLi.classList.contains("collapsed");
        groupLi.classList.toggle("collapsed", nowCollapsed);
        if (nowCollapsed) collapsed.add(key); else collapsed.delete(key);
        saveCollapsed(collapsed);
      });
      groupLi.appendChild(head);

      // ---- 该分组的会话项 ----
      const ul = document.createElement("ul");
      ul.className = "wd-group-items";
      for (const s of arr) {
        const itemLi = document.createElement("li");
        itemLi.className = "session-item" + (s.id === currentId ? " active" : "");
        itemLi.innerHTML = `
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
        itemLi.addEventListener("click", (ev) => {
          if (ev.target.dataset.act) return;
          currentId = s.id;
          document.querySelectorAll(".session-item").forEach(x => x.classList.remove("active"));
          itemLi.classList.add("active");
          onSelect(s.id);
        });
        itemLi.querySelector('[data-act="rename"]').addEventListener("click", async (ev) => {
          ev.stopPropagation();
          const newTitle = prompt("新标题：", s.title);
          if (!newTitle) return;
          await onRename(s.id, newTitle);
          refresh();
        });
        itemLi.querySelector('[data-act="delete"]').addEventListener("click", async (ev) => {
          ev.stopPropagation();
          if (!confirm(`确定删除会话「${s.title}」？`)) return;
          await onDelete(s.id);
          if (currentId === s.id) currentId = null;
          refresh();
        });
        ul.appendChild(itemLi);
      }
      groupLi.appendChild(ul);
      listEl.appendChild(groupLi);
    }
  }

  function setCurrent(sid) {
    currentId = sid;
    refresh();
  }

  function getCurrent() { return currentId; }

  return { refresh, setCurrent, getCurrent };
}
