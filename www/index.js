/* R2 Web UI */

// ── Key helpers ────────────────────────────────────────────────────────────

// PA  session key: "paName/sessionId"
// TA session key: "paName/paSessionId/taSessionId"

function sessionKey(paName, sessionId) { return `${paName}/${sessionId}`; }
function taSessionKey(paName, paSessionId, taId) { return `${paName}/${paSessionId}/${taId}`; }
function isTA(key) { return key ? key.split("/").length === 3 : false; }

// ── Session state ──────────────────────────────────────────────────────────

const sessionStates = new Map();   // key → state (PA or TA)
const paCounters    = new Map();   // paName → next session number
const sessionNumbers = new Map();  // PA session key → number

let activeKey = null;

// ── PA session state ───────────────────────────────────────────────────────

function getOrCreateState(paName, sessionId) {
  const key = sessionKey(paName, sessionId);
  if (!sessionStates.has(key)) {
    const pane = document.createElement("div");
    pane.className = "session-pane";

    const messagesEl = document.createElement("div");
    messagesEl.className = "session-messages";
    pane.appendChild(messagesEl);

    const inputArea = document.createElement("div");
    inputArea.className = "session-input-area";
    const textarea = document.createElement("textarea");
    textarea.className = "session-input";
    textarea.placeholder = "Message… (Enter to send, Ctrl+Enter for newline)";
    textarea.rows = 1;
    const sendBtn = document.createElement("button");
    sendBtn.className = "session-send-btn";
    sendBtn.type = "button";
    sendBtn.textContent = "Send";
    inputArea.appendChild(textarea);
    inputArea.appendChild(sendBtn);
    pane.appendChild(inputArea);

    document.getElementById("messages-container").appendChild(pane);

    const st = {
      paName, sessionId,
      isTA: false,
      ws: null, pane, messagesEl,
      streamingBubble: null, thinkStreamingBubble: null, waitingIndicator: null,
      hiddenRoles: new Set(), msgCount: 0, intentionalClose: false,
    };
    sessionStates.set(key, st);

    sendBtn.addEventListener("click", () => submitInput(st));
    textarea.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.ctrlKey && !e.metaKey) { e.preventDefault(); submitInput(st); }
      autoResize(textarea);
    });
    textarea.addEventListener("input", () => autoResize(textarea));
  }
  return sessionStates.get(key);
}

function getState(paName, sessionId) {
  return sessionStates.get(sessionKey(paName, sessionId)) || null;
}

// ── TA session state ───────────────────────────────────────────────────────

function getOrCreateTAState(paName, paSessionId, taSessionId, agentName) {
  const key = taSessionKey(paName, paSessionId, taSessionId);
  if (!sessionStates.has(key)) {
    const pane = document.createElement("div");
    pane.className = "session-pane";

    const messagesEl = document.createElement("div");
    messagesEl.className = "session-messages";
    pane.appendChild(messagesEl);

    const inputArea = document.createElement("div");
    inputArea.className = "session-input-area ta-direct-input";
    const textarea = document.createElement("textarea");
    textarea.className = "session-input";
    textarea.placeholder = "Ask the TA directly… (Enter to send)";
    textarea.rows = 1;
    const sendBtn = document.createElement("button");
    sendBtn.className = "session-send-btn";
    sendBtn.textContent = "Ask";
    inputArea.appendChild(textarea);
    inputArea.appendChild(sendBtn);
    pane.appendChild(inputArea);

    document.getElementById("messages-container").appendChild(pane);

    const taSt = {
      paName, paSessionId, sessionId: taSessionId, agentName,
      isTA: true,
      pane, messagesEl,
      streamingBubble: null, thinkStreamingBubble: null, waitingIndicator: null,
      hiddenRoles: new Set(), msgCount: 0,
    };
    sessionStates.set(key, taSt);

    const submitDirect = () => {
      const text = textarea.value.trim();
      if (!text) return;
      textarea.value = "";
      autoResize(textarea);
      createBubble(taSt, "user", text, false);
      if (text.startsWith("/")) { handleTACommand(taSt, text); return; }
      showWaitingIndicator(taSt);
      sendWS(taSt, { type: "ta_direct_question", agent_name: agentName, text });
    };
    sendBtn.addEventListener("click", submitDirect);
    textarea.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.ctrlKey && !e.metaKey) { e.preventDefault(); submitDirect(); }
      autoResize(textarea);
    });
    textarea.addEventListener("input", () => autoResize(textarea));
  }
  return sessionStates.get(key);
}

