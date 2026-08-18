import { describe, expect, it } from "vitest";

import {
  COOKIE_IMPORT_MAX_BYTES,
  COOKIE_IMPORT_TERMINAL,
  cookieImportFileError,
  defaultCookieImportSpaceName,
} from "./cookieImport";

describe("cookie import helpers", () => {
  it("uses the documented terminal batch states", () => {
    expect([...COOKIE_IMPORT_TERMINAL]).toEqual(["COMPLETED", "PARTIAL_FAILED", "FAILED"]);
  });

  it("builds the operator-friendly default space name", () => {
    expect(defaultCookieImportSpaceName(new Date("2026-08-13T03:04:00Z"), true)).toBe("cookie-import-20260813-0304");
  });

  it("prechecks ZIP extension and the 20 MiB browser limit", () => {
    expect(COOKIE_IMPORT_MAX_BYTES).toBe(20 * 1024 * 1024);
    expect(cookieImportFileError(new File(["x"], "cookies.txt", { type: "text/plain" }))).toContain("ZIP");
    expect(cookieImportFileError({ name: "cookies.zip", size: COOKIE_IMPORT_MAX_BYTES + 1 })).toContain("20 MiB");
    expect(cookieImportFileError({ name: "cookies.zip", size: COOKIE_IMPORT_MAX_BYTES })).toBe("");
  });
});
