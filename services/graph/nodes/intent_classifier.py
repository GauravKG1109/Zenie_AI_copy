import logging
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sentence_transformers import SentenceTransformer
from sklearn.metrics.pairwise import cosine_similarity

from services.graph.state import GraphState

logger = logging.getLogger(__name__)

# ── Resolve path to Intent_file.xlsx from any working directory ──────────────
_DATA_DIR = Path(__file__).resolve().parent.parent.parent.parent / "data"
_EXCEL_PATH = _DATA_DIR / "Intent_file.xlsx"

# ── Load model and build embedding matrix once at import time ─────────────────
logger.info("[IntentClassifier] Loading SentenceTransformer model...")
_model = SentenceTransformer("all-MiniLM-L6-v2")

logger.info("[IntentClassifier] Loading intent Excel from %s", _EXCEL_PATH)
try:
    _df = pd.read_excel(_EXCEL_PATH, engine="openpyxl")
except Exception:
    _df = pd.read_excel(_EXCEL_PATH, engine="xlrd")

# Clean up Excel
_df = _df[_df["Intent_Code"].notna() & (_df["Intent_Code"].astype(str).str.strip() != "")].copy()
_df = _df[_df["Intent_Code"].astype(str).str.strip() != "Intent_Code"].copy()
_df = _df.drop_duplicates(subset=["Intent_Code"], keep="last").copy()
_df = _df.reset_index(drop=True)

_CORE_COLS = [
    "Intent_Code", "Intent_Name", "Intent_Category",
    "Action_Type (READ/WRITE)", "Description",
    "Required_Parameters", "Optional_Parameters",
    "Security_Scope", "Human_Approval_Required", "Status", "View",
]
_df = _df[[c for c in _CORE_COLS if c in _df.columns]].copy()

if "View" in _df.columns:
    _df = _df[_df["View"].notna() & (_df["View"].astype(str).str.strip() != "")].copy()
    _df = _df.reset_index(drop=True)
    logger.info("[IntentClassifier] %d intents with mapped views loaded.", len(_df))
else:
    _df["View"] = None
    logger.info("[IntentClassifier] No View column — %d intents loaded.", len(_df))

_df["embedded_text"] = (
    _df["Intent_Name"].fillna("") + " " +
    _df["Intent_Category"].fillna("") + " " +
    _df["Description"].fillna("")
)

logger.info("[IntentClassifier] Building embedding matrix for %d intents...", len(_df))
_df["embeddings"] = _df["embedded_text"].apply(_model.encode)
_embedding_matrix = np.vstack(_df["embeddings"].values)
logger.info("[IntentClassifier] Embedding matrix ready.")


def _find_intent(query: str) -> dict:
    vec = _model.encode(query).reshape(1, -1)
    sims = cosine_similarity(vec, _embedding_matrix)[0]
    idx = int(np.argmax(sims))
    row = _df.iloc[idx]
    return {
        "similarity": round(float(sims[idx]) * 100, 2),
        "intent_code": str(row["Intent_Code"]),
        "intent_name": str(row["Intent_Name"]),
        "intent_category": str(row["Intent_Category"]),
        "action_type": str(row.get("Action_Type (READ/WRITE)", "")),
        "description": str(row["Description"]),
        "required_parameters": str(row["Required_Parameters"]) if pd.notna(row.get("Required_Parameters")) else "None",
        "optional_parameters": str(row["Optional_Parameters"]) if pd.notna(row.get("Optional_Parameters")) else "None",
        "security_scope": str(row["Security_Scope"]) if pd.notna(row.get("Security_Scope")) else "N/A",
        "human_approval": str(row["Human_Approval_Required"]) if pd.notna(row.get("Human_Approval_Required")) else "No",
        "status": str(row["Status"]),
        "view": str(row["View"]) if pd.notna(row.get("View")) else "",
    }


def intent_classifier_node(state: GraphState) -> dict:
    message = state.get("message", "")
    logger.info("[IntentClassifier] Classifying: %s", message)
    intent = _find_intent(message)
    log_line = (
        f"[IntentClassifier] Matched: {intent['intent_name']} "
        f"({intent['similarity']}% similarity) → view: {intent['view']}"
    )
    logger.info(log_line)
    return {
        "intent": intent,
        "intent_logs": [log_line],
    }
