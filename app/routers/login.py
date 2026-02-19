from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.models.product import DashboardUser
from app.services.dashboard_auth import hash_password, verify_password, require_login

router = APIRouter(tags=["login"])

templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse(request, "login.html", {"error": None})


@router.post("/login")
async def login_submit(request: Request, session: AsyncSession = Depends(get_session)):
    form = await request.form()
    username = form.get("username", "").strip()
    password = form.get("password", "")

    result = await session.execute(
        select(DashboardUser).where(DashboardUser.username == username)
    )
    user = result.scalar_one_or_none()

    if not user or not verify_password(password, user.password_hash, user.salt):
        return templates.TemplateResponse(
            request, "login.html", {"error": "Invalid username or password"}, status_code=401
        )

    request.session["user_id"] = user.id
    request.session["username"] = user.username

    if user.must_change_password:
        return RedirectResponse("/change-password", status_code=303)

    return RedirectResponse("/", status_code=303)


@router.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@router.get("/change-password", response_class=HTMLResponse)
async def change_password_page(request: Request, _=Depends(require_login)):
    return templates.TemplateResponse(request, "change_password.html", {"error": None})


@router.post("/change-password")
async def change_password_submit(
    request: Request,
    _=Depends(require_login),
    session: AsyncSession = Depends(get_session),
):
    form = await request.form()
    new_password = form.get("new_password", "")
    confirm_password = form.get("confirm_password", "")

    if len(new_password) < 4:
        return templates.TemplateResponse(
            request, "change_password.html",
            {"error": "Password must be at least 4 characters"},
            status_code=400,
        )

    if new_password != confirm_password:
        return templates.TemplateResponse(
            request, "change_password.html",
            {"error": "Passwords do not match"},
            status_code=400,
        )

    user_id = request.session["user_id"]
    result = await session.execute(
        select(DashboardUser).where(DashboardUser.id == user_id)
    )
    user = result.scalar_one()

    pw_hash, salt = hash_password(new_password)
    user.password_hash = pw_hash
    user.salt = salt
    user.must_change_password = False
    await session.commit()

    return RedirectResponse("/", status_code=303)
