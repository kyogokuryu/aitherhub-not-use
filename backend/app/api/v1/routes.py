from fastapi import APIRouter

from app.api.v1.endpoints.auth import router as auth_router
from app.api.v1.endpoints.video import router as video_router
from app.api.v1.endpoints.chat import router as chat_router
from app.api.v1.endpoints.feedback import router as feedback_router
from app.api.v1.endpoints.external_api import router as external_api_router
from app.api.v1.endpoints.lcj_linking import router as lcj_linking_router
from app.api.v1.endpoints.admin import router as admin_router
from app.api.v1.endpoints.live import router as live_router
from app.api.v1.endpoints.report import router as report_router

routers = APIRouter()
routers.include_router(auth_router, prefix="/auth", tags=["Auth"])
routers.include_router(video_router) 
routers.include_router(chat_router)
routers.include_router(feedback_router)
routers.include_router(external_api_router)
routers.include_router(lcj_linking_router)
routers.include_router(admin_router)
routers.include_router(live_router)
routers.include_router(report_router)
