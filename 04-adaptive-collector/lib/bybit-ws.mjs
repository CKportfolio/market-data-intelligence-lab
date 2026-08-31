import WebSocket from "ws";

export class BybitPublicSocket {
  constructor({ name, url, topics, onMessage, onStatus }) {
    this.name = name;
    this.url = url;
    this.topics = topics.filter(Boolean);
    this.onMessage = onMessage;
    this.onStatus = onStatus || (() => {});
    this.ws = null;
    this.stopping = false;
    this.heartbeat = null;
    this.reconnectTimer = null;
    this.reconnectAttempt = 0;
  }

  start() {
    this.stopping = false;
    this.#connect();
  }

  #connect() {
    if (this.stopping || !this.topics.length) return;
    this.onStatus({ socket: this.name, state: "connecting", tsMs: Date.now(), url: this.url, topics: this.topics });

    const ws = new WebSocket(this.url, { perMessageDeflate: false, handshakeTimeout: 10_000 });
    this.ws = ws;

    ws.on("open", () => {
      this.reconnectAttempt = 0;
      this.onStatus({ socket: this.name, state: "open", tsMs: Date.now(), topics: this.topics });
      ws.send(JSON.stringify({ op: "subscribe", args: this.topics }));
      this.#startHeartbeat();
    });

    ws.on("message", (raw) => {
      let msg;
      try { msg = JSON.parse(raw.toString()); }
      catch { return; }

      if (msg?.op === "ping" || msg?.op === "pong") return;
      if (msg?.op === "subscribe") {
        this.onStatus({ socket: this.name, state: msg.success === false ? "subscribe_error" : "subscribed", tsMs: Date.now(), response: msg });
        return;
      }
      if (msg?.topic) this.onMessage(msg, Date.now());
    });

    ws.on("error", (err) => {
      this.onStatus({ socket: this.name, state: "error", tsMs: Date.now(), error: err?.message || String(err) });
    });

    ws.on("close", (code, reason) => {
      this.#stopHeartbeat();
      this.onStatus({ socket: this.name, state: "closed", tsMs: Date.now(), code, reason: reason?.toString?.() || "" });
      if (!this.stopping) this.#scheduleReconnect();
    });
  }

  #startHeartbeat() {
    this.#stopHeartbeat();
    this.heartbeat = setInterval(() => {
      if (this.ws?.readyState === WebSocket.OPEN) {
        this.ws.send(JSON.stringify({ op: "ping" }));
      }
    }, 20_000);
    this.heartbeat.unref?.();
  }

  #stopHeartbeat() {
    if (this.heartbeat) clearInterval(this.heartbeat);
    this.heartbeat = null;
  }

  #scheduleReconnect() {
    if (this.reconnectTimer || this.stopping) return;
    const delay = Math.min(30_000, 1000 * 2 ** Math.min(this.reconnectAttempt++, 5));
    this.onStatus({ socket: this.name, state: "reconnect_scheduled", tsMs: Date.now(), delayMs: delay });
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      this.#connect();
    }, delay);
  }

  stop() {
    this.stopping = true;
    this.#stopHeartbeat();
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    this.reconnectTimer = null;
    if (this.ws && (this.ws.readyState === WebSocket.OPEN || this.ws.readyState === WebSocket.CONNECTING)) {
      try { this.ws.close(1000, "shutdown"); } catch {}
    }
  }
}
