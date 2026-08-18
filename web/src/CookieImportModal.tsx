import {
  AlertTriangle,
  CheckCircle2,
  Clock3,
  FileArchive,
  RefreshCcw,
  Upload,
  X,
} from "lucide-react";
import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type ChangeEvent,
  type DragEvent,
  type FormEvent,
} from "react";

import { ApiError, VideoTaskApi } from "./api";
import {
  COOKIE_IMPORT_MAX_BYTES,
  COOKIE_IMPORT_STAGE_LABELS,
  COOKIE_IMPORT_STATUS_LABELS,
  COOKIE_IMPORT_TERMINAL,
  cookieImportFileError,
  defaultCookieImportSpaceName,
  formatCookieImportBytes,
  newCookieImportIdempotencyKey,
} from "./cookieImport";
import type { CookieImportBatch, CookieImportStage } from "./types";

type CookieImportModalProps = {
  api: VideoTaskApi;
  onClose: () => void;
  onImported: (batch: CookieImportBatch) => void | Promise<void>;
};

const STAGES = Object.keys(COOKIE_IMPORT_STAGE_LABELS) as CookieImportStage[];

function formatNumber(value: number | null): string {
  return value === null ? "—" : new Intl.NumberFormat("zh-CN").format(value);
}

function formatDate(value: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN", { hour12: false });
}

function errorText(cause: unknown): string {
  if (cause instanceof ApiError) return cause.message;
  if (cause instanceof Error) return cause.message;
  return "Cookie ZIP 导入请求失败";
}

async function sha256(file: File): Promise<string> {
  if (typeof crypto === "undefined" || !crypto.subtle) return "浏览器未启用摘要计算";
  const digest = await crypto.subtle.digest("SHA-256", await file.arrayBuffer());
  return [...new Uint8Array(digest)].map((value) => value.toString(16).padStart(2, "0")).join("");
}

