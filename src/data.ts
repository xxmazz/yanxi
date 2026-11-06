export type View =
  | "library"
  | "reader"
  | "chat"
  | "discover"
  | "graph"
  | "write";
export type Paper = {
  id: string;
  title: string;
  authors: string;
  year: number;
  venue: string;
  tag: string;
  color: "sage" | "sand" | "blue" | "rose";
  abstract: string;
  pages: number;
  date: string;
  favorite: boolean;
  source?: string;
  version?: string;
  local?: boolean;
  filename?: string;
  contentFingerprint?: string;
  backendId?: string;
  backendStatus?: "queued" | "processing" | "review" | "ready" | "error";
};
export const seedPapers: Paper[] = [
  {
    id: "attention",
    title: "Attention Is All You Need",
    authors: "Ashish Vaswani, Noam Shazeer et al.",
    year: 2017,
    venue: "NeurIPS",
    tag: "Transformer",
    color: "sage",
    abstract: "以自注意力机制构建序列转换架构，开启 Transformer 研究脉络。",
    pages: 15,
    date: "2026-10-23",
    favorite: true,
    source: "https://arxiv.org/abs/1706.03762",
    version: "v7",
  },
  {
    id: "bert",
    title:
      "BERT: Pre-training of Deep Bidirectional Transformers for Language Understanding",
    authors: "Jacob Devlin, Ming-Wei Chang et al.",
    year: 2019,
    venue: "NAACL",
    tag: "预训练模型",
    color: "sand",
    abstract:
      "通过掩码语言建模学习双向表征，用预训练与微调连接多种语言理解任务。",
    pages: 16,
    date: "2026-10-23",
    favorite: true,
    source: "https://arxiv.org/abs/1810.04805",
  },
  {
    id: "rag",
    title: "Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks",
    authors: "Patrick Lewis, Ethan Perez et al.",
    year: 2020,
    venue: "NeurIPS",
    tag: "RAG",
    color: "blue",
    abstract: "结合可训练检索器和生成器，为知识密集型任务引入外部文档证据。",
    pages: 19,
    date: "2026-10-22",
    favorite: false,
    source: "https://arxiv.org/abs/2005.11401",
  },
  {
    id: "lora",
    title: "LoRA: Low-Rank Adaptation of Large Language Models",
    authors: "Edward J. Hu, Yelong Shen et al.",
    year: 2022,
    venue: "ICLR",
    tag: "高效微调",
    color: "rose",
    abstract:
      "冻结预训练权重，利用低秩矩阵学习增量，降低大模型适配时的训练参数量。",
    pages: 26,
    date: "2026-10-22",
    favorite: false,
    source: "https://arxiv.org/abs/2106.09685",
  },
  {
    id: "gpt3",
    title: "Language Models are Few-Shot Learners",
    authors: "Tom B. Brown, Benjamin Mann et al.",
    year: 2020,
    venue: "NeurIPS",
    tag: "大语言模型",
    color: "sage",
    abstract: "研究语言模型规模与上下文学习的关系，展示少样本任务适应能力。",
    pages: 75,
    date: "2026-10-21",
    favorite: false,
    source: "https://arxiv.org/abs/2005.14165",
  },
  {
    id: "react",
    title: "ReAct: Synergizing Reasoning and Acting in Language Models",
    authors: "Shunyu Yao, Jeffrey Zhao et al.",
    year: 2023,
    venue: "ICLR",
    tag: "Agent",
    color: "sand",
    abstract:
      "交替组织任务推理与环境行动，探索语言模型与外部工具交互的工作方式。",
    pages: 33,
    date: "2026-10-21",
    favorite: true,
    source: "https://arxiv.org/abs/2210.03629",
  },
];
export const graphNodes = [
  {
    id: "attention",
    x: 410,
    y: 190,
    r: 42,
    label: "Transformer",
    sub: "2017 · 基础架构",
    type: "foundation",
  },
  {
    id: "bert",
    x: 220,
    y: 330,
    r: 32,
    label: "BERT",
    sub: "2019 · 双向表征",
    type: "understanding",
  },
  {
    id: "gpt3",
    x: 590,
    y: 335,
    r: 36,
    label: "GPT-3",
    sub: "2020 · 上下文学习",
    type: "generation",
  },
  {
    id: "rag",
    x: 335,
    y: 495,
    r: 31,
    label: "RAG",
    sub: "2020 · 检索增强",
    type: "generation",
  },
  {
    id: "lora",
    x: 625,
    y: 510,
    r: 28,
    label: "LoRA",
    sub: "2022 · 高效适配",
    type: "generation",
  },
  {
    id: "react",
    x: 110,
    y: 490,
    r: 28,
    label: "ReAct",
    sub: "2023 · 工具交互",
    type: "generation",
  },
];
export const graphEdges = [
  ["attention", "bert"],
  ["attention", "gpt3"],
  ["attention", "rag"],
  ["attention", "lora"],
  ["bert", "rag"],
  ["gpt3", "react"],
];
