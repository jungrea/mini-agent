// ws.js —— 会话 WebSocket（目前仅用于权限 ask 回推）。

class WSManager {
  constructor() {
    this.ws = null;
    this.sid = null;
    this.pending = new Map();
  }

  connect(sid) {
    this.disconnect();
    this.sid = sid;
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const url = `${proto}://${location.host}/api/ws/${sid}`;
    this.ws = new WebSocket(url);
    this.ws.onopen = () => {
      // 保活 ping
      this._keepaliveTimer = setInterval(() => this._ping(), 30000);
    };
    this.ws.onclose = () => {
      clearInterval(this._keepaliveTimer);
      this._keepaliveTimer = null;
    };
    this.ws.onerror = (e) => console.warn("[ws] error", e);
  }

  disconnect() {
    if (this._keepaliveTimer) { clearInterval(this._keepaliveTimer); this._keepaliveTimer = null; }
    if (this.ws) { try { this.ws.close(); } catch (_) {} this.ws = null; }
    this.sid = null;
  }

  _ping() {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify({ type: "ping" }));
    }
  }

  resolvePermission(askId, decision) {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
      console.warn("[ws] not open, cannot resolve permission");
      return false;
    }
    this.ws.send(JSON.stringify({ type: "permission_resolve", ask_id: askId, decision }));
    return true;
  }
}

export const ws = new WSManager();