function destroyTASessionsFor(paName, paSessionId) {
  const prefix = `${paName}/${paSessionId}/`;
  for (const key of [...sessionStates.keys()]) {
    if (key.startsWith(prefix)) {
      sessionStates.get(key).pane.remove();
      sessionStates.delete(key);
    }
  }
  if (activeKey && activeKey.startsWith(prefix)) {
    activeKey = null;
    document.getElementById("chat-title").textContent = "Select or start a session";
  }
}

function destroyState(paName, sessionId) {
  const key = sessionKey(paName, sessionId);
  const st = sessionStates.get(key);
  if (st) {
    st.intentionalClose = true;
    if (st.ws) { st.ws.close(); st.ws = null; }
    st.pane.remove();
    sessionStates.delete(key);
    sessionNumbers.delete(key);
    destroyTASessionsFor(paName, sessionId);
  }
}

function destroyPA(paName) {
  for (const key of [...sessionStates.keys()]) {
    if (key.startsWith(paName + "/")) {
      const st = sessionStates.get(key);
      if (!st.isTA) {
        st.intentionalClose = true;
        if (st.ws) { st.ws.close(); st.ws = null; }
        sessionNumbers.delete(key);
      }
      st.pane.remove();
      sessionStates.delete(key);
    }
  }
  paCounters.delete(paName);
  if (activeKey && activeKey.startsWith(paName + "/")) {
    activeKey = null;
    document.getElementById("chat-title").textContent = "Select or start a session";
  }
}

function assignNumber(paName, sessionId) {
  const key = sessionKey(paName, sessionId);
  if (!sessionNumbers.has(key)) {
    const n = (paCounters.get(paName) || 0) + 1;
    paCounters.set(paName, n);
    sessionNumbers.set(key, n);
  }
  return sessionNumbers.get(key);
}

// ── WebSocket ──────────────────────────────────────────────────────────────

function connect(paName, sessionId) {
  const st = getOrCreateState(paName, sessionId);
  activateKey(sessionKey(paName, sessionId));

  if (st.ws && st.ws.readyState === WebSocket.OPEN) return;
  if (st.ws && st.ws.readyState === WebSocket.CONNECTING) return;

  if (st.ws) { st.intentionalClose = true; st.ws.close(); st.ws = null; }

  const url = `ws://${location.host}/ws/${encodeURIComponent(paName)}/${encodeURIComponent(sessionId)}`;
  const ws = new WebSocket(url);
  st.ws = ws;

  ws.onopen = () => {
    activateKey(sessionKey(paName, sessionId));
    const s = getState(paName, sessionId);
    // Show waiting dots while the server generates the initial greeting
    if (s && s.messagesEl.children.length === 0) showWaitingIndicator(s);
  };

  ws.onmessage = (evt) => {
    const st = getState(paName, sessionId);
    if (!st) return;
    try { handleMessage(st, JSON.parse(evt.data)); }
    catch (e) { console.error("WS parse error", e); }
  };

  ws.onclose = () => {
    const st = getState(paName, sessionId);
    if (!st) return;
    if (st.intentionalClose) { st.intentionalClose = false; return; }
    clearSessionMessages(st);
    createBubble(st, "system", "Session restarted. Reconnecting…", false);
    setTimeout(() => connect(paName, sessionId), 2000);
  };
}

function sendWS(st, obj) {
  // Always send over the PA session's WS — TA sessions share it
  let ws = st.ws;
  if (!ws && st.isTA) {
    const paKey = sessionKey(st.paName, st.paSessionId);
    ws = sessionStates.get(paKey)?.ws || null;
  }
  if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(obj));
}

// ── Pane activation ────────────────────────────────────────────────────────

