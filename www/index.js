/* R2 Web UI */

let PA_NAME = "";
let ws = null;
let currentSessionId = null;
let streamingBubble = null;
let thinkStreamingBubble = null;
let waitingIndicator = null;
const hiddenRoles = new Set();
let intentionalClose = false;

// ── WebSocket ──────────────────────────────────────────────────────────────

function connect(paName, sessionId) {
  if (ws) {
    intentionalClose = true;
    ws.close();
  }
  PA_NAME = paName;
  currentSessionId = sessionId;
  const url = `ws://${location.host}/ws/${encodeURIComponent(paName)}/${encodeURIComponent(sessionId)}`;
  ws = new WebSocket(url);

  ws.onopen = () => {
    document.getElementById("chat-title").textContent = `${paName} — ${sessionId}`;
    highlightActiveSession();
    document.getElementById("input").focus();
  };

  ws.onmessage = (evt) => {
    try { handleMessage(JSON.parse(evt.data)); }
    catch (e) { console.error("WS parse error", e); }
  };

  ws.onclose = () => {
    if (intentionalClose) {
      intentionalClose = false;
      return;
    }
    clearMessages();
    createBubble("system", "Session restarted. Reconnecting…", false);
    setTimeout(() => connect(PA_NAME, currentSessionId), 2000);
  };
}

function send(obj) {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify(obj));
  }
}

// ── Message handling ───────────────────────────────────────────────────────

function handleMessage(msg) {
  if (msg.type === "server_info") {
    const key = "r2_server_start";
    const stored = localStorage.getItem(key);
    const current = String(msg.start_time);
    localStorage.setItem(key, current);
    if (stored && stored !== current) location.reload();
    return;
  }
  if (msg.type === "new_session") {
    addSession(PA_NAME);
    return;
  }
  if (msg.type === "transcript") {
    openTranscript(msg.html);
    return;
  }
  if (msg.type === "visibility") {
    applyVisibility(msg.role, msg.hidden);
    return;
  }
  if (msg.type === "ta_sessions") {
    updateTASessions(currentSessionId, msg.sessions);
    return;
  }
  if (msg.type === "message") {
    handleChatMessage(msg);
  }
}

// ── Tree ───────────────────────────────────────────────────────────────────

async function loadTree() {
  const res = await fetch("/api/tree");
  const tree = await res.json();
  renderTree(tree);
}

function renderTree(tree) {
  const ul = document.getElementById("pa-list");
  ul.innerHTML = "";
  for (const pa of tree) {
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
  addBtn.className = "icon-btn";
  addBtn.type = "button";
  addBtn.title = "New Session";
  addBtn.textContent = "+";
  addBtn.addEventListener("click", (e) => { e.stopPropagation(); addSession(pa.pa_name); });

  const delBtn = document.createElement("button");
  delBtn.className = "delete-btn";
  delBtn.type = "button";
  delBtn.title = "Delete assistant";
  delBtn.textContent = "×";
  delBtn.addEventListener("click", (e) => { e.stopPropagation(); deletePA(pa.pa_name); });

  header.appendChild(toggle);
  header.appendChild(label);
  header.appendChild(addBtn);
  header.appendChild(delBtn);
  li.appendChild(header);

  const sessUl = document.createElement("ul");
  sessUl.className = "session-list";
  for (const sess of pa.sessions) {
    sessUl.appendChild(makeSessionNode(pa.pa_name, sess));
  }
  li.appendChild(sessUl);

  return li;
}

function makeSessionNode(paName, sess) {
  const li = document.createElement("li");
  li.className = "session-entry";
  li.dataset.pa = paName;
  li.dataset.id = sess.session_id;
  if (paName === PA_NAME && sess.session_id === currentSessionId) {
    li.classList.add("active");
  }

  const label = document.createElement("span");
  label.className = "session-label";
  label.textContent = sess.session_id;
  li.appendChild(label);

  const delBtn = document.createElement("button");
  delBtn.className = "delete-btn";
  delBtn.type = "button";
  delBtn.title = "Delete session";
  delBtn.textContent = "×";
  delBtn.addEventListener("click", (e) => { e.stopPropagation(); deleteSession(paName, sess.session_id); });
  li.appendChild(delBtn);

  li.addEventListener("click", () => {
    clearMessages();
    connect(paName, sess.session_id);
  });

  if (sess.ta_sessions && sess.ta_sessions.length > 0) {
    li.appendChild(makeTAList(sess.ta_sessions));
  }

  return li;
}

function makeTAList(taSessions) {
  const ul = document.createElement("ul");
  ul.className = "ta-list";
  for (const ta of taSessions) {
    const li = document.createElement("li");
    li.className = "ta-entry";
    li.textContent = ta.agent_name;
    ul.appendChild(li);
  }
  return ul;
}

function highlightActiveSession() {
  document.querySelectorAll("#pa-list .session-entry").forEach(li => {
    li.classList.toggle("active",
      li.dataset.pa === PA_NAME && li.dataset.id === currentSessionId);
  });
}

function updateTASessions(sessionId, taSessions) {
  const sessLi = document.querySelector(`#pa-list .session-entry[data-id="${sessionId}"]`);
  if (!sessLi) return;
  const existing = sessLi.querySelector(".ta-list");
  if (existing) existing.remove();
  if (taSessions.length > 0) sessLi.appendChild(makeTAList(taSessions));
}

// ── PA / session creation ──────────────────────────────────────────────────

async function createPA(name) {
  let res;
  try {
    res = await fetch("/api/pas", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    });
  } catch (e) {
    createBubble("error", `Failed to create assistant: ${e.message}`, false);
    return;
  }
  if (!res.ok) {
    const body = await res.text();
    createBubble("error", `Failed to create assistant "${name}": ${res.status} ${body}`, false);
    return;
  }
  await addSession(name);
}

