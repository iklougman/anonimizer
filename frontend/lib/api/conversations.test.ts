import { describe, expect, it, vi, beforeEach } from "vitest";
import { listConversations, createConversation, deleteConversation, getMessages } from "./conversations";

describe("conversations API client", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("listConversations sends the bearer token and returns the parsed list", async () => {
    const mockConversations = [
      { id: "1", title: null, created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z" },
    ];
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => mockConversations });
    vi.stubGlobal("fetch", fetchMock);

    const result = await listConversations("token-123");

    expect(fetchMock).toHaveBeenCalledWith("http://localhost:8000/api/conversations", {
      headers: { Authorization: "Bearer token-123" },
    });
    expect(result).toEqual(mockConversations);
  });

  it("createConversation posts and returns the created conversation", async () => {
    const created = { id: "2", title: null, created_at: "x", updated_at: "x" };
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => created });
    vi.stubGlobal("fetch", fetchMock);

    const result = await createConversation("token-123");

    expect(fetchMock).toHaveBeenCalledWith("http://localhost:8000/api/conversations", {
      method: "POST",
      headers: { Authorization: "Bearer token-123" },
    });
    expect(result).toEqual(created);
  });

  it("deleteConversation sends DELETE and resolves on 204", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: false, status: 204 });
    vi.stubGlobal("fetch", fetchMock);

    await expect(deleteConversation("token-123", "5")).resolves.toBeUndefined();
    expect(fetchMock).toHaveBeenCalledWith("http://localhost:8000/api/conversations/5", {
      method: "DELETE",
      headers: { Authorization: "Bearer token-123" },
    });
  });

  it("getMessages returns the parsed message list", async () => {
    const messages = [{ id: "m1", role: "user", content: "Hallo", created_at: "x" }];
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => messages });
    vi.stubGlobal("fetch", fetchMock);

    const result = await getMessages("token-123", "5");

    expect(fetchMock).toHaveBeenCalledWith("http://localhost:8000/api/conversations/5/messages", {
      headers: { Authorization: "Bearer token-123" },
    });
    expect(result).toEqual(messages);
  });

  it("listConversations throws on a non-ok response", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: false, status: 401 });
    vi.stubGlobal("fetch", fetchMock);

    await expect(listConversations("bad-token")).rejects.toThrow("401");
  });
});
