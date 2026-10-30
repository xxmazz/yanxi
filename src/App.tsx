import { useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowDownToLine,
  ArrowRight,
  ArrowUp,
  ArrowUpRight,
  BookOpen,
  Check,
  CheckCheck,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  CircleHelp,
  Clock3,
  Columns2,
  FileText,
  FlaskConical,
  FolderOpen,
  GitBranch,
  Globe2,
  LayoutGrid,
  List,
  Loader2,
  MessageSquare,
  MoreHorizontal,
  Network,
  PanelLeftClose,
  Plus,
  Search,
  Settings2,
  ShieldCheck,
  Sparkles,
  Star,
  Trash2,
  Upload,
  X,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { readSections, seedPapers } from "./data";
import type { Paper, View } from "./data";
import { parseMultiChartCsv, radarError } from "./csv";
import { ChartFigure, type ChartType } from "./chart-figure";
import { fingerprintPdf } from "./pdf-fingerprint";
import { hasPdfHeader } from "./pdf-validation";
import { api } from "./api";
import { Discovery } from "./discovery";
import { CitationGraph } from "./citation-graph";
import { WritingWorkspace } from "./writing-workspace";
import { DocumentActions, ArchivePanel, ServerReader } from "./document-tools";
import type { Evidence, Health, ServerDocument } from "./api";

const nav: { id: View; label: string; icon: LucideIcon; hint?: string }[] = [
  { id: "library", label: "我的文献", icon: FolderOpen },
  { id: "reader", label: "沉浸阅读", icon: BookOpen },
  { id: "chat", label: "文献问答", icon: MessageSquare, hint: "AI" },
  { id: "discover", label: "探索发现", icon: Globe2 },
  { id: "graph", label: "引用图谱", icon: Network },
  { id: "write", label: "写作工坊", icon: FlaskConical },
];
const STORAGE = "yanxi-demo-library-v1";
const DEFAULT_CHART_CSV =
  "方法,得分\nBaseline,68.4\n+ 检索,75.2\n+ 重排序,79.6\n+ 引用校验,82.1";
// getRandomValues also works on an HTTP LAN preview; randomUUID requires a secure context.
function createId() {
  return Array.from(crypto.getRandomValues(new Uint8Array(16)), (value) =>
    value.toString(16).padStart(2, "0"),
  ).join("");
}
function loadPapers(): Paper[] {
  try {
    const value = JSON.parse(localStorage.getItem(STORAGE) || "null");
    if (
      Array.isArray(value) &&
      value.every(
        (p) =>
          typeof p.id === "string" &&
          typeof p.title === "string" &&
          typeof p.authors === "string" &&
          ["sage", "sand", "blue", "rose"].includes(p.color),
      )
    )
      return value;
  } catch {
    /* Keep a usable library if browser storage is unavailable. */
  }
  return seedPapers;
}
function download(
  name: string,
  text: string,
  type = "text/markdown;charset=utf-8",
) {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}
type Message = {
  id: string;
  role: "user" | "assistant";
  text: string;
  evidence?: Evidence[];
  steps?: string[];
};

export default function App() {
  const [view, setView] = useState<View>("library");
  const [papers, setPapers] = useState<Paper[]>(loadPapers);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState("全部文献");
  const [layout, setLayout] = useState<"grid" | "list">("grid");
  const [selected, setSelected] = useState<string[]>([]);
  const [currentId, setCurrentId] = useState("attention");
  const [modal, setModal] = useState<"upload" | "compare" | "settings" | null>(
    null,
  );
  const [toast, setToast] = useState("");
  const [menu, setMenu] = useState<string | null>(null);
  const [sort, setSort] = useState("最近添加");
  const [mobileNav, setMobileNav] = useState(false);
  const [urls, setUrls] = useState<Record<string, string>>({});
  const urlsRef = useRef<Record<string, string>>({});
  const searchRef = useRef<HTMLInputElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const modalRef = useRef<HTMLDivElement>(null);
  const mainRef = useRef<HTMLElement>(null);
  const [bilingual, setBilingual] = useState(true);
  const [readerSection, setReaderSection] = useState(0);
  const [question, setQuestion] = useState("");
  const [messages, setMessages] = useState<Message[]>([]);
  const [busy, setBusy] = useState(false);
  const [chatScope, setChatScope] = useState("all");
  const [conversation, setConversation] = useState<{
    scope: string;
    documentIds: string[];
  } | null>(null);
  const [health, setHealth] = useState<Health | null>(null);
  const [serverDocs, setServerDocs] = useState<ServerDocument[]>([]);
  const [refreshTick, setRefreshTick] = useState(0);
  const changed = () => setRefreshTick((n) => n + 1);
  const [importing, setImporting] = useState(false);
  const [writingTab, setWritingTab] = useState<"outline" | "chart">("outline");
  const [chartCsv, setChartCsv] = useState(DEFAULT_CHART_CSV);
  const [chartType, setChartType] = useState<ChartType>("bar");

  const active = papers.find((p) => p.id === currentId) || papers[0];
  const title = nav.find((n) => n.id === view)!.label;
  const notify = (message: string) => setToast(message);
  useEffect(() => {
    try {
      localStorage.setItem(STORAGE, JSON.stringify(papers));
    } catch {
      setToast("浏览器存储空间不足，当前操作仅保留在本次会话。");
    }
  }, [papers]);
  useEffect(() => {
    if (!toast) return;
    const timer = setTimeout(() => setToast(""), 3600);
    return () => clearTimeout(timer);
  }, [toast]);
  useEffect(
    () => () => {
      Object.values(urlsRef.current).forEach(URL.revokeObjectURL);
    },
    [],
  );
  useEffect(() => {
    let disposed = false;
    let refreshing = false;
    const refresh = async () => {
      if (refreshing) return;
      refreshing = true;
      try {
        const status = await api<Health>("/api/health");
        const docs = await api<ServerDocument[]>("/api/documents");
        if (disposed) return;
        setHealth(status);
        setServerDocs(docs);
        setPapers((items) => {
          const server = docs.map(
            (doc): Paper => ({
              id: doc.id,
              backendId: doc.id,
              backendStatus: doc.status,
              title: doc.title,
              authors:
                doc.authors ||
                (doc.status === "review"
                  ? "相似版本待确认"
                  : doc.status === "ready"
                    ? "服务器文献库"
                    : "正在解析与建索引"),
              year: new Date(doc.created + "Z").getFullYear(),
              venue: "服务器导入",
              tag:
                doc.status === "ready"
                  ? "已建立索引"
                  : doc.status === "review"
                    ? "版本待确认"
                    : doc.status === "error"
                      ? "需要处理"
                      : "解析中",
              color: "blue",
              abstract:
                doc.error ||
                doc.abstract ||
                (doc.status === "ready"
                  ? "已按页建立原文索引，可用于本地模型问答。"
                  : "PDF 已保存，正在后台提取正文。"),
              pages: doc.pages,
              date: doc.created.slice(0, 10),
              favorite:
                items.find((p) => p.backendId === doc.id)?.favorite || false,
              local: true,
              source: `/api/documents/${doc.id}/pdf`,
              filename: doc.filename,
            }),
          );
          const merged = [...server, ...items.filter((p) => !p.backendId)];
          return JSON.stringify(merged) === JSON.stringify(items)
            ? items
            : merged;
        });
      } catch {
        if (!disposed) setHealth(null);
      } finally {
        refreshing = false;
      }
    };
    void refresh();
    const timer = setInterval(refresh, 5000);
    return () => {
      disposed = true;
      clearInterval(timer);
    };
  }, [refreshTick]);
  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setModal(null);
        setMenu(null);
        setMobileNav(false);
      }
      if (
        event.key === "/" &&
        !(event.target instanceof HTMLInputElement) &&
        !(event.target instanceof HTMLTextAreaElement)
      ) {
        event.preventDefault();
        searchRef.current?.focus();
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, []);
  useEffect(() => {
    if (!modal) return;
    const previous = document.activeElement as HTMLElement | null;
    const dialog = modalRef.current;
    dialog
      ?.querySelector<HTMLElement>("button, input, select, textarea")
      ?.focus();
    const trap = (event: KeyboardEvent) => {
      if (event.key !== "Tab" || !dialog) return;
      const controls = Array.from(
        dialog.querySelectorAll<HTMLElement>(
          "button:not([disabled]), input, select, textarea, a[href]",
        ),
      );
      const first = controls[0],
        last = controls[controls.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last?.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first?.focus();
      }
    };
    document.addEventListener("keydown", trap);
    return () => {
      document.removeEventListener("keydown", trap);
      previous?.focus();
    };
  }, [modal]);
  const go = (next: View) => {
    setView(next);
    setQuery("");
    setMenu(null);
    setMobileNav(false);
    mainRef.current?.scrollTo({ top: 0 });
  };
  const openPaper = (paper: Paper) => {
    setCurrentId(paper.id);
    setReaderSection(0);
    go("reader");
  };
  const favorite = (id: string) =>
    setPapers((items) =>
      items.map((p) => (p.id === id ? { ...p, favorite: !p.favorite } : p)),
    );
  const removePaper = async (id: string) => {
    if (papers.find((p) => p.id === id)?.backendId) {
      try {
        await api(`/api/documents/${id}/archive`, { method: "POST" });
        changed();
        notify("已移入归档，可从归档记录恢复");
      } catch (error) {
        notify(error instanceof Error ? error.message : "操作失败");
      }
      setMenu(null);
      return;
    }
    setPapers((items) => items.filter((p) => p.id !== id));
    setSelected((items) => items.filter((x) => x !== id));
    if (urlsRef.current[id]) {
      URL.revokeObjectURL(urlsRef.current[id]);
      delete urlsRef.current[id];
      setUrls({ ...urlsRef.current });
    }
    setMenu(null);
    notify("已从本地文献库移除，可在设置中恢复示例文献。");
  };
  const importFiles = async (files: FileList | File[] | null) => {
    if (!files || importing) return;
    if (health) {
      setImporting(true);
      const notes: string[] = [];
      try {
        for (const file of Array.from(files)) {
          const form = new FormData();
          form.append("file", file);
          try {
            const doc = await api<ServerDocument>("/api/documents", {
              method: "POST",
              body: form,
            });
            notes.push(
              doc.duplicate
                ? `${file.name} 已在文献库中`
                : `${file.name} 已上传，正在后台建立索引`,
            );
          } catch (error) {
            notes.push(
              `${file.name}：${error instanceof Error ? error.message : "上传失败"}`,
            );
          }
        }
        setModal(null);
        changed();
        notify(notes.join("；"));
      } finally {
        setImporting(false);
      }
      return;
    }
    const added: Paper[] = [];
    const warnings: string[] = [];
    for (const file of Array.from(files)) {
      if (
        !file.name.toLowerCase().endsWith(".pdf") ||
        (file.type && file.type !== "application/pdf")
      ) {
        warnings.push(file.name + " 不是 PDF");
        continue;
      }
      if (file.size > 20 * 1024 * 1024) {
        warnings.push(file.name + " 超过 20 MB");
        continue;
      }
      const header = new Uint8Array(await file.slice(0, 1028).arrayBuffer());
      if (!hasPdfHeader(header)) {
        warnings.push(file.name + " 不是有效的 PDF 文件");
        continue;
      }
      const contentFingerprint = await fingerprintPdf(file);
      const duplicate = [...papers, ...added].find(
        (p) => p.contentFingerprint === contentFingerprint,
      );
      if (duplicate) {
        warnings.push(
          "内容重复：" + file.name + " 与「" + duplicate.title + "」一致",
        );
        continue;
      }
      const id = createId();
      urlsRef.current[id] = URL.createObjectURL(file);
      added.push({
        id,
        title: file.name.replace(/.pdf$/i, ""),
        authors: "本地文件 · 尚未解析",
        year: new Date().getFullYear(),
        venue: "本地导入",
        tag: "待解析",
        color: "blue",
        abstract:
          "已在浏览器内导入，可预览原始 PDF。正文解析、OCR 和语义去重需要后端支持。",
        pages: 0,
        date: new Date().toLocaleDateString("en-CA"),
        favorite: false,
        local: true,
        filename: file.name,
        contentFingerprint,
      });
    }
    setUrls({ ...urlsRef.current });
    if (added.length) setPapers((items) => [...added, ...items]);
    setModal(null);
    notify(
      [
        added.length
          ? "已导入 " + added.length + " 份 PDF，仅保存在本机浏览器。"
          : "",
        ...warnings,
      ]
        .filter(Boolean)
        .join("；") || "没有选择文件。",
    );
  };
  const filtered = papers
    .filter(
      (p) =>
        (filter !== "星标文献" || p.favorite) &&
        (filter !== "本地导入" || p.local) &&
        [p.title, p.authors, p.tag]
          .join(" ")
          .toLowerCase()
          .includes(query.toLowerCase()),
    )
    .sort((a, b) =>
      sort === "发表年份" ? b.year - a.year : b.date.localeCompare(a.date),
    );
  const ask = async (text = question) => {
    const prompt = text.trim();
    if (!prompt || busy) return;
    setQuestion("");
    setMessages((items) => [
      ...items,
      { id: createId(), role: "user", text: prompt },
    ]);
    setBusy(true);
    try {
      if (!health)
        throw new Error("无法连接文献后端，请从服务器演示地址打开后再试。");
      const scoped = papers.find((p) => p.id === chatScope);
      if (chatScope !== "all" && !scoped?.backendId)
        throw new Error(
          "这篇是示例或浏览器本地文件，请先上传 PDF 到服务器文献库。",
        );
      const result = await api<{
        answer: string;
        citations: Evidence[];
        steps: string[];
        insufficient_evidence: boolean;
      }>("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          question: prompt,
          document_ids: scoped?.backendId ? [scoped.backendId] : [],
          previous_document_ids:
            conversation?.scope === chatScope ? conversation.documentIds : [],
        }),
      });
      if (!result.insufficient_evidence && result.citations.length) {
        setConversation({
          scope: chatScope,
          documentIds: [
            ...new Set(result.citations.map((item) => item.document_id)),
          ].slice(0, 6),
        });
      } else {
        setConversation(null);
      }
      setMessages((items) => [
        ...items,
        {
          id: createId(),
          role: "assistant",
          text: result.answer,
          evidence: result.citations,
          steps: result.steps,
        },
      ]);
    } catch (error) {
      setConversation(null);
      setMessages((items) => [
        ...items,
        {
          id: createId(),
          role: "assistant",
          text: error instanceof Error ? error.message : "请求失败，请重试。",
        },
      ]);
    } finally {
      setBusy(false);
    }
  };
  const exportLibrary = () => {
    download(
      "研析-文献目录.json",
      JSON.stringify(
        papers.map(({ id, title, authors, year, source }) => ({
          id,
          title,
          authors,
          year,
          source,
        })),
        null,
        2,
      ),
      "application/json",
    );
    notify("文献目录已导出。");
  };

  return (
    <div className="app-shell">
      <aside className={"sidebar " + (mobileNav ? "mobile-open" : "")}>
        <button
          className="brand"
          onClick={() => go("library")}
          aria-label="研析首页"
        >
          <div className="brand-mark">
            <BookOpen size={23} strokeWidth={1.7} />
            <i />
          </div>
          <div>
            <strong>
              研析<span>YANXI</span>
            </strong>
            <small>科研文献智能助手</small>
          </div>
        </button>
        <button
          className="workspace-switch"
          onClick={() => setModal("settings")}
        >
          <span className="workspace-avatar">研</span>
          <span>
            我的研究空间<small>个人工作区</small>
          </span>
          <ChevronDown size={14} />
        </button>
        <div className="nav-caption">
          WORKSPACE <span>工作空间</span>
        </div>
        <nav>
          {nav.map((item) => (
            <button
              key={item.id}
              className={"nav-item " + (view === item.id ? "active" : "")}
              onClick={() => go(item.id)}
            >
              <item.icon size={19} strokeWidth={1.7} />
              <span>{item.label}</span>
              {item.hint && <small>{item.hint}</small>}
              {item.id === "library" && <em>{papers.length}</em>}
            </button>
          ))}
        </nav>
        <div className="sidebar-line" />
        <div className="nav-caption">
          COLLECTIONS <span>我的收藏夹</span>
        </div>
        <button
          className={
            "collection " +
            (filter === "星标文献" && view === "library" ? "selected" : "")
          }
          onClick={() => {
            go("library");
            setFilter("星标文献");
          }}
        >
          <span className="dot gold" />
          星标文献<span>{papers.filter((p) => p.favorite).length}</span>
        </button>
        <button
          className="collection"
          onClick={() => {
            go("library");
            setFilter("全部文献");
            setQuery("Transformer");
          }}
        >
          <span className="dot sage" />
          Transformer 研究
          <span>
            {
              papers.filter((p) =>
                (p.title + p.authors + p.tag)
                  .toLowerCase()
                  .includes("transformer"),
              ).length
            }
          </span>
        </button>
        <button
          className="collection"
          onClick={() => {
            go("library");
            setFilter("全部文献");
            setQuery("RAG");
          }}
        >
          <span className="dot blue" />
          检索增强生成
          <span>
            {
              papers.filter((p) =>
                (p.title + p.authors + p.tag).toLowerCase().includes("rag"),
              ).length
            }
          </span>
        </button>
        <div className="sidebar-bottom">
          <div className="demo-card">
            <div>
              <Sparkles size={16} />
              <b>从文献，到洞见。</b>
            </div>
            <p>每一个回答，都应该有出处。</p>
            <button onClick={() => setModal("settings")}>
              了解当前演示 <ArrowUpRight size={13} />
            </button>
          </div>
          <button
            className="utility-button"
            onClick={() => setModal("settings")}
          >
            <Settings2 size={17} />
            设置与帮助
            <CircleHelp size={16} />
          </button>
          <div className="profile">
            <div className="avatar">X</div>
            <span>
              xxmacc<small>个人研究者</small>
            </span>
            <span className="profile-dot" />
          </div>
        </div>
      </aside>
      <div className="main-shell">
        <header className="topbar">
          <div className="breadcrumb">
            <button
              className="icon-button sidebar-toggle"
              aria-label="展开导航"
              onClick={() => setMobileNav(!mobileNav)}
            >
              <PanelLeftClose size={19} />
            </button>
            <span>工作空间</span>
            <ChevronRight size={13} />
            <b>{title}</b>
          </div>
          <div className="topbar-right">
            <label className="global-search">
              <Search size={15} />
              <input
                ref={searchRef}
                aria-label="搜索文献"
                placeholder="搜索文献、作者、关键词…"
                value={query}
                onChange={(e) => {
                  setQuery(e.target.value);
                  if (view !== "library" && view !== "discover")
                    setView("library");
                }}
              />
              <kbd>/</kbd>
            </label>
            <span className="demo-pill">
              <i />
              {health?.model_ready ? "本地模型已连接" : "浏览器预览"}
            </span>
            <button
              className="icon-button"
              aria-label="帮助"
              onClick={() => setModal("settings")}
            >
              <CircleHelp size={19} />
            </button>
          </div>
        </header>
        <main className="main-content" ref={mainRef}>
          {view === "library" && (
            <>
              <div className="page-heading">
                <div>
                  <div className="eyebrow">文献档案 / LIBRARY</div>
                  <h1>
                    我的文献<span className="count-pill">{papers.length}</span>
                  </h1>
                  <p>原件、阅读与研究线索，集中整理。</p>
                </div>
                <button
                  className="button primary"
                  onClick={() => setModal("upload")}
                >
                  <Plus size={17} />
                  导入文献
                </button>
              </div>
              {health && <ArchivePanel changed={changed} notify={notify} />}
              <section className="welcome-banner" aria-label="文献工作流程">
                <div className="banner-copy">
                  <span className="banner-label">研析 · 文献工作台</span>
                  <h2>
                    从原文出发，
                    <br />
                    把研究线索留在案头。
                  </h2>
                  <p>归档论文，核对证据，再把想法写下来。</p>
                  <button onClick={() => go("chat")}>
                    带着问题阅读 <ArrowRight size={16} />
                  </button>
                </div>
                <ol className="banner-register" aria-label="研究流程">
                  <li>
                    <span>01</span>
                    <div>
                      <strong>收录原文</strong>
                      <small>PDF 解析 · 版本管理</small>
                    </div>
                    <FolderOpen size={18} />
                  </li>
                  <li>
                    <span>02</span>
                    <div>
                      <strong>核对出处</strong>
                      <small>原文问答 · 引用回看</small>
                    </div>
                    <BookOpen size={18} />
                  </li>
                  <li>
                    <span>03</span>
                    <div>
                      <strong>整理研究</strong>
                      <small>引用图谱 · 研究计划</small>
                    </div>
                    <GitBranch size={18} />
                  </li>
                </ol>
              </section>
              <div className="stats-row">
                <div>
                  <span className="stat-icon sage">
                    <FolderOpen size={18} />
                  </span>
                  <span>
                    文献总数
                    <strong>
                      {papers.length}
                      <small>篇</small>
                    </strong>
                  </span>
                  <em>你的知识积累</em>
                </div>
                <div>
                  <span className="stat-icon sand">
                    <Star size={18} />
                  </span>
                  <span>
                    星标文献
                    <strong>
                      {papers.filter((p) => p.favorite).length}
                      <small>篇</small>
                    </strong>
                  </span>
                  <em>值得反复阅读</em>
                </div>
                <div>
                  <span className="stat-icon blue">
                    <Network size={18} />
                  </span>
                  <span>
                    研究主题
                    <strong>
                      {new Set(papers.map((p) => p.tag)).size}
                      <small>个</small>
                    </strong>
                  </span>
                  <em>连接研究脉络</em>
                </div>
              </div>
              <div className="library-toolbar">
                <div className="tabs">
                  {["全部文献", "星标文献", "本地导入"].map((f) => (
                    <button
                      key={f}
                      className={filter === f ? "active" : ""}
                      onClick={() => {
                        setFilter(f);
                        setSelected([]);
                      }}
                    >
                      {f}
                      {f === "全部文献" && <span>{papers.length}</span>}
                    </button>
                  ))}
                </div>
                <div className="toolbar-actions">
                  <select
                    aria-label="文献排序"
                    value={sort}
                    onChange={(e) => setSort(e.target.value)}
                  >
                    <option>最近添加</option>
                    <option>发表年份</option>
                  </select>
                  <div className="layout-switch">
                    <button
                      aria-label="卡片视图"
                      className={layout === "grid" ? "active" : ""}
                      onClick={() => setLayout("grid")}
                    >
                      <LayoutGrid size={16} />
                    </button>
                    <button
                      aria-label="列表视图"
                      className={layout === "list" ? "active" : ""}
                      onClick={() => setLayout("list")}
                    >
                      <List size={17} />
                    </button>
                  </div>
                </div>
              </div>
              <div className="list-caption">
                <span>
                  {query
                    ? "找到 " + filtered.length + " 篇相关文献"
                    : "全部知识，从这里开始连接"}
                </span>
                <button onClick={exportLibrary}>
                  <ArrowDownToLine size={13} />
                  导出目录
                </button>
              </div>
              {selected.length > 0 && (
                <div className="selection-bar">
                  <CheckCheck size={16} />
                  <span>已选择 {selected.length} 篇文献</span>
                  <button onClick={() => setSelected([])}>取消选择</button>
                  <button
                    className="button primary small"
                    disabled={selected.length < 2}
                    onClick={() => setModal("compare")}
                  >
                    对比阅读 <Columns2 size={14} />
                  </button>
                </div>
              )}
              <div
                className={
                  "paper-grid " + (layout === "list" ? "list-layout" : "")
                }
              >
                {filtered.map((paper) => (
                  <article
                    className={
                      "paper-card " +
                      (selected.includes(paper.id) ? "is-selected" : "")
                    }
                    key={paper.id}
                  >
                    <div className="paper-card-top">
                      <span className={"paper-file " + paper.color}>
                        <FileText size={21} strokeWidth={1.5} />
                      </span>
                      <span className={"tag " + paper.color}>{paper.tag}</span>
                      <div className="card-actions">
                        <button
                          className={
                            "star-button " +
                            (paper.favorite ? "is-starred" : "")
                          }
                          aria-label={
                            (paper.favorite ? "取消星标 " : "星标 ") +
                            paper.title
                          }
                          onClick={() => favorite(paper.id)}
                        >
                          <Star
                            size={16}
                            fill={paper.favorite ? "currentColor" : "none"}
                          />
                        </button>
                        <button
                          className="icon-button"
                          aria-label={"更多操作 " + paper.title}
                          onClick={() =>
                            setMenu(menu === paper.id ? null : paper.id)
                          }
                        >
                          <MoreHorizontal size={18} />
                        </button>
                        {menu === paper.id && (
                          <div className="dropdown">
                            <button onClick={() => openPaper(paper)}>
                              <BookOpen size={14} />
                              打开阅读
                            </button>
                            {paper.source && (
                              <a
                                href={paper.source}
                                target="_blank"
                                rel="noreferrer"
                              >
                                <ArrowUpRight size={14} />
                                查看论文来源
                              </a>
                            )}
                            <button
                              className="danger"
                              onClick={() => removePaper(paper.id)}
                            >
                              <Trash2 size={14} />
                              移出文献库
                            </button>
                          </div>
                        )}
                      </div>
                    </div>
                    <button
                      className="paper-title"
                      onClick={() => openPaper(paper)}
                    >
                      {paper.title}
                    </button>
                    <div className="paper-authors">{paper.authors}</div>
                    <p className="paper-abstract">{paper.abstract}</p>
                    {paper.backendId &&
                      serverDocs.find((doc) => doc.id === paper.backendId) && (
                        <DocumentActions
                          document={
                            serverDocs.find(
                              (doc) => doc.id === paper.backendId,
                            )!
                          }
                          changed={changed}
                          notify={notify}
                        />
                      )}
                    <div className="paper-meta">
                      <span>
                        {paper.backendId ? `导入于 ${paper.year}` : paper.year}
                        <i />
                        {paper.venue}
                      </span>
                      <span>
                        {paper.backendId
                          ? "服务器 PDF"
                          : paper.local
                            ? "本地 PDF"
                            : "示例文献"}
                      </span>
                    </div>
                    <div className="paper-card-footer">
                      <label>
                        <input
                          type="checkbox"
                          aria-label={"选择 " + paper.title}
                          checked={selected.includes(paper.id)}
                          onChange={() =>
                            setSelected((items) =>
                              items.includes(paper.id)
                                ? items.filter((x) => x !== paper.id)
                                : [...items, paper.id],
                            )
                          }
                        />
                        <span>
                          {paper.backendId
                            ? `${paper.pages} 页 · ${paper.backendStatus === "ready" ? "可问答" : paper.backendStatus === "review" ? "版本待确认" : paper.backendStatus === "error" ? "解析失败" : "解析中"}`
                            : paper.local
                              ? "本地预览"
                              : "阅读样例"}
                        </span>
                      </label>
                      <button onClick={() => openPaper(paper)}>
                        开始阅读
                        <ArrowUpRight size={14} />
                      </button>
                    </div>
                  </article>
                ))}
              </div>
              {filtered.length === 0 && (
                <div className="empty-state">
                  <Search size={32} />
                  <h3>这里还没有文献</h3>
                  <p>
                    {query
                      ? "换个关键词，或者导入你的 PDF。"
                      : "导入一篇论文，开始建立你的知识脉络。"}
                  </p>
                  <button
                    className="button secondary"
                    onClick={() => setModal("upload")}
                  >
                    导入文献
                  </button>
                </div>
              )}
              <div className="bottom-note">
                <ShieldCheck size={14} />
                {health
                  ? "上传的 PDF 保存在你的服务器；索引完成后可用于本地模型问答。示例卡片用于体验界面。"
                  : "浏览器预览模式：本地 PDF 尚未上传服务器，不能用于模型问答。"}
              </div>
            </>
          )}
          {view === "reader" && (
            <>
              <div className="page-heading compact">
                <div>
                  <div className="eyebrow">READ WITH CONTEXT</div>
                  <h1>沉浸阅读</h1>
                  <p>让理解与证据，始终在同一个页面。</p>
                </div>
                {!active?.local && (
                  <button
                    className="button secondary"
                    onClick={() => setBilingual(!bilingual)}
                  >
                    <Columns2 size={16} />
                    {bilingual ? "中英对照" : "原文视图"}
                  </button>
                )}
              </div>
              {active ? (
                <>
                  <div className="reader-toolbar">
                    <select
                      aria-label="选择阅读文献"
                      value={active.id}
                      onChange={(e) => {
                        setCurrentId(e.target.value);
                        setReaderSection(0);
                      }}
                    >
                      {papers.map((p) => (
                        <option value={p.id} key={p.id}>
                          {p.title}
                        </option>
                      ))}
                    </select>
                    <button
                      className="button primary small"
                      onClick={() => {
                        setChatScope(active.id);
                        go("chat");
                      }}
                    >
                      <Sparkles size={14} />
                      问这篇论文
                    </button>
                  </div>
                  {active.backendId ? (
                    <ServerReader
                      key={active.backendId}
                      documentId={active.backendId}
                      title={active.title}
                      pages={active.pages}
                    />
                  ) : active.local ? (
                    <div className="local-preview">
                      {active.backendId || urls[active.id] ? (
                        <iframe
                          title={active.title}
                          src={
                            active.backendId ? active.source : urls[active.id]
                          }
                        />
                      ) : (
                        <div className="empty-state">
                          <FileText size={32} />
                          <h3>请重新选择本地 PDF</h3>
                          <p>文献目录会保留，文件内容不会写入浏览器存储。</p>
                          <button
                            className="button primary"
                            onClick={() => setModal("upload")}
                          >
                            重新关联文件
                          </button>
                        </div>
                      )}
                    </div>
                  ) : (
                    <div className="reader-layout">
                      <aside className="document-toc">
                        <span>文档大纲</span>
                        {readSections.map((section, i) => (
                          <button
                            key={section.title}
                            className={readerSection === i ? "active" : ""}
                            onClick={() => setReaderSection(i)}
                          >
                            {section.title}
                          </button>
                        ))}
                        <div className="info-note">
                          <ShieldCheck size={17} />
                          当前显示固定阅读样例，非完整论文解析。
                        </div>
                        {active.source && (
                          <a
                            href={active.source}
                            target="_blank"
                            rel="noreferrer"
                          >
                            打开原始文献 <ArrowUpRight size={14} />
                          </a>
                        )}
                      </aside>
                      <article className="document-paper">
                        <span className="document-kicker">
                          READING SAMPLE · 固定样例
                        </span>
                        <h2>
                          {active.id === "attention"
                            ? active.title
                            : "Attention Is All You Need"}
                        </h2>
                        <p className="document-byline">
                          Ashish Vaswani · Noam Shazeer · Niki Parmar et al.
                        </p>
                        {active.id !== "attention" && (
                          <div className="notice">
                            本阶段仅提供 Transformer
                            阅读样例。当前文献可点击左侧“打开原始文献”查看。
                          </div>
                        )}
                        <div className="document-rule" />
                        <h3>{readSections[readerSection].title}</h3>
                        <p className="document-english">
                          {readSections[readerSection].en}
                        </p>
                        {bilingual && (
                          <div className="translation">
                            <span>中文对照</span>
                            <p>{readSections[readerSection].zh}</p>
                          </div>
                        )}
                        <div className="formula">
                          Attention(Q, K, V) = softmax(QKᵀ / √dₖ)V
                        </div>
                        <p className="document-footnote">
                          {readSections[readerSection].note}
                        </p>
                        <div className="reader-pagination">
                          <button
                            disabled={readerSection === 0}
                            onClick={() => setReaderSection((i) => i - 1)}
                          >
                            <ChevronLeft size={16} />
                            上一节
                          </button>
                          <span>
                            {readerSection + 1} / {readSections.length}
                          </span>
                          <button
                            disabled={readerSection === readSections.length - 1}
                            onClick={() => setReaderSection((i) => i + 1)}
                          >
                            下一节
                            <ChevronRight size={16} />
                          </button>
                        </div>
                      </article>
                    </div>
                  )}
                </>
              ) : (
                <div className="empty-state">请先导入或恢复文献。</div>
              )}
            </>
          )}
          {view === "chat" && (
            <>
              <div className="page-heading compact">
                <div>
                  <div className="eyebrow">ANSWERS WITH EVIDENCE</div>
                  <h1>文献问答</h1>
                  <p>不止于回答，更在于有据可循。</p>
                </div>
                <button
                  className="button secondary"
                  disabled={busy || !messages.length}
                  onClick={() => {
                    setMessages([]);
                    setConversation(null);
                  }}
                >
                  <Plus size={16} />
                  新对话
                </button>
              </div>
              <div className="chat-layout">
                <section className="chat-panel">
                  <div className="chat-panel-header">
                    <span>
                      <span className="mini-mark">
                        <Sparkles size={15} />
                      </span>
                      研析助手
                      <small>
                        {health?.model_ready
                          ? "本地模型 · 原文证据"
                          : "等待模型连接"}
                      </small>
                    </span>
                    <span className="small-status">
                      <i />
                      {health?.model_ready ? "模型已就绪" : "模型未连接"}
                    </span>
                  </div>
                  <div className="chat-messages" aria-live="polite">
                    {!messages.length && (
                      <div className="chat-welcome">
                        <div className="chat-logo">
                          <Sparkles size={30} />
                        </div>
                        <h2>让文献，回答你的问题。</h2>
                        <p>选择研究范围，从一个好问题开始。</p>
                        <div className="suggestion-grid">
                          {[
                            "所选文献的主要研究问题是什么？",
                            "所选文献使用了哪些实验设置？",
                            "所选文献提出了哪些局限？",
                          ].map((q, i) => (
                            <button key={q} onClick={() => ask(q)}>
                              <span>0{i + 1}</span>
                              {q}
                              <ArrowUpRight size={15} />
                            </button>
                          ))}
                        </div>
                      </div>
                    )}
                    {messages.map((message) => (
                      <div
                        key={message.id}
                        className={"chat-message " + message.role}
                      >
                        <span className="message-avatar">
                          {message.role === "assistant" ? (
                            <Sparkles size={15} />
                          ) : (
                            "X"
                          )}
                        </span>
                        <div>
                          <b>
                            {message.role === "assistant"
                              ? "研析 · 文献回答"
                              : "你"}
                          </b>
                          <p>{message.text}</p>
                          {message.steps && (
                            <div className="answer-steps">
                              {message.steps.join(" → ")}
                            </div>
                          )}
                          {message.evidence && message.evidence.length > 0 && (
                            <div className="evidence-list">
                              {message.evidence.map((e) => (
                                <details key={e.id}>
                                  <summary>
                                    [{e.id}] {e.title} · 第 {e.page} 页
                                  </summary>
                                  <blockquote>{e.quote}</blockquote>
                                  <a
                                    href={e.url}
                                    target="_blank"
                                    rel="noreferrer"
                                  >
                                    打开原文第 {e.page} 页{" "}
                                    <ArrowUpRight size={13} />
                                  </a>
                                </details>
                              ))}
                            </div>
                          )}
                        </div>
                      </div>
                    ))}
                    {busy && (
                      <div className="assistant-loading">
                        <Loader2 size={16} className="spin" />
                        正在检索原文并运行本地模型…
                      </div>
                    )}
                  </div>
                  <form
                    className="chat-composer"
                    onSubmit={(e) => {
                      e.preventDefault();
                      ask();
                    }}
                  >
                    <textarea
                      aria-label="输入文献问题"
                      placeholder="关于这些文献，你想了解什么？"
                      value={question}
                      onChange={(e) => setQuestion(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter" && !e.shiftKey) {
                          e.preventDefault();
                          ask();
                        }
                      }}
                    />
                    <div>
                      <span>
                        <ShieldCheck size={14} />
                        {health?.model_ready
                          ? "本地模型推理 · 关键结论请核对原文"
                          : "模型未连接 · 请先启动服务器"}
                      </span>
                      <button
                        className="send-button"
                        type="submit"
                        aria-label="发送问题"
                        disabled={!question.trim() || busy}
                      >
                        <ArrowUp size={19} />
                      </button>
                    </div>
                  </form>
                </section>
                <aside className="context-panel">
                  <h3>
                    <FolderOpen size={17} />
                    研究范围
                  </h3>
                  <p>
                    限定文献，聚焦问题。可用“这篇”“其中”接着问；新对话或切换范围会清空追问关联。
                  </p>
                  <select
                    aria-label="问答文献范围"
                    value={chatScope}
                    onChange={(e) => {
                      setChatScope(e.target.value);
                      setConversation(null);
                    }}
                  >
                    <option value="all">全部已解析文献</option>
                    {papers
                      .filter((p) => p.backendId && p.backendStatus === "ready")
                      .map((p) => (
                        <option key={p.id} value={p.id}>
                          {p.title}
                        </option>
                      ))}
                  </select>
                  <div className="scope-papers">
                    {papers
                      .filter(
                        (p) =>
                          p.backendId &&
                          (chatScope === "all" || p.id === chatScope),
                      )
                      .map((p) => (
                        <button key={p.id} onClick={() => openPaper(p)}>
                          <FileText size={16} />
                          <span>
                            {p.title}
                            <small>
                              {p.year} · {p.venue}
                            </small>
                          </span>
                          <ChevronRight size={13} />
                        </button>
                      ))}
                  </div>
                  <div className="info-note">
                    <ShieldCheck size={20} />
                    <strong>证据，比答案更重要</strong>
                    <p>
                      问答仅使用服务器已解析的文献。展开引用可查看检索原文，并跳转
                      PDF 页码；引用标号校验不等于结论一定正确。
                    </p>
                  </div>
                </aside>
              </div>
            </>
          )}
          {view === "discover" && (
            <Discovery
              documents={serverDocs}
              changed={changed}
              notify={notify}
              open={(id) => {
                const paper = papers.find((item) => item.id === id);
                if (paper) openPaper(paper);
                else {
                  changed();
                  go("library");
                }
              }}
            />
          )}
          {view === "graph" && (
            <CitationGraph
              documents={serverDocs}
              open={(id) => {
                const paper = papers.find((item) => item.id === id);
                if (paper) openPaper(paper);
              }}
            />
          )}
          {view === "write" && (
            <>
              <div className="page-heading">
                <div>
                  <div className="eyebrow">TURN IDEAS INTO RESEARCH</div>
                  <h1>写作工坊</h1>
                  <p>从一个想法开始，把研究组织得更清楚。</p>
                </div>
                <span className="subtle-label">本地模型与真实文献</span>
              </div>
              <div className="tabs writing-tabs">
                <button
                  className={writingTab === "outline" ? "active" : ""}
                  onClick={() => setWritingTab("outline")}
                >
                  论文框架
                </button>
                <button
                  className={writingTab === "chart" ? "active" : ""}
                  onClick={() => setWritingTab("chart")}
                >
                  数据可视化
                </button>
              </div>
              {writingTab === "outline" ? (
                <WritingWorkspace documents={serverDocs} />
              ) : (
                <ChartWorkspace
                  notify={notify}
                  csv={chartCsv}
                  onCsvChange={setChartCsv}
                  chartType={chartType}
                  onChartTypeChange={setChartType}
                />
              )}
            </>
          )}
          <footer className="page-footer">
            <span>
              研析 <i />
              让知识连接，让研究发生。
            </span>
            <span>
              {health ? "本地科研工作台 · v0.2" : "浏览器预览 · v0.2"}
            </span>
          </footer>
        </main>
      </div>
      {mobileNav && (
        <button
          className="nav-scrim"
          aria-label="关闭导航"
          onClick={() => setMobileNav(false)}
        />
      )}
      {toast && (
        <div className="toast" role="status">
          <Check size={17} />
          {toast}
          <button aria-label="关闭提示" onClick={() => setToast("")}>
            <X size={14} />
          </button>
        </div>
      )}
      {modal && (
        <div
          className="modal-backdrop"
          onMouseDown={(e) => {
            if (e.target === e.currentTarget) setModal(null);
          }}
        >
          <div
            ref={modalRef}
            className={"modal " + (modal === "compare" ? "wide" : "")}
            role="dialog"
            aria-modal="true"
            aria-labelledby="modal-title"
          >
            <button
              className="modal-close icon-button"
              aria-label="关闭弹窗"
              onClick={() => setModal(null)}
            >
              <X size={20} />
            </button>
            {modal === "upload" ? (
              <>
                <span className="modal-symbol">
                  <Upload size={24} />
                </span>
                <h2 id="modal-title">把知识，带进你的工作空间。</h2>
                <p>导入 PDF，建立属于你的研究文献库。</p>
                <div
                  className="dropzone"
                  role="button"
                  tabIndex={0}
                  aria-label="选择或拖入 PDF 文件"
                  onClick={() => inputRef.current?.click()}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      inputRef.current?.click();
                    }
                  }}
                  onDragOver={(e) => e.preventDefault()}
                  onDrop={(e) => {
                    e.preventDefault();
                    importFiles(e.dataTransfer.files);
                  }}
                >
                  <FileText size={34} />
                  <b>点击选择，或将 PDF 拖到这里</b>
                  <span>
                    {health
                      ? "上传至本地服务器 · 单文件不超过 100 MB"
                      : "浏览器本地预览 · 单文件不超过 20 MB"}
                  </span>
                  <span>本地文件指纹可识别内容完全相同的重复 PDF</span>
                  <span className="button primary small">
                    {importing ? "正在上传…" : "选择文件"} <Plus size={14} />
                  </span>
                </div>
                <input
                  type="file"
                  ref={inputRef}
                  accept="application/pdf,.pdf"
                  multiple
                  hidden
                  onChange={(e) => {
                    importFiles(e.target.files);
                    e.target.value = "";
                  }}
                />
                <div className="info-note">
                  <ShieldCheck size={17} />
                  <p>
                    {health
                      ? "PDF 保存到你的服务器，后台逐页解析并建立问答索引；扫描页使用本地 OCR，相似版本由你确认保留策略。"
                      : "后端未连接：文件只在浏览器预览，刷新后需重新选择。"}
                  </p>
                </div>
              </>
            ) : modal === "compare" ? (
              <>
                <h2 id="modal-title">并排看见，文献之间的联系。</h2>
                <p>元数据对比演示 · 未进行跨论文自动推理</p>
                <div className="compare-grid">
                  {papers
                    .filter((p) => selected.includes(p.id))
                    .map((p) => (
                      <article key={p.id}>
                        <span className={"tag " + p.color}>{p.tag}</span>
                        <h3>{p.title}</h3>
                        <p>{p.authors}</p>
                        <b>
                          {p.year} · {p.venue}
                        </b>
                        <div className="details-divider" />
                        <p>{p.abstract}</p>
                        <button
                          className="button secondary small"
                          onClick={() => {
                            setModal(null);
                            openPaper(p);
                          }}
                        >
                          打开阅读
                          <ArrowRight size={14} />
                        </button>
                      </article>
                    ))}
                </div>
              </>
            ) : (
              <>
                <span className="modal-symbol">
                  <FlaskConical size={24} />
                </span>
                <h2 id="modal-title">研析 · 当前演示说明</h2>
                <p>文献与问答连接本地服务器，模型推理不调用商业 API。</p>
                <div className="settings-features">
                  <div>
                    <Check size={16} />
                    <span>
                      已实现
                      <b>
                        文献上传、逐页正文与扫描件
                        OCR、内容相似检测、版本保留与恢复、本地模型问答、原文引用及
                        CSV
                        图表、中英逐段翻译与双向联动阅读、真实引用图谱与有来源的摘要综述、参考文献批量下载及每日／每周主题订阅。
                      </b>
                    </span>
                  </div>
                  <div>
                    <Clock3 size={16} />
                    <span>
                      后续接入
                      <b>
                        复杂表格与公式还原、更多引用格式、研究方向时效核验。
                      </b>
                    </span>
                  </div>
                  <div>
                    <ShieldCheck size={16} />
                    <span>
                      数据说明
                      <b>
                        所有 AI 文献问答连接本地服务器模型与按页索引；探索页连接
                        arXiv
                        真实检索；图谱由已入库论文的参考文献匹配构建，写作可依据真实文献生成研究计划与架构图。浏览器保存目录偏好，上传的
                        PDF 保存在服务器。
                      </b>
                    </span>
                  </div>
                </div>
                <button
                  className="button secondary"
                  onClick={() => {
                    setPapers(seedPapers);
                    setSelected([]);
                    setCurrentId("attention");
                    notify("已恢复初始示例文献。");
                    setModal(null);
                  }}
                >
                  恢复示例文献
                </button>
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

function ChartWorkspace({
  notify,
  csv,
  onCsvChange,
  chartType,
  onChartTypeChange,
}: {
  notify: (text: string) => void;
  csv: string;
  onCsvChange: (value: string) => void;
  chartType: ChartType;
  onChartTypeChange: (value: ChartType) => void;
}) {
  const fileRef = useRef<HTMLInputElement>(null);
  const svgRef = useRef<SVGSVGElement>(null);
  const result = useMemo(() => {
    try {
      return { data: parseMultiChartCsv(csv), error: "" };
    } catch (e) {
      return {
        data: null,
        error: e instanceof Error ? e.message : "无法解析 CSV。",
      };
    }
  }, [csv]);
  const data = result.data;
  const chartError = data && chartType === "radar" ? radarError(data) : "";
  const caption = data
    ? data.series
        .map((name, series) => {
          const values = data.rows.map((row) => row.values[series]);
          const best = data.rows.reduce((a, b) =>
            b.values[series] > a.values[series] ? b : a,
          );
          return `在本次输入的 ${data.rows.length} 组数据中，${best.label} 的${name}最高（${Math.max(...values)}），最低为 ${Math.min(...values)}。`;
        })
        .join("\n") +
      "该描述仅概括输入数据，不代表统计显著性或真实实验结论。" +
      (chartType === "radar"
        ? "雷达图使用所有系列共同的零起点刻度；指标须使用可比较的单位和方向，未自动归一化。"
        : "")
    : "";
  const loadCsv = async (file?: File) => {
    if (!file) return;
    if (file.size > 1024 * 1024) {
      notify("CSV 文件请控制在 1 MB 以内。");
      return;
    }
    try {
      const text = await file.text();
      parseMultiChartCsv(text);
      onCsvChange(text);
      notify("已读取本地 CSV。");
    } catch (e) {
      notify(e instanceof Error ? e.message : "文件读取失败。");
    }
  };
  return (
    <div className="chart-layout">
      <section className="chart-input panel">
        <span className="eyebrow">FROM DATA TO INSIGHT</span>
        <h3>让数据，讲述你的发现。</h3>
        <p>
          粘贴 CSV：第一列类别，后续 1–6
          列数值。下方初始数据为虚构演示，不是实验结果。
        </p>
        <label htmlFor="csv-data">CSV 数据 · 类别 + 一列或多列数值</label>
        <textarea
          id="csv-data"
          spellCheck={false}
          value={csv}
          onChange={(e) => onCsvChange(e.target.value)}
        />
        {result.error && (
          <p className="chart-error" role="alert">
            {result.error}
          </p>
        )}
        <div className="chart-tools">
          <button
            className="button secondary small"
            onClick={() => fileRef.current?.click()}
          >
            <Upload size={14} />
            导入 CSV
          </button>
          <button
            className="button secondary small"
            onClick={() => {
              onCsvChange(DEFAULT_CHART_CSV);
            }}
          >
            恢复示例
          </button>
        </div>
        <input
          ref={fileRef}
          type="file"
          accept=".csv,text/csv"
          onChange={(e) => {
            void loadCsv(e.target.files?.[0]);
            e.target.value = "";
          }}
        />
        <div className="info-note" style={{ marginTop: 22 }}>
          <ShieldCheck size={18} />
          <p>
            图表在浏览器本地计算。支持负值、零值和带引号的类别。最多 30 行。
            页面内切换会保留输入；刷新前请导出图注备份数据。
          </p>
        </div>
      </section>
      <section className="chart-output panel">
        <header>
          <h3>{data ? data.title + " · " + data.label : "数据预览"}</h3>
          <div className="chart-type">
            <button
              className={chartType === "bar" ? "active" : ""}
              onClick={() => onChartTypeChange("bar")}
            >
              柱状图
            </button>
            <button
              className={chartType === "line" ? "active" : ""}
              onClick={() => onChartTypeChange("line")}
            >
              折线图
            </button>
            <button
              className={chartType === "radar" ? "active" : ""}
              onClick={() => onChartTypeChange("radar")}
            >
              雷达图
            </button>
          </div>
        </header>
        {data ? (
          <>
            {chartError ? (
              <p className="chart-error" role="alert">
                {chartError}
              </p>
            ) : (
              <div className="chart-scroll">
                <ChartFigure data={data} type={chartType} svgRef={svgRef} />
              </div>
            )}
            {!chartError && (
              <p className="chart-scroll-hint">窄屏可左右滑动查看完整图表。</p>
            )}
            <div className="chart-caption">
              <b>Figure Caption · 数据描述</b>
              <p>{caption}</p>
            </div>
            <div className="chart-tools">
              <button
                className="button secondary small"
                disabled={!!chartError}
                onClick={() => {
                  if (svgRef.current) {
                    download(
                      "研析-数据图表.svg",
                      svgRef.current.outerHTML,
                      "image/svg+xml",
                    );
                    notify("已导出矢量图。");
                  }
                }}
              >
                <ArrowDownToLine size={14} />
                导出 SVG
              </button>
              <button
                className="button secondary small"
                onClick={() =>
                  download(
                    "研析-图表说明.md",
                    "# 数据描述\n\n" + caption + "\n\n## 输入数据\n\n" + csv,
                  )
                }
              >
                导出图注
              </button>
            </div>
          </>
        ) : (
          <div className="empty-state">
            <FlaskConical size={32} />
            <p>修正数据后将自动绘图。</p>
          </div>
        )}
      </section>
    </div>
  );
}
