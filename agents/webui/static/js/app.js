// app.js —— 应用入口，串联所有模块。

import { api }            from "./api.js";
import { stream }         from "./stream.js";
import { ws }             from "./ws.js";
import { chat }           from "./chat.js";
import { hud }            from "./hud.js";
import { notify }         from "./notify.js";
import { makeSessions }   from "./sessions.js";
import { makeCronPanel }  from "./cron_panel.js";
import { initSlash }      from "./slash.js";
import { permission }    from "./permission.js";
import { phase }          from "./phase.js";

let currentSessionId = null;
let sessionsUI;
let cronUI;

async function init() {
  // -- 面板 --
  sessionsUI = makeSessions({
    onSelect: switchSession,
    onDelete: async (sid) => { try { await api.deleteSession(sid); } catch (e) { alert(e.message); } },
    onRename: async (sid, title) => { try { await api.patchSession(sid, { title }); } catch (e) { alert(e.message); } },
  });
  cronUI = makeCronPanel();
  await sessionsUI.refresh();
  await cronUI.refresh();

  // -- 输入框 + 发送 --
  const box = document.getElementById("inputBox");
  const btn = document.getElementById("sendBtn");
  btn.addEventListener("click", submitFromInput);
  box.addEventListener("keydown", (ev) => {
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
  permission.init();

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
  const btn = document.getElementById("sendBtn");
  btn.disabled = (state === "running");
  btn.textContent = state === "running" ? "处理中…" : "发送";
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

async function onNewSession() {
  const mode = document.getElementById("modeSelect").value || "default";
  try {
    const meta = await api.createSession("", mode);
    await sessionsUI.refresh();
    await switchSession(meta.id);
  } catch (e) {
    notify.show({ level: "error", title: "新建失败", body: e.message });
  }
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
    // 没会话自动新建一个
    await onNewSession();
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
      permission.hide();
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
