import { AlertTriangle, CheckCircle2, RefreshCcw } from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";

import type { RegistrationPoolSettings, Space } from "./types";

export function registrationSettingsPayload(
  settings: RegistrationPoolSettings | null,
  targetSpaceUuid: string,
  defaultMaxConcurrency: number,
) {
  return {
    target_space_uuid: targetSpaceUuid || null,
    default_max_concurrency: defaultMaxConcurrency,
    expected_version: settings?.version ?? 0,
  };
}

export function RegistrationSettings({ settings, spaces, onSave }: {
  settings: RegistrationPoolSettings | null;
  spaces: Space[];
  onSave: (payload: { target_space_uuid: string | null; default_max_concurrency: number; expected_version: number }) => Promise<void>;
}) {
  const [spaceUuid, setSpaceUuid] = useState(settings?.target_space_uuid ?? "");
  const [concurrency, setConcurrency] = useState(settings?.default_max_concurrency ?? 3);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    setSpaceUuid(settings?.target_space_uuid ?? "");
    setConcurrency(settings?.default_max_concurrency ?? 3);
  }, [settings]);
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    try {
      await onSave(registrationSettingsPayload(settings, spaceUuid, concurrency));
    } finally {
      setBusy(false);
    }
  };
  return <section className="registration-settings">
    <header><div><span>REGISTRATION POOL</span><h3>注册账号入池设置</h3></div>{settings?.promotion_available ? <em className="is-ok"><CheckCircle2 size={14} />空间可用</em> : <em className="is-warning"><AlertTriangle size={14} />空间不可用</em>}</header>
    <form onSubmit={(event) => void submit(event)}><label><span>固定目标空间</span><select value={spaceUuid} onChange={(event) => setSpaceUuid(event.target.value)}><option value="">暂停加入账号池</option>{spaces.map((space) => <option key={space.space_uuid} value={space.space_uuid} disabled={space.status !== "ACTIVE"}>{space.name} · {space.status}</option>)}</select></label><label><span>默认最大并发</span><input type="number" min={1} max={100} value={concurrency} onChange={(event) => setConcurrency(Number(event.target.value))} /></label><button className="primary-button" type="submit" disabled={busy}>{busy ? <RefreshCcw className="spin" size={16} /> : <CheckCircle2 size={16} />}{busy ? "正在保存…" : "保存入池设置"}</button></form>
  </section>;
}
