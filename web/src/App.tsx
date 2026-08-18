import {
  Activity,
  AudioLines,
  AlertTriangle,
  ArrowRight,
  ArrowUpRight,
  BarChart3,
  BookOpen,
  Check,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  CircleDollarSign,
  Clock3,
  Copy,
  ChevronsLeft,
  ChevronsRight,
  Cpu,
  Database,
  Download,
  ExternalLink,
  FileArchive,
  Gauge,
  Grid2X2,
  Inbox,
  Image as ImageIcon,
  KeyRound,
  LayoutDashboard,
  List,
  Menu,
  Pencil,
  Play,
  Plus,
  Power,
  RefreshCcw,
  Search,
  Send,
  Server,
  Settings2,
  ShieldCheck,
  TimerReset,
  Trash2,
  Upload,
  UsersRound,
  Video,
  X,
  Zap,
  type LucideIcon,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent, type ReactNode } from "react";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { ApiError, VideoTaskApi, loadCredentials, saveCredentials } from "./api";
import { parseBulkAccountText, type BulkImportAccount } from "./accountImport";
import { ClientMonitorView } from "./ClientMonitorView";
import { CookieImportModal } from "./CookieImportModal";
import { parseMailboxImportPreview } from "./mailboxImport";
import { ModelDocsView } from "./ModelDocsView";
import { parseParentAccountImportPreview } from "./parentAccountImport";
import { RegistrationDrawer } from "./RegistrationDrawer";
import { RegistrationSettings } from "./RegistrationSettings";
import { taskMediaKindOf, taskMediaOf } from "./taskMedia";
import type {
  Account,
  AccountBulkDeletePreview,
  AccountBulkDeleteResult,
  AccountCredentialExport,
  AccountCreatePayload,
  AccountLabel,
  AccountPatchPayload,
  ApiCredentials,
  DashboardPeriod,
  DashboardStats,
  Mailbox,
  MailboxCodeResult,
  MailboxImportPeriod,
  MailboxImportResult,
  MailboxStats,
  ParentAccount,
  ParentAccountImportResult,
  ParentAccountStats,
  RegistrationPoolSettings,
  RegistrationRecord,
  ProtocolRenewalAccount,
  ProtocolRenewalEvent,
  ProtocolRenewalPeriod,
  ProtocolRenewalStats,
  Space,
  Task,
  ViewName,
} from "./types";

const PAGE_SIZE = 12;
const TELEGRAM_COMMUNITY_URL = "https://t.me/lowbcc";
const DASHBOARD_PERIODS: Array<{ value: DashboardPeriod; label: string; code: string; note: string }> = [
  { value: "total", label: "总数", code: "ALL TIME", note: "完整历史累计" },
  { value: "today", label: "当天", code: "TODAY", note: "本地自然日" },
  { value: "hour", label: "最近 1 小时", code: "LAST 60M", note: "滚动 60 分钟" },
];
const ACCOUNT_PAGE_SIZE_OPTIONS = [20, 50, 100] as const;
const SUCCESSFUL_ACCOUNT_PAGE_SIZE_OPTIONS = [20, 50, 100, 500] as const;
const SUCCESSFUL_ACCOUNT_PAGE_SIZE_DEFAULT = 50;
const SUCCESSFUL_ACCOUNT_PAGE_SIZE_MIN = 1;
const SUCCESSFUL_ACCOUNT_PAGE_SIZE_MAX = 500;
const SUCCESSFUL_ACCOUNT_PAGE_SIZE_STORAGE_KEY = "frame-ops-successful-account-page-size";

function normalizeSuccessfulAccountPageSize(value: unknown): number {
  const parsed = typeof value === "number" ? value : Number(value);
  if (!Number.isInteger(parsed) || parsed < SUCCESSFUL_ACCOUNT_PAGE_SIZE_MIN || parsed > SUCCESSFUL_ACCOUNT_PAGE_SIZE_MAX) {
    return SUCCESSFUL_ACCOUNT_PAGE_SIZE_DEFAULT;
  }
  return parsed;
}

export function parseSuccessfulAccountPageJump(value: string, pageCount: number): number | null {
  if (!value.trim() || pageCount < 1) return null;
  const requestedPage = Number(value);
  if (!Number.isInteger(requestedPage)) return null;
  return Math.min(pageCount - 1, Math.max(0, requestedPage - 1));
}

function loadSuccessfulAccountPageSize(): number {
  if (typeof window === "undefined") return SUCCESSFUL_ACCOUNT_PAGE_SIZE_DEFAULT;
  try {
    return normalizeSuccessfulAccountPageSize(window.localStorage.getItem(SUCCESSFUL_ACCOUNT_PAGE_SIZE_STORAGE_KEY));
  } catch {
    return SUCCESSFUL_ACCOUNT_PAGE_SIZE_DEFAULT;
  }
}

function persistSuccessfulAccountPageSize(value: number): void {
  try {
    window.localStorage.setItem(SUCCESSFUL_ACCOUNT_PAGE_SIZE_STORAGE_KEY, String(value));
  } catch {
    // Storage can be unavailable in privacy-restricted browser profiles; the in-memory setting still applies.
  }
}
const PROTOCOL_RENEWAL_PERIODS: Array<{ value: ProtocolRenewalPeriod; label: string }> = [
  { value: "hour", label: "1h" },
  { value: "six_hours", label: "6h" },
  { value: "day", label: "24h" },
  { value: "week", label: "7d" },
];
const PROTOCOL_RENEWAL_STATUS_LABELS: Record<string, string> = {
  IDLE: "正常",
  PENDING: "等待",
  RUNNING: "运行中",
  RETRY: "重试",
  FALLBACK: "已回退",
  UNCONFIGURED: "未配置",
};
const TASK_STATUS_FILTERS = ["", "QUEUED", "SUBMITTED", "PROCESSING", "COMPLETED", "FAILED"];
const ACCOUNT_STATUS_OPTIONS = [
  { value: "", label: "全部账号", code: "ALL STATUS" },
  { value: "ACTIVE", label: "正常可用", code: "ACTIVE" },
  { value: "PENDING_VALIDATION", label: "待校验", code: "PENDING VALIDATION" },
  { value: "LOW_BALANCE_DISABLED", label: "积分不足", code: "LOW BALANCE" },
  { value: "TOKEN_EXPIRING", label: "Token 将过期", code: "TOKEN EXPIRING" },
  { value: "TOKEN_EXPIRED", label: "Token 已过期", code: "TOKEN EXPIRED" },
  { value: "TOKEN_INVALID", label: "Token 无效", code: "TOKEN INVALID" },
  { value: "MANUAL_DISABLED", label: "手动停用", code: "MANUAL DISABLED" },
] as const;
const TERMINAL_SUCCESS = new Set(["COMPLETED"]);
const TERMINAL_ERROR = new Set(["FAILED", "SUBMIT_UNKNOWN", "TOKEN_INVALID", "TOKEN_EXPIRED", "LOW_BALANCE", "LOW_BALANCE_DISABLED"]);
const PENDING = new Set(["QUEUED", "WAITING_ACCOUNT", "RETRY_WAIT", "PENDING_VALIDATION"]);
const RUNNING = new Set(["ASSIGNED", "SUBMITTING", "SUBMITTED", "PROCESSING", "RUNNING", "ACTIVE"]);
const CANCELLABLE = new Set(["QUEUED", "WAITING_ACCOUNT", "RETRY_WAIT"]);
const PIE_COLORS = ["#c7f36b", "#6fd6c2", "#f5b971", "#ff7a70", "#718096", "#87a0ff"];
const VIEW_LABELS: Record<ViewName, string> = {
  overview: "运行总览",
  accounts: "账号池",
  mailboxes: "邮箱池",
  "parent-accounts": "母号池",
  "successful-accounts": "成功账号",
  "registration-clients": "客户端监控",
  tasks: "任务中心",
  docs: "模型接入",
};
const VIEW_QUERY_KEY = "view";
const VIEW_NAMES = new Set<ViewName>(["overview", "accounts", "tasks", "docs"]);

function viewFromLocation(): ViewName {
  const candidate = new URL(window.location.href).searchParams.get(VIEW_QUERY_KEY);
  return candidate && VIEW_NAMES.has(candidate as ViewName) ? candidate as ViewName : "overview";
}

function urlForView(view: ViewName): string {
  const url = new URL(window.location.href);
  if (view === "overview") url.searchParams.delete(VIEW_QUERY_KEY);
  else url.searchParams.set(VIEW_QUERY_KEY, view);
  url.hash = "";
  return `${url.pathname}${url.search}`;
}

function cn(...names: Array<string | false | null | undefined>): string {
  return names.filter(Boolean).join(" ");
}

function downloadCredentialExport(file: Pick<AccountCredentialExport, "blob" | "filename">): void {
  const url = URL.createObjectURL(file.blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = file.filename;
  anchor.style.display = "none";
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 30_000);
}

function formatNumber(value: number | null | undefined): string {
  return new Intl.NumberFormat("zh-CN").format(value ?? 0);
}

function formatDate(value: string | null | undefined, compact = false): string {
  if (!value) return "—";
  const date = new Date(value.endsWith("Z") || value.includes("+") ? value : `${value}Z`);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    ...(compact ? {} : { year: "numeric" }),
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(date);
}

function relativeTime(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value.endsWith("Z") || value.includes("+") ? value : `${value}Z`);
  const seconds = Math.round((date.getTime() - Date.now()) / 1000);
  const formatter = new Intl.RelativeTimeFormat("zh-CN", { numeric: "auto" });
  if (Math.abs(seconds) < 60) return formatter.format(seconds, "second");
  const minutes = Math.round(seconds / 60);
  if (Math.abs(minutes) < 60) return formatter.format(minutes, "minute");
  const hours = Math.round(minutes / 60);
  if (Math.abs(hours) < 24) return formatter.format(hours, "hour");
  return formatter.format(Math.round(hours / 24), "day");
}

function formatDuration(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  if (value < 60) return `${value.toFixed(1)}s`;
  const minutes = Math.floor(value / 60);
  return `${minutes}m ${Math.round(value % 60)}s`;
}

function taskIsLive(task: Pick<Task, "finished_at" | "status">): boolean {
  return !task.finished_at && !TERMINAL_SUCCESS.has(task.status) && !TERMINAL_ERROR.has(task.status) && task.status !== "CANCELED";
}

function taskDurationSeconds(task: Pick<Task, "created_at" | "finished_at" | "updated_at" | "status">, now = Date.now()): number | null {
  const createdAt = new Date(task.created_at.endsWith("Z") || task.created_at.includes("+") ? task.created_at : `${task.created_at}Z`).getTime();
  const endValue = task.finished_at ?? (taskIsLive(task) ? null : task.updated_at);
  const endAt = endValue
    ? new Date(endValue.endsWith("Z") || endValue.includes("+") ? endValue : `${endValue}Z`).getTime()
    : now;
  if (Number.isNaN(createdAt) || Number.isNaN(endAt)) return null;
  return Math.max(0, (endAt - createdAt) / 1000);
}

function taskDurationLabel(task: Task): string {
  return taskIsLive(task) ? "进行中" : "已结束";
}

function shortId(value: string | null | undefined, size = 8): string {
  if (!value) return "—";
  return `${value.slice(0, size)}…${value.slice(-4)}`;
}

function accountLoginName(account: Account): string {
  return account.login_name ?? account.login_name_masked ?? "—";
}

export function accountSourceLabel(label: AccountLabel | null | undefined): string {
  if (label === "mmoshenqi") return "mmoshenqi";
  if (label === "macbook") return "macbook";
  return "未标注";
}

function promptOf(task: Task): string {
  const prompt = task.input.prompt;
  return typeof prompt === "string" ? prompt : "未提供提示词";
}

function statusTone(status: string): string {
  if (TERMINAL_SUCCESS.has(status)) return "success";
  if (TERMINAL_ERROR.has(status)) return "danger";
  if (PENDING.has(status)) return "warning";
  if (RUNNING.has(status)) return "active";
  if (status === "CANCELED" || status === "MANUAL_DISABLED") return "muted";
  return "neutral";
}

function StatusBadge({ status, pulse = false }: { status: string; pulse?: boolean }) {
  return (
    <span className={cn("status-badge", `tone-${statusTone(status)}`)}>
      <span className={cn("status-dot", pulse && "is-pulsing")} />
      {status.replaceAll("_", " ")}
    </span>
  );
}

function IconButton({ label, children, onClick, disabled = false, className = "" }: { label: string; children: ReactNode; onClick?: () => void; disabled?: boolean; className?: string }) {
  return (
    <button className={cn("icon-button", className)} aria-label={label} title={label} onClick={onClick} disabled={disabled}>
      {children}
    </button>
  );
}

function EmptyState({ icon: Icon, title, description }: { icon: LucideIcon; title: string; description: string }) {
  return (
    <div className="empty-state">
      <div className="empty-state__icon"><Icon size={24} /></div>
      <strong>{title}</strong>
      <span>{description}</span>
    </div>
  );
}

function SkeletonRows({ count = 6 }: { count?: number }) {
  return (
    <div className="skeleton-stack">
      {Array.from({ length: count }, (_, index) => <div className="skeleton-line" key={index} style={{ animationDelay: `${index * 70}ms` }} />)}
    </div>
  );
}

function MetricCard({ label, value, suffix, icon: Icon, tone, note, progress }: { label: string; value: string; suffix?: string; icon: LucideIcon; tone: string; note: string; progress?: number }) {
  return (
    <article className={cn("metric-card", `metric-card--${tone}`)}>
      <div className="metric-card__head">
        <span>{label}</span>
        <span className="metric-card__icon"><Icon size={18} /></span>
      </div>
      <div className="metric-card__number">{value}<small>{suffix}</small></div>
      <div className="metric-card__foot">
        <span>{note}</span>
        {progress !== undefined && <div className="micro-progress"><i style={{ width: `${Math.min(progress, 100)}%` }} /></div>}
      </div>
    </article>
  );
}

function SectionHeading({ eyebrow, title, action }: { eyebrow: string; title: string; action?: ReactNode }) {
  return (
    <div className="section-heading">
      <div><span>{eyebrow}</span><h3>{title}</h3></div>
      {action}
    </div>
  );
}

function CustomTooltip({ active, payload, label }: { active?: boolean; payload?: Array<{ color: string; name: string; value: number }>; label?: string }) {
  if (!active || !payload?.length) return null;
  return (
    <div className="chart-tooltip">
      <span>{label}</span>
      {payload.map((item) => <div key={item.name}><i style={{ background: item.color }} />{item.name}<strong>{formatNumber(item.value)}</strong></div>)}
    </div>
  );
}

function Modal({ title, eyebrow, onClose, children, wide = false }: { title: string; eyebrow: string; onClose: () => void; children: ReactNode; wide?: boolean }) {
  return (
    <div className="modal-backdrop" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <div className={cn("modal", wide && "modal--wide")} role="dialog" aria-modal="true">
        <div className="modal__header">
          <div><span>{eyebrow}</span><h2>{title}</h2></div>
          <IconButton label="关闭" onClick={onClose}><X size={19} /></IconButton>
        </div>
        {children}
      </div>
    </div>
  );
}

