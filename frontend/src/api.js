const API_BASE_URL = (
  import.meta.env.VITE_API_BASE_URL || "http://localhost:8000"
).replace(/\/+$/, "");

function formatApiError(data, status) {
  if (!data) {
    return `Request failed with status ${status}`;
  }
  if (typeof data.detail === "string") {
    return `${status}: ${data.detail}`;
  }
  if (Array.isArray(data.detail)) {
    const msgs = data.detail
      .map((item) => item?.msg || JSON.stringify(item))
      .join("; ");
    return `${status}: ${msgs}`;
  }
  return `${status}: ${JSON.stringify(data)}`;
}

export async function fetchHealth() {
  const response = await fetch(`${API_BASE_URL}/health`);
  const data = await response.json().catch(() => null);
  if (!response.ok) {
    throw new Error(formatApiError(data, response.status));
  }
  return data;
}

export async function sendChatMessage({
  sessionId,
  message,
  systemPrompt,
  simulateFailure,
}) {
  const headers = {
    "Content-Type": "application/json",
  };
  if (simulateFailure) {
    headers["X-Simulate-Failure"] = simulateFailure;
  }

  const body = {
    session_id: sessionId,
    message,
  };
  if (systemPrompt && systemPrompt.trim()) {
    body.system_prompt = systemPrompt.trim();
  }

  const response = await fetch(`${API_BASE_URL}/chat`, {
    method: "POST",
    headers,
    body: JSON.stringify(body),
  });

  const data = await response.json().catch(() => null);
  if (!response.ok) {
    throw new Error(formatApiError(data, response.status));
  }
  return data;
}

export async function fetchSessionMessages(sessionId) {
  const response = await fetch(
    `${API_BASE_URL}/sessions/${encodeURIComponent(sessionId)}/messages`
  );
  const data = await response.json().catch(() => null);
  if (!response.ok) {
    throw new Error(formatApiError(data, response.status));
  }
  return data;
}
