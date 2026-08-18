import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { MailboxesView } from "./App";
import type { Mailbox, MailboxStatus } from "./types";

function mailbox(status: MailboxStatus): Mailbox {
  return {
    mailbox_uuid: "67420f85-e589-4356-9c3a-12345678d086",
    email: "user@example.com",
    status,
    disabled_reason: null,
    validation_attempts: 0,
    next_validation_at: null,
    last_validated_at: "2026-08-13T05:45:00Z",
    last_error_code: null,
    last_error_message: null,
    last_message_received_at: null,
    version: 1,
    created_at: "2026-08-13T05:45:00Z",
    updated_at: "2026-08-13T05:45:00Z",
  };
}

function renderMailbox(status: MailboxStatus): string {
  return renderToStaticMarkup(
    <MailboxesView
      mailboxes={[mailbox(status)]}
      stats={null}
      total={1}
      page={0}
      search=""
      status=""
      importPeriod=""
      loading={false}
      onPage={vi.fn()}
      onSearch={vi.fn()}
      onStatus={vi.fn()}
      onImportPeriod={vi.fn()}
      onImport={vi.fn()}
      onViewCode={vi.fn()}
      onRevalidate={vi.fn()}
      onToggle={vi.fn()}
      onDelete={vi.fn()}
    />,
  );
}

describe("MailboxesView verification-code action", () => {
  it("shows an enabled verification-code button for an active mailbox", () => {
    const markup = renderMailbox("ACTIVE");

    expect(markup).toContain("查看验证码");
    expect(markup).toContain("mailbox-code-button");
    expect(markup).not.toContain('mailbox-code-button" disabled');
  });

  it("disables verification-code lookup for an inactive mailbox", () => {
    const markup = renderMailbox("INVALID");

    expect(markup).toContain('mailbox-code-button" disabled');
  });

  it("shows mutually exclusive import-time categories and the imported-at column", () => {
    const markup = renderMailbox("ACTIVE");

    expect(markup).toContain("按导入时间分类");
    expect(markup).toContain("今天");
    expect(markup).toContain("昨天");
    expect(markup).toContain("2–7 天");
    expect(markup).toContain("7 天前");
    expect(markup).toContain("导入时间");
    expect(markup).toContain("mailbox-import-date");
  });
});
