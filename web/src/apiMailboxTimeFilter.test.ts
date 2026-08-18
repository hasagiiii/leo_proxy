import { afterEach, describe, expect, it, vi } from "vitest";

import { VideoTaskApi } from "./api";

describe("VideoTaskApi mailbox import-time filter", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("sends the selected mutually exclusive period and local timezone offset", async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ items: [], total: 0, limit: 50, offset: 0 }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }));
    vi.stubGlobal("fetch", fetchMock);
    const api = new VideoTaskApi({
      apiBase: "https://api.example.test",
      apiKey: "business-key",
      adminKey: "admin-key",
    });

    await api.getMailboxes("ACTIVE", "user", 50, 100, "yesterday", 480);

    const calls = fetchMock.mock.calls as unknown as [string, RequestInit][];
    const [url, init] = calls[0];
    expect(url).toBe("https://api.example.test/admin/mailboxes?limit=50&offset=100&status=ACTIVE&search=user&import_period=yesterday&timezone_offset_minutes=480");
    expect(new Headers(init?.headers).get("X-Admin-Key")).toBe("admin-key");
  });
});
