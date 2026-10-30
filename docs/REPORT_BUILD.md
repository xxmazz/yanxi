# 技术文档构建与审阅

技术内容已定稿、真实团队信息待本人补齐。技术源稿为 [技术源稿](TECHNICAL_REPORT_DRAFT.md)，交付预览为 [技术 PDF](../output/pdf/yanxi-technical-draft.pdf)。团队信息按本人确认暂标“信息待补”。技术内容已更新到2026-10-30工程收尾，文档描述的工程基线为 `e47f644`，文档自身由之后的真实提交管理；演示视频仍只有 [七分钟脚本](DEMO_SCRIPT.md)，尚未录制发布。

## 独立文档环境

生成器不导入模型、不读取 `.env`、不访问数据库，也不会启动 GPU。文档依赖单独保存，不安装进现有服务环境：

```sh
uv venv --python 3.12 .venv-docs
uv pip install --python .venv-docs/bin/python -r scripts/requirements-docs.txt
.venv-docs/bin/python scripts/build_technical_report.py \
  --source docs/TECHNICAL_REPORT_DRAFT.md \
  --output output/pdf/yanxi-technical-draft.pdf \
  --font /System/Library/Fonts/STHeiti\ Light.ttc \
  --bold-font /System/Library/Fonts/STHeiti\ Medium.ttc \
  --baseline e47f644 --date 2026-10-30
```

示例字体路径适用于本次 Mac。其他机器通过两个字体参数指定有相应使用权限、含中文字形且受 ReportLab 支持的 TrueType 文件；TTC 使用第0个子字体。不要把系统字体源文件复制进仓库。正文字体以子集嵌入 PDF，读者无需另装字体。`.venv-docs` 也应位于忽略目录；本次使用工具提供的独立文档运行时。

`--baseline` 同时控制页脚与仓库证据链接的版本。改为其他工程版本时，须重新核对正文和证据，不能只改封面日期。PDF 创建时间可随构建变化，页面内容可复现不代表字节完全相同。

## 导出后检查

- 用 pypdf 重新打开，检查页数不超过30、中文可提取、各章节及目录存在、没有加密或 JavaScript。
- 检查两个中文字体已嵌入，PDF 的仓库证据链接与 Markdown 相对链接均有目标。
- 用 Poppler 逐页渲染到临时目录，查看所有页面及架构图、跨页表格：正文无重叠截断，字号可读，页眉页脚和目录页码对应。修改后重新导出并复查。
- 补齐团队信息、最终部署与视频状态后再定稿；初稿页数合格不等于材料已正式提交。

本轮可核对记录见 [PDF 交付检查](TECHNICAL_REPORT_VERIFICATION_20261030.json)。渲染中间图不入库，不分发论文原件、凭据、模型或生产数据库。

## 2026-10-30 内容定稿

工程基线e47f644；已更新169项后端、13项前端、12组真实Chromium页面流程、9个文件落盘及连续追问结果。PDF共10页、50个独立外链、4个中文字体子集均嵌入。目录及Git证据链接目标核对通过；10页渲染总览已查看，架构、测试表和跨页来源表另做细看，无截断重叠。团队信息待本人填写，不能以技术内容完成代替真实报名信息。旧稿的9页与145/5记录保留在Git历史，本文件关联JSON指向当前PDF。
