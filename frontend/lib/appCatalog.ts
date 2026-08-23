import { IconApps, IconShieldLock } from "@tabler/icons-react";
import type { ComponentType } from "react";

interface AppCatalogEntry {
  icon: ComponentType<{ size?: number }>;
  color: string;
}

const APP_CATALOG: Record<string, AppCatalogEntry> = {
  anonymization: { icon: IconShieldLock, color: "teal" },
};

const DEFAULT_APP_CATALOG_ENTRY: AppCatalogEntry = { icon: IconApps, color: "gray" };

/** Icon/color for a catalog app's `key`, with a sensible fallback so a future
 * app shows up correctly before anyone remembers to add it here. */
export function getAppCatalogEntry(key: string): AppCatalogEntry {
  return APP_CATALOG[key] ?? DEFAULT_APP_CATALOG_ENTRY;
}
