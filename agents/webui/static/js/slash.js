// slash.js —— 斜杠命令补全浮层。
import { api } from "./api.js";

let commands = [];
let filtered = [];
let activeIdx = 0;
let menuVisible = false;
let _onExecute = null;

const menu  = () => document.getElementById("slashMenu");
const input = () => document.getElementById("inputBox");

export async function initSlash({ onExecute }) {
  _onExecute = onExecute;

  // 拉取命令清单（后端 config.SLASH_COMMANDS 的唯一来源）
  try {
    const { commands: cmds } = await api.slashCommands();
    commands = cmds;
  } catch (e) {
    console.warn("slash commands load failed", e);
  }

  const inp = input();
  inp.addEventListener("input", () => {
    const v = inp.value;
    if (!v.startsWith("/") || v.includes("\n")) {
      hideMenu();
      return;
    }
    showMatching(v);
  });

  inp.addEventListener("keydown", (ev) => {
    if (!menuVisible) return;
    if (ev.key === "ArrowDown") {
      ev.preventDefault();
      activeIdx = (activeIdx + 1) % filtered.length;
      syncActiveClass();
    } else if (ev.key === "ArrowUp") {
      ev.preventDefault();
      activeIdx = (activeIdx - 1 + filtered.length) % filtered.length;
      syncActiveClass();
    } else if (ev.key === "Tab") {
      ev.preventDefault();
      const cmd = filtered[activeIdx];
      if (cmd) inp.value = cmd.name + " ";
      hideMenu();
    } else if (ev.key === "Escape") {
      ev.preventDefault();
      hideMenu();
    } else if (ev.key === "Enter" && !ev.shiftKey) {
      // 菜单开着：Enter = 选中高亮项（等价鼠标点击）
      // 带参命令 → 塞到输入框留给你补参数；不带参 → 直接执行
      ev.preventDefault();
      ev.stopPropagation();
      const cmd = filtered[activeIdx];
      if (cmd) {
        selectCommand(cmd);
      } else {
        hideMenu();
      }
    }
  });

  // 点击输入框/菜单外关闭菜单
  document.addEventListener("mousedown", (e) => {
    if (!menuVisible) return;
    if (e.target === inp || menu().contains(e.target)) return;
    hideMenu();
  });
}

function showMatching(value) {
  const head = value.split(" ")[0];
  filtered = commands.filter(c => c.name.startsWith(head));
  if (filtered.length === 0) { hideMenu(); return; }
  activeIdx = 0;
  renderMenu();
  const el = menu();
  el.classList.remove("is-hidden");
  el.style.display = "";
  el.removeAttribute("hidden");
  menuVisible = true;
}

function renderMenu() {
  const el = menu();
  el.innerHTML = "";
  filtered.forEach((c, i) => {
    const div = document.createElement("div");
    div.className = "slash-item" + (i === activeIdx ? " active" : "");
    div.dataset.idx = String(i);
    div.innerHTML = `<span class="cmd">${c.name}</span><span class="desc">${escapeHTML(c.usage)}</span>`;

    // mouseenter 只换 active class，不重建 DOM —— 避免破坏 click/mousedown 的目标
    div.addEventListener("mouseenter", () => {
      activeIdx = i;
      syncActiveClass();
    });

    // 用 mousedown 代替 click：
    //   mousedown 发生在 blur 之前；click 要等 mouseup。如果外层还有
    //   click-outside 的关闭逻辑，click 极易被"吞掉"。
    div.addEventListener("mousedown", (ev) => {
      ev.preventDefault();   // 避免 input 失焦
      ev.stopPropagation();  // 避免触发 document 的关闭监听
      selectCommand(c);
    });

    el.appendChild(div);
  });
}

function syncActiveClass() {
  menu().querySelectorAll(".slash-item").forEach((x, j) => {
    x.classList.toggle("active", j === activeIdx);
  });
}

function selectCommand(c) {
  const inp = input();
  // 命令带参数（usage 里有 "<..."），留在输入框让用户补参数；否则直接执行
  if (c.usage && c.usage.includes("<")) {
    inp.value = c.name + " ";
    hideMenu();
    inp.focus();
    return;
  }
  hideMenu();
  if (_onExecute) _onExecute(c.name);
  inp.value = "";
  inp.focus();
}

function hideMenu() {
  const el = menu();
  el.classList.add("is-hidden");
  el.style.display = "none";
  menuVisible = false;
}

function escapeHTML(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}
