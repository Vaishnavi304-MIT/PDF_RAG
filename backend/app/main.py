from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from uuid import uuid4
import os
import re
import json
import logging
from urllib.parse import urlencode
from urllib.request import Request as URLRequest, urlopen
from urllib.error import URLError, HTTPError
from typing import Any

from aiohttp import request
import pymupdf as fitz
import numpy as np
from fastembed import TextEmbedding
from fastapi import FastAPI, UploadFile, File, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, Field
from supabase import Client, create_client
from storage3 import SyncStorageClient
from dotenv import load_dotenv

from app.services.agent import answer_question
from fastapi.responses import JSONResponse
from app.services.rag import GroqQuotaExceeded

# Load local development environment files; container/cloud providers inject env vars directly.
_BACKEND_DIR = Path(__file__).resolve().parents[1]
load_dotenv(_BACKEND_DIR / ".env")
load_dotenv(_BACKEND_DIR.parent / ".env", override=False)

MAX_FILE_SIZE = 50 * 1024 * 1024
CHUNK_SIZE = 1000
CHUNK_OVERLAP = 150
MODEL_NAME = os.getenv("EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5")
EMBEDDING_DIMENSION = 384
TOP_K_DEFAULT = 5
STORAGE_BUCKET = os.getenv("SUPABASE_STORAGE_BUCKET", "intellidoc-documents")

app = FastAPI(title="IntelliDoc AI API", version="1.1.0")
_default_origins = [
    "http://localhost:5173", "http://127.0.0.1:5173",
    "http://localhost:8080", "http://127.0.0.1:8080",
]
_allowed_origins = [
    origin.strip()
    for origin in os.getenv("CORS_ORIGINS", ",".join(_default_origins)).split(",")
    if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

model = None
model_lock = RLock()
anon_client: Client | None = None
client_lock = RLock()
bearer = HTTPBearer(auto_error=False)

logger = logging.getLogger(__name__)



@app.exception_handler(GroqQuotaExceeded)
async def groq_quota_handler(request, exc):
    return JSONResponse(
        status_code=429,
        content={
            "code": "GROQ_QUOTA_EXCEEDED",
            "detail": (
                "Sorry! IntelliDoc AI has temporarily reached "
                "its AI service usage limit. Your documents and "
                "chat history are safe. Please try again when "
                "the API quota resets."
            ),
            "retry_after": exc.retry_after,
        },
    )

@dataclass
class AuthContext:
    user_id: str
    client: Client
    storage: SyncStorageClient


def get_anon_client() -> Client:
    """Create a Supabase client with the public anon/publishable key only."""
    global anon_client
    if anon_client is None:
        with client_lock:
            if anon_client is None:
                url = os.getenv("SUPABASE_URL", "").strip()
                key = (
                    os.getenv("SUPABASE_ANON_KEY", "").strip()
                    or os.getenv("SUPABASE_PUBLISHABLE_KEY", "").strip()
                )
                if not url or not key:
                    raise RuntimeError("SUPABASE_URL and SUPABASE_ANON_KEY must be configured.")
                anon_client = create_client(url, key)
    return anon_client


def require_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> AuthContext:
    """Validate a Supabase JWT and create user-scoped database and Storage clients.

    Compatible with supabase==2.34.1 and storage3==2.34.1. The public
    anon/publishable key is used as the API key; the user's access token is
    sent as the Authorization bearer token so Supabase RLS remains active.
    """
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=401, detail="Sign in to continue.")

    token = credentials.credentials.strip()
    if not token:
        raise HTTPException(status_code=401, detail="Missing Supabase access token.")

    url = os.getenv("SUPABASE_URL", "").strip()
    key = (
        os.getenv("SUPABASE_ANON_KEY", "").strip()
        or os.getenv("SUPABASE_PUBLISHABLE_KEY", "").strip()
    )
    if not url or not key:
        logger.error("Supabase URL or anon/publishable key is not configured.")
        raise HTTPException(
            status_code=503,
            detail="Backend Supabase configuration is incomplete.",
        )

    # Create a new client for this request. Never mutate a shared client's
    # Authorization header because requests from different users can overlap.
    try:
        user_client = create_client(url, key)
    except Exception as exc:
        logger.exception("Could not initialize a request-scoped Supabase client.")
        raise HTTPException(
            status_code=503,
            detail="Could not initialize the Supabase connection.",
        ) from exc

    # Validate the bearer token with Supabase Auth.
    try:
        verified = user_client.auth.get_user(token)
        user = getattr(verified, "user", None)
        if user is None or not getattr(user, "id", None):
            raise ValueError("Supabase Auth returned no user.")
    except Exception as exc:
        logger.exception(
            "Supabase Auth rejected the bearer token. Check that frontend and "
            "backend use the same Supabase project and that the browser sends "
            "the current access_token."
        )
        raise HTTPException(
            status_code=401,
            detail="Supabase did not accept this access token. Refresh your session and try again.",
        ) from exc

    try:
    
        user_client.postgrest.auth(token)

        storage_client = SyncStorageClient(
            f"{url.rstrip('/')}/storage/v1",
            {
                "apiKey": key,
                "Authorization": f"Bearer {token}",
            },
        )
    except Exception as exc:
        logger.exception("Could not configure authenticated Supabase access.")
        raise HTTPException(
            status_code=500,
            detail="Could not configure authenticated Supabase access.",
        ) from exc

    return AuthContext(
        user_id=str(user.id),
        client=user_client,
        storage=storage_client,
    )


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
        with model_lock:
            if model is None:
                model = TextEmbedding(MODEL_NAME, device=os.getenv("EMBEDDING_DEVICE", "cpu"))
    return model


