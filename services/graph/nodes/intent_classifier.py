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


# ── Keyword pre-filter (Modification 1) ───────────────────────────────────────
# When enabled, certain words in the user message restrict semantic search to a
# specific action_type BEFORE cosine similarity runs — preventing false READ
# matches for clear WRITE queries like "Create an invoice".
#
# Set KEYWORD_FILTER_ENABLED = False to bypass this filter entirely.
# Add more entries to _KEYWORD_RULES to cover additional trigger words.
#
# Pattern format: \b<word>\b  →  exact whole-word, case-insensitive match.
# Example: matches "Create" and "create" but NOT "created" or "creat".

KEYWORD_FILTER_ENABLED = True

_KEYWORD_RULES: list[tuple[re.Pattern, str]] = [
    # (compiled pattern,  action_type to restrict search to)

    # WRITE triggers — clear mutation keywords
    (re.compile(r'\bcreate\b', re.IGNORECASE), "WRITE"),
    (re.compile(r'\badd\b',    re.IGNORECASE), "WRITE"),
    (re.compile(r'\binsert\b', re.IGNORECASE), "WRITE"),
    (re.compile(r'\bpost\b',   re.IGNORECASE), "WRITE"),

    # READ triggers — query/retrieval keywords
    (re.compile(r'\btell\b',   re.IGNORECASE), "READ"),
    (re.compile(r'\bshow\b',   re.IGNORECASE), "READ"),
    (re.compile(r'\bfind\b',   re.IGNORECASE), "READ"),

    # ANALYSE triggers — reserved for future analytical intent type
    (re.compile(r'\bexplain\b', re.IGNORECASE), "ANALYSE"),
    (re.compile(r'\banalyse\b', re.IGNORECASE), "ANALYSE"),
    (re.compile(r'\banalyze\b', re.IGNORECASE), "ANALYSE"),  # US spelling
]


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
        "Typical_User_Query",   # used in embedding text
        "View", "SQL_Query",
    ]
    df = df[[c for c in _CORE_COLS if c in df.columns]].copy()

    if "SQL_Query" not in df.columns:
        df["SQL_Query"] = None
    if "Typical_User_Query" not in df.columns:
        df["Typical_User_Query"] = None

    # Embedding filter:
    #   READ intents  → require a non-empty View column (no view = no SQL = useless)
    #   WRITE intents → embed regardless of View (they drive payload filler, not SQL)
    if "View" not in df.columns:
        df["View"] = None

    action_col = "Action_Type (READ/WRITE)"
    if action_col in df.columns:
        is_write = df[action_col].astype(str).str.strip().str.upper() == "WRITE"
        has_view  = df["View"].notna() & (df["View"].astype(str).str.strip() != "")
        df = df[is_write | has_view].copy()
    else:
        df = df[df["View"].notna() & (df["View"].astype(str).str.strip() != "")].copy()

    df = df.reset_index(drop=True)
    return df


def _build_embeddings(df: pd.DataFrame) -> np.ndarray:
    texts = (
        df["Intent_Name"].fillna("") + " " +
        df["Intent_Category"].fillna("") + " " +
        df["Description"].fillna("") + " " +
        df["Typical_User_Query"].fillna("")
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


# ── Precompute per-action_type boolean masks (used by keyword pre-filter) ─────
_action_col = "Action_Type (READ/WRITE)"
if _action_col in _df.columns:
    _action_series = _df[_action_col].astype(str).str.strip().str.upper()
    _write_mask:   np.ndarray = (_action_series == "WRITE").values
    _read_mask:    np.ndarray = (_action_series == "READ").values
    _analyse_mask: np.ndarray = (_action_series == "ANALYSE").values  # future type
else:
    _write_mask   = np.zeros(len(_df), dtype=bool)
    _read_mask    = np.zeros(len(_df), dtype=bool)
    _analyse_mask = np.zeros(len(_df), dtype=bool)

_ACTION_MASKS: dict[str, np.ndarray] = {
    "WRITE":   _write_mask,
    "READ":    _read_mask,
    "ANALYSE": _analyse_mask,   # no rows yet; filter gracefully degrades (see _get_action_filter)
}


# ── Keyword pre-filter helper ─────────────────────────────────────────────────

def _get_action_filter(message: str) -> np.ndarray | None:
    """
    Checks the user message against _KEYWORD_RULES.
    Returns a boolean mask that restricts semantic search to matching action_type
    rows, or None if no keyword matched (= no restriction, search all rows).

    Only active when KEYWORD_FILTER_ENABLED is True.
    """
    if not KEYWORD_FILTER_ENABLED:
        return None

    for pattern, action_type in _KEYWORD_RULES:
        if pattern.search(message):
            mask = _ACTION_MASKS.get(action_type.upper())
            if mask is not None and mask.any():
                logger.info(
                    "[IntentClassifier] Keyword filter triggered by pattern '%s' "
                    "— restricting search to %s intents (%d rows)",
                    pattern.pattern, action_type, mask.sum(),
                )
                return mask
    return None


# ── Intent lookup ─────────────────────────────────────────────────────────────

_CANDIDATE_N = 5   # number of candidate intents returned to the orchestrator


def _parse_views(raw: str) -> list[str]:
    return [v.strip() for v in re.split(r'[,;]+', raw) if v.strip()][:4]


def _find_intent(query: str) -> dict:
    vec = _model.encode(query).reshape(1, -1)

    # Apply keyword pre-filter if triggered
    action_filter = _get_action_filter(query)
    if action_filter is not None:
        filtered_matrix = _embedding_matrix[action_filter]
        filtered_df     = _df[action_filter].reset_index(drop=True)
    else:
        filtered_matrix = _embedding_matrix
        filtered_df     = _df

    sims = cosine_similarity(vec, filtered_matrix)[0]
    idx  = int(np.argmax(sims))
    row  = filtered_df.iloc[idx]

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
        "security_scope":      str(row["Security_Scope"])          if pd.notna(row.get("Security_Scope"))          else "N/A",
        "human_approval":      str(row["Human_Approval_Required"]) if pd.notna(row.get("Human_Approval_Required")) else "No",
        "status":              str(row["Status"]),
        "view":                view_raw,
        "views":               views,
        "sql_query_manual":    sql_query_manual,
    }


