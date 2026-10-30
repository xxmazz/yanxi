# 研析 · 科研文献智能助手

面向科研阅读与写作场景的本地模型文献工作台，支持 PDF 入库、混合检索、带原文页码的文献问答。引用图谱从参考文献原文构建，写作工坊基于真实摘要生成可编辑研究计划和架构图。

2026-10-30 已完成核心版工程收尾：四卡本地模型、接上文追问、真实Chromium页面流程及9个文件下载核验；169项后端与13项前端测试通过。怎样启动、评委体验路径及剩余个人信息见[交付说明](docs/FINAL_HANDOFF.md)，赛题逐项覆盖见[需求核对](docs/REQUIREMENTS_COVERAGE.md)。此前10组API联合回归保留在[工程验收说明](docs/ENGINEERING_RELEASE.md)。

## 功能概览

- 文献管理：关键词搜索、筛选、星标、排序、卡片与列表切换，以及目录 JSON 导出。
- 文献对比：多篇文献元数据并排查看。
- PDF 入库：上传到本地服务器，逐页解析、后台索引与持久化，支持本地 OCR、正文相似版本提示、保留／替换决策、归档恢复及逐页正文回看。
- 文献问答：本地 Qwen2.5-7B-Instruct + multilingual-e5-small / BM25，支持范围筛选、原文摘录、页码跳转和数值检查。
- 研究任务：中文主题转换、本地 E5 主题重排、入库、解析等待与带来源的摘要综述，保存进度，支持检查点重试、摘要补生成、版本确认暂停和取消，详见 [研究任务](docs/RESEARCH_TASKS.md)。
- 论文检索：arXiv 官方搜索、中文主题转换、摘要与来源展示、本页 E5 相关排序、一键下载与后台解析。
- 对照翻译：本地 Qwen 逐页／整篇翻译、中英段落对应、双向联动滚动、局部翻译与中文摘要英译、对照 Markdown 导出。
- 研究图谱：真实参考文献连边、同作品版本合并、局部 PageRank／入度、逐页引用证据、本地模型简短综述、Future Work 原文与 JSON 导出。
- 写作工具：本地模型生成有来源的研究计划、服务端真实书目、摘要原页核对、Markdown 编辑预览与导出、Mermaid／SVG 架构图。
- 数据图表：两列 CSV 导入、数值校验、柱状与折线图、SVG 和图注导出。
- 自适应布局：桌面与移动端导航、弹窗和内容排版。

## 运行方式

前端环境：Node.js 22.18+ 或 Node.js 24。完整模型版还需要 Python 3.12 与 NVIDIA GPU，安装、显卡分配和启动说明见 [本地模型后端](docs/LOCAL_BACKEND.md)。以下命令只启动前端开发服务。

```sh
npm ci
npm run dev
```

浏览器访问 `http://localhost:4173`。

```sh
npm run build
npm test
npm run preview
```

## 技术栈与目录

React 19、TypeScript、Vite、FastAPI、SQLite、pypdf、PyTorch、Transformers；NetworkX／SciPy 计算引用网络；React Markdown 与 GFM 负责写作排版。

处理链：PDF → 文本层／本地 OCR → 相似版本确认 → 按页切块 → 本地 E5 向量与 BM25 → 模型扩展中英检索词 → RRF 召回 → Qwen 证据问答 → 来源与数值检查 → PDF 页码回看。

| 目录     | 内容                                    |
| -------- | --------------------------------------- |
| src/     | 页面、样式、演示数据与 CSV 解析         |
| tests/   | React 组件、CSV 与 PDF 文件头校验测试   |
| backend/ | 本地模型、PDF 索引、问答 API 与后端测试 |
| scripts/ | 运行辅助、联合回归与工程打包工具        |
| docs/    | 开发记录、协作说明与验证记录            |

## 演示范围与数据

连接后端时，上传的 PDF、文本和向量保存在用户自己的服务器，模型只加载本地权重。前端无法连接后端时，仅支持浏览器 PDF 预览，不用预置回答冒充推理。

