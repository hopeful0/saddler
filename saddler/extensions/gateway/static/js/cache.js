(function (global) {
  const PREFIX_AGENT = "saddler:agent:";
  const PREFIX_SESSION = "saddler:session:";

  function safeParse(json, fallback) {
    try {
      return JSON.parse(json);
    } catch {
      return fallback;
    }
  }

  function getAgentCaps(agentId) {
    try {
      const raw = localStorage.getItem(`${PREFIX_AGENT}${agentId}:caps`);
      if (!raw) return null;
      return safeParse(raw, null);
    } catch {
      return null;
    }
  }

  function setAgentCaps(agentId, caps) {
    try {
      localStorage.setItem(
        `${PREFIX_AGENT}${agentId}:caps`,
        JSON.stringify(caps),
      );
      return true;
    } catch {
      return false;
    }
  }

  function getSessionList(agentId) {
    try {
      const raw = localStorage.getItem(`${PREFIX_AGENT}${agentId}:sessions`);
      if (!raw) return null;
      return safeParse(raw, null);
    } catch {
      return null;
    }
  }

  function setSessionList(agentId, sessions) {
    try {
      localStorage.setItem(
        `${PREFIX_AGENT}${agentId}:sessions`,
        JSON.stringify(sessions),
      );
      return true;
    } catch {
      return false;
    }
  }

  function getMessages(sessionId) {
    try {
      const raw = localStorage.getItem(
        `${PREFIX_SESSION}${sessionId}:messages`,
      );
      if (!raw) return null;
      return safeParse(raw, null);
    } catch {
      return null;
    }
  }

  function setMessages(sessionId, msgs) {
    try {
      localStorage.setItem(
        `${PREFIX_SESSION}${sessionId}:messages`,
        JSON.stringify(msgs),
      );
      return true;
    } catch {
      return false;
    }
  }

  function appendMessage(sessionId, msg) {
    try {
      const cur = getMessages(sessionId);
      const list = Array.isArray(cur) ? cur.slice() : [];
      list.push(msg);
      return setMessages(sessionId, list);
    } catch {
      return false;
    }
  }

  global.GatewayCache = {
    getAgentCaps,
    setAgentCaps,
    getSessionList,
    setSessionList,
    getMessages,
    setMessages,
    appendMessage,
  };
})(window);