def _find_top_n_intents(query: str, n: int = _CANDIDATE_N) -> list[dict]:
    """
    Returns top-N slim intent dicts for the orchestrator.
    Each dict: {intent_code, description, action_type, similarity}
    Applies the same keyword pre-filter as _find_intent.
    """
    vec = _model.encode(query).reshape(1, -1)

    action_filter = _get_action_filter(query)
    if action_filter is not None:
        filtered_matrix = _embedding_matrix[action_filter]
        filtered_df     = _df[action_filter].reset_index(drop=True)
    else:
        filtered_matrix = _embedding_matrix
        filtered_df     = _df

    sims        = cosine_similarity(vec, filtered_matrix)[0]
    top_indices = np.argsort(sims)[::-1][:n]

    return [
        {
            "intent_code": str(filtered_df.iloc[int(i)]["Intent_Code"]),
            "description": str(filtered_df.iloc[int(i)]["Description"]),
            "action_type": str(filtered_df.iloc[int(i)].get("Action_Type (READ/WRITE)", "")),
            "similarity":  round(float(sims[int(i)]), 4),
        }
        for i in top_indices
    ]


def _find_intent_by_code(intent_code: str) -> dict | None:
    """
    Looks up the full intent dict by exact Intent_Code.
    Used by orchestrator when it picks a candidate that isn't the classifier's top-1.
    Returns None if not found.
    """
    matches = _df[_df["Intent_Code"].astype(str) == intent_code]
    if matches.empty:
        return None
    row      = matches.iloc[0]
    view_raw = str(row["View"]) if pd.notna(row.get("View")) else ""
    views    = _parse_views(view_raw)
    sql_val          = row.get("SQL_Query")
    sql_query_manual = (
        str(sql_val).strip()
        if pd.notna(sql_val) and str(sql_val).strip()
        else ""
    )
    return {
        "similarity":          None,
        "intent_code":         str(row["Intent_Code"]),
        "intent_name":         str(row["Intent_Name"]),
        "intent_category":     str(row["Intent_Category"]),
        "action_type":         str(row.get("Action_Type (READ/WRITE)", "")),
        "description":         str(row["Description"]),
        "required_parameters": str(row["Required_Parameters"]) if pd.notna(row.get("Required_Parameters")) else "None",
        "optional_parameters": str(row["Optional_Parameters"]) if pd.notna(row.get("Optional_Parameters")) else "None",
        "security_scope":      str(row["Security_Scope"])          if pd.notna(row.get("Security_Scope"))          else "N/A",
        "human_approval":      str(row["Human_Approval_Required"]) if pd.notna(row.get("Human_Approval_Required")) else "No",
        "status":              str(row["Status"]),
        "view":                view_raw,
        "views":               views,
        "sql_query_manual":    sql_query_manual,
    }


def intent_classifier_node(state: GraphState) -> dict:
    message = state.get("message", "")
    logger.info("[IntentClassifier] Classifying: %s", message)

    intent     = _find_intent(message)
    candidates = _find_top_n_intents(message)

    views_str  = ", ".join(intent["views"]) if intent["views"] else "none"
    has_manual = bool(intent["sql_query_manual"])
    log_line   = (
        f"[IntentClassifier] Matched: {intent['intent_name']} "
        f"({intent['similarity']}% similarity) → views: [{views_str}]"
        + (" | pre-written SQL found" if has_manual else "")
    )
    log_candidates = (
        f"[IntentClassifier] Top-{len(candidates)} candidates: "
        + ", ".join(f"{c['intent_code']}({c['similarity']:.3f})" for c in candidates)
    )
    logger.info(log_line)
    logger.info(log_candidates)

    return {
        "intent":            intent,
        "candidate_intents": candidates,
        "intent_logs":       [log_line, log_candidates],
    }
