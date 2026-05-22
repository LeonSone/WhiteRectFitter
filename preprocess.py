#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Thin wrapper — 调用 python/preprocess.py"""

import sys
from pathlib import Path

# 确保 python/ 目录在 import 路径中 (仅源码运行时需要)
if not getattr(sys, 'frozen', False):
    sys.path.insert(0, str(Path(__file__).parent / 'python'))

from preprocess import main

if __name__ == '__main__':
    main()
