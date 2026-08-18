export interface MailboxImportPreviewRecord {
  lineNumber: number;
  email: string;
}

export interface MailboxImportPreviewIssue {
  lineNumber: number;
  email: string;
  code: "FORMAT" | "EMPTY_FIELD" | "INVALID_EMAIL" | "DUPLICATE_IN_BATCH" | "DUPLICATE_EXISTING";
  reason: string;
}

export interface MailboxImportPreviewResult {
  records: MailboxImportPreviewRecord[];
  issues: MailboxImportPreviewIssue[];
  blankLines: number;
}

const EMAIL = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;

export function parseMailboxImportPreview(
  source: string,
  existingEmails: Iterable<string> = [],
): MailboxImportPreviewResult {
  if (!source.trim()) return { records: [], issues: [], blankLines: 0 };
  const existing = new Set(Array.from(existingEmails, (email) => email.trim().toLowerCase()).filter(Boolean));
  const seen = new Set<string>();
  const records: MailboxImportPreviewRecord[] = [];
  const issues: MailboxImportPreviewIssue[] = [];
  let blankLines = 0;

  source.split(/\r\n?|\n/).forEach((rawLine, index) => {
    const lineNumber = index + 1;
    const line = rawLine.replaceAll("\uFEFF", "").trim();
    if (!line) {
      blankLines += 1;
      return;
    }
    const fields = line.split("----").map((field) => field.trim());
    const email = (fields[0] ?? "").toLowerCase();
    if (fields.length !== 4) {
      issues.push({ lineNumber, email, code: "FORMAT", reason: "必须包含四个字段" });
      return;
    }
    if (fields.some((field) => !field)) {
      issues.push({ lineNumber, email, code: "EMPTY_FIELD", reason: "字段不能为空" });
      return;
    }
    if (!EMAIL.test(email)) {
      issues.push({ lineNumber, email, code: "INVALID_EMAIL", reason: "邮箱格式无效" });
      return;
    }
    if (seen.has(email)) {
      issues.push({ lineNumber, email, code: "DUPLICATE_IN_BATCH", reason: "同批邮箱重复" });
      return;
    }
    seen.add(email);
    if (existing.has(email)) {
      issues.push({ lineNumber, email, code: "DUPLICATE_EXISTING", reason: "邮箱池中已存在" });
      return;
    }
    records.push({ lineNumber, email });
  });
  return { records, issues, blankLines };
}
