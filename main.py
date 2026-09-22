#!/usr/bin/env python3
# encoding: utf-8
"""桌面端入口。

    python main.py

依赖安装：
    python3 -m pip install -r desktop/requirements.txt
"""

import sys

try:
    from desktop.app import main
except ImportError as exc:  # PySide6 等未安装
    print(f'依赖未安装（{exc}）。请先执行：')
    print('    python3 -m pip install -r desktop/requirements.txt')
    sys.exit(1)

if __name__ == '__main__':
    sys.exit(main())
