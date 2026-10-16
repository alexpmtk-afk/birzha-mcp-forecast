"""Refresh explicitly configured Google credentials; no secret discovery or logging."""
from __future__ import annotations
import json
from pathlib import Path
import threading
import time
import httpx
from birzha.application.canonical_market_mirror import MirrorConflict

TOKEN_URI = "https://oauth2.googleapis.com/token"
MIRROR_SCOPES = (
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.metadata.readonly",
)


def _service_account_grant(cfg: dict) -> dict:
    """Use Google's signer; fixed endpoint/scopes, no user delegation."""
    from google.auth import crypt, jwt
    email = cfg.get("client_email")
    if (cfg.get("token_uri") != TOKEN_URI
            or cfg.get("universe_domain", "googleapis.com") != "googleapis.com"
            or not isinstance(email, str)
            or not email.endswith(".iam.gserviceaccount.com")
            or cfg.get("subject")):
        raise ValueError("unsupported service account configuration")
    issued = int(time.time())
    assertion = jwt.encode(crypt.RSASigner.from_service_account_info(cfg), {
        "iss": email, "aud": TOKEN_URI, "iat": issued, "exp": issued + 3600,
        "scope": " ".join(MIRROR_SCOPES),
    })
    return {"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "assertion": assertion.decode("ascii")}


class GoogleRefreshToken:
    """Lazy local service account or OAuth refresh credentials."""
    def __init__(self, credential_path: str, *, transport=None):
        self.path=Path(credential_path)
        self.transport=transport
        self._lock=threading.Lock()
        self._token=""
        self._expires=0.0

    def __call__(self) -> str:
        with self._lock:
            if self._token and time.monotonic() < self._expires:
                return self._token
            try:
                cfg=json.loads(self.path.read_text(encoding="utf-8"))
                if cfg.get("type") == "service_account":
                    grant = _service_account_grant(cfg)
                else:
                    if cfg.get("type") not in (None, "authorized_user") or not all(isinstance(cfg.get(k),str) and cfg[k] for k in ("client_id","client_secret","refresh_token")):
                        raise ValueError("configuration incomplete")
                    grant = {"grant_type":"refresh_token", "client_id":cfg["client_id"], "client_secret":cfg["client_secret"], "refresh_token":cfg["refresh_token"]}
            except Exception:
                raise MirrorConflict("Google background authorisation is not configured") from None
            try:
                with httpx.Client(transport=self.transport,timeout=30,follow_redirects=False) as client:
                    response=client.post(TOKEN_URI,data=grant)
                if not response.is_success: raise ValueError("request refused")
                payload=response.json()
                token=payload["access_token"]; lifetime=int(payload["expires_in"])
                if not isinstance(token,str) or not token or payload.get("token_type", "").lower() != "bearer" or lifetime < 120:
                    raise ValueError("invalid token response")
            except Exception:
                raise MirrorConflict("Google background authorisation refresh refused") from None
            self._token=token; self._expires=time.monotonic()+min(lifetime-60,3540)
            return token