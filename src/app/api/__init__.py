from fastapi import APIRouter
from .auth import router as auth_router
from .profile import router as profile_router
from .analysis import router as analysis_router
from .patients import router as patients_router
from .cases import router as cases_router
from .admin import router as admin_router
from .interop import router as interop_router

api_router = APIRouter()
api_router.include_router(auth_router, prefix="/auth", tags=["Authentifizierung"])
api_router.include_router(profile_router, prefix="/auth", tags=["Profil"])
api_router.include_router(analysis_router, prefix="/analysis", tags=["Analyse"])
api_router.include_router(patients_router, prefix="/patients", tags=["Patienten"])
api_router.include_router(cases_router, prefix="/cases", tags=["Fallakten"])
api_router.include_router(admin_router, prefix="/admin", tags=["Administration"])
api_router.include_router(interop_router, prefix="/interop", tags=["Interoperabilität"])
