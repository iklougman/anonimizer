"use client";

import { useEffect, useState } from "react";
import { useSession } from "next-auth/react";
import { useRouter } from "next/navigation";
import {
  createConversation,
  deleteConversation,
  listConversations,
} from "@/lib/api/conversations";
import type { ConversationSummary } from "@/lib/api/types";
import styles from "./ConversationSidebar.module.css";

export function ConversationSidebar({ activeConversationId }: { activeConversationId?: string }) {
  const { data: session } = useSession();
  const router = useRouter();
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);

  useEffect(() => {
    if (!session?.accessToken) return;
    listConversations(session.accessToken).then(setConversations);
  }, [session?.accessToken]);

  async function handleCreate() {
    if (!session?.accessToken) return;
    const created = await createConversation(session.accessToken);
    setConversations((current) => [created, ...current]);
    router.push(`/c/${created.id}`);
  }

  async function handleDelete(conversationId: string, title: string) {
    if (!session?.accessToken) return;
    await deleteConversation(session.accessToken, conversationId);
    setConversations((current) => current.filter((c) => c.id !== conversationId));
    if (activeConversationId === conversationId) {
      router.push("/");
    }
  }

  return (
    <nav className={styles.sidebar}>
      <button type="button" className={styles.newButton} onClick={handleCreate}>
        + Neue Anfrage
      </button>
      {conversations.map((conversation) => {
        const title = conversation.title ?? "Neue Anfrage";
        return (
          <div
            key={conversation.id}
            className={
              conversation.id === activeConversationId
                ? `${styles.conversationItem} ${styles.conversationItemActive}`
                : styles.conversationItem
            }
            onClick={() => router.push(`/c/${conversation.id}`)}
          >
            <span className={styles.conversationTitle}>{title}</span>
            <button
              type="button"
              aria-label={`Delete ${title}`}
              className={styles.deleteButton}
              onClick={(event) => {
                event.stopPropagation();
                handleDelete(conversation.id, title);
              }}
            >
              ×
            </button>
          </div>
        );
      })}
    </nav>
  );
}
