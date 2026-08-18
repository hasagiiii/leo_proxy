import type {
  Account,
  AccountBalanceRefreshResult,
  AccountBulkDeletePreview,
  AccountBulkDeleteResult,
  AccountCredentialExport,
  AccountCreatePayload,
  AccountPatchPayload,
  ApiCredentials,
  DashboardPeriod,
  DashboardStats,
  CookieImportBatch,
  CookieImportBatchList,
  ModelCatalogCardType,
  ModelCatalogResponse,
  Mailbox,
  MailboxCodeResult,
  MailboxImportPeriod,
  MailboxImportResult,
  MailboxList,
  MailboxStats,
  ParentAccount,
  ParentAccountImportResult,
  ParentAccountList,
  ParentAccountStats,
  RegistrationPoolSettings,
  RegistrationCookieExport,
  RegistrationClientDetailResponse,
  RegistrationClientListResponse,
  RegistrationMonitorHealth,
  RegistrationPromotionResult,
  RegistrationRecord,
  RegistrationRecordList,
  ProtocolRenewalAccountList,
  ProtocolRenewalEventList,
  ProtocolRenewalPeriod,
  ProtocolRenewalStats,
  Space,
  Task,
  TaskListResponse,
  ClientRegistrationTaskList,
  TokenUpdatePayload,
} from "./types";

const STORAGE_KEY = "video-task-console-credentials";

export class ApiError extends Error {
  status: number;
  detail: unknown;

  constructor(status: number, message: string, detail: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

export function loadCredentials(): ApiCredentials {
  const runtime = window.__VIDEO_TASK_CONFIG__ ?? { apiBase: "/api" };
  try {
    const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "{}") as Partial<ApiCredentials>;
    return {
      apiBase: saved.apiBase || runtime.apiBase || "/api",
      apiKey: saved.apiKey || runtime.bootstrapApiKey || "",
      adminKey: saved.adminKey || runtime.bootstrapAdminKey || "",
    };
  } catch {
    return {
      apiBase: runtime.apiBase || "/api",
      apiKey: runtime.bootstrapApiKey || "",
      adminKey: runtime.bootstrapAdminKey || "",
    };
  }
}

export function saveCredentials(credentials: ApiCredentials): void {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(credentials));
}

function detailMessage(detail: unknown): string {
  if (typeof detail === "string") return detail;
  if (detail && typeof detail === "object") {
    const candidate = detail as { message?: string; code?: string };
    return candidate.message || candidate.code || "请求处理失败";
  }
  return "请求处理失败";
}

export class VideoTaskApi {
  credentials: ApiCredentials;

  constructor(credentials: ApiCredentials) {
    this.credentials = credentials;
  }

  updateCredentials(credentials: ApiCredentials): void {
    this.credentials = credentials;
  }

