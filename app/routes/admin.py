from fastapi import APIRouter

from services.graph.nodes.get_knowledgebase import reload_knowledge_base

router = APIRouter()


@router.post("/admin/reload-kb")
def reload_kb():
    reload_knowledge_base()
    return {"status": "reloaded"}