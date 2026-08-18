import {
  AlertTriangle,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  Clock3,
  Copy,
  RefreshCcw,
  Search,
  X,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import type { VideoTaskApi } from "./api";
import type {
  ClientRegistrationTaskList,
  RegistrationClient,
  RegistrationClientDetailResponse,
  RegistrationMonitorWindow,
} from "./types";

const PAGE_SIZE = 50;
const STATUS_FILTERS = [
  ["", "全部"],
  ["PROCESSING", "处理中"],
  ["VALIDATING", "校验中"],
  ["SUCCEEDED", "成功"],
  ["FAILED", "失败"],
  ["STALLED", "疑似停滞"],
] as const;
const STATUS_LABELS: Record<string, string> = {
  RUNNING: "注册中",
  COOKIE_REPORTED: "Cookie 已收到",
  VALIDATING: "校验中",
  VALIDATION_RETRY_WAIT: "等待重试",
  VALIDATION_FAILED: "校验失败",
  FAILED: "注册失败",
  SUCCEEDED: "校验成功",
};
const CLIENT_HEALTH_LABELS = {
  NORMAL: "正常",
  ATTENTION: "需关注",
  ABNORMAL: "异常",
  NO_ACTIVITY: "无作业",
} as const;

type ClientMonitorDrawerProps = {
  client: RegistrationClient;
  window: RegistrationMonitorWindow;
  api: VideoTaskApi;
  onClose: () => void;
  onCopy: (value: string) => void;
};

function formatTime(value: string | null): string {
  return value ? new Date(value).toLocaleString("zh-CN", { hour12: false }) : "—";
}

function formatDuration(value: number | null): string {
  if (value === null) return "—";
  if (value < 60) return `${value.toFixed(1)} 秒`;
  return `${Math.floor(value / 60)}分 ${Math.round(value % 60)}秒`;
}

export function ClientMonitorDrawer({ client, window: monitorWindow, api, onClose, onCopy }: ClientMonitorDrawerProps) {
  const [detail, setDetail] = useState<RegistrationClientDetailResponse | null>(null);
  const [tasks, setTasks] = useState<ClientRegistrationTaskList | null>(null);
  const [status, setStatus] = useState("");
  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const requestSequence = useRef(0);

  const load = useCallback(async (quiet = false) => {
    const sequence = ++requestSequence.current;
    if (!quiet) setLoading(true);
    try {
      const [nextDetail, nextTasks] = await Promise.all([
        api.getRegistrationClientDetail(client.client_id, monitorWindow.from, monitorWindow.to),
        api.getRegistrationClientTasks(client.client_id, {
          ...monitorWindow,
          status,
          search,
          limit: PAGE_SIZE,
          offset: page * PAGE_SIZE,
        }),
      ]);
      if (sequence !== requestSequence.current) return;
      setDetail(nextDetail);
      setTasks(nextTasks);
      setError("");
    } catch (cause) {
      if (sequence !== requestSequence.current) return;
      setError(cause instanceof Error ? cause.message : "客户端任务详情读取失败");
    } finally {
      if (sequence === requestSequence.current) setLoading(false);
    }
  }, [api, client.client_id, monitorWindow, page, search, status]);

  useEffect(() => { void load(); }, [load]);
  useEffect(() => {
    const closeOnEscape = (event: KeyboardEvent) => event.key === "Escape" && onClose();
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [onClose]);

  const current = detail?.client ?? client;
  const pageCount = Math.max(1, Math.ceil((tasks?.total ?? 0) / PAGE_SIZE));
  const submitSearch = (event: FormEvent) => {
    event.preventDefault();
    setPage(0);
    setSearch(searchInput.trim());
  };

  return (
    <div className="client-monitor-drawer-backdrop" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <aside className="client-monitor-drawer" role="dialog" aria-modal="true" aria-label={`${current.display_name} 作业详情`}>
        <header className="client-monitor-drawer__header">
          <div><span>CLIENT WORKLOAD TRACE</span><h2>{current.display_name}</h2><button type="button" onClick={() => onCopy(current.client_id)}>{current.client_id}<Copy size={13} /></button></div>
          <div><button type="button" onClick={() => void load()} aria-label="刷新客户端详情"><RefreshCcw size={17} className={loading ? "spin" : ""} /></button><button type="button" onClick={onClose} aria-label="关闭客户端详情"><X size={19} /></button></div>
        </header>

        <section className="client-monitor-drawer__summary">
          <div><span>健康状态</span><strong className={`is-${current.health.toLowerCase()}`}>{CLIENT_HEALTH_LABELS[current.health]}</strong></div>
          <div><span>窗口作业</span><strong>{current.jobs}</strong></div>
          <div><span>成功 / 失败</span><strong>{current.succeeded} / {current.failed}</strong></div>
          <div><span>处理中</span><strong>{current.processing}</strong></div>
          <div><span>平均耗时</span><strong>{formatDuration(current.average_duration_seconds)}</strong></div>
        </section>

        {current.health_reasons.length > 0 && <div className="client-monitor-drawer__reasons">{current.health_reasons.map((reason) => <div key={reason.code}><AlertTriangle size={15} /><span><strong>{reason.code}</strong>{reason.message}</span></div>)}</div>}
        {error && <div className="client-monitor-error"><AlertTriangle size={16} /><span>{error}</span><button type="button" onClick={() => void load()}>重试</button></div>}

        <section className="client-monitor-chart" aria-label="客户端作业趋势">
          <header><div><span>WORKLOAD TIMELINE</span><h3>时段作业趋势</h3></div><small>{formatTime(monitorWindow.from)} — {formatTime(monitorWindow.to)}</small></header>
          <div className="client-monitor-chart__canvas">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={detail?.series ?? []} margin={{ top: 10, right: 12, left: -18, bottom: 0 }}>
                <defs><linearGradient id="clientClaimed" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="#c7f36b" stopOpacity={0.38} /><stop offset="100%" stopColor="#c7f36b" stopOpacity={0} /></linearGradient></defs>
                <CartesianGrid stroke="#2b352d" strokeDasharray="3 5" vertical={false} />
                <XAxis dataKey="at" tickFormatter={(value) => new Date(String(value)).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", hour12: false })} stroke="#6f796f" fontSize={10} />
                <YAxis allowDecimals={false} stroke="#6f796f" fontSize={10} />
                <Tooltip labelFormatter={(value) => formatTime(String(value))} contentStyle={{ background: "#121a14", border: "1px solid #344238", borderRadius: 8 }} />
                <Area type="monotone" dataKey="claimed" name="领取" stroke="#c7f36b" fill="url(#clientClaimed)" strokeWidth={2} />
                <Area type="monotone" dataKey="succeeded" name="成功" stroke="#6fd6c2" fill="transparent" strokeWidth={1.7} />
                <Area type="monotone" dataKey="failed" name="失败" stroke="#ff7a70" fill="transparent" strokeWidth={1.7} />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        </section>

        <section className="client-monitor-task-section">
          <header><div><span>TASK TRACE</span><h3>最近注册任务</h3></div><span>{tasks?.total ?? 0} 条</span></header>
          <div className="client-monitor-task-filters">
            <nav>{STATUS_FILTERS.map(([value, label]) => <button key={value} type="button" className={status === value ? "is-active" : ""} onClick={() => { setStatus(value); setPage(0); }}>{label}</button>)}</nav>
            <form onSubmit={submitSearch}><Search size={14} /><input value={searchInput} onChange={(event) => setSearchInput(event.target.value)} placeholder="任务 UUID、邮箱或母号…" /><button type="submit">搜索</button></form>
          </div>
          <div className="client-monitor-task-table-wrap">
            <table className="client-monitor-task-table">
              <thead><tr><th>任务 / 子号</th><th>状态</th><th>归属母号</th><th>时间线</th><th>积分 / 耗时</th><th>错误</th></tr></thead>
              <tbody>{loading && !tasks ? <tr><td colSpan={6}><div className="client-monitor-empty"><RefreshCcw className="spin" />读取任务详情…</div></td></tr> : (tasks?.items.length ?? 0) === 0 ? <tr><td colSpan={6}><div className="client-monitor-empty">暂无匹配任务</div></td></tr> : tasks?.items.map((task) => {
                const errorCode = task.client_error_code ?? task.validation_error_code;
                const errorMessage = task.client_error_message ?? task.validation_error_message;
                return <tr key={task.registration_uuid} className={task.stalled ? "is-stalled" : ""}>
                  <td><div><strong>{task.email}</strong><button type="button" onClick={() => onCopy(task.registration_uuid)}>{task.registration_uuid}<Copy size={12} /></button></div></td>
                  <td><span className={`client-task-status is-${task.status.toLowerCase()}`}>{task.stalled ? "疑似停滞" : STATUS_LABELS[task.status] ?? task.status}</span></td>
                  <td><strong>{task.parent_email}</strong></td>
                  <td><dl><div><dt>开始</dt><dd>{formatTime(task.started_at)}</dd></div><div><dt>心跳</dt><dd>{formatTime(task.last_heartbeat_at)}</dd></div><div><dt>上报</dt><dd>{formatTime(task.reported_at)}</dd></div><div><dt>完成</dt><dd>{formatTime(task.validation_finished_at)}</dd></div></dl></td>
                  <td><strong>{task.awarded_points ?? "—"}</strong><small><Clock3 size={12} />{formatDuration(task.duration_seconds)}</small></td>
                  <td>{errorCode ? <div className="client-task-error" title={errorMessage ?? errorCode}><AlertTriangle size={13} /><span><strong>{errorCode}</strong>{errorMessage}</span></div> : <span className="client-task-ok"><CheckCircle2 size={14} />无错误</span>}</td>
                </tr>;
              })}</tbody>
            </table>
          </div>
          <footer><span>第 {page + 1} / {pageCount} 页</span><div><button type="button" disabled={page === 0} onClick={() => setPage((value) => Math.max(0, value - 1))}><ChevronLeft size={15} />上一页</button><button type="button" disabled={page + 1 >= pageCount} onClick={() => setPage((value) => value + 1)}>下一页<ChevronRight size={15} /></button></div></footer>
        </section>
      </aside>
    </div>
  );
}
