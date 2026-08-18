import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { ParentAccountsView } from "./App";
import type { ParentAccount, ParentAccountStats } from "./types";

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

describe("ParentAccountsView", () => {
  it("shows plaintext credentials, invitation counters, and row actions", () => {
    const onCopyPassword = vi.fn();
    const markup = renderToStaticMarkup(
      <ParentAccountsView
        parentAccounts={[account]}
        stats={stats}
        total={1}
        page={0}
        search=""
        loading={false}
        onPage={vi.fn()}
        onSearch={vi.fn()}
        onImport={vi.fn()}
        onCopyPassword={onCopyPassword}
        onCopyInviteUrl={vi.fn()}
        onOpenRegistrations={vi.fn()}
        onDelete={vi.fn()}
      />,
    );

    expect(markup).toContain("母号池");
    expect(markup).toContain("user@example.com");
    expect(markup).toContain("密码");
    expect(markup).toContain("Visible-Secret");
    expect(markup).toContain('aria-label="复制密码 user@example.com"');
    expect(markup).toContain("parent-account-secret");
    expect(markup).toContain("https://example.test/join");
    expect(markup).toContain("连续低于8000");
    expect(markup).toContain("2 / 3");
    expect(markup).toContain("注册记录");
    expect(markup).toContain("删除母号");
    expect(markup).not.toContain('type="password"');
  });
});