async function addSession(paName) {
  const res = await fetch(`/api/${encodeURIComponent(paName)}/sessions`, { method: "POST" });
  if (!res.ok) {
    const body = await res.text();
    createBubble("error", `Failed to create session for "${paName}": ${res.status} ${body}`, false);
    return;
  }
  const { session_id } = await res.json();
  // Set active vars before loadTree so the new node renders highlighted
  PA_NAME = paName;
  currentSessionId = session_id;
  await loadTree();
  clearMessages();
  connect(paName, session_id);
}

async function deleteSession(paName, sessionId) {
  if (!confirm(`Delete session "${sessionId}"?`)) return;
  const res = await fetch(
    `/api/${encodeURIComponent(paName)}/sessions/${encodeURIComponent(sessionId)}`,
    { method: "DELETE" }
  );
  if (!res.ok) {
    const body = await res.text();
    createBubble("error", `Failed to delete session: ${res.status} ${body}`, false);
    return;
  }
  if (paName === PA_NAME && sessionId === currentSessionId) {
    if (ws) { intentionalClose = true; ws.close(); ws = null; }
    clearMessages();
    currentSessionId = null;
    document.getElementById("chat-title").textContent = "Select or start a session";
  }
  await loadTree();
}

async function deletePA(paName) {
  if (!confirm(`Delete assistant "${paName}" and all its sessions?`)) return;
  const res = await fetch(`/api/pas/${encodeURIComponent(paName)}`, { method: "DELETE" });
  if (!res.ok) {
    const body = await res.text();
    createBubble("error", `Failed to delete assistant: ${res.status} ${body}`, false);
    return;
  }
  if (paName === PA_NAME) {
    if (ws) { intentionalClose = true; ws.close(); ws = null; }
    clearMessages();
    PA_NAME = "";
    currentSessionId = null;
    document.getElementById("chat-title").textContent = "Select or start a session";
  }
  await loadTree();
}

// ── Chat message handling ──────────────────────────────────────────────────

function applyVisibility(role, hide) {
  if (hide) hiddenRoles.add(role); else hiddenRoles.delete(role);
  document.querySelectorAll(`#messages .bubble-wrap.${role}`).forEach(el => {
    el.classList.toggle("role-hidden", hide);
  });
}

function showWaitingIndicator() {
  if (waitingIndicator) return;
  const wrap = document.createElement("div");
  wrap.className = "bubble-wrap left";
  const bubble = document.createElement("div");
  bubble.className = "bubble";
  bubble.innerHTML = '<span class="waiting-dots"><span></span><span></span><span></span></span>';
  wrap.appendChild(bubble);
  document.getElementById("messages").appendChild(wrap);
  scrollToBottom();
  waitingIndicator = wrap;
}

function hideWaitingIndicator() {
  if (waitingIndicator) { waitingIndicator.remove(); waitingIndicator = null; }
}

