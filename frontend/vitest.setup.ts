import { afterEach } from "vitest";
import { cleanup } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";

// Without vitest's `globals: true` (not enabled in this project -- every test
// file imports explicitly from "vitest"), @testing-library/react's automatic
// afterEach cleanup cannot find a global `afterEach` to hook into, so renders
// from earlier tests in the same file stay in jsdom's document.body and leak
// into later tests' queries. Registering it explicitly here fixes that.
afterEach(() => {
  cleanup();
});
