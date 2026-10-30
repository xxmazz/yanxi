import { useEffect, useState } from "react";
import { api } from "./api";
import type { ServerDocument, ParsedPage } from "./api";
import { TranslationReader } from "./translation";
import { ReadingLinks } from "./reading-links";
import { VisualStructure } from "./visual-structure";
import { PageStructure } from "./page-structure";

type ActionsProps = {
  document: ServerDocument;
  changed: () => void;
  notify: (text: string) => void;
};

export function DocumentActions({
  document: doc,
  changed,
  notify,
}: ActionsProps) {
  const [pending, setPending] = useState(false);
  const act = async (operation: string, body?: object) => {
    if (pending) return;
    setPending(true);
    try {
      await api(`/api/documents/${doc.id}/${operation}`, {
        method: "POST",
        ...(body
          ? {
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify(body),
            }
          : {}),
      });
      changed();
      notify(
        operation === "reindex"
          ? "已加入重新解析队列"
          : "文献状态已更新，原文件保留在服务器",
      );
    } catch (error) {
      notify(error instanceof Error ? error.message : "操作失败");
    } finally {
      setPending(false);
    }
  };
  const processing = doc.status === "queued" || doc.status === "processing";
  return (
    <div className="document-actions">
      {processing && (
        <div role="status">
          {doc.status === "queued"
            ? "排队中"
            : `已解析 ${doc.processed_pages} / ${doc.pages} 页 · OCR ${doc.ocr_pages} 页`}
          <progress max={doc.pages || 1} value={doc.processed_pages} />
        </div>
      )}
      {!!doc.warnings?.length && <p>{doc.warnings.join("；")}</p>}
      {doc.status === "review" && (
        <details open className="version-review">
          <summary>发现相似正文 · 请确认版本</summary>
          <p>待确认文献暂不参与问答。重合度是正文词组的估计重合比例。</p>
          {doc.similar_documents.map((match) => (
            <div className="version-candidate" key={match.id}>
              <b>{match.title}</b>
              <p>
                正文重合 {(match.overlap * 100).toFixed(1)}%
                {match.semantic_similarity !== null
                  ? ` · 语义余弦 ${match.semantic_similarity.toFixed(3)}`
                  : ""}
              </p>
              <p>{match.reason}</p>
              <div className="action-row">
                <button
                  disabled={pending}
                  onClick={() =>
                    void act("resolve", {
                      existing_id: match.id,
                      action: "keep_existing",
                    })
                  }
                >
                  保留库中版本
                  {match.recommendation === "keep_existing" ? "（建议）" : ""}
                </button>
                <button
                  disabled={pending}
                  onClick={() =>
                    void act("resolve", {
                      existing_id: match.id,
                      action: "replace_existing",
                    })
                  }
                >
                  使用新上传版本
                  {match.recommendation === "replace_existing"
                    ? "（建议）"
                    : ""}
                </button>
                <button
                  disabled={pending}
                  onClick={() =>
                    void act("resolve", {
                      existing_id: match.id,
                      action: "keep_both",
                    })
                  }
                >
                  两版都保留
                </button>
              </div>
            </div>
          ))}
        </details>
      )}
      {!processing && (
        <div className="action-row">
          <button disabled={pending} onClick={() => void act("reindex")}>
            重新解析
          </button>
          <button disabled={pending} onClick={() => void act("archive")}>
            移入归档
          </button>
          {!!doc.ocr_pages && <span>OCR {doc.ocr_pages} 页</span>}
          {!!doc.version && <span>arXiv v{doc.version}</span>}
        </div>
      )}
    </div>
  );
}

export function ArchivePanel({
  changed,
  notify,
}: Omit<ActionsProps, "document">) {
  const [docs, setDocs] = useState<ServerDocument[]>([]);
  const [opened, setOpened] = useState(false);
  const [pending, setPending] = useState(false);
  const load = async () => {
    try {
      const all = await api<ServerDocument[]>(
        "/api/documents?include_archived=true",
      );
      setDocs(all.filter((doc) => doc.trashed_at || !doc.is_active));
    } catch (error) {
      notify(error instanceof Error ? error.message : "加载失败");
    }
  };
  return (
    <details
      className="archive-panel"
      onToggle={(e) => {
        setOpened(e.currentTarget.open);
        if (e.currentTarget.open) void load();
      }}
    >
      <summary>归档与已替换版本</summary>
      {opened && (
        <>
          <p>原 PDF 和索引均保留，可恢复到文献库。</p>
          {!docs.length && <p>没有归档记录。</p>}
          {docs.map((doc) => (
            <div className="archive-item" key={doc.id}>
              <span>
                {doc.title} · {doc.trashed_at ? "手动归档" : "版本替换"}
              </span>
              <button
                disabled={pending}
                onClick={async () => {
                  setPending(true);
                  try {
                    await api(`/api/documents/${doc.id}/restore`, {
                      method: "POST",
                    });
                    await load();
                    changed();
                    notify("文献已恢复");
                  } catch (error) {
                    notify(error instanceof Error ? error.message : "恢复失败");
                  } finally {
                    setPending(false);
                  }
                }}
              >
                恢复
              </button>
            </div>
          ))}
        </>
      )}
    </details>
  );
}

