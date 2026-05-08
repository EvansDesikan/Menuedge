"""
fix_currency.py — MenuIQ Currency Normaliser
=============================================
Replaces all non-dollar currency symbols (£, €, ₹, ¥, etc.) with $ across
all HTML and Python files in this project. Run this whenever new content is
added that might contain non-$ currency symbols.

Usage:
    python fix_currency.py          # preview changes (dry run)
    python fix_currency.py --apply  # apply changes to files
"""

import os
import re
import sys
import glob

# Symbols to replace → "$"
CURRENCY_PATTERNS = [
    (r'£',  '$'),   # GBP
    (r'€',  '$'),   # EUR
    (r'₹',  '$'),   # INR
    (r'¥',  '$'),   # JPY/CNY
    (r'₩',  '$'),   # KRW
    (r'₪',  '$'),   # ILS
    (r'₦',  '$'),   # NGN
    (r'A\$', '$'),  # AUD
    (r'C\$', '$'),  # CAD
]

# File globs to scan (relative to this script's directory)
FILE_GLOBS = [
    "*.html",
    "*.py",
]

# Files to skip
SKIP_FILES = {
    "fix_currency.py",  # don't rewrite ourselves
}


def scan_file(path: str, apply: bool = False) -> int:
    """Scan (and optionally fix) a single file. Returns number of replacements."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            original = f.read()
    except (UnicodeDecodeError, PermissionError):
        return 0

    text = original
    for pattern, replacement in CURRENCY_PATTERNS:
        text = re.sub(pattern, replacement, text)

    changes = sum(1 for a, b in zip(original, text) if a != b)
    if original == text:
        return 0

    print(f"  {'FIXED' if apply else 'WOULD FIX'}: {os.path.basename(path)}")
    if apply:
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)

    return changes


def main():
    apply = "--apply" in sys.argv
    base  = os.path.dirname(os.path.abspath(__file__))

    files = []
    for pattern in FILE_GLOBS:
        files.extend(glob.glob(os.path.join(base, pattern)))

    total = 0
    for path in sorted(files):
        if os.path.basename(path) in SKIP_FILES:
            continue
        total += scan_file(path, apply=apply)

    if total == 0:
        print("  All currency symbols are already uniform ($). Nothing to fix.")
    elif not apply:
        print(f"\n  {total} character(s) would change. Run with --apply to fix.")
    else:
        print(f"\n  Done. {total} character(s) updated to $.")


if __name__ == "__main__":
    main()
