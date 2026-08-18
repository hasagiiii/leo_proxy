export type ViewName = "overview" | "accounts" | "mailboxes" | "parent-accounts" | "successful-accounts" | "registration-clients" | "tasks" | "docs";

export interface RuntimeConfig {
  apiBase: string;
  bootstrapApiKey?: string;
  bootstrapAdminKey?: string;
}

declare global {
  interface Window {
    __VIDEO_TASK_CONFIG__?: RuntimeConfig;
  }
}

export interface ApiCredentials {
  apiBase: string;
  apiKey: string;
  adminKey: string;
}

export interface Space {
  space_uuid: string;
  name: string;
  routing_key: string | null;
  status: string;
  max_concurrency: number;
  active_tasks: number;
  created_at: string;
  updated_at: string;
}

export interface Account {
  account_uuid: string;
  space_uuid: string;
  login_name?: string;
  login_name_masked?: string;
  label: AccountLabel | null;
  status: string;
  disabled_reason: string | null;
  token_configured: boolean;
  token_expires_at: string | null;
  token_refreshed_at: string | null;
  balance_credits: number;
  reserved_credits: number;
  balance_synced_at: string | null;
  max_concurrency: number;
  active_tasks: number;
  completed_tasks: number;
  failed_tasks: number;
  version: number;
  created_at: string;
  updated_at: string;
}

export type AccountLabel = "mmoshenqi" | "macbook";

export type MailboxStatus = "PENDING_VALIDATION" | "ACTIVE" | "INVALID" | "MANUAL_DISABLED";
export type MailboxImportPeriod = "" | "today" | "yesterday" | "recent_7d" | "older";

export interface Mailbox {
  mailbox_uuid: string;
  email: string;
  status: MailboxStatus;
  disabled_reason: string | null;
  validation_attempts: number;
  next_validation_at: string | null;
  last_validated_at: string | null;
  last_error_code: string | null;
  last_error_message: string | null;
  last_message_received_at: string | null;
  version: number;
  created_at: string;
  updated_at: string;
}

export interface MailboxList {
  items: Mailbox[];
  total: number;
  limit: number;
  offset: number;
}

export interface MailboxStats {
  total: number;
  pending_validation: number;
  active: number;
  invalid: number;
  manual_disabled: number;
}

export interface MailboxImportIssue {
  line_number: number;
  email: string;
  code: string;
  reason: string;
}

export interface MailboxImportResult {
  requested: number;
  imported: number;
  duplicates: number;
  invalid: number;
  blank_lines: number;
  issues: MailboxImportIssue[];
}

export interface MailboxCodeResult {
  email: string;
  code: string;
  received_at: string;
  subject: string;
  sender: string;
  message_id: string;
  matched_by: "KEYWORD_NEARBY" | "HTML_EMPHASIS" | "NUMERIC_FALLBACK";
}

export interface ParentAccount {
  parent_account_uuid: string;
  email: string;
  password: string;
  invite_url: string;
  invite_success_count: number;
  invite_failure_count: number;
  status: "ACTIVE" | "EXHAUSTED" | "MANUAL_DISABLED";
  consecutive_150_count: number;
  exhausted_reason: string | null;
  exhausted_at: string | null;
  legacy_invite_success_count: number;
  legacy_invite_failure_count: number;
  running_registration_count: number;
  traceable_registration_count: number;
  promotable_registration_count: number;
  version: number;
  created_at: string;
  updated_at: string;
}

export interface ParentAccountList {
  items: ParentAccount[];
  total: number;
  limit: number;
  offset: number;
}

export interface ParentAccountStats {
  total_parent_accounts: number;
  total_invite_successes: number;
  total_invite_failures: number;
  active_parent_accounts: number;
  exhausted_parent_accounts: number;
  traceable_registrations: number;
  promotable_registrations: number;
  legacy_invite_successes: number;
  legacy_invite_failures: number;
}

export type RegistrationStatus =
  | "RUNNING"
  | "COOKIE_REPORTED"
  | "VALIDATING"
  | "VALIDATION_RETRY_WAIT"
  | "VALIDATION_FAILED"
  | "FAILED"
  | "SUCCEEDED";

