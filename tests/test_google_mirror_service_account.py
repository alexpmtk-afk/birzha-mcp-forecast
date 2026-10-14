import json
from urllib.parse import parse_qs
import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from google.auth import jwt
from birzha.application.canonical_market_mirror import MirrorConflict
from birzha.storage.google_mirror_auth import GoogleRefreshToken, MIRROR_SCOPES, TOKEN_URI

@pytest.fixture
def config():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return {
        "type": "service_account", "client_email": "test@isolated.iam.gserviceaccount.com",
        "token_uri": TOKEN_URI, "private_key_id": "synthetic-only",
        "private_key": key.private_bytes(serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode(),
        "public": key.public_key().public_bytes(serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo).decode(),
    }

def test_signed_grant_scopes_cache_and_signature(tmp_path, config):
    path = tmp_path / "synthetic.json"
    path.write_text(json.dumps(config))
    calls = []
    def handler(req):
        calls.append(req)
        assert str(req.url) == TOKEN_URI
        form = parse_qs(req.content.decode())
        assert form["grant_type"] == ["urn:ietf:params:oauth:grant-type:jwt-bearer"]
        claims = jwt.decode(form["assertion"][0], certs=config["public"], audience=TOKEN_URI)
        assert claims["iss"] == config["client_email"]
        assert set(claims["scope"].split()) == set(MIRROR_SCOPES)
        assert claims["exp"] - claims["iat"] == 3600
        assert "sub" not in claims
        return httpx.Response(200, json={"access_token":"synthetic", "expires_in":3600, "token_type":"Bearer"})
    auth = GoogleRefreshToken(str(path), transport=httpx.MockTransport(handler))
    assert auth() == auth() == "synthetic"
    assert len(calls) == 1

@pytest.mark.parametrize("field,value", [
    ("token_uri", "https://example.com/stolen"),
    ("universe_domain", "example.com"), ("subject", "person@example.com"),
    ("client_email", "wrong@example.com"), ("private_key", "invalid secret"),
    ("type", "external_account"),
])
def test_invalid_or_delegated_credentials_fail_before_network(tmp_path, config, field, value):
    config[field] = value
    path = tmp_path / "synthetic.json"; path.write_text(json.dumps(config))
    def handler(req):
        pytest.fail("No network allowed for invalid credentials")
    with pytest.raises(MirrorConflict) as exc:
        GoogleRefreshToken(str(path), transport=httpx.MockTransport(handler))()
    assert "secret" not in str(exc.value) and "stolen" not in str(exc.value)
