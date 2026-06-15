import { api } from "./api.js?v=21";
import { renderMarkdown } from "./markdown.js?v=23";

const WORKSPACES_KEY = "mini-agent-writing-workspaces";
const LEGACY_ROOT_KEY = "mini-agent-writing-root";
const COLLAPSED_KEY = "mini-agent-writing-collapsed";
const LIST_LIMIT = 200;
const MAX_SCAN = 1000;
const PREVIEW_DEBOUNCE_MS = 250;
const AUTO_PREVIEW_MAX_CHARS = 120000;
const AUTO_SAVE_DELAY_MS = 1200;

export function shouldRetryAutoSave(error) {
  const message = String(error?.message || error || "");
  const status = message.match(/^\s*(\d{3})\b/);
  if (!status) return true;
  const code = Number(status[1]);
  return code >= 500;
}

export function shouldScheduleAutoSave(currentRoot, currentPath, pausedPath) {
  return Boolean(currentRoot && currentPath && currentPath !== pausedPath);
}

export function shouldSyncWritingScroll(sourceRole) {
  return sourceRole === "editor";
}

export function shouldReplacePreviewHTML(nextHTML, lastHTML) {
  return nextHTML !== lastHTML;
}

export function clampPreviewScrollTop(scrollTop, scrollHeight, clientHeight) {
  const max = Math.max(0, Number(scrollHeight || 0) - Number(clientHeight || 0));
  return Math.min(Math.max(0, Number(scrollTop || 0)), max);
}

function el(id) { return document.getElementById(id); }

function basename(path) {
  return String(path || "").split("/").filter(Boolean).pop() || path || "";
}

function shortenPath(path) {
  const parts = String(path || "").split("/").filter(Boolean);
  if (parts.length <= 3) return path || "";
  return ".../" + parts.slice(-3).join("/");
}

