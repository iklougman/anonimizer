import { describe, expect, it, vi, beforeEach } from "vitest";
import { sendMessage, ChatApiError } from "./chat";

function streamFromChunks(chunks: string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  let index = 0;
  return new ReadableStream({
    pull(controller) {
      if (index < chunks.length) {
        controller.enqueue(encoder.encode(chunks[index]));
        index += 1;
      } else {
        controller.close();
      }
    },
  });
}

describe("sendMessage", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("emits deltas in order and calls onDone with the final payload", async () => {
    const body = streamFromChunks([
      'event: token\ndata: {"delta": "Das klingt "}\n\n',
      'event: token\ndata: {"delta": "gut."}\n\n',
      'event: done\ndata: {"id": "m1", "created_at": "2026-01-01T00:00:00Z"}\n\n',
    ]);
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, body }));

    const deltas: string[] = [];
    let done: { id: string; created_at: string } | undefined;

    await sendMessage("token-123", "conv-1", "Hallo", {
      onDelta: (delta) => deltas.push(delta),
      onDone: (result) => {
        done = result;
      },
    });

    expect(deltas).toEqual(["Das klingt ", "gut."]);
    expect(done).toEqual({ id: "m1", created_at: "2026-01-01T00:00:00Z" });
  });

  it("buffers a frame split across multiple stream chunks", async () => {
    const body = streamFromChunks([
      'event: token\ndata: {"delta": "Hallo"',
      "}\n\n",
      'event: done\ndata: {"id": "m1", "created_at": "x"}\n\n',
    ]);
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, body }));

    const deltas: string[] = [];
    await sendMessage("token-123", "conv-1", "Hallo", {
      onDelta: (delta) => deltas.push(delta),
      onDone: () => {},
    });

    expect(deltas).toEqual(["Hallo"]);
  });

  it("sends the message content as a JSON body with the bearer token", async () => {
    const body = streamFromChunks(['event: done\ndata: {"id": "m1", "created_at": "x"}\n\n']);
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, body });
    vi.stubGlobal("fetch", fetchMock);

    await sendMessage("token-123", "conv-1", "Hallo Welt", { onDelta: () => {}, onDone: () => {} });

    expect(fetchMock).toHaveBeenCalledWith(
      "http://localhost:8000/api/conversations/conv-1/messages",
      {
        method: "POST",
        headers: { Authorization: "Bearer token-123", "Content-Type": "application/json" },
        body: JSON.stringify({ content: "Hallo Welt" }),
      }
    );
  });

  it("throws a ChatApiError with the backend detail on a 422", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 422,
        json: async () => ({ detail: "Sensitive information could not be safely processed." }),
      })
    );

    await expect(
      sendMessage("token-123", "conv-1", "risky message", { onDelta: () => {}, onDone: () => {} })
    ).rejects.toMatchObject({
      status: 422,
      message: "Sensitive information could not be safely processed.",
    });
  });

  it("throws a ChatApiError with a generic message when the error response isn't JSON", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 502,
        json: async () => {
          throw new Error("not json");
        },
      })
    );

    await expect(
      sendMessage("token-123", "conv-1", "hi", { onDelta: () => {}, onDone: () => {} })
    ).rejects.toMatchObject({ status: 502, message: "Something went wrong. Try again." });
  });

  it("is an instance of ChatApiError", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: false, status: 500, json: async () => ({}) })
    );

    await expect(
      sendMessage("token-123", "conv-1", "hi", { onDelta: () => {}, onDone: () => {} })
    ).rejects.toBeInstanceOf(ChatApiError);
  });
});
