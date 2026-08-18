export interface BulkImportAccount {
  lineNumber: number;
  loginName: string;
  password: string;
}

export interface BulkImportIssue {
  lineNumber: number;
  loginName: string;
  code: "FORMAT" | "DUPLICATE_IN_BATCH" | "DUPLICATE_EXISTING";
  reason: string;
  source: string;
}

export interface BulkImportParseResult {
  accounts: BulkImportAccount[];
  issues: BulkImportIssue[];
  blankLines: number;
}

export function parseBulkAccountText(
  source: string,
  existingLoginNames: Iterable<string> = [],
): BulkImportParseResult {
  if (!source.trim()) return { accounts: [], issues: [], blankLines: 0 };

  const accounts: BulkImportAccount[] = [];
  const issues: BulkImportIssue[] = [];
  const existing = new Set(
    Array.from(existingLoginNames, (loginName) => loginName.trim().toLowerCase()).filter(Boolean),
  );
  const seenLoginNames = new Set<string>();
  let blankLines = 0;

  source.split(/\r\n?|\n/).forEach((rawLine, index) => {
    const lineNumber = index + 1;
    const line = rawLine.replaceAll("\uFEFF", "").trim();
    if (!line) {
      blankLines += 1;
      return;
    }

    const separatorIndex = line.indexOf("|");
    if (separatorIndex < 0) {
      issues.push({ lineNumber, loginName: "", code: "FORMAT", reason: "缺少 | 分隔符", source: line });
      return;
    }

    const loginName = line.slice(0, separatorIndex).trim().toLowerCase();
    const password = line.slice(separatorIndex + 1).trim();
    if (!loginName) {
      issues.push({ lineNumber, loginName: "", code: "FORMAT", reason: "登录账号为空", source: line });
      return;
    }
    if (loginName.length < 3 || loginName.length > 255) {
      issues.push({ lineNumber, loginName, code: "FORMAT", reason: "登录账号长度应为 3–255 个字符", source: line });
      return;
    }
    if (!password) {
      issues.push({ lineNumber, loginName, code: "FORMAT", reason: "登录密码为空", source: line });
      return;
    }
    if (password.length > 4096) {
      issues.push({ lineNumber, loginName, code: "FORMAT", reason: "登录密码超过 4096 个字符", source: line });
      return;
    }
    if (seenLoginNames.has(loginName)) {
      issues.push({ lineNumber, loginName, code: "DUPLICATE_IN_BATCH", reason: "同一批次内账号重复", source: line });
      return;
    }

    seenLoginNames.add(loginName);
    if (existing.has(loginName)) {
      issues.push({ lineNumber, loginName, code: "DUPLICATE_EXISTING", reason: "账号池中已存在", source: line });
      return;
    }
    accounts.push({ lineNumber, loginName, password });
  });

  return { accounts, issues, blankLines };
}