export default function App() {
  const [credentials, setCredentials] = useState<ApiCredentials>(() => loadCredentials());
  const api = useMemo(() => new VideoTaskApi(credentials), [credentials]);
  const [view, setView] = useState<ViewName>(() => viewFromLocation());
  const [dashboardPeriod, setDashboardPeriod] = useState<DashboardPeriod>("total");
  const [stats, setStats] = useState<DashboardStats | null>(null);
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [mailboxes, setMailboxes] = useState<Mailbox[]>([]);
  const [mailboxStats, setMailboxStats] = useState<MailboxStats | null>(null);
  const [mailboxSearch, setMailboxSearch] = useState("");
  const [mailboxStatus, setMailboxStatus] = useState("");
  const [mailboxImportPeriod, setMailboxImportPeriod] = useState<MailboxImportPeriod>("");
  const [mailboxPage, setMailboxPage] = useState(0);
  const [mailboxTotal, setMailboxTotal] = useState(0);
  const [mailboxImportOpen, setMailboxImportOpen] = useState(false);
  const [mailboxCodeTarget, setMailboxCodeTarget] = useState<Mailbox | null>(null);
  const [parentAccounts, setParentAccounts] = useState<ParentAccount[]>([]);
  const [parentAccountStats, setParentAccountStats] = useState<ParentAccountStats | null>(null);
  const [parentAccountSearch, setParentAccountSearch] = useState("");
  const [parentAccountPage, setParentAccountPage] = useState(0);
  const [parentAccountTotal, setParentAccountTotal] = useState(0);
  const [parentAccountImportOpen, setParentAccountImportOpen] = useState(false);
  const [registrationParent, setRegistrationParent] = useState<ParentAccount | null>(null);
  const [registrationRecords, setRegistrationRecords] = useState<RegistrationRecord[]>([]);
  const [registrationFilter, setRegistrationFilter] = useState("");
  const [registrationLoading, setRegistrationLoading] = useState(false);
  const [registrationSettings, setRegistrationSettings] = useState<RegistrationPoolSettings | null>(null);
  const [successfulAccounts, setSuccessfulAccounts] = useState<RegistrationRecord[]>([]);
  const [successfulAccountSearch, setSuccessfulAccountSearch] = useState("");
  const [successfulAccountUsage, setSuccessfulAccountUsage] = useState("");
  const [successfulAccountCredits, setSuccessfulAccountCredits] = useState("");
  const [successfulAccountPage, setSuccessfulAccountPage] = useState(0);
  const [successfulAccountPageSize, setSuccessfulAccountPageSize] = useState(loadSuccessfulAccountPageSize);
  const [successfulAccountTotal, setSuccessfulAccountTotal] = useState(0);
  const [successfulUnused8500Count, setSuccessfulUnused8500Count] = useState(0);
  const [successfulAccountLoading, setSuccessfulAccountLoading] = useState(true);
  const [selectedSuccessfulRegistrationUuids, setSelectedSuccessfulRegistrationUuids] = useState<Set<string>>(new Set());
  const [successfulAccountExporting, setSuccessfulAccountExporting] = useState(false);
  const [clientMonitorRefreshToken, setClientMonitorRefreshToken] = useState(0);
  const [renewalStats, setRenewalStats] = useState<ProtocolRenewalStats | null>(null);
  const [renewalAccounts, setRenewalAccounts] = useState<ProtocolRenewalAccount[]>([]);
  const [renewalPeriod, setRenewalPeriod] = useState<ProtocolRenewalPeriod>("hour");
  const [renewalStatus, setRenewalStatus] = useState("");
  const [renewalError, setRenewalError] = useState("");
  const [spaces, setSpaces] = useState<Space[]>([]);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [taskTotal, setTaskTotal] = useState(0);
  const [taskPage, setTaskPage] = useState(0);
  const [taskStatus, setTaskStatus] = useState("");
  const [taskModel, setTaskModel] = useState("");
  const [taskModels, setTaskModels] = useState<string[]>([]);
  const [accountStatus, setAccountStatus] = useState("");
  const [accountSearch, setAccountSearch] = useState("");
  const [taskSearch, setTaskSearch] = useState("");
  const [loading, setLoading] = useState(true);
  const [taskLoading, setTaskLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [connected, setConnected] = useState(false);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [error, setError] = useState("");
  const [toast, setToast] = useState("");
  const [settingsOpen, setSettingsOpen] = useState(!credentials.apiKey || !credentials.adminKey);
  const [addAccountOpen, setAddAccountOpen] = useState(false);
  const [bulkImportOpen, setBulkImportOpen] = useState(false);
  const [cookieImportOpen, setCookieImportOpen] = useState(false);
  const [tokenAccount, setTokenAccount] = useState<Account | null>(null);
  const [editAccount, setEditAccount] = useState<Account | null>(null);
  const [deleteAccountTarget, setDeleteAccountTarget] = useState<Account | null>(null);
  const [selectedAccountUuids, setSelectedAccountUuids] = useState<Set<string>>(new Set());
  const [bulkDeleteSelection, setBulkDeleteSelection] = useState<string[] | null>(null);
  const [accountExporting, setAccountExporting] = useState(false);
  const [balanceRefreshing, setBalanceRefreshing] = useState<Set<string>>(new Set());
  const [selectedTask, setSelectedTask] = useState<Task | null>(null);
  const [selectedRenewalAccount, setSelectedRenewalAccount] = useState<ProtocolRenewalAccount | null>(null);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const loadSequence = useRef(0);
  const taskLoadSequence = useRef(0);
  const successfulAccountLoadSequence = useRef(0);

  const showToast = useCallback((message: string) => {
    setToast(message);
    window.setTimeout(() => setToast(""), 2800);
  }, []);

  const navigateToView = useCallback((nextView: ViewName) => {
    const nextUrl = urlForView(nextView);
    const currentUrl = `${window.location.pathname}${window.location.search}${window.location.hash}`;
    if (currentUrl !== nextUrl) {
      window.history.pushState({ frameOpsView: nextView }, "", nextUrl);
    }
    setView(nextView);
    setSidebarOpen(false);
    window.scrollTo({ top: 0, behavior: "auto" });
  }, []);

  useEffect(() => {
    const syncViewFromHistory = () => {
      setView(viewFromLocation());
      setSidebarOpen(false);
    };
    window.addEventListener("popstate", syncViewFromHistory);
    return () => window.removeEventListener("popstate", syncViewFromHistory);
  }, []);

  const loadData = useCallback(async (quiet = false) => {
    if (!credentials.apiKey || !credentials.adminKey) {
      setLoading(false);
      setConnected(false);
      return;
    }
    const requestSequence = ++loadSequence.current;
    quiet ? setRefreshing(true) : setLoading(true);
    try {
      const renewalRequest = Promise.all([
        api.getProtocolRenewalStats(renewalPeriod, -new Date().getTimezoneOffset()),
        api.getProtocolRenewalAccounts(),
      ]).then(([renewalStatsData, renewalAccountData]) => ({
        stats: renewalStatsData,
        accounts: renewalAccountData.items,
        error: "",
      })).catch((cause: unknown) => ({
        stats: null,
        accounts: [] as ProtocolRenewalAccount[],
        error: cause instanceof ApiError ? cause.message : "协议续签监控读取异常",
      }));
      const [
        statsData,
        accountData,
        spaceData,
        renewalData,
      ] = await Promise.all([
        api.getStats(dashboardPeriod, -new Date().getTimezoneOffset()),
        api.getAccounts(),
        api.getSpaces(),
        renewalRequest,
      ]);
      if (requestSequence !== loadSequence.current) return;
      setStats(statsData);
      setAccounts(accountData);
      setRenewalStats(renewalData.stats);
      setRenewalAccounts(renewalData.accounts);
      setRenewalError(renewalData.error);
      setSpaces(spaceData);
      setConnected(true);
      setError("");
      setLastUpdated(new Date());
    } catch (cause) {
      if (requestSequence !== loadSequence.current) return;
      setConnected(false);
      setError(cause instanceof ApiError ? cause.message : "服务连接异常，请检查地址和访问密钥");
    } finally {
      if (requestSequence === loadSequence.current) {
        setLoading(false);
        setRefreshing(false);
      }
    }
  }, [api, credentials.apiKey, credentials.adminKey, dashboardPeriod, renewalPeriod]);

  useEffect(() => { void loadData(); }, [loadData]);

  const loadRegistrationRecords = useCallback(async (parent: ParentAccount, filter = registrationFilter) => {
    setRegistrationLoading(true);
    try {
      const result = await api.getParentRegistrations(parent.parent_account_uuid, { status: filter });
      setRegistrationRecords(result.items);
    } catch (cause) {
      showToast(cause instanceof Error ? cause.message : "注册记录读取失败");
    } finally {
      setRegistrationLoading(false);
    }
  }, [api, registrationFilter, showToast]);

  const loadSuccessfulAccounts = useCallback(async (quiet = false) => {
    if (!credentials.apiKey || !credentials.adminKey) {
      setSuccessfulAccountLoading(false);
      return;
    }
    const requestSequence = ++successfulAccountLoadSequence.current;
    if (!quiet) setSuccessfulAccountLoading(true);
    try {
      const result = await api.getSuccessfulRegistrations({
        search: successfulAccountSearch,
        isUsed: successfulAccountUsage ? successfulAccountUsage === "used" : undefined,
        credits: successfulAccountCredits ? Number(successfulAccountCredits) : undefined,
        limit: successfulAccountPageSize,
        offset: successfulAccountPage * successfulAccountPageSize,
      });
      if (requestSequence !== successfulAccountLoadSequence.current) return;
      setSuccessfulAccounts(result.items);
      setSuccessfulAccountTotal(result.total);
      setSuccessfulUnused8500Count(result.unused_8500_count ?? 0);
    } catch (cause) {
      if (requestSequence !== successfulAccountLoadSequence.current) return;
      showToast(cause instanceof Error ? cause.message : "成功账号读取失败");
    } finally {
      if (requestSequence === successfulAccountLoadSequence.current) setSuccessfulAccountLoading(false);
    }
  }, [api, credentials.apiKey, credentials.adminKey, showToast, successfulAccountCredits, successfulAccountPage, successfulAccountPageSize, successfulAccountSearch, successfulAccountUsage]);

  const loadTasks = useCallback(async (quiet = false) => {
    if (!credentials.apiKey || !credentials.adminKey) {
      setTaskLoading(false);
      return;
    }
    const requestSequence = ++taskLoadSequence.current;
    if (!quiet) setTaskLoading(true);
    try {
      const taskData = await api.getTasks(taskStatus, PAGE_SIZE, taskPage * PAGE_SIZE, taskModel);
      if (requestSequence !== taskLoadSequence.current) return;
      setTasks(taskData.items);
      setTaskTotal(taskData.total);
      setTaskModels(taskData.models ?? [...new Set(taskData.items.map((task) => task.model))].sort());
    } catch (cause) {
      if (requestSequence !== taskLoadSequence.current) return;
      showToast(cause instanceof ApiError ? cause.message : "任务列表读取异常");
    } finally {
      if (requestSequence === taskLoadSequence.current) setTaskLoading(false);
    }
  }, [api, credentials.apiKey, credentials.adminKey, showToast, taskModel, taskPage, taskStatus]);

  useEffect(() => { void loadTasks(); }, [loadTasks]);
  useEffect(() => {
    if (!autoRefresh) return;
    const timer = window.setInterval(() => {
      void loadData(true);
      void loadTasks(true);
    }, 15_000);
    return () => window.clearInterval(timer);
  }, [autoRefresh, loadData, loadTasks]);
  useEffect(() => {
    const needle = accountSearch.trim().toLowerCase();
    const renewalStates = new Map(
      renewalAccounts.map((renewal) => [renewal.account_uuid, renewal.status]),
    );
    const existing = new Set(
      accounts
        .filter((account) => (
          (!accountStatus || account.status === accountStatus)
          && (!renewalStatus || (renewalStates.get(account.account_uuid) ?? "UNCONFIGURED") === renewalStatus)
          && (!needle || accountLoginName(account).toLowerCase().includes(needle) || account.account_uuid.toLowerCase().includes(needle))
        ))
        .map((account) => account.account_uuid),
    );
    setSelectedAccountUuids((current) => {
      const next = new Set([...current].filter((accountUuid) => existing.has(accountUuid)));
      return next.size === current.size ? current : next;
    });
  }, [accounts, accountSearch, accountStatus, renewalStatus, renewalAccounts]);

  const saveSettings = (next: ApiCredentials) => {
    saveCredentials(next);
    setCredentials(next);
    setSettingsOpen(false);
    showToast("连接配置已保存");
  };

  const refreshAfterMutation = async (message: string) => {
    await Promise.all([loadData(true), loadTasks(true)]);
    showToast(message);
  };

  const toggleAccount = async (account: Account) => {
    try {
      await api.patchAccount(account.account_uuid, { manual_status: account.status === "MANUAL_DISABLED" ? "ACTIVE" : "MANUAL_DISABLED" });
      await refreshAfterMutation(account.status === "MANUAL_DISABLED" ? "账号已进入校验队列" : "账号已停用");
    } catch (cause) {
      showToast(cause instanceof Error ? cause.message : "账号状态更新失败");
    }
  };

  const refreshBalance = async (account: Account) => {
    setBalanceRefreshing((current) => new Set(current).add(account.account_uuid));
    try {
      const result = await api.refreshAccountBalance(account.account_uuid);
      await loadData(true);
      if (result.valid) {
        const delta = result.credit_delta >= 0 ? `+${formatNumber(result.credit_delta)}` : formatNumber(result.credit_delta);
        showToast(`积分已同步：${formatNumber(result.balance_credits)}（${delta}）`);
      } else {
        showToast(`积分同步未通过：${result.error_code ?? "上游未返回余额"}`);
      }
    } catch (cause) {
      showToast(cause instanceof Error ? cause.message : "积分同步失败");
    } finally {
      setBalanceRefreshing((current) => {
        const next = new Set(current);
        next.delete(account.account_uuid);
        return next;
      });
    }
  };

  const cancelTask = async (task: Task) => {
    try {
      await api.cancelTask(task.task_uuid);
      setSelectedTask(null);
      await refreshAfterMutation("任务已取消");
    } catch (cause) {
      showToast(cause instanceof Error ? cause.message : "任务取消失败");
    }
  };

  const copyText = async (value: string) => {
    try {
      await navigator.clipboard.writeText(value);
    } catch {
      const fallback = document.createElement("textarea");
      fallback.value = value;
      fallback.setAttribute("readonly", "");
      fallback.style.position = "fixed";
      fallback.style.opacity = "0";
      document.body.appendChild(fallback);
      fallback.select();
      document.execCommand("copy");
      fallback.remove();
    }
    showToast("已复制到剪贴板");
  };

  const exportSelectedAccounts = async (accountUuids: string[]) => {
    if (accountUuids.length === 0) return;
    setAccountExporting(true);
    try {
      const file = await api.exportAccountCredentials(accountUuids);
      downloadCredentialExport(file);
      showToast(`已导出 ${file.exportedCount} 个账号凭据`);
    } catch (cause) {
      showToast(cause instanceof Error ? cause.message : "账号凭据导出失败");
    } finally {
      setAccountExporting(false);
    }
  };

  const exportSuccessfulAccounts = async (registrationUuids: string[]) => {
    if (registrationUuids.length === 0) return;
    const selected = new Set(registrationUuids);
    const emails = successfulAccounts
      .filter((account) => selected.has(account.registration_uuid) && !account.is_used)
      .map(successfulAccountEmail);
    if (emails.length !== registrationUuids.length) {
      showToast("选择中包含已使用或已离开当前页的账号，请刷新后重试");
      return;
    }
    setSuccessfulAccountExporting(true);
    try {
      const file = await api.exportRegistrationCookies(emails);
      downloadCredentialExport(file);
      setSelectedSuccessfulRegistrationUuids(new Set());
      await loadSuccessfulAccounts(true);
      showToast(`已导出 ${file.exportedCount} 个账号 Cookie，并标记为已使用`);
    } catch (cause) {
      showToast(cause instanceof Error ? cause.message : "成功账号 Cookie 导出失败");
    } finally {
      setSuccessfulAccountExporting(false);
    }
  };

  const renewalByAccount: Record<string, ProtocolRenewalAccount> = Object.fromEntries(
    renewalAccounts.map((renewal) => [renewal.account_uuid, renewal]),
  );
  const filteredAccounts = accounts.filter((account) => {
    const matchesStatus = !accountStatus || account.status === accountStatus;
    const accountRenewalStatus = renewalByAccount[account.account_uuid]?.status ?? "UNCONFIGURED";
    const matchesRenewal = !renewalStatus || accountRenewalStatus === renewalStatus;
    const needle = accountSearch.trim().toLowerCase();
    const matchesSearch = !needle || accountLoginName(account).toLowerCase().includes(needle) || account.account_uuid.toLowerCase().includes(needle);
    return matchesStatus && matchesRenewal && matchesSearch;
  });
  const filteredTasks = tasks.filter((task) => {
    const needle = taskSearch.trim().toLowerCase();
    const matchesModel = !taskModel || task.model === taskModel;
    const matchesSearch = !needle || task.task_uuid.toLowerCase().includes(needle) || task.model.toLowerCase().includes(needle) || promptOf(task).toLowerCase().includes(needle);
    return matchesModel && matchesSearch;
  });
  const spacesById = Object.fromEntries(spaces.map((space) => [space.space_uuid, space]));

  return (
    <div className="app-shell">
      <aside className={cn("sidebar", sidebarOpen && "sidebar--open")}>
        <button className="sidebar__close" onClick={() => setSidebarOpen(false)} aria-label="关闭导航"><X /></button>
        <div className="brand-block">
          <div className="brand-mark"><Video size={21} /><span /></div>
          <div><strong>LEO PROXY</strong><small>GENERATION CONTROL PLANE</small></div>
        </div>
        <div className="rail-label">工作台</div>
        <nav className="main-nav">
          {([
            ["overview", LayoutDashboard, "运行总览", "01"],
            ["accounts", UsersRound, "账号池", "02"],
            ["tasks", BarChart3, "任务中心", "03"],
            ["docs", BookOpen, "模型接入", "04"],
          ] as Array<[ViewName, LucideIcon, string, string]>).map(([name, Icon, label, index]) => (
            <button key={name} className={cn("nav-item", view === name && "is-active")} aria-current={view === name ? "page" : undefined} onClick={() => navigateToView(name)}>
              <span className="nav-item__index">{index}</span><Icon size={19} /><span>{label}</span>{view === name && <ArrowRight size={16} />}
            </button>
          ))}
        </nav>
        <div className="sidebar__spacer" />
        <div className="system-card">
          <div className="system-card__head"><span>系统脉搏</span><Activity size={15} /></div>
          <div className="system-card__status"><i className={connected ? "online" : "offline"} /><strong>{connected ? "服务在线" : "连接中断"}</strong></div>
          <div className="system-card__grid">
            <div><span>API</span><b>{connected ? "READY" : "—"}</b></div>
            <div><span>刷新</span><b>15 SEC</b></div>
          </div>
          <button onClick={() => setAutoRefresh((value) => !value)}><span>{autoRefresh ? "自动刷新已开启" : "自动刷新已暂停"}</span><i className={cn("switch", autoRefresh && "is-on")} /></button>
        </div>
        <a
          className="sidebar-community"
          href={TELEGRAM_COMMUNITY_URL}
          target="_blank"
          rel="noopener noreferrer"
          aria-label="加入 Telegram 交流群"
        >
          <Send size={16} />
          <span>Telegram 交流群</span>
          <ExternalLink size={13} />
        </a>
        <button className="sidebar-settings" onClick={() => setSettingsOpen(true)}><Settings2 size={17} /><span>连接与密钥</span></button>
        <div className="sidebar-version">CONSOLE / 0.1.0</div>
      </aside>

      {sidebarOpen && <div className="mobile-overlay" onClick={() => setSidebarOpen(false)} />}

      <main className="workspace">
        <header className="topbar">
          <button className="mobile-menu" onClick={() => setSidebarOpen(true)} aria-label="打开导航"><Menu /></button>
          <div className="breadcrumb"><span>LEO PROXY</span><i>/</i><strong>{VIEW_LABELS[view]}</strong></div>
          <div className="topbar__actions">
            <div className="connection-pill"><i className={connected ? "online" : "offline"} />{connected ? "LIVE" : "OFFLINE"}</div>
            <span className="last-updated">{lastUpdated ? `更新于 ${lastUpdated.toLocaleTimeString("zh-CN", { hour12: false })}` : "等待同步"}</span>
            {view !== "docs" && <IconButton label="刷新数据" onClick={() => { void loadData(true); void loadTasks(true); }} disabled={refreshing}><RefreshCcw size={18} className={refreshing ? "spin" : ""} /></IconButton>}
            <IconButton label="连接设置" onClick={() => setSettingsOpen(true)}><KeyRound size={18} /></IconButton>
          </div>
        </header>

        {error && <div className="error-banner"><AlertTriangle size={18} /><span><strong>数据同步失败</strong>{error}</span><button onClick={() => setSettingsOpen(true)}>检查连接</button></div>}

        <div className="workspace__content">
          {view === "overview" && <Overview stats={stats} period={dashboardPeriod} tasks={tasks} accounts={accounts} loading={loading} onPeriod={setDashboardPeriod} onViewTasks={() => navigateToView("tasks")} onSelectTask={setSelectedTask} />}
          {view === "accounts" && (
            <AccountsView
              accounts={filteredAccounts}
              allAccounts={accounts}
              activeCreditTotal={stats?.accounts.active_balance_credits}
              activeCreditTarget={stats?.accounts.active_credit_target}
              spacesById={spacesById}
              loading={loading}
              search={accountSearch}
              status={accountStatus}
              renewalStats={renewalStats}
              renewalAccounts={renewalByAccount}
              renewalPeriod={renewalPeriod}
              renewalStatus={renewalStatus}
              renewalError={renewalError}
              selectedAccountUuids={selectedAccountUuids}
              accountExporting={accountExporting}
              onSelectionChange={setSelectedAccountUuids}
              onSearch={(value) => { setAccountSearch(value); setSelectedAccountUuids(new Set()); }}
              onStatus={(value) => { setAccountStatus(value); setSelectedAccountUuids(new Set()); }}
              onRenewalPeriod={setRenewalPeriod}
              onRenewalStatus={(value) => { setRenewalStatus(value); setSelectedAccountUuids(new Set()); }}
              onRenewalAccount={setSelectedRenewalAccount}
              onAdd={() => setAddAccountOpen(true)}
              onBulkImport={() => setBulkImportOpen(true)}
              onCookieImport={() => setCookieImportOpen(true)}
              onExportSelected={(accountUuids) => void exportSelectedAccounts(accountUuids)}
              onExportAndDelete={setBulkDeleteSelection}
              onToken={setTokenAccount}
              onEdit={setEditAccount}
              onDelete={setDeleteAccountTarget}
              onBalance={(account) => void refreshBalance(account)}
              balanceRefreshing={balanceRefreshing}
              onToggle={(account) => void toggleAccount(account)}
              onCopy={(value) => void copyText(value)}
            />
          )}
          {view === "mailboxes" && (
            <MailboxesView
              mailboxes={mailboxes}
              stats={mailboxStats}
              total={mailboxTotal}
              page={mailboxPage}
              search={mailboxSearch}
              status={mailboxStatus}
              importPeriod={mailboxImportPeriod}
              loading={loading}
              onPage={setMailboxPage}
              onSearch={(value) => { setMailboxSearch(value); setMailboxPage(0); }}
              onStatus={(value) => { setMailboxStatus(value); setMailboxPage(0); }}
              onImportPeriod={(value) => { setMailboxImportPeriod(value); setMailboxPage(0); }}
              onImport={() => setMailboxImportOpen(true)}
              onViewCode={setMailboxCodeTarget}
              onRevalidate={async (mailbox) => { try { await api.revalidateMailbox(mailbox.mailbox_uuid); await refreshAfterMutation(`${mailbox.email} 已加入校验队列`); } catch (cause) { showToast(cause instanceof Error ? cause.message : "重新校验失败"); } }}
              onToggle={async (mailbox) => { try { await api.patchMailbox(mailbox.mailbox_uuid, mailbox.status === "MANUAL_DISABLED" ? "PENDING_VALIDATION" : "MANUAL_DISABLED", mailbox.version); await refreshAfterMutation(mailbox.status === "MANUAL_DISABLED" ? "邮箱已恢复并等待校验" : "邮箱已停用"); } catch (cause) { showToast(cause instanceof Error ? cause.message : "邮箱状态更新失败"); } }}
              onDelete={async (mailbox) => { if (!window.confirm(`确认删除 ${mailbox.email}？`)) return; try { await api.deleteMailbox(mailbox.mailbox_uuid); await refreshAfterMutation("邮箱已删除"); } catch (cause) { showToast(cause instanceof Error ? cause.message : "邮箱删除失败"); } }}
            />
          )}
          {view === "parent-accounts" && (
            <ParentAccountsView
              parentAccounts={parentAccounts}
              stats={parentAccountStats}
              total={parentAccountTotal}
              page={parentAccountPage}
              search={parentAccountSearch}
              loading={loading}
              onPage={setParentAccountPage}
              onSearch={(value) => { setParentAccountSearch(value); setParentAccountPage(0); }}
              onImport={() => setParentAccountImportOpen(true)}
              onCopyPassword={(value) => void copyText(value)}
              onCopyInviteUrl={(value) => void copyText(value)}
              onOpenRegistrations={(parentAccount) => { setRegistrationParent(parentAccount); setRegistrationFilter(""); void loadRegistrationRecords(parentAccount, ""); }}
              onDelete={async (parentAccount) => {
                if (!window.confirm(`确认删除母号 ${parentAccount.email}？`)) return;
                try {
                  await api.deleteParentAccount(parentAccount.parent_account_uuid);
                  if (parentAccountPage > 0 && parentAccounts.length === 1) {
                    setParentAccountPage((current) => Math.max(0, current - 1));
                    showToast("母号已删除");
                  } else {
                    await refreshAfterMutation("母号已删除");
                  }
                } catch (cause) {
                  showToast(cause instanceof Error ? cause.message : "母号删除失败");
                }
              }}
            />
          )}
          {view === "successful-accounts" && (
            <SuccessfulAccountsView
              accounts={successfulAccounts}
              total={successfulAccountTotal}
              page={successfulAccountPage}
              pageSize={successfulAccountPageSize}
              search={successfulAccountSearch}
              usage={successfulAccountUsage}
              credits={successfulAccountCredits}
              unused8500Count={successfulUnused8500Count}
              loading={successfulAccountLoading}
              selectedRegistrationUuids={selectedSuccessfulRegistrationUuids}
              exporting={successfulAccountExporting}
              onPage={setSuccessfulAccountPage}
              onPageSize={(value) => {
                const normalized = normalizeSuccessfulAccountPageSize(value);
                setSuccessfulAccountPageSize(normalized);
                persistSuccessfulAccountPageSize(normalized);
                setSuccessfulAccountPage(0);
                setSelectedSuccessfulRegistrationUuids(new Set());
              }}
              onSearch={(value) => { setSuccessfulAccountSearch(value); setSuccessfulAccountPage(0); setSelectedSuccessfulRegistrationUuids(new Set()); }}
              onUsage={(value) => { setSuccessfulAccountUsage(value); setSuccessfulAccountPage(0); setSelectedSuccessfulRegistrationUuids(new Set()); }}
              onCredits={(value) => { setSuccessfulAccountCredits(value); setSuccessfulAccountPage(0); setSelectedSuccessfulRegistrationUuids(new Set()); }}
              onQuickUnused8500={() => { setSuccessfulAccountUsage("unused"); setSuccessfulAccountCredits("8500"); setSuccessfulAccountPage(0); setSelectedSuccessfulRegistrationUuids(new Set()); }}
              onSelectionChange={setSelectedSuccessfulRegistrationUuids}
              onExport={(registrationUuids) => void exportSuccessfulAccounts(registrationUuids)}
            />
          )}
          {view === "registration-clients" && (
            <ClientMonitorView
              api={api}
              autoRefresh={autoRefresh}
              refreshToken={clientMonitorRefreshToken}
              onCopy={(value) => void copyText(value)}
            />
          )}
          {view === "tasks" && (
            <TasksView
              tasks={filteredTasks}
              total={taskTotal}
              page={taskPage}
              status={taskStatus}
              model={taskModel}
              models={taskModels}
              search={taskSearch}
              loading={taskLoading}
              onPage={setTaskPage}
              onStatus={(value) => { setTaskStatus(value); setTaskPage(0); }}
              onModel={(value) => { setTaskModel(value); setTaskPage(0); }}
              onSearch={setTaskSearch}
              onSelect={setSelectedTask}
              onCopy={(value) => void copyText(value)}
            />
          )}
          {view === "docs" && <ModelDocsView api={api} apiBase={credentials.apiBase} onCopy={(value) => void copyText(value)} />}
        </div>
      </main>

      {settingsOpen && <SettingsModal credentials={credentials} api={api} spaces={spaces} registrationSettings={null} onRegistrationSettings={async () => undefined} onSave={saveSettings} onClose={() => credentials.apiKey && credentials.adminKey && setSettingsOpen(false)} />}
      {addAccountOpen && <AddAccountModal spaces={spaces} api={api} onClose={() => setAddAccountOpen(false)} onCreated={async () => { setAddAccountOpen(false); await refreshAfterMutation("账号已添加，正在校验 Token"); }} />}
      {bulkImportOpen && <BulkImportAccountModal spaces={spaces} existingAccounts={accounts} api={api} onClose={() => setBulkImportOpen(false)} onImported={async (imported, failed) => { await loadData(true); showToast(`批量导入完成：成功 ${imported} 个${failed ? `，待处理 ${failed} 个` : ""}`); }} />}
      {cookieImportOpen && <CookieImportModal api={api} onClose={() => setCookieImportOpen(false)} onImported={async (batch) => { await loadData(true); showToast(`Cookie 导入完成：新增 ${batch.created} 个，更新 ${batch.updated} 个，失败 ${batch.failed} 个`); }} />}
      {mailboxImportOpen && <MailboxImportModal existingEmails={mailboxes.map((mailbox) => mailbox.email)} api={api} onClose={() => setMailboxImportOpen(false)} onImported={async (result) => { await loadData(true); showToast(`邮箱导入完成：成功 ${result.imported} 个，跳过 ${result.duplicates + result.invalid} 个`); }} />}
      {parentAccountImportOpen && <ParentAccountImportModal existingEmails={parentAccounts.map((parentAccount) => parentAccount.email)} api={api} onClose={() => setParentAccountImportOpen(false)} onImported={async (result) => { await loadData(true); showToast(`母号导入完成：成功 ${result.imported} 个，跳过 ${result.duplicates + result.invalid} 个`); }} />}
      {mailboxCodeTarget && <MailboxCodeModal mailbox={mailboxCodeTarget} api={api} onClose={() => setMailboxCodeTarget(null)} onCopy={(value) => void copyText(value)} />}
      {tokenAccount && <TokenModal account={tokenAccount} api={api} onClose={() => setTokenAccount(null)} onUpdated={async () => { setTokenAccount(null); await refreshAfterMutation("Token 已更新，账号进入校验队列"); }} />}
      {editAccount && <EditAccountModal account={editAccount} spaces={spaces} api={api} onClose={() => setEditAccount(null)} onUpdated={async () => { setEditAccount(null); await refreshAfterMutation("账号资料已更新"); }} />}
      {deleteAccountTarget && <DeleteAccountModal account={deleteAccountTarget} api={api} onClose={() => setDeleteAccountTarget(null)} onDeleted={async () => { setDeleteAccountTarget(null); await refreshAfterMutation("账号已从账号池删除"); }} />}
      {bulkDeleteSelection && <BulkExportDeleteModal accountUuids={bulkDeleteSelection} api={api} onClose={() => setBulkDeleteSelection(null)} onCompleted={async (result) => { const deletedUuids = new Set(result.items.filter((item) => item.outcome === "DELETED").map((item) => item.account_uuid)); setSelectedAccountUuids((current) => new Set([...current].filter((accountUuid) => !deletedUuids.has(accountUuid)))); await loadData(true); showToast(`已导出 ${result.requested} 个账号，删除 ${result.deleted} 个，保留 ${result.skipped} 个`); }} />}
      {selectedTask && <TaskDrawer task={selectedTask} onClose={() => setSelectedTask(null)} onCopy={(value) => void copyText(value)} onCancel={CANCELLABLE.has(selectedTask.status) ? () => void cancelTask(selectedTask) : undefined} />}
      {selectedRenewalAccount && <ProtocolRenewalModal account={selectedRenewalAccount} api={api} onClose={() => setSelectedRenewalAccount(null)} />}
      {registrationParent && <RegistrationDrawer parent={parentAccounts.find((item) => item.parent_account_uuid === registrationParent.parent_account_uuid) ?? registrationParent} records={registrationRecords} settings={registrationSettings} loading={registrationLoading} onClose={() => setRegistrationParent(null)} onFilter={(value) => { setRegistrationFilter(value); void loadRegistrationRecords(registrationParent, value); }} onRefresh={() => void loadRegistrationRecords(registrationParent)} onRevalidate={async (record) => { try { await api.revalidateRegistration(record.registration_uuid); await loadRegistrationRecords(registrationParent); showToast("已加入重新校验队列"); } catch (cause) { showToast(cause instanceof Error ? cause.message : "重新校验失败"); } }} onPromote={async (record) => { try { await api.promoteRegistration(record.registration_uuid); await Promise.all([loadRegistrationRecords(registrationParent), loadData(true)]); showToast("注册账号已加入账号池"); } catch (cause) { showToast(cause instanceof Error ? cause.message : "加入账号池失败"); } }} />}
      {toast && <div className="toast"><Check size={16} />{toast}</div>}
    </div>
  );
}

function Overview({ stats, period, tasks, accounts, loading, onPeriod, onViewTasks, onSelectTask }: { stats: DashboardStats | null; period: DashboardPeriod; tasks: Task[]; accounts: Account[]; loading: boolean; onPeriod: (period: DashboardPeriod) => void; onViewTasks: () => void; onSelectTask: (task: Task) => void }) {
  const visibleStats = stats?.period === period ? stats : null;
  const periodOption = DASHBOARD_PERIODS.find((option) => option.value === period) ?? DASHBOARD_PERIODS[0];
  const theoreticalCapacity = visibleStats?.accounts.max_concurrency ?? 0;
  const effectiveCapacity = visibleStats?.accounts.effective_max_concurrency ?? theoreticalCapacity;
  const effectiveAvailable = visibleStats?.accounts.effective_available_concurrency
    ?? Math.max(effectiveCapacity - (visibleStats?.accounts.active_tasks ?? 0), 0);
  const capacity = effectiveCapacity
    ? ((visibleStats?.accounts.active_tasks ?? 0) / effectiveCapacity) * 100
    : 0;
  const activePercent = visibleStats?.accounts.total ? (visibleStats.accounts.active / visibleStats.accounts.total) * 100 : 0;
  const taskPie = visibleStats?.task_statuses.filter((item) => item.count > 0).map((item) => ({ name: item.status.replaceAll("_", " "), value: item.count })) ?? [];
  const timeline = visibleStats?.task_trend ?? [];
  const trendEyebrow = period === "total" ? "7 DAY THROUGHPUT" : period === "today" ? "TODAY · HOURLY" : "LAST 60 MIN · 5 MIN BUCKET";
  const trendCreditLabel = period === "total" ? "近 7 日积分" : period === "today" ? "当天积分" : "近 1 小时积分";
  const consumptionLabel = period === "total" ? "历史累计消耗" : period === "today" ? "当天消耗" : "近 1 小时消耗";
  const timezoneOffset = -new Date().getTimezoneOffset();
  const timezoneLabel = `UTC${timezoneOffset >= 0 ? "+" : "-"}${String(Math.floor(Math.abs(timezoneOffset) / 60)).padStart(2, "0")}:${String(Math.abs(timezoneOffset) % 60).padStart(2, "0")}`;
  const attentionAccounts = accounts.filter((account) => account.status !== "ACTIVE").slice(0, 4);
  return (
    <div className="page page--overview">
      <div className="page-intro reveal">
        <div><span className="eyebrow">CONTROL ROOM / 实时数据</span><h1>运行脉络，一屏掌握。</h1><p>观察账号容量、任务吞吐与积分消耗，快速定位调度链路中的异常节点。</p></div>
        <div className="briefing-stamp"><span>{new Date().toLocaleDateString("zh-CN", { month: "short", day: "2-digit" })}</span><strong>OPS<br />BRIEF</strong><i /></div>
      </div>
      <section className="report-window reveal reveal--delay-1" aria-label="报表时间维度">
        <div className="report-window__label">
          <span className="report-window__icon"><Clock3 size={18} /></span>
          <div><span>REPORT WINDOW</span><strong>任务统计时间维度</strong><small>{periodOption.note} · {timezoneLabel} · 账户为实时快照</small></div>
        </div>
        <div className="report-window__options" role="group" aria-label="切换统计时间维度" aria-busy={loading || !visibleStats}>
          {DASHBOARD_PERIODS.map((option, index) => (
            <button key={option.value} className={option.value === period ? "is-active" : ""} aria-pressed={option.value === period} onClick={() => onPeriod(option.value)}>
              <i>0{index + 1}</i><span><strong>{option.label}</strong><small>{option.code}</small></span><b>{option.value === period ? <Check size={14} /> : <ArrowRight size={13} />}</b>
            </button>
          ))}
        </div>
      </section>
      {!visibleStats ? <SkeletonRows count={8} /> : <>
        <section className="metric-grid reveal reveal--delay-1">
          <MetricCard label="账号可用率" value={`${activePercent.toFixed(1)}`} suffix="%" icon={ShieldCheck} tone="lime" note={`${stats?.accounts.active ?? 0} / ${stats?.accounts.total ?? 0} 个账号在线`} progress={activePercent} />
          <MetricCard label="任务成功率" value={`${stats?.tasks.success_rate.toFixed(1) ?? "0.0"}`} suffix="%" icon={CheckCircle2} tone="aqua" note={`${formatNumber(stats?.tasks.completed)} 次生成已完成`} progress={stats?.tasks.success_rate ?? 0} />
          <MetricCard label="可用积分" value={formatNumber(stats?.accounts.available_credits)} icon={CircleDollarSign} tone="amber" note={`${consumptionLabel} ${formatNumber(stats?.tasks.consumed_credits)}`} />
          <MetricCard label="当前负载" value={formatNumber(stats?.accounts.active_tasks)} suffix=" TASKS" icon={Gauge} tone="coral" note={`有效 ${formatNumber(effectiveCapacity)} · 可用 ${formatNumber(effectiveAvailable)} · 理论 ${formatNumber(theoreticalCapacity)}`} progress={capacity} />
        </section>

        <section className="dashboard-grid reveal reveal--delay-2">
          <article className="panel panel--timeline">
            <SectionHeading eyebrow={trendEyebrow} title="任务吞吐趋势" action={<div className="legend"><span><i className="lime" />完成</span><span><i className="coral" />失败</span></div>} />
            <div className="chart-wrap">
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={timeline} margin={{ top: 12, right: 8, left: -25, bottom: 0 }}>
                  <defs><linearGradient id="completedFill" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="#c7f36b" stopOpacity={0.36} /><stop offset="100%" stopColor="#c7f36b" stopOpacity={0} /></linearGradient></defs>
                  <CartesianGrid stroke="#ffffff12" vertical={false} />
                  <XAxis dataKey="label" axisLine={false} tickLine={false} tick={{ fill: "#7f897d", fontSize: 11 }} />
                  <YAxis allowDecimals={false} axisLine={false} tickLine={false} tick={{ fill: "#7f897d", fontSize: 11 }} />
                  <Tooltip content={<CustomTooltip />} />
                  <Area type="monotone" dataKey="completed" name="完成" stroke="#c7f36b" strokeWidth={2.5} fill="url(#completedFill)" />
                  <Area type="monotone" dataKey="failed" name="失败" stroke="#ff7a70" strokeWidth={2} fill="transparent" />
                </AreaChart>
              </ResponsiveContainer>
            </div>
            <div className="timeline-foot"><span>{periodOption.label}任务 <strong>{formatNumber(stats?.tasks.total)}</strong></span><span>平均处理 <strong>{formatDuration(stats?.tasks.average_duration_seconds)}</strong></span><span>{trendCreditLabel} <strong>{formatNumber(timeline.reduce((sum, bucket) => sum + bucket.credits, 0))}</strong></span></div>
          </article>

          <article className="panel panel--distribution">
            <SectionHeading eyebrow="TASK PULSE" title="任务状态分布" />
            <div className="donut-wrap">
              <ResponsiveContainer width="100%" height={210}>
                <PieChart><Pie data={taskPie} innerRadius={63} outerRadius={86} paddingAngle={3} dataKey="value" stroke="none">{taskPie.map((_, index) => <Cell key={index} fill={PIE_COLORS[index % PIE_COLORS.length]} />)}</Pie><Tooltip content={<CustomTooltip />} /></PieChart>
              </ResponsiveContainer>
              <div className="donut-center"><strong>{formatNumber(stats?.tasks.total)}</strong><span>{periodOption.label}任务</span></div>
            </div>
            <div className="distribution-list">{taskPie.slice(0, 5).map((item, index) => <div key={item.name}><i style={{ background: PIE_COLORS[index % PIE_COLORS.length] }} /><span>{item.name}</span><strong>{item.value}</strong></div>)}</div>
          </article>

          <article className="panel panel--capacity">
            <SectionHeading eyebrow="ACCOUNT CAPACITY" title="账号池容量" />
            <div className="capacity-hero"><div><strong>{formatNumber(stats?.accounts.active_tasks)}</strong><span>正在执行</span></div><ArrowUpRight size={28} /><div><strong>{formatNumber(effectiveCapacity)}</strong><span>有效并发</span></div></div>
            <div className="capacity-track"><i style={{ width: `${Math.max(2, Math.min(capacity, 100))}%` }} /><span style={{ left: `${Math.min(capacity, 96)}%` }}>{capacity.toFixed(0)}%</span></div>
            <div className="capacity-facts">
              <div><span>有效可用槽位</span><strong>{formatNumber(effectiveAvailable)}</strong></div>
              <div><span>需要关注</span><strong className={stats?.accounts.attention ? "warn" : ""}>{formatNumber(stats?.accounts.attention)}</strong></div>
              <div><span>24h 内过期</span><strong>{formatNumber(stats?.accounts.expiring_24h)}</strong></div>
              <div><span>账号理论槽位</span><strong>{formatNumber(theoreticalCapacity)}</strong></div>
            </div>
            {attentionAccounts.length > 0 && <div className="attention-strip">{attentionAccounts.map((account) => <span key={account.account_uuid}><i />{accountLoginName(account)}<b>{account.status}</b></span>)}</div>}
          </article>

          <article className="panel panel--models">
            <SectionHeading eyebrow="MODEL ECONOMY" title="模型任务与积分" />
            <div className="model-chart">
              <ResponsiveContainer width="100%" height="100%"><BarChart data={stats?.models ?? []} layout="vertical" margin={{ left: 0, right: 10, top: 4, bottom: 4 }}><CartesianGrid stroke="#ffffff0d" horizontal={false} /><XAxis type="number" hide /><YAxis type="category" dataKey="model" width={118} axisLine={false} tickLine={false} tick={{ fill: "#aeb8aa", fontSize: 11 }} /><Tooltip content={<CustomTooltip />} /><Bar dataKey="total" name="任务" radius={[0, 5, 5, 0]} fill="#6fd6c2" barSize={11} /></BarChart></ResponsiveContainer>
            </div>
            <div className="model-summary">{(stats?.models ?? []).slice(0, 3).map((model, index) => <div key={model.model}><span>0{index + 1}</span><strong>{model.model}</strong><em>{model.completed}/{model.total}</em><b>{formatNumber(model.credits)} pts</b></div>)}</div>
          </article>
        </section>

        <section className="panel recent-panel reveal reveal--delay-3">
          <SectionHeading eyebrow="LATEST ACTIVITY" title="最近任务" action={<button className="text-button" onClick={onViewTasks}>查看全部 <ArrowRight size={15} /></button>} />
          {tasks.length === 0 ? <EmptyState icon={Video} title="还没有任务" description="提交任务后，执行轨迹会显示在这里。" /> : <div className="recent-list">{tasks.slice(0, 6).map((task) => <button key={task.task_uuid} onClick={() => onSelectTask(task)}><span className="recent-list__icon"><Video size={17} /></span><div><strong>{promptOf(task)}</strong><small>{task.model} · {shortId(task.task_uuid)}</small></div><StatusBadge status={task.status} pulse={RUNNING.has(task.status)} /><span>{relativeTime(task.created_at)}</span><ChevronRight size={17} /></button>)}</div>}
        </section>
      </>}
    </div>
  );
}

function protocolRenewalTone(status: string): "ok" | "warn" | "bad" | "muted" {
  if (["HEALTHY", "HEALTHY_IDLE", "IDLE"].includes(status)) return "ok";
  if (["PENDING", "RUNNING", "RETRY", "DEGRADED"].includes(status)) return "warn";
  if (["FALLBACK", "DOWN"].includes(status)) return "bad";
  return "muted";
}

function durationLabel(seconds: number | null): string {
  if (seconds === null) return "—";
  if (seconds < 60) return `${Math.round(seconds)} 秒`;
  if (seconds < 3600) return `${Math.round(seconds / 60)} 分钟`;
  return `${(seconds / 3600).toFixed(1)} 小时`;
}

function ProtocolRenewalOverview({ stats, period, activeStatus, error, onPeriod, onStatus }: { stats: ProtocolRenewalStats | null; period: ProtocolRenewalPeriod; activeStatus: string; error: string; onPeriod: (period: ProtocolRenewalPeriod) => void; onStatus: (status: string) => void }) {
  const queueTotal = stats ? stats.queue.pending + stats.queue.running + stats.queue.retry + stats.queue.fallback : 0;
  const successRate = stats?.attempts.strict_success_rate;
  return (
    <section className="protocol-renewal panel reveal reveal--delay-2" aria-labelledby="protocol-renewal-title">
      <div className="protocol-renewal__header">
        <div><span>PROTOCOL RENEWAL / 自动续签</span><strong id="protocol-renewal-title">协议续签监控</strong><small>同时检测执行器存活与续签结果有效性</small></div>
        <div className="protocol-renewal__controls">
          <div className="protocol-period-tabs" role="group" aria-label="续签统计窗口">{PROTOCOL_RENEWAL_PERIODS.map((option) => <button key={option.value} className={period === option.value ? "is-active" : ""} aria-pressed={period === option.value} onClick={() => onPeriod(option.value)}>{option.label}</button>)}</div>
          <span className={cn("protocol-health", `protocol-health--${protocolRenewalTone(stats?.health.state ?? "DISABLED")}`)}><i />{stats?.health.label ?? (error ? "数据读取异常" : "正在读取")}</span>
        </div>
      </div>
      {error && <div className="protocol-renewal__error"><AlertTriangle size={16} />{error}</div>}
      <div className="protocol-renewal__metrics">
        <div><span>严格成功率</span><strong>{successRate === null || successRate === undefined ? "—" : `${successRate.toFixed(1)}%`}</strong><small>目标 ≥ {stats?.target_success_rate.toFixed(0) ?? "—"}% · {formatNumber(stats?.attempts.total)} 次</small></div>
        <div><span>成功 / 失败</span><strong>{formatNumber(stats?.attempts.applied_success)} <i>/</i> {formatNumber(stats?.attempts.failed)}</strong><small>平均耗时 {stats?.attempts.average_latency_ms ? `${Math.round(stats.attempts.average_latency_ms)} ms` : "—"}</small></div>
        <button className={activeStatus && ["PENDING", "RUNNING", "RETRY", "FALLBACK"].includes(activeStatus) ? "is-active" : ""} onClick={() => onStatus("")}><span>当前队列</span><strong>{formatNumber(queueTotal)}</strong><small>最老等待 {durationLabel(stats?.queue.oldest_due_age_seconds ?? null)}</small></button>
        <button className={activeStatus === "UNCONFIGURED" ? "is-active" : ""} onClick={() => onStatus(activeStatus === "UNCONFIGURED" ? "" : "UNCONFIGURED")}><span>会话覆盖</span><strong>{stats ? `${stats.coverage.ratio.toFixed(1)}%` : "—"}</strong><small>{formatNumber(stats?.coverage.session_accounts)} / {formatNumber(stats?.coverage.eligible_accounts)} 个有效账号</small></button>
        <div><span>最近成功</span><strong>{stats?.attempts.last_success_at ? relativeTime(stats.attempts.last_success_at) : "—"}</strong><small>平均延长 {durationLabel(stats?.attempts.average_extension_seconds ?? null)}</small></div>
      </div>
      <div className="protocol-renewal__body">
        <div className="protocol-trend" aria-label="协议续签成功失败趋势">
          <div className="protocol-subheading"><strong>续签趋势</strong><span>成功 / 失败</span></div>
          {stats?.trend.length ? <ResponsiveContainer width="100%" height={190}><BarChart data={stats.trend} margin={{ top: 8, right: 8, left: -20, bottom: 0 }}><CartesianGrid strokeDasharray="3 3" vertical={false} stroke="rgba(255,255,255,.07)" /><XAxis dataKey="label" tick={{ fill: "#829080", fontSize: 10 }} axisLine={false} tickLine={false} interval="preserveStartEnd" /><YAxis tick={{ fill: "#829080", fontSize: 10 }} axisLine={false} tickLine={false} allowDecimals={false} /><Tooltip contentStyle={{ background: "#101a14", border: "1px solid #314232", borderRadius: 8 }} /><Bar dataKey="applied_success" name="成功" stackId="renewal" fill="#b8ee62" radius={[3, 3, 0, 0]} /><Bar dataKey="failed" name="失败" stackId="renewal" fill="#ff776d" radius={[3, 3, 0, 0]} /></BarChart></ResponsiveContainer> : <div className="protocol-empty">当前窗口暂无续签终态，心跳仍会持续确认作业存活。</div>}
        </div>
        <div className="protocol-queue">
          <div className="protocol-subheading"><strong>队列与失败</strong><span>{stats?.health.last_heartbeat_at ? `心跳 ${relativeTime(stats.health.last_heartbeat_at)}` : "等待心跳"}</span></div>
          <div className="protocol-queue__grid">
            {([['PENDING', '等待', stats?.queue.pending], ['RUNNING', '运行中', stats?.queue.running], ['RETRY', '重试', stats?.queue.retry], ['FALLBACK', '回退', stats?.queue.fallback]] as Array<[string, string, number | undefined]>).map(([value, label, count]) => <button key={value} className={cn(`tone-${protocolRenewalTone(value)}`, activeStatus === value && "is-active")} onClick={() => onStatus(activeStatus === value ? "" : value)}><span>{label}</span><strong>{formatNumber(count)}</strong></button>)}
          </div>
          <div className="protocol-errors">
            {stats?.errors.length ? stats.errors.slice(0, 4).map((item) => <div key={item.error_code}><span title={item.error_code}>{item.error_code.replace(/^PROTOCOL_/, "")}</span><strong>{item.count}</strong></div>) : <div><span>暂无失败记录</span><strong>0</strong></div>}
          </div>
          {!!stats?.health.reasons.length && <div className="protocol-reasons">{stats.health.reasons.map((reason) => <span key={reason.code}><AlertTriangle size={12} />{reason.code}</span>)}</div>}
        </div>
      </div>
    </section>
  );
}

function RenewalAccountCell({ renewal, onClick }: { renewal: ProtocolRenewalAccount | undefined; onClick: () => void }) {
  const status = renewal?.status ?? "UNCONFIGURED";
  const timestamp = renewal?.last_success_at ?? renewal?.last_attempt_at;
  const label = renewal && !renewal.client_session_fresh ? "待会话轮换" : (PROTOCOL_RENEWAL_STATUS_LABELS[status] ?? status);
  const detail = renewal && !renewal.client_session_fresh
    ? (renewal.client_reported_at ? `客户端上报于 ${relativeTime(renewal.client_reported_at)}` : "等待新版客户端上报")
    : (renewal?.last_error_code ?? (timestamp ? relativeTime(timestamp) : "等待会话上报"));
  return <button type="button" className={cn("renewal-account-cell", `renewal-account-cell--${protocolRenewalTone(status)}`)} onClick={onClick} disabled={!renewal}><span><i />{label}</span><small>{detail}</small></button>;
}

type MailboxesViewProps = {
  mailboxes: Mailbox[];
  stats: MailboxStats | null;
  total: number;
  page: number;
  search: string;
  status: string;
  importPeriod: MailboxImportPeriod;
  loading: boolean;
  onPage: (page: number) => void;
  onSearch: (value: string) => void;
  onStatus: (value: string) => void;
  onImportPeriod: (value: MailboxImportPeriod) => void;
  onImport: () => void;
  onViewCode: (mailbox: Mailbox) => void;
  onRevalidate: (mailbox: Mailbox) => Promise<void>;
  onToggle: (mailbox: Mailbox) => Promise<void>;
  onDelete: (mailbox: Mailbox) => Promise<void>;
};

const MAILBOX_STATUS_OPTIONS = [
  { value: "", label: "全部邮箱", code: "ALL MAILBOXES", stat: "total" },
  { value: "ACTIVE", label: "正常可用", code: "ACTIVE", stat: "active" },
  { value: "PENDING_VALIDATION", label: "待校验", code: "PENDING", stat: "pending_validation" },
  { value: "INVALID", label: "凭据失效", code: "INVALID", stat: "invalid" },
  { value: "MANUAL_DISABLED", label: "手动停用", code: "DISABLED", stat: "manual_disabled" },
] as const;

const MAILBOX_IMPORT_PERIOD_OPTIONS: Array<{ value: MailboxImportPeriod; label: string; code: string }> = [
  { value: "", label: "全部时间", code: "ALL" },
  { value: "today", label: "今天", code: "TODAY" },
  { value: "yesterday", label: "昨天", code: "YESTERDAY" },
  { value: "recent_7d", label: "2–7 天", code: "RECENT 7D" },
  { value: "older", label: "7 天前", code: "OLDER" },
];

export function MailboxesView({ mailboxes, stats, total, page, search, status, importPeriod, loading, onPage, onSearch, onStatus, onImportPeriod, onImport, onViewCode, onRevalidate, onToggle, onDelete }: MailboxesViewProps) {
  const pageCount = Math.max(1, Math.ceil(total / 50));
  const statusCount = (field: (typeof MAILBOX_STATUS_OPTIONS)[number]["stat"]) => stats?.[field] ?? 0;
  return (
    <div className="page page--mailboxes">
      <div className="page-title reveal"><div><span className="eyebrow">MAILBOX POOL / 验证码资源</span><h1>邮箱池</h1><p>独立管理 Microsoft 邮箱 OAuth 凭据、后台校验状态与最近邮件读取能力。</p></div><div className="page-title__actions"><button className="primary-button" onClick={onImport}><Upload size={17} />批量导入邮箱</button></div></div>
      <section className="mailbox-status-overview reveal reveal--delay-1">
        <div className="account-status-overview__heading"><div><span>MAILBOX STATUS</span><strong>邮箱状态统计</strong></div><small>点击状态筛选邮箱，凭据仅在服务端加密保存</small></div>
        <div className="mailbox-status-overview__list">
          {MAILBOX_STATUS_OPTIONS.map((option) => { const selected = status === option.value; return <button key={option.value} type="button" className={cn("account-status-card", `account-status-card--${option.value ? statusTone(option.value) : "all"}`, selected && "is-active")} onClick={() => onStatus(selected && option.value ? "" : option.value)}><span><i />{option.label}</span><strong>{formatNumber(statusCount(option.stat))}</strong><small>{option.code}</small></button>; })}
        </div>
      </section>
      <section className="panel table-panel reveal reveal--delay-2">
        <div className="mailbox-import-period-filter" role="group" aria-label="按导入时间分类">
          <span><Clock3 size={15} />导入时间</span>
          {MAILBOX_IMPORT_PERIOD_OPTIONS.map((option) => <button key={option.value} type="button" className={cn(importPeriod === option.value && "is-active")} aria-pressed={importPeriod === option.value} onClick={() => onImportPeriod(option.value)}><strong>{option.label}</strong><small>{option.code}</small></button>)}
        </div>
        <div className="table-toolbar"><div className="search-box"><Search size={17} /><input value={search} onChange={(event) => onSearch(event.target.value)} placeholder="搜索邮箱或 mailbox UUID…" /></div><span className="active-filter-label">{MAILBOX_STATUS_OPTIONS.find((option) => option.value === status)?.label ?? status}</span><span className="active-filter-label">{MAILBOX_IMPORT_PERIOD_OPTIONS.find((option) => option.value === importPeriod)?.label ?? importPeriod}</span><span className="result-count">匹配 {formatNumber(total)} 个邮箱</span></div>
        {loading ? <SkeletonRows /> : mailboxes.length === 0 ? <EmptyState icon={Inbox} title="没有匹配邮箱" description="调整状态、导入时间或搜索条件，或批量导入新的邮箱凭据。" /> : <div className="data-table-wrap"><table className="data-table mailbox-table"><thead><tr><th>邮箱</th><th>状态</th><th>导入时间</th><th>最近校验</th><th>最近邮件</th><th>校验错误</th><th>操作</th></tr></thead><tbody>{mailboxes.map((mailbox) => <tr key={mailbox.mailbox_uuid}><td><div className="identity-cell"><span className="account-avatar"><Inbox size={16} /></span><div><strong>{mailbox.email}</strong><small>{shortId(mailbox.mailbox_uuid, 18)}</small></div></div></td><td><StatusBadge status={mailbox.status} pulse={mailbox.status === "PENDING_VALIDATION"} />{mailbox.disabled_reason && <small className="cell-note">{mailbox.disabled_reason}</small>}</td><td><div className="date-cell mailbox-import-date"><Upload size={14} /><div><strong>{formatDate(mailbox.created_at)}</strong><span>{relativeTime(mailbox.created_at)}</span></div></div></td><td><div className="date-cell"><Clock3 size={14} /><div><strong>{formatDate(mailbox.last_validated_at)}</strong><span>{relativeTime(mailbox.last_validated_at)} · {mailbox.validation_attempts} 次</span></div></div></td><td><div className="date-cell"><Inbox size={14} /><div><strong>{formatDate(mailbox.last_message_received_at)}</strong><span>{relativeTime(mailbox.last_message_received_at)}</span></div></div></td><td>{mailbox.last_error_code ? <div className="mailbox-error"><strong>{mailbox.last_error_code}</strong><span>{mailbox.last_error_message}</span></div> : <span className="mailbox-ok"><ShieldCheck size={14} />暂无错误</span>}</td><td><div className="row-actions"><button type="button" className="row-action-button mailbox-code-button" disabled={mailbox.status !== "ACTIVE"} title={mailbox.status === "ACTIVE" ? "读取最近邮件中的验证码" : "邮箱状态正常后可查看验证码"} onClick={() => onViewCode(mailbox)}><KeyRound size={14} />查看验证码</button><button className="row-action-button" onClick={() => void onRevalidate(mailbox)}><RefreshCcw size={14} />重新校验</button><IconButton className="table-icon-action" label={mailbox.status === "MANUAL_DISABLED" ? "恢复邮箱" : "停用邮箱"} onClick={() => void onToggle(mailbox)}><Power size={15} /></IconButton><IconButton className="table-icon-action table-icon-action--danger" label="删除邮箱" onClick={() => void onDelete(mailbox)}><Trash2 size={15} /></IconButton></div></td></tr>)}</tbody></table></div>}
        {!loading && total > 0 && <div className="pagination account-pagination"><span>{page * 50 + 1}—{Math.min((page + 1) * 50, total)} / {formatNumber(total)}</span><div><IconButton label="上一页" disabled={page === 0} onClick={() => onPage(Math.max(0, page - 1))}><ChevronLeft size={18} /></IconButton><span>第 {page + 1} / {pageCount} 页</span><IconButton label="下一页" disabled={page >= pageCount - 1} onClick={() => onPage(Math.min(pageCount - 1, page + 1))}><ChevronRight size={18} /></IconButton></div></div>}
      </section>
    </div>
  );
}

type ParentAccountsViewProps = {
  parentAccounts: ParentAccount[];
  stats: ParentAccountStats | null;
  total: number;
  page: number;
  search: string;
  loading: boolean;
  onPage: (page: number) => void;
  onSearch: (value: string) => void;
  onImport: () => void;
  onCopyPassword: (value: string) => void;
  onCopyInviteUrl: (value: string) => void;
  onOpenRegistrations: (parentAccount: ParentAccount) => void;
  onDelete: (parentAccount: ParentAccount) => Promise<void>;
};

export function ParentAccountsView({ parentAccounts, stats, total, page, search, loading, onPage, onSearch, onImport, onCopyPassword, onCopyInviteUrl, onOpenRegistrations, onDelete }: ParentAccountsViewProps) {
  const pageCount = Math.max(1, Math.ceil(total / 50));
  const cards = [
    { label: "母号总数", code: "PARENT ACCOUNTS", value: stats?.total_parent_accounts ?? 0, tone: "total", icon: Database },
    { label: "邀请成功次数", code: "INVITE SUCCEEDED", value: stats?.total_invite_successes ?? 0, tone: "success", icon: CheckCircle2 },
    { label: "注册成功", code: "VALIDATED SUCCESS", value: stats?.total_invite_successes ?? 0, tone: "success", icon: CheckCircle2 },
    { label: "母号已耗尽", code: "EXHAUSTED", value: stats?.exhausted_parent_accounts ?? 0, tone: "failure", icon: AlertTriangle },
  ] as const;
  return (
    <div className="page page--parent-accounts">
      <div className="page-title reveal"><div><span className="eyebrow">PARENT ACCOUNT POOL / 邀请资源</span><h1>母号池</h1><p>独立管理邀请母号凭据、邀请链接及成功与失败累计次数。</p></div><div className="page-title__actions"><button className="primary-button" onClick={onImport}><Upload size={17} />批量导入母号</button></div></div>
      <section className="parent-account-stats reveal reveal--delay-1">
        {cards.map((card) => { const Icon = card.icon; return <article key={card.label} className={cn("parent-account-stat", `parent-account-stat--${card.tone}`)}><div><Icon size={17} /><span>{card.label}</span></div><strong>{formatNumber(card.value)}</strong><small>{card.code}</small></article>; })}
      </section>
      <section className="panel table-panel reveal reveal--delay-2">
        <div className="table-toolbar"><div className="search-box"><Search size={17} /><input value={search} onChange={(event) => onSearch(event.target.value)} placeholder="搜索母号邮箱…" /></div><span className="active-filter-label">全部母号</span><span className="result-count">匹配 {formatNumber(total)} 个母号</span></div>
        {loading ? <SkeletonRows /> : parentAccounts.length === 0 ? <EmptyState icon={KeyRound} title="没有匹配母号" description="调整搜索条件，或批量导入新的母号凭据。" /> : <div className="data-table-wrap"><table className="data-table parent-account-table"><thead><tr><th>邮箱</th><th>密码</th><th>状态</th><th>邀请链接</th><th>成功 / 失败</th><th>连续低于8000</th><th>运行中</th><th>创建时间</th><th>操作</th></tr></thead><tbody>{parentAccounts.map((parentAccount) => <tr key={parentAccount.parent_account_uuid}><td><div className="identity-cell"><span className="account-avatar"><KeyRound size={16} /></span><div><strong>{parentAccount.email}</strong><small>{shortId(parentAccount.parent_account_uuid, 18)}</small></div></div></td><td><button type="button" className="parent-account-secret" title={parentAccount.password} aria-label={`复制密码 ${parentAccount.email}`} onClick={() => onCopyPassword(parentAccount.password)}><span>{parentAccount.password}</span><Copy size={14} /></button></td><td><StatusBadge status={parentAccount.status} /></td><td><button type="button" className="parent-account-invite-link" title={parentAccount.invite_url} aria-label={`复制邀请链接 ${parentAccount.email}`} onClick={() => onCopyInviteUrl(parentAccount.invite_url)}><span>{parentAccount.invite_url}</span><Copy size={14} /></button></td><td><strong className="parent-account-count parent-account-count--success">{formatNumber(parentAccount.invite_success_count)}</strong> / <strong className="parent-account-count parent-account-count--failure">{formatNumber(parentAccount.invite_failure_count)}</strong></td><td><strong>{parentAccount.consecutive_150_count} / 3</strong></td><td><strong>{parentAccount.running_registration_count}</strong></td><td><div className="date-cell"><Clock3 size={14} /><div><strong>{formatDate(parentAccount.created_at)}</strong><span>{relativeTime(parentAccount.created_at)}</span></div></div></td><td><div className="row-actions"><button type="button" className="row-action-button" onClick={() => onOpenRegistrations(parentAccount)}><List size={14} />注册记录</button><IconButton className="table-icon-action table-icon-action--danger" label="删除母号" disabled={parentAccount.traceable_registration_count > 0} onClick={() => void onDelete(parentAccount)}><Trash2 size={15} /></IconButton></div></td></tr>)}</tbody></table></div>}
        {!loading && total > 0 && <div className="pagination account-pagination"><span>{page * 50 + 1}—{Math.min((page + 1) * 50, total)} / {formatNumber(total)}</span><div><IconButton label="上一页" disabled={page === 0} onClick={() => onPage(Math.max(0, page - 1))}><ChevronLeft size={18} /></IconButton><span>第 {page + 1} / {pageCount} 页</span><IconButton label="下一页" disabled={page >= pageCount - 1} onClick={() => onPage(Math.min(pageCount - 1, page + 1))}><ChevronRight size={18} /></IconButton></div></div>}
      </section>
    </div>
  );
}

type SuccessfulAccountsViewProps = {
  accounts: RegistrationRecord[];
  total: number;
  page: number;
  pageSize: number;
  search: string;
  usage: string;
  credits: string;
  unused8500Count: number;
  loading: boolean;
  selectedRegistrationUuids: Set<string>;
  exporting: boolean;
  onPage: (page: number) => void;
  onPageSize: (pageSize: number) => void;
  onSearch: (value: string) => void;
  onUsage: (value: string) => void;
  onCredits: (value: string) => void;
  onQuickUnused8500: () => void;
  onSelectionChange: (registrationUuids: Set<string>) => void;
  onExport: (registrationUuids: string[]) => void;
};

function successfulAccountEmail(account: RegistrationRecord): string {
  return account.verified_email ?? account.registered_email ?? account.email;
}

function successfulRegistrationTime(account: RegistrationRecord): string {
  return account.validation_finished_at ?? account.reported_at ?? account.started_at;
}

export function SuccessfulAccountsView({ accounts, total, page, pageSize, search, usage, credits, unused8500Count, loading, selectedRegistrationUuids, exporting, onPage, onPageSize, onSearch, onUsage, onCredits, onQuickUnused8500, onSelectionChange, onExport }: SuccessfulAccountsViewProps) {
  const pageCount = Math.max(1, Math.ceil(total / pageSize));
  const usesPresetPageSize = SUCCESSFUL_ACCOUNT_PAGE_SIZE_OPTIONS.some((option) => option === pageSize);
  const [customPageSizeOpen, setCustomPageSizeOpen] = useState(!usesPresetPageSize);
  const [customPageSizeDraft, setCustomPageSizeDraft] = useState(String(pageSize));
  const [pageJumpDraft, setPageJumpDraft] = useState(String(page + 1));
  const customPageSize = Number(customPageSizeDraft);
  const customPageSizeIsValid = Number.isInteger(customPageSize)
    && customPageSize >= SUCCESSFUL_ACCOUNT_PAGE_SIZE_MIN
    && customPageSize <= SUCCESSFUL_ACCOUNT_PAGE_SIZE_MAX;
  useEffect(() => {
    setCustomPageSizeDraft(String(pageSize));
    setCustomPageSizeOpen(!SUCCESSFUL_ACCOUNT_PAGE_SIZE_OPTIONS.some((option) => option === pageSize));
  }, [pageSize]);
  useEffect(() => {
    setPageJumpDraft(String(page + 1));
  }, [page, pageCount]);
  const pagePoints = accounts.reduce((sum, account) => sum + (account.awarded_points ?? 0), 0);
  const pageUsed = accounts.filter((account) => account.is_used).length;
  const availableRegistrationUuids = accounts.filter((account) => !account.is_used).map((account) => account.registration_uuid);
  const selectedUuids = availableRegistrationUuids.filter((registrationUuid) => selectedRegistrationUuids.has(registrationUuid));
  const allAvailableSelected = availableRegistrationUuids.length > 0 && selectedUuids.length === availableRegistrationUuids.length;
  const someAvailableSelected = selectedUuids.length > 0 && !allAvailableSelected;
  const usageOptions = [
    { value: "", label: "全部", code: "ALL" },
    { value: "unused", label: "未使用", code: "AVAILABLE" },
    { value: "used", label: "已使用", code: "USED" },
  ];
  const creditOptions = [
    { value: "", label: "全部积分", code: "ALL CREDITS" },
    { value: "150", label: "150 积分", code: "CREDITS 150" },
    { value: "8500", label: "8,500 积分", code: "CREDITS 8500" },
  ];
  const applyCustomPageSize = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!customPageSizeIsValid) return;
    onPageSize(customPageSize);
  };
  const applyPageJump = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const nextPage = parseSuccessfulAccountPageJump(pageJumpDraft, pageCount);
    if (nextPage === null) {
      setPageJumpDraft(String(page + 1));
      return;
    }
    setPageJumpDraft(String(nextPage + 1));
    onPage(nextPage);
  };
  const toggleAllAvailable = () => {
    const next = new Set(selectedRegistrationUuids);
    availableRegistrationUuids.forEach((registrationUuid) => allAvailableSelected ? next.delete(registrationUuid) : next.add(registrationUuid));
    onSelectionChange(next);
  };
  const toggleAccount = (registrationUuid: string) => {
    const next = new Set(selectedRegistrationUuids);
    next.has(registrationUuid) ? next.delete(registrationUuid) : next.add(registrationUuid);
    onSelectionChange(next);
  };
  return (
    <div className="page page--successful-accounts">
      <div className="page-title reveal">
        <div><span className="eyebrow">SUCCESSFUL REGISTRATIONS / 成功流水</span><h1>注册成功账号</h1><p>集中查看已通过服务端校验的账号、积分、注册时间、归属母号与使用状态。</p></div>
        <div className="successful-account-ledger-mark"><CheckCircle2 size={20} /><div><span>匹配账号</span><strong>{formatNumber(total)}</strong></div></div>
      </div>
      <section className="successful-account-summary reveal reveal--delay-1" aria-label="当前成功账号摘要">
        <article><span>当前匹配</span><strong>{formatNumber(total)}</strong><small>SUCCESSFUL ACCOUNTS</small></article>
        <article><span>本页积分</span><strong>{formatNumber(pagePoints)}</strong><small>PAGE CREDITS</small></article>
        <article><span>本页已使用</span><strong>{formatNumber(pageUsed)}</strong><small>USED ON THIS PAGE</small></article>
        <button type="button" className={cn("successful-account-summary-quick", usage === "unused" && credits === "8500" && "is-active")} onClick={onQuickUnused8500}><span>未使用 · 8,500</span><strong>{formatNumber(unused8500Count)}</strong><small>AVAILABLE 8500 · 点击筛选</small></button>
      </section>
      <section className="panel table-panel reveal reveal--delay-2">
        <div className="table-toolbar successful-account-toolbar">
          <div className="search-box"><Search size={17} /><input value={search} onChange={(event) => onSearch(event.target.value)} placeholder="搜索账号或归属母号…" /></div>
          <div className="successful-account-filters" aria-label="使用状态筛选">
            {usageOptions.map((option) => <button key={option.code} type="button" className={cn(usage === option.value && "is-active")} aria-pressed={usage === option.value} onClick={() => onUsage(option.value)}><span>{option.label}</span><small>{option.code}</small></button>)}
          </div>
          <div className="successful-account-filters successful-account-credit-filters" aria-label="积分筛选">
            {creditOptions.map((option) => <button key={option.code} type="button" className={cn(credits === option.value && "is-active")} aria-pressed={credits === option.value} onClick={() => onCredits(option.value)}><span>{option.label}</span><small>{option.code}</small></button>)}
          </div>
          <span className="result-count">匹配 {formatNumber(total)} 个成功账号</span>
        </div>
        {selectedUuids.length > 0 && <div className="account-bulk-bar successful-account-bulk-bar" aria-live="polite"><div><strong>已选择 {selectedUuids.length} 个未使用账号</strong><span>导出 Cookie JSON、邮箱及登录链接压缩包</span></div><div><button className="primary-button" type="button" onClick={() => onExport(selectedUuids)} disabled={exporting}>{exporting ? <RefreshCcw className="spin" size={16} /> : <Download size={16} />}{exporting ? "正在导出…" : `导出选中（${selectedUuids.length}）`}</button><button className="text-button" type="button" onClick={() => onSelectionChange(new Set())} disabled={exporting}>取消选择</button></div></div>}
        {loading ? <SkeletonRows /> : accounts.length === 0 ? <EmptyState icon={CheckCircle2} title="没有匹配的成功账号" description="调整账号、母号、使用状态或积分筛选条件。" /> : <div className="data-table-wrap"><table className="data-table successful-account-table"><thead><tr><th className="selection-cell"><input type="checkbox" aria-label="选择本页全部未使用账号" checked={allAvailableSelected} disabled={availableRegistrationUuids.length === 0 || exporting} ref={(element) => { if (element) element.indeterminate = someAvailableSelected; }} onChange={toggleAllAvailable} /></th><th>账号</th><th>积分</th><th>注册时间</th><th>归属母号</th><th>是否已使用</th></tr></thead><tbody>{accounts.map((account) => { const registeredAt = successfulRegistrationTime(account); const selected = selectedRegistrationUuids.has(account.registration_uuid); return <tr key={account.registration_uuid} className={selected ? "is-selected" : undefined}><td className="selection-cell"><input type="checkbox" aria-label={`选择账号 ${successfulAccountEmail(account)}`} checked={selected} disabled={account.is_used || exporting} onChange={() => toggleAccount(account.registration_uuid)} /></td><td><div className="identity-cell"><span className="account-avatar successful-account-avatar"><CheckCircle2 size={16} /></span><div><strong>{successfulAccountEmail(account)}</strong><small>{shortId(account.registration_uuid, 18)}</small></div></div></td><td><div className="successful-account-points"><CircleDollarSign size={15} /><strong>{account.awarded_points === null ? "—" : formatNumber(account.awarded_points)}</strong></div></td><td><div className="date-cell"><Clock3 size={14} /><div><strong>{formatDate(registeredAt)}</strong><span>{relativeTime(registeredAt)}</span></div></div></td><td><div className="identity-cell successful-account-parent"><span className="account-avatar"><KeyRound size={15} /></span><div><strong>{account.parent_email}</strong><small>{shortId(account.parent_account_uuid, 18)}</small></div></div></td><td><span className={cn("usage-badge", account.is_used ? "is-used" : "is-available")}><i />{account.is_used ? "已使用" : "未使用"}</span></td></tr>; })}</tbody></table></div>}
        {!loading && total > 0 && (
          <div className="pagination account-pagination successful-account-pagination">
            <span>{page * pageSize + 1}—{Math.min((page + 1) * pageSize, total)} / {formatNumber(total)}</span>
            <div>
              <label className="page-size-control">
                <span>每页</span>
                <select
                  aria-label="成功账号每页数量"
                  value={customPageSizeOpen ? "custom" : String(pageSize)}
                  onChange={(event) => {
                    if (event.target.value === "custom") {
                      setCustomPageSizeOpen(true);
                      setCustomPageSizeDraft(String(pageSize));
                      return;
                    }
                    setCustomPageSizeOpen(false);
                    onPageSize(Number(event.target.value));
                  }}
                >
                  {SUCCESSFUL_ACCOUNT_PAGE_SIZE_OPTIONS.map((value) => <option key={value} value={value}>{value}</option>)}
                  <option value="custom">自定义</option>
                </select>
                <span>条</span>
              </label>
              {customPageSizeOpen && (
                <form className="custom-page-size-control" onSubmit={applyCustomPageSize}>
                  <input
                    aria-label="自定义成功账号每页数量"
                    type="number"
                    min={SUCCESSFUL_ACCOUNT_PAGE_SIZE_MIN}
                    max={SUCCESSFUL_ACCOUNT_PAGE_SIZE_MAX}
                    step="1"
                    value={customPageSizeDraft}
                    onChange={(event) => setCustomPageSizeDraft(event.target.value)}
                  />
                  <button type="submit" disabled={!customPageSizeIsValid}>应用</button>
                  <small>1–500</small>
                </form>
              )}
              <form className="page-jump-control" onSubmit={applyPageJump}>
                <label htmlFor="successful-account-page-jump">跳转</label>
                <input
                  id="successful-account-page-jump"
                  aria-label="跳转到成功账号页码"
                  type="number"
                  min="1"
                  max={pageCount}
                  step="1"
                  inputMode="numeric"
                  value={pageJumpDraft}
                  onChange={(event) => setPageJumpDraft(event.target.value)}
                />
                <span>/ {pageCount}</span>
                <button type="submit">前往</button>
              </form>
              <IconButton label="第一页" disabled={page === 0} onClick={() => onPage(0)}><ChevronsLeft size={18} /></IconButton>
              <IconButton label="上一页" disabled={page === 0} onClick={() => onPage(Math.max(0, page - 1))}><ChevronLeft size={18} /></IconButton>
              <span>第 {page + 1} / {pageCount} 页</span>
              <IconButton label="下一页" disabled={page >= pageCount - 1} onClick={() => onPage(Math.min(pageCount - 1, page + 1))}><ChevronRight size={18} /></IconButton>
              <IconButton label="最后一页" disabled={page >= pageCount - 1} onClick={() => onPage(pageCount - 1)}><ChevronsRight size={18} /></IconButton>
            </div>
          </div>
        )}
      </section>
    </div>
  );
}

