"""地空协同林业监测决策系统启动入口。

用法：
    python run.py                # 默认 127.0.0.1:8765
    python run.py --port 9000
    python run.py --host 0.0.0.0 --port 8765
"""
from __future__ import annotations

import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "backend"))

from dikong_xietong.api.app import serve  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description="地空协同林业监测决策系统")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args()
    serve(port=args.port, host=args.host)


if __name__ == "__main__":
    main()
