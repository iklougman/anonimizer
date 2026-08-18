"use client";

import { useState } from "react";
import type { KeyboardEvent } from "react";
import styles from "./Composer.module.css";

export function Composer({
  onSend,
  disabled,
}: {
  onSend: (content: string) => void;
  disabled: boolean;
}) {
  const [value, setValue] = useState("");

  function submit() {
    const trimmed = value.trim();
    if (!trimmed) return;
    onSend(trimmed);
    setValue("");
  }

  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      submit();
    }
  }

  return (
    <div className={styles.composer}>
      <textarea
        className={styles.textarea}
        placeholder="Nachricht eingeben…"
        value={value}
        disabled={disabled}
        onChange={(event) => setValue(event.target.value)}
        onKeyDown={handleKeyDown}
      />
      <button type="button" className={styles.sendButton} disabled={disabled} onClick={submit}>
        Send
      </button>
    </div>
  );
}
