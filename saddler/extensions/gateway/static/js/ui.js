(function (global) {
  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function renderMarkdown(text) {
    const md = global.marked;
    const s = String(text);
    if (md && typeof md.parse === "function") {
      try {
        return md.parse(s, { async: false });
      } catch {
        return `<pre class="md-fallback">${escapeHtml(s)}</pre>`;
      }
    }
    if (typeof md === "function") {
      try {
        return md(s);
      } catch {
        return `<pre class="md-fallback">${escapeHtml(s)}</pre>`;
      }
    }
    return `<pre class="md-fallback">${escapeHtml(s)}</pre>`;
  }

  function showCdnWarning() {
    const el = document.getElementById("cdn-warning");
    if (!el) return;
    el.textContent =
      "Markdown 渲染不可用（CDN 加载失败），消息将以纯文本显示";
    el.classList.remove("hidden");
  }

  function setConnectionStatus(state) {
    const root = document.getElementById("conn-status");
    if (!root) return;
    root.dataset.state = state;
    const label = root.querySelector(".conn-label");
    if (label) {
      if (state === "connecting") label.textContent = "连接中…";
      else if (state === "connected") label.textContent = "已连接";
      else label.textContent = "已断开";
    }
    const retry = document.getElementById("retry-conn");
    if (retry) {
      retry.classList.toggle("hidden", state !== "disconnected");
    }
  }

  function setAgentOptions(agents, selectedId) {
    const sel = document.getElementById("agent-select");
    if (!sel) return;
    sel.innerHTML = "";
    for (const a of agents) {
      const opt = document.createElement("option");
      opt.value = a.id;
      opt.textContent = a.name || a.id;
      sel.appendChild(opt);
    }
    if (selectedId && agents.some((x) => x.id === selectedId)) {
      sel.value = selectedId;
    }
  }

  function setSidebarVisible(showList) {
    const side = document.getElementById("session-sidebar");
    if (side) side.classList.toggle("hidden", !showList);
  }

  function setInputEnabled(enabled) {
    const ta = document.getElementById("prompt-input");
    const btn = document.querySelector("#prompt-form button[type='submit']");
    if (ta) ta.disabled = !enabled;
    if (btn) btn.disabled = !enabled;
  }

  function clearMessages() {
    const root = document.getElementById("messages");
    if (root) root.innerHTML = "";
  }

  function setChatHint(html) {
    const el = document.getElementById("chat-hints");
    if (!el) return;
    el.innerHTML = html || "";
    el.classList.toggle("hidden", !html);
  }

  function appendUserMessage(text) {
    const root = document.getElementById("messages");
    if (!root) return;
    const wrap = document.createElement("div");
    wrap.className = "msg msg-user";
    wrap.dataset.role = "user";
    const bubble = document.createElement("div");
    bubble.className = "msg-bubble msg-md";
    bubble.innerHTML = renderMarkdown(text);
    wrap.appendChild(bubble);
    root.appendChild(wrap);
    root.scrollTop = root.scrollHeight;
    return wrap;
  }

  function ensureUserStreamBubble() {
    const root = document.getElementById("messages");
    if (!root) return null;
    let el = root.querySelector(".msg-user-streaming");
    if (!el) {
      el = document.createElement("div");
      el.className = "msg msg-user msg-user-streaming";
      el.dataset.role = "user";
      const bubble = document.createElement("div");
      bubble.className = "msg-bubble msg-md";
      el.appendChild(bubble);
      root.appendChild(el);
    }
    return el.querySelector(".msg-bubble");
  }

  function finalizeUserBubble() {
    const root = document.getElementById("messages");
    if (!root) return;
    const el = root.querySelector(".msg-user-streaming");
    if (el) {
      el.classList.remove("msg-user-streaming");
    }
  }

  function appendUserTextChunk(text) {
    const bubble = ensureUserStreamBubble();
    if (!bubble) return;
    const acc = (bubble.dataset.raw || "") + text;
    bubble.dataset.raw = acc;
    bubble.innerHTML = renderMarkdown(acc);
    const root = document.getElementById("messages");
    if (root) root.scrollTop = root.scrollHeight;
  }

  function ensureAssistantBubble() {
    const root = document.getElementById("messages");
    if (!root) return null;
    let el = root.querySelector(".msg-assistant-streaming");
    if (!el) {
      el = document.createElement("div");
      el.className = "msg msg-assistant msg-assistant-streaming";
      const bubble = document.createElement("div");
      bubble.className = "msg-bubble msg-md";
      el.appendChild(bubble);
      root.appendChild(el);
    }
    return el.querySelector(".msg-bubble");
  }

  function finalizeAssistantBubble() {
    const root = document.getElementById("messages");
    if (!root) return;
    const el = root.querySelector(".msg-assistant-streaming");
    if (el) {
      el.classList.remove("msg-assistant-streaming");
    }
  }

  function appendAssistantTextChunk(text) {
    const bubble = ensureAssistantBubble();
    if (!bubble) return;
    const acc = (bubble.dataset.raw || "") + text;
    bubble.dataset.raw = acc;
    bubble.innerHTML = renderMarkdown(acc);
    const root = document.getElementById("messages");
    if (root) root.scrollTop = root.scrollHeight;
  }

  function createThoughtBlockShell(streaming) {
    const wrap = document.createElement("div");
    wrap.className = streaming
      ? "thought-block msg-thought-streaming"
      : "thought-block";
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "thought-toggle";
    btn.setAttribute("aria-expanded", "false");
    btn.innerHTML =
      '<span class="thought-chevron">▸</span> 思考';
    const body = document.createElement("div");
    body.className = "thought-body hidden";
    const inner = document.createElement("div");
    inner.className = "thought-inner msg-md";
    body.appendChild(inner);
    btn.addEventListener("click", () => {
      const ex = btn.getAttribute("aria-expanded") === "true";
      btn.setAttribute("aria-expanded", ex ? "false" : "true");
      body.classList.toggle("hidden", ex);
      btn.querySelector(".thought-chevron").textContent = ex ? "▸" : "▾";
    });
    wrap.appendChild(btn);
    wrap.appendChild(body);
    return { wrap, inner };
  }

  function ensureThoughtBubble() {
    const root = document.getElementById("messages");
    if (!root) return null;
    let wrap = root.querySelector(".thought-block.msg-thought-streaming");
    if (!wrap) {
      const shell = createThoughtBlockShell(true);
      root.appendChild(shell.wrap);
      return shell.inner;
    }
    return wrap.querySelector(".thought-inner");
  }

  function finalizeThoughtBubble() {
    const root = document.getElementById("messages");
    if (!root) return;
    const el = root.querySelector(".thought-block.msg-thought-streaming");
    if (el) {
      el.classList.remove("msg-thought-streaming");
    }
  }

  function appendThoughtTextChunk(text) {
    const bubble = ensureThoughtBubble();
    if (!bubble) return;
    const acc = (bubble.dataset.raw || "") + text;
    bubble.dataset.raw = acc;
    bubble.innerHTML = renderMarkdown(acc);
    const root = document.getElementById("messages");
    if (root) root.scrollTop = root.scrollHeight;
  }

  function renderThoughtBlock(text) {
    const root = document.getElementById("messages");
    if (!root) return;
    const shell = createThoughtBlockShell(false);
    shell.inner.dataset.raw = text || "";
    shell.inner.innerHTML = renderMarkdown(text || "");
    root.appendChild(shell.wrap);
    root.scrollTop = root.scrollHeight;
  }

  function renderToolCard(toolCallId, title, status) {
    const root = document.getElementById("messages");
    if (!root) return;
    let row = root.querySelector(
      `[data-tool-id="${String(toolCallId).replace(/\\/g, "\\\\").replace(/"/g, '\\"')}"]`,
    );
    if (!row) {
      row = document.createElement("div");
      row.className = "tool-card";
      row.dataset.toolId = toolCallId;
      row.innerHTML = `
        <button type="button" class="tool-card-toggle" aria-expanded="false">
          <span class="tool-status-icon" data-status="${status}"></span>
          <span class="tool-title"></span>
          <span class="tool-chevron">▸</span>
        </button>
        <div class="tool-body hidden">
          <pre class="tool-detail"></pre>
        </div>`;
      const toggle = row.querySelector(".tool-card-toggle");
      toggle.addEventListener("click", () => {
        const body = row.querySelector(".tool-body");
        const ex = toggle.getAttribute("aria-expanded") === "true";
        toggle.setAttribute("aria-expanded", ex ? "false" : "true");
        body.classList.toggle("hidden", ex);
        row.querySelector(".tool-chevron").textContent = ex ? "▸" : "▾";
      });
      root.appendChild(row);
    }
    row.querySelector(".tool-title").textContent = title || toolCallId;
    const icon = row.querySelector(".tool-status-icon");
    icon.dataset.status = status;
    return row;
  }

  function updateToolCardDetail(toolCallId, text) {
    const root = document.getElementById("messages");
    if (!root) return;
    const row = root.querySelector(
      `[data-tool-id="${String(toolCallId).replace(/\\/g, "\\\\").replace(/"/g, '\\"')}"]`,
    );
    if (!row) return;
    const pre = row.querySelector(".tool-detail");
    if (pre) pre.textContent = text || "";
  }

  function renderPlanBlock(entries) {
    const root = document.getElementById("messages");
    if (!root) return;
    const wrap = document.createElement("div");
    wrap.className = "plan-block";
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "plan-toggle";
    btn.setAttribute("aria-expanded", "false");
    btn.innerHTML =
      '<span class="plan-chevron">▸</span> 计划 <span class="plan-meta"></span>';
    const body = document.createElement("div");
    body.className = "plan-body hidden";
    const ul = document.createElement("ul");
    for (const e of entries || []) {
      const li = document.createElement("li");
      li.textContent = e.content || JSON.stringify(e);
      if (e.status) li.textContent += ` — ${e.status}`;
      ul.appendChild(li);
    }
    body.appendChild(ul);
    btn.addEventListener("click", () => {
      const ex = btn.getAttribute("aria-expanded") === "true";
      btn.setAttribute("aria-expanded", ex ? "false" : "true");
      body.classList.toggle("hidden", ex);
      btn.querySelector(".plan-chevron").textContent = ex ? "▸" : "▾";
    });
    wrap.appendChild(btn);
    wrap.appendChild(body);
    root.appendChild(wrap);
    root.scrollTop = root.scrollHeight;
    return wrap;
  }

  function renderSessionList(
    sessions,
    activeId,
    stale,
    showClose,
    onPick,
    onClose,
  ) {
    const ul = document.getElementById("session-list");
    if (!ul) return;
    ul.innerHTML = "";
    for (const s of sessions) {
      const sid = s.sessionId || s.id;
      const li = document.createElement("li");
      li.className = "session-item";
      if (sid === activeId) li.classList.add("active");
      if (stale) li.classList.add("stale");
      li.dataset.sessionId = sid;
      const title = document.createElement("span");
      title.className = "session-title";
      title.textContent = s.title || sid;
      const ts = document.createElement("span");
      ts.className = "session-ts";
      ts.textContent = s.updatedAt ? formatTs(s.updatedAt) : "";
      const row = document.createElement("div");
      row.className = "session-row";
      row.appendChild(title);
      if (showClose) {
        const closeBtn = document.createElement("button");
        closeBtn.type = "button";
        closeBtn.className = "session-close";
        closeBtn.textContent = "×";
        closeBtn.title = "关闭会话";
        closeBtn.addEventListener("click", (ev) => {
          ev.stopPropagation();
          onClose(sid);
        });
        row.appendChild(closeBtn);
      }
      li.appendChild(row);
      li.appendChild(ts);
      if (stale) {
        const badge = document.createElement("span");
        badge.className = "stale-badge";
        badge.textContent = "可能过期";
        li.appendChild(badge);
      }
      li.addEventListener("click", () => onPick(sid));
      ul.appendChild(li);
    }
  }

  function formatTs(iso) {
    try {
      const d = new Date(iso);
      return d.toLocaleString();
    } catch {
      return iso;
    }
  }

  function restoreMessagesFromCache(entries) {
    clearMessages();
    if (!Array.isArray(entries)) return;
    for (const m of entries) {
      if (m.kind === "user") {
        appendUserMessage(m.text || "");
      } else if (m.kind === "assistant") {
        const root = document.getElementById("messages");
        if (!root) continue;
        const wrap = document.createElement("div");
        wrap.className = "msg msg-assistant";
        const bubble = document.createElement("div");
        bubble.className = "msg-bubble msg-md";
        bubble.dataset.raw = m.text || "";
        bubble.innerHTML = renderMarkdown(m.text || "");
        wrap.appendChild(bubble);
        root.appendChild(wrap);
      } else if (m.kind === "thought") {
        renderThoughtBlock(m.text || "");
      } else if (m.kind === "tool") {
        renderToolCard(m.toolCallId, m.title, m.status);
        updateToolCardDetail(m.toolCallId, m.detail || "");
      } else if (m.kind === "plan") {
        renderPlanBlock(m.entries);
      }
    }
    const root = document.getElementById("messages");
    if (root) root.scrollTop = root.scrollHeight;
  }

  global.GatewayUI = {
    escapeHtml,
    renderMarkdown,
    showCdnWarning,
    setConnectionStatus,
    setAgentOptions,
    setSidebarVisible,
    setInputEnabled,
    clearMessages,
    setChatHint,
    appendUserMessage,
    appendUserTextChunk,
    appendAssistantTextChunk,
    appendThoughtTextChunk,
    finalizeAssistantBubble,
    finalizeUserBubble,
    finalizeThoughtBubble,
    renderToolCard,
    updateToolCardDetail,
    renderPlanBlock,
    renderSessionList,
    restoreMessagesFromCache,
  };
})(window);
