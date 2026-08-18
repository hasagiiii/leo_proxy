import { afterEach, describe, expect, it, vi } from "vitest";

import { VideoTaskApi } from "./api";
import type { MailboxCodeResult } from "./types";

describe("VideoTaskApi.queryMailboxCode", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("queries the mailbox-code endpoint without an API key", async () => {
    const expected: MailboxCodeResult = {
      email: "user@example.com",
      code: "483921",
      received_at: "2026-08-13T01:02:03Z",
      subject: "Your verification code",
      sender: "sender@example.com",
      message_id: "message-id",
      matched_by: "KEYWORD_NEARBY",
    };
    const fetchMock = vi.fn(async () => new Response(JSON.stringify(expected), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }));
    vi.stubGlobal("fetch", fetchMock);
    const api = new VideoTaskApi({
      apiBase: "https://api.example.test",
      apiKey: "business-key",
      adminKey: "admin-key",
    });

    const result = await (api as unknown as {
      queryMailboxCode: (email: string, timeoutSeconds: number) => Promise<MailboxCodeResult>;
    }).queryMailboxCode("User@Example.com", 60);

    expect(result).toEqual(expected);
    expect(fetchMock).toHaveBeenCalledOnce();
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("https://api.example.test/v1/mailbox-codes/query");
    expect(init.method).toBe("POST");
    expect(new Headers(init.headers).get("X-API-Key")).toBeNull();
    expect(new Headers(init.headers).get("X-Admin-Key")).toBeNull();
    expect(JSON.parse(String(init.body))).toEqual({
      email: "User@Example.com",
      timeout_seconds: 60,
    });
  });
});
