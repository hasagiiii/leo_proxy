import { readFileSync } from "node:fs";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { CookieImportBatchDetail, CookieImportModal } from "./CookieImportModal";
import { VideoTaskApi } from "./api";
import type { CookieImportBatch } from "./types";

const appSource = readFileSync(new URL("./App.tsx", import.meta.url), "utf8");
const modalSource = readFileSync(new URL("./CookieImportModal.tsx", import.meta.url), "utf8");

function terminalBatch(): CookieImportBatch {
  return {
    batch_uuid: "f543b87f-8fc6-466b-9c5a-06cfd7e599f0",
    status: "PARTIAL_FAILED",
    archive_filename: "cookies.zip",
    archive_sha256: "a".repeat(64),
    space_name: "cookie-import-20260813-1104",
    item_count: 2,
    queued: 0,
    running: 0,
    created: 1,
    updated: 0,
    failed: 1,
    total_balance_credits: 8360,
    tasks_after_import: 3,
    completed_tasks_after_import: 2,
    failed_tasks_after_import: 1,
    consumed_credits_after_import: 420,
    created_at: "2026-08-13T03:04:00Z",
    started_at: "2026-08-13T03:04:01Z",
    finished_at: "2026-08-13T03:04:05Z",
    items: [{
      item_uuid: "item-1",
      entry_name: "account.json",
      entry_sha256: "b".repeat(64),
      expected_login_name: "account@example.test",
      discovered_login_name: "account@example.test",
      status: "CREATED",
      stage: "RENEWAL_READY",
      attempt_count: 1,
      retryable: false,
      last_error_code: null,
      last_error_message: null,
      account_uuid: "account-uuid",
      account_status: "ACTIVE",
      balance_credits: 8360,
      token_expires_at: "2026-08-13T04:04:00Z",
      renewal_status: "PENDING",
      activated_at: "2026-08-13T03:04:05Z",
      finished_at: "2026-08-13T03:04:05Z",
      created_at: "2026-08-13T03:04:00Z",
      updated_at: "2026-08-13T03:04:05Z",
    }],
  };
}

describe("CookieImportModal", () => {
  it("adds a distinct account-page entry between existing actions", () => {
    const bulkIndex = appSource.indexOf("批量导入");
    const cookieIndex = appSource.indexOf("导入 Cookie ZIP");
    const addIndex = appSource.indexOf("添加账号", bulkIndex);
    expect(bulkIndex).toBeGreaterThan(-1);
    expect(cookieIndex).toBeGreaterThan(bulkIndex);
    expect(cookieIndex).toBeLessThan(addIndex);
  });

  it("renders ZIP filtering, the size limit, default name and disabled upload", () => {
    const api = new VideoTaskApi({ apiBase: "/api", apiKey: "business", adminKey: "admin" });
    const markup = renderToStaticMarkup(<CookieImportModal api={api} onClose={vi.fn()} onImported={vi.fn()} />);
    expect(markup).toContain("新建导入");
    expect(markup).toContain("最近批次");
    expect(markup).toContain('accept=".zip,application/zip,application/x-zip-compressed"');
    expect(markup).toContain("20 MiB");
    expect(markup).toMatch(/cookie-import-\d{8}-\d{4}/);
    expect(markup).toContain("原始 ZIP");
    expect(markup).toContain('disabled=""');
  });

  it("renders terminal result, scheduler and consumption observation without secrets", () => {
    const markup = renderToStaticMarkup(<CookieImportBatchDetail batch={terminalBatch()} />);
    expect(markup).toContain("已进入调度");
    expect(markup).toContain("账号激活");
    expect(markup).toContain("续签就绪");
    expect(markup).toContain("作业 3");
    expect(markup).toContain("消耗 420");
    expect(markup).toContain("account@example.test");
    expect(markup).toContain("错误码");
    expect(markup).not.toContain("Cookie 值");
    expect(markup).not.toContain("Token 值");
  });

  it("keeps polling nonterminal batches and clears the timer on close", () => {
    expect(modalSource).toContain("window.setInterval(() => void poll(), 1500)");
    expect(modalSource).toContain("window.clearInterval(timer)");
  });
});
