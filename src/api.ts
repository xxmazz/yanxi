export type ServerDocument = {
  id: string;
  title: string;
  filename: string;
  pages: number;
  status: "queued" | "processing" | "review" | "ready" | "error";
  error: string;
  created: string;
  duplicate?: boolean;
  processed_pages: number;
  ocr_pages: number;
  authors: string;
  abstract: string;
  version: number;
  arxiv_id: string;
  is_active: number;
  trashed_at: string | null;
  warnings: string[];
  similar_documents: {
    id: string;
    title: string;
    overlap: number;
    semantic_similarity: number | null;
    version: number;
    recommendation: string;
    reason: string;
  }[];
};
export type ParsedPage = {
  page: number;
  text: string;
  method: "native" | "ocr";
  warning?: string;
  blocks: { text: string; bbox: number[][]; confidence: number }[];
};
export type Evidence = {
  id: string;
  document_id: string;
  title: string;
  page: number;
  quote: string;
  url: string;
};
export type Health = {
  ocr_ready: boolean;
  model_ready: boolean;
  embedding_ready: boolean;
  model: string | null;
  retrieval: string;
};

function errorDetail(value: unknown): string {
  if (typeof value === "string") return value;
  if (Array.isArray(value))
    return value.map(errorDetail).filter(Boolean).join("；");
  if (value && typeof value === "object") {
    const item = value as Record<string, unknown>;
    if (
      item.type === "too_long" &&
      Array.isArray(item.loc) &&
      item.loc.includes("document_ids")
    ) {
      const context = item.ctx as Record<string, unknown> | undefined;
      const maximum = context?.max_length;
      if (typeof maximum === "number")
        return `所选文献超过上限，每次最多选择 ${maximum} 个 PDF，请减少选择后重试。`;
    }
    return errorDetail(item.msg ?? item.message ?? item.detail);
  }
  return "";
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  headers.set("X-Yanxi-Request", "1");
  const response = await fetch(path, {
    ...init,
    headers,
    signal: AbortSignal.timeout(180_000),
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(
      errorDetail(body.detail) || `服务器请求失败（${response.status}）`,
    );
  }
  return response.json();
}
