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

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    signal: AbortSignal.timeout(180_000),
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || `服务器请求失败（${response.status}）`);
  }
  return response.json();
}
