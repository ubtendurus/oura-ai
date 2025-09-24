from __future__ import annotations

from datetime import date
from functools import lru_cache
from typing import Optional
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, Form, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from app.clients.openai_client import OpenAIClient
from app.clients.oura import OuraClient
from app.config import get_settings
from app.oauth.service import OuraOAuthService
from app.oauth.token_store import TokenStore
from app.services.daily_message import DailyMessageService

SESSION_USER_KEY = "app_user"


@lru_cache()
def _get_settings():
    return get_settings()


settings = _get_settings()

app = FastAPI(title="Oura Daily Coach")
app.add_middleware(SessionMiddleware, secret_key=settings.app_secret_key, max_age=60 * 60 * 24 * 14)
app.mount("/static", StaticFiles(directory="app/web/static"), name="static")

templates = Jinja2Templates(directory="app/web/templates")


def _is_authenticated(request: Request) -> bool:
    return bool(request.session.get(SESSION_USER_KEY))


def _redirect_to_login(request: Request) -> Optional[RedirectResponse]:
    if _is_authenticated(request):
        return None
    next_url = str(request.url)
    parsed = urlparse(next_url)
    safe_path = parsed.path
    if parsed.query:
        safe_path = f"{safe_path}?{parsed.query}"
    return RedirectResponse(url=f"/login?next={safe_path}", status_code=status.HTTP_303_SEE_OTHER)


@lru_cache()
def _get_oauth_service() -> Optional[OuraOAuthService]:
    if not settings.use_oauth:
        return None
    store = TokenStore(settings.token_store_path)
    return OuraOAuthService(settings, store)


def get_optional_oauth_service() -> Optional[OuraOAuthService]:
    return _get_oauth_service()


def require_oauth_service() -> OuraOAuthService:
    service = _get_oauth_service()
    if not service:
        raise HTTPException(status_code=400, detail="Oura OAuth not configured.")
    return service


@lru_cache()
def get_daily_message_service() -> DailyMessageService:
    oauth_service = _get_oauth_service()

    if settings.use_oauth:
        assert oauth_service is not None

        async def token_supplier() -> str:
            return await oauth_service.get_access_token()
    else:
        personal_token = settings.oura_personal_access_token

        async def token_supplier() -> str:
            if not personal_token:
                raise RuntimeError("Missing Oura personal access token.")
            return personal_token

    return DailyMessageService(
        config=settings,
        oura_client=OuraClient(token_supplier),
        openai_client=OpenAIClient(settings.openai_api_key, settings.openai_model),
    )


@app.get("/login", response_class=HTMLResponse)
async def login_form(request: Request, next: Optional[str] = Query(None)) -> HTMLResponse:
    target = _sanitize_redirect_target(next)
    if _is_authenticated(request):
        return RedirectResponse(url=target, status_code=status.HTTP_303_SEE_OTHER)
    return templates.TemplateResponse(
        "auth/login.html",
        {"request": request, "next": target, "error": None},
    )


@app.post("/login", response_class=HTMLResponse)
async def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    next: Optional[str] = Form("/"),
) -> HTMLResponse:
    expected_user = settings.auth_username
    expected_pass = settings.auth_password
    redirect_target = _sanitize_redirect_target(next)
    if username == expected_user and password == expected_pass:
        request.session[SESSION_USER_KEY] = username
        return RedirectResponse(url=redirect_target, status_code=status.HTTP_303_SEE_OTHER)
    return templates.TemplateResponse(
        "auth/login.html",
        {
            "request": request,
            "next": redirect_target,
            "error": "Invalid credentials. Please try again.",
        },
        status_code=status.HTTP_400_BAD_REQUEST,
    )


@app.post("/logout", include_in_schema=False)
async def logout(request: Request) -> RedirectResponse:
    request.session.pop(SESSION_USER_KEY, None)
    request.session.clear()
    return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)


@app.get("/", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    for_date: Optional[str] = Query(None, description="ISO date (YYYY-MM-DD)"),
    tz: Optional[str] = Query(None, alias="tz"),
    service: DailyMessageService = Depends(get_daily_message_service),
    oauth_service: Optional[OuraOAuthService] = Depends(get_optional_oauth_service),
) -> HTMLResponse:
    redirect = _redirect_to_login(request)
    if redirect:
        return redirect
    target_date = _parse_date(for_date) if for_date else None
    payload = await _build_payload(request, service, target_date, oauth_service, tz)
    return templates.TemplateResponse("dashboard.html", {"request": request, **payload})