function activateKey(key) {
  activeKey = key;
  document.querySelectorAll(".session-pane").forEach(p => p.classList.remove("active-pane"));
  const st = sessionStates.get(key);
  if (st) {
    st.pane.classList.add("active-pane");
    scrollToBottom(st);
    const input = st.pane.querySelector(".session-input:not(:disabled), .ta-input-field:not(:disabled)");
    if (input) requestAnimationFrame(() => input.focus());
  }
  // Update title bar
  const parts = key.split("/");
  if (parts.length === 3) {
    const agentName = st ? st.agentName : parts[2];
    const num = sessionNumbers.get(sessionKey(parts[0], parts[1])) || parts[1];
    document.getElementById("chat-title").textContent = `${parts[0]} — Session ${num} — ${agentName}`;
  } else {
    const num = sessionNumbers.get(key) || "";
    document.getElementById("chat-title").textContent =
      num ? `${parts[0]} — Session ${num}` : `${parts[0]} — ${parts[1]}`;
  }
  highlightActive();
}

function switchActive(paName, sessionId) { activateKey(sessionKey(paName, sessionId)); }
function switchToTA(paName, paSessionId, taSessionId) {
  activateKey(taSessionKey(paName, paSessionId, taSessionId));
}

// ── Message handling ───────────────────────────────────────────────────────

const SERVER_START_KEY = "r2_server_start";
const LAST_SESSION_KEY  = "r2_last_session";

function handleMessage(st, msg) {
  if (msg.type === "server_info") {
    const stored = localStorage.getItem(SERVER_START_KEY);
    const current = String(msg.start_time);
    localStorage.setItem(SERVER_START_KEY, current);
    if (stored && stored !== current) {
      // Save current active PA session so it auto-restores after reload
      if (activeKey && !isTA(activeKey)) localStorage.setItem(LAST_SESSION_KEY, activeKey);
      location.reload();
    }
    return;
  }
  if (msg.type === "transcript") { openTranscript(msg.html); return; }
  if (msg.type === "new_session") { addSession(st.paName); return; }
  if (msg.type === "visibility") { applyVisibility(st, msg.role, msg.hidden); return; }
  if (msg.type === "ta_sessions") { updateTASessions(st, msg.sessions); return; }
  if (msg.type === "ta_message") { handleTAMessage(st, msg); return; }
  if (msg.type === "ta_input_needed") { handleTAInputNeeded(st, msg); return; }
  if (msg.type === "close_session") {
    destroyState(st.paName, st.sessionId);
    if (activeKey === sessionKey(st.paName, st.sessionId)) {
      activeKey = null;
      document.getElementById("chat-title").textContent = "Select or start a session";
    }
    loadTree();
    return;
  }
  if (msg.type === "reset_session") {
    const { paName, sessionId } = st;
    st.intentionalClose = true;
    if (st.ws) { st.ws.close(); st.ws = null; }
    clearSessionMessages(st);
    createBubble(st, "system", "Session reset. Reconnecting…", false);
    setTimeout(() => connect(paName, sessionId), 500);
    return;
  }
  if (msg.type === "message") { handleChatMessage(st, msg); }
}

// ── Tree ───────────────────────────────────────────────────────────────────

async function loadTree() {
  const res = await fetch("/api/tree");
  renderTree(await res.json());
}

function renderTree(tree) {
  const ul = document.getElementById("pa-list");
  ul.innerHTML = "";
  for (const pa of tree) {
    for (const sess of pa.sessions) assignNumber(pa.pa_name, sess.session_id);
    ul.appendChild(makePANode(pa));
  }
}

function makePANode(pa) {
  const li = document.createElement("li");
  li.className = "pa-entry";

  const header = document.createElement("div");
  header.className = "pa-header";

  const toggle = document.createElement("span");
  toggle.className = "pa-toggle";
  toggle.textContent = "▾";
  toggle.addEventListener("click", () => li.classList.toggle("collapsed"));

  const label = document.createElement("span");
  label.className = "pa-label";
  label.textContent = pa.pa_name;

  const addBtn = document.createElement("button");
  addBtn.className = "icon-btn"; addBtn.type = "button"; addBtn.title = "New Session";
  addBtn.textContent = "+";
  addBtn.addEventListener("click", (e) => { e.stopPropagation(); addSession(pa.pa_name); });

  const delBtn = document.createElement("button");
  delBtn.className = "delete-btn"; delBtn.type = "button"; delBtn.title = "Delete assistant";
  delBtn.textContent = "×";
  delBtn.addEventListener("click", (e) => { e.stopPropagation(); deletePA(pa.pa_name); });

  header.append(toggle, label, addBtn, delBtn);
  li.appendChild(header);

  const sessUl = document.createElement("ul");
  sessUl.className = "session-list";
  for (const sess of pa.sessions) sessUl.appendChild(makeSessionNode(pa.pa_name, sess));
  li.appendChild(sessUl);
  return li;
}

