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

export const api = {
  listSessions:   ()             => req("GET",    "/api/sessions"),
  createSession:  (title, mode)  => req("POST",   "/api/sessions", { title, mode }),
  getSession:     (sid)          => req("GET",    `/api/sessions/${sid}`),
  patchSession:   (sid, body)    => req("PATCH",  `/api/sessions/${sid}`, body),
  deleteSession:  (sid)          => req("DELETE", `/api/sessions/${sid}`),

  postMessage:    (sid, text)    => req("POST",   `/api/sessions/${sid}/messages`, { text }),
  postSlash:      (sid, line)    => req("POST",   `/api/sessions/${sid}/slash`,    { line }),
  sessionUsage:   (sid)          => req("GET",    `/api/sessions/${sid}/usage`),

  slashCommands:  ()             => req("GET",    "/api/slash/commands"),

  listCron:       ()             => req("GET",    "/api/cron"),
  fireCronTest:   (prompt)       => req("POST",   "/api/cron/test", { prompt }),
  deleteCron:     (id)           => req("DELETE", `/api/cron/${id}`),
};
