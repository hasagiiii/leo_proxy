import { afterEach, describe, expect, it, vi } from "vitest";

import { VideoTaskApi } from "./api";

describe("registration client monitor API", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("uses Admin endpoints, UTC windows, filters, pagination and encoded client IDs", async () => {
    const fetchMock = vi.fn(async () => new Response("{}", {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }));
    vi.stubGlobal("fetch", fetchMock);
    const api = new VideoTaskApi({
      apiBase: "https://api.example.test",
      apiKey: "api",
      adminKey: "admin",
    });
    const from = "2026-08-15T01:10:00.000Z";
    const to = "2026-08-15T01:20:00.000Z";

    await api.getRegistrationClients({
      from,
      to,
      health: "ABNORMAL",
      search: "1054adf6",
      limit: 50,
      offset: 50,
    });
    await api.getRegistrationClientDetail("desktop/client 01", from, to);
    await api.getRegistrationClientTasks("desktop/client 01", {
      from,
      to,
      status: "FAILED",
      search: "child@example.test",
      limit: 50,
      offset: 100,
    });

    const calls = fetchMock.mock.calls as unknown as [string, RequestInit][];
    expect(calls[0][0]).toContain("/admin/registration-clients?");
    expect(calls[0][0]).toContain("health=ABNORMAL");
    expect(calls[0][0]).toContain("search=1054adf6");
    expect(calls[0][0]).toContain("offset=50");
    expect(calls[1][0]).toContain("/registration-clients/desktop%2Fclient%2001?");
    expect(calls[2][0]).toContain("/registration-clients/desktop%2Fclient%2001/registrations?");
    expect(calls[2][0]).toContain("status=FAILED");
    expect(calls[2][0]).toContain("offset=100");
    expect(new Headers(calls[0][1].headers).get("X-Admin-Key")).toBe("admin");
  });
});