export function ServerReader({
  documentId,
  title,
  pages,
}: {
  documentId: string;
  title: string;
  pages: number;
}) {
  const [sources, setSources] = useState<
    { arxiv_id: string; title: string; url: string; imported_at: string }[]
  >([]);
  useEffect(() => {
    let disposed = false;
    setSources([]);
    api<
      { arxiv_id: string; title: string; url: string; imported_at: string }[]
    >(`/api/documents/${documentId}/sources`)
      .then((value) => {
        if (!disposed) setSources(value);
      })
      .catch(() => {});
    return () => {
      disposed = true;
    };
  }, [documentId]);
  const [number, setNumber] = useState(1);
  const [mode, setMode] = useState<"original" | "translation">("original");
  const [page, setPage] = useState<ParsedPage | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    setNumber(1);
  }, [documentId]);
  useEffect(() => {
    let disposed = false;
    setPage(null);
    setError("");
    api<ParsedPage>(`/api/documents/${documentId}/pages/${number}`)
      .then((value) => {
        if (!disposed) setPage(value);
      })
      .catch((reason) => {
        if (!disposed) setError(reason.message);
      });
    return () => {
      disposed = true;
    };
  }, [documentId, number]);
  return (
    <section className="parsed-reader">
      {!!sources.length && (
        <details className="archive-panel">
          <summary>论文检索来源</summary>
          {sources.map((source) => (
            <p key={source.arxiv_id}>
              <a href={source.url} target="_blank" rel="noreferrer">
                arXiv:{source.arxiv_id} · {source.title}
              </a>{" "}
              · 导入记录 {source.imported_at} UTC
            </p>
          ))}
        </details>
      )}
      <div className="reader-pagination">
        <button
          aria-pressed={mode === "original"}
          onClick={() => setMode("original")}
        >
          原页与解析正文
        </button>
        <button
          aria-pressed={mode === "translation"}
          onClick={() => setMode("translation")}
        >
          中英对照翻译
        </button>
        <button disabled={number <= 1} onClick={() => setNumber((n) => n - 1)}>
          上一页
        </button>
        <label>
          物理页码{" "}
          <input
            aria-label="阅读物理页码"
            type="number"
            min="1"
            max={pages || 1}
            value={number}
            onChange={(e) =>
              setNumber(
                Math.max(1, Math.min(pages || 1, Number(e.target.value) || 1)),
              )
            }
          />{" "}
          / {pages}
        </label>
        <button
          disabled={number >= pages}
          onClick={() => setNumber((n) => n + 1)}
        >
          下一页
        </button>
      </div>
      {mode === "translation" ? (
        <TranslationReader
          key={documentId}
          documentId={documentId}
          number={number}
        />
      ) : (
        <div className="parsed-columns">
          <div className="page-image">
            <a
              href={`/api/documents/${documentId}/pdf#page=${number}`}
              target="_blank"
              rel="noreferrer"
            >
              打开原始 PDF · 第 {number} 页
            </a>
            <img
              key={`${documentId}-${number}`}
              alt={`${title} · 物理第 ${number} 页`}
              src={`/api/documents/${documentId}/render/${number}`}
            />
          </div>
          <article className="parsed-text">
            <h3>{page?.method === "ocr" ? "扫描页 · 本地 OCR" : "逐页正文"}</h3>
            <ReadingLinks
              documentId={documentId}
              page={page}
              jump={setNumber}
            />
            {error ? (
              <p role="status">{error}</p>
            ) : !page ? (
              <p>正在加载本页…</p>
            ) : (
              <>
                <p>
                  物理第 {number} 页 ·{" "}
                  {page.method === "ocr"
                    ? `${page.blocks.length} 个文字区域，点击查看坐标与识别置信度`
                    : "来自 PDF 文本层"}
                </p>
                {page.warning && <p role="status">{page.warning}</p>}
                <VisualStructure
                  key={`visual-${documentId}-${number}`}
                  documentId={documentId}
                  number={number}
                  pages={pages || 1}
                />
                <PageStructure
                  key={`${documentId}-${number}`}
                  documentId={documentId}
                  number={number}
                />

                {page.method === "ocr" &&
                  page.blocks.map((block, i) => (
                    <details className="ocr-block" key={i}>
                      <summary>{block.text}</summary>
                      <small>
                        OCR 置信度 {(block.confidence * 100).toFixed(1)}% ·
                        像素坐标{" "}
                        {block.bbox
                          .map((point) => point.map(Math.round).join(","))
                          .join(" / ")}
                      </small>
                    </details>
                  ))}
                {page.method === "ocr" && (
                  <p>
                    OCR 可能误识别公式、符号和阅读顺序，请结合左侧原页核对。
                  </p>
                )}
              </>
            )}
          </article>
        </div>
      )}
    </section>
  );
}
