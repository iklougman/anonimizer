"use client";

import { useEffect, useState } from "react";
import { signIn, useSession } from "next-auth/react";
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
  const [loadError, setLoadError] = useState(false);

  useEffect(() => {
    // A refresh failure (e.g. the Keycloak refresh token itself expired)
    // leaves session.accessToken stale -- every API call would 401 forever
    // with no recovery. Force a fresh login rather than let that happen.
    if (session?.error === "RefreshAccessTokenError") {
      signIn("keycloak");
    }
  }, [session?.error]);

  useEffect(() => {
    if (!session?.accessToken) return;
    setLoadError(false);
    listConversations(session.accessToken)
      .then(setConversations)
      .catch(() => setLoadError(true));
  }, [session?.accessToken]);

  async function handleCreate() {
    if (!session?.accessToken) return;
    try {
      const created = await createConversation(session.accessToken);
      setConversations((current) => [created, ...current]);
      router.push(`/c/${created.id}`);
    } catch {
      setLoadError(true);
    }
  }

  async function handleDelete(conversationId: string, title: string) {
    if (!session?.accessToken) return;
    try {
      await deleteConversation(session.accessToken, conversationId);
      setConversations((current) => current.filter((c) => c.id !== conversationId));
      if (activeConversationId === conversationId) {
        router.push("/");
      }
    } catch {
      setLoadError(true);
    }
  }

  return (
    <nav className={styles.sidebar}>
      <button type="button" className={styles.newButton} onClick={handleCreate}>
        + Neue Anfrage
      </button>
      {loadError && (
        <div className={styles.loadError}>
          <span>Conversations could not be loaded.</span>
          <button
            type="button"
            className={styles.retryButton}
            onClick={() => {
              if (!session?.accessToken) return;
              setLoadError(false);
              listConversations(session.accessToken)
                .then(setConversations)
                .catch(() => setLoadError(true));
            }}
          >
            Try again
          </button>
        </div>
      )}
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
