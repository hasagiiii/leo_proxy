import { afterEach, describe, expect, it, vi } from "vitest";

import { VideoTaskApi } from "./api";
import type { CookieImportBatch, CookieImportBatchList } from "./types";

const batch: CookieImportBatch = {
  batch_uuid: "f543b87f-8fc6-466b-9c5a-06cfd7e599f0",
  status: "QUEUED",
  archive_filename: "cookies.zip",
  archive_sha256: "a".repeat(64),
  space_name: "cookie-import-20260813-1104",
  item_count: 10,
  queued: 10,
  running: 0,
  created: 0,
  updated: 0,
  failed: 0,
  total_balance_credits: 0,
  tasks_after_import: 0,
  completed_tasks_after_import: 0,
  failed_tasks_after_import: 0,
  consumed_credits_after_import: 0,
  created_at: "2026-08-13T03:04:00Z",
  started_at: null,
  finished_at: null,
  items: [],
};

describe("VideoTaskApi Cookie ZIP imports", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("uploads multipart data with admin and idempotency headers", async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify(batch), {
      status: 202,
      headers: { "Content-Type": "application/json" },
    }));
    vi.stubGlobal("fetch", fetchMock);
    const api = new VideoTaskApi({ apiBase: "https://api.example.test", apiKey: "business", adminKey: "admin" });
    const file = new File(["fixture"], "cookies.zip", { type: "application/zip" });

    await api.createCookieImport(file, "cookie-import-20260813-1104", "request-uuid");

    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    const headers = new Headers(init.headers);
    expect(url).toBe("https://api.example.test/admin/account-cookie-imports");
    expect(init.method).toBe("POST");
    expect(headers.get("X-Admin-Key")).toBe("admin");
    expect(headers.get("Idempotency-Key")).toBe("request-uuid");
    expect(headers.get("Content-Type")).toBeNull();
    expect(init.body).toBeInstanceOf(FormData);
    const body = init.body as FormData;
    expect(body.get("archive")).toBe(file);
    expect(body.get("space_name")).toBe("cookie-import-20260813-1104");
  });

  it("uses the batch list and encoded detail URLs", async () => {
    const list: CookieImportBatchList = { batches: [batch], total: 1, limit: 20, offset: 0 };
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify(list), { status: 200, headers: { "Content-Type": "application/json" } }))
      .mockResolvedValueOnce(new Response(JSON.stringify(batch), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);
    const api = new VideoTaskApi({ apiBase: "/api/", apiKey: "business", adminKey: "admin" });

    await api.listCookieImports(20, 40);
    await api.getCookieImport("batch/id");

    expect(fetchMock.mock.calls[0][0]).toBe("/api/admin/account-cookie-imports?limit=20&offset=40");
    expect(fetchMock.mock.calls[1][0]).toBe("/api/admin/account-cookie-imports/batch%2Fid");
  });
});
