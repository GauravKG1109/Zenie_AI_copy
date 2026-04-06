import json

from fastapi import APIRouter, WebSocket
from fastapi.responses import StreamingResponse

from app.schemas.chat_schema import ChatRequest, ChatResponse
from app.controllers.chat_controller import handle_chat

router = APIRouter()


@router.post("/", response_model=ChatResponse)
async def chat_endpoint(chat_request: ChatRequest):
    """Standard POST — returns full result after pipeline completes."""
    return await handle_chat(chat_request)


@router.post("/stream")
async def stream_chat_endpoint(chat_request: ChatRequest):
    """
    SSE endpoint — streams one JSON event per pipeline node as it completes.

    Event shape: { "node": str, "logs"?: [str], "intent"?: dict,
                   "date_range"?: dict, "sql_query"?: str }
    Final event: { "node": "__done__" }
    """
    from services.message_service import stream_message

    metadata = {
        **(chat_request.metadata or {}),
        "company_id": chat_request.company_id or "1",
        "session_id": chat_request.session_id or "",
    }

    async def generate():
        async for event in stream_message(
            message=chat_request.message,
            history=chat_request.history or [],
            metadata=metadata,
        ):
            yield f"data: {json.dumps(event)}\n\n"
        yield 'data: {"node": "__done__"}\n\n'

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",   # disable nginx buffering if behind proxy
        },
    )


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    while True:
        data = await websocket.receive_json()
        result = await handle_chat(
            ChatRequest(
                message=data.get("message"),
                history=data.get("history", []),
                metadata=data.get("metadata", {}),
            )
        )
        await websocket.send_json(result.dict())
