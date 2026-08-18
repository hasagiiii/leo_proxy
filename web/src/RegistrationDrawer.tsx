import { AlertTriangle, CheckCircle2, RefreshCcw, ShieldCheck, X } from "lucide-react";
import { useEffect, useState } from "react";

import type {
  ParentAccount,
  RegistrationPoolSettings,
  RegistrationRecord,
} from "./types";

type RegistrationDrawerProps = {
  parent: ParentAccount;
  records: RegistrationRecord[];
  settings: RegistrationPoolSettings | null;
  loading: boolean;
  onClose: () => void;
  onFilter: (filter: string) => void;
  onRefresh: () => void;
  onRevalidate: (record: RegistrationRecord) => Promise<void>;
  onPromote: (record: RegistrationRecord) => Promise<void>;
};

const FILTERS = [
  ["", "全部"],
  ["VALIDATING", "校验中"],
  ["PROMOTABLE", "可入池"],
  ["FAILED", "失败"],
  ["PROMOTED", "已入池"],
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

export function RegistrationDrawer({
  parent,
  records,
  settings,
  loading,
  onClose,
  onFilter,
  onRefresh,
  onRevalidate,
  onPromote,
}: RegistrationDrawerProps) {
  const [filter, setFilter] = useState("");
  const [confirming, setConfirming] = useState<RegistrationRecord | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    const closeOnEscape = (event: KeyboardEvent) => event.key === "Escape" && onClose();
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [onClose]);

  const chooseFilter = (value: string) => {
    setFilter(value);
    onFilter(value);
  };
  const promote = async () => {
    if (!confirming) return;
    setBusy(true);
    try {
      await onPromote(confirming);
      setConfirming(null);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="registration-drawer-backdrop" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <aside className="registration-drawer" role="dialog" aria-modal="true" aria-label={`${parent.email} 注册记录`}>
        <header className="registration-drawer__header">
          <div><span>REGISTRATION RECORDS</span><h2>注册记录</h2><p>{parent.email}</p></div>
          <div><button type="button" onClick={onRefresh} aria-label="刷新注册记录"><RefreshCcw size={17} /></button><button type="button" onClick={onClose} aria-label="关闭注册记录"><X size={19} /></button></div>
        </header>
        <section className="registration-drawer__summary">
          <div><span>母号状态</span><strong>{parent.status === "EXHAUSTED" ? "已耗尽" : parent.status === "ACTIVE" ? "正常可用" : "手动停用"}</strong></div>
          <div><span>连续低于8000</span><strong>{parent.consecutive_150_count} / 3</strong></div>
          <div><span>运行中</span><strong>{parent.running_registration_count}</strong></div>
          <div><span>可入池</span><strong>{parent.promotable_registration_count}</strong></div>
        </section>
        {!settings?.promotion_available && <div className="registration-drawer__warning"><AlertTriangle size={17} /><span>固定目标空间未配置或不可用，已暂停加入账号池。</span></div>}
        <nav className="registration-drawer__filters" aria-label="注册状态筛选">
          {FILTERS.map(([value, label]) => <button key={value} type="button" className={filter === value ? "is-active" : ""} onClick={() => chooseFilter(value)}>{label}</button>)}
        </nav>
        <div className="registration-drawer__records">
          {loading ? <div className="registration-drawer__empty"><RefreshCcw className="spin" />正在读取注册记录…</div> : records.length === 0 ? <div className="registration-drawer__empty">暂无匹配注册记录</div> : records.map((record) => (
            <article key={record.registration_uuid} className="registration-record-card">
              <header><div><strong>{record.email}</strong><span>{record.client_id}</span></div><em>{STATUS_LABELS[record.status] ?? record.status}</em></header>
              <dl>
                <div><dt>后端积分</dt><dd>{record.awarded_points ?? "—"}</dd></div>
                <div><dt>Cookie 状态</dt><dd><ShieldCheck size={13} />{record.cookie_status === "VERIFIED" ? "Cookie 已验证" : record.cookie_status === "INVALID" ? "Cookie 无效" : record.cookie_status === "VALIDATING" ? "校验中" : `已收到 · ${record.cookie_count} 个`}</dd></div>
                <div><dt>上报时间</dt><dd>{record.reported_at ? new Date(record.reported_at).toLocaleString() : "—"}</dd></div>
              </dl>
              {(record.validation_error_code || record.validation_error_message) && <div className="registration-record-card__error"><AlertTriangle size={14} /><span><strong>{record.validation_error_code}</strong>{record.validation_error_message}</span></div>}
              <footer>
                {record.status === "VALIDATION_FAILED" && <button type="button" onClick={() => void onRevalidate(record)}><RefreshCcw size={14} />重新校验</button>}
                {record.promotable && <button type="button" className="is-primary" disabled={!settings?.promotion_available} onClick={() => setConfirming(record)}><CheckCircle2 size={14} />加入账号池</button>}
                {record.account_uuid && <span><CheckCircle2 size={14} />已加入</span>}
              </footer>
            </article>
          ))}
        </div>
        {confirming && <div className="registration-confirm">
          <div role="alertdialog" aria-modal="true" aria-label="确认加入账号池">
            <h3>确认加入账号池</h3>
            <p>以下信息由后端校验，确认后使用固定配置创建账号。</p>
            <dl><div><dt>注册邮箱</dt><dd>{confirming.verified_email}</dd></div><div><dt>后端积分</dt><dd>{confirming.awarded_points}</dd></div><div><dt>Cookie</dt><dd>Cookie 已验证</dd></div><div><dt>目标空间</dt><dd>{settings?.target_space_name ?? "未配置"}</dd></div><div><dt>最大并发</dt><dd>{settings?.default_max_concurrency ?? "—"}</dd></div></dl>
            <footer><button type="button" onClick={() => setConfirming(null)}>取消</button><button type="button" className="is-primary" disabled={busy} onClick={() => void promote()}>{busy ? "正在加入…" : "确认加入"}</button></footer>
          </div>
        </div>}
      </aside>
    </div>
  );
}
