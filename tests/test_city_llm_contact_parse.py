from fb_pipeline.contracts.l1_city_llm import _normalize_full_name, _normalize_vietnamese_phone


def test_normalizes_obvious_o_typo_in_vietnamese_phone():
    assert _normalize_vietnamese_phone("o904 069 868") == "0904069868"


def test_normalizes_country_prefix_and_rejects_partial_number():
    assert _normalize_vietnamese_phone("+84 904-069-868") == "0904069868"
    assert _normalize_vietnamese_phone("0904 069") is None


def test_accepts_only_human_looking_contact_name():
    assert _normalize_full_name("  Bùi   Thị  Thúy ") == "Bùi Thị Thúy"
    assert _normalize_full_name("Bùi thị Thúy") == "Bùi Thị Thúy"
    assert _normalize_full_name("SĐT 0904069868") is None
