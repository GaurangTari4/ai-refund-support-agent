const sessionId = crypto.randomUUID();
const customerSelect = document.querySelector("#customerSelect");
const customerSearch = document.querySelector("#customerSearch");
const customerSnapshot = document.querySelector("#customerSnapshot");
const chatLog = document.querySelector("#chatLog");
const chatForm = document.querySelector("#chatForm");
const messageInput = document.querySelector("#messageInput");
const micButton = document.querySelector("#micButton");
const speakReplies = document.querySelector("#speakReplies");
const voiceStatus = document.querySelector("#voiceStatus");
const traceLog = document.querySelector("#traceLog");
const decisionPanel = document.querySelector("#decisionPanel");
const clearLogs = document.querySelector("#clearLogs");
const connectionDot = document.querySelector("#connectionDot");
const connectionText = document.querySelector("#connectionText");
const toolCount = document.querySelector("#toolCount");
const caseCount = document.querySelector("#caseCount");
const policyCount = document.querySelector("#policyCount");
const policySummary = document.querySelector("#policySummary");
const caseList = document.querySelector("#caseList");
const activityList = document.querySelector("#activityList");
const runtimeChip = document.querySelector("#runtimeChip");
const modelSelect = document.querySelector("#modelSelect");
const voiceRuntime = document.querySelector("#voiceRuntime");
const policyDate = document.querySelector("#policyDate");
const sessionRuntime = document.querySelector("#sessionRuntime");
const scenarioButtons = document.querySelectorAll("[data-scenario]");

const messageTemplate = document.querySelector("#messageTemplate");
const traceTemplate = document.querySelector("#traceTemplate");

let customers = [];
let traceItems = [];
let traceIds = new Set();
let cases = [];
let recognition = null;
let recognizing = false;
let runtimeConfig = null;
let customerFilter = "";
let activeCustomerId = null;
let selectedModel = "";
let availableModels = [];
let mediaRecorder = null;
let mediaStream = null;
let voiceChunks = [];
let providerRecording = false;

function money(value) {
  return `$${Number(value).toFixed(2)}`;
}

