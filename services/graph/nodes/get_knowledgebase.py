"""
get_knowledgebase.py — RAG node for GET_KNOWLEDGEBASE routing.

Chunking strategy:
  - Primary split on ## headings (major sections)
  - ### subsections stay attached to their parent ## chunk for context richness
  - Each chunk carries: heading path, text, char_count
  - Overlap: last 2 sentences of previous chunk prepended to next chunk
    (avoids context loss at boundaries without bloating embeddings)

Embedding strategy:
  - Same all-MiniLM-L6-v2 model already used by intent_classifier
    (zero new dependencies)
  - Cache stored as embeddings_cache.pkl next to the .md file
  - Cache auto-invalidated when .md file mtime > cache mtime
  - Embeddings built at import time (same pattern as intent embeddings)

Retrieval strategy:
  - Cosine similarity of query embedding vs all chunk embeddings
  - Top-3 chunks returned (enough context, not too much token waste)
  - Minimum similarity threshold: 0.25 (below = "not found in KB")
  - Top chunks passed to Claude Haiku with strict grounding instruction
  - Claude answers ONLY from retrieved chunks, no hallucination
"""

import os
import re
import pickle
import logging
import hashlib
from pathlib import Path
from typing import Optional

import numpy as np
from anthropic import Anthropic
from sentence_transformers import SentenceTransformer

from services.graph.state import GraphState

logger = logging.getLogger(__name__)

# ── Paths ────────────────────────────────────────────────────────────────────

_HERE        = Path(__file__).resolve()
_DATA_DIR    = _HERE.parents[3] / "data" / "knowledge"
_MD_PATH     = _DATA_DIR / "company_knowledge.md"
_CACHE_PATH  = _DATA_DIR / "embeddings_cache.pkl"

# ── Config ───────────────────────────────────────────────────────────────────

_MODEL_PATH       = str(_HERE.parents[3] / "data" / "models" / "all-MiniLM-L6-v2")
_TOP_K            = 3       # number of chunks to retrieve
_MIN_SIMILARITY   = 0.25    # below this → answer "not found"
_OVERLAP_SENTENCES = 2      # sentences from prev chunk prepended to next

# ── Chunk dataclass (plain dict for pickle compatibility) ─────────────────────

def _make_chunk(heading: str, parent_heading: str, text: str, index: int) -> dict:
    return {
        "index":          index,
        "heading":        heading,
        "parent_heading": parent_heading,
        "text":           text,
        "char_count":     len(text),
    }

# ── Markdown parser ───────────────────────────────────────────────────────────

def _parse_markdown(md_text: str) -> list[dict]:
    """
    Split markdown into chunks on ## headings.
    ### subsections are kept with their parent ## chunk.

    Overlap strategy: last N sentences of the previous chunk are
    prepended to the current chunk before embedding so boundary
    context is preserved.
    """
    lines = md_text.splitlines()
    chunks: list[dict] = []

    current_h2       = ""
    current_h3       = ""
    current_lines: list[str] = []

    def _flush(h2: str, h3: str, body_lines: list[str], prev_chunk: Optional[dict]) -> Optional[dict]:
        text = "\n".join(body_lines).strip()
        if not text:
            return None

        heading = f"{h2} > {h3}" if h3 else h2

        # Prepend overlap from previous chunk
        overlap_prefix = ""
        if prev_chunk:
            sentences = re.split(r'(?<=[.!?])\s+', prev_chunk["text"])
            tail = sentences[-_OVERLAP_SENTENCES:] if len(sentences) >= _OVERLAP_SENTENCES else sentences
            overlap_prefix = " ".join(tail).strip()
            if overlap_prefix:
                overlap_prefix = f"[context] {overlap_prefix}\n\n"

        full_text = overlap_prefix + f"{heading}\n\n{text}"
        return _make_chunk(
            heading        = heading,
            parent_heading = h2,
            text           = full_text,
            index          = len(chunks),
        )

    prev_chunk = None

    for line in lines:
        h2_match = re.match(r'^##\s+(.+)$', line)
        h3_match = re.match(r'^###\s+(.+)$', line)

        if h2_match:
            # Flush current buffer
            c = _flush(current_h2, current_h3, current_lines, prev_chunk)
            if c:
                chunks.append(c)
                prev_chunk = c

            current_h2    = h2_match.group(1).strip()
            current_h3    = ""
            current_lines = []

        elif h3_match:
            # Don't flush on h3 — keep subsections in same chunk
            current_h3 = h3_match.group(1).strip()
            current_lines.append(line)

        else:
            current_lines.append(line)

    # Flush final buffer
    c = _flush(current_h2, current_h3, current_lines, prev_chunk)
    if c:
        chunks.append(c)

    logger.info("[KnowledgeBase] Parsed %d chunks from markdown", len(chunks))
    return chunks


