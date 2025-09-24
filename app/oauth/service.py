from __future__ import annotations

import asyncio
import secrets
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional, Tuple

import httpx

from app.config import Settings
from app.oauth.token_store import TokenStore


logger = logging.getLogger(__name__)


class OuraOAuthService:
    """Handles OAuth token lifecycle for Oura access."""

    DEFAULT_SCOPES = ("email", "personal", "daily")

    def __init__(self, settings: Settings, store: TokenStore) -> None:
        self._settings = settings
        self._store = store
        self._lock = asyncio.Lock()
        self._pending_states: set[str] = set()
        self._authorize_url = settings.oura_authorize_url
        self._token_url = settings.oura_token_url
        raw_scopes = settings.oura_scopes
        if raw_scopes is None:
            self._scopes: Tuple[str, ...] = self.DEFAULT_SCOPES
        else:
            parts = [scope for scope in raw_scopes.split() if scope]
            self._scopes = tuple(parts)
        if not self._scopes:
            logger.warning("No Oura scopes configured; relying on application defaults")

    def build_authorisation_url(self) -> Tuple[str, str]:
        state = secrets.token_urlsafe(16)
        logger.info("Starting Oura OAuth flow")
        logger.debug("Requesting scopes %s with redirect %s", self._scopes, self._redirect_uri)
        logger.debug("Generated OAuth state %s", state)
        self._pending_states.add(state)
        params = {
            "response_type": "code",
            "client_id": self._settings.oura_client_id,
            "redirect_uri": self._redirect_uri,
            "state": state,
        }
        if self._scopes:
            params["scope"] = " ".join(self._scopes)
        query_string = httpx.QueryParams(params)
        return f"{self._authorize_url}?{query_string}", state

    def is_state_valid(self, state: Optional[str]) -> bool:
        if state and state in self._pending_states:
            self._pending_states.remove(state)
            logger.debug("OAuth state validated")
            return True
        logger.warning("OAuth state validation failed for %s", state)
        return False

    def has_tokens(self) -> bool:
        tokens = self._store.load()
        return bool(tokens)

    async def exchange_code(self, code: str) -> Dict[str, str]:
        payload = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self._redirect_uri,
            "client_id": self._settings.oura_client_id,
            "client_secret": self._settings.oura_client_secret,
        }
        logger.info("Exchanging OAuth code for tokens")
        token_data = await self._request_token(payload)
        self._store.save(token_data)
        logger.info("Stored new Oura tokens (expires_at=%s, scope=%s)", token_data.get("expires_at"), token_data.get("scope"))
        return token_data

    async def get_access_token(self) -> str:
        if not self._settings.use_oauth:
            raise RuntimeError("OAuth not configured; use personal access token instead.")
        async with self._lock:
            tokens = self._store.load()
            if not tokens:
                logger.error("No OAuth tokens stored when requesting access token")
                raise RuntimeError("Oura account not connected. Visit /auth/login to authorise access.")
            if self._is_expired(tokens):
                logger.info("Cached Oura access token expired; refreshing")
                tokens = await self._refresh(tokens)
                self._store.save(tokens)
            else:
                logger.debug("Using cached Oura access token")
            return tokens["access_token"]

    def disconnect(self) -> None:
        logger.info("Disconnecting Oura account and clearing tokens")
        self._store.clear()

    async def _refresh(self, tokens: Dict[str, str]) -> Dict[str, str]:
        refresh_token = tokens.get("refresh_token")
        if not refresh_token:
            raise RuntimeError("Stored Oura tokens missing refresh_token, please reconnect.")
        payload = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": self._settings.oura_client_id,
            "client_secret": self._settings.oura_client_secret,
        }
        logger.info("Refreshing Oura access token")
        tokens = await self._request_token(payload)
        logger.info("Received refreshed Oura tokens (expires_at=%s)", tokens.get("expires_at"))
        return tokens

    async def _request_token(self, payload: Dict[str, str]) -> Dict[str, str]:
        async with httpx.AsyncClient(timeout=15) as client:
            logger.debug("Requesting Oura token with grant_type=%s", payload.get("grant_type"))
            response = await client.post(self._token_url, data=payload)
            logger.debug("Oura token response status=%s", response.status_code)
            response.raise_for_status()
            data = response.json()
        expires_in = data.get("expires_in")
        expires_at = None
        if expires_in is not None:
            expires_at = datetime.now(timezone.utc) + timedelta(seconds=int(expires_in))
        token_payload = {
            "access_token": data["access_token"],
            "refresh_token": data.get("refresh_token"),
            "expires_at": expires_at.isoformat() if expires_at else None,
            "scope": data.get("scope"),
            "token_type": data.get("token_type"),
        }
        logger.debug("Prepared token payload (has_refresh=%s, expires_at=%s)", bool(token_payload.get("refresh_token")), token_payload.get("expires_at"))
        return token_payload

    def _is_expired(self, tokens: Dict[str, str]) -> bool:
        expires_at = tokens.get("expires_at")
        if not expires_at:
            return False
        try:
            expiry = datetime.fromisoformat(expires_at)
        except ValueError:
            logger.warning("Invalid expires_at value stored for Oura token: %s", expires_at)
            return True
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        expired = datetime.now(timezone.utc) >= (expiry - timedelta(seconds=30))
        if expired:
            logger.debug("Oura token considered expired (expires_at=%s)", expires_at)
        return expired

    @property
    def _redirect_uri(self) -> str:
        return f"{self._settings.public_base_url.rstrip('/')}/auth/callback"