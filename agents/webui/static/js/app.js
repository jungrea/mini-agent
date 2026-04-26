// app.js —— 应用入口，串联所有模块。
//
// 每个子模块的 import URL 都带上同一个 ?v= 版本号，绕过浏览器对 ES module
// 的强缓存。修改任何 js 模块后，把 ?v=17 改成新值并同步更新 index.html 里
// 主入口 app.js?v=17 的值（保持一致），浏览器会重新下载所有模块。

import { api }            from "./api.js?v=17";
import { stream }         from "./stream.js?v=17";
import { ws }             from "./ws.js?v=17";
import { chat }           from "./chat.js?v=18";
import { hud }            from "./hud.js?v=17";
import { notify }         from "./notify.js?v=17";
import { makeSessions }   from "./sessions.js?v=19";
import { initSlash }      from "./slash.js?v=17";
import { permission }     from "./permission.js?v=17";
import { phase }          from "./phase.js?v=17";
import { theme }          from "./theme.js?v=17";

let currentSessionId = null;
let sessionsUI;
// 定时任务侧栏 UI 已移除；保留一个 no-op 占位，让事件分发不必逐处加判空。
// 用户仍可通过斜杠命令 / 对话方式管理 cron；cron 触发时仍走通知中心。
const cronUI = {
  refresh: () => {},
  appendLog: () => {},
};

async function init() {
  // -- 主题切换（先于其它 UI 初始化，避免按钮图标闪烁） --
  theme.init();

  // -- 面板 --
  sessionsUI = makeSessions({
    onSelect: switchSession,
    onDelete: async (sid) => { try { await api.deleteSession(sid); } catch (e) { alert(e.message); } },
    onRename: async (sid, title) => { try { await api.patchSession(sid, { title }); } catch (e) { alert(e.message); } },
  });
  await sessionsUI.refresh();

  // -- 输入框 + 发送 --
  const box = document.getElementById("inputBox");
  const btn = document.getElementById("sendBtn");
  const stopBtn = document.getElementById("stopBtn");
  btn.addEventListener("click", submitFromInput);
  stopBtn.addEventListener("click", requestStop);
  // 阶段条里的 mini 停止按钮也走同一回调
  phase.bindStop(requestStop);

  box.addEventListener("keydown", (ev) => {
    // ---- 输入法合成中：Enter 仅用于选词，不要触发发送 / 停止 ----
    // ev.isComposing 在 IME 弹候选浮层时为 true；部分老浏览器用 keyCode=229
    if (ev.isComposing || ev.keyCode === 229) return;

    // Esc 或 Ctrl+. 停止（任何时候 running 都能用）
    if ((ev.key === "Escape" || (ev.key === "." && ev.ctrlKey)) &&
        document.getElementById("sessionState").textContent.startsWith("running")) {
      ev.preventDefault();
      requestStop();
      return;
    }
    if (ev.key === "Enter" && !ev.shiftKey) {
      // 如果菜单开着，slash.js 里的 Enter 会先处理（执行命令）
      // 到这儿就是普通发送
      if (document.getElementById("slashMenu").classList.contains("is-hidden")) {
        ev.preventDefault();
        submitFromInput();
      }
    }
  });

  // -- 新建 / 模式 / 斜杠 / 权限 --
  document.getElementById("newSessionBtn").addEventListener("click", onNewSession);
  document.getElementById("modeSelect").addEventListener("change", onModeChange);
  await initSlash({
    onExecute: async (line) => {
      if (!currentSessionId) { alert("请先新建或选择会话"); return; }
      try {
        const res = await api.postSlash(currentSessionId, line);
        if (res && res.output) chat.addNotice(`[${line}]\n${res.output}`);
      } catch (e) { alert(e.message); }
    },
  });
  permission.init({ getSessionId: () => currentSessionId });

  // -- 全局事件流 --
  stream.connectGlobal();
  stream.onGlobal(handleGlobalEvent);

  hud.reset();

  // 默认：如果已有会话，选中第一个；否则提示
  const { sessions } = await api.listSessions();
  if (sessions.length > 0) {
    switchSession(sessions[0].id);
  }
}

