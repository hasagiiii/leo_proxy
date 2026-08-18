import type { Task } from "./types";

export type TaskMediaKind = "audio" | "image" | "video";

export type TaskMedia = {
  url: string;
  thumbnailUrl: string | null;
  type: string | null;
  kind: TaskMediaKind;
};

const IMAGE_URL = /\.(?:avif|bmp|gif|heic|heif|jpe?g|png|svg|webp)(?:$|[?#])/i;
const VIDEO_URL = /\.(?:m4v|mkv|mov|mp4|mpeg|mpg|ogv|webm)(?:$|[?#])/i;
const AUDIO_URL = /\.(?:aac|flac|m4a|mp3|oga|ogg|opus|wav)(?:$|[?#])/i;

function stringField(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value : null;
}

export function taskMediaKindOf(task: Pick<Task, "model" | "task_type">): TaskMediaKind {
  const taskType = task.task_type.toLowerCase();
  if (taskType.includes("image")) return "image";
  if (taskType.includes("audio")) return "audio";
  if (taskType.includes("video")) return "video";
  if (task.model.toLowerCase().includes("audio")) return "audio";
  return task.model.toLowerCase().includes("image") ? "image" : "video";
}

function mediaKindOf(type: string | null, url: string, fallback: TaskMediaKind): TaskMediaKind {
  const normalizedType = type?.toLowerCase() ?? "";
  if (normalizedType.startsWith("image/")) return "image";
  if (normalizedType.startsWith("audio/")) return "audio";
  if (normalizedType.startsWith("video/")) return "video";
  if (url.toLowerCase().startsWith("data:image/")) return "image";
  if (url.toLowerCase().startsWith("data:audio/")) return "audio";
  if (url.toLowerCase().startsWith("data:video/")) return "video";
  if (IMAGE_URL.test(url)) return "image";
  if (AUDIO_URL.test(url)) return "audio";
  if (VIDEO_URL.test(url)) return "video";
  return fallback;
}

export function taskMediaOf(task: Task): TaskMedia | null {
  if (!task.output) return null;
  const media = Array.isArray(task.output.media) ? task.output.media : [];
  const firstMedia = media.find(
    (item): item is Record<string, unknown> =>
      Boolean(item) && typeof item === "object" && Boolean(stringField((item as Record<string, unknown>).url)),
  );
  const url = stringField(firstMedia?.url) ?? stringField(task.output.url);
  if (!url) return null;

  const type = stringField(firstMedia?.type)
    ?? stringField(firstMedia?.content_type)
    ?? stringField(firstMedia?.mime_type)
    ?? stringField(task.output.type)
    ?? stringField(task.output.content_type)
    ?? stringField(task.output.mime_type);
  const thumbnailUrl = stringField(firstMedia?.thumbnail_url)
    ?? stringField(firstMedia?.gif_url)
    ?? stringField(task.output.thumbnail_url);

  return {
    url,
    thumbnailUrl,
    type,
    kind: mediaKindOf(type, url, taskMediaKindOf(task)),
  };
}
