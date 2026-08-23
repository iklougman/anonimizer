import type { MessageOut } from "@/lib/api/types";
import styles from "./MessageBubble.module.css";

interface MessageErrorProps {
  status: number;
  message: string;
}

interface MessageAbortedProps {
  partialContent: string;
}

interface MessageBubbleProps {
  message?: MessageOut;
  error?: MessageErrorProps;
  aborted?: MessageAbortedProps;
  /** Still receiving tokens for this turn -- renders a blinking cursor after
   * the accumulated text so the user can tell "still generating" from
   * "finished, just short." */
  streaming?: boolean;
  onRetry?: () => void;
}

export function MessageBubble({ message, error, aborted, streaming, onRetry }: MessageBubbleProps) {
  if (error) {
    const canRetry = error.status !== 422;
    return (
      <div className={`${styles.row} ${styles.rowAssistant}`}>
        <div className={`${styles.bubble} ${styles.bubbleError}`} role="alert">
          <p>{error.message}</p>
          {canRetry && (
            <button type="button" className={styles.retryButton} onClick={onRetry}>
              Try again
            </button>
          )}
        </div>
      </div>
    );
  }

  if (aborted) {
    // The partial text was already guard-approved chunk by chunk before it
    // arrived (see backend/app/api/chat.py's _stream_and_guard) -- shown as a
    // normal assistant bubble, with a distinct banner marking that the turn
    // itself was never completed or saved.
    return (
      <div className={`${styles.row} ${styles.rowAssistant}`}>
        <div className={`${styles.bubble} ${styles.bubbleAssistant}`}>
          {aborted.partialContent}
          <div className={styles.abortedBanner} role="alert">
            <span>Response was interrupted and could not be completed safely.</span>
            <button type="button" className={styles.retryButton} onClick={onRetry}>
              Try again
            </button>
          </div>
        </div>
      </div>
    );
  }

  if (!message) return null;

  const isUser = message.role === "user";
  return (
    <div className={`${styles.row} ${isUser ? styles.rowUser : styles.rowAssistant}`}>
      <div className={`${styles.bubble} ${isUser ? styles.bubbleUser : styles.bubbleAssistant}`}>
        {message.content}
        {streaming && <span className={styles.cursor} aria-hidden="true" />}
      </div>
    </div>
  );
}