type AccountsViewProps = {
  accounts: Account[];
  allAccounts: Account[];
  activeCreditTotal?: number;
  activeCreditTarget?: number;
  spacesById: Record<string, Space>;
  loading: boolean;
  search: string;
  status: string;
  renewalStats: ProtocolRenewalStats | null;
  renewalAccounts: Record<string, ProtocolRenewalAccount>;
  renewalPeriod: ProtocolRenewalPeriod;
  renewalStatus: string;
  renewalError: string;
  selectedAccountUuids: Set<string>;
  accountExporting: boolean;
  onSelectionChange: (accountUuids: Set<string>) => void;
  onSearch: (value: string) => void;
  onStatus: (value: string) => void;
  onRenewalPeriod: (value: ProtocolRenewalPeriod) => void;
  onRenewalStatus: (value: string) => void;
  onRenewalAccount: (account: ProtocolRenewalAccount) => void;
  onAdd: () => void;
  onBulkImport: () => void;
  onCookieImport: () => void;
  onExportSelected: (accountUuids: string[]) => void;
  onExportAndDelete: (accountUuids: string[]) => void;
  onToken: (account: Account) => void;
  onEdit: (account: Account) => void;
  onDelete: (account: Account) => void;
  onBalance: (account: Account) => void;
  balanceRefreshing: Set<string>;
  onToggle: (account: Account) => void;
  onCopy: (value: string) => void;
};

