import { describe, expect, it } from "vitest";

import { parseMailboxImportPreview } from "./mailboxImport";

describe("parseMailboxImportPreview", () => {
  it("normalizes four-field records and ignores blank lines", () => {
    const result = parseMailboxImportPreview(
      "\uFEFF USER@Example.COM ---- password ---- client ---- refresh\n\nsecond@example.com----p----c----r",
    );

    expect(result.records.map((record) => [record.lineNumber, record.email])).toEqual([
      [1, "user@example.com"],
      [3, "second@example.com"],
    ]);
    expect(result.blankLines).toBe(1);
    expect(result.issues).toEqual([]);
  });

  it("classifies malformed, same-batch, and existing records", () => {
    const result = parseMailboxImportPreview(
      "broken@example.com----password----client\nfirst@example.com----p----c----r\nFIRST@example.com----p2----c2----r2\nexisting@example.com----p3----c3----r3",
      ["Existing@Example.com"],
    );

    expect(result.records.map((record) => record.email)).toEqual(["first@example.com"]);
    expect(result.issues.map((issue) => issue.code)).toEqual([
      "FORMAT",
      "DUPLICATE_IN_BATCH",
      "DUPLICATE_EXISTING",
    ]);
  });
});
