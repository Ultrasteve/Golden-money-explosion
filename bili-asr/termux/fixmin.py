"""Bump cmake_minimum_required(VERSION <3.10) in an unpacked source tree.

CMake 4.x (which is all Termux ships) refuses to configure projects declaring
compatibility below 3.5, and old sentencepiece sdists declare 2.8/3.1.
"""
import pathlib
import re
import sys

root = pathlib.Path(sys.argv[1])
pat = re.compile(r"(cmake_minimum_required\s*\(\s*VERSION\s+)([0-9]+(?:\.[0-9]+)*)", re.I)


def repl(m):
    parts = tuple(int(x) for x in m.group(2).split("."))
    if parts < (3, 10):
        return m.group(1) + "3.10"
    return m.group(0)


changed = []
for path in list(root.rglob("CMakeLists.txt")) + list(root.rglob("*.cmake")):
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        continue
    new = pat.sub(repl, text)
    if new != text:
        path.write_text(new, encoding="utf-8")
        changed.append(str(path))

print(f"patched {len(changed)} file(s)")
for c in changed[:25]:
    print("   ", c)