function AccountsView({ accounts, allAccounts, activeCreditTotal, activeCreditTarget, spacesById, loading, search, status, renewalStats, renewalAccounts, renewalPeriod, renewalStatus, renewalError, selectedAccountUuids, accountExporting, onSelectionChange, onSearch, onStatus, onRenewalPeriod, onRenewalStatus, onRenewalAccount, onAdd, onBulkImport, onCookieImport, onExportSelected, onExportAndDelete, onToken, onEdit, onDelete, onBalance, balanceRefreshing, onToggle, onCopy }: AccountsViewProps) {
  const activeBalance = activeCreditTotal ?? allAccounts
    .filter((account) => account.status === "ACTIVE")
    .reduce((sum, account) => sum + account.balance_credits, 0);
  const creditTarget = activeCreditTarget ?? 1_000_000;
  const belowCreditWatermark = activeBalance < creditTarget;
  const capacity = allAccounts.reduce((sum, account) => sum + account.max_concurrency, 0);
  const statusCounts = allAccounts.reduce<Record<string, number>>((counts, account) => {
    counts[account.status] = (counts[account.status] ?? 0) + 1;
    return counts;
  }, {});
  const knownStatuses = new Set<string>(ACCOUNT_STATUS_OPTIONS.map((option) => option.value));
  const statusOptions = [
    ...ACCOUNT_STATUS_OPTIONS,
    ...Object.keys(statusCounts)
      .filter((accountStatus) => !knownStatuses.has(accountStatus))
      .sort()
      .map((accountStatus) => ({
        value: accountStatus,
        label: accountStatus.replaceAll("_", " "),
        code: "OTHER STATUS",
      })),
  ];
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState<(typeof ACCOUNT_PAGE_SIZE_OPTIONS)[number]>(ACCOUNT_PAGE_SIZE_OPTIONS[0]);
  const pageCount = Math.max(1, Math.ceil(accounts.length / pageSize));
  const currentPage = Math.min(page, pageCount - 1);
  const pageStart = currentPage * pageSize;
  const pageEnd = Math.min(pageStart + pageSize, accounts.length);
  const pagedAccounts = accounts.slice(pageStart, pageEnd);
  const pageAccountUuids = pagedAccounts.map((account) => account.account_uuid);
  const selectedAccountList = allAccounts.filter((account) => selectedAccountUuids.has(account.account_uuid));
  const selectedUuids = selectedAccountList.map((account) => account.account_uuid);
  const selectedPageCount = pageAccountUuids.filter((accountUuid) => selectedAccountUuids.has(accountUuid)).length;
  const allPageSelected = pageAccountUuids.length > 0 && selectedPageCount === pageAccountUuids.length;
  const somePageSelected = selectedPageCount > 0 && !allPageSelected;
  const allMatchingSelected = accounts.length > 0 && accounts.every((account) => selectedAccountUuids.has(account.account_uuid));

  useEffect(() => setPage(0), [search, status]);
  useEffect(() => setPage((value) => Math.min(value, pageCount - 1)), [pageCount]);

  const togglePage = () => {
    const next = new Set(selectedAccountUuids);
    pageAccountUuids.forEach((accountUuid) => allPageSelected ? next.delete(accountUuid) : next.add(accountUuid));
    onSelectionChange(next);
  };

  const toggleAccountSelection = (accountUuid: string) => {
    const next = new Set(selectedAccountUuids);
    next.has(accountUuid) ? next.delete(accountUuid) : next.add(accountUuid);
    onSelectionChange(next);
  };

  const selectAllMatching = () => {
    const next = new Set(selectedAccountUuids);
    accounts.forEach((account) => next.add(account.account_uuid));
    onSelectionChange(next);
  };

  return (
    <div className="page">
      <div className="page-title reveal"><div><span className="eyebrow">ACCOUNT POOL / 调度资源</span><h1>账号池</h1><p>集中查看登录状态、Token 生命周期、积分水位与实时并发占用。</p></div><div className="page-title__actions"><button className="secondary-button" onClick={onBulkImport}><Upload size={17} />批量导入</button><button className="secondary-button" onClick={onCookieImport}><FileArchive size={17} />导入 Cookie ZIP</button><button className="primary-button" onClick={onAdd}><Plus size={18} />添加账号</button></div></div>
      <section className="summary-strip reveal reveal--delay-1">
        <div><span>账号总数</span><strong>{formatNumber(allAccounts.length)}</strong><UsersRound size={19} /></div>
        <div><span>有效账号</span><strong>{formatNumber(allAccounts.filter((account) => account.status === "ACTIVE").length)}</strong><ShieldCheck size={19} /></div>
        <div className={belowCreditWatermark ? "is-below-watermark" : undefined}><span>ACTIVE 积分 · 水位 {formatNumber(creditTarget)}</span><strong>{formatNumber(activeBalance)}</strong><CircleDollarSign size={19} /></div>
        <div><span>总并发位</span><strong>{formatNumber(capacity)}</strong><Cpu size={19} /></div>
      </section>
      <section className="account-status-overview reveal reveal--delay-2" aria-labelledby="account-status-overview-title">
        <div className="account-status-overview__heading">
          <div><span>STATUS DISTRIBUTION</span><strong id="account-status-overview-title">账号状态统计</strong></div>
          <small>点击状态即可筛选，再次点击当前状态返回全部</small>
        </div>
        <div className="account-status-overview__list" role="group" aria-label="按账号状态筛选">
          {statusOptions.map((option) => {
            const selected = status === option.value;
            const count = option.value ? statusCounts[option.value] ?? 0 : allAccounts.length;
            const tone = option.value ? statusTone(option.value) : "all";
            return (
              <button
                key={option.value}
                type="button"
                className={cn("account-status-card", `account-status-card--${tone}`, selected && "is-active")}
                aria-pressed={selected}
                onClick={() => onStatus(selected && option.value ? "" : option.value)}
              >
                <span><i />{option.label}</span>
                <strong>{formatNumber(count)}</strong>
                <small>{option.code}</small>
              </button>
            );
          })}
        </div>
      </section>
      <ProtocolRenewalOverview
        stats={renewalStats}
        period={renewalPeriod}
        activeStatus={renewalStatus}
        error={renewalError}
        onPeriod={onRenewalPeriod}
        onStatus={onRenewalStatus}
      />
      <section className="panel table-panel reveal reveal--delay-2">
        <div className="table-toolbar">
          <div className="search-box"><Search size={17} /><input value={search} onChange={(event) => onSearch(event.target.value)} placeholder="搜索邮箱或账号 UUID…" /><kbd>⌘ K</kbd></div>
          <span className="active-filter-label">{status ? statusOptions.find((option) => option.value === status)?.label ?? status : "全部状态"}</span>
          {renewalStatus && <button className="active-filter-label active-filter-label--button" onClick={() => onRenewalStatus("")}>续签：{PROTOCOL_RENEWAL_STATUS_LABELS[renewalStatus] ?? renewalStatus}<X size={12} /></button>}
          <span className="result-count" aria-live="polite">匹配 {formatNumber(accounts.length)} / {formatNumber(allAccounts.length)}</span>
        </div>
        {selectedUuids.length > 0 && (
          <div className="account-bulk-bar" aria-live="polite">
            <div><strong>已选择 {selectedUuids.length} 个账号</strong>{!allMatchingSelected && accounts.length > selectedUuids.length && <button type="button" onClick={selectAllMatching}>选择全部 {accounts.length} 条匹配结果</button>}</div>
            <div>
              <button className="secondary-button" type="button" onClick={() => onExportSelected(selectedUuids)} disabled={accountExporting}>{accountExporting ? <RefreshCcw className="spin" size={16} /> : <Download size={16} />}{accountExporting ? "正在导出…" : "导出选中"}</button>
              <button className="danger-button" type="button" onClick={() => onExportAndDelete(selectedUuids)} disabled={accountExporting}><Trash2 size={16} />导出并删除选中</button>
              <button className="text-button" type="button" onClick={() => onSelectionChange(new Set())}>取消选择</button>
            </div>
          </div>
        )}
        {loading ? <SkeletonRows /> : accounts.length === 0 ? <EmptyState icon={UsersRound} title="没有匹配账号" description="调整搜索条件，或添加新的账号资源。" /> : <div className="data-table-wrap"><table className="data-table account-table"><thead><tr><th className="selection-cell"><input type="checkbox" aria-label="选择当前页账号" checked={allPageSelected} ref={(element) => { if (element) element.indeterminate = somePageSelected; }} onChange={togglePage} /></th><th>账号 / 空间</th><th>账号来源</th><th>状态</th><th className="created-at-column">创建时间</th><th>积分水位</th><th>并发</th><th>Token 有效期</th><th>协议续签</th><th>任务表现</th><th>操作</th></tr></thead><tbody>{pagedAccounts.map((account) => {
          const available = Math.max(0, account.balance_credits - account.reserved_credits);
          const usage = account.max_concurrency ? (account.active_tasks / account.max_concurrency) * 100 : 0;
          const syncingBalance = balanceRefreshing.has(account.account_uuid);
          const selected = selectedAccountUuids.has(account.account_uuid);
          const renewal = renewalAccounts[account.account_uuid];
          return <tr key={account.account_uuid} className={selected ? "is-selected" : undefined}><td className="selection-cell"><input type="checkbox" aria-label={`选择账号 ${accountLoginName(account)}`} checked={selected} onChange={() => toggleAccountSelection(account.account_uuid)} /></td><td><div className="identity-cell"><span className="account-avatar">{accountLoginName(account).slice(0, 1).toUpperCase()}</span><div><strong>{accountLoginName(account)}</strong><button onClick={() => onCopy(account.account_uuid)}>{shortId(account.account_uuid, 10)} <Copy size={11} /></button><small>{spacesById[account.space_uuid]?.name ?? shortId(account.space_uuid)}</small></div></div></td><td><span className={cn("account-source-badge", `account-source-badge--${account.label ?? "unlabeled"}`)}><i />{accountSourceLabel(account.label)}</span></td><td><StatusBadge status={account.status} pulse={account.status === "ACTIVE"} />{account.disabled_reason && <small className="cell-note">{account.disabled_reason}</small>}</td><td className="created-at-column"><div className="date-cell"><Clock3 size={14} /><div><strong>{formatDate(account.created_at)}</strong><span>{relativeTime(account.created_at)}</span></div></div></td><td><div className="credit-cell"><strong>{formatNumber(available)}</strong><span>可用 / {formatNumber(account.balance_credits)}</span><div><i style={{ width: `${Math.min((available / Math.max(account.balance_credits, 1)) * 100, 100)}%` }} /></div><small>{account.balance_synced_at ? `同步于 ${relativeTime(account.balance_synced_at)}` : "等待首次同步"}</small></div></td><td><div className="concurrency-cell"><strong>{account.active_tasks}<i>/</i>{account.max_concurrency}</strong><div><i style={{ width: `${usage}%` }} /></div></div></td><td><div className="date-cell"><Clock3 size={14} /><div><strong>{formatDate(account.token_expires_at, true)}</strong><span>{relativeTime(account.token_expires_at)}</span></div></div></td><td><RenewalAccountCell renewal={renewal} onClick={() => renewal && onRenewalAccount(renewal)} /></td><td><div className="performance-cell"><span><i className="ok" />{formatNumber(account.completed_tasks)} 成功</span><span><i className="bad" />{formatNumber(account.failed_tasks)} 失败</span></div></td><td><div className="row-actions"><button className="row-action-button row-action-button--balance" onClick={() => onBalance(account)} disabled={!account.token_configured || syncingBalance}>{syncingBalance ? <RefreshCcw className="spin" size={14} /> : <CircleDollarSign size={14} />}获取积分</button><button className="row-action-button" onClick={() => onToken(account)}><KeyRound size={14} />Token</button><IconButton className="table-icon-action" label="编辑账号" onClick={() => onEdit(account)}><Pencil size={15} /></IconButton><IconButton className="table-icon-action" label={account.status === "MANUAL_DISABLED" ? "重新启用" : "停用账号"} onClick={() => onToggle(account)}><Power size={15} /></IconButton><IconButton className="table-icon-action table-icon-action--danger" label="删除账号" onClick={() => onDelete(account)} disabled={account.active_tasks > 0}><Trash2 size={15} /></IconButton></div></td></tr>;
        })}</tbody></table></div>}
        {!loading && accounts.length > 0 && (
          <div className="pagination account-pagination">
            <span>{pageStart + 1}—{pageEnd} / {formatNumber(accounts.length)}{accounts.length !== allAccounts.length ? `（全部 ${formatNumber(allAccounts.length)}）` : ""}</span>
            <div>
              <label className="page-size-control"><span>每页</span><select aria-label="账号每页数量" value={pageSize} onChange={(event) => { setPageSize(Number(event.target.value) as (typeof ACCOUNT_PAGE_SIZE_OPTIONS)[number]); setPage(0); }}>{ACCOUNT_PAGE_SIZE_OPTIONS.map((value) => <option key={value} value={value}>{value}</option>)}</select><span>条</span></label>
              <IconButton label="上一页" disabled={currentPage === 0} onClick={() => setPage(Math.max(0, currentPage - 1))}><ChevronLeft size={18} /></IconButton>
              <span>第 {currentPage + 1} / {pageCount} 页</span>
              <IconButton label="下一页" disabled={currentPage >= pageCount - 1} onClick={() => setPage(Math.min(pageCount - 1, currentPage + 1))}><ChevronRight size={18} /></IconButton>
            </div>
          </div>
        )}
      </section>
    </div>
  );
}

