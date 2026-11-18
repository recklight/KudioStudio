# -*- coding: utf-8 -*-
"""Build-time pieces of the user guide, wired in from mkdocs.yml.

The Chinese pages are wrapped at 80 columns like the English ones, so the
diffs stay readable. A browser renders each line break as a space, and next
to a Chinese character that space shows, so the lines of a Chinese paragraph
are joined before the page is rendered. The same hook runs on kudio's site.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List

#: A Chinese character or full-width punctuation mark.
_CJK = re.compile("[　-〿一-鿿＀-￯]")
#: A line that opens a block of its own, so nothing is joined onto it.
_OPENS = re.compile(r"[ \t]*(?:[-*+]\s|\d+\.\s|[|#>:<]|!!!|\?\?\?|===|```|~~~)")
#: A line that ends a block, so the next line is never joined onto it.
_CLOSED = re.compile(r"[ \t]*(?:[|#<]|!!!|\?\?\?|===|```|~~~)")
#: A Han character, which the pages set apart from Latin text by a space.
_HAN = re.compile("[一-鿿]")
#: Latin text: a letter, a digit, or the backtick that opens inline code.
_LATIN = re.compile("[A-Za-z0-9`]")


def _edge(line: str, end: bool) -> str:
    """The character a reader sees at the end (or start) of a line, past a
    link's target, its brackets and emphasis marks."""
    if end:
        line = re.sub(r"\]\([^)]*\)$|\]\[[^\]]*\]$", "", line.rstrip())
        return line.rstrip("*_]")[-1:]
    return line.lstrip().lstrip("[*_")[:1]


def _join_cjk_lines(markdown: str) -> str:
    """Join the lines of a Chinese paragraph. Where a break falls between a
    Chinese character and a Latin word, a number or inline code, the join
    keeps the space the pages put there. Fenced code is left alone."""
    lines = markdown.split("\n")
    out: List[str] = []
    fenced = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith(("```", "~~~")):
            fenced = not fenced
            out.append(line)
            continue
        prev = out[-1] if out else ""
        if (not fenced and stripped and prev.strip() and not _OPENS.match(line)
                and not _CLOSED.match(prev)
                and (_CJK.match(prev.rstrip()[-1]) or _CJK.match(stripped[0]))):
            a, b = _edge(prev, True), _edge(stripped, False)
            mixed = (_HAN.match(a) and _LATIN.match(b)) or (_LATIN.match(a) and _HAN.match(b))
            out[-1] = prev.rstrip() + (" " if mixed else "") + stripped
            continue
        out.append(line)
    return "\n".join(out)


def on_page_markdown(markdown: str, page, config, files, **kwargs) -> str:
    if page.file.src_uri.endswith(".zh-TW.md"):
        return _join_cjk_lines(markdown)
    return markdown


#: the English build's 404 page. Each language's build writes 404.html at
#: the root of the site, the Chinese one last, and GitHub Pages shows that one
#: file for every address it does not have, so a mistyped English address
#: answered in Chinese.
_NOT_FOUND: Dict[str, bytes] = {}


def on_post_build(config, **kwargs) -> None:
    page = Path(config["site_dir"]) / "404.html"
    if not page.is_file():
        return
    if config["theme"]["language"] == "en":
        _NOT_FOUND["en"] = page.read_bytes()
    elif "en" in _NOT_FOUND:
        page.write_bytes(_NOT_FOUND["en"])
