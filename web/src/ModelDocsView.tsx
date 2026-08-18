import {
  ArrowUpRight,
  BookOpen,
  Braces,
  Check,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Clock3,
  Code2,
  Copy,
  ExternalLink,
  FileCode2,
  FileText,
  KeyRound,
  Layers3,
  RefreshCcw,
  Search,
  ShieldCheck,
  Terminal,
  X,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import type { VideoTaskApi } from "./api";
import type { ModelCatalogResponse } from "./types";

type ModeName = "text-to-video" | "image-to-video" | "reference-to-video";
type CodeLanguage = "curl" | "javascript" | "python";

interface ModelDocsViewProps {
  api: VideoTaskApi;
  apiBase: string;
  onCopy: (value: string) => void;
}

const MODE_LABELS: Record<ModeName, { index: string; label: string; hint: string }> = {
  "text-to-video": { index: "01", label: "文生视频", hint: "Prompt → Video" },
  "image-to-video": { index: "02", label: "图生视频", hint: "Start / End Frame" },
  "reference-to-video": { index: "03", label: "参考生视频", hint: "Image / Audio Refs" },
};

const LANGUAGE_LABELS: Record<CodeLanguage, string> = {
  curl: "cURL",
  javascript: "JavaScript",
  python: "Python",
};

const MODE_PAYLOADS: Record<ModeName, Record<string, unknown>> = {
  "text-to-video": {
    provider: "leonardo",
    task_type: "VIDEO_GENERATION",
    model: "hailuo-03",
    mode: "text-to-video",
    input: {
      prompt: "A white kitten chases a butterfly across a sunlit garden.",
      duration: 5,
      resolution: "2K",
      aspect_ratio: "16:9",
    },
  },
  "image-to-video": {
    provider: "leonardo",
    task_type: "VIDEO_GENERATION",
    model: "hailuo-03",
    mode: "image-to-video",
    input: {
      prompt: "A slow cinematic camera orbit with natural movement.",
      duration: 5,
      resolution: "2K",
      image_url: "https://cdn.example.com/start-frame.jpg",
      end_image_url: "https://cdn.example.com/end-frame.jpg",
    },
  },
  "reference-to-video": {
    provider: "leonardo",
    task_type: "VIDEO_GENERATION",
    model: "hailuo-03",
    mode: "reference-to-video",
    input: {
      prompt: "Preserve the subject identity and follow the reference pacing.",
      duration: 10,
      resolution: "2K",
      aspect_ratio: "16:9",
      reference_image_urls: ["https://cdn.example.com/character.png"],
      reference_audio_urls: [],
      reference_video_urls: [],
    },
  },
};

const STATUS_FLOW = [
  ["QUEUED", "请求已持久化"],
  ["RESOLVING_MEDIA", "解析并上传网络媒体"],
  ["SUBMITTING", "组装并提交上游请求"],
  ["RUNNING", "第三方正在生成"],
  ["COMPLETED", "结果地址已写回"],
] as const;

const PARAMETERS = [
  ["prompt", "string", "必填", "1–7000 字符，描述画面、动作与镜头。"],
  ["duration", "integer", "可选", "5–15 秒，默认 5。"],
  ["resolution", "string", "可选", "当前落地值为 2K。"],
  ["aspect_ratio", "string", "按模式", "21:9 / 16:9 / 4:3 / 1:1 / 3:4 / 9:16。"],
  ["image_url", "URL", "图生必填", "首帧公网 HTTP(S) 地址。"],
  ["end_image_url", "URL", "可选", "尾帧公网 HTTP(S) 地址。"],
  ["reference_image_urls", "URL[]", "参考生", "有序角色或画面参考列表。"],
  ["reference_audio_urls", "URL[]", "参考生", "音频参考列表，随任务分配后转 Media ID。"],
] as const;

const RELATED_DOCS = [
  { title: "Seed Audio 1.0", meta: "AUDIO · REQUEST GUIDE", description: "文字转语音、页面音色与多条输出接入。", file: "seed-audio-1-request-guide.md" },
  { title: "Gemini Omni Flash", meta: "OMNI · REQUEST GUIDE", description: "图像、视频、音频等多模态内容生成。", file: "gemini-omni-flash-request-guide.md" },
  { title: "Seedance 2.5", meta: "VIDEO · REQUEST GUIDE", description: "4–30 秒、双分辨率、30 图 + 10 视频 + 10 音频参考。", file: "seedance-2.5-request-guide.md" },
  { title: "Seedance 2.5 验收", meta: "VIDEO · LOCAL REPORT", description: "三模式、积分结算与余额门槛的 Compose mock 验收。", file: "seedance-2.5-local-verification.md" },
  { title: "Seedance 2.0", meta: "VIDEO · REQUEST GUIDE", description: "视频生成请求、媒体输入与任务轮询。", file: "seedance-2-request-guide.md" },
  { title: "Kling O3", meta: "VIDEO · REQUEST GUIDE", description: "多模式视频生成与请求字段说明。", file: "kling-video-o3-request-guide.md" },
  { title: "GPT Image 2", meta: "IMAGE · REQUEST GUIDE", description: "图片生成与编辑模式接入说明。", file: "gpt-image-2-request-guide.md" },
  { title: "Nano 2 / Pro", meta: "IMAGE · REQUEST GUIDE", description: "Nano 系列图片模型的统一请求契约。", file: "nano-image-request-guide.md" },
] as const;

const VEO_DOCS = [
  { title: "Veo 3.1", meta: "VIDEO · REQUEST GUIDE", description: "Veo 主模型生成模式与完整请求契约。", file: "veo-3.1-request-guide.md" },
  { title: "Veo 3.1 Fast", meta: "VIDEO · REQUEST GUIDE", description: "Veo Fast 视频生成请求与积分说明。", file: "veo-3.1-fast-request-guide.md" },
  { title: "Veo 3.1 Lite", meta: "VIDEO · REQUEST GUIDE", description: "文生、首尾帧、音频和多档清晰度请求。", file: "veo-3.1-lite-request-guide.md" },
] as const;

const SECTIONS = [
  ["quick-start", "快速开始"],
  ["endpoint-query", "任务查询"],
  ["parameters", "参数字段"],
  ["status-flow", "状态流转"],
  ["error-contract", "错误处理"],
  ["related-docs", "相关文档"],
] as const;

const COMPLETED_RESPONSE = {
  task_uuid: "4641529b-37a9-4a9d-bdf2-e318fa2ca698",
  upstream_task_id: "1f190d64-3730-6ad0-ab0d-0b93f35886d1",
  status: "COMPLETED",
  progress: { phase: "COMPLETED", resolved_assets: 2, total_assets: 2 },
  output: {
    provider: "leonardo",
    generation_id: "1f190d64-3730-6ad0-ab0d-0b93f35886d1",
    media: [{ type: "video/mp4", width: 1440, height: 1440, url: "https://cdn.example.com/result.mp4" }],
  },
};

export function ModelDocsView({ api, apiBase, onCopy }: ModelDocsViewProps) {
  const [mode, setMode] = useState<ModeName>("text-to-video");
  const [language, setLanguage] = useState<CodeLanguage>("curl");
  const [catalog, setCatalog] = useState<ModelCatalogResponse | null>(null);
  const [catalogLoading, setCatalogLoading] = useState(true);
  const [catalogError, setCatalogError] = useState<string | null>(null);
  const [catalogOpen, setCatalogOpen] = useState(false);
  const [catalogSearch, setCatalogSearch] = useState("");
  const [parameterSearch, setParameterSearch] = useState("");
  const [requiredOnly, setRequiredOnly] = useState(false);
  const [activeSection, setActiveSection] = useState<string>(SECTIONS[0][0]);
  const [copiedKey, setCopiedKey] = useState<string | null>(null);

  const displayBase = apiBase.replace(/\/+$/, "") || "http://127.0.0.1:18080";
  const requestObject = MODE_PAYLOADS[mode];
  const payload = useMemo(() => JSON.stringify(requestObject, null, 2), [requestObject]);
  const submitCode = useMemo(() => buildSubmitExample(language, displayBase, requestObject), [displayBase, language, requestObject]);
  const queryCurl = [
    "curl '" + displayBase + "/v1/tasks/4641529b-37a9-4a9d-bdf2-e318fa2ca698' \\",
    "  -H 'X-API-Key: YOUR_API_KEY'",
  ].join("\n");
  const acceptedResponse = useMemo(() => JSON.stringify({
    task_uuid: "4641529b-37a9-4a9d-bdf2-e318fa2ca698",
    model: "hailuo-03",
    mode,
    input_schema_version: "h3.v1",
    status: "QUEUED",
    progress: {
      phase: "QUEUED",
      resolved_assets: 0,
      total_assets: mode === "text-to-video" ? 0 : mode === "image-to-video" ? 2 : 1,
    },
  }, null, 2), [mode]);

  const visibleCatalog = useMemo(() => {
    const needle = catalogSearch.trim().toLowerCase();
    return (catalog?.items ?? []).filter((item) => !needle || item.title.toLowerCase().includes(needle) || item.model?.toLowerCase().includes(needle));
  }, [catalog, catalogSearch]);

  const visibleParameters = useMemo(() => {
    const needle = parameterSearch.trim().toLowerCase();
    return PARAMETERS.filter(([name, type, required, description]) => {
      const matchesSearch = !needle || [name, type, required, description].some((value) => value.toLowerCase().includes(needle));
      return matchesSearch && (!requiredOnly || required !== "可选");
    });
  }, [parameterSearch, requiredOnly]);

  const refreshCatalog = useCallback(async () => {
    setCatalogLoading(true);
    setCatalogError(null);
    try {
      setCatalog(await api.getModelCatalog());
    } catch (error) {
      setCatalogError(error instanceof Error ? error.message : "模型目录加载失败");
    } finally {
      setCatalogLoading(false);
    }
  }, [api]);

  const copyValue = useCallback((key: string, value: string) => {
    onCopy(value);
    setCopiedKey(key);
    window.setTimeout(() => setCopiedKey((current) => current === key ? null : current), 1800);
  }, [onCopy]);

  useEffect(() => {
    void refreshCatalog();
  }, [refreshCatalog]);

  useEffect(() => {
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setCatalogOpen(false);
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, []);

  useEffect(() => {
    const observer = new IntersectionObserver((entries) => {
      const visible = entries.filter((entry) => entry.isIntersecting).sort((a, b) => b.intersectionRatio - a.intersectionRatio)[0];
      if (visible?.target.id) setActiveSection(visible.target.id);
    }, { rootMargin: "-22% 0px -62% 0px", threshold: [0, 0.2, 0.6] });
    SECTIONS.forEach(([id]) => {
      const element = document.getElementById(id);
      if (element) observer.observe(element);
    });
    return () => observer.disconnect();
  }, []);

  return (
    <div className="page docs-page">
      <section className="docs-command" aria-labelledby="docs-title">
        <div className="docs-command__main">
          <div className="docs-command__eyebrow"><span>MODEL INTEGRATION</span><i />HAILUO H3</div>
          <div className="docs-command__identity">
            <span className="docs-live-dot" />
            <code>hailuo-03</code>
            <b>LIVE</b>
          </div>
          <h1 id="docs-title">模型接入文档</h1>
          <p>从鉴权、任务提交到结果读取，一条清晰、可复制、可追踪的 H3 视频生成接入路径。</p>
          <div className="docs-command__actions">
            <button className="primary-button" type="button" onClick={() => copyValue("hero", submitCode)}>
              {copiedKey === "hero" ? <Check size={17} /> : <Copy size={17} />}
              {copiedKey === "hero" ? "快速开始已复制" : "复制快速开始"}
            </button>
            <a className="secondary-button" href="/docs/viewer.html?doc=HAILUO_H3_API.md" target="_blank" rel="noreferrer">
              <FileText size={16} />打开完整文档
            </a>
          </div>
        </div>

        <div className="docs-command__side">
          <div className="docs-selector">
            <button className="docs-selector__trigger" type="button" aria-expanded={catalogOpen} aria-controls="model-catalog-panel" onClick={() => setCatalogOpen((open) => !open)}>
              <span><Layers3 size={17} /><i>当前接入手册</i><strong>MiniMax H3</strong></span>
              <ChevronDown className={catalogOpen ? "is-open" : ""} size={18} />
            </button>
            {catalogOpen && (
              <div className="docs-selector__panel" id="model-catalog-panel" role="dialog" aria-label="实时模型目录">
                <header>
                  <div><span>LIVE MODEL CATALOG</span><strong>浏览实时模型</strong></div>
                  <button type="button" aria-label="关闭模型目录" onClick={() => setCatalogOpen(false)}><X size={17} /></button>
                </header>
                <label className="docs-selector__search"><Search size={16} /><input value={catalogSearch} onChange={(event) => setCatalogSearch(event.target.value)} placeholder="搜索模型名称或 slug" /></label>
                <div className="docs-selector__list">
                  {catalogLoading && <div className="docs-selector__state"><RefreshCcw className="spin" size={16} />正在同步模型目录…</div>}
                  {catalogError && <div className="docs-selector__state is-error"><ShieldCheck size={16} />{catalogError}</div>}
                  {!catalogLoading && !catalogError && visibleCatalog.length === 0 && <div className="docs-selector__state">没有匹配的模型</div>}
                  {!catalogLoading && visibleCatalog.map((item) => (
                    <a href={item.url || "#quick-start"} target={item.url ? "_blank" : undefined} rel={item.url ? "noreferrer" : undefined} key={item.id}>
                      <span>{String(item.rank).padStart(2, "0")}</span>
                      <div><strong>{item.title}</strong><code>{item.model || "model slug pending"}</code></div>
                      <ArrowUpRight size={15} />
                    </a>
                  ))}
                </div>
                <footer><span>{catalog?.total ?? 0} MODELS</span><button type="button" disabled={catalogLoading} onClick={() => void refreshCatalog()}><RefreshCcw className={catalogLoading ? "spin" : ""} size={14} />同步目录</button></footer>
              </div>
            )}
          </div>

          <dl className="docs-capabilities" aria-label="模型能力摘要">
            <div><dt>SCHEMA</dt><dd>h3.v1</dd></div>
            <div><dt>OUTPUT</dt><dd>video/mp4</dd></div>
            <div><dt>DURATION</dt><dd>5–15 sec</dd></div>
            <div><dt>AUTH</dt><dd>X-API-Key</dd></div>
          </dl>
          <div className="docs-command__path"><span>01</span><i /><span>02</span><i /><span>03</span><strong>AUTH · SUBMIT · POLL</strong></div>
        </div>
      </section>

      <nav className="docs-section-nav" aria-label="本页章节">
        <span>ON THIS PAGE</span>
        <div>{SECTIONS.map(([id, label], index) => <a href={`#${id}`} aria-current={activeSection === id ? "location" : undefined} key={id}><i>{String(index + 1).padStart(2, "0")}</i>{label}</a>)}</div>
      </nav>

      <main className="docs-content">
        <section className="docs-reading-section docs-quickstart" id="quick-start">
          <SectionHeading index="01" eyebrow="CREATE TASK" title="快速开始" description="选择生成模式和代码语言，复制一段完整请求即可创建异步任务。" />

          <div className="docs-steps" aria-label="接入步骤">
            {[
              [KeyRound, "准备密钥", "X-API-Key"],
              [Terminal, "提交任务", "POST /v1/tasks"],
              [RefreshCcw, "轮询结果", "GET /v1/tasks/{id}"],
            ].map(([Icon, title, detail], index) => {
              const StepIcon = Icon as typeof KeyRound;
              return <div key={String(title)}><span><StepIcon size={17} /></span><i>{String(index + 1).padStart(2, "0")}</i><strong>{String(title)}</strong><code>{String(detail)}</code></div>;
            })}
          </div>

          <EndpointBar method="POST" path="/v1/tasks" label="创建视频生成任务" />

          <div className="docs-workbench">
            <div className="docs-workbench__toolbar">
              <div className="docs-mode-switch" role="tablist" aria-label="生成模式">
                {(Object.keys(MODE_LABELS) as ModeName[]).map((name) => (
                  <button role="tab" aria-selected={mode === name} className={mode === name ? "is-active" : ""} onClick={() => setMode(name)} key={name}>
                    <i>{MODE_LABELS[name].index}</i><span><strong>{MODE_LABELS[name].label}</strong><small>{MODE_LABELS[name].hint}</small></span>
                  </button>
                ))}
              </div>
              <div className="docs-language-switch" role="tablist" aria-label="代码语言">
                {(Object.keys(LANGUAGE_LABELS) as CodeLanguage[]).map((name) => <button role="tab" aria-selected={language === name} className={language === name ? "is-active" : ""} onClick={() => setLanguage(name)} key={name}>{LANGUAGE_LABELS[name]}</button>)}
              </div>
            </div>

            <div className="docs-example-grid">
              <CodePanel title={`${LANGUAGE_LABELS[language]} / POST`} code={submitCode} copyKey="submit" copiedKey={copiedKey} onCopy={copyValue} />
              <ResponsePanel status="202" title="Accepted" description="任务已持久化" code={acceptedResponse} />
            </div>
            <details className="docs-advanced">
              <summary><FileCode2 size={16} />查看原始 request.json <ChevronRight size={16} /></summary>
              <pre>{payload}</pre>
            </details>
          </div>
        </section>

        <section className="docs-reading-section" id="endpoint-query">
          <SectionHeading index="02" eyebrow="GET TASK" title="查询任务结果" description="使用内部 task_uuid 查询媒体处理进度、第三方任务 ID 和最终视频地址。" />
          <EndpointBar method="GET" path="/v1/tasks/{task_uuid}" label="读取任务状态" />
          <div className="docs-example-grid docs-example-grid--query">
            <CodePanel title="cURL / GET" code={queryCurl} copyKey="query" copiedKey={copiedKey} onCopy={copyValue} compact />
            <div className="docs-query-result">
              <div className="docs-query-result__meta"><CheckCircle2 size={19} /><div><span>TERMINAL RESPONSE</span><strong>COMPLETED</strong></div><i>200 OK</i></div>
              <pre>{JSON.stringify(COMPLETED_RESPONSE, null, 2)}</pre>
            </div>
          </div>
        </section>

        <section className="docs-reading-section" id="parameters">
          <SectionHeading index="03" eyebrow="INPUT CONTRACT" title="公共参数与媒体字段" description="媒体字段使用公网 HTTP(S) URL；Worker 在提交上游前完成下载、探测与上传。" />
          <div className="docs-table-toolbar">
            <label><Search size={16} /><input value={parameterSearch} onChange={(event) => setParameterSearch(event.target.value)} placeholder="搜索字段、类型或说明" /></label>
            <label className="docs-required-toggle"><input type="checkbox" checked={requiredOnly} onChange={(event) => setRequiredOnly(event.target.checked)} /><span />只看必填与模式字段</label>
            <em>{visibleParameters.length} / {PARAMETERS.length} FIELDS</em>
          </div>
          <div className="docs-table-wrap"><table className="docs-table"><thead><tr><th>字段</th><th>类型</th><th>要求</th><th>说明</th></tr></thead><tbody>{visibleParameters.map(([name, type, required, description]) => <tr key={name}><td><code>{name}</code></td><td>{type}</td><td><span>{required}</span></td><td>{description}</td></tr>)}</tbody></table></div>
          {visibleParameters.length === 0 && <div className="docs-table-empty">没有匹配字段。<button type="button" onClick={() => { setParameterSearch(""); setRequiredOnly(false); }}>清除筛选</button></div>}
        </section>

        <section className="docs-reading-section" id="status-flow">
          <SectionHeading index="04" eyebrow="STATE MACHINE" title="任务状态流转" description="建议每 3–10 秒查询一次；进入 COMPLETED 或 FAILED 后停止轮询。" />
          <ol className="docs-status-list">{STATUS_FLOW.map(([status, description], index) => <li key={status}><span>{String(index + 1).padStart(2, "0")}</span><i /><div><strong>{status}</strong><p>{description}</p></div></li>)}</ol>
        </section>

        <section className="docs-reading-section" id="error-contract">
          <SectionHeading index="05" eyebrow="FAILURE CONTRACT" title="错误与重试" description="HTTP 错误描述请求校验；任务级错误通过 error_code 与 error_message 返回。" />
          <div className="docs-error-grid">
            <article><span>422</span><strong>INPUT_VALIDATION</strong><p>字段、模式、URL scheme 或参数范围校验失败。</p><small>修正请求后重新提交</small></article>
            <article><span>409</span><strong>IDEMPOTENCY_CONFLICT</strong><p>相同幂等键对应了不同请求体。</p><small>复用原请求或更换幂等键</small></article>
            <article><span>TASK</span><strong>MEDIA_* / UPSTREAM_*</strong><p>媒体解析或第三方调用错误，详情随任务持久化。</p><small>根据 error_code 决定重试</small></article>
          </div>
        </section>

        <section className="docs-reading-section docs-related" id="related-docs">
          <div className="docs-related__heading"><div><span>RELATED GUIDES</span><h2>继续接入其他模型</h2><p>每个入口都保持独立，不再与当前 H3 快速开始重复。</p></div><BookOpen size={27} /></div>
          <div className="docs-related__grid">
            <RelatedDocCard data-doc="veo-3.1-guide-inline" doc={VEO_DOCS[0]} index={1} />
            <RelatedDocCard data-doc="veo-3.1-fast-guide-inline" doc={VEO_DOCS[1]} index={2} />
            <RelatedDocCard data-doc="veo-3.1-lite-guide-inline" doc={VEO_DOCS[2]} index={3} />
            {RELATED_DOCS.map((doc, index) => <RelatedDocCard doc={doc} index={index + 4} key={doc.file} />)}
          </div>
        </section>
      </main>
    </div>
  );
}

function SectionHeading({ index, eyebrow, title, description }: { index: string; eyebrow: string; title: string; description: string }) {
  return <div className="docs-section-heading"><span>{index}</span><div><small>{eyebrow}</small><h2>{title}</h2><p>{description}</p></div></div>;
}

function EndpointBar({ method, path, label }: { method: "POST" | "GET"; path: string; label: string }) {
  return <div className="endpoint-bar"><span className={`method-pill method-pill--${method.toLowerCase()}`}>{method}</span><code>{path}</code><small>{label}</small><i><ChevronRight size={17} /></i></div>;
}

function CodePanel({ title, code, copyKey, copiedKey, onCopy, compact = false }: { title: string; code: string; copyKey: string; copiedKey: string | null; onCopy: (key: string, value: string) => void; compact?: boolean }) {
  const copied = copiedKey === copyKey;
  return <div className={`docs-code ${compact ? "docs-code--compact" : ""}`}><div className="docs-code__head"><span><Code2 size={15} />{title}</span><button type="button" onClick={() => onCopy(copyKey, code)}>{copied ? <Check size={15} /> : <Copy size={15} />}{copied ? "已复制" : "复制"}</button></div><pre>{code}</pre><div className="docs-code__foot"><Braces size={14} /><span>UTF-8 / application/json</span><Clock3 size={14} /><span>ASYNC</span></div></div>;
}

function ResponsePanel({ status, title, description, code }: { status: string; title: string; description: string; code: string }) {
  return <div className="docs-response"><div className="docs-response__head"><span className="method-pill method-pill--response">{status}</span><strong>{title}</strong><small>{description}</small></div><pre>{code}</pre></div>;
}

function RelatedDocCard({ doc, index, "data-doc": dataDoc }: { doc: { title: string; meta: string; description: string; file: string }; index: number; "data-doc"?: string }) {
  return <a data-doc={dataDoc} href={`/docs/viewer.html?doc=${doc.file}`} target="_blank" rel="noreferrer">
    <span>{String(index).padStart(2, "0")}</span>
    <small>{doc.meta}</small>
    <strong>{doc.title}</strong>
    <p>{doc.description}</p>
    <i>查看文档 <ExternalLink size={14} /></i>
  </a>;
}

function buildSubmitExample(language: CodeLanguage, base: string, request: Record<string, unknown>) {
  const body = JSON.stringify(request);
  const prettyBody = JSON.stringify(request, null, 2);
  if (language === "javascript") {
    return [
      `const response = await fetch("${base}/v1/tasks", {`,
      '  method: "POST",',
      "  headers: {",
      '    "X-API-Key": "YOUR_API_KEY",',
      '    "Idempotency-Key": "h3-request-0001",',
      '    "Content-Type": "application/json",',
      "  },",
      `  body: JSON.stringify(${prettyBody.replace(/\n/g, "\n  ")}),`,
      "});",
      "",
      "const task = await response.json();",
    ].join("\n");
  }
  if (language === "python") {
    return [
      "import requests",
      "",
      `response = requests.post("${base}/v1/tasks",`,
      "    headers={",
      '        "X-API-Key": "YOUR_API_KEY",',
      '        "Idempotency-Key": "h3-request-0001",',
      "    },",
      `    json=${prettyBody.replace(/true/g, "True").replace(/false/g, "False").replace(/null/g, "None").replace(/\n/g, "\n    ")},`,
      ")",
      "task = response.json()",
    ].join("\n");
  }
  return [
    `curl -X POST '${base}/v1/tasks' \\`,
    "  -H 'X-API-Key: YOUR_API_KEY' \\",
    "  -H 'Idempotency-Key: h3-request-0001' \\",
    "  -H 'Content-Type: application/json' \\",
    `  --data '${body}'`,
  ].join("\n");
}
