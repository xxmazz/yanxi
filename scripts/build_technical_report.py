"""从技术Markdown源稿生成嵌入中文字体的PDF；文档工具不属于模型运行依赖。"""

import argparse
import html
import re
from functools import partial
from pathlib import Path

from reportlab.graphics.shapes import Drawing, Line, Polygon, Rect, String
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.platypus.tableofcontents import TableOfContents

INK = colors.HexColor("#172B3A")
BLUE = colors.HexColor("#1F6079")
MUTED = colors.HexColor("#5E6D76")
PALE = colors.HexColor("#EDF4F6")
WIDTH = A4[0] - 90


def inline(value, baseline="1e05378"):
    text = html.escape(re.sub(r"[\u2010-\u2015]", "-", value))
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"`([^`]+)`", r'<font color="#1F6079">\1</font>', text)

    def link(match):
        label, target = match.groups()
        if not target.startswith(("https://", "http://")):
            target = f"https://github.com/xxmazz/yanxi/blob/{baseline}/docs/" + target
        return f'<a href="{target}" color="#1F6079">{label}</a>'

    return re.sub(r"\[([^\]]+)\]\(([^ )]+)\)", link, text)


def styles():
    base = dict(fontName="Body", textColor=INK, wordWrap="CJK", splitLongWords=1)
    return {
        "body": ParagraphStyle("Body", fontSize=10.4, leading=17, spaceAfter=8, **base),
        "h2": ParagraphStyle(
            "Section",
            fontName="Heading",
            fontSize=16,
            leading=23,
            textColor=BLUE,
            spaceBefore=18,
            spaceAfter=10,
            keepWithNext=True,
            wordWrap="CJK",
        ),
        "h3": ParagraphStyle(
            "Subsection",
            fontName="Heading",
            fontSize=11.8,
            leading=18,
            textColor=INK,
            spaceBefore=11,
            spaceAfter=7,
            keepWithNext=True,
            wordWrap="CJK",
        ),
        "small": ParagraphStyle(
            "Small", fontSize=8.4, leading=13, spaceAfter=6, **{**base, "textColor": MUTED}
        ),
        "table": ParagraphStyle("Cell", fontSize=8.8, leading=14, spaceAfter=0, **base),
        "code": ParagraphStyle(
            "Code",
            fontSize=8.3,
            leading=13,
            backColor=PALE,
            borderPadding=9,
            spaceBefore=4,
            spaceAfter=12,
            **base,
        ),
        "bullet": ParagraphStyle(
            "Bullet",
            fontSize=10.1,
            leading=16.5,
            leftIndent=11,
            firstLineIndent=-9,
            spaceAfter=6,
            **base,
        ),
    }


def architecture():
    drawing = Drawing(WIDTH, 210)

    def box(x, y, w, h, title, subtitle, fill=PALE):
        drawing.add(
            Rect(x, y, w, h, rx=5, ry=5, strokeColor=colors.HexColor("#C7D9E0"), fillColor=fill)
        )
        drawing.add(
            String(
                x + w / 2,
                y + h / 2 + 4,
                title,
                fontName="Heading",
                fontSize=10,
                textAnchor="middle",
                fillColor=INK,
            )
        )
        drawing.add(
            String(
                x + w / 2,
                y + h / 2 - 12,
                subtitle,
                fontName="Body",
                fontSize=8,
                textAnchor="middle",
                fillColor=MUTED,
            )
        )

    def arrow(x1, y1, x2, y2):
        drawing.add(Line(x1, y1, x2, y2, strokeColor=BLUE, strokeWidth=1))
        drawing.add(
            Polygon([x2 - 3, y2 + 5, x2, y2, x2 + 3, y2 + 5], fillColor=BLUE, strokeColor=BLUE)
        )

    box(0, 164, 150, 42, "React工作台", "阅读 / 任务 / 图谱 / 写作")
    box(174, 164, WIDTH - 174, 42, "FastAPI服务与检查点", "文献范围 / 队列 / 来源校验 / 访问控制")
    drawing.add(Line(150, 185, 174, 185, strokeColor=BLUE))
    drawing.add(Polygon([169, 188, 174, 185, 169, 182], fillColor=BLUE, strokeColor=BLUE))
    arrow(300, 164, 300, 145)
    box(
        0,
        98,
        WIDTH,
        47,
        "原文、解析与持久化",
        "pypdf / Poppler / SQLite：原文件、页面、向量、版本、任务",
    )
    arrow(WIDTH / 2, 98, WIDTH / 2, 78)
    drawing.add(
        String(
            WIDTH / 2,
            64,
            "服务按任务调用本地模型，检索与生成保留原文来源",
            fontName="Body",
            fontSize=8.4,
            textAnchor="middle",
            fillColor=MUTED,
        )
    )
    gap = 9
    w = (WIDTH - 3 * gap) / 4
    box(0, 4, w, 46, "Qwen2.5-7B", "GPU 0、1：语言生成")
    box(w + gap, 4, w, 46, "E5-small", "GPU 2：向量检索")
    box(2 * (w + gap), 4, w, 46, "Qwen3-VL-4B", "GPU 2：表格 / 公式")
    box(3 * (w + gap), 4, w, 46, "EasyOCR", "GPU 3：扫描文字")
    return drawing


