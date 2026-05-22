#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Thin wrapper — 调用 python/player.py"""

import sys
from pathlib import Path

# 确保 python/ 目录在 import 路径中
sys.path.insert(0, str(Path(__file__).parent / 'python'))

from player import main

if __name__ == '__main__':
    main()
