import type { CookieImportBatchStatus, CookieImportItemStatus, CookieImportStage } from "./types";

export const COOKIE_IMPORT_MAX_BYTES = 20 * 1024 * 1024;
export const COOKIE_IMPORT_TERMINAL = new Set<CookieImportBatchStatus>(["COMPLETED", "PARTIAL_FAILED", "FAILED"]);

export const COOKIE_IMPORT_STAGE_LABELS: Record<CookieImportStage, string> = {
  RECEIVED: "已接收",
  SESSION_VALIDATION: "会话验证",
  BALANCE_VALIDATION: "积分验证",
  ACCOUNT_ACTIVATION: "账号激活",
  RENEWAL_READY: "续签就绪",
};

export const COOKIE_IMPORT_STATUS_LABELS: Record<CookieImportItemStatus, string> = {
  QUEUED: "等待处理",
  RUNNING: "处理中",
  RETRY_WAIT: "等待重试",
  CREATED: "已新增",
  UPDATED: "已更新",
  SKIPPED_DUPLICATE: "已跳过",
  FAILED: "失败",
};

type FileMetadata = Pick<File, "name" | "size">;

export function cookieImportFileError(file: FileMetadata): string {
  if (!file.name.toLowerCase().endsWith(".zip")) return "请选择 ZIP（.zip）格式的 Cookie 压缩包";
  if (file.size <= 0) return "ZIP 文件为空";
  if (file.size > COOKIE_IMPORT_MAX_BYTES) return "ZIP 文件超过 20 MiB 上传上限";
  return "";
}

function two(value: number): string {
  return String(value).padStart(2, "0");
}

export function defaultCookieImportSpaceName(now = new Date(), utc = false): string {
  const year = utc ? now.getUTCFullYear() : now.getFullYear();
  const month = utc ? now.getUTCMonth() + 1 : now.getMonth() + 1;
  const date = utc ? now.getUTCDate() : now.getDate();
  const hours = utc ? now.getUTCHours() : now.getHours();
  const minutes = utc ? now.getUTCMinutes() : now.getMinutes();
  return `cookie-import-${year}${two(month)}${two(date)}-${two(hours)}${two(minutes)}`;
}

export function formatCookieImportBytes(size: number): string {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KiB`;
  const mib = size / 1024 / 1024;
  return `${Number.isInteger(mib) ? mib.toFixed(0) : mib.toFixed(2)} MiB`;
}

export function newCookieImportIdempotencyKey(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) return crypto.randomUUID();
  return `cookie-import-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}
