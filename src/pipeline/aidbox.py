"""Minimal Aidbox FHIR client over the standard library (no extra deps).

The endpoint and credentials are passed in, never baked in: the UI sends the
endpoint it has in its field, and the CLI falls back to config/.env. Auth is
chosen in this order: explicit bearer token > client_id/secret (Basic) > raw
"user:pass" Basic. Aidbox advertises Basic, OAuth2 and SMART in its
CapabilityStatement; Basic with a Client resource is the simplest for a backend
load and is what this client uses by default.
"""

from __future__ import annotations

import base64
import json
import os
import ssl
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any


class AidboxError(RuntimeError):
    """A non-2xx response or a transport failure talking to Aidbox."""

    def __init__(self, message: str, *, status: int | None = None, body: Any = None):
        super().__init__(message)
        self.status = status
        self.body = body


@dataclass
class AidboxClient:
    base_url: str = "https://dakwjlidfo.aidbox.app"
    client_id: str = ""
    client_secret: str = ""
    token: str = ""          # pre-issued bearer token (optional)
    basic: str = ""          # raw "user:pass" for Basic (optional)
    timeout: float = 60.0
    _auth_header: str = field(default="", init=False)

    def __post_init__(self) -> None:
        self.base_url = (self.base_url or "").rstrip("/")
        self._auth_header = self._build_auth()
        # Transport hardening for flaky TLS (self-signed cert in chain, corporate
        # proxy, transient resets). AIDBOX_INSECURE=1 skips cert verification.
        insecure = os.getenv("AIDBOX_INSECURE", "").lower() in ("1", "true", "yes")
        self._ssl_ctx = ssl._create_unverified_context() if insecure else None
        self._retries = max(1, int(os.getenv("AIDBOX_RETRIES", "3")))

    # -- base URLs -----------------------------------------------------------
    @property
    def fhir_base(self) -> str:
        """The FHIR R4 base. Aidbox serves it under /fhir."""
        return self.base_url + "/fhir"

    # -- auth ----------------------------------------------------------------
    def _build_auth(self) -> str:
        if self.token:
            return "Bearer " + self.token
        creds = ""
        if self.client_id:
            creds = f"{self.client_id}:{self.client_secret}"
        elif self.basic:
            creds = self.basic
        if creds:
            return "Basic " + base64.b64encode(creds.encode("utf-8")).decode("ascii")
        return ""

    @property
    def has_auth(self) -> bool:
        return bool(self._auth_header)

    # -- transport -----------------------------------------------------------
    def _request(self, method: str, url: str, body: dict | None = None) -> Any:
        data = None
        headers = {"Accept": "application/fhir+json"}
        if self._auth_header:
            headers["Authorization"] = self._auth_header
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/fhir+json"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        last_exc = None
        for attempt in range(self._retries):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout, context=self._ssl_ctx) as resp:
                    raw = resp.read().decode("utf-8")
                    return json.loads(raw) if raw else {}
            except urllib.error.HTTPError as exc:
                # HTTP status errors are not transient — do not retry.
                raw = exc.read().decode("utf-8", "replace")
                try:
                    parsed = json.loads(raw)
                except ValueError:
                    parsed = raw
                raise AidboxError(
                    f"Aidbox {method} {url} -> HTTP {exc.code}", status=exc.code, body=parsed
                ) from None
            except (urllib.error.URLError, ssl.SSLError, ConnectionError, TimeoutError) as exc:
                # Transient transport failure (TLS blip, reset, timeout): retry.
                last_exc = exc
                if attempt < self._retries - 1:
                    time.sleep(0.6 * (attempt + 1))
                    continue
        reason = getattr(last_exc, "reason", last_exc)
        raise AidboxError(f"cannot reach Aidbox at {url}: {reason}") from None

    # -- operations ----------------------------------------------------------
    def capability(self) -> dict:
        """GET /fhir/metadata — also a cheap reachability probe (no auth needed)."""
        return self._request("GET", self.fhir_base + "/metadata")

    def ping(self) -> dict:
        """Confirm the endpoint is reachable AND the credentials are accepted.

        Returns {ok, reachable, authenticated, fhirVersion, software, detail}.
        """
        out = {"ok": False, "reachable": False, "authenticated": False,
               "fhirVersion": None, "software": None, "detail": ""}
        try:
            cap = self.capability()
            out["reachable"] = True
            out["fhirVersion"] = cap.get("fhirVersion")
            sw = cap.get("software") or {}
            out["software"] = sw.get("name") if isinstance(sw, dict) else None
        except AidboxError as exc:
            out["detail"] = str(exc)
            return out
        # A metadata read is public; hit a protected search to check auth.
        try:
            self._request("GET", self.fhir_base + "/Patient?_count=0")
            out["authenticated"] = True
            out["ok"] = True
            out["detail"] = "endpoint reachable, credentials accepted"
        except AidboxError as exc:
            out["detail"] = (
                "endpoint reachable but credentials rejected "
                f"(HTTP {exc.status}); create a Client + AccessPolicy in Aidbox"
                if exc.status in (401, 403)
                else f"auth check failed: {exc}"
            )
        return out

    def transaction(self, bundle: dict) -> dict:
        """POST a FHIR transaction Bundle to the FHIR base."""
        return self._request("POST", self.fhir_base, bundle)
