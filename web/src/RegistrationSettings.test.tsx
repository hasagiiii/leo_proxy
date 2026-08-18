import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { RegistrationSettings, registrationSettingsPayload } from "./RegistrationSettings";
import type { RegistrationPoolSettings, Space } from "./types";

const settings: RegistrationPoolSettings = {
  target_space_uuid: "50000000-0000-0000-0000-000000000001",
  target_space_name: "注册账号空间",
  target_space_status: "ACTIVE",
  default_max_concurrency: 3,
  promotion_available: true,
  version: 7,
  updated_at: "2026-08-13T06:00:00Z",
};

const spaces: Space[] = [
  {
    space_uuid: "50000000-0000-0000-0000-000000000001",
    name: "注册账号空间",
    status: "ACTIVE",
    routing_key: null,
    max_concurrency: 3,
    active_tasks: 0,
    created_at: "2026-08-13T06:00:00Z",
    updated_at: "2026-08-13T06:00:00Z",
  },
];

describe("RegistrationSettings", () => {
  it("renders the server-backed fixed destination configuration", () => {
    const markup = renderToStaticMarkup(
      <RegistrationSettings settings={settings} spaces={spaces} onSave={vi.fn()} />,
    );

    expect(markup).toContain("注册账号入池设置");
    expect(markup).toContain("固定目标空间");
    expect(markup).toContain("默认最大并发");
    expect(markup).toContain("空间可用");
    expect(markup).toContain("注册账号空间 · ACTIVE");
  });

  it("submits optimistic version and pauses when no space is selected", () => {
    expect(registrationSettingsPayload(settings, settings.target_space_uuid ?? "", 5)).toEqual({
      target_space_uuid: settings.target_space_uuid,
      default_max_concurrency: 5,
      expected_version: 7,
    });
    expect(registrationSettingsPayload(settings, "", 3).target_space_uuid).toBeNull();
  });
});
