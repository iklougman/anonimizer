import { afterEach } from "vitest";
import { cleanup } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";

// jsdom has no matchMedia implementation; Mantine's MantineProvider reads it
// (for color-scheme detection) on every mount, so any test rendering a
// Mantine component needs this polyfill or it throws "window.matchMedia is
// not a function". Always reports no-match/no-op listeners -- fine here
// since the app forces a single light color scheme (forceColorScheme="light"
// in app/layout.tsx) and never actually depends on matchMedia's real value.
Object.defineProperty(window, "matchMedia", {
  writable: true,
  value: (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  }),
});

// Without vitest's `globals: true` (not enabled in this project -- every test
// file imports explicitly from "vitest"), @testing-library/react's automatic
// afterEach cleanup cannot find a global `afterEach` to hook into, so renders
// from earlier tests in the same file stay in jsdom's document.body and leak
// into later tests' queries. Registering it explicitly here fixes that.
afterEach(() => {
  cleanup();
});
