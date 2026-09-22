from fastapi import APIRouter

from systutor.api.v1.core.branches import router as branches_router
from systutor.api.v1.core.documents import router as documents_router
from systutor.api.v1.core.legacy import router as legacy_router
from systutor.api.v1.core.permissions import router as permissions_router
from systutor.api.v1.core.plugins import router as plugins_router
from systutor.api.v1.core.roles import router as roles_router
from systutor.api.v1.core.signatures import router as signatures_router
from systutor.api.v1.core.users import router as users_router

router = APIRouter(tags=["core"])
router.include_router(legacy_router)
router.include_router(users_router)
router.include_router(roles_router)
router.include_router(branches_router)
router.include_router(permissions_router)
router.include_router(plugins_router)
router.include_router(documents_router)
router.include_router(signatures_router)

__all__ = ["router"]