  private async request<T>(
    path: string,
    scope: "api" | "admin" | null,
    init: RequestInit = {},
  ): Promise<T> {
    const base = this.credentials.apiBase.replace(/\/$/, "");
    const headers = new Headers(init.headers);
    if (scope) {
      headers.set(scope === "admin" ? "X-Admin-Key" : "X-API-Key", scope === "admin" ? this.credentials.adminKey : this.credentials.apiKey);
    }
    const isFormData = typeof FormData !== "undefined" && init.body instanceof FormData;
    if (init.body && !isFormData && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
    const response = await fetch(`${base}${path}`, { ...init, headers });
    const contentType = response.headers.get("content-type") ?? "";
    const payload: unknown = contentType.includes("application/json") ? await response.json() : await response.text();
    if (!response.ok) {
      const wrapped = payload as { detail?: unknown };
      const detail = wrapped?.detail ?? payload;
      throw new ApiError(response.status, detailMessage(detail), detail);
    }
    return payload as T;
  }

  private async requestCredentialFile(path: string, accountUuids: string[]): Promise<AccountCredentialExport> {
    const base = this.credentials.apiBase.replace(/\/$/, "");
    const response = await fetch(`${base}${path}`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Admin-Key": this.credentials.adminKey,
      },
      body: JSON.stringify({ account_uuids: accountUuids }),
    });
    if (!response.ok) {
      const contentType = response.headers.get("content-type") ?? "";
      const payload: unknown = contentType.includes("application/json") ? await response.json() : await response.text();
      const wrapped = payload as { detail?: unknown };
      const detail = wrapped?.detail ?? payload;
      throw new ApiError(response.status, detailMessage(detail), detail);
    }
    const receipt = response.headers.get("x-account-export-receipt") ?? "";
    if (!receipt) {
      throw new ApiError(502, "导出响应缺少删除回执", { code: "ACCOUNT_EXPORT_RECEIPT_MISSING" });
    }
    const disposition = response.headers.get("content-disposition") ?? "";
    const filename = /filename="([^"]+)"/.exec(disposition)?.[1] ?? "accounts-credentials.txt";
    return {
      blob: await response.blob(),
      filename,
      receipt,
      exportedCount: Number(response.headers.get("x-exported-count") ?? accountUuids.length),
    };
  }

  getStats(period: DashboardPeriod, timezoneOffsetMinutes: number): Promise<DashboardStats> {
    const params = new URLSearchParams({
      period,
      timezone_offset_minutes: String(timezoneOffsetMinutes),
    });
    return this.request(`/admin/stats/dashboard?${params}`, "admin");
  }

  getAccounts(status?: string): Promise<Account[]> {
    const query = status ? `?status=${encodeURIComponent(status)}` : "";
    return this.request(`/admin/accounts${query}`, "admin");
  }

  getMailboxes(
    status = "",
    search = "",
    limit = 50,
    offset = 0,
    importPeriod: MailboxImportPeriod = "",
    timezoneOffsetMinutes = -new Date().getTimezoneOffset(),
  ): Promise<MailboxList> {
    const params = new URLSearchParams({ limit: String(limit), offset: String(offset) });
    if (status) params.set("status", status);
    if (search) params.set("search", search);
    if (importPeriod) {
      params.set("import_period", importPeriod);
      params.set("timezone_offset_minutes", String(timezoneOffsetMinutes));
    }
    return this.request(`/admin/mailboxes?${params.toString()}`, "admin");
  }

  getMailboxStats(): Promise<MailboxStats> {
    return this.request("/admin/mailboxes/stats", "admin");
  }

  importMailboxes(content: string): Promise<MailboxImportResult> {
    return this.request("/admin/mailboxes/import", "admin", {
      method: "POST",
      body: JSON.stringify({ content }),
    });
  }

  revalidateMailbox(mailboxUuid: string): Promise<Mailbox> {
    return this.request(`/admin/mailboxes/${mailboxUuid}/revalidate`, "admin", { method: "POST" });
  }

  patchMailbox(mailboxUuid: string, manualStatus: "PENDING_VALIDATION" | "MANUAL_DISABLED", expectedVersion: number): Promise<Mailbox> {
    return this.request(`/admin/mailboxes/${mailboxUuid}`, "admin", {
      method: "PATCH",
      body: JSON.stringify({ manual_status: manualStatus, expected_version: expectedVersion }),
    });
  }

  deleteMailbox(mailboxUuid: string): Promise<void> {
    return this.request(`/admin/mailboxes/${mailboxUuid}`, "admin", { method: "DELETE" });
  }

  queryMailboxCode(email: string, timeoutSeconds = 60, signal?: AbortSignal): Promise<MailboxCodeResult> {
    return this.request("/v1/mailbox-codes/query", null, {
      method: "POST",
      body: JSON.stringify({ email, timeout_seconds: timeoutSeconds }),
      signal,
    });
  }

  getParentAccounts(search = "", limit = 50, offset = 0): Promise<ParentAccountList> {
    const params = new URLSearchParams({ limit: String(limit), offset: String(offset) });
    if (search) params.set("search", search);
    return this.request(`/admin/parent-accounts?${params.toString()}`, "admin");
  }

  getParentAccountStats(): Promise<ParentAccountStats> {
    return this.request("/admin/parent-accounts/stats", "admin");
  }

  importParentAccounts(content: string): Promise<ParentAccountImportResult> {
    return this.request("/admin/parent-accounts/import", "admin", {
      method: "POST",
      body: JSON.stringify({ content }),
    });
  }

  deleteParentAccount(parentAccountUuid: string): Promise<void> {
    return this.request(`/admin/parent-accounts/${parentAccountUuid}`, "admin", { method: "DELETE" });
  }

  recordParentAccountInvitationResult(parentAccountUuid: string, success: boolean): Promise<ParentAccount> {
    return this.request(`/admin/parent-accounts/${parentAccountUuid}/invitation-result`, "admin", {
      method: "POST",
      body: JSON.stringify({ success }),
    });
  }

  getParentRegistrations(
    parentAccountUuid: string,
    filters: { status?: string; search?: string; limit?: number; offset?: number } = {},
  ): Promise<RegistrationRecordList> {
    const params = new URLSearchParams({
      limit: String(filters.limit ?? 50),
      offset: String(filters.offset ?? 0),
    });
    if (filters.status) params.set("status", filters.status);
    if (filters.search) params.set("search", filters.search);
    return this.request(
      `/admin/parent-accounts/${parentAccountUuid}/registrations?${params.toString()}`,
      "admin",
    );
  }

  getSuccessfulRegistrations(
    filters: { search?: string; isUsed?: boolean; credits?: number; limit?: number; offset?: number } = {},
  ): Promise<RegistrationRecordList> {
    const params = new URLSearchParams({
      limit: String(filters.limit ?? 50),
      offset: String(filters.offset ?? 0),
    });
    if (filters.search?.trim()) params.set("search", filters.search.trim());
    if (filters.isUsed !== undefined) params.set("is_used", String(filters.isUsed));
    if (filters.credits !== undefined) params.set("credits", String(filters.credits));
    return this.request(`/admin/registration-records?${params.toString()}`, "admin");
  }

  async exportRegistrationCookies(emails: string[]): Promise<RegistrationCookieExport> {
    const base = this.credentials.apiBase.replace(/\/$/, "");
    const response = await fetch(`${base}/v1/registration-cookies/export`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ emails }),
    });
    if (!response.ok) {
      const contentType = response.headers.get("content-type") ?? "";
      const payload: unknown = contentType.includes("application/json") ? await response.json() : await response.text();
      const wrapped = payload as { detail?: unknown };
      const detail = wrapped?.detail ?? payload;
      throw new ApiError(response.status, detailMessage(detail), detail);
    }
    const disposition = response.headers.get("content-disposition") ?? "";
    const filename = /filename="([^"]+)"/.exec(disposition)?.[1] ?? "registration-cookies.zip";
    return {
      blob: await response.blob(),
      filename,
      exportedCount: Number(response.headers.get("x-exported-count") ?? emails.length),
    };
  }

  revalidateRegistration(registrationUuid: string): Promise<RegistrationRecord> {
    return this.request(
      `/admin/registration-records/${registrationUuid}/revalidate`,
      "admin",
      { method: "POST" },
    );
  }

  promoteRegistration(registrationUuid: string): Promise<RegistrationPromotionResult> {
    return this.request(
      `/admin/registration-records/${registrationUuid}/promote`,
      "admin",
      { method: "POST" },
    );
  }

  getRegistrationSettings(): Promise<RegistrationPoolSettings> {
    return this.request("/admin/registration-settings", "admin");
  }

  getRegistrationClients(filters: {
    from: string;
    to: string;
    health?: RegistrationMonitorHealth | "";
    search?: string;
    limit?: number;
    offset?: number;
  }): Promise<RegistrationClientListResponse> {
    const params = new URLSearchParams({
      from: filters.from,
      to: filters.to,
      limit: String(filters.limit ?? 50),
      offset: String(filters.offset ?? 0),
    });
    if (filters.health) params.set("health", filters.health);
    if (filters.search?.trim()) params.set("search", filters.search.trim());
    return this.request(`/admin/registration-clients?${params.toString()}`, "admin");
  }

  getRegistrationClientDetail(
    clientId: string,
    from: string,
    to: string,
  ): Promise<RegistrationClientDetailResponse> {
    const params = new URLSearchParams({ from, to });
    return this.request(
      `/admin/registration-clients/${encodeURIComponent(clientId)}?${params.toString()}`,
      "admin",
    );
  }

  getRegistrationClientTasks(
    clientId: string,
    filters: {
      from: string;
      to: string;
      status?: string;
      search?: string;
      limit?: number;
      offset?: number;
    },
  ): Promise<ClientRegistrationTaskList> {
    const params = new URLSearchParams({
      from: filters.from,
      to: filters.to,
      limit: String(filters.limit ?? 50),
      offset: String(filters.offset ?? 0),
    });
    if (filters.status) params.set("status", filters.status);
    if (filters.search?.trim()) params.set("search", filters.search.trim());
    return this.request(
      `/admin/registration-clients/${encodeURIComponent(clientId)}/registrations?${params.toString()}`,
      "admin",
    );
  }

  patchRegistrationSettings(payload: {
    target_space_uuid: string | null;
    default_max_concurrency: number;
    expected_version: number;
  }): Promise<RegistrationPoolSettings> {
    return this.request("/admin/registration-settings", "admin", {
      method: "PATCH",
      body: JSON.stringify(payload),
    });
  }

  getProtocolRenewalStats(
    period: ProtocolRenewalPeriod,
    timezoneOffsetMinutes: number,
  ): Promise<ProtocolRenewalStats> {
    const params = new URLSearchParams({
      period,
      timezone_offset_minutes: String(timezoneOffsetMinutes),
    });
    return this.request(`/admin/stats/protocol-renewals?${params}`, "admin");
  }

  getProtocolRenewalAccounts(status = ""): Promise<ProtocolRenewalAccountList> {
    const params = new URLSearchParams({ limit: "2000" });
    if (status) params.set("status", status);
    return this.request(`/admin/protocol-renewals/accounts?${params}`, "admin");
  }

  getProtocolRenewalEvents(accountUuid: string): Promise<ProtocolRenewalEventList> {
    return this.request(
      `/admin/protocol-renewals/accounts/${encodeURIComponent(accountUuid)}/events?limit=20`,
      "admin",
    );
  }

  getSpaces(): Promise<Space[]> {
    return this.request("/admin/spaces", "admin");
  }

  createCookieImport(file: File, spaceName: string, idempotencyKey: string): Promise<CookieImportBatch> {
    const body = new FormData();
    body.set("archive", file);
    body.set("space_name", spaceName);
    return this.request("/admin/account-cookie-imports", "admin", {
      method: "POST",
      headers: { "Idempotency-Key": idempotencyKey },
      body,
    });
  }

  listCookieImports(limit = 20, offset = 0): Promise<CookieImportBatchList> {
    const params = new URLSearchParams({ limit: String(limit), offset: String(offset) });
    return this.request(`/admin/account-cookie-imports?${params.toString()}`, "admin");
  }

  getCookieImport(batchUuid: string): Promise<CookieImportBatch> {
    return this.request(`/admin/account-cookie-imports/${encodeURIComponent(batchUuid)}`, "admin");
  }

  createAccount(payload: AccountCreatePayload): Promise<Account> {
    return this.request("/admin/accounts", "admin", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  }

  updateToken(accountUuid: string, payload: TokenUpdatePayload): Promise<Account> {
    return this.request(`/admin/accounts/${accountUuid}/token`, "admin", {
      method: "PUT",
      body: JSON.stringify(payload),
    });
  }

  patchAccount(accountUuid: string, payload: AccountPatchPayload): Promise<Account> {
    return this.request(`/admin/accounts/${accountUuid}`, "admin", {
      method: "PATCH",
      body: JSON.stringify(payload),
    });
  }

  refreshAccountBalance(accountUuid: string): Promise<AccountBalanceRefreshResult> {
    return this.request(`/admin/accounts/${accountUuid}/refresh-balance`, "admin", {
      method: "POST",
    });
  }

  deleteAccount(accountUuid: string): Promise<void> {
    return this.request(`/admin/accounts/${accountUuid}`, "admin", {
      method: "DELETE",
    });
  }

  previewBulkDeleteAccounts(accountUuids: string[]): Promise<AccountBulkDeletePreview> {
    return this.request("/admin/accounts/bulk-delete/preview", "admin", {
      method: "POST",
      body: JSON.stringify({ account_uuids: accountUuids }),
    });
  }

  exportAccountCredentials(accountUuids: string[]): Promise<AccountCredentialExport> {
    return this.requestCredentialFile("/admin/accounts/export", accountUuids);
  }

  bulkDeleteAccounts(accountUuids: string[], exportReceipt: string): Promise<AccountBulkDeleteResult> {
    return this.request("/admin/accounts/bulk-delete", "admin", {
      method: "POST",
      body: JSON.stringify({ account_uuids: accountUuids, export_receipt: exportReceipt }),
    });
  }

  getTasks(status = "", limit = 50, offset = 0, model = ""): Promise<TaskListResponse> {
    const params = new URLSearchParams({ limit: String(limit), offset: String(offset) });
    if (status) params.set("status", status);
    if (model) params.set("model", model);
    return this.request(`/v1/tasks?${params.toString()}`, "api");
  }

  getModelCatalog(type: ModelCatalogCardType = "MODEL"): Promise<ModelCatalogResponse> {
    return this.request(`/v1/models?type=${encodeURIComponent(type)}`, "api");
  }

  cancelTask(taskUuid: string): Promise<Task> {
    return this.request(`/v1/tasks/${taskUuid}/cancel`, "api", { method: "POST" });
  }
}
