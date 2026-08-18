export interface ParentAccountImportPreviewRecord {
  lineNumber: number;
  email: string;
  password: string;
  inviteUrl: string;
}

export type ParentAccountImportPreviewIssueCode =
  | "FORMAT"
  | "INVALID_EMAIL"
  | "INVALID_URL"
  | "FIELD_TOO_LONG"
  | "DUPLICATE_IN_BATCH"
  | "DUPLICATE_EXISTING"
  | "TOO_MANY_ROWS";

export interface ParentAccountImportPreviewIssue {
  lineNumber: number;
  email: string;
  code: ParentAccountImportPreviewIssueCode;
  reason: string;
}

export interface ParentAccountImportPreviewResult {
  records: ParentAccountImportPreviewRecord[];
  issues: ParentAccountImportPreviewIssue[];
  blankLines: number;
}

const EMAIL = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;
const MAX_IMPORT_LINES = 5_000;
const FIELD_LIMITS = [255, 4_096, 8_192] as const;

function isValidInviteUrl(value: string): boolean {
  try {
    const parsed = new URL(value);
    return (parsed.protocol === "http:" || parsed.protocol === "https:") && Boolean(parsed.hostname);
  } catch {
    return false;
  }
}

export function parseParentAccountImportPreview(
  source: string,
  existingEmails: Iterable<string> = [],
): ParentAccountImportPreviewResult {
  if (!source.trim()) return { records: [], issues: [], blankLines: 0 };
  const existing = new Set(
    Array.from(existingEmails, (email) => email.trim().toLowerCase()).filter(Boolean),
  );
  const seen = new Set<string>();
  const records: ParentAccountImportPreviewRecord[] = [];
  const issues: ParentAccountImportPreviewIssue[] = [];
  let blankLines = 0;
  let nonblankLines = 0;

  source.split(/\r\n?|\n/).forEach((rawLine, index) => {
    const lineNumber = index + 1;
    const line = rawLine.replaceAll("\uFEFF", "").trim();
    if (!line) {
      blankLines += 1;
      return;
    }
    nonblankLines += 1;
    if (nonblankLines > MAX_IMPORT_LINES) {
      issues.push({
        lineNumber,
        email: "",
        code: "TOO_MANY_ROWS",
        reason: `单次最多导入 ${MAX_IMPORT_LINES} 行`,
      });
      return;
    }

    const fields = line.split(/\s+/);
    const email = (fields[0] ?? "").toLowerCase();
    if (fields.length !== 3) {
      issues.push({ lineNumber, email, code: "FORMAT", reason: "必须包含三个字段" });
      return;
    }
    if (fields.some((field, fieldIndex) => field.length > FIELD_LIMITS[fieldIndex])) {
      issues.push({ lineNumber, email, code: "FIELD_TOO_LONG", reason: "字段长度超过限制" });
      return;
    }
    if (!EMAIL.test(email)) {
      issues.push({ lineNumber, email, code: "INVALID_EMAIL", reason: "邮箱格式无效" });
      return;
    }
    if (!isValidInviteUrl(fields[2])) {
      issues.push({ lineNumber, email, code: "INVALID_URL", reason: "邀请链接必须是 HTTP 或 HTTPS URL" });
      return;
    }
    if (seen.has(email)) {
      issues.push({ lineNumber, email, code: "DUPLICATE_IN_BATCH", reason: "同批母号重复" });
      return;
    }
    seen.add(email);
    if (existing.has(email)) {
      issues.push({ lineNumber, email, code: "DUPLICATE_EXISTING", reason: "母号池中已存在" });
      return;
    }
    records.push({
      lineNumber,
      email,
      password: fields[1],
      inviteUrl: fields[2],
    });
  });

  return { records, issues, blankLines };
}
