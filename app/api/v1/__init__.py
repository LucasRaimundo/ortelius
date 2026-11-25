from fastapi import APIRouter

from app.api.v1.entities import router as entities_router
from app.api.v1.media import router as media_router

router = APIRouter(prefix="/v1")
router.include_router(entities_router)
router.include_router(media_router)
