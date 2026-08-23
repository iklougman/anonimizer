"use client";

import { useEffect, useState, type KeyboardEvent } from "react";
import { signIn, useSession } from "next-auth/react";
import { usePathname, useRouter } from "next/navigation";
import { IconMessage2, IconPlus, IconTrash } from "@tabler/icons-react";
import {
  createConversation,
  deleteConversation,
  listConversations,
} from "@/lib/api/conversations";
import type { ConversationSummary } from "@/lib/api/types";
import { useMe } from "@/components/MeProvider";
import styles from "./ConversationSidebar.module.css";

export function ConversationSidebar() {
  const { data: session } = useSession();
  const router = useRouter();
  const pathname = usePathname();
  const me = useMe();
  const [conversations, setConversations] = useState<ConversationSummary[] | null>(null);
  const [loadError, setLoadError] = useState(false);
  // No caller ever passed this as a prop (dead code before this fix) -- a
  // Next.js layout has no access to the matched page's dynamic route params,
  // so it has to be derived from the current path instead.
  const activeConversationId = pathname.match(/^\/apps\/anonymization\/c\/([^/]+)/)?.[1];
  // While `me` is still loading, permission-gated controls stay hidden rather
  // than briefly flashing enabled -- fails closed on the loading state too.
  const canCreate = me?.permissions.includes("conversations:create") ?? false;
  const canDeleteAny = me?.permissions.includes("conversations:delete:any") ?? false;

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
      setConversations((current) => [created, ...(current ?? [])]);
      router.push(`/apps/anonymization/c/${created.id}`);
    } catch {
      setLoadError(true);
    }
  }

  async function handleDelete(conversationId: string, title: string) {
    if (!session?.accessToken) return;
    try {
      await deleteConversation(session.accessToken, conversationId);
      setConversations((current) => current?.filter((c) => c.id !== conversationId) ?? null);
      if (activeConversationId === conversationId) {
        router.push("/apps/anonymization");
      }
    } catch {
      setLoadError(true);
    }
  }

  function goToConversation(conversationId: string) {
    router.push(`/apps/anonymization/c/${conversationId}`);
  }

  function handleRowKeyDown(event: KeyboardEvent<HTMLDivElement>, conversationId: string) {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      goToConversation(conversationId);
    }
  }

  return (
    <nav className={styles.sidebar}>
      {canCreate && (
        <button type="button" className={styles.newButton} onClick={handleCreate}>
          <IconPlus size={16} stroke={2.5} />
          Neue Anfrage
        </button>
      )}
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
      {conversations === null && !loadError && (
        <div className={styles.skeletonList} aria-hidden="true">
          <div className={styles.skeletonItem} />
          <div className={styles.skeletonItem} />
          <div className={styles.skeletonItem} />
        </div>
      )}
      {conversations !== null && conversations.length === 0 && !loadError && (
        <div className={styles.emptyState}>
          <IconMessage2 size={28} stroke={1.5} />
          <p>Noch keine Unterhaltungen.</p>
          {canCreate && <span>Mit &quot;Neue Anfrage&quot; loslegen.</span>}
        </div>
      )}
      {conversations?.map((conversation) => {
        const title = conversation.title ?? "Neue Anfrage";
        const canDelete = conversation.is_own || canDeleteAny;
        const isActive = conversation.id === activeConversationId;
        return (
          <div
            key={conversation.id}
            role="button"
            tabIndex={0}
            aria-current={isActive || undefined}
            className={isActive ? `${styles.conversationItem} ${styles.conversationItemActive}` : styles.conversationItem}
            onClick={() => goToConversation(conversation.id)}
            onKeyDown={(event) => handleRowKeyDown(event, conversation.id)}
          >
            <span className={styles.conversationTitle}>{title}</span>
            {!conversation.is_own && (
              <span className={styles.ownerBadge}>{conversation.owner_email.split("@")[0]}</span>
            )}
            {canDelete && (
              <button
                type="button"
                aria-label={`Delete ${title}`}
                className={styles.deleteButton}
                onClick={(event) => {
                  event.stopPropagation();
                  handleDelete(conversation.id, title);
                }}
              >
                <IconTrash size={14} stroke={1.75} />
              </button>
            )}
          </div>
        );
      })}
    </nav>
  );
}
