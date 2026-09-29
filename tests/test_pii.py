from app.pii import hash_user_id, scrub_text


def test_scrub_email() -> None:
    out = scrub_text("Email me at student@vinuni.edu.vn")
    assert "student@" not in out
    assert "REDACTED_EMAIL" in out


def test_scrub_common_vietnamese_phone_formats() -> None:
    phone_numbers = (
        "0901234567",
        "090 123 4567",
        "090.123.4567",
        "090-123-4567",
        "+84 90 123 4567",
    )

    for phone_number in phone_numbers:
        out = scrub_text(f"Contact: {phone_number}")
        assert phone_number not in out
        assert "REDACTED_PHONE_VN" in out


def test_scrub_cccd() -> None:
    out = scrub_text("CCCD của tôi là 001099012345, cần cập nhật")
    assert "001099012345" not in out
    assert "REDACTED_CCCD" in out


def test_scrub_credit_card_formats() -> None:
    card_numbers = (
        "4111 1111 1111 1111",
        "4111-1111-1111-1111",
        "4111111111111111",
        "3782 822463 10005",  # Amex 15 số
    )

    for card_number in card_numbers:
        out = scrub_text(f"Card: {card_number}")
        assert card_number not in out
        assert "REDACTED_CREDIT_CARD" in out
        # Thẻ phải được che trọn, không bị tách thành phone/CCCD còn sót số.
        assert "REDACTED_PHONE_VN" not in out
        assert "REDACTED_CCCD" not in out


def test_scrub_phone_with_84_prefix_without_plus() -> None:
    out = scrub_text("Gọi 84987654321 nhé")
    assert "84987654321" not in out
    assert "REDACTED_PHONE_VN" in out


def test_scrub_passport() -> None:
    out = scrub_text("Passport C1234567 hết hạn")
    assert "C1234567" not in out
    assert "REDACTED_PASSPORT_VN" in out


def test_scrub_multiple_pii_in_one_message() -> None:
    message = (
        "Email a.b+test@example.com, phone 0987654321, "
        "CCCD 079203001234, card 5500 0000 0000 0004"
    )
    out = scrub_text(message)
    for raw in ("a.b+test@example.com", "0987654321", "079203001234", "5500 0000 0000 0004"):
        assert raw not in out
    for token in ("EMAIL", "PHONE_VN", "CCCD", "CREDIT_CARD"):
        assert f"[REDACTED_{token}]" in out


def test_scrub_keeps_non_pii_text() -> None:
    text = "req-1a2b3c4d latency 2500ms P95 over 60 minutes, 7 days refund"
    assert scrub_text(text) == text


def test_hash_user_id_is_stable_and_not_raw() -> None:
    hashed = hash_user_id("u01")
    assert hashed == hash_user_id("u01")
    assert hashed != hash_user_id("u02")
    assert "u01" not in hashed
    assert len(hashed) == 12
