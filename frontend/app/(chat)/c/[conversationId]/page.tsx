"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { useSession } from "next-auth/react";
import { getMessages } from "@/lib/api/conversations";
import { sendMessage, ChatApiError } from "@/lib/api/chat";
import type { MessageOut } from "@/lib/api/types";
import { MessageBubble } from "@/components/MessageBubble";
import { Composer } from "@/components/Composer";
import styles from "./page.module.css";

type DisplayItem =
  | { kind: "message"; message: MessageOut }
  | { kind: "error"; status: number; message: string; retryContent: string };

export default function ConversationPage() {
  const { conversationId } = useParams<{ conversationId: string }>();
  const { data: session } = useSession();
  const [items, setItems] = useState<DisplayItem[]>([]);
  const [pendingAssistantText, setPendingAssistantText] = useState<string | null>(null);
  const [sending, setSending] = useState(false);

  useEffect(() => {
    if (!session?.accessToken) return;
    getMessages(session.accessToken, conversationId).then((messages) => {
      setItems(messages.map((message) => ({ kind: "message", message })));
    });
  }, [session?.accessToken, conversationId]);

  async function handleSend(content: string) {
    if (!session?.accessToken) return;
    setItems((current) => [
      ...current,
      { kind: "message", message: { id: `pending-${Date.now()}`, role: "user", content, created_at: "" } },
    ]);
    setSending(true);
    setPendingAssistantText("");
    // A local accumulator, not the pendingAssistantText state: onDone's closure
    // over React state would capture the value from when handleSend was called,
    // not the deltas onDelta accumulates during the same call -- state updates
    // don't propagate into an already-created closure. onDelta and onDone run
    // synchronously in sequence inside sendMessage's stream-reading loop, so a
    // plain local variable shared by both closures is correct here.
    let accumulatedText = "";

    try {
      await sendMessage(session.accessToken, conversationId, content, {
        onDelta: (delta) => {
          accumulatedText += delta;
          setPendingAssistantText(accumulatedText);
        },
        onDone: (result) => {
          setItems((current) => [
            ...current,
            {
              kind: "message",
              message: {
                id: result.id,
                role: "assistant",
                content: accumulatedText,
                created_at: result.created_at,
              },
            },
          ]);
          setPendingAssistantText(null);
        },
      });
    } catch (error) {
      const chatError =
        error instanceof ChatApiError ? error : new ChatApiError(500, "Something went wrong. Try again.");
      setItems((current) => [
        ...current,
        { kind: "error", status: chatError.status, message: chatError.message, retryContent: content },
      ]);
      setPendingAssistantText(null);
    } finally {
      setSending(false);
    }
  }

  return (
    <>
      <div className={styles.messages}>
        {items.map((item, index) =>
          item.kind === "message" ? (
            <MessageBubble key={item.message.id} message={item.message} />
          ) : (
            <MessageBubble
              key={`error-${index}`}
              error={{ status: item.status, message: item.message }}
              onRetry={() => handleSend(item.retryContent)}
            />
          )
        )}
        {sending && pendingAssistantText !== null && (
          <MessageBubble
            message={{ id: "pending", role: "assistant", content: pendingAssistantText || "…", created_at: "" }}
          />
        )}
      </div>
      <Composer onSend={handleSend} disabled={sending} />
    </>
  );
}
