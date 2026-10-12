from gunther.qwen_stream import split_sentences


def word(text: str, punctuation: str, at: int) -> dict:
    return {"begin_time": at, "end_time": at + 100, "text": text, "punctuation": punctuation}


def test_no_space_is_kept_next_to_full_width_punctuation_but_latin_words_stay_apart() -> None:
    words = [word("一般来说", "， ", 0), word("Batch", "", 100), word(" Size", " ", 200)]
    words += [word("变大", "。", 300), word("Then", "", 400), word(" it", " grows.", 500)]
    sentences = split_sentences(words)
    assert [sentence["text"] for sentence in sentences] == [
        "一般来说，Batch Size 变大。",
        "Then it grows.",
    ]
