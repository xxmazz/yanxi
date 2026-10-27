# 本地扫描件与论文版本管理

## 处理流程

PDF 保存后进入有界队列，由单个后台任务逐页处理。优先读取文本层；文字稀少且含图像的页面使用 Poppler 渲染与 EasyOCR。每页正文、物理页码、像素坐标、识别置信度单独存入 SQLite，前端显示已处理页数及 OCR 页数。OCR 使用 GPU 3，问答模型继续使用 GPU 0/1，E5 使用 GPU 2。

EasyOCR 的 CRAFT 检测器共享中英文文字区域，简体中文及英文识别头在本地运行；根据首轮结果的汉字比例选择识别头。在线服务禁止自动下载权重。单／双栏排序与语言选择属于启发式，公式和复杂表格目前保留识别文本及原图，不保证 LaTeX 或表格结构还原。

每篇文献计算正文五词组的 bottom-k 指纹及 E5 文档均值向量。词组重合、长度比例、标题相似及正文 arXiv 标识共同筛选候选；文件名不参与相似判断。正文 arXiv 版本号只在内容有实质重合时用于建议保留新版。语义余弦与词组重合不是事实置信度，不能保证所有改写、翻译或扫描噪声都被识别。

相似文献进入 `review`，确认前不参与问答。支持保留库中版本、使用新上传版本、两版均保留；存在多个候选时逐一确认。版本处理在数据库事务中执行并记录决策，原 PDF 与旧索引保留。归档与已替换版本可恢复。

## 安装与模型来源

使用项目的 Python 3.12 环境，先安装问答依赖，再安装 OCR：

```sh
uv pip install --python .venv/bin/python -r backend/requirements-model.txt \
  -r backend/requirements-ocr.txt \
  --extra-index-url https://download.pytorch.org/whl/cu130
# Ubuntu/Debian 系统依赖：poppler-utils（提供 pdftoppm）
.venv/bin/python scripts/download_ocr.py --directory /absolute/path/to/easyocr
```

EasyOCR 来源：[JaidedAI/EasyOCR](https://github.com/JaidedAI/EasyOCR)，代码许可证 Apache-2.0。使用官方 Release 的 CRAFT、zh_sim_g2、english_g2 权重；部署时按官方 MD5 核对，SHA256 记录见 [OCR 权重清单](OCR_MODELS_20261027.json)。模型文件不提交至仓库。

`.env` 中配置：

```dotenv
YANXI_OCR_PATH=/absolute/path/to/easyocr
YANXI_OCR_DEVICE=cuda:3
```

未配置 OCR 时，普通文本 PDF 仍可解析；遇到扫描页明确报错，不把未识别的文献当作已完成索引。中途失败保留错误和已解析页；可重新解析。旧数据库自动增补字段与页表，不移除已有文献。

## 页面与 API

- 文献卡片显示进度、OCR 页数、版本提示、重新解析与归档。
- 阅读页并排呈现原页图片和该物理页正文，保留原 PDF 下载链接；OCR 文字可展开查看坐标与识别置信度。
- `GET /api/documents?include_archived=true` 查看全部版本。
- `GET /api/documents/{id}/pages/{number}` 查看本页解析结果。
- `GET /api/documents/{id}/render/{number}` 缓存渲染本页原图，兼容不内置 PDF 查看器的浏览器。
- `POST /api/documents/{id}/reindex` 重新排队。
- `POST /api/documents/{id}/resolve` 请求体含 `existing_id` 及 `action`：`keep_existing`、`replace_existing`、`keep_both`。
- `POST /api/documents/{id}/archive`、`restore` 进行可恢复管理。

## 本轮验证

自动化覆盖旧数据库迁移、版本检索隔离、多个候选逐一决策、归档恢复、原文件留存、扫描／文本／空白页分流及接口错误状态。

真实版本样本使用 arXiv `1706.03762v1` 与 `v7`，新上传文件改名后仍识别为同一论文，词组重合约 80%，E5 均值余弦约 0.9992，建议保留 v7；网页操作已验证。

扫描样本由 v1 的 15 页逐页栅格化生成，仅用于无文本层流程验证；不能替代官网 `1706.03762v1_img.pdf` 的正式回归。541 页指定教材、中文扫描件质量、复杂公式／表格还原及大规模文献误报率仍需单独评测。

15 页扫描样本首次入库耗时约 92.9 秒；模型问答约 4.15 秒，正确回答前馈网络内部维度 2048，摘录逐字匹配物理第 5 页保存的 OCR 正文。原生 v7 与扫描样本的词组重合约 69.62%、语义余弦约 0.9978，触发相似提示；因扫描边栏 arXiv 标识未可靠识别，不推测扫描件版本号。本轮保留两份用于对照。原始结果见 [真实服务检查](DOCUMENT_VERIFICATION_20261027.json)。