@app.post("/refresh", response_class=HTMLResponse)
async def refresh(
    request: Request,
    service: DailyMessageService = Depends(get_daily_message_service),
    oauth_service: Optional[OuraOAuthService] = Depends(get_optional_oauth_service),
) -> HTMLResponse:
    if not _is_authenticated(request):
        response = HTMLResponse(status_code=status.HTTP_401_UNAUTHORIZED)
        response.headers["HX-Redirect"] = "/login"
        return response
    payload = await _build_payload(request, service, None, oauth_service, None)
    return templates.TemplateResponse(
        "partials/message.html",
        {"request": request, **payload},
    )


@app.get("/auth/login", include_in_schema=False)
async def oauth_login(
    request: Request,
    oauth_service: OuraOAuthService = Depends(require_oauth_service),
) -> RedirectResponse:
    redirect = _redirect_to_login(request)
    if redirect:
        return redirect
    authorise_url, _ = oauth_service.build_authorisation_url()
    return RedirectResponse(url=authorise_url, status_code=status.HTTP_302_FOUND)


@app.get("/auth/callback", include_in_schema=False)
async def oauth_callback(
    request: Request,
    code: str,
    state: Optional[str] = None,
    oauth_service: OuraOAuthService = Depends(require_oauth_service),
) -> RedirectResponse:
    if not _is_authenticated(request):
        redirect = _redirect_to_login(request)
        assert redirect is not None
        return redirect
    if not oauth_service.is_state_valid(state):
        raise HTTPException(status_code=400, detail="Invalid OAuth state.")
    await oauth_service.exchange_code(code)
    return RedirectResponse(url="/?auth=connected", status_code=status.HTTP_302_FOUND)


@app.post("/auth/disconnect", include_in_schema=False)
async def oauth_disconnect(
    request: Request,
    oauth_service: OuraOAuthService = Depends(require_oauth_service),
) -> RedirectResponse:
    if not _is_authenticated(request):
        redirect = _redirect_to_login(request)
        assert redirect is not None
        return redirect
    oauth_service.disconnect()
    return RedirectResponse(url="/?auth=disconnected", status_code=status.HTTP_302_FOUND)


def _parse_date(date_value: str) -> date:
    try:
        return date.fromisoformat(date_value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD.") from exc


async def _build_payload(
    request: Request,
    service: DailyMessageService,
    target_date: Optional[date],
    oauth_service: Optional[OuraOAuthService],
    tz_alias: Optional[str],
) -> dict:
    oauth_meta = {
        "enabled": oauth_service is not None,
        "connected": oauth_service.has_tokens() if oauth_service else True,
        "login_url": "/auth/login",
        "disconnect_url": "/auth/disconnect",
    }
    timezone_hint = tz_alias or request.headers.get("X-Timezone")
    try:
        payload = await service.build_daily_message(target_date, timezone_hint)
        payload.setdefault("error", None)
        payload.setdefault("timezone", timezone_hint or service.config.app_timezone or "UTC")
        payload.setdefault("timezone_source", "client" if timezone_hint else "config")
    except Exception as exc:  # pragma: no cover - surfaces API issues to UI
        fallback_date = target_date.isoformat() if target_date else None
        payload = {
            "date_iso": fallback_date,
            "requested_date_iso": target_date.isoformat() if target_date else fallback_date,
            "message": None,
            "summary": {},
            "metrics": {},
            "error": str(exc),
            "timezone": timezone_hint or service.config.app_timezone or "UTC",
            "timezone_source": "client" if timezone_hint else "config",
        }
        if oauth_meta["enabled"] and not oauth_meta["connected"]:
            payload["oauth_prompt"] = True
        elif oauth_meta["enabled"] and "authorise" in str(exc).lower():
            payload["oauth_prompt"] = True
    else:
        payload["oauth_prompt"] = False
    payload.setdefault("oauth_prompt", False)
    payload["oauth"] = oauth_meta
    payload.setdefault("timezone", timezone_hint or service.config.app_timezone or "UTC")
    payload.setdefault("timezone_source", "client" if timezone_hint else "config")
    return payload


def _sanitize_redirect_target(value: Optional[str]) -> str:
    if not value:
        return "/"
    parsed = urlparse(value)
    if parsed.scheme or parsed.netloc:
        return "/"
    target = parsed.path or "/"
    if parsed.query:
        target = f"{target}?{parsed.query}"
    return target
