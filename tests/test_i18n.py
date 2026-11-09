# Copyright (c) 2026 Taras Bulubas. All rights reserved.
# Use requires prior written permission; see LICENSE.

"""The UI translations in static/i18n: complete in every language, and nothing but English in the code."""

import json
import re
from pathlib import Path

import pytest

from energie import gemma

PKG = Path(gemma.__file__).parent
ROOT = PKG.parent.parent
I18N = PKG / "static" / "i18n"
INDEX = (PKG / "static" / "index.html").read_text(encoding="utf-8")
PLACEHOLDER = re.compile(r"\{(\w+)\}")
LANGS = sorted(p.stem for p in I18N.glob("*.json"))


def messages(lang: str) -> dict[str, str]:
    return json.loads((I18N / f"{lang}.json").read_text(encoding="utf-8"))


def test_the_three_languages_exist():
    assert LANGS == ["de", "en", "sk"]


def test_frontend_and_commentary_know_the_same_languages():
    locale = re.search(r"const LOCALE = \{(.*?)\}", INDEX).group(1)
    assert sorted(re.findall(r"(\w+):'", locale)) == LANGS
    assert sorted(gemma.LANGUAGES) == LANGS


@pytest.mark.parametrize("lang", ["de", "sk"])
def test_every_language_has_every_key_with_the_same_placeholders(lang):
    en, other = messages("en"), messages(lang)
    assert other.keys() == en.keys()
    for key, text in en.items():
        assert set(PLACEHOLDER.findall(other[key])) == set(PLACEHOLDER.findall(text)), key
        assert other[key].strip(), key


def test_every_key_in_index_html_is_translated():
    used = set(re.findall(r'data-i18n(?:-ph)?="(\w+)"', INDEX))
    used |= set(re.findall(r"\bt\('(\w+)'[,)]", INDEX))
    assert used, "no keys found"
    assert used - messages("en").keys() == set()


@pytest.mark.parametrize("source, prefix", [("app.py", "err_"), ("importer.py", "is_"), ("analysis.py", "in_")])
def test_every_code_from_the_backend_is_translated(source, prefix):
    codes = set(re.findall(r'\{"code": "(\w+)"', (PKG / source).read_text(encoding="utf-8")))
    assert codes, source
    assert {prefix + c for c in codes} - messages("en").keys() == set()


# letters of German and Slovak (not of English loanwords such as café)
FOREIGN = re.compile("[äöüßÄÖÜčďĺľňôŕšťýžČĎĹĽŇÔŔŠŤÝŽáíúÁÍÚ]")
CODE = [*PKG.glob("*.py"), PKG / "static" / "index.html", *(ROOT / "collector").glob("*.py"),
        *(ROOT / "scripts").glob("*.py")]


@pytest.mark.parametrize("path", CODE, ids=lambda p: p.name)
def test_code_is_english_only(path):
    lines = [f"{n}: {line.strip()}" for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
             if FOREIGN.search(line)]
    assert lines == []