def encode_texts(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []

    # Generate embeddings using FastEmbed
    vectors = list(get_model().embed(texts))

    vectors = np.asarray(vectors, dtype="float32")

    if vectors.ndim != 2:
        raise RuntimeError(
            f"Invalid embedding shape: {vectors.shape}"
        )

    if vectors.shape[1] != EMBEDDING_DIMENSION:
        raise RuntimeError(
            f"Expected {EMBEDDING_DIMENSION}-dimensional "
            f"embeddings; received {vectors.shape}"
        )

    # Normalize embeddings for cosine similarity
    norms = np.linalg.norm(
        vectors, axis=1, keepdims=True
    )

    vectors = vectors / np.maximum(norms, 1e-12)

    return vectors.tolist()


def rows(response: Any) -> list[dict]:
    return response.data or []


def document_to_dict(row: dict) -> dict:
    return {
        "id": row["id"], "filename": row["filename"],
        "file_size": row["file_size"], "page_count": row["page_count"],
        "extracted_chars": row["extracted_chars"], "uploaded_at": row["uploaded_at"],
    }


def insert_chunks(ctx: AuthContext, document_id: str, chunks: list[tuple[int, int, str]]):
    for start in range(0, len(chunks), 40):
        batch = chunks[start:start + 40]
        vectors = encode_texts([item[2] for item in batch])
        payload = [
            {
                "owner_id": ctx.user_id,
                "document_id": document_id,
                "page_number": page,
                "chunk_index": index,
                "content": content,
                "embedding": vector,
            }
            for (page, index, content), vector in zip(batch, vectors)
        ]
        if payload:
            ctx.client.table("document_chunks").insert(payload).execute()


class SearchRequest(BaseModel):
    query: str = Field(min_length=2, max_length=2000)
    top_k: int = Field(default=TOP_K_DEFAULT, ge=1, le=10)


class TurnstileVerifyRequest(BaseModel):
    token: str = Field(min_length=1, max_length=2048)


class ChatMessage(BaseModel):
    role: str
    content: str = Field(max_length=6000)


class ChatRequest(BaseModel):
    question: str = Field(min_length=2, max_length=2000)
    chat_id: str | None = None
    history: list[ChatMessage] = Field(default_factory=list)
    top_k: int = Field(default=5, ge=1, le=10)
    edit_from_index: int | None = Field(default=None, ge=0)

@app.get("/")
def root():
    return {"message": "Welcome to IntelliDoc AI API", "docs": "/docs", "health": "/health"}


@app.get("/health")
def health():
    """Public liveness check. Protected endpoints validate each user's JWT."""
    url = os.getenv("SUPABASE_URL", "").strip()
    key = (
        os.getenv("SUPABASE_ANON_KEY", "").strip()
        or os.getenv("SUPABASE_PUBLISHABLE_KEY", "").strip()
    )
    return {
        "status": "ok",
        "service": "IntelliDoc AI API",
        "supabase_configured": bool(url and key),
    }


@app.post("/api/auth/verify-captcha")
def verify_login_captcha(payload: TurnstileVerifyRequest):
    """
    Verify a one-time Cloudflare Turnstile token for email/password login only.
    Supabase's global Auth CAPTCHA setting must be OFF for this split policy;
    the frontend calls this endpoint before signInWithPassword.
    """
    secret = os.getenv("TURNSTILE_SECRET_KEY", "").strip()
    if not secret:
        raise HTTPException(
            status_code=503,
            detail="Login CAPTCHA is not configured on the backend.",
        )

    form_data = urlencode({
        "secret": secret,
        "response": payload.token,
    }).encode("utf-8")
    request = URLRequest(
        "https://challenges.cloudflare.com/turnstile/v0/siteverify",
        data=form_data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )

    try:
        with urlopen(request, timeout=8) as response:
            result = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, ValueError) as exc:
        raise HTTPException(
            status_code=502,
            detail="Could not verify the Cloudflare challenge. Please try again.",
        ) from exc

    if not result.get("success"):
        # Do not expose provider internals or secrets to the browser.
        raise HTTPException(
            status_code=400,
            detail="Cloudflare verification failed or expired. Complete the check again.",
        )

    allowed_hostnames = {
        item.strip().lower()
        for item in os.getenv("TURNSTILE_ALLOWED_HOSTNAMES", "").split(",")
        if item.strip()
    }
    hostname = str(result.get("hostname", "")).lower()
    if allowed_hostnames and hostname not in allowed_hostnames:
        raise HTTPException(status_code=400, detail="CAPTCHA hostname is not allowed.")

    return {"verified": True}


