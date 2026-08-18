import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  Clock3,
  Copy,
  Cpu,
  RefreshCcw,
  Search,
  ShieldAlert,
  TimerReset,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from "react";

import type { VideoTaskApi } from "./api";
import { ClientMonitorDrawer } from "./ClientMonitorDrawer";
import type {
  RegistrationClient,
  RegistrationClientListResponse,
  RegistrationClientSummary,
  RegistrationMonitorHealth,
  RegistrationMonitorWindow,
} from "./types";

export type ClientMonitorRange = "10m" | "30m" | "1h" | "6h" | "24h" | "custom";

const PAGE_SIZE = 50;
const RANGE_OPTIONS: Array<{ value: ClientMonitorRange; label: string; code: string }> = [
  { value: "10m", label: "10 分钟", code: "10M" },
  { value: "30m", label: "30 分钟", code: "30M" },
  { value: "1h", label: "1 小时", code: "1H" },
  { value: "6h", label: "6 小时", code: "6H" },
  { value: "24h", label: "24 小时", code: "24H" },
  { value: "custom", label: "自定义", code: "RANGE" },
];
const RANGE_MILLISECONDS: Record<Exclude<ClientMonitorRange, "custom">, number> = {
  "10m": 10 * 60_000,
  "30m": 30 * 60_000,
  "1h": 60 * 60_000,
  "6h": 6 * 60 * 60_000,
  "24h": 24 * 60 * 60_000,
};
const HEALTH_FILTERS: Array<{ value: RegistrationMonitorHealth | ""; label: string }> = [
  { value: "", label: "全部客户端" },
  { value: "NORMAL", label: "正常" },
  { value: "ATTENTION", label: "需关注" },
  { value: "ABNORMAL", label: "异常" },
  { value: "NO_ACTIVITY", label: "无作业" },
];
const EMPTY_SUMMARY: RegistrationClientSummary = {
  total_clients: 0,
  active_clients: 0,
  normal_clients: 0,
  attention_clients: 0,
  abnormal_clients: 0,
  no_activity_clients: 0,
  jobs: 0,
  succeeded: 0,
  failed: 0,
  processing: 0,
};

type ClientMonitorViewProps = {
  api: VideoTaskApi;
  autoRefresh: boolean;
  refreshToken: number;
  onCopy: (value: string) => void;
};

function toLocalInput(value: Date): string {
  const shifted = new Date(value.getTime() - value.getTimezoneOffset() * 60_000);
  return shifted.toISOString().slice(0, 16);
}

export function clientMonitorWindow(
  range: ClientMonitorRange,
  customFrom: string,
  customTo: string,
  now = new Date(),
): RegistrationMonitorWindow | null {
  if (range === "custom") {
    const from = new Date(customFrom);
    const to = new Date(customTo);
    if (!customFrom || !customTo || Number.isNaN(from.getTime()) || Number.isNaN(to.getTime()) || from >= to) return null;
    return { from: from.toISOString(), to: to.toISOString() };
  }
  return {
    from: new Date(now.getTime() - RANGE_MILLISECONDS[range]).toISOString(),
    to: now.toISOString(),
  };
}

function formatNumber(value: number): string {
  return new Intl.NumberFormat("zh-CN").format(value);
}

function formatTime(value: string | null): string {
  return value ? new Date(value).toLocaleString("zh-CN", { hour12: false }) : "—";
}

function formatDuration(value: number | null): string {
  if (value === null) return "—";
  if (value < 60) return `${value.toFixed(1)} 秒`;
  return `${Math.floor(value / 60)}分 ${Math.round(value % 60)}秒`;
}

export const CLIENT_HEALTH_LABELS: Record<RegistrationMonitorHealth, string> = {
  NORMAL: "正常",
  ATTENTION: "需关注",
  ABNORMAL: "异常",
  NO_ACTIVITY: "无作业",
};