function setInputState(state) {
  const pill = document.getElementById("sessionState");
  pill.className = "pill " + (state === "running" ? "pill-running" : "pill-idle");
  pill.textContent = state === "running" ? "running · 处理中" : "idle";
  const sendBtn = document.getElementById("sendBtn");
  const stopBtn = document.getElementById("stopBtn");
  if (state === "running") {
    sendBtn.classList.add("is-hidden");
    sendBtn.style.display = "none";
    stopBtn.classList.remove("is-hidden");
    stopBtn.style.display = "";
    stopBtn.disabled = false;
    stopBtn.textContent = "停止";
  } else {
    stopBtn.classList.add("is-hidden");
    stopBtn.style.display = "none";
    sendBtn.classList.remove("is-hidden");
    sendBtn.style.display = "";
    sendBtn.disabled = false;
    sendBtn.textContent = "发送";
  }
}

function setModeUI(mode) {
  document.getElementById("modeSelect").value = mode;
  document.getElementById("currentMode").textContent = mode;
}

async function switchSession(sid) {
  currentSessionId = sid;
  sessionsUI.setCurrent(sid);
  stream.disconnectSession();
  ws.disconnect();
  // 切换会话时清理"跑着的"状态，避免上一个会话的 phase/typing 残留
  phase.reset();
  chat.removeTyping();

  try {
    const detail = await api.getSession(sid);
    document.getElementById("currentTitle").textContent = detail.title;
    setModeUI(detail.mode || "default");
    setWorkdirTag(detail.workdir, detail.workdir_default);
    chat.renderHistory(detail.history);
    if (detail.usage) hud.update(detail.usage);
    setInputState(detail.state || "idle");
    // 如果目标会话恰好在跑一轮（例如多标签同时用），给用户一个提示
    if ((detail.state || "idle") === "running") {
      phase.set("thinking", "后台正在处理该会话…");
    }
  } catch (e) {
    notify.show({ level: "error", title: "加载会话失败", body: e.message });
    return;
  }

  // 建立会话级事件流 & WebSocket
  stream.connectSession(sid);
  stream.onSession(handleSessionEvent);
  ws.connect(sid);
}

// ---------------- 顶部"当前会话工作区"小标签 ----------------

const RECENT_WORKDIR_KEY = "mini-agent-recent-workdirs";
const RECENT_WORKDIR_MAX = 5;

function loadRecentWorkdirs() {
  try {
    const raw = localStorage.getItem(RECENT_WORKDIR_KEY);
    const arr = raw ? JSON.parse(raw) : [];
    return Array.isArray(arr) ? arr.filter(s => typeof s === "string") : [];
  } catch (_) { return []; }
}

function pushRecentWorkdir(path) {
  if (!path) return;
  const list = loadRecentWorkdirs().filter(p => p !== path);
  list.unshift(path);
  try {
    localStorage.setItem(RECENT_WORKDIR_KEY, JSON.stringify(list.slice(0, RECENT_WORKDIR_MAX)));
  } catch (_) { /* 配额满 / 隐私模式：静默 */ }
}

function setWorkdirTag(workdir, workdirDefault) {
  const tag = document.getElementById("currentWorkdir");
  if (!tag) return;
  if (workdir) {
    // 显示尾部更短的形式：~/xxx 或最后两段
    const display = shortenPath(workdir);
    tag.textContent = `📁 ${display}`;
    tag.title = `工作区: ${workdir}\n（默认: ${workdirDefault || "?"}）`;
    tag.classList.remove("is-hidden");
    tag.style.display = "";
    tag.classList.add("is-custom");
  } else {
    // 默认（项目根）不展示标签——视觉更清爽，避免每个会话顶部都挂一个 badge
    tag.classList.add("is-hidden");
    tag.style.display = "none";
    tag.classList.remove("is-custom");
    tag.textContent = "";
    tag.title = "";
  }
}

