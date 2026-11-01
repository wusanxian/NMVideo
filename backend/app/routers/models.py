"""模型库 API：看清单、看状态、手动下载。

下载是**用户显式动作**：这里不提供自动补全，「缺模型」只会被报成状态，
不会被悄悄拉起来。理由见 ``app/model_store.py`` 顶部。
"""
from fastapi import APIRouter, HTTPException

from .. import model_store

router = APIRouter(prefix="/api/models", tags=["models"])


@router.get("")
def list_models():
    return model_store.snapshot()


@router.post("/{model_id}/download")
def download(model_id: str):
    ok, message = model_store.start(model_id)
    if not ok:
        # 冲突（已有任务在跑）用 409，参数不对用 400
        code = 409 if ("下载" in message and "正在" in message) or "已有下载任务" in message else 400
        raise HTTPException(status_code=code, detail=message)
    return {"ok": True, "message": message}


@router.post("/{model_id}/cancel")
def cancel(model_id: str):
    ok, message = model_store.cancel(model_id)
    if not ok:
        raise HTTPException(status_code=409, detail=message)
    return {"ok": True, "message": message}
