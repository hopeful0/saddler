/**
 * Agent Client Protocol: https://agentclientprotocol.com/get-started/introduction
 * Schema: https://agentclientprotocol.com/protocol/schema
 * JSON-RPC errors: https://agentclientprotocol.com/protocol/overview#error-handling
 */

(function (global) {
  class ACPJsonRpcError extends Error {
    /**
     * @param {unknown} err JSON-RPC `error` object (`code`, `message`, optional `data`)
     */
    constructor(err) {
      let code = null;
      let data;
      let text = "";
      if (err && typeof err === "object" && !Array.isArray(err)) {
        if (typeof err.code === "number") {
          code = err.code;
        }
        if ("data" in err) {
          data = err.data;
        }
        if (typeof err.message === "string") {
          text = err.message;
        }
      } else if (typeof err === "string") {
        text = err;
      }
      if (!text) {
        text =
          code != null
            ? `远程方法返回错误（code ${code}）`
            : "远程方法返回错误";
      }
      super(text);
      this.name = "ACPJsonRpcError";
      this.code = code;
      if (data !== undefined) {
        this.data = data;
      }
    }
  }

  function withMcpServersArray(params) {
    const p = params && typeof params === "object" ? { ...params } : {};
    if (!Array.isArray(p.mcpServers)) {
      p.mcpServers = [];
    }
    return p;
  }

  class ACPClient {
    constructor(agentId) {
      this.agentId = agentId;
      this.ws = null;
      this._nextId = 1;
      this._pending = new Map();
      this._sessionUpdateHandlers = new Set();
      this._connHandlers = new Set();
      this._state = "disconnected";
    }

    onConnection(fn) {
      this._connHandlers.add(fn);
      return () => this._connHandlers.delete(fn);
    }

    onSessionUpdate(fn) {
      this._sessionUpdateHandlers.add(fn);
      return () => this._sessionUpdateHandlers.delete(fn);
    }

    _emitConn() {
      for (const fn of this._connHandlers) {
        try {
          fn(this._state);
        } catch (e) {
          console.error(e);
        }
      }
    }

    setState(s) {
      if (this._state === s) return;
      this._state = s;
      this._emitConn();
    }

    get connectionState() {
      return this._state;
    }

    connect() {
      return new Promise((resolve, reject) => {
        this.setState("connecting");
        const proto = location.protocol === "https:" ? "wss:" : "ws:";
        const url = `${proto}//${location.host}/agents/${encodeURIComponent(this.agentId)}/ws`;
        const ws = new WebSocket(url);
        this.ws = ws;
        let opened = false;
        ws.onopen = () => {
          opened = true;
          this.setState("connected");
          resolve();
        };
        ws.onmessage = (ev) => {
          try {
            const msg = JSON.parse(ev.data);
            this._handleMessage(msg);
          } catch (e) {
            console.error("ws parse error", e);
          }
        };
        ws.onclose = () => {
          this.ws = null;
          for (const [, p] of this._pending) {
            p.reject(new Error("连接已断开"));
          }
          this._pending.clear();
          this.setState("disconnected");
          if (!opened) {
            reject(new Error("无法建立 WebSocket 连接"));
          }
        };
      });
    }

    close() {
      if (this.ws) {
        try {
          this.ws.close();
        } catch {
          /* ignore */
        }
        this.ws = null;
      }
      this.setState("disconnected");
    }

    _handleMessage(msg) {
      if (Array.isArray(msg)) {
        for (const part of msg) {
          this._handleMessage(part);
        }
        return;
      }
      if (!msg || typeof msg !== "object") {
        console.warn("acp: ignored non-object message");
        return;
      }
      if (
        msg.id !== undefined &&
        msg.id !== null &&
        (msg.result !== undefined || msg.error !== undefined)
      ) {
        const p = this._pending.get(msg.id);
        if (p) {
          this._pending.delete(msg.id);
          if (msg.error !== undefined && msg.error !== null) {
            p.reject(new ACPJsonRpcError(msg.error));
          } else if (msg.result !== undefined) {
            p.resolve(msg.result);
          } else {
            p.reject(
              new ACPJsonRpcError({
                code: -32603,
                message: "响应缺少 result 与 error",
              }),
            );
          }
        } else if (msg.id !== undefined && msg.id !== null) {
          console.warn("acp: unmatched response id", msg.id);
        }
        return;
      }
      if (msg.method === "session/update") {
        for (const fn of this._sessionUpdateHandlers) {
          try {
            fn(msg.params);
          } catch (e) {
            console.error(e);
          }
        }
      }
    }

    request(method, params) {
      if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
        return Promise.reject(new Error("WebSocket 未连接"));
      }
      const id = this._nextId++;
      const payload = {
        jsonrpc: "2.0",
        id,
        method,
        params: params === undefined ? {} : params,
      };
      return new Promise((resolve, reject) => {
        this._pending.set(id, { resolve, reject });
        try {
          this.ws.send(JSON.stringify(payload));
        } catch (e) {
          this._pending.delete(id);
          reject(e);
        }
      });
    }

    notify(method, params) {
      if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return;
      this.ws.send(
        JSON.stringify({
          jsonrpc: "2.0",
          method,
          params: params === undefined ? {} : params,
        }),
      );
    }

    async initialize(clientInfo) {
      const result = await this.request("initialize", {
        protocolVersion: 1,
        clientCapabilities: {},
        clientInfo: clientInfo || {
          name: "saddler-gateway-ui",
          version: "0.1.0",
          title: "Saddler Gateway UI",
        },
      });
      return result;
    }

    sessionList(params) {
      return this.request("session/list", params || {});
    }

    sessionNew(params) {
      return this.request("session/new", withMcpServersArray(params));
    }

    sessionResume(params) {
      return this.request("session/resume", withMcpServersArray(params));
    }

    sessionLoad(params) {
      return this.request("session/load", withMcpServersArray(params));
    }

    sessionClose(params) {
      return this.request("session/close", params);
    }

    sessionPrompt(params) {
      return this.request("session/prompt", params);
    }
  }

  global.ACPJsonRpcError = ACPJsonRpcError;
  global.ACPClient = ACPClient;
})(window);
