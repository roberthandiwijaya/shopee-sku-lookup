"""Public read-only preflight and authenticated private collection management."""
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.models.product import DashboardUser
from app.schemas.catalog import CatalogSelection, CollectionInput
from app.services import catalog_collection_service as collections
from app.services.catalog_preflight_service import preflight

router = APIRouter(prefix="/linked-products", tags=["catalog tools"])


def collection_csrf(request):
    if "collection_csrf" not in request.session:
        request.session["collection_csrf"] = secrets.token_urlsafe(32)
    return request.session["collection_csrf"]


async def collection_user(request: Request, session: AsyncSession = Depends(get_session)):
    user_id = request.session.get("user_id")
    user = await session.get(DashboardUser, user_id) if user_id else None
    if user is None:
        raise HTTPException(401, "Sign in to save catalog collections.")
    if user.must_change_password:
        raise HTTPException(403, "Change your password before managing collections.")
    return user.id


def verify_collection_csrf(request: Request):
    expected = request.session.get("collection_csrf")
    supplied = request.headers.get("X-CSRF-Token", "")
    if not expected or not secrets.compare_digest(expected.encode(), supplied.encode()):
        raise HTTPException(403, "Reload the catalog page and try again.")


@router.post("/exports/preflight")
async def export_preflight(payload: CatalogSelection, response: Response,
                           session: AsyncSession = Depends(get_session)):
    response.headers["Cache-Control"] = "no-store"
    return await preflight(session, payload)


@router.get("/collections")
async def list_saved(request: Request, response: Response, user_id: int = Depends(collection_user),
                     session: AsyncSession = Depends(get_session)):
    response.headers["Cache-Control"] = "no-store"
    return {"collections": await collections.list_collections(session, user_id), "csrf_token": collection_csrf(request)}


@router.post("/collections", status_code=201, dependencies=[Depends(verify_collection_csrf)])
async def create_saved(payload: CollectionInput, response: Response, user_id: int = Depends(collection_user),
                       session: AsyncSession = Depends(get_session)):
    response.headers["Cache-Control"] = "no-store"
    try:
        return await collections.save_collection(session, user_id, payload)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.put("/collections/{collection_id}", dependencies=[Depends(verify_collection_csrf)])
async def update_saved(collection_id: str, payload: CollectionInput, response: Response,
                       user_id: int = Depends(collection_user), session: AsyncSession = Depends(get_session)):
    response.headers["Cache-Control"] = "no-store"
    collection = await collections.owned_collection(session, user_id, collection_id)
    if collection is None:
        raise HTTPException(404, "Collection not found.")
    try:
        return await collections.save_collection(session, user_id, payload, collection)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.delete("/collections/{collection_id}", dependencies=[Depends(verify_collection_csrf)])
async def delete_saved(collection_id: str, response: Response, user_id: int = Depends(collection_user),
                       session: AsyncSession = Depends(get_session)):
    response.headers["Cache-Control"] = "no-store"
    collection = await collections.owned_collection(session, user_id, collection_id)
    if collection is None:
        raise HTTPException(404, "Collection not found.")
    await session.delete(collection)
    await session.commit()
    return {"deleted": True}