function makeSessionNode(paName, sess) {
  const key = sessionKey(paName, sess.session_id);
  const num = sessionNumbers.get(key) || "?";

  // Wrapper li — contains the clickable row + optional TA list below
  const li = document.createElement("li");

  // Clickable row (flex)
  const row = document.createElement("div");
  row.className = "session-entry";
  row.dataset.pa = paName;
  row.dataset.id = sess.session_id;
  if (key === activeKey) row.classList.add("active");

  const label = document.createElement("span");
  label.className = "session-label";
  label.textContent = `Session ${num}`;
  row.appendChild(label);

  const badge = document.createElement("span");
  badge.className = "msg-count";
  const st = sessionStates.get(key);
  badge.textContent = st && st.msgCount > 0 ? String(st.msgCount) : "";
  row.appendChild(badge);

  const delBtn = document.createElement("button");
  delBtn.className = "delete-btn"; delBtn.type = "button"; delBtn.title = "Delete session";
  delBtn.textContent = "×";
  delBtn.addEventListener("click", (e) => { e.stopPropagation(); deleteSession(paName, sess.session_id); });
  row.appendChild(delBtn);

  row.addEventListener("click", () => connect(paName, sess.session_id));
  li.appendChild(row);

  if (sess.ta_sessions && sess.ta_sessions.length > 0) {
    li.appendChild(makeTAList(sess.ta_sessions, paName, sess.session_id));
  }
  return li;
}

function makeTAList(taSessions, paName, paSessionId) {
  const ul = document.createElement("ul");
  ul.className = "ta-list";
  for (const ta of taSessions) {
    const taKey = taSessionKey(paName, paSessionId, ta.ta_session_id);
    const li = document.createElement("li");
    li.className = "ta-entry";
    li.dataset.taKey = taKey;
    if (ta.status === "pending") li.classList.add("ta-pending");
    else if (ta.status === "done") li.classList.add("ta-done");
    if (taKey === activeKey) li.classList.add("active");

    const nameSpan = document.createElement("span");
    nameSpan.className = "ta-name";
    nameSpan.textContent = ta.agent_name;
    li.appendChild(nameSpan);

    const badge = document.createElement("span");
    badge.className = "msg-count";
    const taSt = sessionStates.get(taKey);
    badge.textContent = taSt && taSt.msgCount > 0 ? String(taSt.msgCount) : "";
    li.appendChild(badge);

    li.addEventListener("click", (e) => {
      e.stopPropagation();
      switchToTA(paName, paSessionId, ta.ta_session_id);
    });
    ul.appendChild(li);
  }
  return ul;
}

function highlightActive() {
  document.querySelectorAll("#pa-list .session-entry").forEach(li => {
    li.classList.toggle("active", sessionKey(li.dataset.pa, li.dataset.id) === activeKey);
  });
  document.querySelectorAll("#pa-list .ta-entry").forEach(li => {
    li.classList.toggle("active", li.dataset.taKey === activeKey);
  });
}

function updateTASessions(st, taSessions) {
  // Create/update TA session states
  for (const ta of taSessions) {
    const taSt = getOrCreateTAState(st.paName, st.sessionId, ta.ta_session_id, ta.agent_name);
    taSt.status = ta.status;
  }
  // Rebuild sidebar TA list under this session's wrapper li
  const sessRow = document.querySelector(
    `#pa-list .session-entry[data-pa="${st.paName}"][data-id="${st.sessionId}"]`
  );
  if (!sessRow) return;
  const sessLi = sessRow.parentElement;
  const existing = sessLi.querySelector(".ta-list");
  if (existing) existing.remove();
  if (taSessions.length > 0) {
    sessLi.appendChild(makeTAList(taSessions, st.paName, st.sessionId));
  }
}

// ── TA message routing ─────────────────────────────────────────────────────

