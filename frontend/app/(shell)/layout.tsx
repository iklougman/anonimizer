import type { ReactNode } from "react";
import { AvailableAppsProvider } from "@/components/AvailableAppsProvider";
import { AppShellChrome } from "@/components/layout/AppShellChrome";

export default function ShellLayout({ children }: { children: ReactNode }) {
  return (
    <AvailableAppsProvider>
      <AppShellChrome>{children}</AppShellChrome>
    </AvailableAppsProvider>
  );
}