export function CookieImportBatchDetail({ batch }: { batch: CookieImportBatch }) {
  const furthestStage = batch.items.reduce(
    (highest, item) => Math.max(highest, STAGES.indexOf(item.stage)),
    batch.status === "QUEUED" ? 0 : -1,
  );
  const activeAccounts = batch.items.filter((item) => item.account_status === "ACTIVE").length;
  const failedItems = batch.items.filter((item) => item.status === "FAILED");
  const terminal = COOKIE_IMPORT_TERMINAL.has(batch.status);

  return (
    <section className="cookie-import-batch" aria-label={`Cookie 导入批次 ${batch.batch_uuid}`}>
      <header className="cookie-import-batch__head">
        <div>
          <span>{batch.space_name}</span>
          <strong>{batch.archive_filename}</strong>
          <small>{batch.batch_uuid}</small>
        </div>
        <em className={`cookie-import-status cookie-import-status--${batch.status.toLowerCase()}`}>{batch.status}</em>
      </header>

      {terminal && (
        <div className={`cookie-import-result ${activeAccounts ? "is-active" : "has-warning"}`}>
          {activeAccounts ? <CheckCircle2 size={18} /> : <AlertTriangle size={18} />}
          <div>
            <strong>{activeAccounts ? `已进入调度 · ${activeAccounts} 个 ACTIVE 账号` : "批次处理结束"}</strong>
            <span>新增 {batch.created} · 更新 {batch.updated} · 失败 {batch.failed}</span>
          </div>
        </div>
      )}

      <div className="cookie-import-stages" aria-label="导入处理阶段">
        {STAGES.map((stage, index) => (
          <div key={stage} className={index <= furthestStage ? "is-reached" : undefined}>
            <i>{index + 1}</i><span>{COOKIE_IMPORT_STAGE_LABELS[stage]}</span>
          </div>
        ))}
      </div>

      <div className="cookie-import-metrics">
        <div><span>条目</span><strong>{batch.item_count}</strong><small>等待 {batch.queued} / 运行 {batch.running}</small></div>
        <div><span>激活积分</span><strong>{formatNumber(batch.total_balance_credits)}</strong><small>当前账号水位</small></div>
        <div><span>作业观察</span><strong>作业 {batch.tasks_after_import}</strong><small>成功 {batch.completed_tasks_after_import} / 失败 {batch.failed_tasks_after_import}</small></div>
        <div><span>积分观察</span><strong>消耗 {formatNumber(batch.consumed_credits_after_import)}</strong><small>激活后的实际结算</small></div>
      </div>

      {failedItems.length > 0 && (
        <div className="cookie-import-errors">
          <AlertTriangle size={16} />
          <div><strong>{failedItems.length} 个条目处理失败</strong><span>{failedItems.map((item) => item.last_error_code || "UNKNOWN").join(" · ")}</span></div>
        </div>
      )}

      {batch.items.length > 0 && (
        <div className="cookie-import-table-wrap">
          <table className="cookie-import-table">
            <thead><tr><th>文件条目</th><th>账号</th><th>状态</th><th>处理阶段</th><th>积分</th><th>Token 到期</th><th>续签状态</th><th>错误码</th></tr></thead>
            <tbody>
              {batch.items.map((item) => (
                <tr key={item.item_uuid}>
                  <td><strong>{item.entry_name}</strong><small>{item.entry_sha256.slice(0, 12)}…</small></td>
                  <td><strong>{item.discovered_login_name || item.expected_login_name || "待识别"}</strong><small>{item.account_status || "—"}</small></td>
                  <td><span className={`cookie-import-item-status is-${item.status.toLowerCase()}`}>{COOKIE_IMPORT_STATUS_LABELS[item.status]}</span></td>
                  <td>{COOKIE_IMPORT_STAGE_LABELS[item.stage]}</td>
                  <td>{formatNumber(item.balance_credits)}</td>
                  <td>{formatDate(item.token_expires_at)}</td>
                  <td>{item.renewal_status || "—"}</td>
                  <td title={item.last_error_message || ""}>{item.last_error_code || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

export function CookieImportModal({ api, onClose, onImported }: CookieImportModalProps) {
  const [view, setView] = useState<"new" | "recent">("new");
  const [file, setFile] = useState<File | null>(null);
  const [spaceName, setSpaceName] = useState(() => defaultCookieImportSpaceName());
  const [idempotencyKey, setIdempotencyKey] = useState("");
  const [fileError, setFileError] = useState("");
  const [requestError, setRequestError] = useState("");
  const [hash, setHash] = useState("");
  const [hashing, setHashing] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [batch, setBatch] = useState<CookieImportBatch | null>(null);
  const [recent, setRecent] = useState<CookieImportBatch[]>([]);
  const [recentLoading, setRecentLoading] = useState(false);
  const notified = useRef(new Set<string>());

  const chooseFile = useCallback((nextFile: File | null) => {
    setRequestError("");
    setHash("");
    setFile(nextFile);
    if (!nextFile) {
      setFileError("");
      setIdempotencyKey("");
      return;
    }
    const validationError = cookieImportFileError(nextFile);
    setFileError(validationError);
    setIdempotencyKey(newCookieImportIdempotencyKey());
    if (validationError) return;
    setHashing(true);
    void sha256(nextFile)
      .then(setHash)
      .catch(() => setHash("摘要计算异常，服务端仍会复核"))
      .finally(() => setHashing(false));
  }, []);

  const loadRecent = useCallback(async () => {
    setRecentLoading(true);
    setRequestError("");
    try {
      const result = await api.listCookieImports(20, 0);
      setRecent(result.batches);
    } catch (cause) {
      setRequestError(errorText(cause));
    } finally {
      setRecentLoading(false);
    }
  }, [api]);

  useEffect(() => {
    if (view === "recent") void loadRecent();
  }, [loadRecent, view]);

  useEffect(() => {
    if (!batch) return undefined;
    if (COOKIE_IMPORT_TERMINAL.has(batch.status)) {
      if (!notified.current.has(batch.batch_uuid)) {
        notified.current.add(batch.batch_uuid);
        void onImported(batch);
      }
      return undefined;
    }
    let cancelled = false;
    let polling = false;
    const poll = async () => {
      if (polling) return;
      polling = true;
      try {
        const updated = await api.getCookieImport(batch.batch_uuid);
        if (!cancelled) setBatch(updated);
      } catch (cause) {
        if (!cancelled) setRequestError(errorText(cause));
      } finally {
        polling = false;
      }
    };
    const timer = window.setInterval(() => void poll(), 1500);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [api, batch, onImported]);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!file || fileError || !spaceName.trim() || !idempotencyKey) return;
    setUploading(true);
    setRequestError("");
    try {
      const created = await api.createCookieImport(file, spaceName.trim(), idempotencyKey);
      setBatch(created);
    } catch (cause) {
      setRequestError(errorText(cause));
    } finally {
      setUploading(false);
    }
  };

  const showBatch = async (summary: CookieImportBatch) => {
    setRequestError("");
    try {
      setBatch(await api.getCookieImport(summary.batch_uuid));
    } catch (cause) {
      setRequestError(errorText(cause));
    }
  };

  const drop = (event: DragEvent<HTMLLabelElement>) => {
    event.preventDefault();
    setDragging(false);
    chooseFile(event.dataTransfer.files.item(0));
  };

  const uploadDisabled = !file || Boolean(fileError) || !spaceName.trim() || !idempotencyKey || uploading;

  return (
    <div className="modal-backdrop" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <div className="modal modal--cookie-import" role="dialog" aria-modal="true" aria-labelledby="cookie-import-title">
        <div className="modal__header">
          <div><span>COOKIE ARCHIVE / 账号会话接入</span><h2 id="cookie-import-title">导入 Cookie ZIP</h2></div>
          <button type="button" className="icon-button" aria-label="关闭" onClick={onClose}><X size={19} /></button>
        </div>

        <div className="cookie-import-tabs" role="tablist">
          <button type="button" className={view === "new" ? "is-active" : undefined} onClick={() => setView("new")}>新建导入</button>
          <button type="button" className={view === "recent" ? "is-active" : undefined} onClick={() => setView("recent")}>最近批次</button>
        </div>

        {requestError && <div className="cookie-import-request-error"><AlertTriangle size={16} /><span>{requestError}</span></div>}

        {view === "new" && !batch && (
          <form className="cookie-import-form" onSubmit={(event) => void submit(event)}>
            <label
              className={`cookie-import-dropzone ${dragging ? "is-dragging" : ""} ${fileError ? "has-error" : ""}`}
              onDragEnter={(event) => { event.preventDefault(); setDragging(true); }}
              onDragOver={(event) => event.preventDefault()}
              onDragLeave={() => setDragging(false)}
              onDrop={drop}
            >
              <input
                type="file"
                accept=".zip,application/zip,application/x-zip-compressed"
                onChange={(event: ChangeEvent<HTMLInputElement>) => chooseFile(event.target.files?.item(0) ?? null)}
              />
              <FileArchive size={27} />
              <strong>{file ? file.name : "拖入 Cookie ZIP，或点击选择"}</strong>
              <span>{file ? formatCookieImportBytes(file.size) : `仅 ZIP · 最大 ${formatCookieImportBytes(COOKIE_IMPORT_MAX_BYTES)}`}</span>
            </label>

            {fileError && <div className="form-error"><AlertTriangle size={15} />{fileError}</div>}
            {file && !fileError && (
              <div className="cookie-import-file-meta">
                <span>SHA-256</span><code>{hashing ? "正在计算…" : hash || "等待计算"}</code>
              </div>
            )}

            <label className="cookie-import-space">
              <span>目标空间名称</span>
              <input value={spaceName} maxLength={128} onChange={(event) => setSpaceName(event.target.value)} placeholder="cookie-import-YYYYMMDD-HHmm" />
            </label>

            <div className="cookie-import-warning">
              <AlertTriangle size={17} />
              <span><strong>敏感会话仅用于服务端激活</strong>原始 ZIP 完成解析后即丢弃；有效会话先加密暂存，处理完成后清除密文。</span>
            </div>

            <div className="modal-actions">
              <button type="button" className="secondary-button" onClick={onClose}>取消</button>
              <button type="submit" className="primary-button" disabled={uploadDisabled}><Upload size={17} />{uploading ? "正在上传…" : "开始导入"}</button>
            </div>
          </form>
        )}

        {view === "recent" && !batch && (
          <div className="cookie-import-recent">
            <div className="cookie-import-recent__head"><span>最近 20 个批次</span><button type="button" onClick={() => void loadRecent()} disabled={recentLoading}><RefreshCcw size={14} className={recentLoading ? "spin" : ""} />刷新</button></div>
            {recentLoading && recent.length === 0 ? <div className="cookie-import-empty"><Clock3 size={18} />正在读取批次…</div> : recent.length === 0 ? <div className="cookie-import-empty"><FileArchive size={18} />暂无导入记录</div> : recent.map((item) => (
              <button type="button" className="cookie-import-recent__item" key={item.batch_uuid} onClick={() => void showBatch(item)}>
                <div><strong>{item.space_name}</strong><span>{item.archive_filename} · {formatDate(item.created_at)}</span></div>
                <div><em>{item.status}</em><small>新增 {item.created} / 更新 {item.updated} / 失败 {item.failed}</small></div>
              </button>
            ))}
          </div>
        )}

        {batch && (
          <div className="cookie-import-detail-view">
            <CookieImportBatchDetail batch={batch} />
            {!COOKIE_IMPORT_TERMINAL.has(batch.status) && <div className="cookie-import-polling"><RefreshCcw size={14} className="spin" />每 1.5 秒同步处理进度；关闭窗口后服务端作业继续执行。</div>}
            <div className="modal-actions">
              <button type="button" className="secondary-button" onClick={() => { setBatch(null); setView("recent"); }}>返回批次列表</button>
              <button type="button" className="primary-button" onClick={onClose}>完成</button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