function shortenPath(p) {
  if (!p) return "";
  // ~ 替换 home 前缀（前端拿不到 $HOME，靠服务端返回的绝对路径做尾部截断）
  const parts = p.split("/").filter(Boolean);
  if (parts.length <= 2) return p;
  return ".../" + parts.slice(-2).join("/");
}

// ---------------- 新建会话弹窗 ----------------

function openNewSessionModal() {
  const modal = document.getElementById("newSessionModal");
  const wd    = document.getElementById("newSessionWorkdir");
  const mode  = document.getElementById("newSessionMode");
  const err   = document.getElementById("newSessionError");
  const recentEl = document.getElementById("newSessionRecent");

  if (!modal) return;
  // 默认填入顶部 modeSelect 当前值（用户手感连贯）
  mode.value = document.getElementById("modeSelect").value || "default";
  wd.value = "";
  err.classList.add("is-hidden"); err.style.display = "none"; err.textContent = "";

  // 渲染"最近用过的工作区"建议列表
  const recents = loadRecentWorkdirs();
  if (recents.length > 0) {
    recentEl.innerHTML = recents.map(p =>
      `<li data-path="${escapeAttr(p)}">${escapeHTML(p)}</li>`
    ).join("");
    recentEl.classList.remove("is-hidden");
    recentEl.style.display = "";
    // 点击项 → 填进输入框
    recentEl.querySelectorAll("li").forEach(li => {
      li.addEventListener("click", () => {
        wd.value = li.dataset.path;
        wd.focus();
      });
    });
  } else {
    recentEl.classList.add("is-hidden");
    recentEl.style.display = "none";
    recentEl.innerHTML = "";
  }

  modal.classList.remove("is-hidden");
  modal.style.display = "";
  setTimeout(() => wd.focus(), 30);

  // 一次性 keydown：Esc 关 / Enter 确认（避免反复绑定泄漏）
  function onKey(ev) {
    if (ev.key === "Escape") { closeNewSessionModal(); cleanup(); }
    else if (ev.key === "Enter" && document.activeElement !== mode) {
      ev.preventDefault();
      submitNewSession();
    }
  }
  function cleanup() { document.removeEventListener("keydown", onKey); }
  document.addEventListener("keydown", onKey);

  document.getElementById("newSessionCancel").onclick = () => { closeNewSessionModal(); cleanup(); };
  document.getElementById("newSessionConfirm").onclick = () => submitNewSession(cleanup);

  // "📂 浏览"按钮：弹二级目录浏览模态框，选中后回填到工作区输入框
  document.getElementById("newSessionBrowse").onclick = () => {
    openFolderPicker(wd.value, (picked) => {
      if (picked) wd.value = picked;
    });
  };
}

function closeNewSessionModal() {
  const modal = document.getElementById("newSessionModal");
  if (!modal) return;
  modal.classList.add("is-hidden");
  modal.style.display = "none";
}

async function submitNewSession(cleanup) {
  const wd   = document.getElementById("newSessionWorkdir").value.trim();
  const mode = document.getElementById("newSessionMode").value || "default";
  const errEl = document.getElementById("newSessionError");
  errEl.classList.add("is-hidden"); errEl.style.display = "none"; errEl.textContent = "";

  try {
    const meta = await api.createSession("", mode, wd || null);
    if (wd) pushRecentWorkdir(wd);
    closeNewSessionModal();
    if (cleanup) cleanup();
    await sessionsUI.refresh();
    await switchSession(meta.id);
  } catch (e) {
    errEl.textContent = e.message;
    errEl.classList.remove("is-hidden");
    errEl.style.display = "";
  }
}

function escapeHTML(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}
function escapeAttr(s) { return escapeHTML(s); }

async function onNewSession() {
  // 用户主动点"+ 新建"——弹弹窗让他选工作区+模式
  openNewSessionModal();
}

async function quickCreateDefaultSession() {
  // 用于"用户已经输入消息但还没会话"等无人值守路径——静默用默认配置创建，
  // 避免弹窗打断用户的输入流。
  const mode = document.getElementById("modeSelect").value || "default";
  try {
    const meta = await api.createSession("", mode, null);
    await sessionsUI.refresh();
    await switchSession(meta.id);
  } catch (e) {
    notify.show({ level: "error", title: "新建失败", body: e.message });
  }
}

