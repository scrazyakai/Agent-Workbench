from fastapi import APIRouter

from app.api.routes import agents, health

router = APIRouter()
router.include_router(health.router)
router.include_router(agents.router)
