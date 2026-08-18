import type { MessageOut } from "@/lib/api/types";
import styles from "./MessageBubble.module.css";

interface MessageErrorProps {
  status: number;
  message: string;
}

interface MessageBubbleProps {
  message?: MessageOut;
  error?: MessageErrorProps;
  onRetry?: () => void;
}

export function MessageBubble({ message, error, onRetry }: MessageBubbleProps) {
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

  if (!message) return null;

  const isUser = message.role === "user";
  return (
    <div className={`${styles.row} ${isUser ? styles.rowUser : styles.rowAssistant}`}>
      <div className={`${styles.bubble} ${isUser ? styles.bubbleUser : styles.bubbleAssistant}`}>
        {message.content}
      </div>
    </div>
  );
}
