"""Điểm vào cho PyInstaller (import tuyệt đối, tránh lỗi relative import)."""
import sys

from srtpx_cover.gui.app import main

if __name__ == "__main__":
    sys.exit(main())