@app.get("/api/me")
def me(ctx: AuthContext = Depends(require_user)):
    return {"user_id": ctx.user_id}


@app.post("/api/documents/upload")
async def upload_document(file: UploadFile = File(...), ctx: AuthContext = Depends(require_user)):
    original_name = Path(file.filename or "document.pdf").name
    if not original_name.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported.")
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="The uploaded file is empty.")
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(status_code=413, detail="PDF exceeds the 50 MB upload limit.")
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

    extracted = "\n".join(text for _, text in page_texts)
    if not extracted.strip():
        raise HTTPException(status_code=422, detail="No selectable text was found. OCR is not included yet.")
    chunks = [
        (page, index, chunk)
        for page, page_text in page_texts
        for index, chunk in enumerate(split_text(page_text))
    ]
    if not chunks:
        raise HTTPException(status_code=422, detail="No text chunks could be created from this PDF.")

    document_id = str(uuid4())
    storage_path = f"{ctx.user_id}/{document_id}/{document_id}.pdf"
    uploaded = False
    inserted = False
    now = datetime.now(timezone.utc).isoformat()
    try:
        ctx.storage.from_(STORAGE_BUCKET).upload(
            storage_path, content,
            file_options={"content-type": "application/pdf", "upsert": "false", "cache-control": "3600"},
        )
        uploaded = True
        ctx.client.table("documents").insert({
            "id": document_id, "owner_id": ctx.user_id,
            "filename": original_name, "storage_path": storage_path,
            "file_size": len(content), "page_count": page_count,
            "extracted_chars": len(extracted), "uploaded_at": now,
        }).execute()
        inserted = True
        insert_chunks(ctx, document_id, chunks)
    except Exception as exc:
        try:
            if inserted:
                ctx.client.table("documents").delete().eq("id", document_id).execute()
            if uploaded:
                ctx.storage.from_(STORAGE_BUCKET).remove([storage_path])
        except Exception:
            pass
        raise HTTPException(status_code=500, detail=f"PDF upload or indexing failed: {exc}") from exc

    return {
        "message": "PDF uploaded to Supabase, processed and indexed successfully.",
        "document": {
            "id": document_id, "filename": original_name, "file_size": len(content),
            "page_count": page_count, "extracted_chars": len(extracted),
            "chunk_count": len(chunks), "uploaded_at": now,
        },
    }


@app.get("/api/documents")
def list_documents(ctx: AuthContext = Depends(require_user)):
    result = ctx.client.table("documents").select(
        "id,filename,file_size,page_count,extracted_chars,uploaded_at"
    ).eq("owner_id", ctx.user_id).order("uploaded_at", desc=True).execute()
    return {"documents": [document_to_dict(row) for row in rows(result)]}


@app.get("/api/documents/{document_id}")
def get_document(document_id: str, ctx: AuthContext = Depends(require_user)):
    docs = rows(ctx.client.table("documents").select(
        "id,filename,file_size,page_count,extracted_chars,uploaded_at"
    ).eq("id", document_id).eq("owner_id", ctx.user_id).limit(1).execute())
    if not docs:
        raise HTTPException(status_code=404, detail="Document not found.")
    chunks = rows(ctx.client.table("document_chunks").select(
        "page_number,chunk_index,content"
    ).eq("document_id", document_id).eq("owner_id", ctx.user_id).order("page_number").order("chunk_index").execute())
    return {"document": document_to_dict(docs[0]), "chunks": chunks}


