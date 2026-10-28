from redaction_mcp.core.services.text_matching import find_text_matches, replace_text_matches


def test_matching_normalizes_nfkc_whitespace_duplicates_and_longer_values() -> None:
    source = "Account Ｊａｎｅ\n\tDoe is active"
    matches = list(find_text_matches(source, ["Jane Doe", "Jane", "Jane Doe"]))

    assert [match.group() for match in matches] == ["Ｊａｎｅ\n\tDoe"]


def test_matching_uses_unicode_alphanumeric_boundaries() -> None:
    source = "élan lan"

    assert [match.group() for match in find_text_matches(source, ["lan"])] == ["lan"]
    assert [match.group() for match in find_text_matches(source, ["lan"], partial_match=True)] == ["lan", "lan"]


def test_longer_invalid_boundary_does_not_hide_valid_shorter_target() -> None:
    assert [match.group() for match in find_text_matches("John Doe", ["John D", "John"])] == ["John"]


def test_nfkc_matches_decomposed_hangul_and_compatibility_jamo() -> None:
    assert [match.group() for match in find_text_matches("가 ㄱㅏ", ["가"])] == ["가", "ㄱㅏ"]


def test_replacement_uses_original_offsets_after_normalization() -> None:
    output, count = replace_text_matches("A\u00a0 B", ["A B"], lambda match: f"<{match.group()}>")

    assert output == "<A\u00a0 B>"
    assert count == 1


def test_matching_composes_combining_unicode_sequences() -> None:
    source = "Cafe\u0301 owner"

    assert [match.group() for match in find_text_matches(source, ["Café"])] == ["Cafe\u0301"]