function handleTAMessage(paSt, msg) {
  const { ta_session_id, agent_name, role, text } = msg;
  const partial = msg.partial;  // true | false | undefined
  const taSt = sessionStates.get(taSessionKey(paSt.paName, paSt.sessionId, ta_session_id));
  if (!taSt) return;

  if (role === "system") {
    createBubble(taSt, "system", text, false);
    return;
  }

  if (role === "question") {
    createBubble(taSt, "question", text, false, `→ ${paSt.paName}`);
    return;
  }

  if (role === "answer") {
    if (partial === true) {
      if (!taSt.streamingBubble) {
        hideWaitingIndicator(taSt);
        taSt.streamingBubble = createBubble(taSt, "answer");
        taSt.streamingBubble.el.classList.add("streaming");
        taSt.streamingBubble.textAccum = "";
      }
      taSt.streamingBubble.textAccum += text;
      renderBubbleContent(taSt.streamingBubble.el, taSt.streamingBubble.textAccum, true);
    } else if (partial === false) {
      if (taSt.streamingBubble) {
        if (taSt.streamingBubble.textAccum.trim()) {
          taSt.streamingBubble.el.classList.remove("streaming");
          renderBubbleContent(taSt.streamingBubble.el, taSt.streamingBubble.textAccum, false);
        } else {
          taSt.streamingBubble.el.remove();
        }
        taSt.streamingBubble = null;
      }
    } else {
      hideWaitingIndicator(taSt);
      createBubble(taSt, "answer", text, false, `← ${agent_name}`);
    }
  }
}

function handleTAInputNeeded(paSt, msg) {
  hideWaitingIndicator(paSt);
  const { agent_name, ta_session_id, question } = msg;
  const taSt = getOrCreateTAState(paSt.paName, paSt.sessionId, ta_session_id, agent_name);

  // Build the input bubble in the TA session pane
  const wrap = document.createElement("div");
  wrap.className = "bubble-wrap left ta-input expanded";
  const bubble = document.createElement("div");
  bubble.className = "bubble";

  const content = document.createElement("div");
  content.className = "bubble-content";

  const row = document.createElement("div");
  row.className = "ta-input-row";

  const textarea = document.createElement("textarea");
  textarea.className = "ta-input-field";
  textarea.placeholder = "Type your answer…";
  textarea.rows = 1;
  textarea.addEventListener("input", () => autoResize(textarea));
  textarea.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.ctrlKey && !e.metaKey) { e.preventDefault(); submitTA(); }
    autoResize(textarea);
  });

  const btn = document.createElement("button");
  btn.className = "ta-input-submit"; btn.type = "button"; btn.textContent = "Answer";

  const submitTA = () => {
    const text = textarea.value.trim();
    if (!text) return;
    sendWS(taSt, { type: "ta_input", ta_session_id, text });
    textarea.disabled = true;
    btn.disabled = true;
    const done = document.createElement("div");
    done.className = "ta-input-answered";
    done.textContent = `Answered: ${text}`;
    content.appendChild(done);
    // Switch back to PA pane so the user sees the PA's response
    activateKey(sessionKey(paSt.paName, paSt.sessionId));
    showWaitingIndicator(paSt);
  };

  btn.addEventListener("click", submitTA);
  row.appendChild(textarea);
  row.appendChild(btn);
  content.appendChild(row);
  bubble.appendChild(content);
  wrap.appendChild(bubble);
  taSt.messagesEl.appendChild(wrap);
  scrollToBottom(taSt);
  taSt.msgCount++;
  // No badge for TA sessions

  // Auto-switch to the TA session so the user sees the prompt
  switchToTA(paSt.paName, paSt.sessionId, ta_session_id);
  requestAnimationFrame(() => textarea.focus());
}

// ── PA / session creation ──────────────────────────────────────────────────

async function createPA(name) {
  let res;
  try {
    res = await fetch("/api/pas", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    });
  } catch (e) { showGlobalError(`Failed to create assistant: ${e.message}`); return; }
  if (!res.ok) {
    showGlobalError(`Failed to create assistant "${name}": ${res.status} ${await res.text()}`);
    return;
  }
  await addSession(name);
}

async function addSession(paName) {
  const res = await fetch(`/api/${encodeURIComponent(paName)}/sessions`, { method: "POST" });
  if (!res.ok) {
    showGlobalError(`Failed to create session for "${paName}": ${res.status} ${await res.text()}`);
    return;
  }
  const { session_id } = await res.json();
  assignNumber(paName, session_id);
  await loadTree();
  getOrCreateState(paName, session_id);
  connect(paName, session_id);
}

