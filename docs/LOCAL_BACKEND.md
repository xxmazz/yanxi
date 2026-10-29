# 本地模型后端

本阶段打通真实 PDF 入库、按页索引、混合检索、本地模型问答和原文页码回看。前端与 FastAPI 同源部署，模型推理不调用商业云端服务。

## 处理流程

1. PDF 上传后立即保存 SHA256、文件名和解析状态；单文件上限 100 MB。
2. 单个后台工作线程用 pypdf 逐页提取文本，按 1100 字符切块、重叠 160 字符，保留 PDF 的物理页码与块内偏移。当前上限 650 页；已验证 541 页教材，并确认与用户提供的比赛资料包原件哈希一致。
3. 本地 multilingual-e5-small 生成归一化向量，与文本一同写入 SQLite。文件、文本、向量在重启后保留。
4. Qwen 将问题扩展为中英检索词；BM25 和向量相似度分别召回，通过 RRF 合并前六段证据。
5. Qwen 输出逐条中文结论及来源编号。服务端只接受当前证据编号，附上数据库中的真实原文，并检查结论的阿拉伯数字是否存在于对应摘录中。可选逐字摘录也会做原文匹配与页码重定位。
6. 前端展开引用查看原文，点击链接打开原始 PDF 的 `#page=N`。

这是一条可审查的模型驱动检索工作流，尚不是具有任意工具调用、自主多轮探索能力的完整 Agent。数值出现和逐字匹配检查也不等于语义蕴含验证，不能保证零幻觉。

## 模型与显卡