class Report(BaseDocTemplate):
    def __init__(self, target, baseline, date):
        super().__init__(
            str(target),
            pagesize=A4,
            leftMargin=45,
            rightMargin=45,
            topMargin=53,
            bottomMargin=48,
            title="研析 - 本地GPU科研助手技术方案与实现说明",
            author="研析项目 / 团队信息待补",
            pageCompression=1,
        )
        self.baseline, self.date = baseline, date
        frame = Frame(
            self.leftMargin,
            self.bottomMargin,
            self.width,
            self.height,
            leftPadding=0,
            rightPadding=0,
            topPadding=0,
            bottomPadding=0,
        )
        self.addPageTemplates(PageTemplate(id="report", frames=[frame], onPage=self.draw_page))

    def draw_page(self, canvas, doc):
        canvas.saveState()
        if doc.page > 1:
            canvas.setFont("Body", 8)
            canvas.setFillColor(MUTED)
            canvas.drawString(45, A4[1] - 30, "研析  /  技术方案与实现说明")
            canvas.drawRightString(A4[0] - 45, A4[1] - 30, "技术内容定稿 · 团队信息待补")
            canvas.setStrokeColor(colors.HexColor("#D9E4E9"))
            canvas.line(45, A4[1] - 38, A4[0] - 45, A4[1] - 38)
        canvas.setFont("Body", 8)
        canvas.setFillColor(MUTED)
        canvas.drawString(45, 28, f"源码基线 {self.baseline}  /  {self.date}")
        canvas.drawRightString(A4[0] - 45, 28, f"{doc.page:02d}")
        canvas.restoreState()

    def afterFlowable(self, flowable):
        if isinstance(flowable, Paragraph) and hasattr(flowable, "section_id"):
            text = flowable.getPlainText()
            key = "section-" + str(getattr(flowable, "section_id", 0))
            self.canv.bookmarkPage(key)
            self.canv.addOutlineEntry(text, key, level=0)
            self.notify("TOCEntry", (0, text, self.page, key))


