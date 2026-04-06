from datetime import datetime
from typing import Any, Dict, Optional, List #typing library for type hints
from pydantic import BaseModel

class Message(BaseModel):
    role: str #"user" | "assistant" | "toolresponse"
    content: str
    timestamp: Optional[datetime] = None

class ChatRequest(BaseModel):
    company_id: Optional[str] = None
    session_id: Optional[str] = None
    message: str #Latest User message
    history: Optional[List[Message]] = None #Previous conversation history
    metadata: Optional[Dict[str, Any]] = None #Additional info like user preferences, context, etc.

class ChatResponse(BaseModel):
    status : str
    response : str
    data: Optional[Dict[str, Any]] = None #Additional info like tool outputs, follow-up actions, etc.

