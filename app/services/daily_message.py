from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone, tzinfo
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.clients.openai_client import OpenAIClient
from app.clients.oura import OuraClient, OuraDailyMetrics
from app.config import Settings


logger = logging.getLogger(__name__)


@dataclass
class CachedMessage:
    payload: Dict[str, Any]
    created_at: datetime


@dataclass
class DailyMessageService:
    config: Settings
    oura_client: OuraClient
    openai_client: OpenAIClient
    _cache: Dict[str, CachedMessage] = field(default_factory=dict, init=False)

    async def build_daily_message(self, target_date: Optional[date] = None, tz_alias: Optional[str] = None) -> Dict[str, Any]:
        tz, tz_key = self._get_timezone(tz_alias)
        today = datetime.now(tz).date()
        target = target_date or today
        cache_key = f"{tz_key}|{target.isoformat()}"

        cached = self._cache.get(cache_key)
        if cached and not self._is_expired(cached.created_at, tz):
            return cached.payload

        metrics, resolved_date = await self._fetch_metrics_with_fallback(target)

        summary = self._summarise_metrics(metrics)
        messages = self._build_prompt(metrics, summary, resolved_date)
        message_text = await self.openai_client.generate_daily_message(messages)
        if not message_text:
            logger.warning("OpenAI returned empty message; using fallback copy")
            message_text = self._build_fallback_message(summary)

        payload = {
            "requested_date_iso": target.isoformat(),
            "date_iso": resolved_date.isoformat(),
            "message": message_text,
            "summary": summary,
            "metrics": {
                "readiness": metrics.readiness,
                "sleep": metrics.sleep,
                "activity": metrics.activity,
            },
        }
        payload["timezone"] = tz_key
        payload["timezone_source"] = "client" if tz_alias else "config"
        resolved_cache_key = f"{tz_key}|{resolved_date.isoformat()}"
        self._cache[resolved_cache_key] = CachedMessage(payload=payload, created_at=datetime.now(tz))
        request_cache_key = f"{tz_key}|{target.isoformat()}"
        if request_cache_key != resolved_cache_key:
            self._cache[request_cache_key] = CachedMessage(payload=payload, created_at=datetime.now(tz))
        return payload


    async def _fetch_metrics_with_fallback(self, target: date) -> tuple[OuraDailyMetrics, date]:
        fallback_window = max(0, int(self.config.data_fallback_days))
        last_error: Optional[Exception] = None
        for offset in range(0, fallback_window + 1):
            candidate = target - timedelta(days=offset)
            try:
                metrics = await self.oura_client.fetch_daily_metrics(candidate)
            except Exception as exc:  # pragma: no cover - API errors surface to caller
                logger.error("Failed to fetch Oura metrics", exc_info=exc)
                last_error = exc
                continue
            if any((metrics.readiness, metrics.sleep, metrics.activity)):
                if offset > 0:
                    logger.info(
                        "Using fallback date %s due to missing data on %s",
                        candidate,
                        target,
                    )
                return metrics, candidate
        if last_error:
            raise last_error
        raise ValueError(
            "No Oura data available for the requested date or fallback window."
        )

    def _is_expired(self, created_at: datetime, tz: tzinfo) -> bool:
        ttl_minutes = int(self.config.cache_ttl_minutes)
        return datetime.now(tz) >= created_at + timedelta(minutes=ttl_minutes)

    def _build_prompt(
        self,
        metrics: OuraDailyMetrics,
        summary: Dict[str, Any],
        target_date: date,
    ) -> list[Dict[str, Any]]:
        readiness_score = summary.get("readiness_score")
        sleep_score = summary.get("sleep_score")
        context_blob = (
            "You are a daily motivator coach. Use the Oura readiness and sleep scores to craft a helpful, kind plan for the day. Keep a warm tone, avoid medical claims or PII, and focus on actionable guidance. Respond with HTML markup: provide two or three <p> paragraphs followed by a <ul> containing one or two specific focus items."
            f" Readiness score: {readiness_score} out of 100. Sleep score: {sleep_score} out of 100."
        )
        structured_payload = {
            "date": target_date.isoformat(),
            "summary": summary,
            "raw_metrics": {
                "readiness": metrics.readiness,
                "sleep": metrics.sleep,
                "activity": metrics.activity,
            },
        }
        return [
            {
                "role": "system",
                "content": context_blob,
            },
            {
                "role": "user",
                "content": json.dumps(structured_payload, indent=2, sort_keys=True),
            },
        ]

    @staticmethod
    def _summarise_metrics(metrics: OuraDailyMetrics) -> Dict[str, Any]:
        summary: Dict[str, Any] = {}
        if metrics.readiness:
            summary["readiness_score"] = metrics.readiness.get("score")
        if metrics.sleep:
            summary["sleep_score"] = metrics.sleep.get("score")
            duration = metrics.sleep.get("total_sleep_duration")
            if duration is not None:
                summary["sleep_duration_hours"] = round(duration / 3600, 2)
        if metrics.activity:
            summary["activity_score"] = metrics.activity.get("score")
            summary["steps"] = metrics.activity.get("steps")
        return summary


    @staticmethod
    def _build_fallback_message(summary: Dict[str, Any]) -> str:
        readiness = summary.get("readiness_score")
        sleep = summary.get("sleep_score")
        paragraphs = ["Here's a gentle check-in based on your latest Oura data."]
        if readiness is not None:
            paragraphs.append(f"Readiness score: {readiness}.")
        if sleep is not None:
            paragraphs.append(f"Sleep score: {sleep}.")
        if len(paragraphs) == 1:
            paragraphs.append("Keep looking after yourself today.")
        else:
            paragraphs.append("Keep listening to your body and make thoughtful adjustments as needed.")
        body = ''.join(f"<p>{p}</p>" for p in paragraphs)
        body += "<ul><li>Take one simple action that respects how you feel right now.</li></ul>"
        return body


    def _get_timezone(self, tz_alias: Optional[str]) -> tuple[tzinfo, str]:
        tz_key = (tz_alias or self.config.app_timezone or "UTC").strip() or "UTC"
        try:
            tz = ZoneInfo(tz_key)
            return tz, tz_key
        except ZoneInfoNotFoundError:
            logger.warning("No time zone found with key %s; falling back to UTC", tz_key)
            return timezone.utc, "UTC"

