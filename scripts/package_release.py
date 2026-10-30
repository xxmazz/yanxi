"""将干净Git提交打包为工程源码快照，附原始提交号和SHA-256；不打包运行数据。"""

import argparse
import hashlib
import json
import subprocess
from pathlib import Path


def git(*args):
    return subprocess.check_output(["git", *args], text=True).strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("output/release"))
    args = parser.parse_args()
    root = Path(git("rev-parse", "--show-toplevel"))
    if Path.cwd().resolve() != root:
        raise SystemExit("请从仓库根目录执行")
    if git("status", "--porcelain"):
        raise SystemExit("存在未提交修改，先形成可复核的真实提交后再打包")
    revision = git("rev-parse", "HEAD")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    archive = args.output_dir / f"yanxi-engineering-{revision[:7]}.zip"
    if archive.exists():
        raise SystemExit("此版本工程包已存在，保留原文件，不覆盖")
    subprocess.run(
        [
            "git",
            "archive",
            "--format=zip",
            "--prefix=yanxi/",
            "--output",
            str(archive.resolve()),
            revision,
        ],
        check=True,
    )
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    manifest = {
        "kind": "engineering-source-snapshot",
        "revision": revision,
        "commit_url": f"https://github.com/xxmazz/yanxi/commit/{revision}",
        "archive": archive.name,
        "bytes": archive.stat().st_size,
        "sha256": digest,
        "contents": "仅Git跟踪文件，包含源码、锁定依赖清单、模板及验证资料。模型和运行数据需按说明另行准备。",
        "history": "源码快照不含.git历史；完整真实开发历史保留在GitHub仓库。",
        "status": "工程验收候选包；不表示公网、浏览器落盘、团队信息、公开视频或比赛提交均已完成。",
    }
    (args.output_dir / f"{archive.stem}.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    )
    (args.output_dir / f"{archive.stem}.sha256").write_text(f"{digest}  {archive.name}\n")
    print(archive.resolve())


if __name__ == "__main__":
    main()
