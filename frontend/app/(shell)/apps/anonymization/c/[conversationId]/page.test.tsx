import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";

vi.mock("next-auth/react", () => ({
  useSession: () => ({ data: { accessToken: "token-123" } }),
}));

vi.mock("next/navigation", () => ({
  useParams: () => ({ conversationId: "conv-1" }),
}));

import * as conversationsApi from "@/lib/api/conversations";
import ConversationPage from "./page";

const OWN_CONVERSATION = {
  id: "conv-1",
  title: "Anfrage",
  created_at: "x",
  updated_at: "x",
  owner_user_id: "me-1",
  owner_email: "me@example.com",
  is_own: true,
};

const SHARED_CONVERSATION = {
  ...OWN_CONVERSATION,
  owner_user_id: "colleague-1",
  owner_email: "colleague@example.com",
  is_own: false,
};

describe("ConversationPage", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("renders the composer for an own conversation", async () => {
    vi.spyOn(conversationsApi, "getConversation").mockResolvedValue(OWN_CONVERSATION);
    vi.spyOn(conversationsApi, "getMessages").mockResolvedValue([]);

    render(<ConversationPage />);

    expect(await screen.findByPlaceholderText("Nachricht eingeben…")).toBeInTheDocument();
    expect(screen.queryByText(/Schreibgeschützt/)).not.toBeInTheDocument();
  });

  it("replaces the composer with a read-only notice for a shared conversation", async () => {
    vi.spyOn(conversationsApi, "getConversation").mockResolvedValue(SHARED_CONVERSATION);
    vi.spyOn(conversationsApi, "getMessages").mockResolvedValue([]);

    render(<ConversationPage />);

    expect(await screen.findByText(/Schreibgeschützt.*colleague@example.com/)).toBeInTheDocument();
    expect(screen.queryByPlaceholderText("Nachricht eingeben…")).not.toBeInTheDocument();
  });
});
