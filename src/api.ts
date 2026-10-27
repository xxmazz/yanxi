export type ServerDocument = {
  id: string;
  title: string;
  filename: string;
  pages: number;
  status: "queued" | "ready" | "error";
  error: string;
  created: string;
  duplicate?: boolean;
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