@app.delete("/api/documents/{document_id}")
def delete_document(document_id: str, ctx: AuthContext = Depends(require_user)):
    docs = rows(ctx.client.table("documents").select(
        "id,storage_path"
    ).eq("id", document_id).eq("owner_id", ctx.user_id).limit(1).execute())
    if not docs:
        raise HTTPException(status_code=404, detail="Document not found.")
    storage_path = docs[0].get("storage_path")
    ctx.client.table("documents").delete().eq("id", document_id).eq("owner_id", ctx.user_id).execute()
    if storage_path:
        try:
            ctx.storage.from_(STORAGE_BUCKET).remove([storage_path])
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Metadata deleted but PDF cleanup failed: {exc}") from exc
    return {"message": "Document deleted successfully.", "document_id": document_id}


def retrieve_chunks(query: str, top_k: int, ctx: AuthContext):
    vector = encode_texts([query])[0]

    # Retrieve relevant chunks independently from every PDF.
    per_document_k = max(1, min(int(top_k), 8))

    result = ctx.client.rpc(
        "match_document_chunks_per_document",
        {
            "query_embedding": vector,
            "match_count_per_document": per_document_k,
        },
    ).execute()

    return [
        {
            "document_id": item["document_id"],
            "filename": item["filename"],
            "page_number": item["page_number"],
            "chunk_index": item["chunk_index"],
            "content": item["content"],
            "score": round(float(item["similarity"]), 4),
        }
        for item in rows(result)
    ]



def retrieve_overview_chunks(ctx: AuthContext, limit: int = 4):
    # Get every PDF belonging to the authenticated user.
    docs = rows(
        ctx.client.table("documents")
        .select("id,filename")
        .eq("owner_id", ctx.user_id)
        .order("uploaded_at", desc=True)
        .execute()
    )

    result = []

    # Retrieve the opening chunks of every document.
    for doc in docs:
        chunks = rows(
            ctx.client.table("document_chunks")
            .select("document_id,page_number,chunk_index,content")
            .eq("document_id", doc["id"])
            .eq("owner_id", ctx.user_id)
            .order("page_number")
            .order("chunk_index")
            .limit(limit)
            .execute()
        )

        for item in chunks:
            result.append({
                "document_id": item["document_id"],
                "filename": doc["filename"],
                "page_number": item["page_number"],
                "chunk_index": item["chunk_index"],
                "content": item["content"],
                "score": 1.0,
            })

    return result



@app.post("/api/search")
def semantic_search(request: SearchRequest, ctx: AuthContext = Depends(require_user)):
    results = retrieve_chunks(request.query.strip(), request.top_k, ctx)
    message = f"Found {len(results)} relevant passage(s)." if results else "No indexed PDF chunks are available yet."
    return {"query": request.query.strip(), "results": results, "message": message}


@app.post("/api/chats")
def create_chat(ctx: AuthContext = Depends(require_user)):
    chat_id = str(uuid4())
    now = datetime.now(timezone.utc).isoformat()
    record = {
        "id": chat_id, "owner_id": ctx.user_id, "title": "New conversation",
        "created_at": now, "updated_at": now,
    }
    ctx.client.table("chat_sessions").insert(record).execute()
    return {k: record[k] for k in ("id", "title", "created_at", "updated_at")}


@app.get("/api/chats")
def list_chats(ctx: AuthContext = Depends(require_user)):
    result = ctx.client.table("chat_sessions").select(
        "id,title,created_at,updated_at"
    ).eq("owner_id", ctx.user_id).order("updated_at", desc=True).execute()
    return {"chats": rows(result)}


@app.get("/api/chats/{chat_id}")
def get_chat(chat_id: str, ctx: AuthContext = Depends(require_user)):
    sessions = rows(ctx.client.table("chat_sessions").select(
        "id,title,created_at,updated_at"
    ).eq("id", chat_id).eq("owner_id", ctx.user_id).limit(1).execute())
    if not sessions:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    messages = rows(ctx.client.table("chat_messages").select(
        "role,content,created_at"
    ).eq("chat_id", chat_id).eq("owner_id", ctx.user_id).order("id").execute())
    return {"chat": sessions[0], "messages": messages}


