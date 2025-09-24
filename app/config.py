from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Optional

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    oura_personal_access_token: Optional[str] = Field(None, validation_alias="OURA_PERSONAL_ACCESS_TOKEN")
    oura_client_id: Optional[str] = Field(None, validation_alias="OURA_CLIENT_ID")
    oura_client_secret: Optional[str] = Field(None, validation_alias="OURA_CLIENT_SECRET")
    openai_api_key: str = Field(..., validation_alias="OPENAI_API_KEY")
    openai_model: str = Field("gpt-5", validation_alias="OPENAI_MODEL")
    app_timezone: str = Field("UTC", validation_alias="APP_TIMEZONE")
    cache_ttl_minutes: int = Field(15, validation_alias="CACHE_TTL_MINUTES")
    data_fallback_days: int = Field(1, validation_alias="DATA_FALLBACK_DAYS")
    public_base_url: str = Field("http://localhost:8000", validation_alias="PUBLIC_BASE_URL")
    app_secret_key: str = Field(..., validation_alias="APP_SECRET_KEY")
    auth_username: str = Field(..., validation_alias="APP_USERNAME")
    auth_password: str = Field(..., validation_alias="APP_PASSWORD")
    token_store_path: Path = Field(Path("var/tokens.json"), validation_alias="TOKEN_STORE_PATH")
    oura_authorize_url: str = Field("https://cloud.ouraring.com/oauth/authorize", validation_alias="OURA_AUTHORIZE_URL")
    oura_token_url: str = Field("https://cloud.ouraring.com/oauth/token", validation_alias="OURA_TOKEN_URL")
    oura_scopes: Optional[str] = Field(None, validation_alias="OURA_SCOPES")

    @model_validator(mode='after')
    def _validate_credentials(cls, values: 'Settings') -> 'Settings':
        personal = values.oura_personal_access_token
        client_id = values.oura_client_id
        client_secret = values.oura_client_secret
        if personal or (client_id and client_secret):
            return values
        raise ValueError(
            'Provide OURA_PERSONAL_ACCESS_TOKEN or both OURA_CLIENT_ID and OURA_CLIENT_SECRET.'
        )

    @property
    def use_oauth(self) -> bool:
        return bool(self.oura_client_id and self.oura_client_secret and not self.oura_personal_access_token)

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


@lru_cache()
def get_settings() -> Settings:
    return Settings()
