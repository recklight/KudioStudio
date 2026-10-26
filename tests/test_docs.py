# -*- coding: utf-8 -*-
"""The user guide under docs/: every page in both languages, every page in
the navigation, every screenshot there for both, and the Help menu pointing
at the site mkdocs.yml builds. None of this needs mkdocs; CI's docs job builds
the site itself, strict, which also checks the links between pages."""
from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest
import yaml

from kudiostudio.web.studio import GUIDE_URL

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
ZH = ".zh-TW"

# the source package on PyPI carries the tests but not the guide
pytestmark = pytest.mark.skipif(not (ROOT / "mkdocs.yml").is_file(),
                                reason="the user guide is not in this checkout")


def _hooks():
    spec = importlib.util.spec_from_file_location("docs_hooks", ROOT / "tools/docs/hooks.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _config():
    return yaml.safe_load((ROOT / "mkdocs.yml").read_text(encoding="utf-8"))


def _nav(items):
    """(title, page) for every page in the navigation."""
    for item in items:
        for title, value in (item.items() if isinstance(item, dict) else [(None, item)]):
            if isinstance(value, list):
                yield from _nav(value)
            else:
                yield title, value


def _pages(chinese: bool):
    found = sorted(DOCS.rglob("*.md"))
    return [p for p in found if p.stem.endswith(ZH) == chinese]


# ------------------------------------------------------------------ the hook

def test_a_chinese_paragraph_is_joined_without_spaces():
    join = _hooks()._join_cjk_lines
    assert join("第一行\n第二行") == "第一行第二行"


def test_chinese_next_to_latin_keeps_its_space():
    join = _hooks()._join_cjk_lines
    assert join("在瀏覽器開啟\nPython 腳本") == "在瀏覽器開啟 Python 腳本"
    assert join("呼叫 `kudio.vad`\n找出語音") == "呼叫 `kudio.vad` 找出語音"


def test_lists_tables_headings_and_code_are_left_alone():
    join = _hooks()._join_cjk_lines
    for text in ("- 第一項\n- 第二項", "| 欄 |\n| 列 |", "## 標題\n內文",
                 "```\n第一行\n第二行\n```"):
        assert join(text) == text


# ----------------------------------------------------------------- the pages

def test_every_page_is_in_both_languages():
    english = {p.relative_to(DOCS).as_posix() for p in _pages(False)}
    chinese = {p.relative_to(DOCS).as_posix().replace(ZH, "") for p in _pages(True)}
    assert english == chinese


def test_every_page_is_in_the_navigation_and_every_entry_is_a_page():
    listed = [page for _, page in _nav(_config()["nav"])]
    assert len(listed) == len(set(listed))
    assert sorted(listed) == sorted(p.relative_to(DOCS).as_posix() for p in _pages(False))


def test_the_chinese_navigation_names_every_section_and_page():
    i18n = next(p["i18n"] for p in _config()["plugins"] if isinstance(p, dict) and "i18n" in p)
    chinese = next(lang for lang in i18n["languages"] if lang["locale"] == "zh-TW")
    titles = set()

    def collect(items):
        for item in items:
            for title, value in item.items():
                titles.add(title)
                if isinstance(value, list):
                    collect(value)

    collect(_config()["nav"])
    assert titles <= set(chinese["nav_translations"])


@pytest.mark.parametrize("page", _pages(False) + _pages(True), ids=lambda p: p.name)
def test_every_screenshot_is_there_in_the_pages_language(page):
    """A Chinese page asks for images/x.png and gets images/x.zh-TW.png; the
    build falls back to the English one without a word, which would put the
    English window in the Chinese guide."""
    text = page.read_text(encoding="utf-8")
    for target in re.findall(r"!\[[^\]]*\]\(([^)\s]+)\)", text):
        image = (page.parent / target).resolve()
        if page.stem.endswith(ZH) and image.parent.name == "images":
            image = image.with_name(image.stem + ZH + image.suffix)
        assert image.is_file(), f"{page.name}: {target}"


# ------------------------------------------------------------------ the site

def test_the_help_menu_opens_the_site_mkdocs_builds():
    assert GUIDE_URL == _config()["site_url"]
