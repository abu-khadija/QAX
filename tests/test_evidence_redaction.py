"""S6: the saved replay evidence must never carry live secrets."""
from services.results import redact_secrets


def test_redacts_secret_keys_keeps_others():
    out = redact_secrets({"email": "a@b.com", "password": "Secret123!",
                          "body": {"access_token": "abc", "title": "t"}})
    assert out["email"] == "a@b.com" and out["body"]["title"] == "t"
    assert out["password"] == "«redacted»" and out["body"]["access_token"] == "«redacted»"


def test_masks_bearer_and_jwt_in_strings():
    s = "sent Authorization: Bearer eyJhbGciOi.eyJzdWIiOi.SIGNATURE to /x"
    out = redact_secrets(s)
    assert "SIGNATURE" not in out and "eyJhbGciOi.eyJzdWIiOi" not in out
    assert "«redacted" in out


def test_redacts_inside_lists_and_nested():
    out = redact_secrets([{"headers": {"Authorization": "Bearer t0ken"}}, {"ok": 1}])
    assert out[0]["headers"]["Authorization"] == "«redacted»" and out[1]["ok"] == 1