function ProtocolRenewalModal({ account, api, onClose }: { account: ProtocolRenewalAccount; api: VideoTaskApi; onClose: () => void }) {
  const [events, setEvents] = useState<ProtocolRenewalEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    setLoading(true);
    api.getProtocolRenewalEvents(account.account_uuid).then((result) => {
      if (!active) return;
      setEvents(result.items);
      setError("");
    }).catch((cause: unknown) => {
      if (!active) return;
      setError(cause instanceof Error ? cause.message : "续签事件读取异常");
    }).finally(() => active && setLoading(false));
    return () => { active = false; };
  }, [account.account_uuid, api]);
  const extension = account.previous_token_expires_at && account.renewed_token_expires_at
    ? Math.max((new Date(account.renewed_token_expires_at).getTime() - new Date(account.previous_token_expires_at).getTime()) / 1000, 0)
    : null;
  return (
    <Modal title="协议续签详情" eyebrow="PROTOCOL RENEWAL" onClose={onClose} wide>
      <div className="renewal-detail-head">
        <div><strong>{account.login_name}</strong><span>{shortId(account.account_uuid, 16)}</span></div>
        <span className={cn("protocol-health", `protocol-health--${protocolRenewalTone(account.status)}`)}><i />{PROTOCOL_RENEWAL_STATUS_LABELS[account.status] ?? account.status}</span>
      </div>
      <div className="renewal-detail-grid">
        <div><span>最近尝试</span><strong>{formatDate(account.last_attempt_at, true)}</strong><small>{relativeTime(account.last_attempt_at)}</small></div>
        <div><span>最近成功</span><strong>{formatDate(account.last_success_at, true)}</strong><small>{relativeTime(account.last_success_at)}</small></div>
        <div><span>尝试次数</span><strong>{account.attempt_count}</strong><small>当前续签周期</small></div>
        <div><span>Token 延长</span><strong>{durationLabel(extension)}</strong><small>{formatDate(account.renewed_token_expires_at, true)}</small></div>
        <div><span>下次重试</span><strong>{formatDate(account.retry_after, true)}</strong><small>{relativeTime(account.retry_after)}</small></div>
        <div><span>回退截止</span><strong>{formatDate(account.fallback_after, true)}</strong><small>{relativeTime(account.fallback_after)}</small></div>
        <div><span>客户端会话上报</span><strong>{formatDate(account.client_reported_at, true)}</strong><small>{account.client_session_fresh ? "会话年龄正常" : "已进入提前轮换"}</small></div>
        <div><span>客户端能力</span><strong>{account.client_version ?? "旧版/未知"}</strong><small>{account.renewal_capability ?? "未声明续签能力"}</small></div>
      </div>
      {account.last_error_code && <div className="renewal-detail-error"><AlertTriangle size={15} /><span><strong>最近错误</strong>{account.last_error_code}</span></div>}
      <div className="renewal-events">
        <div className="protocol-subheading"><strong>最近事件</strong><span>最多 20 条</span></div>
        {loading ? <div className="protocol-empty">正在读取续签事件…</div> : error ? <div className="protocol-renewal__error"><AlertTriangle size={15} />{error}</div> : events.length === 0 ? <div className="protocol-empty">该账号暂无续签事件。</div> : <div className="data-table-wrap"><table className="data-table"><thead><tr><th>完成时间</th><th>结果</th><th>落库</th><th>耗时</th><th>下一状态</th><th>错误码</th></tr></thead><tbody>{events.map((event) => <tr key={event.event_uuid}><td>{formatDate(event.finished_at, true)}</td><td><span className={cn("renewal-event-outcome", event.applied ? "is-ok" : "is-bad")}>{event.outcome}</span></td><td>{event.applied ? "已应用" : "未应用"}</td><td>{event.latency_ms} ms</td><td>{event.next_state}</td><td>{event.error_code ?? "—"}</td></tr>)}</tbody></table></div>}
      </div>
    </Modal>
  );
}

