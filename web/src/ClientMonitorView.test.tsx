import { readFileSync } from "node:fs";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import type { VideoTaskApi } from "./api";
import { ClientMonitorView, clientMonitorWindow } from "./ClientMonitorView";

const appSource = readFileSync(new URL("./App.tsx", import.meta.url), "utf8");

describe("ClientMonitorView", () => {
  it("defaults to a rolling ten-minute UTC window", () => {
    const now = new Date("2026-08-15T01:20:00.000Z");
    expect(clientMonitorWindow("10m", "", "", now)).toEqual({
      from: "2026-08-15T01:10:00.000Z",
      to: "2026-08-15T01:20:00.000Z",
    });
    expect(clientMonitorWindow("custom", "2026-08-15T02:00", "2026-08-15T01:00", now)).toBeNull();
  });

  it("renders monitoring controls and explicit no-activity semantics", () => {
    const markup = renderToStaticMarkup(
      <ClientMonitorView
        api={{} as VideoTaskApi}
        autoRefresh
        refreshToken={0}
        onCopy={vi.fn()}
      />,
    );

    expect(markup).toContain("客户端监控");
    expect(markup).toContain("CLIENT REGISTRY / LIVE TELEMETRY");
    expect(markup).toContain("10 分钟");
    expect(markup).toContain("无作业");
    expect(markup).toContain("15 秒刷新");
    expect(markup).not.toContain("离线客户端");
  });

  it("keeps client monitoring out of the migrated navigation", () => {
    expect(appSource).not.toContain('["registration-clients", Activity, "客户端监控", "06"]');
    expect(appSource).toContain('["tasks", BarChart3, "任务中心", "03"]');
    expect(appSource).toContain('["docs", BookOpen, "模型接入", "04"]');
    expect(appSource).toContain('new Set<ViewName>(["overview", "accounts", "tasks", "docs"])');
  });
});