async function deleteSession(paName, sessionId) {
  if (!confirm(`Delete session "${sessionId}"?`)) return;
  const res = await fetch(
    `/api/${encodeURIComponent(paName)}/sessions/${encodeURIComponent(sessionId)}`,
    { method: "DELETE" });
  if (!res.ok) {
    showGlobalError(`Failed to delete session: ${res.status} ${await res.text()}`); return;
  }
  const wasActive = activeKey === sessionKey(paName, sessionId) ||
                    (activeKey && activeKey.startsWith(`${paName}/${sessionId}/`));
  destroyState(paName, sessionId);
  if (wasActive) {
    activeKey = null;
    document.getElementById("chat-title").textContent = "Select or start a session";
  }
  await loadTree();
}

async function deletePA(paName) {
  if (!confirm(`Delete assistant "${paName}" and all its sessions?`)) return;
  const res = await fetch(`/api/pas/${encodeURIComponent(paName)}`, { method: "DELETE" });
  if (!res.ok) {
    showGlobalError(`Failed to delete assistant: ${res.status} ${await res.text()}`); return;
  }
  const hadActive = activeKey && activeKey.startsWith(paName + "/");
  destroyPA(paName);
  if (hadActive) {
    activeKey = null;
    document.getElementById("chat-title").textContent = "Select or start a session";
  }
  await loadTree();
}

function showGlobalError(text) {
  const key = activeKey;
  if (key) {
    const st = sessionStates.get(key);
    if (st && !st.isTA) { createBubble(st, "error", text, true, "Error"); return; }
  }
  alert(text);
}

// ── Chat message handling ──────────────────────────────────────────────────

function applyVisibility(st, role, hide) {
  if (hide) st.hiddenRoles.add(role); else st.hiddenRoles.delete(role);
  st.messagesEl.querySelectorAll(`.bubble-wrap.${role}`).forEach(el => {
    el.classList.toggle("role-hidden", hide);
  });
}

function showWaitingIndicator(st) {
  if (st.waitingIndicator) return;
  const wrap = document.createElement("div");
  wrap.className = "bubble-wrap left";
  const bubble = document.createElement("div");
  bubble.className = "bubble";
  bubble.innerHTML = '<span class="waiting-dots"><span></span><span></span><span></span></span>';
  wrap.appendChild(bubble);
  st.messagesEl.appendChild(wrap);
  scrollToBottom(st);
  st.waitingIndicator = wrap;
}

function hideWaitingIndicator(st) {
  if (st.waitingIndicator) { st.waitingIndicator.remove(); st.waitingIndicator = null; }
}

function handleChatMessage(st, msg) {
  hideWaitingIndicator(st);
  const role = msg.role || "system";
  const partial = msg.partial === true;
  const text = msg.text || "";

  if (role === "assistant") {
    if (partial) {
      if (!st.streamingBubble) {
        st.streamingBubble = createBubble(st, "assistant");
        st.streamingBubble.el.classList.add("streaming");
        st.streamingBubble.textAccum = "";
      }
      st.streamingBubble.textAccum += text;
      renderBubbleContent(st.streamingBubble.el, st.streamingBubble.textAccum, true);
    } else {
      if (st.streamingBubble) {
        st.streamingBubble.el.classList.remove("streaming");
        renderBubbleContent(st.streamingBubble.el, st.streamingBubble.textAccum, false);
        st.streamingBubble = null;
      } else if (text) {
        // Replayed non-streaming message from msg_log
        createBubble(st, "assistant", text, false);
      }
    }
    return;
  }

  if (role === "think") {
    if (partial) {
      if (!st.thinkStreamingBubble) {
        st.thinkStreamingBubble = createBubble(st, "think", "", true, "Thinking");
        st.thinkStreamingBubble.el.classList.add("expanded");
        st.thinkStreamingBubble.textAccum = "";
      }
      st.thinkStreamingBubble.textAccum += text;
      renderBubbleContent(st.thinkStreamingBubble.el, st.thinkStreamingBubble.textAccum, true);
    } else {
      if (st.thinkStreamingBubble) {
        st.thinkStreamingBubble.el.classList.remove("expanded");
        renderBubbleContent(st.thinkStreamingBubble.el, st.thinkStreamingBubble.textAccum, false);
        st.thinkStreamingBubble = null;
      } else if (text) {
        // Replayed non-streaming think bubble from msg_log
        createBubble(st, "think", text, true, "Thinking");
      }
    }
    return;
  }

  const collapsible = ["think", "note", "remember", "fyi", "question", "answer", "error"].includes(role);
  const b = createBubble(st, role, text, collapsible, roleLabel(role, msg));
}

