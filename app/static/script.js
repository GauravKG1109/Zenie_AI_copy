let history = [];

// Load session from localStorage
window.onload = () => {
  let session = localStorage.getItem("sessionId");
  if (!session) {
    session = crypto.randomUUID();
    localStorage.setItem("sessionId", session);
  }
  document.getElementById("sessionId").value = session;
};

function generateSession() {
  const newId = crypto.randomUUID();
  localStorage.setItem("sessionId", newId);
  document.getElementById("sessionId").value = newId;

  history = [];
  document.getElementById("chatWindow").innerHTML = "";
  document.getElementById("sqlOutput").innerText = "— SQL will appear here after you send a message —";
  document.getElementById("intentOutput").innerHTML = '<span class="placeholder">— will populate after a message —</span>';
  document.getElementById("dateOutput").innerHTML = '<span class="placeholder">— will populate after a message —</span>';
  document.getElementById("logWindow").innerHTML = "";
  log("🔄 New session started");
}

function addMessage(role, text) {
  const chat = document.getElementById("chatWindow");
  const div = document.createElement("div");
  div.className = `message ${role}`;
  div.innerText = `${role.toUpperCase()}: ${text}`;
  chat.appendChild(div);
  chat.scrollTop = chat.scrollHeight;
}

function log(text) {
  const logWindow = document.getElementById("logWindow");
  const div = document.createElement("div");
  div.innerText = text;
  logWindow.appendChild(div);
  logWindow.scrollTop = logWindow.scrollHeight;
}

function copySQL() {
  const sql = document.getElementById("sqlOutput").innerText;
  navigator.clipboard.writeText(sql).then(() => {
    const btn = document.querySelector(".copy-btn");
    const original = btn.innerText;
    btn.innerText = "Copied ✓";
    setTimeout(() => { btn.innerText = original; }, 1500);
  });
}

function renderIntent(intent) {
  if (!intent || !intent.intent_code) {
    document.getElementById("intentOutput").innerHTML = '<span class="placeholder">No intent matched.</span>';
    return;
  }
  document.getElementById("intentOutput").innerHTML = `
    <div class="info-row-item"><span class="info-label">Code</span><span class="info-value code-tag">${intent.intent_code}</span></div>
    <div class="info-row-item"><span class="info-label">Name</span><span class="info-value">${intent.intent_name}</span></div>
    <div class="info-row-item"><span class="info-label">Category</span><span class="info-value">${intent.intent_category}</span></div>
    <div class="info-row-item"><span class="info-label">View</span><span class="info-value code-tag">${intent.view || '—'}</span></div>
    <div class="info-row-item"><span class="info-label">Match</span><span class="info-value">${intent.similarity}%</span></div>
    <div class="info-desc">${intent.description}</div>
  `;
}

function renderDate(dateRange) {
  if (!dateRange || !dateRange.primary) {
    document.getElementById("dateOutput").innerHTML = '<span class="placeholder">No date extracted.</span>';
    return;
  }
  const p = dateRange.primary;
  let html = `
    <div class="info-row-item"><span class="info-label">Label</span><span class="info-value">${p.label || '—'}</span></div>
    <div class="info-row-item"><span class="info-label">From</span><span class="info-value code-tag">${p.start}</span></div>
    <div class="info-row-item"><span class="info-label">To</span><span class="info-value code-tag">${p.end}</span></div>
  `;
  if (dateRange.is_comparison && dateRange.secondary) {
    const s = dateRange.secondary;
    html += `
      <div class="compare-divider">vs</div>
      <div class="info-row-item"><span class="info-label">Label</span><span class="info-value">${s.label || '—'}</span></div>
      <div class="info-row-item"><span class="info-label">From</span><span class="info-value code-tag">${s.start}</span></div>
      <div class="info-row-item"><span class="info-label">To</span><span class="info-value code-tag">${s.end}</span></div>
    `;
  }
  document.getElementById("dateOutput").innerHTML = html;
}

async function sendMessage() {
  const input = document.getElementById("messageInput");
  const message = input.value.trim();
  if (!message) return;

  const companyId = document.getElementById("companyId").value.trim() || "1";
  const sessionId = document.getElementById("sessionId").value;

  addMessage("user", message);
  history.push({ role: "user", content: message });

  const trimmedHistory = history.slice(-8);

  log("📤 Sending request...");

  const response = await fetch("/api/v1/chat/", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      company_id: companyId,
      session_id: sessionId,
      message: message,
      history: trimmedHistory,
      metadata: { company_id: companyId, session_id: sessionId }
    })
  });

  const data = await response.json();

  const sqlResult = data.data?.sql_query || "";
  addMessage("bot", sqlResult || data.response);
  history.push({ role: "assistant", content: data.response });

  // Populate panels
  document.getElementById("sqlOutput").innerText = sqlResult || "No SQL generated.";
  renderIntent(data.data?.intent);
  renderDate(data.data?.date_range);

  // Pipeline logs
  const pipelineLogs = data.data?.logs || [];
  pipelineLogs.forEach(line => log(line));
  log("📥 Response received");

  input.value = "";
}
