import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const appSource = readFileSync(new URL("./App.tsx", import.meta.url), "utf8");

describe("task filter refresh orchestration", () => {
  it("loads a filtered task page without waiting for dashboard-wide data", () => {
    const globalLoaderStart = appSource.indexOf("const loadData = useCallback");
    const globalLoaderEnd = appSource.indexOf("useEffect(() => { void loadData();", globalLoaderStart);
    const globalLoader = appSource.slice(globalLoaderStart, globalLoaderEnd);

    expect(globalLoaderStart).toBeGreaterThan(-1);
    expect(globalLoaderEnd).toBeGreaterThan(globalLoaderStart);
    expect(appSource).toContain("const loadTasks = useCallback");
    expect(globalLoader).not.toContain("api.getTasks(");
    expect(appSource).toContain("useEffect(() => { void loadTasks(); }, [loadTasks]);");
    expect(appSource).toContain("loading={taskLoading}");
  });
});
