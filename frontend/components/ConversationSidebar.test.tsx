import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

vi.mock("next-auth/react", () => ({
  useSession: () => ({ data: { accessToken: "token-123" } }),
}));

const mockRouterPush = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: mockRouterPush }),
  usePathname: () => "/apps/anonymization",
}));

const mockUseMe = vi.fn();
vi.mock("@/components/MeProvider", () => ({
  useMe: () => mockUseMe(),
}));

import * as conversationsApi from "@/lib/api/conversations";
import { ConversationSidebar } from "./ConversationSidebar";

const ME_FULL_PERMISSIONS = {
  user_id: "me-1",
  tenant_id: "tenant-1",
  tenant_name: "Clinic",
  email: "me@example.com",
  role: "super_admin",
  branch_id: null,
  branch_name: null,
  permissions: ["conversations:create", "conversations:delete:any"],
};

function ownConversation(overrides: Partial<Record<string, unknown>> = {}) {
  return {
    id: "1",
    title: "Anfrage Patient A",
    created_at: "x",
    updated_at: "x",
    owner_user_id: "me-1",
    owner_email: "me@example.com",
    is_own: true,
    ...overrides,
  };
}

describe("ConversationSidebar", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    mockRouterPush.mockReset();
    mockUseMe.mockReturnValue(ME_FULL_PERMISSIONS);
  });

  it("lists conversations on mount", async () => {
    vi.spyOn(conversationsApi, "listConversations").mockResolvedValue([ownConversation()]);

    render(<ConversationSidebar />);

    expect(await screen.findByText("Anfrage Patient A")).toBeInTheDocument();
  });

  it("shows a placeholder title for untitled conversations", async () => {
    vi.spyOn(conversationsApi, "listConversations").mockResolvedValue([
      ownConversation({ title: null }),
    ]);

    render(<ConversationSidebar />);

    // Two matches once the conversation list has resolved: the "new
    // conversation" button's own label, and this conversation's placeholder
    // title -- both render the same string. Polled via waitFor (not
    // findAllByText, which would resolve early on the button alone, before
    // the async conversation list arrives).
    await waitFor(() => {
      expect(screen.getAllByText("Neue Anfrage")).toHaveLength(2);
    });
  });

  it("creates a conversation and navigates to it", async () => {
    vi.spyOn(conversationsApi, "listConversations").mockResolvedValue([]);
    vi.spyOn(conversationsApi, "createConversation").mockResolvedValue(
      ownConversation({ id: "new-1", title: null })
    );
    const user = userEvent.setup();

    render(<ConversationSidebar />);
    await user.click(await screen.findByText("Neue Anfrage"));

    await waitFor(() => expect(mockRouterPush).toHaveBeenCalledWith("/apps/anonymization/c/new-1"));
  });

  it("removes a conversation from the list after deleting it", async () => {
    vi.spyOn(conversationsApi, "listConversations").mockResolvedValue([ownConversation()]);
    vi.spyOn(conversationsApi, "deleteConversation").mockResolvedValue(undefined);
    const user = userEvent.setup();

    render(<ConversationSidebar />);
    await screen.findByText("Anfrage Patient A");
    await user.click(screen.getByLabelText("Delete Anfrage Patient A"));

    await waitFor(() => expect(screen.queryByText("Anfrage Patient A")).not.toBeInTheDocument());
  });

  it("hides the new-conversation button without conversations:create", async () => {
    mockUseMe.mockReturnValue({ ...ME_FULL_PERMISSIONS, permissions: [] });
    vi.spyOn(conversationsApi, "listConversations").mockResolvedValue([]);

    render(<ConversationSidebar />);

    await waitFor(() => expect(conversationsApi.listConversations).toHaveBeenCalled());
    expect(screen.queryByText("Neue Anfrage")).not.toBeInTheDocument();
  });

  it("shows an owner badge and hides delete for a shared conversation without delete:any", async () => {
    mockUseMe.mockReturnValue({ ...ME_FULL_PERMISSIONS, permissions: ["conversations:create"] });
    vi.spyOn(conversationsApi, "listConversations").mockResolvedValue([
      ownConversation({
        is_own: false,
        owner_user_id: "someone-else",
        owner_email: "colleague@example.com",
      }),
    ]);

    render(<ConversationSidebar />);

    expect(await screen.findByText("colleague")).toBeInTheDocument();
    expect(screen.queryByLabelText("Delete Anfrage Patient A")).not.toBeInTheDocument();
  });
});
