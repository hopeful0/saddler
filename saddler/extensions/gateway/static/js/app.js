(function () {
  const ui = window.GatewayUI;
  const cache = window.GatewayCache;
  const ACPClient = window.ACPClient;

  const state = {
    agents: [],
    agentId: null,
    client: null,
    caps: null,
    sessions: [],
    sessionsStale: false,
    currentSessionId: null,
    readonly: false,
    readonlyDetail: "",
    transcript: [],
    lastPromptUserText: null,
    sending: false,
    unsubConn: null,
    unsubUp: null,
    initializing: false,
  };

  function parseHash() {
    const h = location.hash.replace(/^#/, "");
    const p = new URLSearchParams(h);
    return {
      agent: p.get("agent") || "",
      session: p.get("session") || "",
    };
  }

  function writeHash(agentId, sessionId) {
    const p = new URLSearchParams();
    if (agentId) p.set("agent", agentId);
    if (sessionId) p.set("session", sessionId);
    const tail = p.toString();
    const next = tail ? `#${tail}` : "";
    if (location.hash !== next) {
      location.hash = next;
    }
  }

  function normalizeCaps(result) {
    const ac = result.agentCapabilities || result.capabilities || {};
    const sc = ac.sessionCapabilities || {};
    const hasCap = (key) =>
      Object.prototype.hasOwnProperty.call(sc, key) && sc[key] !== false;
    return {
      raw: result,
      loadSession: ac.loadSession === true,
      sessionList: hasCap("list"),
      sessionResume: hasCap("resume"),
      sessionClose: hasCap("close"),
    };
  }

  function persistTranscript() {
    if (!state.currentSessionId) return;
    try {
      cache.setMessages(state.currentSessionId, state.transcript);
    } catch {
      /* ignore */
    }
  }

  function setReadonly(on, detail) {
    state.readonly = on;
    state.readonlyDetail = detail || "";
    refreshInputState();
    if (!on) {
      ui.setChatHint("");
    } else if (detail) {
      ui.setChatHint(`<p>${ui.escapeHtml(detail)}</p>`);
    }
  }

  function refreshInputState() {
    const ok =
      state.client &&
      state.client.connectionState === "connected" &&
      state.currentSessionId &&
      !state.readonly &&
      !state.sending &&
      state.caps;
    ui.setInputEnabled(!!ok);
  }

  function blockText(content) {
    if (!content) return "";
    if (typeof content === "string") return content;
    if (content.type === "text" && content.text) return String(content.text);
    return "";
  }

  function flattenToolContent(contentArr) {
    if (!Array.isArray(contentArr)) return "";
    const parts = [];
    for (const c of contentArr) {
      if (c && c.type === "content" && c.content) {
        parts.push(blockText(c.content));
      } else {
        parts.push(JSON.stringify(c));
      }
    }
    return parts.join("\n");
  }

  function stringifyToolDetail(value) {
    if (value == null) return "";
    const t = typeof value;
    if (t === "string") return value;
    if (t === "number" || t === "boolean") return String(value);
    if (t === "bigint") return String(value);
    try {
      return JSON.stringify(value, null, 2);
    } catch {
      return String(value);
    }
  }

  function finalizeThoughtTurn() {
    const root = document.getElementById("messages");
    if (!root || !root.querySelector(".msg-thought-streaming")) return;
    const bubble = root.querySelector(
      ".msg-thought-streaming .thought-inner",
    );
    const raw = bubble && bubble.dataset.raw ? bubble.dataset.raw : "";
    ui.finalizeThoughtBubble();
    if (raw) {
      state.transcript.push({ kind: "thought", text: raw });
      persistTranscript();
    }
  }

  function finalizeAssistantTurn() {
    const root = document.getElementById("messages");
    if (!root || !root.querySelector(".msg-assistant-streaming")) return;
    const bubble = root.querySelector(
      ".msg-assistant-streaming .msg-bubble",
    );
    const raw = bubble && bubble.dataset.raw ? bubble.dataset.raw : "";
    ui.finalizeAssistantBubble();
    if (raw) {
      state.transcript.push({ kind: "assistant", text: raw });
      persistTranscript();
    }
  }

  function finalizeUserTurn() {
    const root = document.getElementById("messages");
    if (!root || !root.querySelector(".msg-user-streaming")) return;
    const wrap = root.querySelector(".msg-user-streaming");
    const bubble = wrap && wrap.querySelector(".msg-bubble");
    const raw = bubble && bubble.dataset.raw ? bubble.dataset.raw : "";
    if (
      raw &&
      state.lastPromptUserText != null &&
      raw === state.lastPromptUserText
    ) {
      state.lastPromptUserText = null;
      wrap.remove();
      return;
    }
    state.lastPromptUserText = null;
    ui.finalizeUserBubble();
    if (raw) {
      state.transcript.push({ kind: "user", text: raw });
      persistTranscript();
    }
  }

  function finalizeOpenUserStreamIfAny() {
    const root = document.getElementById("messages");
    if (root && root.querySelector(".msg-user-streaming")) {
      finalizeUserTurn();
    }
  }

  /** 将仍带 streaming 类的气泡写入 transcript 并持久化（避免未收到 isLast 就切会话导致缓存缺最后一条） */
  function flushStreamingToTranscript() {
    if (!state.currentSessionId) return;
    finalizeThoughtTurn();
    finalizeAssistantTurn();
    finalizeUserTurn();
  }

  function finalizeStreamingTurns() {
    finalizeThoughtTurn();
    finalizeAssistantTurn();
    finalizeUserTurn();
    state.lastPromptUserText = null;
  }

  function handleSessionUpdate(params) {
    const sid = params && params.sessionId;
    if (!sid || sid !== state.currentSessionId) {
      if (params && params.update) {
        const u = params.update;
        const kind = u.sessionUpdate || u.type;
        if (kind === "session_info_update" && u.title) {
          const hit = state.sessions.find(
            (x) => (x.sessionId || x.id) === sid,
          );
          if (hit) {
            hit.title = u.title;
            if (u.updatedAt) hit.updatedAt = u.updatedAt;
            renderSessionListUI();
            if (state.agentId) {
              cache.setSessionList(state.agentId, state.sessions);
            }
          }
        }
      }
      return;
    }
    const u = params.update || {};
    const kind = u.sessionUpdate || u.type;

    if (kind === "user_message_chunk") {
      const root = document.getElementById("messages");
      if (root) {
        if (root.querySelector(".msg-thought-streaming")) {
          finalizeThoughtTurn();
        }
        if (root.querySelector(".msg-assistant-streaming")) {
          finalizeAssistantTurn();
        }
      }
      const piece = blockText(u.content);
      if (piece) ui.appendUserTextChunk(piece);
      if (u.isLast === true || u.last === true) {
        finalizeUserTurn();
      }
      return;
    }

    if (kind === "agent_thought_chunk") {
      const root = document.getElementById("messages");
      if (root && root.querySelector(".msg-user-streaming")) {
        finalizeUserTurn();
      }
      if (root && root.querySelector(".msg-assistant-streaming")) {
        finalizeAssistantTurn();
      }
      const piece = blockText(u.content);
      if (piece) ui.appendThoughtTextChunk(piece);
      if (u.isLast === true || u.last === true) {
        finalizeThoughtTurn();
      }
      return;
    }

    if (kind === "agent_message_chunk") {
      const root = document.getElementById("messages");
      if (root && root.querySelector(".msg-user-streaming")) {
        finalizeUserTurn();
      }
      if (root && root.querySelector(".msg-thought-streaming")) {
        finalizeThoughtTurn();
      }
      const piece = blockText(u.content);
      if (piece) ui.appendAssistantTextChunk(piece);
      if (u.isLast === true || u.last === true) {
        finalizeAssistantTurn();
      }
      return;
    }

    if (kind === "tool_call") {
      finalizeOpenUserStreamIfAny();
      ui.renderToolCard(
        u.toolCallId,
        u.title || u.toolCallId,
        u.status || "pending",
      );
      state.transcript.push({
        kind: "tool",
        toolCallId: u.toolCallId,
        title: u.title || u.toolCallId,
        status: u.status || "pending",
        detail: "",
      });
      persistTranscript();
      return;
    }

    if (kind === "tool_call_update") {
      finalizeOpenUserStreamIfAny();
      const st = u.status || "pending";
      ui.renderToolCard(u.toolCallId, "", st);
      const detail =
        flattenToolContent(u.content) ||
        (u.rawOutput != null ? stringifyToolDetail(u.rawOutput) : "");
      if (detail) ui.updateToolCardDetail(u.toolCallId, detail);
      const t = state.transcript.find(
        (x) => x.kind === "tool" && x.toolCallId === u.toolCallId,
      );
      if (t) {
        t.status = st;
        if (detail) t.detail = detail;
        persistTranscript();
      }
      return;
    }

    if (kind === "plan") {
      finalizeOpenUserStreamIfAny();
      ui.renderPlanBlock(u.entries || []);
      state.transcript.push({ kind: "plan", entries: u.entries || [] });
      persistTranscript();
      return;
    }

    if (kind === "session_info_update") {
      const hit = state.sessions.find(
        (x) => (x.sessionId || x.id) === state.currentSessionId,
      );
      if (hit) {
        if (u.title != null) hit.title = u.title;
        if (u.updatedAt != null) hit.updatedAt = u.updatedAt;
        renderSessionListUI();
        if (state.agentId) {
          cache.setSessionList(state.agentId, state.sessions);
        }
      }
    }
  }

  function renderSessionListUI() {
    if (!state.caps || !state.caps.sessionList) return;
    ui.renderSessionList(
      state.sessions,
      state.currentSessionId,
      state.sessionsStale,
      state.caps && state.caps.sessionClose,
      (sid) => {
        void selectSession(sid);
      },
      (sid) => {
        void closeSession(sid);
      },
    );
  }

  async function closeSession(sessionId) {
    if (!state.client || !state.caps || !state.caps.sessionClose) return;
    try {
      await state.client.sessionClose({ sessionId });
    } catch {
      /* ignore */
    }
    state.sessions = state.sessions.filter(
      (x) => (x.sessionId || x.id) !== sessionId,
    );
    if (state.agentId) {
      cache.setSessionList(state.agentId, state.sessions);
    }
    renderSessionListUI();
    if (state.currentSessionId === sessionId) {
      state.currentSessionId = null;
      state.transcript = [];
      ui.clearMessages();
      writeHash(state.agentId, "");
    }
  }

  async function fetchAllSessions(client) {
    let cursor;
    const all = [];
    while (true) {
      const r = await client.sessionList(cursor ? { cursor } : {});
      const sessions = r.sessions || [];
      all.push(...sessions);
      if (!r.nextCursor) break;
      cursor = r.nextCursor;
    }
    return all;
  }

  function cwdForAgent(agentId) {
    const a = state.agents.find((x) => x.id === agentId);
    if (a && a.spec && a.spec.workdir) return a.spec.workdir;
    return undefined;
  }

  /** Merge agent workdir as ACP `cwd` when present (session/load 等要求绝对路径字符串). */
  function acpSessionParams(extra) {
    const out = { ...extra };
    const cwd = state.agentId ? cwdForAgent(state.agentId) : undefined;
    if (cwd) out.cwd = cwd;
    return out;
  }

  async function loadSessionsFromServer() {
    if (!state.client || !state.caps || !state.caps.sessionList) return;
    try {
      const list = await fetchAllSessions(state.client);
      state.sessions = list;
      state.sessionsStale = false;
      if (state.agentId) {
        cache.setSessionList(state.agentId, list);
      }
      renderSessionListUI();
    } catch {
      /* keep stale list */
    }
  }

  function showCachedSessionsImmediately() {
    if (!state.agentId) return;
    const list = cache.getSessionList(state.agentId);
    if (Array.isArray(list) && list.length) {
      state.sessions = list;
      state.sessionsStale = true;
      renderSessionListUI();
    }
  }

  async function selectSession(sessionId) {
    if (!sessionId || !state.agentId) return;
    flushStreamingToTranscript();
    state.lastPromptUserText = null;
    state.currentSessionId = sessionId;
    writeHash(state.agentId, sessionId);
    state.readonly = false;
    state.readonlyDetail = "";
    ui.setChatHint("");

    const l3 = cache.getMessages(sessionId);
    const hasL3 = Array.isArray(l3) && l3.length > 0;
    const caps = state.caps;

    if (hasL3) {
      state.transcript = l3.slice();
      ui.restoreMessagesFromCache(state.transcript);
      try {
        await state.client.sessionResume(acpSessionParams({ sessionId }));
        setReadonly(false, "");
      } catch {
        if (caps && caps.sessionResume) {
          setReadonly(true, "该会话已结束，无法续聊");
        } else {
          setReadonly(
            true,
            "无法继续对话（未支持会话恢复且续连失败）",
          );
        }
      }
      refreshInputState();
      renderSessionListUI();
      return;
    }

    state.transcript = [];
    ui.clearMessages();

    if (caps && caps.loadSession) {
      ui.setChatHint("<p>正在加载会话历史…</p>");
      const loadCwd = cwdForAgent(state.agentId);
      let loadSucceeded = false;
      if (loadCwd) {
        try {
          await state.client.sessionLoad(acpSessionParams({ sessionId }));
          loadSucceeded = true;
        } catch {
          /* fall back to session/resume */
        }
      }
      ui.setChatHint("");
      if (loadSucceeded) {
        setReadonly(false, "");
      } else {
        try {
          await state.client.sessionResume(acpSessionParams({ sessionId }));
          setReadonly(false, "");
        } catch {
          if (caps.sessionResume) {
            setReadonly(true, "该会话已结束，无法续聊");
          } else {
            setReadonly(
              true,
              "无法继续对话（未支持会话恢复且续连失败）",
            );
          }
        }
      }
      refreshInputState();
      renderSessionListUI();
      return;
    }

    ui.setChatHint(
      `<p>${ui.escapeHtml("该会话历史不可恢复")}</p>`,
    );
    try {
      await state.client.sessionResume(acpSessionParams({ sessionId }));
      setReadonly(false, "");
    } catch {
      if (state.caps && state.caps.sessionResume) {
        setReadonly(true, "该会话已结束，无法续聊");
      } else {
        setReadonly(
          true,
          "无法继续对话（未支持会话恢复且续连失败）",
        );
      }
    }
    refreshInputState();
    renderSessionListUI();
  }

  async function newSession() {
    if (!state.client || !state.agentId) return;
    flushStreamingToTranscript();
    const res = await state.client.sessionNew(acpSessionParams({}));
    const sid = res.sessionId || res.session_id;
    if (!sid) throw new Error("session/new 未返回 sessionId");
    state.sessions = [
      { sessionId: sid, title: "新对话", updatedAt: new Date().toISOString() },
      ...state.sessions.filter((x) => (x.sessionId || x.id) !== sid),
    ];
    if (state.agentId) {
      cache.setSessionList(state.agentId, state.sessions);
    }
    state.transcript = [];
    state.lastPromptUserText = null;
    cache.setMessages(sid, []);
    ui.clearMessages();
    ui.setChatHint("");
    state.readonly = false;
    state.currentSessionId = sid;
    writeHash(state.agentId, sid);
    renderSessionListUI();
    refreshInputState();
  }

  async function connectAgent(agentId) {
    flushStreamingToTranscript();

    if (state.unsubConn) {
      state.unsubConn();
      state.unsubConn = null;
    }
    if (state.unsubUp) {
      state.unsubUp();
      state.unsubUp = null;
    }
    if (state.client) {
      state.client.close();
      state.client = null;
    }

    state.agentId = agentId;
    state.lastPromptUserText = null;
    state.caps = null;
    state.sessions = [];
    state.currentSessionId = null;
    state.transcript = [];
    state.sessionsStale = false;
    ui.clearMessages();
    ui.setChatHint("");

    const cached = cache.getAgentCaps(agentId);
    if (cached && cached.sessionList) {
      ui.setSidebarVisible(true);
      showCachedSessionsImmediately();
    } else {
      ui.setSidebarVisible(true);
    }

    const client = new ACPClient(agentId);
    state.client = client;

    state.unsubConn = client.onConnection((st) => {
      ui.setConnectionStatus(st);
      refreshInputState();
    });
    state.unsubUp = client.onSessionUpdate(handleSessionUpdate);

    try {
      await client.connect();
    } catch (e) {
      ui.setConnectionStatus("disconnected");
      ui.setChatHint(`<p>${ui.escapeHtml(String(e.message || e))}</p>`);
      return;
    }

    try {
      const initResult = await client.initialize();
      state.caps = normalizeCaps(initResult);
      cache.setAgentCaps(agentId, state.caps);
      ui.setSidebarVisible(!!state.caps.sessionList);
      if (!state.caps.sessionList) {
        state.sessions = [];
        renderSessionListUI();
      } else {
        await loadSessionsFromServer();
      }
    } catch (e) {
      ui.setChatHint(`<p>初始化失败：${ui.escapeHtml(String(e.message || e))}</p>`);
      return;
    }

    const { session } = parseHash();
    if (session && state.caps.sessionList) {
      await selectSession(session);
    } else if (session && !state.caps.sessionList) {
      state.currentSessionId = session;
      writeHash(agentId, session);
      state.transcript = cache.getMessages(session) || [];
      if (state.transcript.length) {
        ui.restoreMessagesFromCache(state.transcript);
      }
      try {
        await client.sessionResume(acpSessionParams({ sessionId: session }));
        setReadonly(false, "");
      } catch {
        if (state.caps.sessionResume) {
          setReadonly(true, "该会话已结束，无法续聊");
        } else {
          setReadonly(
            true,
            "无法继续对话（未支持会话恢复且续连失败）",
          );
        }
      }
      refreshInputState();
    } else if (state.caps.sessionList) {
      ui.setChatHint(
        `<p>${ui.escapeHtml("点击「新对话」开始，或从左侧选择历史会话。")}</p>`,
      );
      state.currentSessionId = null;
      state.transcript = [];
      ui.clearMessages();
      refreshInputState();
    } else {
      await newSession();
    }
  }

  async function onAgentSelectChange() {
    const sel = document.getElementById("agent-select");
    const id = sel && sel.value;
    if (!id) return;
    writeHash(id, "");
    await connectAgent(id);
  }

  async function bootstrap() {
    if (window.__markedFailed) {
      ui.showCdnWarning();
    }
    ui.setConnectionStatus("disconnected");
    let res;
    try {
      res = await fetch("/agents", { headers: { Accept: "application/json" } });
    } catch {
      ui.setChatHint("<p>无法加载 agent 列表</p>");
      return;
    }
    if (!res.ok) {
      ui.setChatHint("<p>无法加载 agent 列表</p>");
      return;
    }
    state.agents = await res.json();
    if (!state.agents.length) {
      const main = document.querySelector(".chat-main");
      if (main) {
        const d = document.createElement("div");
        d.className = "empty-state";
        d.textContent = "暂无已注册的 agent，请使用 CLI 创建后再试。";
        main.insertBefore(d, main.firstChild);
      }
      ui.setAgentOptions([], "");
      return;
    }

    const { agent, session } = parseHash();
    const pick =
      agent && state.agents.some((x) => x.id === agent)
        ? agent
        : state.agents[0].id;
    ui.setAgentOptions(state.agents, pick);
    await connectAgent(pick);
  }

  document.getElementById("agent-select").addEventListener("change", () => {
    void onAgentSelectChange();
  });

  document.getElementById("new-session").addEventListener("click", () => {
    void newSession().catch((e) => {
      ui.setChatHint(`<p>${ui.escapeHtml(String(e.message || e))}</p>`);
    });
  });

  document.getElementById("retry-conn").addEventListener("click", () => {
    const id = state.agentId || document.getElementById("agent-select").value;
    if (id) void connectAgent(id);
  });

  window.addEventListener("hashchange", () => {
    const { agent, session } = parseHash();
    const sel = document.getElementById("agent-select");
    if (agent && agent !== state.agentId && state.agents.some((x) => x.id === agent)) {
      sel.value = agent;
      void connectAgent(agent);
    } else if (
      session &&
      session !== state.currentSessionId &&
      state.client &&
      state.client.connectionState === "connected"
    ) {
      void selectSession(session);
    }
  });

  document.getElementById("prompt-form").addEventListener("submit", (ev) => {
    ev.preventDefault();
    const ta = document.getElementById("prompt-input");
    const text = (ta.value || "").trim();
    if (!text || !state.client || !state.currentSessionId) return;
    if (state.readonly || state.sending) return;
    state.sending = true;
    refreshInputState();
    ta.value = "";
    ui.appendUserMessage(text);
    state.transcript.push({ kind: "user", text });
    state.lastPromptUserText = text;
    persistTranscript();

    const sid = state.currentSessionId;
    state.client
      .sessionPrompt({
        sessionId: sid,
        prompt: [{ type: "text", text }],
      })
      .then(() => {
        finalizeStreamingTurns();
      })
      .catch((e) => {
        const msg =
          e && e.message
            ? String(e.message)
            : JSON.stringify(e || "session/prompt 失败");
        ui.setChatHint(`<p>${ui.escapeHtml(msg)}</p>`);
        finalizeStreamingTurns();
      })
      .finally(() => {
        state.sending = false;
        refreshInputState();
      });
  });

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => void bootstrap());
  } else {
    void bootstrap();
  }
})();
