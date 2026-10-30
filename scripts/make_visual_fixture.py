"""生成明确标为合成的两页扫描表格夹具；需独立文档依赖和Poppler。"""

import argparse
import subprocess
import tempfile
from pathlib import Path

from reportlab.lib.utils import ImageReader
from reportlab.pdfgen.canvas import Canvas


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit("保留已有回归文件，请指定新路径")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as directory:
        source = Path(directory) / "native.pdf"
        canvas = Canvas(str(source), pagesize=(612, 792), invariant=1)
        for number, rows in enumerate(
            [
                (("Alpha", "10", "20"), ("Beta", "15", "25")),
                (("Gamma", "30", "40"), ("Delta", "50", "60")),
            ],
            1,
        ):
            canvas.setFont("Helvetica-Bold", 16)
            canvas.drawString(55, 730, f"Cross-page regression table - page {number}")
            canvas.setFont("Helvetica", 16)
            for y in [692, 652, 612, 572]:
                canvas.line(55, y, 550, y)
            for x in [55, 300, 425, 550]:
                canvas.line(x, 572, x, 692)
            for y, row in zip([668, 628, 588], [("Model", "Score", "Cost"), *rows]):
                for x, text in zip([65, 315, 440], row):
                    canvas.drawString(x, y, text)
            canvas.drawString(55, 508, "Synthetic scan fixture for cross-page import verification.")
            canvas.showPage()
        canvas.save()
        subprocess.run(
            ["pdftoppm", "-r", "144", "-png", str(source), str(Path(directory) / "page")],
            check=True,
            capture_output=True,
        )
        scanned = Canvas(str(args.output), pagesize=(612, 792), invariant=1)
        for image in sorted(Path(directory).glob("page-*.png")):
            scanned.drawImage(ImageReader(str(image)), 0, 0, width=612, height=792)
            scanned.showPage()
        scanned.save()
    print(args.output)


if __name__ == "__main__":
    main()