// ---------------- 目录浏览模态框（"📂 浏览"按钮） ----------------
//
// 浏览器层面没有"原生 native folder picker that returns absolute path"
// （webkitdirectory 只给文件名、showDirectoryPicker 只给 handle，都拿不到
// 服务端绝对路径）。所以这里的"原生感"是靠后端 /api/fs/list 列服务端
// 目录树 + 前端弹自定义模态框双击穿层级实现的——这是 VS Code / Jupyter
// 等本地化工具的标准做法。
//
// 入口：openFolderPicker(initialPath, onPick)
//   * initialPath 为空 → 后端 /api/fs/home 拿用户家目录作起点
//   * onPick(pathOrNull) → 用户点"使用此目录" 回传字符串；取消则回传 null
//
// UI 状态都局部 closure 持有，不用全局变量，避免和其它地方串。

async function openFolderPicker(initialPath, onPick) {
  const modal      = document.getElementById("folderPickerModal");
  const pathInput  = document.getElementById("folderPickerPath");
  const goBtn      = document.getElementById("folderPickerGo");
  const upBtn      = document.getElementById("folderPickerUp");
  const homeBtn    = document.getElementById("folderPickerHome");
  const showHidden = document.getElementById("folderPickerShowHidden");
  const statusEl   = document.getElementById("folderPickerStatus");
  const listEl     = document.getElementById("folderPickerList");
  const errEl      = document.getElementById("folderPickerError");
  const cancelBtn  = document.getElementById("folderPickerCancel");
  const confirmBtn = document.getElementById("folderPickerConfirm");

  if (!modal) { onPick && onPick(null); return; }

  // 当前正在查看的绝对路径
  let currentPath = "";
  let parentPath = null;

  function setError(msg) {
    if (msg) {
      errEl.textContent = msg;
      errEl.classList.remove("is-hidden");
      errEl.style.display = "";
    } else {
      errEl.classList.add("is-hidden");
      errEl.style.display = "none";
      errEl.textContent = "";
    }
  }

  async function loadDir(path) {
    setError("");
    statusEl.textContent = "加载中…";
    try {
      const res = await api.fsList(path, showHidden.checked);
      currentPath = res.path;
      parentPath  = res.parent;
      pathInput.value = currentPath;
      listEl.innerHTML = "";
      if (res.entries.length === 0) {
        listEl.innerHTML = `<li class="folder-item empty">（此目录下无子目录）</li>`;
      } else {
        for (const e of res.entries) {
          const li = document.createElement("li");
          li.className = "folder-item" + (e.readable ? "" : " disabled");
          li.innerHTML = `<span class="folder-icon">📁</span>
                          <span class="folder-name">${escapeHTML(e.name)}</span>`;
          if (e.readable) {
            li.addEventListener("click", () => loadDir(e.path));
          }
          listEl.appendChild(li);
        }
      }
      statusEl.textContent = res.truncated
        ? `仅显示前 ${res.entries.length} 项（已截断）`
        : `共 ${res.entries.length} 个子目录`;
      upBtn.disabled = !parentPath;
    } catch (e) {
      setError(`加载失败: ${e.message}`);
      statusEl.textContent = "";
    }
  }

  // —— 一次性 keydown：Esc 关闭 / Enter 提交（焦点不在 path 输入框时）
  function onKey(ev) {
    if (ev.key === "Escape") finish(null);
    else if (ev.key === "Enter" && document.activeElement === pathInput) {
      ev.preventDefault();
      loadDir(pathInput.value.trim());
    }
  }

  function finish(picked) {
    document.removeEventListener("keydown", onKey);
    cancelBtn.onclick = null;
    confirmBtn.onclick = null;
    upBtn.onclick = null;
    homeBtn.onclick = null;
    goBtn.onclick = null;
    showHidden.onchange = null;
    modal.classList.add("is-hidden");
    modal.style.display = "none";
    onPick && onPick(picked);
  }

  document.addEventListener("keydown", onKey);
  cancelBtn.onclick   = () => finish(null);
  confirmBtn.onclick  = () => finish(currentPath || null);
  upBtn.onclick       = () => parentPath && loadDir(parentPath);
  goBtn.onclick       = () => loadDir(pathInput.value.trim());
  homeBtn.onclick     = async () => {
    try {
      const r = await api.fsHome();
      loadDir(r.path);
    } catch (e) { setError(e.message); }
  };
  showHidden.onchange = () => loadDir(currentPath);

  // 显示 modal + 加载初始目录
  modal.classList.remove("is-hidden");
  modal.style.display = "";

  // 初始路径：用户传了就用、没传就家目录
  let startPath = (initialPath || "").trim();
  if (!startPath) {
    try {
      const r = await api.fsHome();
      startPath = r.path;
    } catch (_) {
      startPath = "/";  // 极端情况下兜底
    }
  }
  await loadDir(startPath);
}

