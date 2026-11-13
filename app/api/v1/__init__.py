from fastapi import APIRouter

from app.api.v1.entities import router as entities_router

router = APIRouter(prefix="/v1")
router.include_router(entities_router)
