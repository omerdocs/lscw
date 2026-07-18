#!/usr/bin/env python3
"""
LiteSpeed Cache Warmer | LSCW
==================================================
Usage:
    python3 lscw.py --site https://yoursite.com
    python3 lscw.py --site https://yoursite.com --resume
    python3 lscw.py --site https://yoursite.com --delay X --workers X
    python3 lscw.py --site https://yoursite.com --sitemap https://yoursite.com/sitemap.xml
    python3 lscw.py --site https://yoursite.com --sitemap-ua browser
    python3 lscw.py --site https://yoursite.com --urls-file urls.txt --dry-run

Requirements:
-- pip install requests rich
"""

import sys

if sys.version_info < (3, 9):
    print("❌ Python 3.9+ is required.")
    sys.exit(1)

from lscw.cli import main

if __name__ == "__main__":
    main()