# ── Cache helpers ─────────────────────────────────────────────────────────────

def _md_hash(md_path: Path) -> str:
    """MD5 of file content — more reliable than mtime alone."""
    h = hashlib.md5()
    h.update(md_path.read_bytes())
    return h.hexdigest()


def _cache_is_valid(md_path: Path, cache_path: Path) -> bool:
    if not cache_path.exists():
        return False
    try:
        with open(cache_path, "rb") as f:
            cached = pickle.load(f)
        return cached.get("md_hash") == _md_hash(md_path)
    except Exception:
        return False


def _save_cache(cache_path: Path, chunks: list[dict], embeddings: np.ndarray, md_hash: str):
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    with open(cache_path, "wb") as f:
        pickle.dump({
            "md_hash":    md_hash,
            "chunks":     chunks,
            "embeddings": embeddings,
        }, f)
    logger.info("[KnowledgeBase] Cache saved → %s", cache_path)


def _load_cache(cache_path: Path) -> tuple[list[dict], np.ndarray]:
    with open(cache_path, "rb") as f:
        cached = pickle.load(f)
    return cached["chunks"], cached["embeddings"]


# ── Embedding builder ─────────────────────────────────────────────────────────

def _build_embeddings(
    chunks: list[dict],
    model: SentenceTransformer,
) -> np.ndarray:
    texts = [c["text"] for c in chunks]
    embeddings = model.encode(texts, show_progress_bar=False, convert_to_numpy=True)
    return embeddings


# ── Module-level init (runs once at import / startup) ─────────────────────────

def _init_kb() -> tuple[list[dict], np.ndarray, SentenceTransformer]:
    """
    Load or build the knowledge base embeddings.
    Called once at module import — same pattern as intent_classifier.
    """
    # Load the embedding model (already cached from intent_classifier usage)
    logger.info("[KnowledgeBase] Loading embedding model from %s", _MODEL_PATH)
    model = SentenceTransformer(_MODEL_PATH)

    if not _MD_PATH.exists():
        logger.error("[KnowledgeBase] Markdown file not found at %s", _MD_PATH)
        return [], np.array([]), model

    current_hash = _md_hash(_MD_PATH)

    if _cache_is_valid(_MD_PATH, _CACHE_PATH):
        logger.info("[KnowledgeBase] Cache valid — loading from %s", _CACHE_PATH)
        chunks, embeddings = _load_cache(_CACHE_PATH)
        logger.info("[KnowledgeBase] Loaded %d chunks from cache", len(chunks))
    else:
        logger.info("[KnowledgeBase] Cache stale or missing — rebuilding embeddings")
        md_text    = _MD_PATH.read_text(encoding="utf-8")
        chunks     = _parse_markdown(md_text)
        embeddings = _build_embeddings(chunks, model)
        _save_cache(_CACHE_PATH, chunks, embeddings, current_hash)
        logger.info("[KnowledgeBase] Built and cached %d chunk embeddings", len(chunks))

    return chunks, embeddings, model


# Module-level singletons
_KB_CHUNKS:     list[dict]
_KB_EMBEDDINGS: np.ndarray
_KB_MODEL:      SentenceTransformer

try:
    _KB_CHUNKS, _KB_EMBEDDINGS, _KB_MODEL = _init_kb()
except Exception as e:
    logger.error("[KnowledgeBase] Init failed: %s", e)
    _KB_CHUNKS, _KB_EMBEDDINGS, _KB_MODEL = [], np.array([]), None  # type: ignore


# ── Retriever ─────────────────────────────────────────────────────────────────

