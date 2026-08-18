import { afterEach, describe, expect, it, vi } from "vitest";

import { VideoTaskApi } from "./api";

describe("registration Admin API", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("uses fixed settings, list, revalidate and promotion endpoints", async () => {
    const fetchMock = vi.fn(async () => new Response("{}", { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);
    const api = new VideoTaskApi({ apiBase: "https://api.example.test", apiKey: "api", adminKey: "admin" });

    await api.getParentRegistrations("parent-uuid", { status: "PROMOTABLE", search: "child" });
    await api.getSuccessfulRegistrations({ search: "child", isUsed: false, credits: 8500, offset: 50 });
    await api.revalidateRegistration("registration-uuid");
    await api.promoteRegistration("registration-uuid");
    await api.getRegistrationSettings();
    await api.patchRegistrationSettings({ target_space_uuid: "space-uuid", default_max_concurrency: 3, expected_version: 2 });

    const calls = fetchMock.mock.calls as unknown as [string, RequestInit][];
    expect(calls[0][0]).toContain("/admin/parent-accounts/parent-uuid/registrations?");
    expect(calls[0][0]).toContain("status=PROMOTABLE");
    expect(calls[1][0]).toContain("/admin/registration-records?");
    expect(calls[1][0]).toContain("search=child");
    expect(calls[1][0]).toContain("is_used=false");
    expect(calls[1][0]).toContain("credits=8500");
    expect(calls[1][0]).toContain("offset=50");
    expect(calls[2][0]).toContain("/admin/registration-records/registration-uuid/revalidate");
    expect(calls[3][0]).toContain("/admin/registration-records/registration-uuid/promote");
    expect(calls[4][0]).toContain("/admin/registration-settings");
    expect(JSON.parse(String(calls[5][1].body))).toEqual({ target_space_uuid: "space-uuid", default_max_concurrency: 3, expected_version: 2 });
  });

  it("downloads the selected registration Cookie ZIP", async () => {
    const fetchMock = vi.fn(async () => new Response("zip-bytes", {
      status: 200,
      headers: {
        "Content-Type": "application/zip",
        "Content-Disposition": 'attachment; filename="leonardo-8500-cookies-2-unused-20260814-201500.zip"',
        "X-Exported-Count": "2",
      },
    }));
    vi.stubGlobal("fetch", fetchMock);
    const api = new VideoTaskApi({ apiBase: "https://api.example.test", apiKey: "api", adminKey: "admin" });

    const file = await api.exportRegistrationCookies(["first@example.test", "second@example.test"]);

    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("https://api.example.test/v1/registration-cookies/export");
    expect(JSON.parse(String(init.body))).toEqual({ emails: ["first@example.test", "second@example.test"] });
    expect(file.filename).toBe("leonardo-8500-cookies-2-unused-20260814-201500.zip");
    expect(file.exportedCount).toBe(2);
    expect(await file.blob.text()).toBe("zip-bytes");
  });
});
