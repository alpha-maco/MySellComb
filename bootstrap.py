from pathlib import Path
import sys


def ensure_vendor_on_path():
    vendor_dir = Path(__file__).resolve().parent / ".vendor"
    vendor_path = str(vendor_dir)
    if vendor_dir.exists() and vendor_path not in sys.path:
        sys.path.insert(0, vendor_path)


ensure_vendor_on_path()
