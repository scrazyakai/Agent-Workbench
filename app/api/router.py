from fastapi import APIRouter

from app.api.routes import agents, health, model_connections

router = APIRouter()
router.include_router(health.router)
router.include_router(agents.router)
router.include_router(model_connections.router)