export interface RegistrationRecord {
  registration_uuid: string;
  parent_account_uuid: string;
  parent_email: string;
  email: string;
  client_id: string;
  status: RegistrationStatus;
  registered_email: string | null;
  verified_email: string | null;
  awarded_points: number | null;
  is_used: boolean;
  cookie_count: number;
  validation_attempts: number;
  validation_error_code: string | null;
  validation_error_message: string | null;
  started_at: string;
  reported_at: string | null;
  validation_finished_at: string | null;
  promoted_at: string | null;
  account_uuid: string | null;
  promotable: boolean;
  cookie_status: "RECEIVED" | "VALIDATING" | "VERIFIED" | "INVALID";
  version: number;
  created_at: string;
  updated_at: string;
}

export interface RegistrationRecordList {
  items: RegistrationRecord[];
  total: number;
  limit: number;
  offset: number;
  unused_8500_count?: number;
}

export interface RegistrationCookieExport {
  blob: Blob;
  filename: string;
  exportedCount: number;
}

export interface RegistrationPoolSettings {
  target_space_uuid: string | null;
  target_space_name: string | null;
  target_space_status: string | null;
  default_max_concurrency: number;
  promotion_available: boolean;
  version: number;
  updated_at: string;
}

export interface RegistrationPromotionResult {
  registration_uuid: string;
  account_uuid: string;
  account_status: string;
  target_space_uuid: string;
  replayed: boolean;
}

export type RegistrationMonitorHealth = "NORMAL" | "ATTENTION" | "ABNORMAL" | "NO_ACTIVITY";

export interface RegistrationMonitorWindow {
  from: string;
  to: string;
}

export interface RegistrationClientHealthReason {
  code: string;
  message: string;
}

export interface RegistrationClientSummary {
  total_clients: number;
  active_clients: number;
  normal_clients: number;
  attention_clients: number;
  abnormal_clients: number;
  no_activity_clients: number;
  jobs: number;
  succeeded: number;
  failed: number;
  processing: number;
}

export interface RegistrationClient {
  client_id: string;
  display_name: string;
  health: RegistrationMonitorHealth;
  health_reasons: RegistrationClientHealthReason[];
  last_activity_at: string | null;
  jobs: number;
  succeeded: number;
  failed: number;
  processing: number;
  retry_wait: number;
  stalled: number;
  success_rate: number | null;
  average_duration_seconds: number | null;
  latest_error_code: string | null;
  latest_error_message: string | null;
}

export interface RegistrationClientListResponse {
  server_now: string;
  window: RegistrationMonitorWindow;
  summary: RegistrationClientSummary;
  items: RegistrationClient[];
  total: number;
  limit: number;
  offset: number;
}

export interface RegistrationClientSeriesPoint {
  at: string;
  claimed: number;
  succeeded: number;
  failed: number;
}

export interface RegistrationClientDetailResponse {
  server_now: string;
  window: RegistrationMonitorWindow;
  client: RegistrationClient;
  series: RegistrationClientSeriesPoint[];
}

export interface ClientRegistrationTask {
  registration_uuid: string;
  parent_account_uuid: string;
  parent_email: string;
  email: string;
  client_id: string;
  status: RegistrationStatus;
  registered_email: string | null;
  awarded_points: number | null;
  started_at: string;
  lease_expires_at: string;
  last_heartbeat_at: string | null;
  reported_at: string | null;
  validation_finished_at: string | null;
  validation_lease_until: string | null;
  retry_after: string | null;
  duration_seconds: number | null;
  stalled: boolean;
  client_error_code: string | null;
  client_error_message: string | null;
  validation_error_code: string | null;
  validation_error_message: string | null;
  is_used: boolean;
  created_at: string;
  updated_at: string;
}

export interface ClientRegistrationTaskList {
  items: ClientRegistrationTask[];
  total: number;
  limit: number;
  offset: number;
}

export interface ParentAccountImportIssue {
  line_number: number;
  email: string;
  code: string;
  reason: string;
}

export interface ParentAccountImportResult {
  requested: number;
  imported: number;
  duplicates: number;
  invalid: number;
  blank_lines: number;
  issues: ParentAccountImportIssue[];
}