function normalizeFileName(name) {
  const clean = String(name || "").trim().replace(/[\\/:*?"<>|]/g, "-").replace(/\s+/g, " ");
  if (!clean || clean === "." || clean === "..") throw new Error("文件名不能为空");
  if (!/\.(md|markdown|txt)$/i.test(clean)) return clean + ".md";
  return clean;
}

function loadJSON(key, fallback) {
  try {
    const raw = localStorage.getItem(key);
    return raw ? JSON.parse(raw) : fallback;
  } catch (_) {
    return fallback;
  }
}

export async function initWriting({ openFolderPicker, notify } = {}) {
  const pickRootBtn = el("writingPickRoot");
  const addRootBtn = el("writingAddRoot");
  const newFileBtn = el("writingNewFile");
  const saveBtn = el("writingSave");
  const workspaceListEl = el("writingWorkspaceList");
  const fileNameEl = el("writingFileName");
  const statusEl = el("writingStatus");
  const statsEl = el("writingStats");
  const editor = el("writingEditor");
  const preview = el("writingPreview");

  if (!pickRootBtn || !workspaceListEl || !editor || !preview) return;

  let workspaces = loadWorkspaces();
  let collapsed = new Set(loadJSON(COLLAPSED_KEY, []));
  let activeRoot = workspaces[0]?.path || "";
  let currentRoot = "";
  let currentPath = "";
  let currentName = "未打开文件";
  let modified = false;
  let previewTimer = null;
  let autoSaveTimer = null;
  let autoSavePausedPath = "";
  let lastPreviewHTML = "";
  let isSaving = false;
  let isSyncingScroll = false;
  let isRenderingPreview = false;

  function show(level, title, body) {
    if (notify && typeof notify.show === "function") notify.show({ level, title, body });
    else if (level === "error") alert(`${title}: ${body || ""}`);
  }

  function setStatus(text) {
    statusEl.textContent = text || "";
  }

  function setModified(value) {
    modified = Boolean(value);
    fileNameEl.textContent = currentName + (modified ? " •" : "");
    saveBtn.disabled = !currentPath || !modified || isSaving;
  }

  function cancelAutoSave() {
    if (autoSaveTimer) {
      clearTimeout(autoSaveTimer);
      autoSaveTimer = null;
    }
  }

  function scheduleAutoSave() {
    cancelAutoSave();
    if (!shouldScheduleAutoSave(currentRoot, currentPath, autoSavePausedPath)) {
      if (currentPath) setStatus("未保存 · 自动保存已暂停，请手动保存");
      return;
    }
    setStatus("未保存 · 将自动保存");
    autoSaveTimer = setTimeout(() => {
      autoSaveTimer = null;
      saveFile({ auto: true });
    }, AUTO_SAVE_DELAY_MS);
  }

  function updateStats(text) {
    const lines = text ? text.split(/\r\n|\r|\n/).length : 1;
    statsEl.textContent = `${lines} 行 · ${text.length} 字符`;
  }

  function renderPreviewNow({ force = false } = {}) {
    if (previewTimer) {
      clearTimeout(previewTimer);
      previewTimer = null;
    }
    const text = editor.value || "";
    updateStats(text);
    const previousScrollTop = preview.scrollTop;
    if (!force && text.length > AUTO_PREVIEW_MAX_CHARS) {
      const largeHTML = `<div class="writing-large-preview"><strong>文件较大，已暂停自动预览</strong><p>当前 ${text.length} 字符。继续编辑和保存不受影响。</p></div>`;
      if (!shouldReplacePreviewHTML(largeHTML, lastPreviewHTML)) return;
      isRenderingPreview = true;
      preview.innerHTML = "";
      const box = document.createElement("div");
      box.className = "writing-large-preview";
      box.innerHTML = `<strong>文件较大，已暂停自动预览</strong><p>当前 ${text.length} 字符。继续编辑和保存不受影响。</p>`;
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "btn-primary";
      btn.textContent = "仍然预览一次";
      btn.addEventListener("click", () => renderPreviewNow({ force: true }));
      box.appendChild(btn);
      preview.appendChild(box);
      lastPreviewHTML = largeHTML;
      preview.scrollTop = clampPreviewScrollTop(previousScrollTop, preview.scrollHeight, preview.clientHeight);
      isRenderingPreview = false;
      return;
    }
    const html = renderMarkdown(text, { root: currentRoot, path: currentPath });
    if (!shouldReplacePreviewHTML(html, lastPreviewHTML)) return;
    isRenderingPreview = true;
    preview.innerHTML = html;
    lastPreviewHTML = html;
    preview.scrollTop = clampPreviewScrollTop(previousScrollTop, preview.scrollHeight, preview.clientHeight);
    requestAnimationFrame(() => {
      preview.scrollTop = clampPreviewScrollTop(previousScrollTop, preview.scrollHeight, preview.clientHeight);
      isRenderingPreview = false;
    });
  }

  function updatePreview() {
    if (previewTimer) clearTimeout(previewTimer);
    previewTimer = setTimeout(() => renderPreviewNow(), PREVIEW_DEBOUNCE_MS);
  }

  function clearEditor() {
    cancelAutoSave();
    autoSavePausedPath = "";
    currentRoot = "";
    currentPath = "";
    currentName = "未打开文件";
    editor.value = "";
    fileNameEl.textContent = currentName;
    updatePreview();
    setModified(false);
  }

  function canLeaveCurrent() {
    return !modified || confirm("当前文件尚未保存，确定继续吗？");
  }

  function loadWorkspaces() {
    const raw = loadJSON(WORKSPACES_KEY, null);
    const paths = Array.isArray(raw) ? raw : [];
    const legacy = localStorage.getItem(LEGACY_ROOT_KEY);
    if (legacy && !paths.includes(legacy)) paths.unshift(legacy);
    const unique = [...new Set(paths.filter(p => typeof p === "string" && p.trim()))];
    if (legacy) localStorage.removeItem(LEGACY_ROOT_KEY);
    return unique.map(path => ({ path, files: [], loaded: false, loading: false, truncated: false, error: "" }));
  }

  function persistWorkspaces() {
    localStorage.setItem(WORKSPACES_KEY, JSON.stringify(workspaces.map(w => w.path)));
  }

  function persistCollapsed() {
    localStorage.setItem(COLLAPSED_KEY, JSON.stringify([...collapsed]));
  }

  function getWorkspace(root) {
    return workspaces.find(w => w.path === root) || null;
  }

  function setActiveRoot(root) {
    activeRoot = root || activeRoot || workspaces[0]?.path || "";
  }

  function renderWorkspaces() {
    workspaceListEl.innerHTML = "";
    if (!workspaces.length) {
      workspaceListEl.innerHTML = `<li class="writing-empty">点击「添加写作空间」选择文件夹</li>`;
      newFileBtn.disabled = true;
      return;
    }
    newFileBtn.disabled = false;

    const fragment = document.createDocumentFragment();
    for (const ws of workspaces) {
      const root = ws.path;
      const isCollapsed = collapsed.has(root);
      const li = document.createElement("li");
      li.className = "writing-space" + (root === activeRoot ? " active" : "") + (isCollapsed ? " collapsed" : "");
      li.dataset.root = root;

      const head = document.createElement("div");
      head.className = "writing-space-head-row";
      head.title = root;
      head.innerHTML = `
        <button class="writing-space-caret" type="button" title="展开/折叠">▾</button>
        <span class="writing-space-icon">📁</span>
        <span class="writing-space-name"></span>
        <span class="writing-space-actions">
          <button data-act="new" type="button" title="在此空间新建文件">＋</button>
          <button data-act="refresh" type="button" title="刷新">↻</button>
          <button data-act="remove" type="button" title="移除写作空间">⌫</button>
        </span>
      `;
      head.querySelector(".writing-space-name").textContent = basename(root) || shortenPath(root);
      head.addEventListener("click", (ev) => {
        const act = ev.target.dataset.act;
        if (act) return;
        toggleWorkspace(root);
      });
      head.querySelector(".writing-space-caret").addEventListener("click", (ev) => {
        ev.stopPropagation();
        toggleWorkspace(root);
      });
      head.querySelector('[data-act="new"]').addEventListener("click", (ev) => {
        ev.stopPropagation();
        createFile(root);
      });
      head.querySelector('[data-act="refresh"]').addEventListener("click", async (ev) => {
        ev.stopPropagation();
        setActiveRoot(root);
        collapsed.delete(root);
        await refreshWorkspace(root, { force: true });
      });
      head.querySelector('[data-act="remove"]').addEventListener("click", (ev) => {
        ev.stopPropagation();
        removeWorkspace(root);
      });
      li.appendChild(head);

      if (!isCollapsed) {
        const files = document.createElement("ul");
        files.className = "writing-file-list";
        renderFiles(ws, files);
        li.appendChild(files);
      }
      fragment.appendChild(li);
    }
    workspaceListEl.appendChild(fragment);
  }

  function renderFiles(ws, filesEl) {
    if (ws.loading) {
      filesEl.innerHTML = `<li class="writing-empty">加载中…</li>`;
      return;
    }
    if (ws.error) {
      filesEl.innerHTML = `<li class="writing-empty">加载失败：${escapeHTML(ws.error)}</li>`;
      return;
    }
    if (!ws.loaded) {
      filesEl.innerHTML = `<li class="writing-empty">展开后加载文件</li>`;
      return;
    }
    if (!ws.files.length) {
      filesEl.innerHTML = `<li class="writing-empty">暂无 Markdown 文件</li>`;
      return;
    }

    const fragment = document.createDocumentFragment();
    for (const item of ws.files) {
      const li = document.createElement("li");
      li.className = "writing-file-item" + (item.path === currentPath ? " active" : "");
      li.title = item.path;
      li.innerHTML = `<span class="writing-file-icon">📄</span><span class="writing-file-title"></span><span class="writing-file-actions"><button data-act="delete" type="button" title="删除文件">⌫</button></span>`;
      li.querySelector(".writing-file-title").textContent = item.name;
      li.addEventListener("click", (ev) => {
        if (ev.target.dataset.act) return;
        openFile(ws.path, item.path);
      });
      li.querySelector('[data-act="delete"]').addEventListener("click", (ev) => {
        ev.stopPropagation();
        deleteFile(ws.path, item.path, item.name);
      });
      fragment.appendChild(li);
    }
    if (ws.truncated) {
      const truncated = document.createElement("li");
      truncated.className = "writing-empty";
      truncated.textContent = `仅显示前 ${LIST_LIMIT} 个文件`;
      fragment.appendChild(truncated);
    }
    filesEl.appendChild(fragment);
  }

  async function toggleWorkspace(root) {
    setActiveRoot(root);
    if (collapsed.has(root)) {
      collapsed.delete(root);
      persistCollapsed();
      renderWorkspaces();
      await refreshWorkspace(root);
    } else {
      collapsed.add(root);
      persistCollapsed();
      renderWorkspaces();
    }
  }

  async function refreshWorkspace(root, { force = false } = {}) {
    const ws = getWorkspace(root);
    if (!ws || (ws.loaded && !force)) return;
    ws.loading = true;
    ws.error = "";
    renderWorkspaces();
    try {
      const res = await api.writingList(root, LIST_LIMIT, MAX_SCAN);
      ws.path = res.root;
      ws.files = res.files || [];
      ws.truncated = Boolean(res.truncated);
      ws.loaded = true;
      setActiveRoot(ws.path);
      persistWorkspaces();
    } catch (e) {
      ws.error = e.message;
      show("error", "写作空间加载失败", e.message);
    } finally {
      ws.loading = false;
      renderWorkspaces();
    }
  }

  async function openFile(root, path) {
    if (!path || !root) return;
    if (!canLeaveCurrent()) return;
    cancelAutoSave();
    try {
      const res = await api.writingRead(root, path);
      currentRoot = res.root;
      currentPath = res.path;
      autoSavePausedPath = "";
      currentName = res.name || basename(res.path);
      setActiveRoot(currentRoot);
      editor.value = res.content || "";
      fileNameEl.textContent = currentName;
      updatePreview();
      setModified(false);
      setStatus(`已打开 ${currentName}`);
      renderWorkspaces();
    } catch (e) {
      show("error", "打开文件失败", e.message);
    }
  }

  async function addWorkspace() {
    if (!openFolderPicker) return;
    openFolderPicker("", async (picked) => {
      if (!picked) return;
      const exists = workspaces.some(w => w.path === picked);
      if (!exists) {
        workspaces.push({ path: picked, files: [], loaded: false, loading: false, truncated: false, error: "" });
        persistWorkspaces();
      }
      setActiveRoot(picked);
      collapsed.delete(picked);
      persistCollapsed();
      renderWorkspaces();
      await refreshWorkspace(picked, { force: true });
      setStatus(exists ? "已切换写作空间" : "已添加写作空间");
    });
  }

  function removeWorkspace(root) {
    const label = basename(root) || root;
    if (!confirm(`从列表移除写作空间「${label}」？不会删除磁盘文件。`)) return;
    workspaces = workspaces.filter(w => w.path !== root);
    collapsed.delete(root);
    if (activeRoot === root) activeRoot = workspaces[0]?.path || "";
    if (currentRoot === root) clearEditor();
    persistWorkspaces();
    persistCollapsed();
    renderWorkspaces();
    setStatus("已移除写作空间");
  }

  async function createFile(root = activeRoot) {
    if (!root) {
      show("warn", "未选择写作空间", "请先添加一个写作空间");
      return;
    }
    if (!canLeaveCurrent()) return;
    const input = prompt("新文件名", "未命名.md");
    if (input === null) return;
    let name;
    try {
      name = normalizeFileName(input);
    } catch (e) {
      show("error", "文件名无效", e.message);
      return;
    }
    const title = name.replace(/\.(md|markdown|txt)$/i, "");
    try {
      const res = await api.writingCreate(root, name, `# ${title}\n\n`);
      const ws = getWorkspace(root);
      if (ws) ws.loaded = false;
      collapsed.delete(root);
      await refreshWorkspace(root, { force: true });
      await openFile(root, res.path);
      show("info", "已新建文件", name);
    } catch (e) {
      show("error", "新建文件失败", e.message);
    }
  }

  async function saveFile({ auto = false } = {}) {
    if (!currentRoot || !currentPath) {
      if (!auto) show("warn", "未打开文件", "请先新建或打开 Markdown 文件");
      return;
    }
    if (isSaving) return;
    cancelAutoSave();
    isSaving = true;
    saveBtn.disabled = true;
    setStatus(auto ? "自动保存中…" : "保存中…");
    try {
      await api.writingWrite(currentRoot, currentPath, editor.value || "");
      autoSavePausedPath = "";
      setModified(false);
      setStatus(auto ? `已自动保存 ${currentName}` : `已保存 ${currentName}`);
      if (!auto) show("info", "已保存", currentName);
    } catch (e) {
      const retryable = shouldRetryAutoSave(e);
      const retry = auto && modified && retryable;
      if (!retryable) autoSavePausedPath = currentPath;
      setStatus(auto && !retry ? "自动保存失败 · 已暂停自动保存" : (auto ? "自动保存失败" : "保存失败"));
      show("error", auto ? "自动保存失败" : "保存失败", e.message);
      if (retry) scheduleAutoSave();
    } finally {
      isSaving = false;
      setModified(modified);
    }
  }

  async function deleteFile(root, path, name) {
    if (!root || !path) return;
    if (!confirm(`确定删除文件「${name || basename(path)}」？此操作会删除磁盘文件。`)) return;
    try {
      await api.writingDelete(root, path);
      if (currentPath === path) clearEditor();
      const ws = getWorkspace(root);
      if (ws) ws.loaded = false;
      await refreshWorkspace(root, { force: true });
      setStatus(`已删除 ${name || basename(path)}`);
      show("info", "已删除文件", name || basename(path));
    } catch (e) {
      show("error", "删除失败", e.message);
    }
  }

  function syncScroll(source, target, sourceRole) {
    if (isRenderingPreview || !shouldSyncWritingScroll(sourceRole) || isSyncingScroll) return;
    const sourceScrollable = source.scrollHeight - source.clientHeight;
    const targetScrollable = target.scrollHeight - target.clientHeight;
    if (sourceScrollable <= 0 || targetScrollable <= 0) return;
    isSyncingScroll = true;
    target.scrollTop = (source.scrollTop / sourceScrollable) * targetScrollable;
    requestAnimationFrame(() => { isSyncingScroll = false; });
  }

  function escapeHTML(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  pickRootBtn.addEventListener("click", addWorkspace);
  if (addRootBtn) addRootBtn.addEventListener("click", addWorkspace);
  newFileBtn.addEventListener("click", () => createFile(activeRoot));
  saveBtn.addEventListener("click", () => saveFile());
  editor.addEventListener("input", () => {
    updatePreview();
    setModified(true);
    scheduleAutoSave();
  });
  editor.addEventListener("scroll", () => syncScroll(editor, preview, "editor"));
  preview.addEventListener("scroll", () => syncScroll(preview, editor, "preview"));
  editor.addEventListener("keydown", (ev) => {
    if ((ev.metaKey || ev.ctrlKey) && ev.key.toLowerCase() === "s") {
      ev.preventDefault();
      saveFile();
      return;
    }
    if (ev.key === "Tab") {
      ev.preventDefault();
      const start = editor.selectionStart;
      const end = editor.selectionEnd;
      editor.value = editor.value.slice(0, start) + "  " + editor.value.slice(end);
      editor.setSelectionRange(start + 2, start + 2);
      updatePreview();
      setModified(true);
    }
  });

  clearEditor();
  renderWorkspaces();
  setStatus(workspaces.length ? "请选择或展开写作空间" : "请添加写作空间");
}