async function onModeChange() {
  const mode = document.getElementById("modeSelect").value;
  if (!currentSessionId) return;
  try {
    await api.patchSession(currentSessionId, { mode });
    setModeUI(mode);
    notify.show({ level: "info", title: "模式已切换", body: mode });
  } catch (e) {
    notify.show({ level: "error", title: "切换失败", body: e.message });
  }
}

async function submitFromInput() {
  if (!currentSessionId) {
    // 没会话自动新建一个（用默认配置，不弹窗，避免打断用户输入）
    await quickCreateDefaultSession();
    if (!currentSessionId) return;
  }
  const box = document.getElementById("inputBox");
  const text = box.value.trim();
  if (!text) return;
  box.value = "";

  if (text.startsWith("/")) {
    try {
      const res = await api.postSlash(currentSessionId, text);
      if (res && res.output) chat.addNotice(`[${text}]\n${res.output}`);
    } catch (e) {
      notify.show({ level: "error", title: "斜杠命令失败", body: e.message });
    }
    return;
  }

  try {
    await api.postMessage(currentSessionId, text);
    chat.addUser(text);              // 本地立即回显用户消息
    chat.showTyping("正在准备上下文…"); // 在第一个 llm_start 之前先显示等待气泡
    phase.set("thinking", "正在准备上下文…");
    setInputState("running");
  } catch (e) {
    notify.show({ level: "error", title: "发送失败", body: e.message });
  }
}

async function requestStop() {
  if (!currentSessionId) return;
  const stopBtn = document.getElementById("stopBtn");
  if (stopBtn) {
    stopBtn.disabled = true;
    stopBtn.textContent = "停止中…";
  }
  phase.set("cancelling", "正在停止…（等 LLM 当前调用返回）");
  try {
    const res = await api.cancelSession(currentSessionId);
    if (res && res.accepted === false) {
      notify.show({ level: "info", title: "无需停止", body: res.reason || res.state });
      if (stopBtn) { stopBtn.disabled = false; stopBtn.textContent = "停止"; }
    } else {
      notify.show({ level: "warn", title: "已请求停止", body: "LLM 调用返回后将中止" });
    }
  } catch (e) {
    notify.show({ level: "error", title: "停止失败", body: e.message });
    if (stopBtn) { stopBtn.disabled = false; stopBtn.textContent = "停止"; }
  }
}

// ================= 事件处理 =================

