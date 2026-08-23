"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { useSession } from "next-auth/react";
import { IconLock } from "@tabler/icons-react";
import { getConversation, getMessages } from "@/lib/api/conversations";
import { sendMessage, ChatApiError } from "@/lib/api/chat";
import type { ConversationDetail, MessageOut } from "@/lib/api/types";
import { MessageBubble } from "@/components/MessageBubble";
import { Composer } from "@/components/Composer";
import styles from "./page.module.css";

type DisplayItem =
  | { kind: "message"; message: MessageOut }
  | { kind: "error"; status: number; message: string; retryContent: string }
  | { kind: "aborted"; partialContent: string; retryContent: string };

export default function ConversationPage() {
  const { conversationId } = useParams<{ conversationId: string }>();
  const { data: session } = useSession();
  const [conversation, setConversation] = useState<ConversationDetail | null>(null);
  const [items, setItems] = useState<DisplayItem[]>([]);
  const [pendingAssistantText, setPendingAssistantText] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const [loadError, setLoadError] = useState(false);

  function loadMessages() {
    if (!session?.accessToken) return;
    setLoadError(false);
    Promise.all([
      getConversation(session.accessToken, conversationId),
      getMessages(session.accessToken, conversationId),
    ])
      .then(([conversationDetail, messages]) => {
        setConversation(conversationDetail);
        setItems(messages.map((message) => ({ kind: "message", message })));
      })
      .catch(() => setLoadError(true));
  }

  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(loadMessages, [session?.accessToken, conversationId]);

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
        // The turn is never persisted on a guard/provider failure (see
        // backend/app/api/chat.py's _stream_and_guard), so this partial text
        // is shown only for this render -- a reload re-fetches persisted
        // messages and correctly won't include it.
        onError: () => {
          setItems((current) => [
            ...current,
            { kind: "aborted", partialContent: accumulatedText, retryContent: content },
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

  const isReadOnly = conversation !== null && !conversation.is_own;

  return (
    <>
      <div className={styles.messages}>
        {loadError && (
          <MessageBubble
            error={{ status: 500, message: "Conversation could not be loaded." }}
            onRetry={loadMessages}
          />
        )}
        {items.map((item, index) => {
          if (item.kind === "message") {
            return <MessageBubble key={item.message.id} message={item.message} />;
          }
          if (item.kind === "aborted") {
            return (
              <MessageBubble
                key={`aborted-${index}`}
                aborted={{ partialContent: item.partialContent }}
                onRetry={() => handleSend(item.retryContent)}
              />
            );
          }
          return (
            <MessageBubble
              key={`error-${index}`}
              error={{ status: item.status, message: item.message }}
              onRetry={() => handleSend(item.retryContent)}
            />
          );
        })}
        {sending && pendingAssistantText !== null && (
          <MessageBubble
            message={{ id: "pending", role: "assistant", content: pendingAssistantText, created_at: "" }}
            streaming
          />
        )}
      </div>
      {isReadOnly ? (
        <div className={styles.readOnlyNotice}>
          <IconLock size={14} />
          Schreibgeschützt — Unterhaltung von {conversation.owner_email}
        </div>
      ) : (
        <Composer onSend={handleSend} disabled={sending} />
      )}
    </>
  );
}