function handleChatMessage(msg) {
  hideWaitingIndicator();
  const role = msg.role || "system";
  const partial = msg.partial === true;
  const text = msg.text || "";

  if (role === "assistant") {
    if (partial) {
      if (!streamingBubble) {
        streamingBubble = createBubble("assistant");
        streamingBubble.el.classList.add("streaming");
        streamingBubble.textAccum = "";
      }
      streamingBubble.textAccum += text;
      renderBubbleContent(streamingBubble.el, streamingBubble.textAccum, true);
    } else {
      if (streamingBubble) {
        streamingBubble.el.classList.remove("streaming");
        renderBubbleContent(streamingBubble.el, streamingBubble.textAccum, false);
        streamingBubble = null;
      }
    }
    return;
  }

  if (role === "think") {
    if (partial) {
      if (!thinkStreamingBubble) {
        thinkStreamingBubble = createBubble("think", "", true, "Thinking");
        thinkStreamingBubble.el.classList.add("expanded");
        thinkStreamingBubble.textAccum = "";
      }
      thinkStreamingBubble.textAccum += text;
      renderBubbleContent(thinkStreamingBubble.el, thinkStreamingBubble.textAccum, true);
    } else {
      if (thinkStreamingBubble) {
        thinkStreamingBubble.el.classList.remove("expanded");
        renderBubbleContent(thinkStreamingBubble.el, thinkStreamingBubble.textAccum, false);
        thinkStreamingBubble = null;
      }
    }
    return;
  }

  const collapsible = ["think", "note", "fyi", "question", "answer", "error"].includes(role);
  const label = roleLabel(role, msg);
  createBubble(role, text, collapsible, label);
}

// ── Bubble creation ────────────────────────────────────────────────────────

function createBubble(role, text = "", collapsible = false, toggleLabel = null) {
  const isUser = role === "user";
  const wrap = document.createElement("div");
  wrap.className = `bubble-wrap ${isUser ? "user" : "left"} ${role}`;
  if (collapsible) wrap.classList.add("collapsible");
  if (hiddenRoles.has(role)) wrap.classList.add("role-hidden");

  const bubble = document.createElement("div");
  bubble.className = "bubble";

  if (collapsible && toggleLabel !== null) {
    const toggle = document.createElement("div");
    toggle.className = "bubble-toggle";
    toggle.textContent = toggleLabel;
    toggle.addEventListener("click", () => wrap.classList.toggle("expanded"));
    bubble.appendChild(toggle);
  }

  const content = document.createElement("div");
  content.className = "bubble-content";
  bubble.appendChild(content);
  wrap.appendChild(bubble);

  document.getElementById("messages").appendChild(wrap);
  scrollToBottom();

  if (text) renderBubbleContent(wrap, text, false);

  return { el: wrap, content, textAccum: "" };
}

function renderBubbleContent(wrapEl, text, streaming) {
  const content = wrapEl.querySelector(".bubble-content");
  if (!content) return;
  try { content.innerHTML = marked.parse(text); }
  catch { content.textContent = text; }
  if (streaming) scrollToBottom();
}

function roleLabel(role, msg) {
  const labels = {
    think: "Thinking",
    note: "Note",
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
  // New PA dialog
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

  document.getElementById("send-btn").addEventListener("click", submitInput);
  const input = document.getElementById("input");
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.ctrlKey && !e.metaKey) {
      e.preventDefault();
      submitInput();
    }
    autoResize(input);
  });
  input.addEventListener("input", () => autoResize(input));

  await loadTree();
});

function submitInput() {
  const input = document.getElementById("input");
  const text = input.value.trim();
  if (!text || !ws || ws.readyState !== WebSocket.OPEN) return;

  createBubble("user", text, false);
  if (!text.startsWith("/")) showWaitingIndicator();
  send({ type: "message", text });
  input.value = "";
  autoResize(input);
}

function autoResize(el) {
  el.style.height = "auto";
  el.style.height = Math.min(el.scrollHeight, 140) + "px";
}

function clearMessages() {
  document.getElementById("messages").innerHTML = "";
  streamingBubble = null;
  thinkStreamingBubble = null;
  waitingIndicator = null;
  hiddenRoles.clear();
}

function scrollToBottom() {
  const el = document.getElementById("messages");
  el.scrollTop = el.scrollHeight;
}

function openTranscript(html) {
  const win = window.open("", "_blank");
  win.document.write(`<html><head><title>Transcript</title></head><body>${html}</body></html>`);
  win.document.close();
}
