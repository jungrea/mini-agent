// permission.js —— 权限 ask 模态框。
//
// 回推决策的双通道：
//   1. REST（可靠兜底）：POST /api/sessions/{sid}/permission/resolve
//   2. WebSocket（低延迟，可选）：已连上时同步发送一次
// 两路都触达后端的 resolve_permission_ask（内部幂等），不会重复决策。
import { ws }  from "./ws.js";
import { api } from "./api.js";

let currentAskId = null;
let timeoutTimer = null;
let timeoutEndTs = 0;
let _getSid = () => null;   // 由 init() 注入，返回当前会话 id

const $ = (id) => document.getElementById(id);

export const permission = {
  /**
   * 初始化按钮/快捷键。
   * @param {() => string | null} getSessionId  外部注入"取当前会话 id"的方法
   */
  init({ getSessionId } = {}) {
    if (typeof getSessionId === "function") _getSid = getSessionId;
    $("permAllow").addEventListener("click",  () => resolve("allow"));
    $("permAlways").addEventListener("click", () => resolve("always"));
    $("permDeny").addEventListener("click",   () => resolve("deny"));
    document.addEventListener("keydown", (ev) => {
      if (!isVisible($("permModal"))) return;
      // IME 合成中不要触发（万一权限弹出时正在别的地方打字）
      if (ev.isComposing || ev.keyCode === 229) return;
      if (ev.key === "Enter") resolve("allow");
      else if (ev.key === "Escape") resolve("deny");
    });
  },

  show({ ask_id, tool_name, tool_input, timeout_sec } = {}) {
    if (!ask_id) {
      console.warn("[permission] show called without ask_id, ignored");
      return;
    }
    // 覆盖式显示：LLM 一轮里可能连续请求多个权限，
    // 新的 ask 直接替换当前弹窗的上下文，避免"老 RESOLVED 事件"关掉"新 ASK 弹窗"。
    currentAskId = ask_id;
    $("permTool").textContent = tool_name || "?";
    try {
      $("permInput").textContent = JSON.stringify(tool_input || {}, null, 2);
    } catch (_) {
      $("permInput").textContent = String(tool_input);
    }
    // 关键：重置按钮为可点击。前一次点击可能刚把按钮 disable 了，
    // 但还没等 REST 返回、新的 ask 就来了——此时必须把按钮恢复，否则新弹窗点不动。
    _resetButtons();
    const el = $("permModal");
    el.classList.remove("is-hidden");
    el.style.display = "";
    el.removeAttribute("hidden");
    timeoutEndTs = Date.now() + (timeout_sec || 120) * 1000;
    tickTimeout();
  },

  /**
   * 关闭弹窗。
   * @param {string} [expectedAskId]  仅在当前弹窗的 ask_id 等于该值时才真正关闭；
   *                                  不传则无条件关闭（用户点按钮的路径）。
   *
   * 为什么要比对 ask_id：
   *   后端 worker 在"前端点允许"后会立刻推进并可能马上发起下一次 permission_ask。
   *   由于 permission_resolved 的 publish 与下一次 permission_ask 的 publish
   *   来自不同线程，两条 SSE 事件到达前端的顺序不保证严格一致。
   *   如果不比对 ask_id，就可能出现"新弹窗刚 show，马上被旧的 resolved 关掉"，
   *   导致 currentAskId 被置空，用户再点按钮也发不出 resolve，worker 超时走 deny。
   */
  hide(expectedAskId) {
    if (expectedAskId && currentAskId && expectedAskId !== currentAskId) {
      // 这是"旧 ask 的迟到 resolved"——忽略，不要关掉当前已经换成的新弹窗
      return;
    }
    const el = $("permModal");
    el.classList.add("is-hidden");
    el.style.display = "none";
    currentAskId = null;
    if (timeoutTimer) { clearInterval(timeoutTimer); timeoutTimer = null; }
  },
};

function isVisible(el) {
  return el && !el.classList.contains("is-hidden") && el.style.display !== "none";
}

/** 让三个按钮恢复可点击。供 show() 与 resolve() 失败/竞态兜底路径共用。 */
function _resetButtons() {
  for (const id of ["permAllow", "permAlways", "permDeny"]) {
    const b = $(id);
    if (b) b.disabled = false;
  }
}

function tickTimeout() {
  if (timeoutTimer) clearInterval(timeoutTimer);
  timeoutTimer = setInterval(() => {
    const left = Math.max(0, Math.round((timeoutEndTs - Date.now()) / 1000));
    $("permTimeout").textContent = `${left}s`;
    if (left <= 0) {
      clearInterval(timeoutTimer);
      timeoutTimer = null;
    }
  }, 500);
}

function resolve(decision) {
  if (!currentAskId) return;
  const askId = currentAskId;
  const sid = _getSid();

  if (!sid) {
    console.error("[permission] no current session id; cannot resolve");
    alert("会话 id 丢失，无法回传权限决策。请刷新页面后重试。");
    return;   // 故意不 hide：让用户看到弹窗仍在
  }

  // 防重复：点击后先禁用按钮，避免多次重复点击
  for (const id of ["permAllow", "permAlways", "permDeny"]) {
    const b = $(id);
    if (b) b.disabled = true;
  }

  // 1) REST 主通道（必须成功；失败就保留弹窗让用户再点一次）
  api.resolvePermission(sid, askId, decision)
    .then((res) => {
      console.log("[permission] REST resolve ok:", res);
      // 2) WS 双发做冗余（已连上才发；后端幂等，重复无害）
      try { ws.resolvePermission(askId, decision); } catch (_) {}
      // hide() 里会比对 ask_id：
      //   * 相同   → 关闭弹窗
      //   * 不同   → 说明 REST 在途时已弹出新 ask，弹窗不关、但本次点击也不影响新 ask
      //             此时必须恢复按钮，否则用户看到的新弹窗按钮是灰的！
      permission.hide(askId);
      if (currentAskId && currentAskId !== askId) {
        _resetButtons();
      }
    })
    .catch((err) => {
      console.error("[permission] REST resolve FAILED:", err);
      // 给用户明确反馈，不关弹窗，恢复按钮
      _resetButtons();
      alert(
        "权限决策回传失败：" + (err && err.message ? err.message : err) +
        "\n请再点一次按钮重试。若反复失败，请检查后端服务是否运行/刷新页面。"
      );
    });
}