export interface Task {
  task_uuid: string;
  idempotency_key: string | null;
  provider: string;
  upstream_task_id: string | null;
  account_uuid: string | null;
  space_uuid: string | null;
  task_type: string;
  model: string;
  input: Record<string, unknown>;
  output: Record<string, unknown> | null;
  status: string;
  priority: number;
  estimated_credit_cost: number;
  reserved_credit_cost: number;
  actual_credit_cost: number | null;
  submit_attempts: number;
  sync_attempts: number;
  error_code: string | null;
  error_message: string | null;
  created_at: string;
  queued_at: string;
  assigned_at: string | null;
  upstream_submitted_at: string | null;
  finished_at: string | null;
  updated_at: string;
}

export interface TaskListResponse {
  items: Task[];
  total: number;
  models: string[];
}

export type ModelCatalogCardType = "MODEL" | "BLUEPRINT" | "GUIDE" | "ALL";

export interface ModelCatalogItem {
  id: string;
  type: string;
  rank: number;
  title: string;
  description: string | null;
  url: string;
  image_url: string | null;
  video_url: string | null;
  model: string | null;
}

export interface ModelCatalogResponse {
  provider: string;
  source: string;
  items: ModelCatalogItem[];
  total: number;
}

export interface StatusCount {
  status: string;
  count: number;
}

export interface DailyTaskMetric {
  date: string;
  total: number;
  completed: number;
  failed: number;
  credits: number;
}

export interface ModelMetric {
  model: string;
  total: number;
  completed: number;
  credits: number;
}

export type DashboardPeriod = "total" | "today" | "hour";

export interface TaskTrendMetric {
  bucket_start: string;
  label: string;
  total: number;
  completed: number;
  failed: number;
  credits: number;
}

export interface DashboardStats {
  generated_at: string;
  period: DashboardPeriod;
  period_started_at: string | null;
  timezone_offset_minutes: number;
  trend_granularity: "day" | "hour" | "five_minutes";
  accounts: {
    total: number;
    active: number;
    attention: number;
    low_balance: number;
    expiring_24h: number;
    balance_credits: number;
    available_credits: number;
    reserved_credits: number;
    active_tasks: number;
    max_concurrency: number;
    effective_max_concurrency: number;
    effective_available_concurrency: number;
    active_balance_credits: number;
    active_credit_target: number;
  };
  tasks: {
    total: number;
    queued: number;
    running: number;
    completed: number;
    failed: number;
    canceled: number;
    success_rate: number;
    consumed_credits: number;
    average_duration_seconds: number | null;
  };
  account_statuses: StatusCount[];
  task_statuses: StatusCount[];
  daily_tasks: DailyTaskMetric[];
  task_trend: TaskTrendMetric[];
  models: ModelMetric[];
}

export type ProtocolRenewalPeriod = "hour" | "six_hours" | "day" | "week";
export type ProtocolRenewalHealthState = "HEALTHY" | "HEALTHY_IDLE" | "DEGRADED" | "DOWN" | "DISABLED";

export interface ProtocolRenewalHealthReason {
  code: string;
  value: number | null;
  threshold: number | null;
}

export interface ProtocolRenewalStats {
  generated_at: string;
  period: ProtocolRenewalPeriod;
  period_started_at: string;
  timezone_offset_minutes: number;
  target_success_rate: number;
  health: {
    state: ProtocolRenewalHealthState;
    label: string;
    reasons: ProtocolRenewalHealthReason[];
    enabled: boolean;
    last_heartbeat_at: string | null;
    last_scan_at: string | null;
    last_completed_at: string | null;
  };
  attempts: {
    total: number;
    applied_success: number;
    failed: number;
    stale: number;
    strict_success_rate: number | null;
    average_latency_ms: number | null;
    average_extension_seconds: number | null;
    last_success_at: string | null;
  };
  queue: {
    pending: number;
    running: number;
    retry: number;
    fallback: number;
    expired_leases: number;
    oldest_due_age_seconds: number | null;
  };
  coverage: {
    session_accounts: number;
    eligible_accounts: number;
    ratio: number;
  };
  trend: Array<{
    bucket_start: string;
    label: string;
    total: number;
    applied_success: number;
    failed: number;
    strict_success_rate: number | null;
  }>;
  errors: Array<{ error_code: string; count: number }>;
}

