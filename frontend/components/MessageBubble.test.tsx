import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MessageBubble } from "./MessageBubble";

describe("MessageBubble", () => {
  it("renders a user message right-aligned", () => {
    render(<MessageBubble message={{ id: "1", role: "user", content: "Hallo", created_at: "x" }} />);
    expect(screen.getByText("Hallo")).toBeInTheDocument();
  });

  it("renders an assistant message", () => {
    render(
      <MessageBubble message={{ id: "1", role: "assistant", content: "Guten Tag", created_at: "x" }} />
    );
    expect(screen.getByText("Guten Tag")).toBeInTheDocument();
  });

  it("renders a 422 error with no retry button", () => {
    render(
      <MessageBubble
        error={{ status: 422, message: "Sensitive information could not be safely processed." }}
        onRetry={() => {}}
      />
    );
    expect(
      screen.getByText("Sensitive information could not be safely processed.")
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Try again" })).not.toBeInTheDocument();
  });

  it("renders a 502 error with a retry button that calls onRetry", async () => {
    const onRetry = vi.fn();
    const user = userEvent.setup();
    render(
      <MessageBubble error={{ status: 502, message: "Provider unavailable" }} onRetry={onRetry} />
    );

    await user.click(screen.getByRole("button", { name: "Try again" }));

    expect(onRetry).toHaveBeenCalledOnce();
  });

  it("renders a 500 error with a retry button", () => {
    render(<MessageBubble error={{ status: 500, message: "Something went wrong" }} onRetry={() => {}} />);
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });
});
