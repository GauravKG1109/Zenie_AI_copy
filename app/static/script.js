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
  document.getElementById("writeResultOutput").innerHTML = '<span class="placeholder">— will populate after WRITE completes —</span>';
  document.getElementById("notificationBanner").style.display = "none";
  document.getElementById("logWindow").innerHTML = "";
  log("New session started");
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

// Handle one SSE chunk from the pipeline stream
function handleStreamChunk(chunk) {
  if (chunk.node === "__done__") return;

  // Log lines arrive as soon as the node completes
  if (chunk.logs && chunk.logs.length > 0) {
    chunk.logs.forEach(line => log(line));
  }

  // Intent panel — populated when intent_classifier node finishes
  if (chunk.intent) renderIntent(chunk.intent);

  // Date panel — populated when date_extractor node finishes
  if (chunk.date_range) renderDate(chunk.date_range);

  // SQL panel — populated when sql_generator node finishes (READ path)
  if (chunk.sql_query) {
    document.getElementById("sqlOutput").innerText = chunk.sql_query;
  }

  // Query results — displayed in chat window (READ path)
  if (chunk.query_result) {
    const msg = formatQueryResult(chunk.query_result);
    addMessage("bot", msg);
    history.push({ role: "assistant", content: msg });
  }

  // Bot reply — populated when payload_filler_node or end_with_reply finishes
  if (chunk.reply) {
    addMessage("bot", chunk.reply);
    history.push({ role: "assistant", content: chunk.reply });
  }

  // Write notification — WRITE confirmed and submitted
  if (chunk.write_notification) {
    handleWriteNotification(chunk.write_notification);
  }
}

function handleWriteNotification(notif) {
  showNotificationBanner(notif.intent_name + " submitted successfully!", "success");
  renderWriteResult(notif);
  log("[WriteAPI] " + notif.intent_code + " submitted. Status: " + notif.status);
}

function showNotificationBanner(text, type) {
  const banner = document.getElementById("notificationBanner");
  banner.textContent = text;
  banner.className = "notification-banner " + type;
  banner.style.display = "block";
  setTimeout(() => { banner.style.display = "none"; }, 4000);
}

function renderWriteResult(notif) {
  const panel = document.getElementById("writeResultOutput");
  if (!notif || !notif.payload) return;
  const rows = Object.entries(notif.payload)
    .map(([k, v]) => `<div class="info-row-item"><span class="info-label">${k}</span><span class="info-value code-tag">${v ?? "—"}</span></div>`)
    .join("");
  panel.innerHTML = `
    <div class="info-row-item"><span class="info-label">Intent</span><span class="info-value code-tag">${notif.intent_code}</span></div>
    <div class="info-row-item"><span class="info-label">Status</span><span class="info-value">${notif.status}</span></div>
    <hr style="border-color:#1e293b; margin:6px 0;">${rows}
  `;
}

function formatQueryResult(qr) {
  if (!qr.success) {
    return "Query error: " + (qr.error || "Unknown error");
  }
  if (!qr.data || qr.data.length === 0) {
    return "Query returned no results.";
  }
  const rows = qr.data;
  const keys = Object.keys(rows[0]);

  // Single value (e.g. COUNT or SUM aggregate): show inline
  if (rows.length === 1 && keys.length === 1) {
    return keys[0] + ": " + rows[0][keys[0]];
  }

  // Single row, multiple columns
  if (rows.length === 1) {
    return keys.map(k => k + ": " + rows[0][k]).join("\n");
  }

  // Multiple rows
  let text = qr.row_count + " row" + (qr.row_count !== 1 ? "s" : "") + " returned:\n";
  rows.forEach((row, i) => {
    text += "\n[" + (i + 1) + "] " + keys.map(k => k + ": " + row[k]).join(" | ");
  });
  return text;
}

async function sendMessage() {
  const input = document.getElementById("messageInput");
  const message = input.value.trim();
  if (!message) return;

  const companyId = document.getElementById("companyId").value.trim() || "1";
  const sessionId = document.getElementById("sessionId").value;

  addMessage("user", message);
  history.push({ role: "user", content: message });
  input.value = "";

  log("📤 Sending request...");

  try {
    const response = await fetch("/api/v1/chat/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        company_id: companyId,
        session_id: sessionId,
        message: message,
        history: history.slice(-8),
        metadata: { company_id: companyId, session_id: sessionId }
      })
    });

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop(); // keep incomplete last line

      for (const line of lines) {
        if (!line.startsWith("data: ")) continue;
        const jsonStr = line.slice(6).trim();
        if (!jsonStr) continue;
        try {
          handleStreamChunk(JSON.parse(jsonStr));
        } catch (_) { /* malformed chunk — skip */ }
      }
    }

    log("📥 Done");
  } catch (err) {
    log(`❌ Error: ${err.message}`);
  }
}