function TasksView({ tasks, total, page, status, model, models, search, loading, onPage, onStatus, onModel, onSearch, onSelect, onCopy }: { tasks: Task[]; total: number; page: number; status: string; model: string; models: string[]; search: string; loading: boolean; onPage: (page: number) => void; onStatus: (status: string) => void; onModel: (model: string) => void; onSearch: (value: string) => void; onSelect: (task: Task) => void; onCopy: (value: string) => void }) {
  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const hasRunningTasks = tasks.some(taskIsLive);
  const modelOptions = model && !models.includes(model) ? [model, ...models] : models;
  const [now, setNow] = useState(() => Date.now());
  const [layout, setLayout] = useState<"grid" | "list">("grid");
  const [previewTask, setPreviewTask] = useState<Task | null>(null);

  useEffect(() => {
    if (!hasRunningTasks) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [hasRunningTasks]);

  return (
    <div className="page">
      <div className="page-title reveal"><div><span className="eyebrow">TASK LEDGER / 全链路记录</span><h1>任务中心</h1><p>跟踪每次生成的输入、调度账号、上游任务、积分结算与异常信息。</p></div><div className="headline-stat"><span>历史任务</span><strong>{formatNumber(total)}</strong></div></div>
      <section className="status-tabs reveal reveal--delay-1">{TASK_STATUS_FILTERS.map((item) => <button key={item} className={status === item ? "is-active" : ""} onClick={() => onStatus(item)}><span>{item ? item.replaceAll("_", " ") : "全部任务"}</span>{status === item && <i />}</button>)}</section>
      <section className="task-browser reveal reveal--delay-2">
        <div className="panel task-browser__toolbar">
          <div className="search-box"><Search size={17} /><input value={search} onChange={(event) => onSearch(event.target.value)} placeholder="搜索任务 ID、模型或提示词…" /></div>
          <label className={cn("task-model-filter", model && "is-active")}>
            <Cpu size={16} />
            <span>模型</span>
            <select aria-label="按模型筛选任务" value={model} onChange={(event) => onModel(event.target.value)}>
              <option value="">全部模型</option>
              {modelOptions.map((item) => <option key={item} value={item}>{item}</option>)}
            </select>
          </label>
          <div className="task-browser__tools">
            <span className="result-count">第 {page + 1} 页 · 共 {formatNumber(total)} 条</span>
            <div className="layout-switch" role="group" aria-label="任务展示方式">
              <button className={layout === "grid" ? "is-active" : ""} onClick={() => setLayout("grid")} aria-pressed={layout === "grid"} title="方片视图"><Grid2X2 size={16} /><span>方片</span></button>
              <button className={layout === "list" ? "is-active" : ""} onClick={() => setLayout("list")} aria-pressed={layout === "list"} title="列表视图"><List size={17} /><span>列表</span></button>
            </div>
          </div>
        </div>

        {loading ? (
          <div className="panel task-browser__state"><SkeletonRows /></div>
        ) : tasks.length === 0 ? (
          <div className="panel task-browser__state"><EmptyState icon={Video} title="没有匹配任务" description="切换模型、状态筛选或检查搜索关键词。" /></div>
        ) : layout === "grid" ? (
          <div className="task-card-grid">
            {tasks.map((task) => <TaskCard key={task.task_uuid} task={task} now={now} onSelect={onSelect} onCopy={onCopy} onPreview={setPreviewTask} />)}
          </div>
        ) : (
          <div className="panel table-panel task-list-panel">
            <div className="data-table-wrap"><table className="data-table task-table"><thead><tr><th>任务 / 快速预览</th><th>状态</th><th>模型 / 类型</th><th>账号</th><th>积分</th><th>时间</th><th>耗时</th><th aria-label="查看" /></tr></thead><tbody>{tasks.map((task) => {
              const media = taskMediaOf(task);
              const MediaIcon = media?.kind === "image" ? ImageIcon : media?.kind === "audio" ? AudioLines : Play;
              const mediaLabel = media?.kind === "image" ? "查看图片" : media?.kind === "audio" ? "播放音频" : "播放视频";
              const expected = taskMediaKindOf(task);
              return <tr key={task.task_uuid} onClick={() => onSelect(task)}><td><div className="task-primary">{media ? <button className={cn("quick-play", "quick-play--compact", `quick-play--${media.kind}`)} title={mediaLabel} aria-label={`${mediaLabel}：${task.model}`} onClick={(event) => { event.stopPropagation(); setPreviewTask(task); }}><MediaIcon size={14} fill={media.kind === "video" ? "currentColor" : "none"} /></button> : <span title="暂无可预览媒体">{expected === "image" ? <ImageIcon size={17} /> : expected === "audio" ? <AudioLines size={17} /> : <Video size={17} />}</span>}<div><strong>{promptOf(task)}</strong><button onClick={(event) => { event.stopPropagation(); onCopy(task.task_uuid); }}>{shortId(task.task_uuid, 10)} <Copy size={11} /></button></div></div></td><td><StatusBadge status={task.status} pulse={RUNNING.has(task.status)} />{task.error_code && <small className="cell-error">{task.error_code}</small>}</td><td><div className="model-cell"><strong>{task.model}</strong><span>{task.task_type.replaceAll("_", " ")}</span></div></td><td><div className="mono-cell"><strong>{shortId(task.account_uuid, 7)}</strong><span>{shortId(task.upstream_task_id, 7)}</span></div></td><td><div className="cost-cell"><strong>{formatNumber(task.actual_credit_cost ?? task.estimated_credit_cost)}</strong><span>{task.actual_credit_cost === null ? "预估" : "已结算"}</span></div></td><td><div className="date-cell"><div><strong>{formatDate(task.created_at, true)}</strong><span>{relativeTime(task.created_at)}</span></div></div></td><td><div className={cn("duration-cell", taskIsLive(task) && "duration-cell--running")}><strong>{formatDuration(taskDurationSeconds(task, now))}</strong><span>{taskDurationLabel(task)}</span></div></td><td><ChevronRight size={18} /></td></tr>;
            })}</tbody></table></div>
          </div>
        )}

        <div className="panel pagination task-browser__pagination"><span>{total === 0 ? 0 : page * PAGE_SIZE + 1}—{Math.min((page + 1) * PAGE_SIZE, total)} / {formatNumber(total)}</span><div><IconButton label="上一页" disabled={page === 0} onClick={() => onPage(Math.max(0, page - 1))}><ChevronLeft size={18} /></IconButton><span>{page + 1} / {pageCount}</span><IconButton label="下一页" disabled={page >= pageCount - 1} onClick={() => onPage(Math.min(pageCount - 1, page + 1))}><ChevronRight size={18} /></IconButton></div></div>
      </section>
      {previewTask && <TaskPreviewModal task={previewTask} onClose={() => setPreviewTask(null)} />}
    </div>
  );
}

function TaskCard({ task, now, onSelect, onCopy, onPreview }: { task: Task; now: number; onSelect: (task: Task) => void; onCopy: (value: string) => void; onPreview: (task: Task) => void }) {
  const media = taskMediaOf(task);
  const expectedKind = media?.kind ?? taskMediaKindOf(task);
  return (
    <article className={cn("task-card", `task-card--${statusTone(task.status)}`)} tabIndex={0} onClick={() => onSelect(task)} onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); onSelect(task); } }}>
      <header className="task-card__header">
        <div><strong>{task.model}</strong><span>{relativeTime(task.created_at)} · {task.task_type.replaceAll("_", " ")}</span></div>
        <StatusBadge status={task.status} pulse={RUNNING.has(task.status)} />
      </header>
      <div className="task-card__body">
        {media ? (
          <div className={cn("task-card__media", `task-card__media--${media.kind}`)} onClick={(event) => event.stopPropagation()}>
            {media.kind === "image" ? (
              <button className="task-card__image" type="button" onClick={() => onPreview(task)} aria-label={`查看 ${task.model} 任务图片`}>
                <span className="task-card__image-glow" style={{ backgroundImage: `url(${JSON.stringify(media.url)})` }} aria-hidden="true" />
                <img src={media.url} alt={`${task.model} 生成结果`} loading="lazy" />
                <span className="task-card__media-label"><ImageIcon size={12} />IMAGE · 点击放大</span>
              </button>
            ) : media.kind === "audio" ? (
              <div className="task-card__audio"><AudioLines size={28} /><audio controls preload="metadata"><source src={media.url} type={media.type ?? undefined} /></audio><span>AUDIO · 点击播放</span></div>
            ) : (
              <video controls preload="metadata" playsInline poster={media.thumbnailUrl ?? undefined}>
                <source src={media.url} type={media.type ?? undefined} />
              </video>
            )}
          </div>
        ) : task.error_message ? (
          <div className="task-card__error"><AlertTriangle size={16} /><div><strong>{task.error_code ?? "TASK_ERROR"}</strong><p>{task.error_message}</p></div></div>
        ) : (
          <div className="task-card__pending"><span>{expectedKind === "image" ? <ImageIcon size={26} /> : expectedKind === "audio" ? <AudioLines size={26} /> : <Video size={26} />}</span><div><strong>{taskIsLive(task) ? `${expectedKind === "image" ? "图片" : expectedKind === "audio" ? "音频" : "视频"}正在生成` : `暂无${expectedKind === "image" ? "图片" : expectedKind === "audio" ? "音频" : "成片"}`}</strong><p>{taskIsLive(task) ? `已运行 ${formatDuration(taskDurationSeconds(task, now))}` : `输出中未找到可预览的${expectedKind === "image" ? "图片" : expectedKind === "audio" ? "音频" : "视频"}地址`}</p></div></div>
        )}
        <div className="task-card__prompt"><p>{promptOf(task)}</p><button title="复制任务 ID" aria-label="复制任务 ID" onClick={(event) => { event.stopPropagation(); onCopy(task.task_uuid); }}><Copy size={12} /></button></div>
      </div>
      <footer className="task-card__footer"><div><strong>{formatNumber(task.actual_credit_cost ?? task.estimated_credit_cost)}</strong><span>积分 · {task.actual_credit_cost === null ? "预估" : "已结算"}</span></div><div><span>{formatDate(task.created_at, true)}</span><button onClick={(event) => { event.stopPropagation(); onSelect(task); }}>详情 <ChevronRight size={13} /></button></div></footer>
    </article>
  );
}

