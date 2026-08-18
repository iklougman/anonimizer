import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Composer } from "./Composer";

describe("Composer", () => {
  it("sends the message on Enter and clears the input", async () => {
    const onSend = vi.fn();
    const user = userEvent.setup();
    render(<Composer onSend={onSend} disabled={false} />);

    const textarea = screen.getByPlaceholderText("Nachricht eingeben…");
    await user.type(textarea, "Hallo{Enter}");

    expect(onSend).toHaveBeenCalledWith("Hallo");
    expect(textarea).toHaveValue("");
  });

  it("inserts a newline on Shift+Enter instead of sending", async () => {
    const onSend = vi.fn();
    const user = userEvent.setup();
    render(<Composer onSend={onSend} disabled={false} />);

    const textarea = screen.getByPlaceholderText("Nachricht eingeben…");
    await user.type(textarea, "Zeile 1{Shift>}{Enter}{/Shift}Zeile 2");

    expect(onSend).not.toHaveBeenCalled();
    expect(textarea).toHaveValue("Zeile 1\nZeile 2");
  });

  it("does not send an empty or whitespace-only message", async () => {
    const onSend = vi.fn();
    const user = userEvent.setup();
    render(<Composer onSend={onSend} disabled={false} />);

    const textarea = screen.getByPlaceholderText("Nachricht eingeben…");
    await user.type(textarea, "   {Enter}");

    expect(onSend).not.toHaveBeenCalled();
  });

  it("disables the textarea and send button while disabled", () => {
    render(<Composer onSend={() => {}} disabled={true} />);

    expect(screen.getByPlaceholderText("Nachricht eingeben…")).toBeDisabled();
    expect(screen.getByRole("button", { name: "Send" })).toBeDisabled();
  });
});