def build(source, output, font, bold_font, baseline, date):
    pdfmetrics.registerFont(TTFont("Body", str(font), subfontIndex=0))
    pdfmetrics.registerFont(TTFont("Heading", str(bold_font), subfontIndex=0))
    pdfmetrics.registerFontFamily(
        "Body", normal="Body", bold="Heading", italic="Body", boldItalic="Heading"
    )
    s = styles()
    markup = partial(inline, baseline=baseline)
    story = [Spacer(1, 62)]
    story.append(Paragraph("YANXI / LOCAL RESEARCH WORKSPACE", s["small"]))
    story.append(Spacer(1, 17))
    story.append(
        Paragraph(
            "研析",
            ParagraphStyle(
                "CoverTitle", fontName="Heading", fontSize=48, leading=60, textColor=INK
            ),
        )
    )
    story.append(
        Paragraph(
            "本地GPU科研辅助系统",
            ParagraphStyle("Subtitle", fontName="Heading", fontSize=22, leading=33, textColor=BLUE),
        )
    )
    story.append(Spacer(1, 27))
    story.append(Paragraph("技术方案与实现说明", s["h2"]))
    story.append(Paragraph("从文献原件到可回看的证据，再到检索、问答、阅读与研究写作。", s["body"]))
    story.append(Spacer(1, 24))
    items = [
        ["4张GPU", "20篇 / 100边", "15 / 15页", "6个模块"],
        ["本地模型设备", "固定引用图谱样本", "官方扫描件OCR", "科研核心工作流"],
    ]
    matrix = Table(
        [[Paragraph(markup(v), s["table"]) for v in row] for row in items],
        colWidths=[WIDTH / 4] * 4,
    )
    matrix.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), PALE),
                ("TOPPADDING", (0, 0), (-1, -1), 12),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("LINEBELOW", (0, 0), (-1, 0), 0.4, colors.HexColor("#D0E0E6")),
            ]
        )
    )
    story.extend([matrix, Spacer(1, 26)])
    for text in [
        f"文档日期：{date}",
        f"实现基线：{baseline}",
        "团队名称 / 成员 / 学校与学院：信息待补",
        "状态：技术内容定稿，团队信息待补；视频与公网由参赛者安排",
    ]:
        story.append(Paragraph(text, s["body"]))
    story.extend(
        [
            Spacer(1, 16),
            Paragraph(
                "各数字对应不同测试范围，正文分别列出证据及限制；不构成项目整体完成率或通用性能承诺。",
                s["small"],
            ),
            PageBreak(),
        ]
    )
    story.append(Paragraph("文档导航", s["h2"]))
    toc = TableOfContents()
    toc.levelStyles = [
        ParagraphStyle(
            "Contents",
            fontName="Body",
            fontSize=10.4,
            leading=23,
            textColor=INK,
            wordWrap="CJK",
            leftIndent=0,
            rightIndent=10,
        )
    ]
    story.extend(
        [
            toc,
            Spacer(1, 18),
            Paragraph(
                "阅读提示：先看系统架构与AI方案，再结合测试结果审查实现范围。本文中的研究计划、确定性归纳和模型输出分别说明，不把计划当实验结论。",
                s["body"],
            ),
            PageBreak(),
        ]
    )

    lines = source.read_text().splitlines()
    index, section, diagram = 0, 0, False
    while index < len(lines):
        line = lines[index].strip()
        index += 1
        if not line or line.startswith("# ") or re.fullmatch(r"---+", line):
            continue
        if line.startswith("## "):
            section += 1
            item = Paragraph(markup(line[3:]), s["h2"])
            item.section_id = section
            story.append(item)
            if "架构" in line and not diagram:
                story.extend(
                    [
                        architecture(),
                        Paragraph(
                            "图1  服务层、原文持久化与四卡模型调用关系。PDF解析调用OCR，混合检索调用E5，生成任务调用Qwen。",
                            s["small"],
                        ),
                    ]
                )
                diagram = True
            continue
        if line.startswith("### "):
            story.append(Paragraph(markup(line[4:]), s["h3"]))
            continue
        if line.startswith("```"):
            code = []
            while index < len(lines) and not lines[index].strip().startswith("```"):
                code.append(html.escape(lines[index]).replace(" ", "&nbsp;"))
                index += 1
            index += 1
            story.append(Paragraph("<br/>".join(code) or " ", s["code"]))
            continue
        if line.startswith("|"):
            rows = [line]
            while index < len(lines) and lines[index].strip().startswith("|"):
                rows.append(lines[index].strip())
                index += 1
            data = [[cell.strip() for cell in row.strip("|").split("|")] for row in rows]
            data = [row for row in data if not all(re.fullmatch(r":?-{2,}:?", v) for v in row)]
            columns = max(len(row) for row in data)
            content = [
                [Paragraph(markup(cell), s["table"]) for cell in row + [""] * (columns - len(row))]
                for row in data
            ]
            table = Table(
                content, colWidths=[WIDTH / columns] * columns, repeatRows=1, hAlign="LEFT"
            )
            table.setStyle(
                TableStyle(
                    [
                        ("VALIGN", (0, 0), (-1, -1), "TOP"),
                        ("BACKGROUND", (0, 0), (-1, 0), PALE),
                        ("LINEBELOW", (0, 0), (-1, 0), 0.7, BLUE),
                        ("LINEBELOW", (0, 1), (-1, -1), 0.3, colors.HexColor("#DCE5EA")),
                        ("TOPPADDING", (0, 0), (-1, -1), 7),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
                        ("LEFTPADDING", (0, 0), (-1, -1), 7),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 7),
                    ]
                )
            )
            story.extend([table, Spacer(1, 12)])
            continue
        if re.match(r"(?:[-*] |\d+[.)] )", line):
            story.append(Paragraph(markup(line), s["bullet"]))
            continue
        paragraph = [line]
        while (
            index < len(lines)
            and lines[index].strip()
            and not re.match(r"#{1,3} |\||```|[-*] |\d+[.)] ", lines[index].strip())
        ):
            paragraph.append(lines[index].strip())
            index += 1
        story.append(Paragraph(markup(" ".join(paragraph)), s["body"]))
    output.parent.mkdir(parents=True, exist_ok=True)
    Report(output, baseline, date).multiBuild(story)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path("docs/TECHNICAL_REPORT_DRAFT.md"))
    parser.add_argument("--output", type=Path, default=Path("output/pdf/yanxi-technical-draft.pdf"))
    parser.add_argument(
        "--font", type=Path, default=Path("/System/Library/Fonts/STHeiti Light.ttc")
    )
    parser.add_argument(
        "--bold-font", type=Path, default=Path("/System/Library/Fonts/STHeiti Medium.ttc")
    )
    parser.add_argument("--baseline", default="1e05378")
    parser.add_argument("--date", default="2026-10-31")
    args = parser.parse_args()
    build(args.source, args.output, args.font, args.bold_font, args.baseline, args.date)
    print(args.output.resolve())


if __name__ == "__main__":
    main()