function escapeHtml(value) {
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function addMessage(role, text) {
  const node = messageTemplate.content.firstElementChild.cloneNode(true);
  node.classList.add(role);
  node.querySelector(".bubble").textContent = text;
  chatLog.append(node);
  chatLog.scrollTop = chatLog.scrollHeight;
}

function renderCustomerSnapshot(customer) {
  if (!customer) {
    customerSnapshot.innerHTML = "";
    return;
  }
  const order = customer.orders[0];
  const delivered = order.deliveredAt || "Not delivered";
  customerSnapshot.innerHTML = `
    <div><small>Tier</small><strong>${escapeHtml(customer.tier)}</strong></div>
    <div><small>Risk</small><strong>${escapeHtml(customer.fraudScore)}</strong></div>
    <div><small>180-Day Refunds</small><strong>${escapeHtml(customer.refundsLast180Days)}</strong></div>
    <div><small>Latest Order</small><strong>${escapeHtml(order.id)}</strong></div>
    <div><small>Status</small><strong>${escapeHtml(order.status)}</strong></div>
    <div><small>Delivered</small><strong>${escapeHtml(delivered)}</strong></div>
    <div><small>Total</small><strong>${escapeHtml(money(order.total))}</strong></div>
    <div><small>Category</small><strong>${escapeHtml(order.category.replaceAll("_", " "))}</strong></div>
  `;
}

function selectedCustomer() {
  return customers.find((customer) => customer.id === activeCustomerId) || filteredCustomers()[0] || null;
}

function filteredCustomers() {
  const query = customerFilter.trim().toLowerCase();
  if (!query) return customers;
  return customers.filter((customer) => {
    const haystack = [
      customer.id,
      customer.name,
      customer.email,
      customer.tier,
      customer.notes,
      customer.orders.map((order) => `${order.id} ${order.category} ${order.items.join(" ")}`).join(" ")
    ]
      .join(" ")
      .toLowerCase();
    return haystack.includes(query);
  });
}

function seedPromptForCustomer(customer) {
  if (!customer) return;
  const order = customer.orders[0];
  const exampleReason = {
    standard_physical: "changed my mind",
    digital_download: "changed my mind",
    final_sale: "changed my mind",
    in_transit: "delivery delay",
    gift_card: "changed my mind",
    subscription: "duplicate charge"
  }[order.category] || "refund request";
  messageInput.value = `Hi, I need a refund for ${order.id}. Reason: ${exampleReason}.`;
}

function applyScenario(scenario) {
  const customer = selectedCustomer();
  if (!customer) return;
  const order = customer.orders[0];
  const examples = {
    approved: `Hi, I need a refund for ${order.id} because it arrived damaged.`,
    denied: `Hi, I need a refund for ${order.id} because I changed my mind.`,
    review: `Hi, I need a refund for ${order.id} because the charge looks wrong.`,
    missing: "Hi, I need help with a refund but I do not have the order ID."
  };
  messageInput.value = examples[scenario] || examples.approved;
  messageInput.focus();
}

function renderCustomers() {
  const list = filteredCustomers();
  const previousSelection = activeCustomerId;
  const current = list.find((customer) => customer.id === previousSelection) || list[0] || null;

  customerSelect.innerHTML = list
    .map((customer) => `<option value="${escapeHtml(customer.id)}">${escapeHtml(customer.name)} - ${escapeHtml(customer.orders[0].id)}</option>`)
    .join("");
  customerSelect.disabled = list.length === 0;
  policyCount.textContent = customers.length;

  if (current) {
    activeCustomerId = current.id;
    customerSelect.value = current.id;
    renderCustomerSnapshot(current);
    seedPromptForCustomer(current);
  } else {
    activeCustomerId = null;
    customerSnapshot.innerHTML = `<div class="empty-customer"><strong>No matches</strong><p>Try a different search term.</p></div>`;
  }
}

function renderActivity() {
  const items = traceItems.slice(0, 5);
  activityList.innerHTML = "";
  if (!items.length) {
    const empty = document.createElement("p");
    empty.className = "empty-state";
    empty.textContent = "No live activity yet.";
    activityList.append(empty);
    return;
  }

  items.forEach((item) => {
    const node = document.createElement("article");
    node.className = `activity-item ${item.type}`;
    const time = new Date(item.at).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
    node.innerHTML = `
      <div class="activity-meta">
        <span>${escapeHtml(item.type)}</span>
        <small>${escapeHtml(time)}</small>
      </div>
      <strong>${escapeHtml(item.title)}</strong>
      <p>${escapeHtml(item.detail)}</p>
    `;
    activityList.append(node);
  });
}

function updateMetrics() {
  toolCount.textContent = traceItems.filter((item) => item.type === "tool").length;
  caseCount.textContent = cases.length;
}

function addTrace(entry) {
  if (traceIds.has(entry.id)) return;
  traceIds.add(entry.id);
  traceItems.push(entry);
  const node = traceTemplate.content.firstElementChild.cloneNode(true);
  node.dataset.type = entry.type;
  node.querySelector(".trace-type").textContent = entry.type;
  node.querySelector("strong").textContent = entry.title;
  node.querySelector("p").textContent = entry.detail;
  traceLog.prepend(node);
  renderActivity();
  updateMetrics();
}

function renderPolicyChecks(checks = []) {
  if (!checks.length) return "";
  const items = checks
    .map((check) => {
      const tone = check.status === "fail" ? "fail" : check.status === "review" ? "review" : "pass";
      return `<li class="${tone}"><span>${escapeHtml(check.rule)}</span>${escapeHtml(check.detail)}</li>`;
    })
    .join("");
  return `<ul class="policy-checks">${items}</ul>`;
}

function renderCases() {
  caseList.innerHTML = "";
  if (!cases.length) {
    const empty = document.createElement("p");
    empty.className = "empty-state";
    empty.textContent = "No cases yet.";
    caseList.append(empty);
    return;
  }

  cases.slice(0, 5).forEach((refundCase) => {
    const node = document.createElement("article");
    node.className = `case-card ${refundCase.outcome.toLowerCase()}`;

    const label = document.createElement("span");
    label.textContent = refundCase.outcome.replace("_", " ");

    const title = document.createElement("strong");
    title.textContent = `${refundCase.caseId} - ${refundCase.orderId || "Unknown order"}`;

    const detail = document.createElement("p");
    detail.textContent =
      refundCase.outcome === "APPROVED"
        ? `${money(refundCase.refundAmount)} refund recorded.`
        : refundCase.policyReason;

    node.append(label, title, detail);
    caseList.append(node);
  });
}

function renderPolicySummary(policyText) {
  const rules = policyText
    .split("\n")
    .filter((line) => /^\d+\.\s/.test(line.trim()))
    .slice(0, 8)
    .map((line) => line.replace(/^\d+\.\s*/, "").trim());

  policySummary.innerHTML = "";
  rules.forEach((rule) => {
    const item = document.createElement("p");
    item.textContent = rule;
    policySummary.append(item);
  });
}

function labelForModel(modelId) {
  return availableModels.find((model) => model.id === modelId)?.label || modelId || "Default";
}

function agentModeLabel(config) {
  const mode = config.agent?.mode;
  if (mode === "crewai-local") return "Ollama LLM refund agent ready";
  if (mode === "crewai-openai") return "OpenAI LLM refund agent ready";
  if (!config.agent?.llmEnabled) return "Deterministic policy engine active";
  if (!config.agent?.crewAIInstalled) return "Install CrewAI to enable the LLM agent";
  return "LLM unavailable, policy fallback active";
}

function updateModelPicker(config) {
  availableModels = config.agent?.localModels || [];
  selectedModel = config.agent?.selectedModel || availableModels[0]?.id || "";

  modelSelect.innerHTML = availableModels
    .map((model) => `<option value="${escapeHtml(model.id)}">${escapeHtml(model.label)}</option>`)
    .join("");
  modelSelect.disabled = !availableModels.length || !config.agent?.localLLMAvailable;
  if (selectedModel) {
    modelSelect.value = selectedModel;
  }
}

function setDecision(decision, refundCase, policyChecks = []) {
  if (!decision) return;
  decisionPanel.className = "decision-panel";
  const label = decision.outcome || "Waiting";
  if (label === "APPROVED") decisionPanel.classList.add("approved");
  else if (label === "DENIED") decisionPanel.classList.add("denied");
  else if (label === "MANUAL_REVIEW") decisionPanel.classList.add("review");
  else decisionPanel.classList.add("neutral");

  decisionPanel.innerHTML = `
    <span class="decision-label">${escapeHtml(label)}</span>
    <strong>${escapeHtml(refundCase?.caseId || "No case yet")}</strong>
    <p>${escapeHtml(decision.reason || "Decision pending.")}</p>
    ${renderPolicyChecks(policyChecks)}
  `;
}

function speak(text) {
  if (!speakReplies.checked || !("speechSynthesis" in window)) return;
  const utterance = new SpeechSynthesisUtterance(text);
  utterance.rate = 0.96;
  utterance.pitch = 1;
  speechSynthesis.cancel();
  speechSynthesis.speak(utterance);
}

function arrayBufferToBase64(buffer) {
  const bytes = new Uint8Array(buffer);
  let binary = "";
  const chunkSize = 0x8000;
  for (let i = 0; i < bytes.length; i += chunkSize) {
    binary += String.fromCharCode(...bytes.subarray(i, i + chunkSize));
  }
  return btoa(binary);
}

function setVoiceUiRecording(recording, label) {
  providerRecording = recording;
  micButton.classList.toggle("recording", recording);
  voiceStatus.textContent = label;
}

async function playReturnedAudio(audioBase64, mimeType = "audio/mpeg") {
  if (!audioBase64) return;
  const audio = new Audio(`data:${mimeType};base64,${audioBase64}`);
  audio.volume = 1;
  await audio.play();
}

async function submitVoiceTurn(blob) {
  const customer = selectedCustomer();
  const buffer = await blob.arrayBuffer();
  const response = await fetch("/api/voice/turn", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      sessionId,
      customerId: customer?.id,
      modelName: selectedModel,
      audioBase64: arrayBufferToBase64(buffer),
      mimeType: blob.type || "audio/webm"
    })
  });

  const payload = await response.json();
  if (!response.ok) {
    const message = payload.detail ? `${payload.error || "Voice processing failed."} ${payload.detail}` : (payload.error || "Voice processing failed.");
    addMessage("agent", message);
    voiceStatus.textContent = message;
    return;
  }

  addMessage("user", payload.transcript);
  addMessage("agent", payload.message);
  setDecision(payload.decision, payload.case, payload.policyChecks);
  await playReturnedAudio(payload.audioBase64, payload.audioMimeType);
  await refreshCases();
}

