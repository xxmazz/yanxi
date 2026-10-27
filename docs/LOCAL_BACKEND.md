# 本地模型后端

本阶段打通真实 PDF 入库、按页索引、混合检索、本地模型问答和原文页码回看。前端与 FastAPI 同源部署，模型推理不调用商业云端服务。

## 处理流程

1. PDF 上传后立即保存 SHA256、文件名和解析状态；单文件上限 100 MB。
2. 单个后台工作线程用 pypdf 逐页提取文本，按 1100 字符切块、重叠 160 字符，保留 PDF 的物理页码与块内偏移。当前上限 650 页；本轮已验证作者公开的同名 541 页教材；尚未与组委会资料包比对哈希。
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

默认仅监听 `127.0.0.1:18474`。本次内网部署在 `.env` 中配置了服务器局域网地址，入口为 `http://192.168.3.193:18474/`。这是内部开发实例，没有登录隔离，不应直接作为公网评审入口。

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

当前 72 项后端测试覆盖页码与重叠、中文检索、版本决策、归档、持久化、文献范围隔离、非法来源与数值拦截、OCR、检索入库、翻译、引用图谱及研究计划生成。接口测试用替身隔离模型；真实 GPU 模型的检查单独记录在 [后端验证](BACKEND_VERIFICATION.md) 及各模块回归数据中。

## 已知边界

- 支持文本层与本地 OCR 扫描页；复杂公式和表格尚未完成结构化还原。
- 支持字节去重与正文／E5 相似版本候选；官方扫描件、长书和复杂格式仍待完整回归。
- 对话为单轮证据问答，回答完成后返回；尚未加入会话记忆、Token 流式输出和语义级事实核验。
- 单模型请求串行执行；第二个并发问答返回 429。解析队列上限八个；没有完成大规模并发与长期运行压测。
- 文献可归档与恢复；暂无用户权限隔离和公网 HTTPS 部署。
- 写作基于所选摘要生成研究计划，不代表完成实验或创新性核验；自动追踪、最新研究核验、页内参考文献双向跳转等能力尚待补齐，不能视为已完成赛题六大模块。

扫描件与版本管理安装、环境变量及接口见 [OCR 与版本管理](OCR_AND_VERSIONS.md)。在三卡问答基础上，本地 OCR 使用 GPU 3；需要额外安装 `backend/requirements-ocr.txt`、系统 Poppler，并预下载权重。

arXiv 真实检索无需新增密钥，使用现有 httpx；服务器需能访问官方 API 和 PDF 域名。见 [检索与一键入库](DISCOVERY.md)。检索／下载联网，问答和中文检索词转换均在本地模型执行。

对照翻译复用现有本地 Qwen 权重，无需新增依赖或密钥；任务与缓存保存在同一 SQLite 中。使用、接口、资源限制与长书边界见 [本地论文对照翻译](TRANSLATION.md)。

引用图谱与综述接口、NetworkX／SciPy 依赖和验证见 [局域引用图谱](GRAPH.md)。构图不使用外部网络，综述使用本地 Qwen。

研究计划与架构图复用本地 Qwen，无新增依赖。接口、来源核对与真实验证见 [写作说明](WRITING.md)。

541 页教材、明确图号补召回、数学字体及解析警告说明见 [长文档回归](LONG_DOCUMENT.md)。更新字体依赖后需重启服务并重新解析需修复的文献，已保存正文不会自动变化。
