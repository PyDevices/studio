"""Fail if anything in the app could navigate away from it.

    .venv/bin/python tools/check_no_links.py

The installed app has no way out: no anchors, no window.open, no assignments
to location, and no URL shown to the user in the UI. Fetches (fonts from
lvgl-bindings) aren't links and are allowed where listed in ALLOWED.
"""

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
FILES = [ROOT / "index.html", *sorted((ROOT / "workers").glob("*.js")), *sorted((ROOT / "app").rglob("*.py"))]
BAD = [
    (re.compile(r"<a[\s>]", re.I), "an anchor"),
    (re.compile(r"\bhref\s*=", re.I), "an href"),
    (re.compile(r"window\.open\s*\("), "window.open"),
    (re.compile(r"location\.(assign|replace)\s*\(|location\.href\s*=|location\s*=\s*['\"]"), "navigation"),
    (re.compile(r"target\s*=\s*['\"]_blank"), "a new-window target"),
    (re.compile(r"https?://"), "a URL"),
]
ALLOWED = {
    # fetched, never shown or followed
    ("app/ui/fonts.py", "https://raw.githubusercontent.com/PyDevices/lvgl-bindings/"),
}


def main():
    problems = []
    for f in FILES:
        rel = f.relative_to(ROOT).as_posix()
        for n, line in enumerate(f.read_text().split("\n"), 1):
            if "<link " in line and "href" in line:
                continue   # the manifest and icon in <head> aren't links a user can follow
            for pat, what in BAD:
                if pat.search(line) and not any(rel == a and s in line for a, s in ALLOWED):
                    problems.append("%s:%d: %s: %s" % (rel, n, what, line.strip()[:100]))
    for p in problems:
        print(p)
    print("%d files checked, %d problems" % (len(FILES), len(problems)))
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