async function startProviderVoiceCapture() {
  if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) {
    voiceStatus.textContent = "This browser cannot record audio.";
    return;
  }

  const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  mediaStream = stream;
  voiceChunks = [];
  mediaRecorder = new MediaRecorder(stream);
  mediaRecorder.addEventListener("dataavailable", (event) => {
    if (event.data.size > 0) voiceChunks.push(event.data);
  });
  mediaRecorder.addEventListener("stop", async () => {
    const blob = new Blob(voiceChunks, { type: mediaRecorder?.mimeType || "audio/webm" });
    mediaChunksReset();
    try {
      setVoiceUiRecording(false, "Processing audio");
      await submitVoiceTurn(blob);
    } catch (error) {
      addMessage("agent", error.message || "Voice processing failed.");
      voiceStatus.textContent = error.message || "Voice processing failed.";
    }
  });

  mediaRecorder.start();
  setVoiceUiRecording(true, "Listening");
}

function mediaChunksReset() {
  voiceChunks = [];
  if (mediaStream) {
    for (const track of mediaStream.getTracks()) track.stop();
    mediaStream = null;
  }
  mediaRecorder = null;
}

function stopProviderVoiceCapture() {
  if (mediaRecorder && mediaRecorder.state !== "inactive") {
    mediaRecorder.stop();
  }
}

