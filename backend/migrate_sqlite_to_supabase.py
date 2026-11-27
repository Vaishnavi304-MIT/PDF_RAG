"""
Migrate the local SQLite workspace using ONLY Supabase's public anon/publishable
key and a real Supabase Auth user's email/password session.

Required environment variables:
SUPABASE_URL, SUPABASE_ANON_KEY, SUPABASE_EMAIL, SUPABASE_PASSWORD

This script signs in with Supabase Auth and uses the resulting user's JWT for
PostgREST and Storage. RLS is enforced. Local files are never deleted.
"""
from __future__ import annotations
import os
import sqlite3
from pathlib import Path
from dotenv import load_dotenv
from sentence_transformers import SentenceTransformer
from supabase import create_client

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("DATA_DIR", str(BASE_DIR / "data"))).resolve()
DB_PATH = DATA_DIR / "intellidoc.db"
UPLOAD_DIR = DATA_DIR / "uploads"
BUCKET = os.getenv("SUPABASE_STORAGE_BUCKET", "intellidoc-documents")
MODEL_NAME = os.getenv("EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
BATCH_SIZE = 40


def main():
    load_dotenv(BASE_DIR / ".env")
    load_dotenv(BASE_DIR.parent / ".env", override=False)
    url = os.getenv("SUPABASE_URL", "").strip()
    anon_key = (os.getenv("SUPABASE_ANON_KEY", "").strip()
                or os.getenv("SUPABASE_PUBLISHABLE_KEY", "").strip())
    email = os.getenv("SUPABASE_EMAIL", "").strip()
    password = os.getenv("SUPABASE_PASSWORD", "")
    if not all([url, anon_key, email, password]):
        raise SystemExit(
            "Set SUPABASE_URL, SUPABASE_ANON_KEY, SUPABASE_EMAIL and SUPABASE_PASSWORD."
        )
    if not DB_PATH.exists():
        raise SystemExit(f"SQLite database not found: {DB_PATH}")

    public_client = create_client(url, anon_key)
    auth = public_client.auth.sign_in_with_password({"email": email, "password": password})
    session = getattr(auth, "session", None)
    user = getattr(auth, "user", None)
    if not session or not user:
        raise SystemExit("Supabase Auth did not return a session. Check email/password and email confirmation.")
    owner_id = str(user.id)
    client = public_client.with_token(session.access_token)

    print(f"Authenticated as Supabase user: {owner_id}")
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    docs = conn.execute("SELECT * FROM documents ORDER BY uploaded_at").fetchall()
    chats = conn.execute("SELECT * FROM chat_sessions ORDER BY created_at").fetchall()
    messages = conn.execute("SELECT * FROM chat_messages ORDER BY id").fetchall()
    print(f"Found {len(docs)} documents, {len(chats)} chat sessions, {len(messages)} chat messages.")

    model = None

    for doc in docs:
        doc_id = str(doc["id"])
        local_pdf = UPLOAD_DIR / doc["stored_filename"]
        storage_path = None
        if local_pdf.exists():
            storage_path = f"{owner_id}/{doc_id}/{doc['stored_filename']}"
            try:
                client.storage.from_(BUCKET).remove([storage_path])
            except Exception:
                pass
            client.storage.from_(BUCKET).upload(
                storage_path, local_pdf.read_bytes(),
                file_options={"content-type": "application/pdf", "upsert": "true", "cache-control": "3600"},
            )
        else:
            print(f"WARNING: PDF missing locally for {doc['filename']}; migrating metadata/chunks only.")

        client.table("documents").upsert({
            "id": doc_id, "owner_id": owner_id, "filename": doc["filename"],
            "storage_path": storage_path, "file_size": doc["file_size"],
            "page_count": doc["page_count"], "extracted_chars": doc["extracted_chars"],
            "uploaded_at": doc["uploaded_at"],
        }).execute()

        old_chunks = conn.execute(
            "SELECT page_number, chunk_index, content FROM chunks WHERE document_id=? ORDER BY page_number, chunk_index",
            (doc["id"],),
        ).fetchall()
        client.table("document_chunks").delete().eq("document_id", doc_id).eq("owner_id", owner_id).execute()

        for start in range(0, len(old_chunks), BATCH_SIZE):
            if model is None:
                model = SentenceTransformer(MODEL_NAME, device="cpu")
            batch = old_chunks[start:start + BATCH_SIZE]
            vectors = model.encode(
                [row["content"] for row in batch], convert_to_numpy=True,
                normalize_embeddings=True, show_progress_bar=False,
            ).tolist()
            payload = [{
                "owner_id": owner_id, "document_id": doc_id,
                "page_number": row["page_number"], "chunk_index": row["chunk_index"],
                "content": row["content"], "embedding": vector,
            } for row, vector in zip(batch, vectors)]
            if payload:
                client.table("document_chunks").insert(payload).execute()
        print(f"Migrated {doc['filename']} with {len(old_chunks)} chunks.")

    for chat in chats:
        client.table("chat_sessions").upsert({
            "id": str(chat["id"]), "owner_id": owner_id, "title": chat["title"],
            "created_at": chat["created_at"], "updated_at": chat["updated_at"],
        }).execute()

    chat_ids = [str(chat["id"]) for chat in chats]
    for chat_id in chat_ids:
        client.table("chat_messages").delete().eq("chat_id", chat_id).eq("owner_id", owner_id).execute()

    payload = [{
        "chat_id": str(msg["chat_id"]), "owner_id": owner_id,
        "role": msg["role"], "content": msg["content"], "created_at": msg["created_at"],
    } for msg in messages]
    for start in range(0, len(payload), 100):
        client.table("chat_messages").insert(payload[start:start + 100]).execute()

    conn.close()
    print("Migration completed with anon key + authenticated JWT. Local SQLite/uploads were not changed.")


if __name__ == "__main__":
    main()
