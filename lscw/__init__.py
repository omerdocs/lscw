"""LiteSpeed Cache Warmer | LSCW"""

import sys

__version__ = "1.2.0"

try:
    import requests  # noqa: F401
except ImportError:
    print("❌ 'requests' library not found. To install: pip install -r requirements.txt")
    sys.exit(1)
