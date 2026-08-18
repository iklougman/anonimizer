const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export interface SendMessageCallbacks {
  onDelta: (delta: string) => void;
  onDone: (result: { id: string; created_at: string }) => void;
}

export class ChatApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ChatApiError";
    this.status = status;
  }
}

export async function sendMessage(
  accessToken: string,
  conversationId: string,
  content: string,
  callbacks: SendMessageCallbacks
): Promise<void> {
  const response = await fetch(`${API_BASE_URL}/api/conversations/${conversationId}/messages`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${accessToken}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ content }),
  });

  if (!response.ok) {
    let message = "Something went wrong. Try again.";
    try {
      const body = await response.json();
      if (typeof body.detail === "string") message = body.detail;
    } catch {
      // response body wasn't JSON; keep the generic message
    }
    throw new ChatApiError(response.status, message);
  }

  if (!response.body) {
    throw new ChatApiError(response.status, "Empty response stream");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    let boundary = buffer.indexOf("\n\n");
    while (boundary !== -1) {
      const frame = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      processFrame(frame, callbacks);
      boundary = buffer.indexOf("\n\n");
    }
  }
}

function processFrame(frame: string, callbacks: SendMessageCallbacks): void {
  const lines = frame.split("\n");
  const eventLine = lines.find((line) => line.startsWith("event: "));
  const dataLine = lines.find((line) => line.startsWith("data: "));
  if (!eventLine || !dataLine) return;

  const event = eventLine.slice("event: ".length);
  const data = JSON.parse(dataLine.slice("data: ".length));

  if (event === "token") {
    callbacks.onDelta(data.delta);
  } else if (event === "done") {
    callbacks.onDone(data);
  }
}
