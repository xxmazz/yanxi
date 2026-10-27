"""部署前显式下载 EasyOCR 官方权重；在线服务保持 download_enabled=False。"""

import argparse
import hashlib
import json
from pathlib import Path

import easyocr

parser = argparse.ArgumentParser()
parser.add_argument("--directory", required=True)
args = parser.parse_args()


easyocr.Reader(
    ["ch_sim", "en"],
    gpu=False,
    model_storage_directory=args.directory,
    download_enabled=True,
    verbose=True,
)
easyocr.Reader(
    ["en"],
    gpu=False,
    model_storage_directory=args.directory,
    detector=False,
    download_enabled=True,
    verbose=True,
)
root = Path(args.directory)
print(
    json.dumps(
        {
            "easyocr": easyocr.__version__,
            "files": [
                {
                    "file": p.name,
                    "bytes": p.stat().st_size,
                    "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
                }
                for p in sorted(root.glob("*.pth"))
            ],
        },
        ensure_ascii=False,
        indent=2,
    )
)
