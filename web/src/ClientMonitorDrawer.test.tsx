import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import type { VideoTaskApi } from "./api";
import { ClientMonitorDrawer } from "./ClientMonitorDrawer";
import type { RegistrationClient } from "./types";

const client: RegistrationClient = {
  client_id: "invitation-desktop-00000000-0000-0000-0000-00001054adf6",
  display_name: "客户端 1054adf6",
  health: "ABNORMAL",
  health_reasons: [{ code: "STALE_LEASE", message: "存在 1 个运行租约已过期的任务" }],
  last_activity_at: "2026-08-15T01:20:00Z",
  jobs: 52,
  succeeded: 49,
  failed: 2,
  processing: 1,
  retry_wait: 0,
  stalled: 1,
  success_rate: 49 / 51,
  average_duration_seconds: 31.4,
  latest_error_code: "REGISTRATION_TIMEOUT",
  latest_error_message: "registration timed out",
};

describe("ClientMonitorDrawer", () => {
  it("shows health reasons, trend and task filters without credential fields", () => {
    const markup = renderToStaticMarkup(
      <ClientMonitorDrawer
        client={client}
        window={{ from: "2026-08-15T01:10:00Z", to: "2026-08-15T01:20:00Z" }}
        api={{} as VideoTaskApi}
        onClose={vi.fn()}
        onCopy={vi.fn()}
      />,
    );

    expect(markup).toContain("CLIENT WORKLOAD TRACE");
    expect(markup).toContain("客户端 1054adf6");
    expect(markup).toContain("STALE_LEASE");
    expect(markup).toContain("时段作业趋势");
    expect(markup).toContain("疑似停滞");
    expect(markup).toContain("最近注册任务");
    expect(markup).not.toContain("report_token");
    expect(markup).not.toContain("session_ciphertext");
    expect(markup).not.toContain("video_token_ciphertext");
  });
});
