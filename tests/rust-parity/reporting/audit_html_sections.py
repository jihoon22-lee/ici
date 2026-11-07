"""Development-only audit: which frozen HTML tab facts are absent from the native page.

    python -I audit_html_sections.py FROZEN.html NATIVE.html NODE > audit.json

FROZEN.html is a frozen v0.13.0 `generate_html_report` page; NATIVE.html is `ici report` over
the same saved result. The native page is opened with `lazy-dom-check.cjs` so on-demand
sections count as visible. For every frozen tab the script lists the text tokens (words,
numbers, paths) that never occur in the native page, so a reviewer can decide whether each
is presentation chrome or a missing fact. It also lists every absolute URL in either page.
"""

import json
import re
import subprocess
import sys
from html.parser import HTMLParser
from pathlib import Path

TOKEN = re.compile(r"[\w./:@<>=+-]{3,}")
URL = re.compile(r"\b(?:https?|ftp)://[^\s\"'<>)]+")


class Tabs(HTMLParser):
    """Collect visible text per frozen `div.tab-content` (and the page header)."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.text = {"header": []}
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag in ("script", "style"):
            self.skip += 1
        tab = attributes.get("id", "") if tag == "div" else ""
        owner = tab if tab.startswith("tab-") else None
        self.stack.append((tag, owner))
        if owner:
            self.text.setdefault(owner, [])

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.skip -= 1
        while self.stack:
            popped, _ = self.stack.pop()
            if popped == tag:
                break

    def current(self):
        for _, owner in reversed(self.stack):
            if owner:
                return owner
        return "header"

    def handle_data(self, data):
        if not self.skip and data.strip():
            self.text[self.current()].append(data)


class Visible(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skip += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.skip -= 1

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def main():
    frozen_path, native_path, node = (Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3])
    frozen_html = frozen_path.read_text(encoding="utf-8")
    native_html = native_path.read_text(encoding="utf-8")
    tabs = Tabs()
    tabs.feed(frozen_html)
    visible = Visible()
    visible.feed(native_html)
    harness = Path(__file__).with_name("lazy-dom-check.cjs")
    hydrated = json.loads(
        subprocess.run(
            [node, str(harness), str(native_path)],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    )
    native_text = "\n".join(
        [
            *visible.parts,
            *(section["text"] for section in hydrated["sections"]),
            *(text for cards in hydrated["cards"] for text in cards["texts"]),
        ]
    )
    native_tokens = set(TOKEN.findall(native_text))
    report = {"tabs": {}, "urls": {}}
    for tab, parts in tabs.text.items():
        tokens = TOKEN.findall(" ".join(parts))
        missing = sorted({token for token in tokens if token not in native_tokens})
        report["tabs"][tab] = {"tokens": len(set(tokens)), "missing": missing}
    for name, text in (("frozen", frozen_html), ("native", native_html)):
        report["urls"][name] = sorted(set(URL.findall(text)))
    json.dump(report, sys.stdout, indent=1, ensure_ascii=False)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
