"""交互生成评审密码哈希；不接受命令行明文密码，不显示密码。"""

import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.access import password_hash  # noqa: E402


def main():
    first = getpass.getpass("评审密码（至少12位，不回显）：")
    second = getpass.getpass("再次输入：")
    if first != second:
        raise ValueError("两次密码不一致")
    print("YANXI_AUTH_PASSWORD_HASH=" + password_hash(first))


if __name__ == "__main__":
    if len(sys.argv) != 1:
        sys.exit("请直接运行脚本并按提示输入；不接受命令行密码参数")
    try:
        main()
    except (ValueError, EOFError, KeyboardInterrupt) as exc:
        sys.exit(str(exc) or "已取消")
