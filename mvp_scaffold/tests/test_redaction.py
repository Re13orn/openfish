from src.redaction import redact_object, redact_text


def test_redact_text_masks_token_like_values() -> None:
    text = "token=abc12345 secret=my-secret-value Bearer abc.def.ghi"
    redacted = redact_text(text)
    assert "abc12345" not in redacted
    assert "my-secret-value" not in redacted
    assert "Bearer abc.def.ghi" not in redacted
    assert "[REDACTED]" in redacted


def test_redact_object_recurses() -> None:
    payload = {
        "token": "token=abc",
        "nested": {"authorization": "Bearer xyz"},
        "items": ["password=hello", 123],
    }
    redacted = redact_object(payload)
    assert redacted["token"] != payload["token"]
    assert "xyz" not in redacted["nested"]["authorization"]
    assert "hello" not in redacted["items"][0]
