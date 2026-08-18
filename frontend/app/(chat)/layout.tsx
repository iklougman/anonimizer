import type { ReactNode } from "react";
import { ConversationSidebar } from "@/components/ConversationSidebar";
import { MeProvider } from "@/components/MeProvider";
import styles from "./layout.module.css";

export default function ChatLayout({ children }: { children: ReactNode }) {
  return (
    <MeProvider>
      <div className={styles.shell}>
        <ConversationSidebar />
        <main className={styles.main}>{children}</main>
      </div>
    </MeProvider>
  );
}
