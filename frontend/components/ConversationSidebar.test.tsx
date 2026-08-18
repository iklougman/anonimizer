import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

vi.mock("next-auth/react", () => ({
  useSession: () => ({ data: { accessToken: "token-123" } }),
}));

const mockRouterPush = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: mockRouterPush }),
}));

import * as conversationsApi from "@/lib/api/conversations";
import { ConversationSidebar } from "./ConversationSidebar";

describe("ConversationSidebar", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    mockRouterPush.mockReset();
  });

  it("lists conversations on mount", async () => {
    vi.spyOn(conversationsApi, "listConversations").mockResolvedValue([
      { id: "1", title: "Anfrage Patient A", created_at: "x", updated_at: "x" },
    ]);

    render(<ConversationSidebar />);

    expect(await screen.findByText("Anfrage Patient A")).toBeInTheDocument();
  });

  it("shows a placeholder title for untitled conversations", async () => {
    vi.spyOn(conversationsApi, "listConversations").mockResolvedValue([
      { id: "1", title: null, created_at: "x", updated_at: "x" },
    ]);

    render(<ConversationSidebar />);

    expect(await screen.findByText("Neue Anfrage")).toBeInTheDocument();
  });

  it("creates a conversation and navigates to it", async () => {
    vi.spyOn(conversationsApi, "listConversations").mockResolvedValue([]);
    vi.spyOn(conversationsApi, "createConversation").mockResolvedValue({
      id: "new-1",
      title: null,
      created_at: "x",
      updated_at: "x",
    });
    const user = userEvent.setup();

    render(<ConversationSidebar />);
    await user.click(await screen.findByText("+ Neue Anfrage"));

    await waitFor(() => expect(mockRouterPush).toHaveBeenCalledWith("/c/new-1"));
  });

  it("removes a conversation from the list after deleting it", async () => {
    vi.spyOn(conversationsApi, "listConversations").mockResolvedValue([
      { id: "1", title: "Anfrage Patient A", created_at: "x", updated_at: "x" },
    ]);
    vi.spyOn(conversationsApi, "deleteConversation").mockResolvedValue(undefined);
    const user = userEvent.setup();

    render(<ConversationSidebar />);
    await screen.findByText("Anfrage Patient A");
    await user.click(screen.getByLabelText("Delete Anfrage Patient A"));

    await waitFor(() => expect(screen.queryByText("Anfrage Patient A")).not.toBeInTheDocument());
  });
});
