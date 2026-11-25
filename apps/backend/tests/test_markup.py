import pytest

from gunther.citations import check_citations
from gunther.markup import parse_markup


@pytest.mark.parametrize("fence", ["````", "~~~~"])
def test_fences_keep_offsets_language_and_body(fence):
    nested = fence[:-1]
    source = f"Before\r\n{fence} python example\r\nx[7]\r\n{nested}\r\n{fence}\r\nAfter"
    markup = parse_markup(source)
    [block] = markup.blocks
    assert source[block.start:block.end] == source.removeprefix("Before\r\n").removesuffix("After")
    assert block.kind == "fence"
    assert block.language == "python"
    assert block.body == f"x[7]\n{nested}\n"


def test_list_fence_and_unclosed_fence():
    source = "- Before\n\n  ```js\n  f(x)\n  ```\n\n~~~plain\ntext `here`"
    markup = parse_markup(source)
    assert [block.body for block in markup.blocks] == ["f(x)\n", "text `here`"]
    assert markup.blocks[-1].end == len(source)
    assert not markup.inline


def test_indented_code_math_and_legacy_svg_blocks():
    source = (
        "Before.\n\n    x[7]\n\n$$\ny[3]\n$$\n\n"
        '<svg xmlns="http://www.w3.org/2000/svg"><text>[4]</text></svg>\n\nAfter.'
    )
    markup = parse_markup(source)
    assert [block.kind for block in markup.blocks] == ["code_block", "math_block", "svg"]
    assert source[markup.blocks[0].start:markup.blocks[0].end] == "    x[7]\n"
    assert markup.blocks[1].body.strip() == "y[3]"
    assert "".join(source[start:end] for start, end in markup.prose_ranges()).split() == [
        "Before.", "After."
    ]


def test_legacy_svg_requires_its_own_block_or_paragraph():
    table = "| Drawing | Evidence |\n| --- | --- |\n| <svg/> | From the source [1] |"
    text = f"{table}\n\nSee <svg/> with text [2].\n\n# <svg/>\n"
    assert not parse_markup(text).blocks
    assert check_citations(text, {1, 2}) == (text, [1, 2])
    [block] = parse_markup("<svg>\n<text>[3]</text>\n</svg>\n").blocks
    assert block.kind == "svg"


def test_inline_delimiters_stay_in_their_markdown_context():
    source = (
        "Use ``x ` y`` and $$z[3]$$. Price $5 and $10.\n\n"
        "`unclosed [1]\n\nplain [2] `\n\n"
        "| `open [3] | close` [4] |\n| --- | --- |\n| `code [5]` | text |"
    )
    markup = parse_markup(source)
    assert [source[start:end] for start, end in markup.inline] == [
        "``x ` y``", "$$z[3]$$", "`code [5]`"
    ]


def test_two_inline_formulas_leave_the_prose_between_them_visible():
    source = "$$x[7]$$ relates to [1] $$y[3]$$"
    markup = parse_markup(source)
    assert not markup.blocks
    [(start, end)] = markup.prose_ranges()
    assert source[start:end] == " relates to [1] "
