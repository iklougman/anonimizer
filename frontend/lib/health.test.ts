import { describe, expect, it } from "vitest";
import { getHealthStatus } from "./health";

describe("getHealthStatus", () => {
  it("reports ok", () => {
    expect(getHealthStatus()).toEqual({ status: "ok" });
  });
});