export function ClientMonitorView({ api, autoRefresh, refreshToken, onCopy }: ClientMonitorViewProps) {
  const now = useMemo(() => new Date(), []);
  const [range, setRange] = useState<ClientMonitorRange>("10m");
  const [customFrom, setCustomFrom] = useState(toLocalInput(new Date(now.getTime() - 10 * 60_000)));
  const [customTo, setCustomTo] = useState(toLocalInput(now));
  const [health, setHealth] = useState<RegistrationMonitorHealth | "">("");
  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(0);
  const [data, setData] = useState<RegistrationClientListResponse | null>(null);
  const [selectedClient, setSelectedClient] = useState<RegistrationClient | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const requestSequence = useRef(0);

  const load = useCallback(async (quiet = false) => {
    const window = clientMonitorWindow(range, customFrom, customTo);
    if (!window) {
      setError("自定义时段无效，请确认开始时间早于结束时间");
      setLoading(false);
      return;
    }
    const sequence = ++requestSequence.current;
    if (!quiet) setLoading(true);
    try {
      const result = await api.getRegistrationClients({
        ...window,
        health,
        search,
        limit: PAGE_SIZE,
        offset: page * PAGE_SIZE,
      });
      if (sequence !== requestSequence.current) return;
      setData(result);
      setError("");
      setSelectedClient((current) => {
        if (!current) return null;
        return result.items.find((item) => item.client_id === current.client_id) ?? current;
      });
    } catch (cause) {
      if (sequence !== requestSequence.current) return;
      setError(cause instanceof Error ? cause.message : "客户端监控读取失败");
    } finally {
      if (sequence === requestSequence.current) setLoading(false);
    }
  }, [api, customFrom, customTo, health, page, range, search]);

  useEffect(() => { void load(); }, [load, refreshToken]);
  useEffect(() => {
    if (!autoRefresh) return;
    const timer = window.setInterval(() => void load(true), 15_000);
    return () => window.clearInterval(timer);
  }, [autoRefresh, load]);

  const summary = data?.summary ?? EMPTY_SUMMARY;
  const pageCount = Math.max(1, Math.ceil((data?.total ?? 0) / PAGE_SIZE));
  const submitSearch = (event: FormEvent) => {
    event.preventDefault();
    setPage(0);
    setSearch(searchInput.trim());
  };
  const chooseRange = (value: ClientMonitorRange) => {
    setRange(value);
    setPage(0);
  };

  return (
    <div className="page client-monitor-page">
      <section className="client-monitor-hero reveal">
        <div>
          <span className="eyebrow">CLIENT REGISTRY / LIVE TELEMETRY</span>
          <h1>客户端监控</h1>
          <p>按客户端聚合母号邀请注册作业，快速定位连续失败、租约停滞与校验重试。</p>
        </div>
        <div className="client-monitor-hero__pulse"><Activity size={18} /><span>15 秒刷新</span><strong>{autoRefresh ? "LIVE" : "PAUSED"}</strong></div>
      </section>

      <section className="client-monitor-summary reveal reveal--delay-1" aria-label="客户端运行摘要">
        <article><span>独立客户端</span><strong>{formatNumber(summary.total_clients)}</strong><small>{formatNumber(summary.active_clients)} 个时段内活跃</small><Cpu size={19} /></article>
        <article className="is-healthy"><span>运行正常</span><strong>{formatNumber(summary.normal_clients)}</strong><small>{formatNumber(summary.succeeded)} 个任务成功</small><CheckCircle2 size={19} /></article>
        <article className="is-attention"><span>需关注</span><strong>{formatNumber(summary.attention_clients)}</strong><small>{formatNumber(summary.failed)} 个任务失败</small><AlertTriangle size={19} /></article>
        <article className="is-abnormal"><span>异常客户端</span><strong>{formatNumber(summary.abnormal_clients)}</strong><small>优先检查租约与连续失败</small><ShieldAlert size={19} /></article>
        <article><span>窗口作业</span><strong>{formatNumber(summary.jobs)}</strong><small>{formatNumber(summary.processing)} 个处理中</small><TimerReset size={19} /></article>
      </section>

      <section className="client-monitor-panel reveal reveal--delay-2">
        <header className="client-monitor-toolbar">
          <nav className="client-monitor-ranges" aria-label="客户端监控时段">
            {RANGE_OPTIONS.map((option) => <button key={option.value} type="button" className={range === option.value ? "is-active" : ""} onClick={() => chooseRange(option.value)}><span>{option.label}</span><small>{option.code}</small></button>)}
          </nav>
          <button type="button" className="client-monitor-refresh" onClick={() => void load()} disabled={loading} aria-label="刷新客户端监控"><RefreshCcw size={16} className={loading ? "spin" : ""} />刷新</button>
        </header>

        {range === "custom" && <div className="client-monitor-custom-range"><label><span>开始时间</span><input type="datetime-local" value={customFrom} onChange={(event) => { setCustomFrom(event.target.value); setPage(0); }} /></label><i>至</i><label><span>结束时间</span><input type="datetime-local" value={customTo} onChange={(event) => { setCustomTo(event.target.value); setPage(0); }} /></label></div>}

        <div className="client-monitor-filters">
          <form onSubmit={submitSearch}><Search size={16} /><input value={searchInput} onChange={(event) => setSearchInput(event.target.value)} placeholder="搜索客户端 ID 或后 8 位…" /><button type="submit">搜索</button></form>
          <div role="group" aria-label="客户端健康状态筛选">{HEALTH_FILTERS.map((option) => <button key={option.value} type="button" className={health === option.value ? "is-active" : ""} onClick={() => { setHealth(option.value); setPage(0); }}>{option.label}</button>)}</div>
          <span>匹配 {formatNumber(data?.total ?? 0)} 个客户端</span>
        </div>

        {error && <div className="client-monitor-error"><AlertTriangle size={17} /><span>{error}</span><button type="button" onClick={() => void load()}>重试</button></div>}

        <div className="client-monitor-table-wrap">
          <table className="client-monitor-table">
            <thead><tr><th>客户端</th><th>健康</th><th>最近活动</th><th>作业</th><th>成功 / 失败 / 处理中</th><th>成功率</th><th>平均耗时</th><th>最近错误</th></tr></thead>
            <tbody>
              {loading && !data ? <tr><td colSpan={8}><div className="client-monitor-empty"><RefreshCcw className="spin" />正在聚合客户端作业…</div></td></tr> : (data?.items.length ?? 0) === 0 ? <tr><td colSpan={8}><div className="client-monitor-empty"><Cpu />{summary.total_clients === 0 ? "暂无注册客户端" : "所选时段或筛选条件下没有客户端"}</div></td></tr> : data?.items.map((client) => {
                const reason = client.health_reasons[0]?.message;
                return <tr key={client.client_id} className={`health-${client.health.toLowerCase()}`}>
                  <td><div className="client-monitor-identity"><button type="button" onClick={() => setSelectedClient(client)}><Cpu size={17} /><span><strong>{client.display_name}</strong><small>{client.client_id}</small></span></button><button type="button" aria-label={`复制 ${client.display_name} 标识`} onClick={() => onCopy(client.client_id)}><Copy size={14} /></button></div></td>
                  <td><button type="button" className={`client-health-badge is-${client.health.toLowerCase()}`} title={reason ?? CLIENT_HEALTH_LABELS[client.health]} onClick={() => setSelectedClient(client)}><i />{CLIENT_HEALTH_LABELS[client.health]}</button>{reason && <small className="client-health-reason">{reason}</small>}</td>
                  <td><div className="client-monitor-time"><Clock3 size={14} /><span>{formatTime(client.last_activity_at)}</span></div></td>
                  <td><strong>{formatNumber(client.jobs)}</strong></td>
                  <td><div className="client-job-split"><b>{client.succeeded}</b><em>{client.failed}</em><span>{client.processing}</span></div></td>
                  <td><strong>{client.success_rate === null ? "—" : `${(client.success_rate * 100).toFixed(1)}%`}</strong></td>
                  <td>{formatDuration(client.average_duration_seconds)}</td>
                  <td>{client.latest_error_code ? <button type="button" className="client-latest-error" title={client.latest_error_message ?? client.latest_error_code} onClick={() => setSelectedClient(client)}><AlertTriangle size={13} /><span>{client.latest_error_code}</span></button> : <span className="client-monitor-muted">—</span>}</td>
                </tr>;
              })}
            </tbody>
          </table>
        </div>

        <footer className="client-monitor-pagination"><span>第 {page + 1} / {pageCount} 页</span><div><button type="button" disabled={page === 0} onClick={() => setPage((value) => Math.max(0, value - 1))}><ChevronLeft size={16} />上一页</button><button type="button" disabled={page + 1 >= pageCount} onClick={() => setPage((value) => value + 1)}>下一页<ChevronRight size={16} /></button></div></footer>
      </section>

      {selectedClient && data?.window && <ClientMonitorDrawer client={selectedClient} window={data.window} api={api} onClose={() => setSelectedClient(null)} onCopy={onCopy} />}
    </div>
  );
}