正文词组与本地语义向量联合识别相似版本，扫描页通过本地 OCR 入库；详见 [扫描件与版本管理](docs/OCR_AND_VERSIONS.md)。显式编号三级目录定位见 [章节导航](docs/CHAPTER_NAVIGATION.md)。数字制与有限英文作者年份制正文引用／书目双向跳转见 [引用导航](docs/READING_LINKS.md)。公式／表格结构化、更多引用格式与定期论文追踪仍需开发。对照翻译见 [本地翻译说明](docs/TRANSLATION.md)。真实检索与一键入库见 [论文检索说明](docs/DISCOVERY.md)。真实引用网络与摘要综述见 [图谱说明](docs/GRAPH.md)；官方 20 篇、100 条引用边的核对见 [真实图谱验收](docs/GRAPH20.md)，未来方向是否已被新研究解决仍需核验。来源和数值检查不能代替完整事实核验。文献库保留明确标注的示例卡片，写作仅使用所选文献摘要，方法与评估标明研究计划，详见 [写作说明](docs/WRITING.md)；图表初始数值为演示数据。

541 页教材的完整索引、逐页复核和固定问答用例见 [长文档回归](docs/LONG_DOCUMENT.md)，支持明确图号定位并保留解析警告；已与用户提供的比赛资料包原件核对哈希；官方扫描件和版本回归见 [官方样本验证](docs/OFFICIAL_FIXTURES.md)。

官方问答抽查发现的作者顺序与训练硬件问题已修复，署名区直接核对顺序及机构脚注，GPU 问题补召回训练配置段。五问与八项边界检查见 [问答改进](docs/QA_RELIABILITY.md)。

BLEU 问答增加原生表格列坐标核对与明确标注的差值计算；跨篇问题分别取证并检查所选文献引用覆盖。七项真实用例、两篇架构对比及支持范围见 [表格与跨篇问答](docs/COMPARISON_QA.md)。

GPT 系列参数量、LN 位置和上下文比较已验证，支持按指标逐篇召回、有限配置表原单位读数及归纳来源标注，见 [GPT 跨篇验收](docs/GPT_COMPARISON.md)。

当前服务是内部开发实例，公网访问控制与评审部署另行验收。模型权重、PDF、数据库和密钥均不进入 Git。不要将密钥放进前端变量。

完整本地模型栈已在四张RTX2080Ti的新虚拟环境中复现，实际验证Qwen问答、E5索引及官方扫描件GPU OCR，见[四卡GPU复现](docs/GPU_REPRODUCTION.md)。早期CPU检查仅用于基础安装、原文解析和重启持久化，见[基础复现记录](docs/CLEAN_REPRODUCTION.md)。可选共享评审账号保护全站页面、API和论文文件；本机HTTPS、认证与跨站请求验收见[评审访问控制](docs/REVIEW_ACCESS.md)。Chromium回环认证与页面流程已完成；实际公网域名、手动密码弹窗及评审保活仍由后续外网验收核对。

## 开发与验证

2026-10-23 完成工程初始化、工作台交互、写作与图表工具、响应式检查，以及内网 HTTP 兼容性修复。2026-10-25 增加本地 PDF 文件头校验和重复文件检测。2026-10-26 增加写作草稿编辑／预览切换，支持 GFM 表格；模块来源见 [依赖说明](docs/MARKDOWN_MODULES.md)。生产构建、5 项自动化测试与 11 组浏览器交互检查通过，详情见 docs/VERIFICATION.md。

开发日志：docs/DEVELOPMENT.md · 进度记录：docs/PROGRESS.md · AI 协作说明：docs/AI_COLLABORATION.md

## 后续计划

各模块现状及扩展方向见 [比赛工程清单](docs/COMPETITION_PLAN.md)，本轮核心链路验收见 [工程候选版](docs/ENGINEERING_RELEASE.md)。公网和视频发布按参赛者安排另行处理；团队信息及最终提交材料仍需补齐。历次后端验证见 [后端验证记录](docs/BACKEND_VERIFICATION.md)。

仓库：https://github.com/xxmazz/yanxi

## 团队与协作

参赛负责人：xxmazz（GitHub 账号）。正式姓名、学校及评审所需团队信息在最终提交前由参赛者补齐，填写位置见[团队信息](docs/TEAM.md)。AI 辅助开发过程见 [协作说明](docs/AI_COLLABORATION.md)。

## 技术说明与演示准备

[10页技术文档](output/pdf/yanxi-technical-draft.pdf)已按工程e47f644更新，包含系统架构、四卡本地模型方案、实现与测试依据，技术内容定稿，团队信息待补。源稿与重建方式见[文档构建](docs/REPORT_BUILD.md)，视频按[七分钟脚本](docs/DEMO_SCRIPT.md)准备，尚未录制发布。工程和材料的剩余事项见[交付清单](docs/DELIVERY_PLAN.md)。
