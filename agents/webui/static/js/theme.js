// theme.js —— 暗/亮主题切换。
//
// 原理：
//   * CSS 在 :root 定义暗色变量，在 :root[data-theme="light"] 覆盖为亮色。
//   * 切换就是改 <html data-theme="dark|light">，CSS 变量级联刷新，
//     无需重新加载资源/重绘任何元素。
//   * 首帧防闪烁由 <head> 里的一段内联脚本负责（读 localStorage）。
//   * 本模块仅负责按钮绑定 + 持久化。
const KEY = "mini-agent-theme";

function currentTheme() {
  return document.documentElement.getAttribute("data-theme") === "light"
    ? "light" : "dark";
}

function applyTheme(theme) {
  if (theme === "light") {
    document.documentElement.setAttribute("data-theme", "light");
  } else {
    document.documentElement.removeAttribute("data-theme");
  }
  // 按钮图标跟随：当前是亮色显示 ☀，点一下会变暗；反之亦然
  const btn = document.getElementById("themeToggle");
  if (btn) {
    btn.textContent = theme === "light" ? "☀" : "🌙";
    btn.title = theme === "light" ? "切换到暗色" : "切换到亮色";
  }
  try { localStorage.setItem(KEY, theme); } catch (_) { /* ignore */ }
}

export const theme = {
  init() {
    applyTheme(currentTheme());
    const btn = document.getElementById("themeToggle");
    if (!btn) return;
    btn.addEventListener("click", () => {
      applyTheme(currentTheme() === "light" ? "dark" : "light");
    });
  },
};
