#!/usr/bin/env python3
"""Compatibility launcher for local runs."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from zephyrsim.app import main

if __name__ == "__main__":
    main()
