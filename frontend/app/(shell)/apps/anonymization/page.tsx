import { IconMessageCircle2 } from "@tabler/icons-react";
import styles from "./empty-state.module.css";

export default function ChatEmptyState() {
  return (
    <div className={styles.emptyState}>
      <IconMessageCircle2 size={40} stroke={1.5} />
      <p>Keine Unterhaltung ausgewählt</p>
      <span>Wählen Sie links eine Unterhaltung aus oder starten Sie eine neue Anfrage.</span>
    </div>
  );
}
