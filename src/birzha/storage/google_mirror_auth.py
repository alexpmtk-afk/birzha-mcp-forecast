"""Refresh explicitly configured Google credentials; no secret discovery or logging."""
from __future__ import annotations
import json
from pathlib import Path
import threading
import time
import httpx
from birzha.application.canonical_market_mirror import MirrorConflict

class GoogleRefreshToken:
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
                if not all(isinstance(cfg.get(k),str) and cfg[k] for k in ("client_id","client_secret","refresh_token")):
                    raise ValueError("configuration incomplete")
            except Exception:
                raise MirrorConflict("Google background authorisation is not configured") from None
            try:
                with httpx.Client(transport=self.transport,timeout=30,follow_redirects=False) as client:
                    response=client.post("https://oauth2.googleapis.com/token",data={"grant_type":"refresh_token", "client_id":cfg["client_id"], "client_secret":cfg["client_secret"], "refresh_token":cfg["refresh_token"]})
                if not response.is_success: raise ValueError("request refused")
                payload=response.json()
                token=payload["access_token"]; lifetime=int(payload["expires_in"])
                if not isinstance(token,str) or not token or payload.get("token_type", "").lower() != "bearer" or lifetime < 120:
                    raise ValueError("invalid token response")
            except Exception:
                raise MirrorConflict("Google background authorisation refresh refused") from None
            self._token=token; self._expires=time.monotonic()+min(lifetime-60,3540)
            return token