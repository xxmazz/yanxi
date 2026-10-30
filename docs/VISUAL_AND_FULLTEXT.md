# 扫描结构识别与研究方向正文核对

2026-10-31，使用本地视觉模型补充原生PDF结构提取，并将研究方向对照延伸至所选论文正文。模型权重、论文原件、数据库和失败原始输出不进入公开仓库。

## 原页框选与导出

在沉浸阅读选择文献和物理页，点击“扫描表格 / 公式 LaTeX 识别”。拖动原页框选单个表格或公式。表格可指定包含名称列的预期列数，多层表头按叶子列展开；公式输出可复制的LaTeX文本。原始PDF不改变，结果保留物理页、选区、页面正文SHA-256、模型身份及时间，可跳回原PDF核对。

表格导出带BOM的CSV，保留空字符串和跨列null，转义引号及电子表格公式前缀。连续两页续表可先保存本页选区，再框选下一页；仅在两页列数一致时合并，仅删除完全相同的重复表头，同时导出各页独立来源JSON。不跨不同文献自动拼接，不把不同表头强行当成同一表。

扫描表格先生成完整候选；如果各数据行名称能与已有OCR坐标唯一匹配，按原图行位置保留表头并裁剪单行，逐行再次视觉识别，防止空格导致上下行串值。OCR提供行位置；单行视觉输出中的数值若与OCR单元块精确相同，且叶子表头位置明确，再按横向坐标校正错列。OCR不补造数值，数字和科学计数法仍从原图转写。行名歧义或不匹配不启用该复核；输出保留是否进行逐行复核。列数、矩形行长、类型和长度异常会拒绝导出。原始整体/逐行模型输出存于私有版本缓存便于检查。

这是视觉转写，不保证任意复杂公式或表格准确。原生PDF仍可用已有“提取本页表格与编号公式”，根据字形与水平线提取而不用视觉模型。视觉LaTeX只作为文本导出，不执行编译；拒绝文件读写类命令。

## 本地模型准备与四卡部署

使用上游[Qwen3-VL-4B-Instruct](https://huggingface.co/Qwen/Qwen3-VL-4B-Instruct)，Apache-2.0。下载脚本固定revision，不追随默认分支：

```sh
.venv/bin/python scripts/download_vision.py --directory /绝对路径/Qwen3-VL-4B-Instruct
```

如下载源需要镜像，可仅对下载命令设置HF_ENDPOINT和HF_HUB_DISABLE_XET；推理不联网。真实文件哈希见[VISION_MODEL_20261031.json](VISION_MODEL_20261031.json)。下载脚本将相同清单保存于权重目录，模型结果身份包含配置及清单摘要。

在.env设置YANXI_VISION_PATH为该绝对路径、YANXI_VISION_DEVICE=cuda:2。四张2080Ti分工为Qwen2.5-7B FP16占GPU0/1，E5-small与Qwen3-VL-4B FP16占GPU2，EasyOCR占GPU3。视觉使用SDPA、每次一个选区，输入图像最长1400像素、最大6500输入token、最大2800生成token。GPU为11GB级别，保持其他任务空闲；不得把显卡序号指向已被其他工程占满的卡。OCR、向量和视觉模型均本地加载。

接口：POST /api/documents/{id}/pages/{page}/visual-structure，JSON含kind（table/equation）、bbox（0至1四坐标）、expected_columns（0为自动）和refresh。无效原文、归档、版本变化或解析未完成会拒绝；缓存按原件/页哈希/模型/选区/列数/算法版本隔离。/api/health增加vision_ready，状态就绪不能代替识别质量验收。

## 对照较新论文正文

引用图谱先选择原方向文献并构图。在“对照已入库较新论文正文”选择最多4篇已解析的较新论文，输入主题并核对。缺少论文可从探索发现下载，完成解析后刷新可选列表。对照文献必须具有已知且更晚的年份，不能与原方向重叠。

保留原文Future Work段落，排除对照论文的已定位摘要和书目区，以BM25/E5召回正文，优先不同物理页，每篇最多3段。Qwen逐篇概括，原句编号及数值经过已有来源检查；无效输出不作为有效概括。返回原页、Unicode字符范围与页面SHA-256。生成前后检查两组文献指纹，原文变化拒绝旧结果。可导出完整JSON，不能由有限检索声称idea从未被尝试、或某方向已全部解决。

接口：POST /api/graph/fulltext-check，在已有document_ids/fingerprint/topic之外加入target_document_ids。

## 验证方式

真实四卡、独立无头Chromium、隔离文献库运行scripts/verify_visual_fulltext.mjs；不申请桌面鼠标权限、不使用API替身。检查官方扫描Transformer五列空格、原生及扫描公式、独立两页扫描夹具续表、RAG到Ragas正文页证据、390像素页面和实际下载。完整结果见[本轮验证](VISUAL_FULLTEXT_VERIFICATION_20261031.json)。两页夹具明确为合成回归文件，不冒充官方论文。

```sh
node scripts/verify_visual_fulltext.mjs \
  --config runtime/private-loopback-config.json \
  --output runtime/visual-新的目录 \
  --crosspage /你的两页扫描夹具.pdf \
  --playwright-module /独立Playwright目录/index.mjs \
  --executable /匹配Chromium可执行文件
```

可在独立文档环境执行 `python scripts/make_visual_fixture.py --output runtime/new-cross-page-scan.pdf` 生成不含文本层的两页合成扫描表格。夹具只用于回归，不是比赛官方样本。

私有配置含base_url及isolatedLibrary=true，脚本仅允许非生产端口的回环隔离实例。正式文献库不用于上传、OCR和模型回归。运行结束停止隔离服务释放显卡。