@app.delete("/api/chats/{chat_id}")
def delete_chat(chat_id: str, ctx: AuthContext = Depends(require_user)):
    result = ctx.client.table("chat_sessions").delete().eq(
        "id", chat_id
    ).eq("owner_id", ctx.user_id).execute()
    if not rows(result):
        raise HTTPException(status_code=404, detail="Conversation not found.")
    return {"deleted": True, "id": chat_id}


@app.post("/api/chat")
def chat(request: ChatRequest, ctx: AuthContext = Depends(require_user)):
    question = request.question.strip()
    if len(question) < 2:
        raise HTTPException(status_code=400, detail="Enter a longer question.")
    chat_id = request.chat_id
    if not chat_id:
        chat_id = create_chat(ctx)["id"]

    sessions = rows(ctx.client.table("chat_sessions").select("id,title").eq(
        "id", chat_id
    ).eq("owner_id", ctx.user_id).limit(1).execute())
    if not sessions:
        raise HTTPException(status_code=404, detail="Conversation not found.")

    stored = rows(
        ctx.client.table("chat_messages")
        .select("id,role,content")
        .eq("chat_id", chat_id)
        .eq("owner_id", ctx.user_id)
        .order("id")
        .execute()
    )

    messages_to_replace = []

    if request.edit_from_index is not None:

        edit_index = request.edit_from_index

        if edit_index >= len(stored):
            raise HTTPException(
                status_code=400,
                detail="The message being edited no longer exists."
            )

        if stored[edit_index]["role"] != "user":
            raise HTTPException(
                status_code=400,
                detail="You can only edit your own questions."
            )

        # Keep messages before the edited question.
        history = [
            {
                "role": item["role"],
                "content": item["content"]
            }
            for item in stored[:edit_index]
        ]

        # Mark the edited question and all following messages
        # for replacement, but do not delete them yet.
        messages_to_replace = stored[edit_index:]

    else:
        history = (
            [
                {
                    "role": item["role"],
                    "content": item["content"]
                }
                for item in stored
            ]
            if stored
            else [item.model_dump() for item in request.history]
        )

    try:
        retrieve = lambda query, k: retrieve_chunks(query, k, ctx)
        overview = lambda: retrieve_overview_chunks(ctx)
        result = answer_question(
            question=question, history=history[-12:], retrieve=retrieve,
            top_k=max(request.top_k, 8), retrieve_overview=overview,
        )
        now = datetime.now(timezone.utc).isoformat()
        title = question[:60] + ("…" if len(question) > 60 else "")
                # Replace the edited message and every message after it.
        # This happens only after a new AI answer was generated.
        if messages_to_replace:

            old_ids = [
                item["id"]
                for item in messages_to_replace
            ]

            ctx.client.table("chat_messages").delete().eq(
                "chat_id", chat_id
            ).eq(
                "owner_id", ctx.user_id
            ).in_(
                "id", old_ids
            ).execute()
        ctx.client.table("chat_messages").insert([
            {"chat_id": chat_id, "owner_id": ctx.user_id, "role": "user", "content": question, "created_at": now},
            {"chat_id": chat_id, "owner_id": ctx.user_id, "role": "assistant", "content": result["answer"], "created_at": now},
        ]).execute()
        update = {"updated_at": now}
        if sessions[0]["title"] == "New conversation":
            update["title"] = title
        ctx.client.table("chat_sessions").update(update).eq(
            "id", chat_id
        ).eq("owner_id", ctx.user_id).execute()

        document_sources, seen = [], set()
        for item in result["document_chunks"]:
            key = (item["document_id"], item["page_number"])
            if key not in seen:
                seen.add(key)
                document_sources.append({
                    "document_id": item["document_id"], "filename": item["filename"],
                    "page_number": item["page_number"], "score": item.get("score", 0),
                })
        return {
            "chat_id": chat_id, "answer": result["answer"], "mode": result["mode"],
            "document_sources": document_sources, "web_sources": result["web_sources"],
            "used_web_search": result["used_web_search"],
            "retrieved_count": len(result["document_chunks"]),
            "suggested_questions": result["suggested_questions"],
        }
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"AI response generation failed: {exc}") from exc


@app.get("/api/search/status")
def search_status(ctx: AuthContext = Depends(require_user)):
    result = ctx.client.table("document_chunks").select(
        "id", count="exact"
    ).eq("owner_id", ctx.user_id).limit(1).execute()
    count = result.count or 0
    return {
        "indexed_chunks": count, "model": MODEL_NAME,
        "index_exists": count > 0, "vector_store": "supabase_pgvector",
    }