function TaskPreviewModal({ task, onClose }: { task: Task; onClose: () => void }) {
  const media = taskMediaOf(task);
  useEffect(() => {
    const closeOnEscape = (event: KeyboardEvent) => { if (event.key === "Escape") onClose(); };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [onClose]);
  if (!media) return null;
  return (
    <div className="media-preview-backdrop" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <div className={cn("media-preview-modal", `media-preview-modal--${media.kind}`)} role="dialog" aria-modal="true" aria-label={`任务${media.kind === "image" ? "图片" : media.kind === "audio" ? "音频" : "视频"}预览`}>
        <header><div><span>QUICK LOOK / {media.kind === "image" ? "图片快览" : media.kind === "audio" ? "音频快览" : "视频快览"}</span><h2>{task.model}</h2></div><IconButton label={`关闭${media.kind === "image" ? "图片" : media.kind === "audio" ? "音频" : "视频"}预览`} onClick={onClose}><X size={19} /></IconButton></header>
        <div className="media-preview-modal__stage">{media.kind === "image" ? <img src={media.url} alt={`${task.model} 生成结果`} /> : media.kind === "audio" ? <div className="media-preview-modal__audio"><AudioLines size={52} /><audio src={media.url} autoPlay controls /></div> : <video src={media.url} poster={media.thumbnailUrl ?? undefined} autoPlay controls playsInline />}</div>
        <footer><div><StatusBadge status={task.status} /><span>{formatDate(task.created_at)} · {formatNumber(task.actual_credit_cost ?? task.estimated_credit_cost)} 积分</span></div><p>{promptOf(task)}</p></footer>
      </div>
    </div>
  );
}

function MailboxImportModal({ existingEmails, api, onClose, onImported }: { existingEmails: string[]; api: VideoTaskApi; onClose: () => void; onImported: (result: MailboxImportResult) => Promise<void> }) {
  const [source, setSource] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<MailboxImportResult | null>(null);
  const preview = useMemo(() => parseMailboxImportPreview(source, existingEmails), [existingEmails, source]);
  const duplicates = preview.issues.filter((issue) => issue.code.startsWith("DUPLICATE_")).length;
  const invalid = preview.issues.length - duplicates;
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!source.trim()) { setError("请粘贴邮箱凭据文本"); return; }
    setBusy(true); setError(""); setResult(null);
    try {
      const imported = await api.importMailboxes(source);
      setResult(imported);
      await onImported(imported);
      const sourceLines = source.split(/\r\n?|\n/);
      setSource(imported.issues.map((issue) => sourceLines[issue.line_number - 1] ?? "").filter(Boolean).join("\n"));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "邮箱导入失败");
    } finally {
      setBusy(false);
    }
  };
  return <Modal title="批量导入邮箱" eyebrow="MAILBOX CREDENTIAL IMPORT" onClose={onClose} wide><form className="form-grid bulk-import" onSubmit={(event) => void submit(event)}><label className="form-grid__wide"><span>邮箱凭据文本</span><textarea className="bulk-import__textarea" rows={12} value={source} onChange={(event) => { setSource(event.target.value); setResult(null); setError(""); }} spellCheck={false} placeholder="email@example.com----password----client_id----refresh_token" /></label><div className="bulk-import__guide form-grid__wide"><ShieldCheck size={18} /><div><strong>每行导入一个邮箱</strong><span>格式：邮箱----密码----client_id----refresh_token。凭据在 API 中以 AES-GCM 加密后落库，页面与响应不会回显。</span></div></div><div className="bulk-import__preview form-grid__wide"><span>可导入 <strong>{preview.records.length}</strong> 条</span><span className={duplicates ? "has-warning" : undefined}>重复 <strong>{duplicates}</strong> 条</span><span className={invalid ? "has-danger" : undefined}>格式问题 <strong>{invalid}</strong> 条</span>{preview.blankLines > 0 && <span>空行 <strong>{preview.blankLines}</strong> 条</span>}</div>{preview.issues.length > 0 && <div className="bulk-import__duplicates form-grid__wide"><div><AlertTriangle size={18} /><span><strong>发现 {preview.issues.length} 条待处理记录</strong>服务端将再次执行完整校验。</span></div><ol>{preview.issues.slice(0, 8).map((issue) => <li key={`${issue.lineNumber}-${issue.code}`}><span>第 {issue.lineNumber} 行</span><strong>{issue.email || "未识别邮箱"}</strong><em>{issue.reason}</em></li>)}</ol></div>}{result && <div className={cn("bulk-import__result", result.issues.length > 0 && "has-warning", "form-grid__wide")}><div><CheckCircle2 size={19} /><span><strong>成功导入 {result.imported} 个邮箱</strong>重复 {result.duplicates} 条，格式问题 {result.invalid} 条；新邮箱已进入后台校验队列。</span></div></div>}{error && <div className="form-error"><AlertTriangle size={16} />{error}</div>}<div className="modal-actions form-grid__wide"><button type="button" className="secondary-button" onClick={onClose} disabled={busy}>关闭</button><button className="primary-button" type="submit" disabled={busy || !source.trim()}>{busy ? <RefreshCcw className="spin" size={17} /> : <Upload size={17} />}{busy ? "正在加密导入…" : "导入邮箱"}</button></div></form></Modal>;
}

function ParentAccountImportModal({ existingEmails, api, onClose, onImported }: { existingEmails: string[]; api: VideoTaskApi; onClose: () => void; onImported: (result: ParentAccountImportResult) => Promise<void> }) {
  const [source, setSource] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<ParentAccountImportResult | null>(null);
  const preview = useMemo(() => parseParentAccountImportPreview(source, existingEmails), [existingEmails, source]);
  const duplicates = preview.issues.filter((issue) => issue.code.startsWith("DUPLICATE_")).length;
  const invalid = preview.issues.length - duplicates;
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!source.trim()) { setError("请粘贴母号凭据文本"); return; }
    setBusy(true); setError(""); setResult(null);
    try {
      const imported = await api.importParentAccounts(source);
      setResult(imported);
      await onImported(imported);
      const sourceLines = source.split(/\r\n?|\n/);
      setSource(imported.issues.map((issue) => sourceLines[issue.line_number - 1] ?? "").filter(Boolean).join("\n"));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "母号导入失败");
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal title="批量导入母号" eyebrow="PARENT ACCOUNT IMPORT" onClose={onClose} wide>
      <form className="form-grid bulk-import" onSubmit={(event) => void submit(event)}>
        <label className="form-grid__wide"><span>母号凭据文本</span><textarea className="bulk-import__textarea" rows={12} value={source} onChange={(event) => { setSource(event.target.value); setResult(null); setError(""); }} spellCheck={false} autoComplete="off" placeholder="email@example.com Password https://example.com/invite/token" /></label>
        <div className="bulk-import__guide form-grid__wide"><ShieldCheck size={18} /><div><strong>每行导入一个母号</strong><span>格式：邮箱 密码 邀请链接。密码经 AES-GCM 加密落库，并在母号池管理表格中直接展示。</span></div></div>
        <div className="bulk-import__preview form-grid__wide"><span>可导入 <strong>{preview.records.length}</strong> 条</span><span className={duplicates ? "has-warning" : undefined}>重复 <strong>{duplicates}</strong> 条</span><span className={invalid ? "has-danger" : undefined}>格式问题 <strong>{invalid}</strong> 条</span>{preview.blankLines > 0 && <span>空行 <strong>{preview.blankLines}</strong> 条</span>}</div>
        {preview.issues.length > 0 && <div className="bulk-import__duplicates form-grid__wide"><div><AlertTriangle size={18} /><span><strong>发现 {preview.issues.length} 条待处理记录</strong>服务端将再次执行完整校验。</span></div><ol>{preview.issues.slice(0, 8).map((issue) => <li key={`${issue.lineNumber}-${issue.code}`}><span>第 {issue.lineNumber} 行</span><strong>{issue.email || "未识别邮箱"}</strong><em>{issue.reason}</em></li>)}</ol></div>}
        {result && <div className={cn("bulk-import__result", result.issues.length > 0 && "has-warning", "form-grid__wide")}><div><CheckCircle2 size={19} /><span><strong>成功导入 {result.imported} 个母号</strong>重复 {result.duplicates} 条，格式问题 {result.invalid} 条；计数均从 0 开始。</span></div></div>}
        {error && <div className="form-error"><AlertTriangle size={16} />{error}</div>}
        <div className="modal-actions form-grid__wide"><button type="button" className="secondary-button" onClick={onClose} disabled={busy}>关闭</button><button className="primary-button" type="submit" disabled={busy || !source.trim()}>{busy ? <RefreshCcw className="spin" size={17} /> : <Upload size={17} />}{busy ? "正在加密导入…" : "导入母号"}</button></div>
      </form>
    </Modal>
  );
}

function MailboxCodeModal({ mailbox, api, onClose, onCopy }: { mailbox: Mailbox; api: VideoTaskApi; onClose: () => void; onCopy: (value: string) => void }) {
  const [result, setResult] = useState<MailboxCodeResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [requestVersion, setRequestVersion] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    setLoading(true);
    setResult(null);
    setError("");
    void api.queryMailboxCode(mailbox.email, 60, controller.signal)
      .then((value) => {
        if (active) setResult(value);
      })
      .catch((cause: unknown) => {
        if (!active || (cause instanceof DOMException && cause.name === "AbortError")) return;
        if (cause instanceof ApiError && cause.status === 408) setError("等待验证码超时，请确认验证码邮件已发送后重试。");
        else if (cause instanceof ApiError && cause.status === 409) setError("当前邮箱状态不可用，请先重新校验邮箱凭据。");
        else if (cause instanceof ApiError && cause.status === 404) setError("邮箱池中没有找到这个邮箱。");
        else setError(cause instanceof Error ? cause.message : "验证码读取失败，请稍后重试。");
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
      controller.abort();
    };
  }, [api, mailbox.email, requestVersion]);

  return (
    <Modal title="查看验证码" eyebrow="MAILBOX OTP LOOKUP" onClose={onClose}>
      <div className="mailbox-code-modal">
        <div className="account-context">
          <span className="account-avatar"><Inbox size={16} /></span>
          <div><strong>{mailbox.email}</strong><small>读取最近十分钟内的最新验证码邮件</small></div>
        </div>
        {loading && <div className="mailbox-code-loading" aria-live="polite"><RefreshCcw className="spin" size={21} /><div><strong>正在等待验证码</strong><span>最长等待 60 秒，服务端每 3 秒检查一次最新邮件。</span></div></div>}
        {!loading && result && <div className="mailbox-code-result" aria-live="polite"><span>VERIFICATION CODE</span><div><strong>{result.code}</strong><button type="button" onClick={() => onCopy(result.code)}><Copy size={16} />复制</button></div><dl><div><dt>接收时间</dt><dd>{formatDate(result.received_at)}</dd></div><div><dt>发件人</dt><dd title={result.sender}>{result.sender || "—"}</dd></div><div><dt>邮件主题</dt><dd title={result.subject}>{result.subject || "—"}</dd></div><div><dt>识别方式</dt><dd>{result.matched_by}</dd></div></dl></div>}
        {!loading && error && <div className="mailbox-code-error" role="alert"><AlertTriangle size={19} /><div><strong>验证码读取未完成</strong><span>{error}</span></div></div>}
        <div className="modal-actions"><button type="button" className="secondary-button" onClick={onClose}>关闭</button>{!loading && error && <button type="button" className="primary-button" onClick={() => setRequestVersion((value) => value + 1)}><RefreshCcw size={16} />重新读取</button>}</div>
      </div>
    </Modal>
  );
}

function SettingsModal({ credentials, spaces, registrationSettings, onRegistrationSettings, onSave, onClose }: { credentials: ApiCredentials; api: VideoTaskApi; spaces: Space[]; registrationSettings: RegistrationPoolSettings | null; onRegistrationSettings: (payload: { target_space_uuid: string | null; default_max_concurrency: number; expected_version: number }) => Promise<void>; onSave: (credentials: ApiCredentials) => void; onClose: () => void }) {
  const [form, setForm] = useState(credentials);
  const submit = (event: FormEvent) => { event.preventDefault(); onSave(form); };
  return <Modal title="连接配置" eyebrow="CONNECTION PROFILE" onClose={onClose}><form className="form-stack" onSubmit={submit}><div className="info-callout"><ShieldCheck size={18} /><span><strong>密钥仅保存在当前浏览器</strong>页面请求经同源代理转发，输入内容不会写入任务参数。</span></div><label><span>API 地址</span><input required value={form.apiBase} onChange={(event) => setForm({ ...form, apiBase: event.target.value })} placeholder="/api" /></label><label><span>业务 API Key</span><input required type="password" value={form.apiKey} onChange={(event) => setForm({ ...form, apiKey: event.target.value })} /></label><label><span>管理 API Key</span><input required type="password" value={form.adminKey} onChange={(event) => setForm({ ...form, adminKey: event.target.value })} /></label><div className="modal-actions"><button type="button" className="secondary-button" onClick={onClose}>取消</button><button className="primary-button" type="submit"><Zap size={17} />保存并连接</button></div></form>{credentials.adminKey && registrationSettings && <RegistrationSettings settings={registrationSettings} spaces={spaces} onSave={onRegistrationSettings} />}</Modal>;
}

function AddAccountModal({ spaces, api, onClose, onCreated }: { spaces: Space[]; api: VideoTaskApi; onClose: () => void; onCreated: () => Promise<void> }) {
  const [form, setForm] = useState<AccountCreatePayload>({ space_uuid: spaces[0]?.space_uuid ?? "", login_name: "", password: "", video_token: "", token_expires_at: "", balance_credits: 1000, max_concurrency: 3 });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const submit = async (event: FormEvent) => { event.preventDefault(); setBusy(true); setError(""); try { const payload = { ...form }; if (!payload.video_token) { delete payload.video_token; delete payload.token_expires_at; } else if (payload.token_expires_at) { payload.token_expires_at = new Date(payload.token_expires_at).toISOString(); } await api.createAccount(payload); await onCreated(); } catch (cause) { setError(cause instanceof Error ? cause.message : "账号添加失败"); } finally { setBusy(false); } };
  return <Modal title="添加账号" eyebrow="NEW POOL RESOURCE" onClose={onClose} wide><form className="form-grid" onSubmit={(event) => void submit(event)}><label><span>所属空间</span><select required value={form.space_uuid} onChange={(event) => setForm({ ...form, space_uuid: event.target.value })}><option value="">选择空间</option>{spaces.map((space) => <option key={space.space_uuid} value={space.space_uuid}>{space.name}</option>)}</select></label><label><span>登录账号</span><input required value={form.login_name} onChange={(event) => setForm({ ...form, login_name: event.target.value })} placeholder="account@example.com" /></label><label><span>登录密码</span><input required type="password" value={form.password} onChange={(event) => setForm({ ...form, password: event.target.value })} /></label><label><span>最大并发</span><input required type="number" min={1} max={100} value={form.max_concurrency} onChange={(event) => setForm({ ...form, max_concurrency: Number(event.target.value) })} /></label><label><span>初始积分</span><input required type="number" min={0} value={form.balance_credits} onChange={(event) => setForm({ ...form, balance_credits: Number(event.target.value) })} /></label><label><span>Token 到期时间</span><input type="datetime-local" required={Boolean(form.video_token)} value={form.token_expires_at} onChange={(event) => setForm({ ...form, token_expires_at: event.target.value })} /></label><label className="form-grid__wide"><span>视频 Token（可稍后补充）</span><textarea rows={3} value={form.video_token} onChange={(event) => setForm({ ...form, video_token: event.target.value })} placeholder="Bearer token 内容" /></label>{error && <div className="form-error"><AlertTriangle size={16} />{error}</div>}<div className="modal-actions form-grid__wide"><button type="button" className="secondary-button" onClick={onClose}>取消</button><button className="primary-button" type="submit" disabled={busy}>{busy ? <RefreshCcw className="spin" size={17} /> : <Plus size={17} />}添加到账号池</button></div></form></Modal>;
}

interface BulkImportFailure {
  lineNumber: number;
  loginName: string;
  reason: string;
}

interface BulkImportSummary {
  imported: number;
  failed: number;
  failures: BulkImportFailure[];
}

function bulkImportFailureReason(cause: unknown): string {
  if (cause instanceof ApiError && cause.status === 409) return "账号已存在";
  if (cause instanceof ApiError && cause.status === 404) return "所属空间不存在";
  return cause instanceof Error ? cause.message : "导入请求失败";
}

