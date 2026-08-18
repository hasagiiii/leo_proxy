import { describe, expect, it } from "vitest";

import { parseParentAccountImportPreview } from "./parentAccountImport";

describe("parseParentAccountImportPreview", () => {
  it("normalizes whitespace-delimited parent accounts", () => {
    const result = parseParentAccountImportPreview(
      "\uFEFF USER@Example.COM Secret-1 https://example.test/join\n\nsecond@example.com\tSecret-2\thttp://example.test/other",
    );

    expect(result.records.map((record) => [record.lineNumber, record.email])).toEqual([
      [1, "user@example.com"],
      [3, "second@example.com"],
    ]);
    expect(result.blankLines).toBe(1);
    expect(result.issues).toEqual([]);
  });

  it("matches backend validation and duplicate categories", () => {
    const result = parseParentAccountImportPreview(
      "broken@example.com only-two\n"
        + "bad-email Secret https://example.test/join\n"
        + "first@example.com Secret ftp://example.test/join\n"
        + "valid@example.com Secret https://example.test/join\n"
        + "VALID@example.com Other https://example.test/other\n"
        + "existing@example.com Secret https://example.test/existing",
      ["Existing@Example.com"],
    );

    expect(result.records.map((record) => record.email)).toEqual(["valid@example.com"]);
    expect(result.issues.map((issue) => issue.code)).toEqual([
      "FORMAT",
      "INVALID_EMAIL",
      "INVALID_URL",
      "DUPLICATE_IN_BATCH",
      "DUPLICATE_EXISTING",
    ]);
  });
});
