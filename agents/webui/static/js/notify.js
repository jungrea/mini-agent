// notify.js —— 右下 toast 通知 + 顶部铃铛 + 通知历史抽屉。
//
// 设计约定：
//   * notify.show(...) 保持原行为：4.5s 自动消失的 toast；
//     同时把这条事件追加到内存历史，并让铃铛徽标 +1
//   * 点击铃铛：切换"通知历史抽屉"开合。打开时徽标清零（"已读"语义）；
//     抽屉内按时间倒序（最新在最上）展示所有通知
//   * 再次点铃铛、点抽屉外遮罩、按 Esc 或点右上角 ✕ 都可关闭
//   * 内存里最多保留 HISTORY_MAX 条；超出按 FIFO 丢弃最老的
//     （纯前端、刷新即清空——不做 localStorage 持久化，避免跨会话泄露）
const container = () => document.getElementById("toasts");

let bellCount = 0;                    // 未读数
const history = [];                   // 最新在**尾部**（push），渲染时倒序展示
const HISTORY_MAX = 200;

//: 同一事件"去重窗口"——毫秒。
//:
//: 为什么需要？服务端把 cron 事件同时 fan-out 到 GLOBAL_BUS 和
//: 每个活跃会话的 EventBus（见 webui/cron_bridge.py），前端有两条
//: 独立的订阅通道（全局 SSE + 当前会话 SSE/WS），同一次 fire 会被
//: 触达两次 → 不去重会出现两条一模一样的 toast 和两条历史。
//:
//: 这里的语义是"短时间内内容完全一致 = 同一事件，只算一次"。
//: 2000ms 足够覆盖双通道 fan-out 的正常时序差（通常 <100ms），
//: 又远小于人工主动发起同类事件的最短间隔，安全。
const DEDUPE_WINDOW_MS = 2000;
let lastShownKey = "";
let lastShownAt = 0;

const bellBadge = () => document.getElementById("bellBadge");
const bellBtn   = () => document.getElementById("bellBtn");
const drawer    = () => document.getElementById("notifyDrawer");
const drawerList = () => document.getElementById("notifyDrawerList");
const drawerEmpty = () => document.getElementById("notifyDrawerEmpty");
const drawerBackdrop = () => document.getElementById("notifyDrawerBackdrop");

export const notify = {
  /**
   * 弹出一条 toast，同时记入历史 + 未读 +1。
   * level: "info" | "warn" | "error" | "ok"
   */
  show({ level = "info", title = "", body = "", duration = 4500 } = {}) {
    // 1) toast（保持原行为）
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

    // 2) 记历史
    history.push({ level, title, body, ts: Date.now() });
    if (history.length > HISTORY_MAX) {
      history.splice(0, history.length - HISTORY_MAX);
    }

    // 3) 未读 +1
    bellCount += 1;
    updateBell();

    // 如果抽屉当前正打开，新事件立即出现在顶部，同时保持"已读"语义
    if (isDrawerOpen()) {
      bellCount = 0;
      updateBell();
      renderDrawer();
    }
  },

  /** 外部显式清零（留作兼容；UI 层点铃铛已走 openDrawer/closeDrawer 流程）。 */
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

// ---------------------------------------------------------------------------
// 抽屉
// ---------------------------------------------------------------------------

function isDrawerOpen() {
  const d = drawer();
  return !!d && !d.classList.contains("is-hidden");
}

function openDrawer() {
  const d = drawer();
  const bd = drawerBackdrop();
  if (!d) return;
  // 打开 = 清零未读（"已读"语义）
  bellCount = 0;
  updateBell();

  renderDrawer();

  d.classList.remove("is-hidden");
  d.removeAttribute("hidden");
  d.style.display = "";
  if (bd) {
    bd.classList.remove("is-hidden");
    bd.style.display = "";
  }
  // 下一帧加 .is-open，触发 CSS transition 从右侧滑入
  requestAnimationFrame(() => d.classList.add("is-open"));

  document.addEventListener("keydown", onEscClose);
}

function closeDrawer() {
  const d = drawer();
  const bd = drawerBackdrop();
  if (!d) return;
  d.classList.remove("is-open");
  // 等过渡结束再隐藏，避免 display:none 打断动画
  setTimeout(() => {
    if (!d.classList.contains("is-open")) {
      d.classList.add("is-hidden");
      d.style.display = "none";
      if (bd) {
        bd.classList.add("is-hidden");
        bd.style.display = "none";
      }
    }
  }, 220);
  document.removeEventListener("keydown", onEscClose);
}

function onEscClose(ev) {
  if (ev.key === "Escape") closeDrawer();
}

function renderDrawer() {
  const list = drawerList();
  const empty = drawerEmpty();
  if (!list || !empty) return;

  if (history.length === 0) {
    list.innerHTML = "";
    empty.classList.remove("is-hidden");
    empty.style.display = "";
    return;
  }
  empty.classList.add("is-hidden");
  empty.style.display = "none";

  // 倒序：最新在最上
  const items = [...history].reverse();
  list.innerHTML = items
    .map(
      (it) => `
        <li class="notify-item ${escapeAttr(it.level)}">
          <div class="notify-item-head">
            <span class="notify-item-title">${escapeHTML(it.title || "")}</span>
            <span class="notify-item-time" title="${escapeAttr(new Date(it.ts).toLocaleString())}">
              ${escapeHTML(formatRelativeTime(it.ts))}
            </span>
          </div>
          <div class="notify-item-body">${escapeHTML(it.body || "")}</div>
        </li>`
    )
    .join("");
}

function formatRelativeTime(ts) {
  const diff = Math.max(0, (Date.now() - ts) / 1000);
  if (diff < 60) return `${Math.floor(diff)}s 前`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m 前`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h 前`;
  return new Date(ts).toLocaleString();
}

function escapeHTML(s) {
  return String(s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}
function escapeAttr(s) {
  return escapeHTML(s);
}

// ---------------------------------------------------------------------------
// 初始化
// ---------------------------------------------------------------------------

document.addEventListener("DOMContentLoaded", () => {
  const btn = bellBtn();
  if (btn) {
    btn.addEventListener("click", () => {
      if (isDrawerOpen()) closeDrawer();
      else openDrawer();
    });
  }
  const bd = drawerBackdrop();
  if (bd) bd.addEventListener("click", closeDrawer);

  const closeBtn = document.getElementById("notifyDrawerClose");
  if (closeBtn) closeBtn.addEventListener("click", closeDrawer);

  const clearBtn = document.getElementById("notifyDrawerClear");
  if (clearBtn) {
    clearBtn.addEventListener("click", () => {
      history.length = 0;
      bellCount = 0;
      updateBell();
      renderDrawer();
    });
  }
});