async function refreshCases() {
  const response = await fetch("/api/cases");
  cases = await response.json();
  renderCases();
  updateMetrics();
}

function updateRuntime(config) {
  runtimeConfig = config;
  if (!config) return;
  const agentMode = agentModeLabel(config);
  runtimeChip.textContent = `${agentMode} - ${config.voice.mode === "openai-audio-pipeline" ? "OpenAI voice optional" : "Browser voice ready"}`;
  voiceRuntime.textContent =
      config.voice.mode === "openai-audio-pipeline"
      ? "OpenAI voice pipeline enabled for recorded audio"
      : "Browser speech recognition + speech synthesis";
  policyDate.textContent = config.policy.effectiveDate;
  sessionRuntime.textContent = `Server time ${new Date(config.serverTime).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}`;
}

function syncRuntimeStatus(config) {
  if (!config) return;
  const agentMode = agentModeLabel(config);
  const modelLabel = labelForModel(selectedModel || config.agent?.selectedModel);
  runtimeChip.textContent = `${agentMode} - ${modelLabel} - ${config.voice.mode === "openai-audio-pipeline" ? "OpenAI voice optional" : "Browser voice ready"}`;
  voiceRuntime.textContent =
      config.voice.mode === "openai-audio-pipeline"
      ? "OpenAI voice pipeline enabled for recorded audio"
      : "Browser speech recognition + speech synthesis";
}

async function sendMessage(text) {
  const customer = selectedCustomer();
  addMessage("user", text);
  messageInput.value = "";

  const response = await fetch("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      sessionId,
      customerId: customer?.id,
      message: text,
      modelName: selectedModel
    })
  });

  const payload = await response.json();
  if (!response.ok) {
    addMessage("agent", payload.error || "Something went wrong.");
    return;
  }

  addMessage("agent", payload.message);
  setDecision(payload.decision, payload.case, payload.policyChecks);
  speak(payload.message);
  await refreshCases();
}

