"""显式下载并核对Qwen3-VL视觉模型；推理阶段始终只读取本地文件。"""

import argparse
import hashlib
import json
from pathlib import Path

from huggingface_hub import snapshot_download

REPO = "Qwen/Qwen3-VL-4B-Instruct"
REVISION = "ebb281ec70b05090aa6165b016eac8ec08e71b17"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=Path("models/Qwen3-VL-4B-Instruct"))
    args = parser.parse_args()
    snapshot_download(
        REPO,
        revision=REVISION,
        local_dir=args.directory,
        allow_patterns=["*.json", "*.safetensors", "*.txt", "*.jinja", "README.md", "LICENSE"],
        max_workers=2,
    )
    files = []
    for path in sorted(args.directory.iterdir()):
        if not path.is_file() or path.name == "yanxi-model-manifest.json":
            continue
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for part in iter(lambda: stream.read(8 * 1024 * 1024), b""):
                digest.update(part)
        files.append(
            {"name": path.name, "bytes": path.stat().st_size, "sha256": digest.hexdigest()}
        )
    payload = {"repository": REPO, "revision": REVISION, "files": files}
    (args.directory / "yanxi-model-manifest.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
