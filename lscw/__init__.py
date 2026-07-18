"""LiteSpeed Cache Warmer | LSCW"""

import sys

__version__ = "1.0.0"

try:
    import requests  # noqa: F401
except ImportError:
    print("❌ 'requests' library not found. To install: pip install requests")
    sys.exit(1)