// ── Bubble creation ────────────────────────────────────────────────────────

function createBubble(st, role, text = "", collapsible = false, toggleLabel = null) {
  const isUser = role === "user";
  const wrap = document.createElement("div");
  wrap.className = `bubble-wrap ${isUser ? "user" : "left"} ${role}`;
  if (collapsible) wrap.classList.add("collapsible");
  if (st.hiddenRoles && st.hiddenRoles.has(role)) wrap.classList.add("role-hidden");

  const bubble = document.createElement("div");
  bubble.className = "bubble";

  if (collapsible && toggleLabel !== null) {
    const toggle = document.createElement("div");
    toggle.className = "bubble-toggle";
    toggle.addEventListener("click", () => wrap.classList.toggle("expanded"));
    const labelEl = document.createElement("span");
    labelEl.textContent = toggleLabel;
    const snippetEl = document.createElement("em");
    snippetEl.className = "bubble-snippet";
    toggle.appendChild(labelEl);
    toggle.appendChild(snippetEl);
    bubble.appendChild(toggle);
  }

  const content = document.createElement("div");
  content.className = "bubble-content";
  bubble.appendChild(content);
  wrap.appendChild(bubble);

  st.messagesEl.appendChild(wrap);
  scrollToBottom(st);
  if (text) renderBubbleContent(wrap, text, false);

  if (role !== "think") {
    st.msgCount++;
    if (st.isTA) updateTABadge(st);
    else updateSessionBadge(st);
  }

  return { el: wrap, content, textAccum: "" };
}

function renderBubbleContent(wrapEl, text, streaming) {
  const content = wrapEl.querySelector(".bubble-content");
  if (!content) return;
  try { content.innerHTML = marked.parse(text); }
  catch { content.textContent = text; }
  const snippetEl = wrapEl.querySelector(".bubble-snippet");
  if (snippetEl) {
    const line = (text.split(/\n/)
      .map(l => l.replace(/^#{1,6}\s+/, "").replace(/[*_`~[\]()]/g, "").trim())
      .find(l => l.length > 0) || "");
    snippetEl.textContent = line.length > 80 ? line.slice(0, 80) + "…" : line;
  }
  if (streaming) {
    const msgEl = wrapEl.closest(".session-messages");
    if (msgEl) msgEl.scrollTop = msgEl.scrollHeight;
  }
}

function roleLabel(role, msg) {
  const labels = {
    think: "Thinking", note: "Note", remember: "Remembered",
    fyi: msg.name ? `FYI: ${msg.name}` : "FYI",
    error: "Error",
    question: msg.name ? `→ ${msg.name}` : "Question",
    answer: msg.name ? `← ${msg.name}` : "Answer",
    system: null,
  };
  return labels[role] ?? role;
}

// ── Input ──────────────────────────────────────────────────────────────────

document.addEventListener("DOMContentLoaded", async () => {
  const dialog = document.getElementById("new-pa-dialog");
  document.getElementById("new-pa-btn").addEventListener("click", () => {
    document.getElementById("new-pa-name-input").value = "";
    dialog.showModal();
  });
  document.getElementById("new-pa-cancel").addEventListener("click", () => dialog.close());
  document.getElementById("new-pa-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const name = document.getElementById("new-pa-name-input").value.trim();
    if (!name) return;
    dialog.close();
    await createPA(name);
  });

  document.getElementById("sidebar-toggle").addEventListener("click", () => {
    const collapsed = document.getElementById("app").classList.toggle("sidebar-collapsed");
    document.getElementById("sidebar-toggle").textContent = collapsed ? "▶" : "◀";
  });

  await loadTree();

  // Restore the session that was active before a server-restart reload
  const lastKey = localStorage.getItem(LAST_SESSION_KEY);
  if (lastKey) {
    const parts = lastKey.split("/");
    if (parts.length === 2) connect(parts[0], parts[1]);
    localStorage.removeItem(LAST_SESSION_KEY);
  }
});

const _TA_SHOWABLE_ROLES = ["question", "answer", "user", "system"];

function handleTACommand(taSt, text) {
  const parts = text.trim().split(/\s+/);
  const cmd = parts[0].toLowerCase();
  const reply = (msg) => createBubble(taSt, "system", msg, false);

  if (cmd === "/help") {
    reply("Available commands:\n\n| Command | Description |\n|---|---|\n" +
      "| `/help` | Show this message |\n" +
      "| `/prompt` | Show the TA system prompt |\n" +
      "| `/status` | Show agent name and session ID |\n" +
      "| `/show [type]` | Show a bubble type |\n" +
      "| `/hide [type]` | Hide a bubble type |\n" +
      "| `/reset` | Clear all messages |");

  } else if (cmd === "/prompt") {
    fetch(`/api/ta/${encodeURIComponent(taSt.agentName)}/prompt`)
      .then(r => r.json())
      .then(data => {
        if (data.prompt) reply(`**System Prompt**\n\n\`\`\`\n${data.prompt}\n\`\`\``);
        else reply(`No system prompt found for \`${taSt.agentName}\`.`);
      })
      .catch(() => reply("Failed to fetch system prompt."));

  } else if (cmd === "/status") {
    reply(`Agent: **${taSt.agentName}**  Session: \`${taSt.sessionId}\``);

  } else if (cmd === "/show" || cmd === "/hide") {
    const hide = cmd === "/hide";
    const role = parts[1]?.toLowerCase();
    if (role) {
      if (!_TA_SHOWABLE_ROLES.includes(role)) {
        reply(`Unknown type: \`${role}\`. Valid types: ${_TA_SHOWABLE_ROLES.map(r => `\`${r}\``).join(", ")}`);
      } else {
        applyVisibility(taSt, role, hide);
        reply(`${hide ? "Hiding" : "Showing"} \`${role}\` bubbles.`);
      }
    } else {
      const rows = _TA_SHOWABLE_ROLES
        .map(r => `| \`${r}\` | ${taSt.hiddenRoles.has(r) ? "hidden" : "shown"} |`)
        .join("\n");
      reply(`Bubble visibility:\n\n| Type | Status |\n|---|---|\n${rows}`);
    }

  } else if (cmd === "/reset") {
    clearSessionMessages(taSt);

  } else {
    reply(`Unknown command: \`${cmd}\`. Type \`/help\` for available commands.`);
  }
}

