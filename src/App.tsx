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
import { graphEdges, graphNodes, readSections, seedPapers } from "./data";
import type { Paper, View } from "./data";
import { parseChartCsv } from "./csv";
import { fingerprintPdf } from "./pdf-fingerprint";

const nav: { id: View; label: string; icon: LucideIcon; hint?: string }[] = [
  { id: "library", label: "我的文献", icon: FolderOpen },
  { id: "reader", label: "沉浸阅读", icon: BookOpen },
  { id: "chat", label: "文献问答", icon: MessageSquare, hint: "AI" },
  { id: "discover", label: "探索发现", icon: Globe2 },
  { id: "graph", label: "引用图谱", icon: Network },
  { id: "write", label: "写作工坊", icon: FlaskConical },
];
const STORAGE = "yanxi-demo-library-v1";
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
  citations?: string[];
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
  const answerTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [graphFocus, setGraphFocus] = useState("attention");
  const [graphFilter, setGraphFilter] = useState("all");
  const [writingTab, setWritingTab] = useState<"outline" | "chart">("outline");
  const [idea, setIdea] = useState("面向科研文献的可验证问答与证据溯源");
  const [outline, setOutline] = useState("");

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
      if (answerTimer.current) clearTimeout(answerTimer.current);
    },
    [],
  );
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
  const removePaper = (id: string) => {
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
    if (!files) return;
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
  const ask = (text = question) => {
    const prompt = text.trim();
    if (!prompt || busy) return;
    setQuestion("");
    setMessages((items) => [
      ...items,
      { id: createId(), role: "user", text: prompt },
    ]);
    setBusy(true);
    answerTimer.current = setTimeout(() => {
      let response =
        "当前是前端交互演示，尚未连接真实检索与大模型。这个问题没有对应的预置证据，因此我不会编造答案。你可以试试下方的演示问题，或打开文献核对原文。";
      let citations: string[] = [];
      const scope =
        chatScope === "all"
          ? papers.filter((p) => !p.local).map((p) => p.id)
          : [chatScope];
      if (
        /BERT|区别|对比/i.test(prompt) &&
        scope.includes("attention") &&
        scope.includes("bert")
      ) {
        response =
          "演示回答：Transformer 包含编码器与自回归解码器，用于序列转换；BERT 采用双向 Transformer 编码器，通过掩码语言建模等预训练目标学习语言表征。两者共享注意力架构，但结构选择、注意力可见范围与训练目标不同。下面是供你核对的文献入口；它们尚不是经过后端验证的原文段落证据。";
        citations = ["attention", "bert"];
      } else if (/RAG|检索增强/i.test(prompt) && scope.includes("rag")) {
        response =
          "演示回答：检索增强生成把外部文档检索与生成模型结合，让生成过程可以利用参数之外的知识。研析计划进一步展示来源段落、原文位置与证据不足提示。检索到文献本身并不等于回答已被证据支持；此演示尚未实现证据校验。";
        citations = ["rag"];
      } else if (
        /注意力|Transformer/i.test(prompt) &&
        scope.includes("attention")
      ) {
        response =
          "演示回答：多头注意力将表征映射到多个子空间，并行计算注意力，再汇合输出，使模型可以同时关注不同的关系。下面的文献入口可用于核对，当前回答为人工编写的前端样例。";
        citations = ["attention"];
      }
      setMessages((items) => [
        ...items,
        {
          id: createId(),
          role: "assistant",
          text: response,
          citations,
        },
      ]);
      setBusy(false);
    }, 950);
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
  const visibleNodes = graphNodes.filter(
    (n) => graphFilter === "all" || n.type === graphFilter,
  );
  const graphPaper = seedPapers.find((p) => p.id === graphFocus)!;

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
              交互演示
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
                  <div className="eyebrow">YOUR RESEARCH, CONNECTED</div>
                  <h1>
                    我的文献<span className="count-pill">{papers.length}</span>
                  </h1>
                  <p>整理知识的碎片，让研究的脉络清晰可见。</p>
                </div>
                <button
                  className="button primary"
                  onClick={() => setModal("upload")}
                >
                  <Plus size={17} />
                  导入文献
                </button>
              </div>
              <section className="welcome-banner">
                <div className="banner-copy">
                  <span className="banner-label">
                    <Sparkles size={14} />
                    让阅读更进一步
                  </span>
                  <h2>
                    读懂一篇论文，
                    <br />
                    连接一整个研究方向。
                  </h2>
                  <p>从文献解析到证据问答，让灵感不止停留在阅读。</p>
                  <button onClick={() => go("chat")}>
                    开始一次有据可循的探索 <ArrowRight size={16} />
                  </button>
                </div>
                <div className="banner-visual" aria-hidden="true">
                  <div className="orbit orbit-one" />
                  <div className="orbit orbit-two" />
                  <div className="orbit orbit-three" />
                  <svg viewBox="0 0 400 220">
                    <path d="M100 140 L208 84 L303 146 M208 84 L242 193 M100 140 L242 193 M303 146 L242 193" />
                  </svg>
                  <div className="floating-doc doc-one">
                    <FileText size={23} />
                    <i />
                    <i />
                    <span>文献</span>
                  </div>
                  <div className="floating-doc doc-two">
                    <Network size={22} />
                    <i />
                    <i />
                    <span>关联</span>
                  </div>
                  <div className="visual-center">
                    <Sparkles size={27} />
                  </div>
                  <div className="evidence-badge">
                    <ShieldCheck size={15} />
                    让知识有迹可循
                  </div>
                  <span className="visual-star star-one">✦</span>
                  <span className="visual-star star-two">✧</span>
                </div>
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
                    <div className="paper-meta">
                      <span>
                        {paper.year}
                        <i />
                        {paper.venue}
                      </span>
                      <span>{paper.local ? "本地 PDF" : "示例文献"}</span>
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
                        <span>{paper.local ? "待解析" : "阅读样例"}</span>
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
                示例文献用于体验界面；导入的文件仅在本机浏览器预览，尚未进行 AI
                解析。
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
                <button
                  className="button secondary"
                  onClick={() => setBilingual(!bilingual)}
                >
                  <Columns2 size={16} />
                  {bilingual ? "中英对照" : "原文视图"}
                </button>
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
                  {active.local ? (
                    <div className="local-preview">
                      {urls[active.id] ? (
                        <iframe title={active.title} src={urls[active.id]} />
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
                  onClick={() => setMessages([])}
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
                      研析助手<small>预置回答演示</small>
                    </span>
                    <span className="small-status">
                      <i />
                      本地演示
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
                            "Transformer 与 BERT 有什么区别？",
                            "如何理解多头注意力？",
                            "RAG 如何引入外部知识？",
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
                              ? "研析 · 演示回答"
                              : "你"}
                          </b>
                          <p>{message.text}</p>
                          {message.citations &&
                            message.citations.length > 0 && (
                              <div className="citation-list">
                                {message.citations.map((id, index) => {
                                  const paper = seedPapers.find(
                                    (p) => p.id === id,
                                  )!;
                                  return (
                                    <a
                                      href={paper.source}
                                      target="_blank"
                                      rel="noreferrer"
                                      key={id}
                                    >
                                      <span>{index + 1}</span>
                                      <FileText size={13} />
                                      {paper.title}
                                      <ArrowUpRight size={13} />
                                    </a>
                                  );
                                })}
                              </div>
                            )}
                        </div>
                      </div>
                    ))}
                    {busy && (
                      <div className="assistant-loading">
                        <Loader2 size={16} className="spin" />
                        正在载入对应演示样例…
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
                        当前未连接大模型 · 请核对原文
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
                  <p>限定文献，聚焦问题。</p>
                  <select
                    aria-label="问答文献范围"
                    value={chatScope}
                    onChange={(e) => setChatScope(e.target.value)}
                  >
                    <option value="all">全部文献</option>
                    {papers.map((p) => (
                      <option key={p.id} value={p.id}>
                        {p.title}
                      </option>
                    ))}
                  </select>
                  <div className="scope-papers">
                    {papers
                      .filter((p) => chatScope === "all" || p.id === chatScope)
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
                      当前展示固定问答及文献入口。真实段落检索、引用校验和模型流式输出将在后续接入。
                    </p>
                  </div>
                </aside>
              </div>
            </>
          )}
          {view === "discover" && (
            <>
              <div className="page-heading">
                <div>
                  <div className="eyebrow">FOLLOW YOUR CURIOSITY</div>
                  <h1>探索发现</h1>
                  <p>沿着一条研究线索，发现值得阅读的下一篇。</p>
                </div>
                <span className="subtle-label">精选样例 · 非实时检索</span>
              </div>
              <div className="discover-intro">
                <Globe2 size={34} />
                <div>
                  <h2>从经典出发，探索研究脉络。</h2>
                  <p>本阶段展示固定文献集；实时 arXiv 检索将在后端接入。</p>
                </div>
              </div>
              <div className="discovery-list">
                {seedPapers
                  .filter((p) =>
                    (p.title + p.tag)
                      .toLowerCase()
                      .includes(query.toLowerCase()),
                  )
                  .map((p, i) => (
                    <article key={p.id}>
                      <div className={"discovery-number " + p.color}>
                        {String(i + 1).padStart(2, "0")}
                      </div>
                      <div>
                        <span className="discovery-meta">
                          {p.venue} · {p.year}
                          <i />
                          {p.tag}
                        </span>
                        <h3>{p.title}</h3>
                        <p>{p.abstract}</p>
                        <span className="recommend-reason">
                          <Sparkles size={12} />
                          主题样例：与你的 Transformer 与语言模型研究脉络相关
                        </span>
                      </div>
                      <div className="discovery-actions">
                        {papers.some((x) => x.id === p.id) ? (
                          <span>
                            <Check size={15} />
                            已在文献库
                          </span>
                        ) : (
                          <button
                            className="button secondary small"
                            onClick={() => {
                              setPapers((items) => [...items, p]);
                              notify("已添加到文献库。");
                            }}
                          >
                            <Plus size={14} />
                            加入文献库
                          </button>
                        )}
                        <a href={p.source} target="_blank" rel="noreferrer">
                          原始文献 <ArrowUpRight size={14} />
                        </a>
                      </div>
                    </article>
                  ))}
              </div>
            </>
          )}
          {view === "graph" && (
            <>
              <div className="page-heading">
                <div>
                  <div className="eyebrow">SEE THE BIGGER PICTURE</div>
                  <h1>引用图谱</h1>
                  <p>一篇文献是一个起点，关联让知识生长。</p>
                </div>
                <button
                  className="button secondary"
                  onClick={() =>
                    download(
                      "研析-示例关系图.json",
                      JSON.stringify(
                        {
                          kind: "curated-demo-relations-not-verified-citations",
                          nodes: graphNodes,
                          edges: graphEdges,
                        },
                        null,
                        2,
                      ),
                      "application/json",
                    )
                  }
                >
                  <ArrowDownToLine size={16} />
                  导出图谱
                </button>
              </div>
              <div className="graph-layout">
                <section className="graph-canvas">
                  <div className="graph-top">
                    <span>
                      <span className="dot sage" />
                      Transformer 研究脉络
                    </span>
                    <select
                      aria-label="图谱主题筛选"
                      value={graphFilter}
                      onChange={(e) => setGraphFilter(e.target.value)}
                    >
                      <option value="all">全部关系</option>
                      <option value="generation">生成与应用</option>
                      <option value="understanding">语言理解</option>
                    </select>
                  </div>
                  <svg
                    viewBox="0 0 800 640"
                    aria-label="交互式示例研究关系图"
                    role="group"
                  >
                    <defs>
                      <pattern
                        id="dots"
                        x="0"
                        y="0"
                        width="22"
                        height="22"
                        patternUnits="userSpaceOnUse"
                      >
                        <circle cx="1" cy="1" r="1" fill="#dfe7df" />
                      </pattern>
                    </defs>
                    <rect width="800" height="640" fill="url(#dots)" />
                    {graphEdges.map(([from, to]) => {
                      const a = visibleNodes.find((n) => n.id === from),
                        b = visibleNodes.find((n) => n.id === to);
                      return a && b ? (
                        <line
                          key={from + to}
                          x1={a.x}
                          y1={a.y}
                          x2={b.x}
                          y2={b.y}
                          stroke={
                            graphFocus === from || graphFocus === to
                              ? "#709783"
                              : "#d3ded6"
                          }
                          strokeWidth={
                            graphFocus === from || graphFocus === to ? 2 : 1.3
                          }
                        />
                      ) : null;
                    })}
                    {visibleNodes.map((node) => (
                      <g
                        key={node.id}
                        role="button"
                        aria-label={"查看 " + node.label}
                        tabIndex={0}
                        className={
                          "graph-node " +
                          (graphFocus === node.id ? "focused" : "")
                        }
                        onClick={() => setGraphFocus(node.id)}
                        onKeyDown={(e) => {
                          if (e.key === "Enter" || e.key === " ") {
                            e.preventDefault();
                            setGraphFocus(node.id);
                          }
                        }}
                      >
                        <circle
                          className="node-halo"
                          cx={node.x}
                          cy={node.y}
                          r={node.r + 10}
                        />
                        <circle
                          cx={node.x}
                          cy={node.y}
                          r={node.r}
                          fill={
                            node.id === "attention"
                              ? "#416f56"
                              : node.type === "understanding"
                                ? "#d7b27f"
                                : "#b4cab9"
                          }
                          stroke="white"
                          strokeWidth="4"
                        />
                        <text
                          x={node.x}
                          y={node.y + 5}
                          textAnchor="middle"
                          className={
                            node.id === "attention" ? "light-label" : ""
                          }
                        >
                          {node.label === "Transformer" ? "T" : node.label}
                        </text>
                        <text
                          x={node.x}
                          y={node.y + node.r + 27}
                          textAnchor="middle"
                          className="graph-label"
                        >
                          {node.label}
                        </text>
                        <text
                          x={node.x}
                          y={node.y + node.r + 45}
                          textAnchor="middle"
                          className="graph-sub"
                        >
                          {node.sub}
                        </text>
                      </g>
                    ))}
                  </svg>
                  <div className="graph-legend">
                    <span>
                      <i className="sage" />
                      基础架构
                    </span>
                    <span>
                      <i className="sand" />
                      语言理解
                    </span>
                    <span>
                      <i className="pale" />
                      生成与应用
                    </span>
                    <span>点击节点查看文献</span>
                  </div>
                </section>
                <aside className="graph-details">
                  <span className="eyebrow">PAPER INSIGHT</span>
                  <span className={"paper-file " + graphPaper.color}>
                    <FileText size={25} />
                  </span>
                  <h2>{graphPaper.title}</h2>
                  <p>{graphPaper.authors}</p>
                  <span className={"tag " + graphPaper.color}>
                    {graphPaper.year} · {graphPaper.venue}
                  </span>
                  <div className="details-divider" />
                  <h4>研究线索</h4>
                  <p>{graphPaper.abstract}</p>
                  <a
                    className="button secondary"
                    href={graphPaper.source}
                    target="_blank"
                    rel="noreferrer"
                  >
                    查看原始文献
                    <ArrowUpRight size={15} />
                  </a>
                  <div className="info-note">
                    <GitBranch size={18} />
                    <p>
                      手工整理的主题关系示例，并非自动抽取、验证后的引用图谱。节点大小也不代表
                      PageRank。
                    </p>
                  </div>
                </aside>
              </div>
            </>
          )}
          {view === "write" && (
            <>
              <div className="page-heading">
                <div>
                  <div className="eyebrow">TURN IDEAS INTO RESEARCH</div>
                  <h1>写作工坊</h1>
                  <p>从一个想法开始，把研究组织得更清楚。</p>
                </div>
                <span className="subtle-label">模板与本地数据工具</span>
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
                <div className="writing-layout">
                  <section className="writing-input panel">
                    <span className="eyebrow">START WITH AN IDEA</span>
                    <h2>你在研究什么？</h2>
                    <p>输入研究主题，建立一份可编辑的论文框架。</p>
                    <label htmlFor="research-idea">研究主题</label>
                    <textarea
                      id="research-idea"
                      value={idea}
                      onChange={(e) => setIdea(e.target.value)}
                      placeholder="例如：基于引用图谱的科研文献推荐…"
                    />
                    <div className="info-note">
                      <ShieldCheck size={18} />
                      <p>
                        当前使用确定性框架模板，不会生成研究结果或虚构参考文献。
                      </p>
                    </div>
                    <button
                      className="button primary"
                      disabled={!idea.trim()}
                      onClick={() =>
                        setOutline(
                          "# " +
                            idea.trim() +
                            "\n\n## Abstract\n[填写真实的研究问题、方法、数据与结果。]\n\n## 1. Introduction\n- 研究背景与实际痛点\n- 现有方案的局限\n- 本研究的贡献（待验证）\n\n## 2. Related Work\n[导入并核对真实文献后补充。]\n\n## 3. Method\n- 问题定义与整体架构\n- 核心模块及证据验证机制\n\n## 4. Experiments\n- 数据集与评测指标\n- 基线、消融及失败案例\n- 真实实验结果（待完成）\n\n## 5. Limitations\n[说明适用边界、成本与失败条件。]\n\n## 6. Conclusion\n[依据已验证结果撰写。]\n\n## References\n[仅填写已核对的真实来源。]",
                        )
                      }
                    >
                      <Sparkles size={16} />
                      生成框架模板
                    </button>
                  </section>
                  <section className="writing-output panel">
                    <div>
                      <h3>
                        <FileText size={17} />
                        框架草稿
                      </h3>
                      <button
                        disabled={!outline}
                        className="button secondary small"
                        onClick={() => download("研析-论文框架.md", outline)}
                      >
                        <ArrowDownToLine size={14} />
                        导出 Markdown
                      </button>
                    </div>
                    {outline ? (
                      <textarea
                        aria-label="编辑框架草稿"
                        value={outline}
                        onChange={(e) => setOutline(e.target.value)}
                      />
                    ) : (
                      <div className="empty-state">
                        <FileText size={36} />
                        <h3>让一个想法，有清晰的结构。</h3>
                        <p>生成后可以直接编辑，再导出 Markdown。</p>
                      </div>
                    )}
                  </section>
                </div>
              ) : (
                <ChartWorkspace notify={notify} />
              )}
            </>
          )}
          <footer className="page-footer">
            <span>
              研析 <i />
              让知识连接，让研究发生。
            </span>
            <span>Frontend Demo · v0.1</span>
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
                  <span>支持批量导入 · 单文件不超过 20 MB</span>
                  <span>本地文件指纹可识别内容完全相同的重复 PDF</span>
                  <span className="button primary small">
                    选择文件 <Plus size={14} />
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
                    文件只在本地预览，刷新后需重新选择。当前不包含
                    OCR、语义去重或真实 AI 解析。
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
                <p>这是第一阶段前端原型，可以体验完整的页面与交互。</p>
                <div className="settings-features">
                  <div>
                    <Check size={16} />
                    <span>
                      已实现
                      <b>
                        文献筛选、星标、对比、PDF
                        本地预览、阅读样例、固定问答、交互图谱、框架编辑和 CSV
                        图表。
                      </b>
                    </span>
                  </div>
                  <div>
                    <Clock3 size={16} />
                    <span>
                      后续接入
                      <b>
                        真实 PDF/OCR
                        解析、混合检索、引用验证、模型流式回答和实时论文检索。
                      </b>
                    </span>
                  </div>
                  <div>
                    <ShieldCheck size={16} />
                    <span>
                      数据说明
                      <b>
                        所有 AI
                        内容均为明确标记的演示或模板。无密钥、无真实后端；浏览器保存目录与星标。
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

function ChartWorkspace({ notify }: { notify: (text: string) => void }) {
  const [csv, setCsv] = useState(
    "方法,得分\nBaseline,68.4\n+ 检索,75.2\n+ 重排序,79.6\n+ 引用校验,82.1",
  );
  const [chartType, setChartType] = useState<"bar" | "line">("bar");
  const fileRef = useRef<HTMLInputElement>(null);
  const svgRef = useRef<SVGSVGElement>(null);
  const result = useMemo(() => {
    try {
      return { data: parseChartCsv(csv), error: "" };
    } catch (e) {
      return {
        data: null,
        error: e instanceof Error ? e.message : "无法解析 CSV。",
      };
    }
  }, [csv]);
  const data = result.data;
  const values = data?.rows.map((row) => row.value) || [0];
  const min = Math.min(0, ...values),
    max = Math.max(0, ...values);
  const range = max - min || 1;
  const y = (value: number) => 260 - ((value - min) / range) * 190;
  const x = (i: number) =>
    78 +
    i * (460 / Math.max(data?.rows.length || 1, 1)) +
    230 / Math.max(data?.rows.length || 1, 1);
  const baseline = y(0);
  const caption = data
    ? "在本次输入的 " +
      data.rows.length +
      " 组数据中，" +
      data.rows.reduce((a, b) => (b.value > a.value ? b : a)).label +
      " 的" +
      data.label +
      "最高（" +
      Math.max(...values) +
      "），最低为 " +
      Math.min(...values) +
      "。该描述仅概括输入数据，不代表统计显著性或真实实验结论。"
    : "";
  const loadCsv = async (file?: File) => {
    if (!file) return;
    if (file.size > 1024 * 1024) {
      notify("CSV 文件请控制在 1 MB 以内。");
      return;
    }
    try {
      const text = await file.text();
      parseChartCsv(text);
      setCsv(text);
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
          粘贴两列 CSV，或导入本地文件。下方初始数据为虚构演示，不是实验结果。
        </p>
        <label htmlFor="csv-data">CSV 数据 · 类别 + 数值</label>
        <textarea
          id="csv-data"
          spellCheck={false}
          value={csv}
          onChange={(e) => setCsv(e.target.value)}
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
              setCsv(
                "方法,得分\nBaseline,68.4\n+ 检索,75.2\n+ 重排序,79.6\n+ 引用校验,82.1",
              );
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
          </p>
        </div>
      </section>
      <section className="chart-output panel">
        <header>
          <h3>{data ? data.title + " · " + data.label : "数据预览"}</h3>
          <div className="chart-type">
            <button
              className={chartType === "bar" ? "active" : ""}
              onClick={() => setChartType("bar")}
            >
              柱状图
            </button>
            <button
              className={chartType === "line" ? "active" : ""}
              onClick={() => setChartType("line")}
            >
              折线图
            </button>
          </div>
        </header>
        {data ? (
          <>
            <svg
              ref={svgRef}
              className="chart-svg"
              viewBox="0 0 610 340"
              xmlns="http://www.w3.org/2000/svg"
              role="img"
              aria-label={data.label + "数据图表"}
            >
              <rect width="610" height="340" fill="#ffffff" />
              <text
                x="30"
                y="32"
                fill="#849373"
                fontSize="10"
                fontFamily="sans-serif"
              >
                {data.label} · 本地数据图表
              </text>
              {Array.from({ length: 5 }, (_, i) => {
                const v = min + (range / 4) * i;
                return (
                  <g key={i}>
                    <line
                      x1="65"
                      x2="560"
                      y1={y(v)}
                      y2={y(v)}
                      stroke="#e8eee0"
                      strokeDasharray="3 4"
                    />
                    <text
                      x="52"
                      y={y(v) + 4}
                      textAnchor="end"
                      fontSize="10"
                      fill="#a2b48e"
                      fontFamily="sans-serif"
                    >
                      {Number(v.toPrecision(4))}
                    </text>
                  </g>
                );
              })}
              <line
                x1="65"
                x2="560"
                y1={baseline}
                y2={baseline}
                stroke="#b8cba4"
              />
              {chartType === "line" && (
                <polyline
                  fill="none"
                  stroke="#6c8d55"
                  strokeWidth="2.5"
                  points={data.rows
                    .map((r, i) => x(i) + "," + y(r.value))
                    .join(" ")}
                />
              )}
              {data.rows.map((r, i) => (
                <g key={i}>
                  {chartType === "bar" ? (
                    <rect
                      x={x(i) - Math.min(24, 170 / data.rows.length)}
                      y={Math.min(baseline, y(r.value))}
                      width={Math.min(48, 340 / data.rows.length)}
                      height={Math.max(1, Math.abs(baseline - y(r.value)))}
                      rx="3"
                      fill={i === data.rows.length - 1 ? "#64874d" : "#bfd2ac"}
                    />
                  ) : (
                    <circle
                      cx={x(i)}
                      cy={y(r.value)}
                      r="4"
                      fill="#6c8d55"
                      stroke="white"
                      strokeWidth="2"
                    />
                  )}
                  <text
                    x={x(i)}
                    y={r.value >= 0 ? y(r.value) - 10 : y(r.value) + 15}
                    textAnchor="middle"
                    fontSize="10"
                    fill="#80986b"
                    fontFamily="sans-serif"
                  >
                    {Number(r.value.toPrecision(5))}
                  </text>
                  <text
                    x={x(i)}
                    y="295"
                    textAnchor="middle"
                    fontSize={data.rows.length > 8 ? "7" : "10"}
                    fill="#99ad85"
                    fontFamily="sans-serif"
                  >
                    {r.label.length > 10 ? r.label.slice(0, 9) + "…" : r.label}
                  </text>
                </g>
              ))}
              <text
                x="310"
                y="325"
                textAnchor="middle"
                fontSize="8"
                fill="#bac7aa"
                fontFamily="sans-serif"
              >
                研析 · 由用户输入数据生成 · 请核对来源
              </text>
            </svg>
            <div className="chart-caption">
              <b>Figure Caption · 数据描述</b>
              <p>{caption}</p>
            </div>
            <div className="chart-tools">
              <button
                className="button secondary small"
                onClick={() => {
                  if (svgRef.current) {
                    download(
                      "研析-数据图表.svg",
                      new XMLSerializer().serializeToString(svgRef.current),
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
