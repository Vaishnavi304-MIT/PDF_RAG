from pathlib import Path
from datetime import datetime, timezone
from uuid import uuid4
import sqlite3
import re
import json
import threading
import os

import fitz
import faiss
import numpy as np
from sentence_transformers import SentenceTransformer
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from app.services.agent import answer_question

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
DB_PATH = DATA_DIR / "intellidoc.db"
INDEX_PATH = DATA_DIR / "faiss.index"
MAP_PATH = DATA_DIR / "faiss_mapping.json"

MAX_FILE_SIZE = 20 * 1024 * 1024
CHUNK_SIZE = 1000
CHUNK_OVERLAP = 150
MODEL_NAME = "sentence-transformers/paraphrase-MiniLM-L3-v2"
TOP_K_DEFAULT = 5

DATA_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="IntelliDoc AI API", version="0.5.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

db_lock = threading.RLock()
vector_lock = threading.RLock()
model = None
faiss_index = None
vector_mapping = []


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def initialize_database():
    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS documents (
                id TEXT PRIMARY KEY,
                filename TEXT NOT NULL,
                stored_filename TEXT NOT NULL,
                file_size INTEGER NOT NULL,
                page_count INTEGER NOT NULL,
                extracted_chars INTEGER NOT NULL,
                uploaded_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS chunks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                document_id TEXT NOT NULL,
                page_number INTEGER NOT NULL,
                chunk_index INTEGER NOT NULL,
                content TEXT NOT NULL,
                FOREIGN KEY(document_id) REFERENCES documents(id) ON DELETE CASCADE
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_chunks_document ON chunks(document_id)")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS chat_sessions (
                id TEXT PRIMARY KEY, title TEXT NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS chat_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id TEXT NOT NULL, role TEXT NOT NULL,
                content TEXT NOT NULL, created_at TEXT NOT NULL,
                FOREIGN KEY(chat_id) REFERENCES chat_sessions(id) ON DELETE CASCADE
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_chat_messages_chat ON chat_messages(chat_id, id)")
        conn.commit()


def split_text(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP):
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return []
    result, start = [], 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            boundary = text.rfind(" ", start + int(size * 0.65), end)
            if boundary > start:
                end = boundary
        part = text[start:end].strip()
        if part:
            result.append(part)
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return result


def get_model():
    global model
    if model is None:
        # The model is downloaded from Hugging Face on first use and then cached locally.
        model = SentenceTransformer(MODEL_NAME)
    return model


def encode_texts(texts):
    if not texts:
        return np.empty((0, 384), dtype="float32")
    vectors = get_model().encode(
        texts,
        convert_to_numpy=True,
        normalize_embeddings=True,
        show_progress_bar=False,
    )
    return np.asarray(vectors, dtype="float32")


def load_vector_index():
    global faiss_index, vector_mapping
    with vector_lock:
        if INDEX_PATH.exists() and MAP_PATH.exists():
            try:
                faiss_index = faiss.read_index(str(INDEX_PATH))
                vector_mapping = json.loads(MAP_PATH.read_text(encoding="utf-8"))
                if faiss_index.ntotal != len(vector_mapping):
                    raise ValueError("FAISS index and mapping are out of sync")
                return
            except Exception:
                faiss_index = None
                vector_mapping = []

        rebuild_vector_index()


def persist_vector_index(index, mapping):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    temp_index = DATA_DIR / "faiss.index.tmp"
    temp_map = DATA_DIR / "faiss_mapping.json.tmp"
    faiss.write_index(index, str(temp_index))
    temp_map.write_text(json.dumps(mapping, ensure_ascii=False), encoding="utf-8")
    temp_index.replace(INDEX_PATH)
    temp_map.replace(MAP_PATH)


def rebuild_vector_index():
    global faiss_index, vector_mapping
    with vector_lock:
        with get_connection() as conn:
            rows = conn.execute("""
                SELECT c.id AS chunk_id, c.document_id, c.page_number, c.chunk_index,
                       c.content, d.filename
                FROM chunks c JOIN documents d ON d.id = c.document_id
                ORDER BY c.id
            """).fetchall()

        if not rows:
            faiss_index = faiss.IndexFlatIP(384)
            vector_mapping = []
            persist_vector_index(faiss_index, vector_mapping)
            return

        texts = [row["content"] for row in rows]
        vectors = encode_texts(texts)
        index = faiss.IndexFlatIP(vectors.shape[1])
        index.add(vectors)
        mapping = [
            {
                "chunk_id": row["chunk_id"],
                "document_id": row["document_id"],
                "filename": row["filename"],
                "page_number": row["page_number"],
                "chunk_index": row["chunk_index"],
                "content": row["content"],
            }
            for row in rows
        ]
        persist_vector_index(index, mapping)
        faiss_index = index
        vector_mapping = mapping


def document_to_dict(row):
    return {
        "id": row["id"],
        "filename": row["filename"],
        "file_size": row["file_size"],
        "page_count": row["page_count"],
        "extracted_chars": row["extracted_chars"],
        "uploaded_at": row["uploaded_at"],
    }


class SearchRequest(BaseModel):
    query: str = Field(min_length=2, max_length=2000)
    top_k: int = Field(default=TOP_K_DEFAULT, ge=1, le=10)


class ChatMessage(BaseModel):
    role: str
    content: str = Field(max_length=6000)


class ChatRequest(BaseModel):
    question: str = Field(min_length=2, max_length=2000)
    chat_id: str | None = None
    history: list[ChatMessage] = Field(default_factory=list)
    top_k: int = Field(default=5, ge=1, le=10)


initialize_database()


@app.on_event("startup")
def startup_event():
    # Loading an existing index is quick. If no index exists, rebuild only when chunks exist.
    load_vector_index()


@app.get("/")
def root():
    return {"message": "Welcome to IntelliDoc AI API", "docs": "/docs", "health": "/health"}


@app.get("/health")
def health():
    return {"status": "ok", "service": "IntelliDoc AI API"}


@app.post("/api/documents/upload")
async def upload_document(file: UploadFile = File(...)):
    original_name = Path(file.filename or "document.pdf").name
    if not original_name.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported.")

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="The uploaded file is empty.")
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(status_code=413, detail="PDF exceeds the 20 MB upload limit.")
    if not content.startswith(b"%PDF-"):
        raise HTTPException(status_code=400, detail="The file does not appear to be a valid PDF.")

    try:
        pdf = fitz.open(stream=content, filetype="pdf")
        if pdf.is_encrypted:
            pdf.close()
            raise HTTPException(status_code=400, detail="Password-protected PDFs are not supported yet.")
        page_texts = [(i + 1, page.get_text("text").strip()) for i, page in enumerate(pdf)]
        page_count = pdf.page_count
        pdf.close()
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Could not read this PDF: {exc}") from exc

    extracted_text = "\n".join(text for _, text in page_texts)
    if not extracted_text.strip():
        raise HTTPException(
            status_code=422,
            detail="No selectable text was found. This may be a scanned/image-only PDF; OCR is not included yet.",
        )

    prepared_chunks = []
    for page_number, page_text in page_texts:
        for chunk_index, chunk in enumerate(split_text(page_text)):
            prepared_chunks.append((page_number, chunk_index, chunk))
    if not prepared_chunks:
        raise HTTPException(status_code=422, detail="No text chunks could be created from this PDF.")

    document_id = str(uuid4())
    stored_filename = f"{document_id}.pdf"
    stored_path = UPLOAD_DIR / stored_filename
    uploaded_at = datetime.now(timezone.utc).isoformat()

    try:
        stored_path.write_bytes(content)
        with db_lock, get_connection() as conn:
            conn.execute(
                """INSERT INTO documents
                   (id, filename, stored_filename, file_size, page_count, extracted_chars, uploaded_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (document_id, original_name, stored_filename, len(content), page_count, len(extracted_text), uploaded_at),
            )
            conn.executemany(
                """INSERT INTO chunks (document_id, page_number, chunk_index, content)
                   VALUES (?, ?, ?, ?)""",
                [(document_id, page, idx, chunk) for page, idx, chunk in prepared_chunks],
            )
            conn.commit()

        # Rebuild persisted index so new chunks are searchable immediately.
        rebuild_vector_index()
    except Exception as exc:
        with db_lock, get_connection() as conn:
            conn.execute("DELETE FROM chunks WHERE document_id = ?", (document_id,))
            conn.execute("DELETE FROM documents WHERE id = ?", (document_id,))
            conn.commit()
        stored_path.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail=f"PDF was extracted but indexing failed: {exc}") from exc

    return {
        "message": "PDF uploaded, processed and indexed successfully.",
        "document": {
            "id": document_id,
            "filename": original_name,
            "file_size": len(content),
            "page_count": page_count,
            "extracted_chars": len(extracted_text),
            "chunk_count": len(prepared_chunks),
            "uploaded_at": uploaded_at,
        },
    }


@app.get("/api/documents")
def list_documents():
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, filename, file_size, page_count, extracted_chars, uploaded_at FROM documents ORDER BY uploaded_at DESC"
        ).fetchall()
    return {"documents": [document_to_dict(row) for row in rows]}


@app.get("/api/documents/{document_id}")
def get_document(document_id: str):
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id, filename, file_size, page_count, extracted_chars, uploaded_at FROM documents WHERE id = ?",
            (document_id,),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Document not found.")
        chunk_rows = conn.execute(
            "SELECT page_number, chunk_index, content FROM chunks WHERE document_id = ? ORDER BY page_number, chunk_index",
            (document_id,),
        ).fetchall()
    return {
        "document": document_to_dict(row),
        "chunks": [
            {"page_number": r["page_number"], "chunk_index": r["chunk_index"], "content": r["content"]}
            for r in chunk_rows
        ],
    }


@app.delete("/api/documents/{document_id}")
def delete_document(document_id: str):
    with db_lock, get_connection() as conn:
        row = conn.execute("SELECT stored_filename FROM documents WHERE id = ?", (document_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Document not found.")
        conn.execute("DELETE FROM chunks WHERE document_id = ?", (document_id,))
        conn.execute("DELETE FROM documents WHERE id = ?", (document_id,))
        conn.commit()
    (UPLOAD_DIR / row["stored_filename"]).unlink(missing_ok=True)
    try:
        rebuild_vector_index()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Document deleted, but vector index rebuild failed: {exc}") from exc
    return {"message": "Document deleted successfully.", "document_id": document_id}


def retrieve_chunks(query: str, top_k: int = TOP_K_DEFAULT):
    """Search the existing FAISS index and return Phase 4-compatible passages."""
    with vector_lock:
        if faiss_index is None or not vector_mapping or faiss_index.ntotal == 0:
            return []
        query_vector = encode_texts([query])
        k = min(top_k, faiss_index.ntotal)
        scores, indices = faiss_index.search(query_vector, k)
        results = []
        for score, position in zip(scores[0].tolist(), indices[0].tolist()):
            if position < 0 or position >= len(vector_mapping):
                continue
            item = vector_mapping[position]
            results.append({
                "document_id": item["document_id"],
                "filename": item["filename"],
                "page_number": item["page_number"],
                "chunk_index": item["chunk_index"],
                "content": item["content"],
                "score": round(float(score), 4),
            })
    return results


def retrieve_overview_chunks(limit: int = 8):
    """Return the opening passages of the most recently uploaded PDF.

    This is used for document-level questions such as "What is this paper about?",
    where semantic similarity against a short generic question can be weak.
    """
    with get_connection() as conn:
        document = conn.execute(
            "SELECT id FROM documents ORDER BY uploaded_at DESC LIMIT 1"
        ).fetchone()
        if document is None:
            return []

        rows = conn.execute(
            """SELECT c.document_id, d.filename, c.page_number,
                      c.chunk_index, c.content
               FROM chunks c
               JOIN documents d ON d.id = c.document_id
               WHERE c.document_id = ?
               ORDER BY c.page_number ASC, c.chunk_index ASC
               LIMIT ?""",
            (document["id"], limit),
        ).fetchall()

    return [
        {
            "document_id": row["document_id"],
            "filename": row["filename"],
            "page_number": row["page_number"],
            "chunk_index": row["chunk_index"],
            "content": row["content"],
            "score": 1.0,
        }
        for row in rows
    ]


@app.post("/api/search")
def semantic_search(request: SearchRequest):
    query = request.query.strip()
    if len(query) < 2:
        raise HTTPException(status_code=400, detail="Enter a longer search query.")
    results = retrieve_chunks(query, request.top_k)
    message = f"Found {len(results)} relevant passage(s)." if results else "No indexed PDF chunks are available yet."
    return {"query": query, "results": results, "message": message}


@app.post("/api/chats")
def create_chat():
    chat_id = str(uuid4())
    now = datetime.now(timezone.utc).isoformat()
    with db_lock, get_connection() as conn:
        conn.execute("INSERT INTO chat_sessions VALUES (?, ?, ?, ?)",
                     (chat_id, "New conversation", now, now))
        conn.commit()
    return {"id": chat_id, "title": "New conversation", "created_at": now, "updated_at": now}


@app.get("/api/chats")
def list_chats():
    with db_lock, get_connection() as conn:
        rows = conn.execute("SELECT id, title, created_at, updated_at FROM chat_sessions ORDER BY updated_at DESC").fetchall()
    return {"chats": [dict(row) for row in rows]}


@app.get("/api/chats/{chat_id}")
def get_chat(chat_id: str):
    with db_lock, get_connection() as conn:
        session = conn.execute("SELECT id, title, created_at, updated_at FROM chat_sessions WHERE id = ?", (chat_id,)).fetchone()
        if session is None:
            raise HTTPException(status_code=404, detail="Conversation not found.")
        rows = conn.execute("SELECT role, content, created_at FROM chat_messages WHERE chat_id = ? ORDER BY id", (chat_id,)).fetchall()
    return {"chat": dict(session), "messages": [dict(row) for row in rows]}


@app.delete("/api/chats/{chat_id}")
def delete_chat(chat_id: str):
    with db_lock, get_connection() as conn:
        cursor = conn.execute("DELETE FROM chat_sessions WHERE id = ?", (chat_id,))
        conn.commit()
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Conversation not found.")
    return {"deleted": True, "id": chat_id}


@app.post("/api/chat")
def chat(request: ChatRequest):
    """PDF-first agent with conditional DuckDuckGo search and conversation context."""
    question = request.question.strip()
    if len(question) < 2:
        raise HTTPException(status_code=400, detail="Enter a longer question.")
    chat_id = request.chat_id
    if not chat_id:
        chat_id = create_chat()["id"]

    with db_lock, get_connection() as conn:
        session = conn.execute("SELECT id FROM chat_sessions WHERE id = ?", (chat_id,)).fetchone()
        if session is None:
            raise HTTPException(status_code=404, detail="Conversation not found.")
        stored_history = conn.execute(
            "SELECT role, content FROM chat_messages WHERE chat_id = ? ORDER BY id", (chat_id,)
        ).fetchall()

    history = [dict(row) for row in stored_history]
    if not history:
        history = [item.dict() for item in request.history]

    try:
        result = answer_question(
            question=question,
            history=history[-12:],
            retrieve=retrieve_chunks,
            top_k=max(request.top_k, 8),
            retrieve_overview=retrieve_overview_chunks,
        )

        now = datetime.now(timezone.utc).isoformat()
        title = question[:60] + ("…" if len(question) > 60 else "")
        with db_lock, get_connection() as conn:
            conn.execute(
                "INSERT INTO chat_messages (chat_id, role, content, created_at) VALUES (?, ?, ?, ?)",
                (chat_id, "user", question, now),
            )
            conn.execute(
                "INSERT INTO chat_messages (chat_id, role, content, created_at) VALUES (?, ?, ?, ?)",
                (chat_id, "assistant", result["answer"], now),
            )
            conn.execute(
                "UPDATE chat_sessions SET title = CASE WHEN title = 'New conversation' THEN ? ELSE title END, updated_at = ? WHERE id = ?",
                (title, now, chat_id),
            )
            conn.commit()

        document_sources = []
        seen = set()
        for item in result["document_chunks"]:
            key = (item["document_id"], item["page_number"])
            if key not in seen:
                seen.add(key)
                document_sources.append({
                    "document_id": item["document_id"],
                    "filename": item["filename"],
                    "page_number": item["page_number"],
                    "score": item.get("score", 0),
                })
        return {
            "chat_id": chat_id,
            "answer": result["answer"],
            "mode": result["mode"],
            "document_sources": document_sources,
            "web_sources": result["web_sources"],
            "used_web_search": result["used_web_search"],
            "retrieved_count": len(result["document_chunks"]),
            "suggested_questions": result["suggested_questions"],
        }
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"AI response generation failed: {exc}") from exc


@app.get("/api/search/status")
def search_status():
    with vector_lock:
        return {
            "indexed_chunks": int(faiss_index.ntotal) if faiss_index is not None else 0,
            "model": MODEL_NAME,
            "index_exists": INDEX_PATH.exists(),
        }
