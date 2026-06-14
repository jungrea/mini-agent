// api.js —— REST 端点 thin wrapper。
// 所有请求的错误都在上层捕获并显示 toast。

async function req(method, url, body) {
  const opts = { method, headers: { "Accept": "application/json" } };
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const resp = await fetch(url, opts);
  const text = await resp.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch (_) { data = { raw: text }; }
  if (!resp.ok) {
    const msg = (data && data.detail) || resp.statusText || "request failed";
    throw new Error(`${resp.status} ${msg}`);
  }
  return data;
}

const DEFAULT_HISTORY_LIMIT = 30;

export const api = {
  listSessions:   ()                       => req("GET",    "/api/sessions"),
  // workdir：会话级工作区（绝对路径或 ~ 开头）；空/null = 用启动时的项目根
  createSession:  (title, mode, workdir, model = "deepseek-v4-flash") => req("POST", "/api/sessions",
                                                  { title, mode, workdir: workdir || null, model }),
  getSession:     (sid, historyLimit = DEFAULT_HISTORY_LIMIT) => req("GET",
      `/api/sessions/${sid}?history_limit=${encodeURIComponent(historyLimit)}`),
  patchSession:   (sid, body)              => req("PATCH",  `/api/sessions/${sid}`, body),
  deleteSession:  (sid)                    => req("DELETE", `/api/sessions/${sid}`),

  postMessage:    (sid, text)              => req("POST",   `/api/sessions/${sid}/messages`, { text }),
  postSlash:      (sid, line)              => req("POST",   `/api/sessions/${sid}/slash`,    { line }),
  cancelSession:  (sid)                    => req("POST",   `/api/sessions/${sid}/cancel`),
  resolvePermission: (sid, askId, decision) =>
      req("POST", `/api/sessions/${sid}/permission/resolve`, { ask_id: askId, decision }),
  sessionUsage:   (sid)                    => req("GET",    `/api/sessions/${sid}/usage`),

  slashCommands:  ()                       => req("GET",    "/api/slash/commands"),

  listCron:       ()                       => req("GET",    "/api/cron"),
  fireCronTest:   (prompt)                 => req("POST",   "/api/cron/test", { prompt }),
  deleteCron:     (id)                     => req("DELETE", `/api/cron/${id}`),

  // 服务端目录浏览（"📂 浏览"按钮的支撑 API）
  fsHome:         ()                       => req("GET",    "/api/fs/home"),
  fsList:         (path, showHidden, limit = 200, maxScan = 1000) => req("GET",
      `/api/fs/list?path=${encodeURIComponent(path || "")}&show_hidden=${showHidden ? 1 : 0}&limit=${encodeURIComponent(limit)}&max_scan=${encodeURIComponent(maxScan)}`),

  writingList:    (path, limit = 200, maxScan = 1000) => req("GET",
      `/api/writing/list?path=${encodeURIComponent(path || "")}&limit=${encodeURIComponent(limit)}&max_scan=${encodeURIComponent(maxScan)}`),
  writingRead:    (root, path)             => req("GET",
      `/api/writing/read?root=${encodeURIComponent(root || "")}&path=${encodeURIComponent(path || "")}`),
  writingWrite:   (root, path, content)    => req("POST",   "/api/writing/write", { root, path, content }),
  writingCreate:  (root, name, content)    => req("POST",   "/api/writing/create", { root, name, content }),
  writingDelete:  (root, path)             => req("POST",   "/api/writing/delete", { root, path }),
};
