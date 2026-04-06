# ── Suppress all transformer / HuggingFace noise ─────────────────────────────
# Must be set BEFORE importing sentence_transformers or transformers
import os
os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")
os.environ.setdefault("HF_HUB_DISABLE_IMPLICIT_TOKEN", "1")
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

import hashlib
import logging
import pickle
import re
from pathlib import Path

import numpy as np
import pandas as pd

# Silence the noisy sub-loggers before the heavy imports happen
logging.getLogger("transformers").setLevel(logging.ERROR)
logging.getLogger("sentence_transformers").setLevel(logging.ERROR)
logging.getLogger("huggingface_hub").setLevel(logging.ERROR)

from sentence_transformers import SentenceTransformer
from sklearn.metrics.pairwise import cosine_similarity

from services.graph.state import GraphState

logger = logging.getLogger(__name__)

# ── Paths ─────────────────────────────────────────────────────────────────────
_DATA_DIR      = Path(__file__).resolve().parent.parent.parent.parent / "data"
_EXCEL_PATH    = _DATA_DIR / "Intent_file.xlsx"
_MODEL_DIR     = _DATA_DIR / "models" / "all-MiniLM-L6-v2"
_EMBED_CACHE   = _DATA_DIR / "embeddings_cache.pkl"


# ── Load model — local disk only, no network call after first download ────────
if _MODEL_DIR.exists():
    logger.info("[IntentClassifier] Loading model from %s", _MODEL_DIR)
    _model = SentenceTransformer(str(_MODEL_DIR))
else:
    logger.info("[IntentClassifier] Local model not found — downloading (first run only)...")
    _model = SentenceTransformer("all-MiniLM-L6-v2")
    _MODEL_DIR.mkdir(parents=True, exist_ok=True)
    _model.save(str(_MODEL_DIR))
    logger.info("[IntentClassifier] Model saved to %s", _MODEL_DIR)


# ── Excel helpers ─────────────────────────────────────────────────────────────

def _excel_md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def _load_and_clean_excel() -> pd.DataFrame:
    try:
        df = pd.read_excel(_EXCEL_PATH, engine="openpyxl")
    except Exception:
        df = pd.read_excel(_EXCEL_PATH, engine="xlrd")

    df = df[df["Intent_Code"].notna() & (df["Intent_Code"].astype(str).str.strip() != "")].copy()
    df = df[df["Intent_Code"].astype(str).str.strip() != "Intent_Code"].copy()
    df = df.drop_duplicates(subset=["Intent_Code"], keep="last").copy()
    df = df.reset_index(drop=True)

    _CORE_COLS = [
        "Intent_Code", "Intent_Name", "Intent_Category",
        "Action_Type (READ/WRITE)", "Description",
        "Required_Parameters", "Optional_Parameters",
        "Security_Scope", "Human_Approval_Required", "Status",
        "View", "SQL_Query",
    ]
    df = df[[c for c in _CORE_COLS if c in df.columns]].copy()

    if "SQL_Query" not in df.columns:
        df["SQL_Query"] = None

    # Only embed rows with a non-empty View column
    if "View" in df.columns:
        df = df[df["View"].notna() & (df["View"].astype(str).str.strip() != "")].copy()
        df = df.reset_index(drop=True)
    else:
        df["View"] = None

    return df


def _build_embeddings(df: pd.DataFrame) -> np.ndarray:
    texts = (
        df["Intent_Name"].fillna("") + " " +
        df["Intent_Category"].fillna("") + " " +
        df["Description"].fillna("")
    ).tolist()
    return np.vstack([_model.encode(t) for t in texts])


# ── Load or rebuild embedding cache ──────────────────────────────────────────
# Cache is invalidated automatically when Intent_file.xlsx is modified.

_current_hash = _excel_md5(_EXCEL_PATH)

if _EMBED_CACHE.exists():
    with open(_EMBED_CACHE, "rb") as _f:
        _cached = pickle.load(_f)

    if _cached.get("excel_hash") == _current_hash:
        _df               = _cached["df"]
        _embedding_matrix = _cached["embedding_matrix"]
        logger.info(
            "[IntentClassifier] Embeddings loaded from cache (%d intents).", len(_df)
        )
    else:
        logger.info("[IntentClassifier] Intent file changed — rebuilding embeddings...")
        _df               = _load_and_clean_excel()
        _embedding_matrix = _build_embeddings(_df)
        with open(_EMBED_CACHE, "wb") as _f:
            pickle.dump(
                {"excel_hash": _current_hash, "df": _df, "embedding_matrix": _embedding_matrix},
                _f,
            )
        logger.info("[IntentClassifier] Embeddings rebuilt and cached (%d intents).", len(_df))
else:
    logger.info("[IntentClassifier] No cache found — building embeddings for the first time...")
    _df               = _load_and_clean_excel()
    _embedding_matrix = _build_embeddings(_df)
    with open(_EMBED_CACHE, "wb") as _f:
        pickle.dump(
            {"excel_hash": _current_hash, "df": _df, "embedding_matrix": _embedding_matrix},
            _f,
        )
    logger.info("[IntentClassifier] Embeddings built and cached (%d intents).", len(_df))


# ── Intent lookup ─────────────────────────────────────────────────────────────

def _parse_views(raw: str) -> list[str]:
    return [v.strip() for v in re.split(r'[,;]+', raw) if v.strip()][:4]


def _find_intent(query: str) -> dict:
    # Only the user query is encoded at request time — fast single-vector op
    vec  = _model.encode(query).reshape(1, -1)
    sims = cosine_similarity(vec, _embedding_matrix)[0]
    idx  = int(np.argmax(sims))
    row  = _df.iloc[idx]

    view_raw = str(row["View"]) if pd.notna(row.get("View")) else ""
    views    = _parse_views(view_raw)

    sql_val          = row.get("SQL_Query")
    sql_query_manual = (
        str(sql_val).strip()
        if pd.notna(sql_val) and str(sql_val).strip()
        else ""
    )

    return {
        "similarity":          round(float(sims[idx]) * 100, 2),
        "intent_code":         str(row["Intent_Code"]),
        "intent_name":         str(row["Intent_Name"]),
        "intent_category":     str(row["Intent_Category"]),
        "action_type":         str(row.get("Action_Type (READ/WRITE)", "")),
        "description":         str(row["Description"]),
        "required_parameters": str(row["Required_Parameters"]) if pd.notna(row.get("Required_Parameters")) else "None",
        "optional_parameters": str(row["Optional_Parameters"]) if pd.notna(row.get("Optional_Parameters")) else "None",
        "security_scope":      str(row["Security_Scope"])        if pd.notna(row.get("Security_Scope"))        else "N/A",
        "human_approval":      str(row["Human_Approval_Required"]) if pd.notna(row.get("Human_Approval_Required")) else "No",
        "status":              str(row["Status"]),
        "view":                view_raw,
        "views":               views,
        "sql_query_manual":    sql_query_manual,
    }


def intent_classifier_node(state: GraphState) -> dict:
    message = state.get("message", "")
    logger.info("[IntentClassifier] Classifying: %s", message)
    intent  = _find_intent(message)

    views_str  = ", ".join(intent["views"]) if intent["views"] else "none"
    has_manual = bool(intent["sql_query_manual"])
    log_line   = (
        f"[IntentClassifier] Matched: {intent['intent_name']} "
        f"({intent['similarity']}% similarity) → views: [{views_str}]"
        + (" | pre-written SQL found" if has_manual else "")
    )
    logger.info(log_line)
    return {
        "intent":      intent,
        "intent_logs": [log_line],
    }
