# 研析

研析是一个使用本地模型的科研文献工作台。上传 PDF 后，可以阅读原文、翻译段落、针对单篇或多篇论文提问，也可以整理引用关系和研究计划。回答附带摘录与页码，方便回到论文核对。

## 团队与贡献

负责人 GitHub 账号：[xxmazz](https://github.com/xxmazz)。
马兴贤 东莞理工学院

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

完整部署需要 Python 3.12、Node.js 22.18+、NVIDIA 驱动和本地模型权重。

```sh
git clone https://github.com/xxmazz/yanxi.git
cd yanxi
uv venv --python 3.12
uv pip install --python .venv/bin/python -r backend/server-environment.lock.txt --extra-index-url https://download.pytorch.org/whl/cu130
npm ci
npm run build
cp .env.example .env
```
启动命令

```sh
bash scripts/serve.sh
```

```sh
systemctl --user start yanxi
systemctl --user status yanxi --no-pager
# 用完释放显卡
systemctl --user stop yanxi
```


## 架构

前端使用 React、TypeScript 和 Vite；后端使用 FastAPI、SQLite 和 PyTorch。PDF 经文本提取或 OCR 后按页切块，E5 向量检索与 BM25 经 RRF 合并召回，再由 Qwen 根据摘录作答。引用关系根据论文书目匹配，由 NetworkX 计算局部排名。

| 模块             | 模型                       | 当前显卡分配 |
| ---------------- | -------------------------- | ------------ |
| 问答、翻译与写作 | Qwen2.5-7B-Instruct，FP16  | GPU 0、1     |
| 语义检索         | multilingual-e5-small      | GPU 2        |
| 扫描表格与公式   | Qwen3-VL-4B-Instruct，FP16 | GPU 2        |
| 扫描文字         | EasyOCR                    | GPU 3        |

上传文件、解析结果和向量保存在服务器。

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