export interface ProtocolRenewalAccount {
  account_uuid: string;
  login_name: string;
  account_status: string;
  token_expires_at: string | null;
  has_session: boolean;
  status: string;
  attempt_count: number;
  lease_until: string | null;
  retry_after: string | null;
  fallback_after: string | null;
  last_attempt_at: string | null;
  last_success_at: string | null;
  last_error_code: string | null;
  previous_token_expires_at: string | null;
  renewed_token_expires_at: string | null;
  client_reported_at: string | null;
  client_version: string | null;
  renewal_capability: string | null;
  client_session_fresh: boolean;
}

export interface ProtocolRenewalAccountList {
  items: ProtocolRenewalAccount[];
  total: number;
}

export interface ProtocolRenewalEvent {
  event_uuid: string;
  account_uuid: string;
  attempt_number: number;
  outcome: string;
  applied: boolean;
  retryable: boolean;
  next_state: string;
  error_code: string | null;
  started_at: string;
  finished_at: string;
  latency_ms: number;
  previous_token_expires_at: string | null;
  renewed_token_expires_at: string | null;
}

export interface ProtocolRenewalEventList {
  items: ProtocolRenewalEvent[];
  total: number;
}

export interface AccountCreatePayload {
  space_uuid: string;
  login_name: string;
  password: string;
  label?: AccountLabel;
  video_token?: string;
  token_expires_at?: string;
  balance_credits: number;
  max_concurrency: number;
}

export interface TokenUpdatePayload {
  video_token: string;
  token_expires_at: string;
  expected_version: number;
}

export interface AccountPatchPayload {
  space_uuid?: string;
  password?: string;
  max_concurrency?: number;
  manual_status?: "ACTIVE" | "MANUAL_DISABLED";
  expected_version?: number;
}

export interface AccountBalanceRefreshResult {
  valid: boolean;
  account: Account;
  previous_balance_credits: number;
  balance_credits: number;
  credit_delta: number;
  refreshed_at: string;
  error_code: string | null;
}

export type AccountBulkDeleteOutcome = "DELETABLE" | "PROTECTED" | "MISSING" | "DELETED" | "SKIPPED";

export interface AccountBulkDeleteItem {
  account_uuid: string;
  login_name: string | null;
  outcome: AccountBulkDeleteOutcome;
  code: string | null;
  message: string | null;
}

export interface AccountBulkDeletePreview {
  requested: number;
  deletable: number;
  protected: number;
  missing: number;
  items: AccountBulkDeleteItem[];
}

export interface AccountBulkDeleteResult {
  requested: number;
  deleted: number;
  skipped: number;
  items: AccountBulkDeleteItem[];
}

export interface AccountCredentialExport {
  blob: Blob;
  filename: string;
  receipt: string;
  exportedCount: number;
}

export type CookieImportBatchStatus = "QUEUED" | "RUNNING" | "COMPLETED" | "PARTIAL_FAILED" | "FAILED";
export type CookieImportItemStatus = "QUEUED" | "RUNNING" | "RETRY_WAIT" | "CREATED" | "UPDATED" | "SKIPPED_DUPLICATE" | "FAILED";
export type CookieImportStage = "RECEIVED" | "SESSION_VALIDATION" | "BALANCE_VALIDATION" | "ACCOUNT_ACTIVATION" | "RENEWAL_READY";

export interface CookieImportItem {
  item_uuid: string;
  entry_name: string;
  entry_sha256: string;
  expected_login_name: string | null;
  discovered_login_name: string | null;
  status: CookieImportItemStatus;
  stage: CookieImportStage;
  attempt_count: number;
  retryable: boolean;
  last_error_code: string | null;
  last_error_message: string | null;
  account_uuid: string | null;
  account_status: string | null;
  balance_credits: number | null;
  token_expires_at: string | null;
  renewal_status: string | null;
  activated_at: string | null;
  finished_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface CookieImportBatch {
  batch_uuid: string;
  status: CookieImportBatchStatus;
  archive_filename: string;
  archive_sha256: string;
  space_name: string;
  item_count: number;
  queued: number;
  running: number;
  created: number;
  updated: number;
  failed: number;
  total_balance_credits: number;
  tasks_after_import: number;
  completed_tasks_after_import: number;
  failed_tasks_after_import: number;
  consumed_credits_after_import: number;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  items: CookieImportItem[];
}

export interface CookieImportBatchList {
  batches: CookieImportBatch[];
  total: number;
  limit: number;
  offset: number;
}
