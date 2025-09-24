from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import date
from typing import Any, Awaitable, Callable, Dict, Optional

import httpx
from httpx import HTTPStatusError

logger = logging.getLogger(__name__)


TokenProvider = Callable[[], Awaitable[str]]


@dataclass
class OuraDailyMetrics:
    readiness: Optional[Dict[str, Any]]
    sleep: Optional[Dict[str, Any]]
    activity: Optional[Dict[str, Any]]


class OuraClient:
    base_url = "https://api.ouraring.com/v2"

    def __init__(self, token_provider: TokenProvider) -> None:
        self._token_provider = token_provider

    async def fetch_daily_metrics(self, target_date: date) -> OuraDailyMetrics:
        """Fetch readiness, sleep, and activity summaries for a given date."""
        logger.info("Requesting Oura daily metrics for %s", target_date.isoformat())
        params = {
            "start_date": target_date.isoformat(),
            "end_date": target_date.isoformat(),
        }

        try:
            readiness, sleep, activity = await asyncio.gather(
                self._get("/usercollection/daily_readiness", params),
                self._get("/usercollection/daily_sleep", params),
                self._get("/usercollection/daily_activity", params),
            )
        except HTTPStatusError as exc:
            logger.error(
                "Oura metrics request failed",
                extra={
                    "status": exc.response.status_code,
                    "path": str(exc.request.url),
                    "body": exc.response.text[:200],
                },
            )
            raise

        logger.debug(
            "Fetched Oura metrics payload lengths",
            extra={
                "readiness_items": len(readiness.get("data", [])),
                "sleep_items": len(sleep.get("data", [])),
                "activity_items": len(activity.get("data", [])),
            },
        )

        return OuraDailyMetrics(
            readiness=self._first_entry(readiness),
            sleep=self._first_entry(sleep),
            activity=self._first_entry(activity),
        )

    async def _get(self, path: str, params: Dict[str, Any]) -> Dict[str, Any]:
        token = await self._token_provider()
        masked = f"{token[:6]}..." if len(token) > 6 else "***"
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        }
        async with httpx.AsyncClient(base_url=self.base_url, timeout=15) as client:
            logger.debug("Using Oura access token prefix=%s", masked)
            logger.debug("GET %s with params=%s", path, params)
            response = await client.get(path, params=params, headers=headers)
            logger.debug("Oura API response status=%s for %s", response.status_code, path)
            try:
                response.raise_for_status()
            except HTTPStatusError as exc:
                logger.error(
                    "Oura API request failed",
                    extra={
                        "status": exc.response.status_code,
                        "path": str(exc.request.url),
                        "body": exc.response.text[:200],
                    },
                )
                raise
            return response.json()

    @staticmethod
    def _first_entry(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        items = payload.get("data", [])
        return items[0] if items else None