def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Vectorized cosine similarity of one vector against a matrix."""
    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(b, axis=1)
    if norm_a == 0 or np.any(norm_b == 0):
        return np.zeros(len(b))
    return np.dot(b, a) / (norm_b * norm_a)


def retrieve_chunks(query: str, top_k: int = _TOP_K) -> list[tuple[dict, float]]:
    """
    Encode the query and return top_k (chunk, similarity_score) pairs.
    Returns empty list if model not loaded or no chunks above threshold.
    """
    if _KB_MODEL is None or len(_KB_CHUNKS) == 0:
        logger.warning("[KnowledgeBase] Model or chunks not available for retrieval")
        return []

    query_embedding = _KB_MODEL.encode(query, show_progress_bar=False, convert_to_numpy=True)
    similarities    = _cosine_similarity(query_embedding, _KB_EMBEDDINGS)

    top_indices = np.argsort(similarities)[::-1][:top_k]

    results = []
    for idx in top_indices:
        score = float(similarities[idx])
        if score >= _MIN_SIMILARITY:
            results.append((_KB_CHUNKS[idx], score))

    logger.info(
        "[KnowledgeBase] Retrieved %d chunks for query '%s' (top score: %.3f)",
        len(results),
        query[:60],
        results[0][1] if results else 0.0,
    )
    return results


# ── LLM answer generator ──────────────────────────────────────────────────────

_client = Anthropic()

_ANSWER_SYSTEM_PROMPT = """
You are Zenie AI, the financial assistant for Global FPO.
You answer questions using ONLY the context chunks provided below.

Rules:
- Answer directly and concisely based on the provided context.
- If the context does not contain enough information to answer, say:
  "I don't have specific information about that in my knowledge base. 
   Please contact Global FPO directly at contact@globalfpo.com."
- Never invent facts, figures, or policies not present in the context.
- Keep answers focused — 2 to 5 sentences unless detail is explicitly requested.
- Do not mention "chunks", "context", or "retrieval" in your answer.
  Speak naturally as if you know this information about Global FPO.
"""


def _generate_answer(query: str, chunks: list[tuple[dict, float]]) -> str:
    if not chunks:
        return (
            "I don't have specific information about that in my knowledge base. "
            "Please contact Global FPO directly at contact@globalfpo.com or "
            "speak with your account manager."
        )

    # Build context block from retrieved chunks
    context_parts = []
    for i, (chunk, score) in enumerate(chunks, 1):
        context_parts.append(
            f"[Section {i}: {chunk['heading']} | relevance: {score:.2f}]\n"
            f"{chunk['text']}"
        )
    context_block = "\n\n---\n\n".join(context_parts)

    user_message = f"""Context from Global FPO knowledge base:

{context_block}

---

User question: {query}

Answer based only on the context above:"""

    response = _client.messages.create(
        model      = "claude-haiku-4-5-20251001",
        max_tokens = 512,
        system     = _ANSWER_SYSTEM_PROMPT,
        messages   = [{"role": "user", "content": user_message}],
    )

    return response.content[0].text.strip()


# ── Public reload function (call when .md is updated at runtime) ──────────────

def reload_knowledge_base():
    """
    Force a full re-parse and re-embed of the markdown file.
    Call this if the .md file is updated while the server is running.
    Example: expose a POST /admin/reload-kb endpoint that calls this.
    """
    global _KB_CHUNKS, _KB_EMBEDDINGS

    if not _MD_PATH.exists():
        logger.error("[KnowledgeBase] Cannot reload — file not found: %s", _MD_PATH)
        return

    logger.info("[KnowledgeBase] Force reload triggered")
    md_text        = _MD_PATH.read_text(encoding="utf-8")
    current_hash   = _md_hash(_MD_PATH)
    chunks         = _parse_markdown(md_text)
    embeddings     = _build_embeddings(chunks, _KB_MODEL)
    _save_cache(_CACHE_PATH, chunks, embeddings, current_hash)

    _KB_CHUNKS     = chunks
    _KB_EMBEDDINGS = embeddings

    logger.info("[KnowledgeBase] Reload complete — %d chunks", len(_KB_CHUNKS))


# ── LangGraph node ────────────────────────────────────────────────────────────

def get_knowledgebase_node(state: GraphState) -> dict:
    message = state.get("message", "")
    logger.info("[KnowledgeBase] Query: %s", message)

    # Retrieve relevant chunks
    retrieved = retrieve_chunks(message, top_k=_TOP_K)

    # Log what was retrieved for debugging
    retrieval_logs = [
        f"[KnowledgeBase] query='{message[:60]}' | chunks_found={len(retrieved)}"
    ]
    for chunk, score in retrieved:
        retrieval_logs.append(
            f"[KnowledgeBase] chunk='{chunk['heading']}' | score={score:.3f}"
        )

    # Generate grounded answer
    answer = _generate_answer(message, retrieved)

    logger.info("[KnowledgeBase] Answer generated (%d chars)", len(answer))

    return {
        "reply":        answer,
        "payload_logs": retrieval_logs,
    }