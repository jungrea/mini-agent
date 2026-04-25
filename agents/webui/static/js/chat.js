// chat.js —— 对话流渲染。
import { renderMarkdown } from "./markdown.js?v=18";

function escapeHTML(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

function formatInput(obj) {
  if (obj == null) return "";
  try { return JSON.stringify(obj, null, 2); } catch (_) { return String(obj); }
}

/**
 * 从工具入参里抽一条"单行摘要"用于折叠态显示，比如：
 *   bash   -> `cat foo.py`
 *   read   -> `path/to/file.py`
 *   search -> `pattern="foo" path=src`
 * 找不到合适字段就退化为一行 JSON。
 */
function summarizeInput(name, input) {
  if (input == null) return "";
  if (typeof input !== "object") return String(input);
  // 常见工具字段优先级
  const preferKeys = [
    "command", "cmd",
    "file_path", "filePath", "path", "target_file", "target_directory",
    "pattern", "query", "url",
  ];
  for (const k of preferKeys) {
    if (input[k] != null && input[k] !== "") return String(input[k]);
  }
  // 退化：把所有键值挤成一行
  try {
    const one = JSON.stringify(input);
    return one;
  } catch (_) { return String(input); }
}

function truncate(s, max = 140) {
  s = String(s == null ? "" : s);
  s = s.replace(/\s+/g, " ").trim();
  if (s.length <= max) return s;
  return s.slice(0, max - 1) + "…";
}

function scrollToBottom() {
  const box = document.getElementById("messages");
  box.scrollTop = box.scrollHeight;
}

const TYPING_ID = "typingBubble";

export const chat = {
  clear() {
    document.getElementById("messages").innerHTML = "";
  },

  hideEmpty() {
    const e = document.getElementById("emptyHint");
    if (e) e.remove();
  },

  showEmpty(text = "点击左上角「+ 新建」开启一段对话。") {
    const box = document.getElementById("messages");
    box.innerHTML = `<div class="empty-hint" id="emptyHint">${escapeHTML(text)}</div>`;
  },

  renderHistory(history) {
    this.clear();
    if (!history || history.length === 0) { this.showEmpty("这是一个全新的会话，尽管开始吧。"); return; }
    this.hideEmpty();
    for (const msg of history) {
      if (msg.role === "user") {
        if (typeof msg.content === "string") this.addUser(msg.content);
        else if (Array.isArray(msg.content)) {
          for (const b of msg.content) {
            if (b.type === "tool_result") this.addToolResult(b.tool_use_id, b.content);
            else if (b.type === "text") this.addNotice(b.text);
          }
        }
      } else if (msg.role === "assistant") {
        if (typeof msg.content === "string") this.addAssistantText(msg.content);
        else if (Array.isArray(msg.content)) {
          for (const b of msg.content) {
            if (b.type === "text") this.addAssistantText(b.text);
            else if (b.type === "tool_use") this.addToolUse(b.id, b.name, b.input, "done");
          }
        }
      }
    }
    scrollToBottom();
  },

  addUser(text) {
    this.hideEmpty();
    const box = document.getElementById("messages");
    const el = document.createElement("div");
    el.className = "msg user";
    el.innerHTML = `<div class="bubble">${escapeHTML(text)}</div>`;
    box.appendChild(el);
    scrollToBottom();
  },

  addAssistantText(text) {
    if (!text) return;
    // 第一段 assistant 文本出现时，把"思考中"泡泡撤掉
    this.removeTyping();
    this.hideEmpty();
    const box = document.getElementById("messages");
    const el = document.createElement("div");
    el.className = "msg assistant";
    // 渲染 markdown：代码块、标题、列表、粗体、链接等。renderMarkdown
    // 内部先 escape 再做片段替换，不会产生 XSS 风险。
    // user / tool_result / notice 等其它来源故意不走 markdown：
    //   - 用户输入保留所见即所得
    //   - 工具输出保留原始空白/制表/路径
    //   - notice 是系统文案不应被误认为 markdown
    el.innerHTML = `<div class="text md-body">${renderMarkdown(text)}</div>`;
    box.appendChild(el);
    scrollToBottom();
  },

  /**
   * 显示或更新一个 tool_use 卡片。
   * status ∈ "running" | "done" | "error"；不传则保留当前状态。
   * 同一个 id 多次调用会更新已有卡片而不是插新卡片（支持流式）。
   *
   * 折叠策略：
   *  - 默认折叠为单行（工具名 + 参数摘要 + 状态）
   *  - running 状态下自动展开，便于实时感知进度/入参
   *  - 完成/出错后自动重新折叠（若用户没有手动切换过）
   *  - 用户点击过 head 后，dataset.userToggled = "1"，后续状态变化不再自动折叠/展开
   */
  addToolUse(id, name, input, status = "running") {
    // 工具卡片出现时，把"思考中"泡泡撤掉（工具也代表 LLM 已经作出响应）
    this.removeTyping();
    this.hideEmpty();
    const box = document.getElementById("messages");
    let el = id ? box.querySelector(`.msg.tool[data-tool-use-id="${CSS.escape(id)}"]`) : null;
    if (!el) {
      el = document.createElement("div");
      el.className = "msg tool collapsed";
      el.dataset.toolUseId = id || "";
      el.innerHTML = `
        <div class="tool-head">
          <span class="tool-caret" aria-hidden="true">▸</span>
          <span class="tool-icon" aria-hidden="true">🔧</span>
          <span class="tool-name"></span>
          <span class="tool-summary"></span>
          <span class="tool-id"></span>
          <span class="tool-status"></span>
        </div>
        <div class="tool-input"></div>
      `;
      el.querySelector(".tool-head").addEventListener("click", () => {
        el.classList.toggle("collapsed");
        el.dataset.userToggled = "1";
      });
      box.appendChild(el);
    }
    el.querySelector(".tool-name").textContent = name || "?";
    el.querySelector(".tool-id").textContent = id ? `(${id})` : "";
    el.querySelector(".tool-summary").textContent = truncate(summarizeInput(name, input));
    el.querySelector(".tool-input").textContent = formatInput(input);
    this._setToolStatus(el, status);
    scrollToBottom();
    return el;
  },

  /** tool_end：更新已有卡片为完成态 + 耗时；tool_result 事件来再贴结果 */
  markToolEnd(id, { duration_ms } = {}) {
    const box = document.getElementById("messages");
    const el = box.querySelector(`.msg.tool[data-tool-use-id="${CSS.escape(id || "")}"]`);
    if (!el) return;
    this._setToolStatus(el, "done", duration_ms);
  },

  markToolDenied(id, reason) {
    const box = document.getElementById("messages");
    const el = box.querySelector(`.msg.tool[data-tool-use-id="${CSS.escape(id || "")}"]`);
    if (!el) return;
    el.classList.remove("is-running", "is-done");
    el.classList.add("is-error");
    const s = el.querySelector(".tool-status");
    if (s) s.textContent = `✕ 已拒绝${reason ? "：" + reason : ""}`;
  },

  _setToolStatus(el, status, durationMs) {
    el.classList.remove("is-running", "is-done", "is-error");
    const s = el.querySelector(".tool-status");
    if (!s) return;
    if (status === "running") {
      el.classList.add("is-running");
      s.innerHTML = `<span class="spinner-sm"></span><span>运行中</span>`;
    } else if (status === "done") {
      el.classList.add("is-done");
      const txt = durationMs != null ? `✓ ${formatDuration(durationMs)}` : `✓`;
      s.textContent = txt;
    } else if (status === "error") {
      el.classList.add("is-error");
      s.textContent = `✕ 错误`;
    }
    // 自动折叠/展开策略（尊重用户手动切换）
    if (el.dataset.userToggled !== "1") {
      if (status === "running" || status === "error") {
        el.classList.remove("collapsed");
      } else if (status === "done") {
        el.classList.add("collapsed");
      }
    }
  },

  addToolResult(toolUseId, content) {
    const box = document.getElementById("messages");
    const container = box.querySelector(`.msg.tool[data-tool-use-id="${CSS.escape(toolUseId || "")}"]`);
    const txt = typeof content === "string" ? content : JSON.stringify(content, null, 2);
    if (container) {
      // 覆盖已存在的结果块，避免重复
      let res = container.querySelector(".tool-result");
      if (!res) {
        res = document.createElement("div");
        res.className = "tool-result";
        container.appendChild(res);
      }
      res.textContent = txt;
      // 如果卡片还在 running 状态，顺势改成 done
      if (container.classList.contains("is-running")) this._setToolStatus(container, "done");
    } else {
      const el = document.createElement("div");
      el.className = "msg tool is-done collapsed";
      el.innerHTML = `<div class="tool-head">
                        <span class="tool-caret" aria-hidden="true">▸</span>
                        <span class="tool-icon" aria-hidden="true">↩</span>
                        <span class="tool-name">tool_result</span>
                        <span class="tool-summary"></span>
                      </div>
                      <div class="tool-result"></div>`;
      el.querySelector(".tool-result").textContent = txt;
      el.querySelector(".tool-summary").textContent = truncate(txt);
      el.querySelector(".tool-head").addEventListener("click", () => {
        el.classList.toggle("collapsed");
        el.dataset.userToggled = "1";
      });
      box.appendChild(el);
    }
    scrollToBottom();
  },

  addNotice(text, level = "info") {
    if (!text) return;
    this.hideEmpty();
    const box = document.getElementById("messages");
    const el = document.createElement("div");
    el.className = "msg notice";
    el.textContent = text;
    box.appendChild(el);
    scrollToBottom();
  },

  addError(text) {
    this.removeTyping();
    this.hideEmpty();
    const box = document.getElementById("messages");
    const el = document.createElement("div");
    el.className = "msg error";
    el.textContent = text;
    box.appendChild(el);
    scrollToBottom();
  },

  /** 在消息区末尾显示"思考中"气泡（同时只存在一个） */
  showTyping(label = "思考中") {
    this.hideEmpty();
    this.removeTyping();
    const box = document.getElementById("messages");
    const el = document.createElement("div");
    el.className = "msg assistant";
    el.id = TYPING_ID;
    el.innerHTML = `
      <div class="typing-bubble">
        <span>${escapeHTML(label)}</span>
        <span class="dots"><span></span><span></span><span></span></span>
      </div>
    `;
    box.appendChild(el);
    scrollToBottom();
  },

  removeTyping() {
    const el = document.getElementById(TYPING_ID);
    if (el) el.remove();
  },
};

function formatDuration(ms) {
  if (ms == null) return "";
  if (ms < 1000) return `${ms}ms`;
  const s = ms / 1000;
  if (s < 60) return `${s.toFixed(1)}s`;
  const m = Math.floor(s / 60);
  return `${m}m${Math.round(s - m * 60)}s`;
}
