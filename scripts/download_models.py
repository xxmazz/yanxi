"""显式下载阶段使用网络，运行服务时只读本地模型。"""

import argparse
from pathlib import Path

from modelscope import snapshot_download

parser = argparse.ArgumentParser()
parser.add_argument("--directory", type=Path, default=Path("models"))
args = parser.parse_args()
for model, folder in [
    ("Qwen/Qwen2.5-7B-Instruct", "Qwen2.5-7B-Instruct"),
    ("intfloat/multilingual-e5-small", "multilingual-e5-small"),
]:
    snapshot_download(
        model,
        local_dir=str(args.directory / folder),
        ignore_patterns=["*.bin", "*.pt", "onnx/*", "openvino/*"],
    )
print("下载完成。发布前应保存实际模型文件的 SHA256 清单。")
