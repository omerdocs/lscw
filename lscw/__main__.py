"""Entry point for `python3 -m lscw`."""

import sys

if sys.version_info < (3, 9):
    print("❌ Python 3.9+ is required.")
    sys.exit(1)

from .cli import main

if __name__ == "__main__":
    main()
