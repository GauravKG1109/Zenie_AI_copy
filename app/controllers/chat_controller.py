from app.schemas.chat_schema import ChatRequest, ChatResponse
from services.message_service import process_message


async def handle_chat(request: ChatRequest) -> ChatResponse:
    """
    Main controller function to handle chat interactions.
    Passes company_id and session_id via metadata to the pipeline.
    """
    try:
        metadata = {
            **(request.metadata or {}),
            "company_id": request.company_id or "1",
            "session_id": request.session_id or "",
        }
        result = await process_message(
            message=request.message,
            history=request.history or [],
            metadata=metadata,
        )
        return ChatResponse(
            status="success",
            response=result.get("response", ""),
            data=result.get("data", {}),
        )
    except Exception as e:
        return ChatResponse(
            status="error",
            response=str(e),
            data={},
        )
