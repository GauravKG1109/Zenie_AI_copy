from fastapi import APIRouter, Depends, WebSocket
from app.schemas.chat_schema import ChatRequest, ChatResponse
from app.controllers.chat_controller import handle_chat

router = APIRouter()

@router.post("/", response_model=ChatResponse)
async def chat_endpoint(chat_request: ChatRequest):
    """
    Endpoint to handle chat interactions.
    Receives a ChatRequest and returns a ChatResponse.
    """
    response = await handle_chat(chat_request)
    return response

@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    while True:
        data = await websocket.receive_json()
        message = data.get("message")
        history = data.get("history", [])
        metadata = data.get("metadata", {})
        result = await handle_chat(
            ChatRequest(
                message=message,
                history=history,
                metadata=metadata
            )
        )
        await websocket.send_json(result.dict())