function handleSessionEvent(ev) {
  const { type, data } = ev;
  switch (type) {
    case "heartbeat":
      break;
    case "user_message":
      break;
    case "assistant_text":
      chat.addAssistantText(data.text);
      break;
    case "tool_use":
      chat.addToolUse(data.id, data.name, data.input, data.status || "running");
      break;
    case "tool_result":
      chat.addToolResult(data.tool_use_id, data.content);
      break;
    case "usage":
      hud.update(data);
      break;
    case "status":
      setInputState(data.state);
      break;
    case "round_end":
      setInputState("idle");
      phase.reset();
      chat.removeTyping();
      if (data && data.error) chat.addError(`Round error: ${data.error}`);
      if (data && data.cancelled) chat.addNotice("⏹ 本轮对话已停止（由用户请求）", "warn");
      sessionsUI.refresh();
      break;
    case "notice":
      chat.addNotice(data.text, data.level || "info");
      break;

    // ---- 细粒度 LLM / 工具进度 ----
    case "phase":
      if (data.state === "idle") phase.reset();
      else phase.set(data.state, data.label);
      break;
    case "llm_start":
      chat.showTyping("LLM 思考中…");
      phase.set("thinking", "LLM 思考中…");
      break;
    case "llm_end":
      // LLM 回来了，如果接下来要执行工具，phase 会被 tool_start 改写；
      // 如果是纯文本终局，assistant_text 会把 typing 气泡撤掉
      break;
    case "tool_start":
      chat.removeTyping();
      phase.set("tool_running", `执行工具 ${data.name}…`);
      chat.addToolUse(data.id, data.name, data.input, "running");
      break;
    case "tool_end":
      chat.markToolEnd(data.id, { duration_ms: data.duration_ms });
      break;
    case "tool_denied":
      chat.markToolDenied(data.id, data.reason);
      break;

    case "permission_ask":
      permission.show(data);
      notify.show({ level: "warn", title: "权限请求", body: data.tool_name });
      break;
    case "permission_resolved":
      // 只关"对应 ask_id 的弹窗"，避免旧 resolve 事件关掉新弹窗
      // （worker 一轮里可能连续请求多个权限，下一轮的 ASK 可能在上一轮的
      //  RESOLVED 之前到达——此时若无脑 hide() 就会误关新弹窗）
      permission.hide(data && data.ask_id);
      break;
    case "error":
      chat.addError(data.message || "unknown error");
      break;
    case "cron_fire":
      notify.show({ level: "warn", title: "⏰ 定时任务触发",
                    body: `${data.id}: ${String(data.prompt || "").slice(0, 80)}` });
      cronUI.appendLog(`fired ${data.id}: ${String(data.prompt || "").slice(0, 60)}`);
      cronUI.refresh();
      break;
    case "cron_auto_run_start":
      cronUI.appendLog(`auto_run start ${data.id}`);
      break;
    case "cron_auto_run_done":
      cronUI.appendLog(`auto_run done ${data.id}`);
      notify.show({ level: "ok", title: "auto_run 完成",
                    body: data.preview || String(data.result || "").slice(0, 100) });
      break;
    case "cron_auto_run_error":
      cronUI.appendLog(`auto_run ERROR ${data.id}: ${data.error}`);
      notify.show({ level: "error", title: "auto_run 失败", body: data.error || "" });
      break;
    case "session_updated":
      sessionsUI.refresh();
      break;
    default:
      break;
  }
}

function handleGlobalEvent(ev) {
  const { type, data } = ev;
  switch (type) {
    case "cron_fire":
      cronUI.appendLog(`fired ${data.id}: ${String(data.prompt || "").slice(0, 60)}`);
      cronUI.refresh();
      notify.show({ level: "warn", title: "⏰ 定时任务触发",
                    body: `${data.id}: ${String(data.prompt || "").slice(0, 80)}` });
      break;
    case "cron_auto_run_done":
      notify.show({ level: "ok", title: "auto_run 完成",
                    body: data.preview || String(data.result || "").slice(0, 100) });
      cronUI.appendLog(`auto_run done ${data.id}`);
      break;
    case "cron_auto_run_error":
      notify.show({ level: "error", title: "auto_run 失败", body: data.error || "" });
      cronUI.appendLog(`auto_run ERROR ${data.id}`);
      break;
    case "session_created":
    case "session_updated":
    case "session_deleted":
      sessionsUI.refresh();
      break;
    default:
      break;
  }
}

// ============ 启动 ============
init().catch((e) => {
  console.error(e);
  document.getElementById("messages").innerHTML =
    `<div class="msg error">初始化失败：${e.message}</div>`;
});
