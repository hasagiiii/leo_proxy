import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { SuccessfulAccountsView, parseSuccessfulAccountPageJump } from "./App";
import type { RegistrationRecord } from "./types";

const record: RegistrationRecord = {
  registration_uuid: "b04fc99a-c906-438a-81c3-12345678c70a",
  parent_account_uuid: "67420f85-e589-4356-9c3a-12345678d086",
  parent_email: "parent@example.com",
  email: "child@example.com",
  client_id: "desktop-a",
  status: "SUCCEEDED",
  registered_email: "child@example.com",
  verified_email: "verified-child@example.com",
  awarded_points: 8_500,
  is_used: true,
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

describe("SuccessfulAccountsView", () => {
  it("clamps page-jump input to the available page range", () => {
    expect(parseSuccessfulAccountPageJump("24", 24)).toBe(23);
    expect(parseSuccessfulAccountPageJump("999", 24)).toBe(23);
    expect(parseSuccessfulAccountPageJump("0", 24)).toBe(0);
    expect(parseSuccessfulAccountPageJump("", 24)).toBeNull();
    expect(parseSuccessfulAccountPageJump("2.5", 24)).toBeNull();
  });

  it("shows account, points, registration time, parent account, and usage", () => {
    const markup = renderToStaticMarkup(
      <SuccessfulAccountsView
        accounts={[record]}
        total={1}
        page={0}
        pageSize={50}
        search=""
        usage=""
        credits=""
        unused8500Count={17}
        loading={false}
        selectedRegistrationUuids={new Set()}
        exporting={false}
        onPage={vi.fn()}
        onPageSize={vi.fn()}
        onSearch={vi.fn()}
        onUsage={vi.fn()}
        onCredits={vi.fn()}
        onQuickUnused8500={vi.fn()}
        onSelectionChange={vi.fn()}
        onExport={vi.fn()}
      />,
    );

    expect(markup).toContain("注册成功账号");
    expect(markup).toContain("verified-child@example.com");
    expect(markup).toContain("8,500");
    expect(markup).toContain("注册时间");
    expect(markup).toContain("parent@example.com");
    expect(markup).toContain("是否已使用");
    expect(markup).toContain("已使用");
    expect(markup).toContain("未使用 · 8,500");
    expect(markup).toContain("17");
    expect(markup).toContain("150 积分");
    expect(markup).toContain("CREDITS 150");
    expect(markup).toContain("8,500 积分");
    expect(markup).toContain("选择本页全部未使用账号");
  });

  it("enables selecting unused accounts for Cookie ZIP export", () => {
    const unused = { ...record, is_used: false };
    const markup = renderToStaticMarkup(
      <SuccessfulAccountsView
        accounts={[unused]}
        total={1}
        page={0}
        pageSize={50}
        search=""
        usage="unused"
        credits="8500"
        unused8500Count={1}
        loading={false}
        selectedRegistrationUuids={new Set([unused.registration_uuid])}
        exporting={false}
        onPage={vi.fn()}
        onPageSize={vi.fn()}
        onSearch={vi.fn()}
        onUsage={vi.fn()}
        onCredits={vi.fn()}
        onQuickUnused8500={vi.fn()}
        onSelectionChange={vi.fn()}
        onExport={vi.fn()}
      />,
    );

    expect(markup).toContain("已选择 1 个未使用账号");
    expect(markup).toContain("导出选中（1）");
    expect(markup).toContain("导出 Cookie JSON、邮箱及登录链接压缩包");
  });
  it("offers 20, 50, 100, 500 and a custom page-size path", () => {
    const markup = renderToStaticMarkup(
      <SuccessfulAccountsView
        accounts={[record]}
        total={368}
        page={1}
        pageSize={100}
        search=""
        usage=""
        credits=""
        unused8500Count={17}
        loading={false}
        selectedRegistrationUuids={new Set()}
        exporting={false}
        onPage={vi.fn()}
        onPageSize={vi.fn()}
        onSearch={vi.fn()}
        onUsage={vi.fn()}
        onCredits={vi.fn()}
        onQuickUnused8500={vi.fn()}
        onSelectionChange={vi.fn()}
        onExport={vi.fn()}
      />,
    );

    expect(markup).toContain('aria-label="成功账号每页数量"');
    expect(markup).toContain('<option value="20">20</option>');
    expect(markup).toContain('<option value="50">50</option>');
    expect(markup).toContain('<option value="100" selected="">100</option>');
    expect(markup).toContain('<option value="500">500</option>');
    expect(markup).toContain('<option value="custom">自定义</option>');
    expect(markup).toContain("101—200 / 368");
    expect(markup).toContain("第 2 / 4 页");
    expect(markup).toContain('aria-label="跳转到成功账号页码"');
    expect(markup).toContain('max="4"');
    expect(markup).toContain("最后一页");
  });

  it("renders the custom page-size editor for a custom saved value", () => {
    const markup = renderToStaticMarkup(
      <SuccessfulAccountsView
        accounts={[record]}
        total={368}
        page={0}
        pageSize={75}
        search=""
        usage=""
        credits=""
        unused8500Count={17}
        loading={false}
        selectedRegistrationUuids={new Set()}
        exporting={false}
        onPage={vi.fn()}
        onPageSize={vi.fn()}
        onSearch={vi.fn()}
        onUsage={vi.fn()}
        onCredits={vi.fn()}
        onQuickUnused8500={vi.fn()}
        onSelectionChange={vi.fn()}
        onExport={vi.fn()}
      />,
    );

    expect(markup).toContain('aria-label="自定义成功账号每页数量"');
    expect(markup).toContain('min="1"');
    expect(markup).toContain('max="500"');
    expect(markup).toContain('value="75"');
    expect(markup).toContain("应用");
  });

});