function submitInput(st) {
  const input = st.pane.querySelector(".session-input");
  const text = input.value.trim();
  if (!text || !st.ws || st.ws.readyState !== WebSocket.OPEN) return;
  createBubble(st, "user", text, false);
  if (!text.startsWith("/")) showWaitingIndicator(st);
  sendWS(st, { type: "message", text });
  input.value = "";
  autoResize(input);
}

function autoResize(el) {
  el.style.height = "auto";
  el.style.height = Math.min(el.scrollHeight, 140) + "px";
}

function updateTABadge(taSt) {
  const taKey = taSessionKey(taSt.paName, taSt.paSessionId, taSt.sessionId);
  const li = document.querySelector(`#pa-list .ta-entry[data-ta-key="${taKey}"]`);
  if (!li) return;
  let badge = li.querySelector(".msg-count");
  if (!badge) {
    badge = document.createElement("span");
    badge.className = "msg-count";
    li.appendChild(badge);
  }
  badge.textContent = taSt.msgCount > 0 ? String(taSt.msgCount) : "";
}

function updateSessionBadge(st) {
  const li = document.querySelector(
    `#pa-list .session-entry[data-pa="${st.paName}"][data-id="${st.sessionId}"]`
  );
  if (!li) return;
  const badge = li.querySelector(".msg-count");
  if (badge) badge.textContent = st.msgCount > 0 ? String(st.msgCount) : "";
}

function clearSessionMessages(st) {
  st.messagesEl.innerHTML = "";
  st.streamingBubble = null;
  st.thinkStreamingBubble = null;
  st.waitingIndicator = null;
  if (st.hiddenRoles) st.hiddenRoles.clear();
  st.msgCount = 0;
  if (st.isTA) updateTABadge(st);
  else updateSessionBadge(st);
}

function scrollToBottom(st) {
  st.messagesEl.scrollTop = st.messagesEl.scrollHeight;
}

function openTranscript(html) {
  const win = window.open("", "_blank");
  win.document.write(`<html><head><title>Transcript</title></head><body>${html}</body></html>`);
  win.document.close();
}