| 模块             | 模型                        | 本次服务器分配            | 来源与许可                                                                   |
| ---------------- | --------------------------- | ------------------------- | ---------------------------------------------------------------------------- |
| 问答、检索词扩展 | Qwen2.5-7B-Instruct，FP16   | GPU 0、1 分片，各预留显存 | [Qwen 官方模型](https://huggingface.co/Qwen/Qwen2.5-7B-Instruct)，Apache-2.0 |
| 中英检索向量     | multilingual-e5-small，FP32 | GPU 2                     | [intfloat 模型](https://huggingface.co/intfloat/multilingual-e5-small)，MIT  |
| OCR              | EasyOCR 英中识别            | GPU 3                     | [EasyOCR](https://github.com/JaidedAI/EasyOCR)，Apache-2.0                   |

本次机器为 Ubuntu 22.04、4 张 11 GB RTX 2080 Ti、Python 3.12.14。使用 PyTorch 2.13.0+cu130 和 Transformers 5.17.0。实测 FP16 的 eager 注意力产生非有限数值，切换 SDPA 后恢复正常。没有使用 2080 Ti 不支持的 BF16 / FlashAttention 作为前提。

## 安装与运行

先安装 Python 3.12、Node.js 22.18+ 和适配显卡的 NVIDIA 驱动，然后在项目根目录执行：

```sh
uv venv --python 3.12
uv pip install --python .venv/bin/python -r backend/server-environment.lock.txt --extra-index-url https://download.pytorch.org/whl/cu130
npm ci
npm run build
.venv/bin/python scripts/download_models.py
cp .env.example .env
```

修改 `.env` 中两个模型的绝对路径、数据目录、监听地址与端口。模型下载阶段需要联网；服务启动后设置了离线模式，只读取已下载权重。脚本当前下载上游默认版本，复现实验时应对照已发布的模型 SHA256 清单，不能假定默认分支永远不变。

```sh
bash scripts/serve.sh
```

默认仅监听 `127.0.0.1:18474`。现有内网部署在 `.env` 中配置了服务器局域网地址，入口为 `http://192.168.3.193:18474/`。该实例未启用认证，不应直接作为公网评审入口。可选共享评审账号、本机HTTPS验收及反代配置见[评审访问控制](REVIEW_ACCESS.md)；正式域名和外网浏览器另行验收。

可安装用户级常驻服务：

```sh
bash scripts/install-service.sh
systemctl --user status yanxi
systemctl --user restart yanxi
tail -f runtime/server.log
```

用户退出后继续运行需要系统已经为该用户启用 linger。本次服务器已有该设置，没有修改其他实验服务。服务只启动一个进程，避免多 worker 重复加载 GPU 模型。

前端单独开发时 `npm run dev` 会将 `/api` 代理到本机 18474 端口。跨机器开发可先建立 SSH 本地端口转发。

## API 与验证

| 路径                          | 行为                                                   |
| ----------------------------- | ------------------------------------------------------ |
| `GET /api/health`             | 模型、向量模型及 OCR 就绪状态                          |
| `POST /api/documents`         | multipart 字段 `file`；返回排队状态或重复标记          |
| `GET /api/documents`          | 文献及后台解析状态                                     |
| `GET /api/documents/{id}/pdf` | 原始 PDF                                               |
| `POST /api/chat`              | `question` 与 `document_ids`，空列表表示全部已解析文献 |

```sh
.venv/bin/python -m unittest discover -s backend -p 'test_*.py'
npm test
npm run build
npm run format:check
```

当前 145 项后端测试覆盖页码与重叠、中文检索、版本决策、归档、持久化、文献范围隔离、非法来源与数值拦截、OCR、检索入库、翻译、引用图谱、研究计划生成、BLEU 表格坐标、跨篇引用覆盖、比较维度召回、参数表原单位读取、当前主题候选重排、研究任务摘要恢复及全站评审认证。接口测试用替身隔离模型；真实 GPU 模型的检查单独记录在 [后端验证](BACKEND_VERIFICATION.md) 及各模块回归数据中。固定提交的独立CPU安装、上传和重启复现见[复现说明](CLEAN_REPRODUCTION.md)，不替代完整GPU环境安装验收。

## 已知边界

- 支持文本层与本地 OCR 扫描页；复杂公式和表格尚未完成结构化还原。
- 支持字节去重与正文／E5 相似版本候选；已补官方 15 页扫描件与长书原件哈希核对，复杂格式及完整问答仍待回归，见 [官方样本验证](OFFICIAL_FIXTURES.md)。
- 对话为单轮证据问答，回答完成后返回；尚未加入会话记忆、Token 流式输出和语义级事实核验。
- 单模型请求串行执行；第二个并发问答返回 429。解析队列上限八个；没有完成大规模并发与长期运行压测。
- 文献可归档与恢复；支持可选共享账号入口，没有多用户文献隔离。实际公网HTTPS部署仍待验收。
- 写作基于所选摘要生成研究计划，不代表完成实验或创新性核验；自动追踪、最新研究核验、页内参考文献双向跳转等能力尚待补齐，不能视为已完成赛题六大模块。

扫描件与版本管理安装、环境变量及接口见 [OCR 与版本管理](OCR_AND_VERSIONS.md)。在三卡问答基础上，本地 OCR 使用 GPU 3；需要额外安装 `backend/requirements-ocr.txt`、系统 Poppler，并预下载权重。

arXiv 真实检索无需新增密钥，使用现有 httpx；服务器需能访问官方 API 和 PDF 域名。见 [检索与一键入库](DISCOVERY.md)。检索／下载联网，问答和中文检索词转换均在本地模型执行。

对照翻译复用现有本地 Qwen 权重，无需新增依赖或密钥；任务与缓存保存在同一 SQLite 中。使用、接口、资源限制与长书边界见 [本地论文对照翻译](TRANSLATION.md)。

引用图谱与综述接口、NetworkX／SciPy 依赖和验证见 [局域引用图谱](GRAPH.md)。构图不使用外部网络，综述使用本地 Qwen。官方 20 篇、816 页、100 条边及逐篇摘要综述的回归见 [真实图谱验收](GRAPH20.md)。

研究计划与架构图复用本地 Qwen，无新增依赖。接口、来源核对与真实验证见 [写作说明](WRITING.md)。

541 页教材、明确图号补召回、数学字体及解析警告说明见 [长文档回归](LONG_DOCUMENT.md)。更新字体依赖后需重启服务并重新解析需修复的文献，已保存正文不会自动变化。

明确作者序号的身份问题先走原生首页署名核对，机构脚注独立引用；GPU 问题补召回训练配置标题段。真实五问及八项补充验证、布局限制见 [问答改进](QA_RELIABILITY.md)。

明确语言列的单篇 BLEU 表格问题可核对坐标并返回独立 `calculation`，需系统 Poppler 的 `pdftotext`；Ubuntu 可安装 `poppler-utils`。明确选择两至六篇进行比较时保证逐篇召回和事实引用覆盖；两篇的明确编码器／解码器描述支持标注为归纳的架构对比。原生布局限制、七项真实用例及复现见 [表格与跨篇问答](COMPARISON_QA.md)。这不代表任意表格结构或复杂比较均已支持。

跨篇配置问题增加参数／上下文／LN 维度召回，六段预算逐轮分配；同页连续证据可按真实重叠扩至每段最多 2000 字符。最多八条结论仍逐条核对单一来源；有限参数表保留原始 M／B 单位，避免未经验证的中文单位换算。GPT 三篇官方题与改写验收、来源限制见 [GPT 跨篇说明](GPT_COMPARISON.md)。

数字制引用导航接口与 CPU 验收见 [引用导航](READING_LINKS.md)。

研究任务状态新增 SQLite research_tasks 表；部署前请备份数据库，任务接口与中断恢复见 [研究任务](RESEARCH_TASKS.md)。

四卡模型栈已在独立代码目录、新虚拟环境与新文献库完成复现：90个锁定依赖、28个模型文件哈希、官方15页扫描件GPU OCR、原生/OCR两条Qwen问答及162段E5向量核对通过，见[完整GPU复现](GPU_REPRODUCTION.md)。基础OS和模型权重复用原主机，依赖使用经过校验的本机缓存；不是冷下载或新操作系统证明。
