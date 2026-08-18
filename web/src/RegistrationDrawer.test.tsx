import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { RegistrationDrawer } from "./RegistrationDrawer";
import type { ParentAccount, RegistrationPoolSettings, RegistrationRecord } from "./types";

const parent: ParentAccount = {
  parent_account_uuid: "67420f85-e589-4356-9c3a-12345678d086",
  email: "parent@example.com",
  password: "fixture-parent-password",
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
  promotable_registration_count: 1,
  version: 4,
  created_at: "2026-08-13T05:45:00Z",
  updated_at: "2026-08-13T05:45:00Z",
};

const record: RegistrationRecord = {
  registration_uuid: "b04fc99a-c906-438a-81c3-12345678c70a",
  parent_account_uuid: parent.parent_account_uuid,
  parent_email: parent.email,
  email: "child@example.com",
  client_id: "desktop-a",
  status: "SUCCEEDED",
  registered_email: "child@example.com",
  verified_email: "child@example.com",
  awarded_points: 8500,
  is_used: false,
  cookie_count: 12,
  validation_attempts: 1,
  validation_error_code: null,
  validation_error_message: null,
  started_at: "2026-08-13T06:00:00Z",
  reported_at: "2026-08-13T06:02:00Z",
  validation_finished_at: "2026-08-13T06:03:00Z",
  promoted_at: null,
  account_uuid: null,
  promotable: true,
  cookie_status: "VERIFIED",
  version: 3,
  created_at: "2026-08-13T06:00:00Z",
  updated_at: "2026-08-13T06:03:00Z",
};

const settings: RegistrationPoolSettings = {
  target_space_uuid: "50000000-0000-0000-0000-000000000001",
  target_space_name: "注册账号空间",
  target_space_status: "ACTIVE",
  default_max_concurrency: 3,
  promotion_available: true,
  version: 1,
  updated_at: "2026-08-13T06:00:00Z",
};

describe("RegistrationDrawer", () => {
  it("renders mother status, verified server metadata and no secrets", () => {
    const markup = renderToStaticMarkup(
      <RegistrationDrawer
        parent={parent}
        records={[record]}
        settings={settings}
        loading={false}
        onClose={vi.fn()}
        onFilter={vi.fn()}
        onRefresh={vi.fn()}
        onRevalidate={vi.fn()}
        onPromote={vi.fn()}
      />,
    );

    expect(markup).toContain("注册记录");
    expect(markup).toContain("连续低于8000");
    expect(markup).toContain("2 / 3");
    expect(markup).toContain("Cookie 已验证");
    expect(markup).toContain("加入账号池");
    expect(markup).not.toContain("fixture-cookie-secret");
    expect(markup).not.toContain("fixture-video-token");
  });
});
