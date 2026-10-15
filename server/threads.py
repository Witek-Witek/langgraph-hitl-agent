from fastapi import APIRouter, Depends, HTTPException, Request, Response

from auth import get_current_user
from server.schemas import ThreadCreate, ThreadOut, ThreadRename

router = APIRouter(prefix="/threads", tags=["threads"])
_NOT_FOUND = HTTPException(404, "Thread not found")


def _repo(request: Request):
    return request.app.state.threads


@router.post("", response_model=ThreadOut, status_code=201)
async def create_thread(request: Request, body: ThreadCreate,
                        user_id: str = Depends(get_current_user)):
    return await _repo(request).create(user_id, body.label)


@router.get("", response_model=list[ThreadOut])
async def list_threads(request: Request, user_id: str = Depends(get_current_user)):
    return await _repo(request).list(user_id)


@router.patch("/{thread_id}", response_model=ThreadOut)
async def rename_thread(request: Request, thread_id: str, body: ThreadRename,
                        user_id: str = Depends(get_current_user)):
    row = await _repo(request).rename(user_id, thread_id, body.label)
    if row is None:
        raise _NOT_FOUND
    return row


@router.delete("/{thread_id}", status_code=204)
async def delete_thread(request: Request, thread_id: str,
                        user_id: str = Depends(get_current_user)):
    if not await _repo(request).delete(user_id, thread_id):
        raise _NOT_FOUND
    return Response(status_code=204)