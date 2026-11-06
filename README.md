# 研析

研析是一个使用本地模型的科研文献工作台。上传 PDF 后，可以阅读原文、翻译段落、针对单篇或多篇论文提问，也可以整理引用关系和研究计划。回答附带摘录与页码，方便回到论文核对。

## 功能

| 页面     | 用途                                                                           |
| -------- | ------------------------------------------------------------------------------ |
| 我的文献 | 批量上传 PDF、扫描件 OCR、内容去重、相似版本选择、星标与归档                   |
| 沉浸阅读 | 原页与解析正文、章节目录、引用跳转、选句与整篇翻译、表格 CSV 和公式 LaTeX 导出 |
| 文献问答 | 在所选论文中检索，支持接着上文提问、跨篇比较和原文回看                         |
| 探索发现 | arXiv 检索与下载、参考文献批量获取、主题订阅、研究任务                         |
| 引用图谱 | 查看库内引用关系、局部排名、摘要综述，结合较新论文核对研究方向                 |
| 写作工坊 | 根据所选摘要起草研究计划，编辑 Markdown，导出方法图和 CSV 数据图表             |

模型在本地运行。论文检索、下载和主题订阅需要连接 arXiv；问答、翻译和图片识别读取本机权重。

## 安装与启动

完整部署需要 Python 3.12、Node.js 22.18+、NVIDIA 驱动和本地模型权重。当前部署使用四张 RTX 2080 Ti。依赖版本见 [环境锁文件](backend/server-environment.lock.txt)，模型准备和系统依赖见 [后端安装说明](docs/LOCAL_BACKEND.md)。

```sh
git clone https://github.com/xxmazz/yanxi.git
cd yanxi
uv venv --python 3.12
uv pip install --python .venv/bin/python -r backend/server-environment.lock.txt --extra-index-url https://download.pytorch.org/whl/cu130
npm ci
npm run build
cp .env.example .env
```

按 [模型与 OCR 配置](docs/OCR_AND_VERSIONS.md) 和 [视觉模型配置](docs/VISUAL_AND_FULLTEXT.md) 准备权重，再填写 `.env` 中的模型路径、数据目录和显卡编号。

```sh
bash scripts/serve.sh
```

默认入口为 `http://127.0.0.1:18474/`，前端和 API 由同一服务提供。模型加载完成后，页面会显示“模型就绪”。

已经安装用户级服务的机器可以直接运行：

```sh
systemctl --user start yanxi
systemctl --user status yanxi --no-pager
# 用完释放显卡
systemctl --user stop yanxi
```

公网部署使用 HTTPS 反向代理，并配置评审账号；具体步骤见 [访问控制](docs/REVIEW_ACCESS.md)。当前局域网部署地址为 `http://192.168.3.193:18474/`。

只开发前端时运行 `npm run dev`，访问 `http://localhost:4173`。开发服务将 `/api` 转发到本机 18474 端口；没有后端时只能预览界面和浏览器内的 PDF。

## 架构

前端使用 React、TypeScript 和 Vite；后端使用 FastAPI、SQLite 和 PyTorch。PDF 经文本提取或 OCR 后按页切块，E5 向量检索与 BM25 经 RRF 合并召回，再由 Qwen 根据摘录作答。引用关系根据论文书目匹配，由 NetworkX 计算局部排名。

| 模块             | 模型                       | 当前显卡分配 |
| ---------------- | -------------------------- | ------------ |
| 问答、翻译与写作 | Qwen2.5-7B-Instruct，FP16  | GPU 0、1     |
| 语义检索         | multilingual-e5-small      | GPU 2        |
| 扫描表格与公式   | Qwen3-VL-4B-Instruct，FP16 | GPU 2        |
| 扫描文字         | EasyOCR                    | GPU 3        |

上传文件、解析结果和向量保存在服务器。仓库不包含论文原件、模型权重、数据库或密钥。

| 目录       | 内容                           |
| ---------- | ------------------------------ |
| `src/`     | 前端页面与样式                 |
| `backend/` | 模型加载、索引、API 与后端测试 |
| `tests/`   | 前端组件和工具测试             |
| `scripts/` | 服务启动、验证与打包           |
| `docs/`    | 安装、功能说明和验证资料       |

## 测试与使用说明

```sh
npm test
npm run build
npm run format:check
.venv/bin/python -m unittest discover -s backend -t . -q
```

## 团队与贡献

负责人 GitHub 账号：[xxmazz](https://github.com/xxmazz)。
马兴贤 东莞理工学院