function BulkImportAccountModal({ spaces, existingAccounts, api, onClose, onImported }: { spaces: Space[]; existingAccounts: Account[]; api: VideoTaskApi; onClose: () => void; onImported: (imported: number, failed: number) => Promise<void> }) {
  const [spaceUuid, setSpaceUuid] = useState(spaces[0]?.space_uuid ?? "");
  const [label, setLabel] = useState<AccountLabel | "">("");
  const [source, setSource] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [summary, setSummary] = useState<BulkImportSummary | null>(null);
  const existingLoginNames = useMemo(() => existingAccounts.map(accountLoginName), [existingAccounts]);
  const parsed = useMemo(() => parseBulkAccountText(source, existingLoginNames), [existingLoginNames, source]);
  const duplicateIssues = parsed.issues.filter((issue) => issue.code !== "FORMAT");
  const formatIssues = parsed.issues.filter((issue) => issue.code === "FORMAT");

  const updateSource = (value: string) => {
    setSource(value);
    setError("");
    setSummary(null);
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!spaceUuid) {
      setError("请选择所属空间");
      return;
    }
    if (!label) {
      setError("请选择账号标签");
      return;
    }
    if (parsed.accounts.length === 0) {
      setError(parsed.issues.length ? "请修正格式错误后再导入" : "请粘贴要导入的账号和密码");
      return;
    }

    setBusy(true);
    setError("");
    setSummary(null);
    const outcomes: Array<{ entry: BulkImportAccount; reason?: string }> = new Array(parsed.accounts.length);
    let cursor = 0;

    const worker = async () => {
      while (cursor < parsed.accounts.length) {
        const index = cursor;
        cursor += 1;
        const entry = parsed.accounts[index];
        try {
          await api.createAccount({
            space_uuid: spaceUuid,
            login_name: entry.loginName,
            password: entry.password,
            label,
            balance_credits: 0,
            max_concurrency: 3,
          });
          outcomes[index] = { entry };
        } catch (cause) {
          outcomes[index] = { entry, reason: bulkImportFailureReason(cause) };
        }
      }
    };

    try {
      await Promise.all(Array.from({ length: Math.min(5, parsed.accounts.length) }, () => worker()));
      const requestFailures = outcomes
        .filter((outcome) => Boolean(outcome.reason))
        .map((outcome) => ({ lineNumber: outcome.entry.lineNumber, loginName: outcome.entry.loginName, reason: outcome.reason ?? "导入请求失败" }));
      const formatFailures = parsed.issues.map((issue) => ({ lineNumber: issue.lineNumber, loginName: issue.loginName, reason: issue.reason }));
      const failures = [...formatFailures, ...requestFailures].sort((left, right) => left.lineNumber - right.lineNumber);
      const imported = outcomes.length - requestFailures.length;
      const remainingLines = [
        ...parsed.issues.map((issue) => issue.source),
        ...outcomes.filter((outcome) => outcome.reason).map((outcome) => `${outcome.entry.loginName} | ${outcome.entry.password}`),
      ];
      setSource(remainingLines.join("\n"));
      setSummary({ imported, failed: failures.length, failures });
      if (imported > 0) await onImported(imported, failures.length);
    } finally {
      setBusy(false);
    }
  };

  return <Modal title="批量导入账号" eyebrow="BULK ACCOUNT IMPORT" onClose={onClose} wide><form className="form-grid bulk-import" onSubmit={(event) => void submit(event)}><label><span>所属空间</span><select required value={spaceUuid} onChange={(event) => setSpaceUuid(event.target.value)}><option value="">选择空间</option>{spaces.map((space) => <option key={space.space_uuid} value={space.space_uuid}>{space.name}</option>)}</select></label><label><span>账号标签</span><select required value={label} onChange={(event) => setLabel(event.target.value as AccountLabel | "")}><option value="">选择标签</option><option value="mmoshenqi">mmoshenqi</option><option value="macbook">macbook</option></select></label><label className="form-grid__wide"><span>账号与密码</span><textarea className="bulk-import__textarea" rows={11} value={source} onChange={(event) => updateSource(event.target.value)} spellCheck={false} placeholder={"account-one@example.com | PASSWORD\naccount-two@example.com | PASSWORD"} /></label><div className="bulk-import__guide form-grid__wide"><Upload size={18} /><div><strong>每行导入一个账号</strong><span>格式：登录账号 | 登录密码。所选标签会应用到本批全部账号；粘贴后会立即对比当前账号池，并按忽略大小写的登录账号识别库内重复和同批重复。</span></div></div><div className="bulk-import__preview form-grid__wide" aria-live="polite"><span>可导入 <strong>{parsed.accounts.length}</strong> 条</span><span>账号标签 <strong>{label || "未选择"}</strong></span><span className={duplicateIssues.length ? "has-warning" : undefined}>重复账号 <strong>{duplicateIssues.length}</strong> 条</span><span className={formatIssues.length ? "has-danger" : undefined}>格式问题 <strong>{formatIssues.length}</strong> 条</span>{parsed.blankLines > 0 && <span>已忽略空行 <strong>{parsed.blankLines}</strong> 条</span>}</div>{duplicateIssues.length > 0 && <div className="bulk-import__duplicates form-grid__wide" aria-live="polite"><div><AlertTriangle size={18} /><span><strong>已识别 {duplicateIssues.length} 个重复账号</strong>这些行会被跳过，不会发起导入请求。</span></div><ol>{duplicateIssues.slice(0, 8).map((issue) => <li key={`${issue.lineNumber}-${issue.loginName}-${issue.code}`}><span>第 {issue.lineNumber} 行</span><strong>{issue.loginName}</strong><em>{issue.reason}</em></li>)}</ol>{duplicateIssues.length > 8 && <small>另有 {duplicateIssues.length - 8} 条重复记录</small>}</div>}{summary && <div className={cn("bulk-import__result", summary.failed ? "has-warning" : "is-success", "form-grid__wide")}><div>{summary.failed ? <AlertTriangle size={19} /> : <CheckCircle2 size={19} />}<span><strong>成功导入 {summary.imported} 个账号</strong>{summary.failed ? `，剩余 ${summary.failed} 条已保留在输入框中` : "，全部处理完成"}</span></div>{summary.failures.length > 0 && <ol>{summary.failures.map((failure, index) => <li key={`${failure.lineNumber}-${failure.loginName}-${index}`}><span>第 {failure.lineNumber} 行</span><strong>{failure.loginName || "未识别账号"}</strong><em>{failure.reason}</em></li>)}</ol>}</div>}{error && <div className="form-error"><AlertTriangle size={16} />{error}</div>}<div className="modal-actions form-grid__wide"><button type="button" className="secondary-button" onClick={onClose} disabled={busy}>关闭</button><button className="primary-button" type="submit" disabled={busy || parsed.accounts.length === 0 || !spaceUuid || !label}>{busy ? <RefreshCcw className="spin" size={17} /> : <Upload size={17} />}{busy ? `正在导入 ${parsed.accounts.length} 个账号…` : `导入 ${parsed.accounts.length} 个账号`}</button></div></form></Modal>;
}

function TokenModal({ account, api, onClose, onUpdated }: { account: Account; api: VideoTaskApi; onClose: () => void; onUpdated: () => Promise<void> }) {
  const [token, setToken] = useState(""); const [expires, setExpires] = useState(""); const [busy, setBusy] = useState(false); const [error, setError] = useState("");
  const submit = async (event: FormEvent) => { event.preventDefault(); setBusy(true); setError(""); try { await api.updateToken(account.account_uuid, { video_token: token, token_expires_at: new Date(expires).toISOString(), expected_version: account.version }); await onUpdated(); } catch (cause) { setError(cause instanceof Error ? cause.message : "Token 更新失败"); } finally { setBusy(false); } };
  return <Modal title="更新视频 Token" eyebrow="CREDENTIAL ROTATION" onClose={onClose}><form className="form-stack" onSubmit={(event) => void submit(event)}><div className="account-context"><span className="account-avatar">{accountLoginName(account).slice(0, 1).toUpperCase()}</span><div><strong>{accountLoginName(account)}</strong><small>当前版本 v{account.version} · {account.status}</small></div></div><label><span>新 Token</span><textarea required rows={4} value={token} onChange={(event) => setToken(event.target.value)} /></label><label><span>到期时间</span><input required type="datetime-local" value={expires} onChange={(event) => setExpires(event.target.value)} /></label>{error && <div className="form-error"><AlertTriangle size={16} />{error}</div>}<div className="modal-actions"><button type="button" className="secondary-button" onClick={onClose}>取消</button><button className="primary-button" disabled={busy}>{busy ? <RefreshCcw className="spin" size={17} /> : <KeyRound size={17} />}更新并重新校验</button></div></form></Modal>;
}

function EditAccountModal({ account, spaces, api, onClose, onUpdated }: { account: Account; spaces: Space[]; api: VideoTaskApi; onClose: () => void; onUpdated: () => Promise<void> }) {
  const [spaceUuid, setSpaceUuid] = useState(account.space_uuid);
  const [password, setPassword] = useState("");
  const [maxConcurrency, setMaxConcurrency] = useState(account.max_concurrency);
  const [manualStatus, setManualStatus] = useState<"KEEP" | "ACTIVE" | "MANUAL_DISABLED">("KEEP");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const payload: AccountPatchPayload = { expected_version: account.version };
    if (spaceUuid !== account.space_uuid) payload.space_uuid = spaceUuid;
    if (password.trim()) payload.password = password;
    if (maxConcurrency !== account.max_concurrency) payload.max_concurrency = maxConcurrency;
    if (manualStatus !== "KEEP") payload.manual_status = manualStatus;
    if (Object.keys(payload).length === 1) {
      setError("请至少修改一项账号资料");
      return;
    }
    setBusy(true);
    setError("");
    try {
      await api.patchAccount(account.account_uuid, payload);
      await onUpdated();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "账号资料更新失败");
    } finally {
      setBusy(false);
    }
  };
  return <Modal title="编辑账号" eyebrow="ACCOUNT ADMINISTRATION" onClose={onClose} wide><form className="form-grid" onSubmit={(event) => void submit(event)}><div className="account-context form-grid__wide"><span className="account-avatar">{accountLoginName(account).slice(0, 1).toUpperCase()}</span><div><strong>{accountLoginName(account)}</strong><small>{account.account_uuid} · v{account.version}</small></div></div><label><span>所属空间</span><select value={spaceUuid} onChange={(event) => setSpaceUuid(event.target.value)}>{spaces.map((space) => <option key={space.space_uuid} value={space.space_uuid}>{space.name}</option>)}</select></label><label><span>最大并发</span><input required type="number" min={Math.max(1, account.active_tasks)} max={100} value={maxConcurrency} onChange={(event) => setMaxConcurrency(Number(event.target.value))} /></label><label><span>调度状态</span><select value={manualStatus} onChange={(event) => setManualStatus(event.target.value as "KEEP" | "ACTIVE" | "MANUAL_DISABLED")}><option value="KEEP">保持当前状态（{account.status}）</option><option value="ACTIVE">启用并重新校验</option><option value="MANUAL_DISABLED">手动停用</option></select></label><label><span>新登录密码（留空不变）</span><input type="password" value={password} onChange={(event) => setPassword(event.target.value)} autoComplete="new-password" /></label>{error && <div className="form-error"><AlertTriangle size={16} />{error}</div>}<div className="modal-actions form-grid__wide"><button type="button" className="secondary-button" onClick={onClose}>取消</button><button className="primary-button" type="submit" disabled={busy}>{busy ? <RefreshCcw className="spin" size={17} /> : <Pencil size={17} />}保存修改</button></div></form></Modal>;
}

function DeleteAccountModal({ account, api, onClose, onDeleted }: { account: Account; api: VideoTaskApi; onClose: () => void; onDeleted: () => Promise<void> }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const remove = async () => {
    setBusy(true);
    setError("");
    try {
      await api.deleteAccount(account.account_uuid);
      await onDeleted();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "账号删除失败");
    } finally {
      setBusy(false);
    }
  };
  return <Modal title="删除账号" eyebrow="DESTRUCTIVE ACTION" onClose={onClose}><div className="form-stack"><div className="account-context"><span className="account-avatar">{accountLoginName(account).slice(0, 1).toUpperCase()}</span><div><strong>{accountLoginName(account)}</strong><small>{account.account_uuid}</small></div></div><div className="danger-callout"><AlertTriangle size={18} /><span><strong>此操作会移除账号凭据。</strong>已有任务或积分流水的账号会被服务端保护，请改用“手动停用”保留审计记录。</span></div>{error && <div className="form-error"><AlertTriangle size={16} />{error}</div>}<div className="modal-actions"><button type="button" className="secondary-button" onClick={onClose}>取消</button><button type="button" className="danger-button" onClick={() => void remove()} disabled={busy}>{busy ? <RefreshCcw className="spin" size={17} /> : <Trash2 size={17} />}确认删除</button></div></div></Modal>;
}

function bulkDeleteReason(code: string | null): string {
  if (code === "ACCOUNT_HAS_ACTIVE_TASKS") return "存在运行中任务";
  if (code === "ACCOUNT_HAS_RESERVED_CREDITS") return "存在预留积分";
  if (code === "ACCOUNT_HAS_HISTORY") return "存在任务、媒体、积分或登录历史";
  if (code === "ACCOUNT_NOT_FOUND") return "账号已不存在";
  return code ?? "当前状态受保护";
}

function BulkExportDeleteModal({ accountUuids, api, onClose, onCompleted }: { accountUuids: string[]; api: VideoTaskApi; onClose: () => void; onCompleted: (result: AccountBulkDeleteResult) => Promise<void> }) {
  const [preview, setPreview] = useState<AccountBulkDeletePreview | null>(null);
  const [result, setResult] = useState<AccountBulkDeleteResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    setPreview(null);
    setError("");
    api.previewBulkDeleteAccounts(accountUuids).then((value) => {
      if (active) setPreview(value);
    }).catch((cause: unknown) => {
      if (active) setError(cause instanceof Error ? cause.message : "批量删除预检失败");
    });
    return () => { active = false; };
  }, [accountUuids, api]);

  const exportAndDelete = async () => {
    setBusy(true);
    setError("");
    let exported = false;
    try {
      const file = await api.exportAccountCredentials(accountUuids);
      downloadCredentialExport(file);
      exported = true;
      const deleted = await api.bulkDeleteAccounts(accountUuids, file.receipt);
      setResult(deleted);
      await onCompleted(deleted);
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : "批量操作失败";
      setError(exported ? `凭据文件已导出；账号删除失败：${message}` : message);
    } finally {
      setBusy(false);
    }
  };

  const protectedItems = (result?.items ?? preview?.items ?? []).filter((item) => item.outcome === "PROTECTED" || item.outcome === "MISSING" || item.outcome === "SKIPPED");
  return <Modal title="导出并删除选中" eyebrow="EXPORT BEFORE DELETE" onClose={() => { if (!busy) onClose(); }} wide><div className="form-stack bulk-delete-modal">
    <div className="danger-callout"><AlertTriangle size={18} /><span><strong>先下载凭据文件，再提交删除。</strong>文件格式为“邮箱|密码|token”，有任务、流水或登录历史的账号会保留。</span></div>
    {!preview && !error && <div className="bulk-delete-loading"><RefreshCcw className="spin" size={18} /><span>正在检查 {accountUuids.length} 个账号…</span></div>}
    {preview && <div className="bulk-delete-summary"><div><span>将导出</span><strong>{preview.requested}</strong></div><div><span>预计删除</span><strong>{preview.deletable}</strong></div><div><span>受保护</span><strong>{preview.protected}</strong></div><div><span>已不存在</span><strong>{preview.missing}</strong></div></div>}
    {result && <div className="bulk-delete-result"><CheckCircle2 size={20} /><div><strong>批量操作完成</strong><span>已导出 {result.requested} 个，删除 {result.deleted} 个，保留 {result.skipped} 个。</span></div></div>}
    {protectedItems.length > 0 && <div className="bulk-delete-protected"><strong>保留账号</strong><ol>{protectedItems.slice(0, 10).map((item) => <li key={item.account_uuid}><span>{item.login_name ?? shortId(item.account_uuid, 12)}</span><em>{bulkDeleteReason(item.code)}</em></li>)}</ol>{protectedItems.length > 10 && <small>另有 {protectedItems.length - 10} 个账号受保护</small>}</div>}
    {preview?.missing ? <div className="form-error"><AlertTriangle size={16} />选择中有账号已不存在，请关闭弹窗刷新后重试。</div> : null}
    {error && <div className="form-error"><AlertTriangle size={16} />{error}</div>}
    <div className="modal-actions"><button type="button" className="secondary-button" onClick={onClose} disabled={busy}>{result ? "关闭" : "取消"}</button>{!result && <button type="button" className="danger-button" onClick={() => void exportAndDelete()} disabled={busy || !preview || preview.missing > 0}>{busy ? <RefreshCcw className="spin" size={17} /> : <Download size={17} />}{busy ? "正在导出并删除…" : `导出 ${preview?.requested ?? accountUuids.length} 个并删除可删除项`}</button>}</div>
  </div></Modal>;
}

function TaskDrawer({ task, onClose, onCopy, onCancel }: { task: Task; onClose: () => void; onCopy: (value: string) => void; onCancel?: () => void }) {
  const outputUrl = taskMediaOf(task)?.url ?? null;
  return <div className="drawer-backdrop" onMouseDown={(event) => event.target === event.currentTarget && onClose()}><aside className="task-drawer"><div className="drawer-head"><div><span>TASK INSPECTOR</span><h2>任务详情</h2></div><IconButton label="关闭" onClick={onClose}><X size={19} /></IconButton></div><div className="drawer-status"><StatusBadge status={task.status} pulse={RUNNING.has(task.status)} /><span>{formatDate(task.updated_at)}</span></div><section className="drawer-prompt"><span>生成提示词</span><p>{promptOf(task)}</p></section><section className="detail-grid"><div><span>内部 UUID</span><button onClick={() => onCopy(task.task_uuid)}>{task.task_uuid}<Copy size={12} /></button></div><div><span>上游任务 ID</span><button onClick={() => task.upstream_task_id && onCopy(task.upstream_task_id)}>{task.upstream_task_id ?? "等待分配"}<Copy size={12} /></button></div><div><span>模型</span><strong>{task.model}</strong></div><div><span>类型</span><strong>{task.task_type}</strong></div><div><span>账号</span><strong>{shortId(task.account_uuid, 12)}</strong></div><div><span>积分</span><strong>{formatNumber(task.actual_credit_cost ?? task.estimated_credit_cost)}</strong></div></section><section className="task-timeline"><h3>执行时间线</h3>{[["任务入队", task.queued_at, Database], ["账号分配", task.assigned_at, UsersRound], ["提交上游", task.upstream_submitted_at, Server], ["处理完成", task.finished_at, CheckCircle2]].map(([label, dateValue, Icon], index) => { const TimelineIcon = Icon as LucideIcon; return <div className={dateValue ? "is-done" : ""} key={String(label)}><span><TimelineIcon size={15} /></span><i /><strong>{String(label)}</strong><em>{dateValue ? formatDate(String(dateValue)) : "等待中"}</em>{index < 3 && <b />}</div>; })}</section>{task.error_message && <section className="drawer-error"><AlertTriangle size={17} /><div><strong>{task.error_code ?? "TASK_ERROR"}</strong><p>{task.error_message}</p></div></section>}<section className="json-section"><details open><summary>输入参数 <ChevronRight size={15} /></summary><pre>{JSON.stringify(task.input, null, 2)}</pre></details><details><summary>输出参数 <ChevronRight size={15} /></summary><pre>{JSON.stringify(task.output, null, 2)}</pre></details></section><div className="drawer-actions">{outputUrl && <a className="primary-button" href={outputUrl} target="_blank" rel="noreferrer">查看结果 <ExternalLink size={16} /></a>}{onCancel && <button className="danger-button" onClick={onCancel}><TimerReset size={16} />取消任务</button>}</div></aside></div>;
}
