import { afterEach, describe, expect, it, vi } from "vitest";

import { VideoTaskApi } from "./api";
import type { ParentAccount, ParentAccountList, ParentAccountStats } from "./types";

const account: ParentAccount = {
  parent_account_uuid: "67420f85-e589-4356-9c3a-12345678d086",
  email: "user@example.com",
  password: "Visible-Secret",
  invite_url: "https://example.test/join",
  invite_success_count: 7,
  invite_failure_count: 2,
  status: "ACTIVE",
  consecutive_150_count: 2,
  exhausted_reason: null,
  exhausted_at: null,
  legacy_invite_success_count: 0,
  legacy_invite_failure_count: 0,
  running_registration_count: 1,
  traceable_registration_count: 9,
  promotable_registration_count: 3,
  version: 4,
  created_at: "2026-08-13T05:45:00Z",
  updated_at: "2026-08-13T05:45:00Z",
};

describe("VideoTaskApi parent-account methods", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("uses authenticated parent-account endpoints and exact payloads", async () => {
    const list: ParentAccountList = { items: [account], total: 1, limit: 50, offset: 100 };
    const stats: ParentAccountStats = {
      total_parent_accounts: 1,
      total_invite_successes: 7,
      total_invite_failures: 2,
      active_parent_accounts: 1,
      exhausted_parent_accounts: 0,
      traceable_registrations: 9,
      promotable_registrations: 3,
      legacy_invite_successes: 0,
      legacy_invite_failures: 0,
    };
    const payloads: unknown[] = [list, stats, { requested: 1, imported: 1, duplicates: 0, invalid: 0, blank_lines: 0, issues: [] }, "", account];
    const fetchMock = vi.fn(async (_url: string, init?: RequestInit) => {
      const payload = payloads.shift();
      if (init?.method === "DELETE") return new Response(null, { status: 204 });
      return new Response(JSON.stringify(payload), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    });
    vi.stubGlobal("fetch", fetchMock);
    const api = new VideoTaskApi({
      apiBase: "https://api.example.test",
      apiKey: "business-key",
      adminKey: "admin-key",
    });

    await api.getParentAccounts("user", 50, 100);
    await api.getParentAccountStats();
    await api.importParentAccounts("user@example.com Secret https://example.test/join");
    await api.deleteParentAccount(account.parent_account_uuid);
    await api.recordParentAccountInvitationResult(account.parent_account_uuid, false);

    const calls = fetchMock.mock.calls as unknown as [string, RequestInit][];
    expect(calls[0][0]).toBe("https://api.example.test/admin/parent-accounts?limit=50&offset=100&search=user");
    expect(calls[1][0]).toBe("https://api.example.test/admin/parent-accounts/stats");
    expect(calls[2][0]).toBe("https://api.example.test/admin/parent-accounts/import");
    expect(calls[3][0]).toBe(`https://api.example.test/admin/parent-accounts/${account.parent_account_uuid}`);
    expect(calls[3][1].method).toBe("DELETE");
    expect(calls[4][0]).toBe(`https://api.example.test/admin/parent-accounts/${account.parent_account_uuid}/invitation-result`);
    expect(calls[4][1].method).toBe("POST");
    expect(JSON.parse(String(calls[4][1].body))).toEqual({ success: false });
    for (const [, init] of calls) {
      expect(new Headers(init.headers).get("X-Admin-Key")).toBe("admin-key");
    }
  });
});
