import type { ReactNode } from "react";
import { ConversationSidebar } from "@/components/ConversationSidebar";
import styles from "./layout.module.css";

export default function AnonymizationLayout({ children }: { children: ReactNode }) {
  return (
    <div className={styles.shell}>
      <ConversationSidebar />
      <main className={styles.main}>{children}</main>
    </div>
  );
}
