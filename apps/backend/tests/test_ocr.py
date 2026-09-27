from gunther.ocr import _parse_region_output, _parse_tesseract_tsv


def test_vision_output_parser_rejects_invalid_regions_and_normalizes_confidence() -> None:
    output = (
        b"1\t100000\t200000\t300000\t40000\t1.4\tGenome contains genes\n"
        b"1\t-1\t0\t1\t1\t0.5\toutside page\n"
        b"1\t900000\t0\t200000\t10\t0.5\toverflows page edge\n"
        b"1\t0\t0\t0\t10\t0.5\tempty region\n"
        b"malformed\n"
    )

    page = _parse_region_output(output, page_number=3, provider_name="test_vision")

    assert page.page_number == 3
    assert page.provider_name == "test_vision"
    assert len(page.regions) == 1
    assert page.regions[0].confidence == 1.0
    assert page.regions[0].text == "Genome contains genes"


def test_tesseract_tsv_groups_words_into_stable_normalized_line_regions() -> None:
    output = (
        b"level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\t"
        b"height\tconf\ttext\n"
        b"1\t1\t0\t0\t0\t0\t0\t0\t1000\t500\t-1\t\n"
        b"5\t1\t1\t1\t1\t1\t100\t50\t200\t25\t90\tGenome\n"
        b"5\t1\t1\t1\t1\t2\t320\t50\t180\t25\t80\tgenes\n"
    )

    page = _parse_tesseract_tsv(output, page_number=2)

    assert page.provider_name == "tesseract"
    assert len(page.regions) == 1
    region = page.regions[0]
    assert (region.left_ppm, region.top_ppm) == (100_000, 100_000)
    assert (region.width_ppm, region.height_ppm) == (400_000, 50_000)
    assert region.text == "Genome genes"
    assert region.confidence == 0.85
