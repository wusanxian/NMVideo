"""引擎列表 API"""
from fastapi import APIRouter

from ..engines import list_engines

router = APIRouter(prefix="/api/engines", tags=["engines"])


@router.get("")
def get_engines():
    return list_engines()