function connectTraceStream() {
  const events = new EventSource(`/api/sessions/${sessionId}/events`);
  events.addEventListener("open", () => {
    connectionDot.classList.add("online");
    connectionText.textContent = "Live";
  });
  events.addEventListener("snapshot", (event) => {
    const snapshot = JSON.parse(event.data);
    snapshot.forEach(addTrace);
  });
  events.addEventListener("reasoning", (event) => addTrace(JSON.parse(event.data)));
  events.addEventListener("error", () => {
    connectionDot.classList.remove("online");
    connectionText.textContent = "Reconnecting";
  });
}

function setupVoice() {
  if (runtimeConfig?.voice.openAIVoicePipelineEnabled) {
    const supported = navigator.mediaDevices?.getUserMedia && window.MediaRecorder;
    voiceStatus.textContent = supported
      ? "OpenAI voice pipeline is ready. Click the mic to record and upload audio."
      : "Audio recording is not available in this browser.";
    micButton.disabled = !supported;
    return;
  }

  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SpeechRecognition) {
    voiceStatus.textContent = "Browser voice is not available in this browser.";
    micButton.disabled = true;
    if (!runtimeConfig?.voice.openAIVoicePipelineEnabled) {
      runtimeChip.textContent = "Browser voice unavailable";
    }
    return;
  }

  recognition = new SpeechRecognition();
  recognition.lang = "en-US";
  recognition.interimResults = true;
  recognition.continuous = false;

  recognition.addEventListener("start", () => {
    recognizing = true;
    micButton.classList.add("recording");
    voiceStatus.textContent = "Listening";
  });

  recognition.addEventListener("result", (event) => {
    const text = Array.from(event.results)
      .map((result) => result[0].transcript)
      .join(" ");
    messageInput.value = text;
  });

  recognition.addEventListener("end", () => {
    recognizing = false;
    micButton.classList.remove("recording");
    voiceStatus.textContent = "Browser voice ready";
  });

  recognition.addEventListener("error", (event) => {
    recognizing = false;
    micButton.classList.remove("recording");
    voiceStatus.textContent = `Voice error: ${event.error}`;
  });
}

chatForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const text = messageInput.value.trim();
  if (!text) return;
  await sendMessage(text);
});

customerSelect.addEventListener("change", () => {
  activeCustomerId = customerSelect.value;
  renderCustomers();
});

customerSearch.addEventListener("input", () => {
  customerFilter = customerSearch.value;
  renderCustomers();
});

micButton.addEventListener("click", () => {
  if (runtimeConfig?.voice.openAIVoicePipelineEnabled) {
    if (providerRecording) stopProviderVoiceCapture();
    else startProviderVoiceCapture().catch((error) => {
      setVoiceUiRecording(false, error.message || "Could not start recording.");
    });
    return;
  }

  if (!recognition) return;
  if (recognizing) recognition.stop();
  else recognition.start();
});

scenarioButtons.forEach((button) => {
  button.addEventListener("click", () => applyScenario(button.dataset.scenario));
});

clearLogs.addEventListener("click", () => {
  traceItems = [];
  traceIds = new Set();
  traceLog.innerHTML = "";
  renderActivity();
  updateMetrics();
});

async function boot() {
  const [customerResponse, policyResponse, configResponse] = await Promise.all([
    fetch("/api/customers"),
    fetch("/api/policy"),
    fetch("/api/config")
  ]);
  customers = await customerResponse.json();
  activeCustomerId = customers[0]?.id || null;
  renderPolicySummary(await policyResponse.text());
  updateRuntime(await configResponse.json());
  updateModelPicker(runtimeConfig);
  syncRuntimeStatus(runtimeConfig);
  renderCustomers();
  renderActivity();
  addMessage("agent", "Hi. Send a refund request with an order ID and I will evaluate it against the strict policy.");
  setupVoice();
  connectTraceStream();
  await refreshCases();
}

modelSelect.addEventListener("change", () => {
  selectedModel = modelSelect.value;
  if (runtimeConfig) syncRuntimeStatus(runtimeConfig);
});

boot();

