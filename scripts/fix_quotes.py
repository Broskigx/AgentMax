"""Fix typographic/curly quotes in Python source files."""

import sys

files = sys.argv[1:]
for path in files:
    with open(path, encoding="utf-8") as f:
        content = f.read()

    replacements = [
        ("'", "'"),  # LEFT SINGLE QUOTATION MARK
        ("'", "'"),  # RIGHT SINGLE QUOTATION MARK
        (
            """, '"'),   # LEFT DOUBLE QUOTATION MARK
        (""",
            '"',
        ),  # RIGHT DOUBLE QUOTATION MARK
        ("--", "--"),  # EM DASH
        ("-", "-"),  # EN DASH
    ]
    for bad, good in replacements:
        content = content.replace(bad, good)

    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"Fixed: {path}")
