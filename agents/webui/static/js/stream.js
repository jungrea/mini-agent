// stream.js —— EventSource 管理。
// 为了简单：同一时刻只保留 "当前会话流" + "全局流" 两条连接。
// 切换会话时关闭旧的，打开新的。

class StreamManager {
  constructor() {
    this.sessionES = null;
    this.globalES = null;
    this.listeners = { session: new Set(), global: new Set() };
  }

  _attach(es, bucket) {
    es.onmessage = (ev) => {
      let data = null;
      try { data = JSON.parse(ev.data); } catch (_) { return; }
      for (const fn of this.listeners[bucket]) {
        try { fn(data); } catch (e) { console.error(e); }
      }
    };
    es.onerror = (err) => {
      console.warn(`[sse ${bucket}] error`, err);
      // EventSource 默认会自动重连；不额外处理
    };
  }

  connectGlobal() {
    if (this.globalES) return;
    this.globalES = new EventSource("/api/stream/global");
    this._attach(this.globalES, "global");
  }

  connectSession(sid) {
    this.disconnectSession();
    if (!sid) return;
    this.sessionES = new EventSource(`/api/stream/${sid}`);
    this._attach(this.sessionES, "session");
  }

  disconnectSession() {
    if (this.sessionES) { this.sessionES.close(); this.sessionES = null; }
  }

  onSession(fn)  { this.listeners.session.add(fn); }
  onGlobal(fn)   { this.listeners.global.add(fn); }
}

export const stream = new StreamManager();
