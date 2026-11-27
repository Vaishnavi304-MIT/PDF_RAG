
"""
Migrate the local SQLite workspace to Supabase using ONLY a
Supabase publishable/anon key and a real Supabase Auth user session.

Required environment variables:
    SUPABASE_URL
    SUPABASE_ANON_KEY (or SUPABASE_PUBLISHABLE_KEY)
    SUPABASE_EMAIL
    SUPABASE_PASSWORD

Optional:
    DATA_DIR
    SUPABASE_STORAGE_BUCKET
    EMBEDDING_MODEL

RLS is enforced using the authenticated user's JWT.
Local SQLite files and uploaded PDFs are never deleted.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from dotenv import load_dotenv
from fastembed import TextEmbedding
from supabase import create_client


# ---------------------------------------------------------
# 1. Load environment variables BEFORE reading configuration
# ---------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent

load_dotenv(BASE_DIR / ".env")
load_dotenv(BASE_DIR.parent / ".env", override=False)

DATA_DIR = Path(
    os.getenv("DATA_DIR", str(BASE_DIR / "data"))
).resolve()

DB_PATH = DATA_DIR / "intellidoc.db"
UPLOAD_DIR = DATA_DIR / "uploads"

BUCKET = os.getenv(
    "SUPABASE_STORAGE_BUCKET",
    "intellidoc-documents"
)

# IMPORTANT:
# This must be the same embedding model used by your application.
MODEL_NAME = os.getenv(
    "EMBEDDING_MODEL",
    "BAAI/bge-small-en-v1.5"
)

BATCH_SIZE = 40
MESSAGE_BATCH_SIZE = 100


# ---------------------------------------------------------
# 2. Helper functions
# ---------------------------------------------------------

def get_setting(name: str, fallback: str = "") -> str:
    return os.getenv(name, fallback).strip()


def get_local_pdf(stored_filename: str) -> Path:
    """Prevent stored filenames from escaping the uploads directory."""

    upload_root = UPLOAD_DIR.resolve()
    pdf_path = (upload_root / stored_filename).resolve()

    if pdf_path.parent != upload_root:
        raise ValueError(
            f"Invalid stored filename: {stored_filename}"
        )

    return pdf_path


def rows_to_dicts(rows):
    return [dict(row) for row in rows]


def get_embedding_model():
    print(f"Loading embedding model: {MODEL_NAME}")
    return TextEmbedding(model_name=MODEL_NAME)


# ---------------------------------------------------------
# 3. Authenticate with Supabase
# ---------------------------------------------------------

def create_authenticated_client():
    url = get_setting("SUPABASE_URL")

    publishable_key = (
        get_setting("SUPABASE_ANON_KEY")
        or get_setting("SUPABASE_PUBLISHABLE_KEY")
    )

    email = get_setting("SUPABASE_EMAIL")
    password = os.getenv("SUPABASE_PASSWORD", "")

    if not all([url, publishable_key, email, password]):
        raise SystemExit(
            "Missing configuration. Set:\n"
            "SUPABASE_URL\n"
            "SUPABASE_ANON_KEY or SUPABASE_PUBLISHABLE_KEY\n"
            "SUPABASE_EMAIL\n"
            "SUPABASE_PASSWORD"
        )

    # Public client: no service-role or secret key.
    public_client = create_client(url, publishable_key)

    try:
        auth = public_client.auth.sign_in_with_password({
            "email": email,
            "password": password,
        })
    except Exception as exc:
        raise SystemExit(
            f"Supabase authentication failed: {exc}"
        ) from exc

    session = getattr(auth, "session", None)
    user = getattr(auth, "user", None)

    if not session or not user:
        raise SystemExit(
            "Supabase did not return an authenticated session. "
            "Check credentials and email confirmation."
        )

    access_token = session.access_token
    owner_id = str(user.id)

    # Create a client that uses the user's JWT for
    # PostgREST and Storage requests.
    authenticated_client = public_client.with_token(access_token)

    print(f"Authenticated Supabase user: {owner_id}")

    return authenticated_client, owner_id


# ---------------------------------------------------------
# 4. Migrate PDF documents and chunks
# ---------------------------------------------------------

def migrate_documents(client, owner_id, conn):
    documents = rows_to_dicts(
        conn.execute(
            "SELECT * FROM documents ORDER BY uploaded_at"
        ).fetchall()
    )

    print(f"Found {len(documents)} local documents.")

    model = None

    for doc in documents:
        doc_id = str(doc["id"])
        filename = doc["filename"]
        stored_filename = doc["stored_filename"]

        local_pdf = get_local_pdf(stored_filename)
        storage_path = None

        # Upload the original PDF to private Supabase Storage.
        if local_pdf.is_file():
            storage_path = (
                f"{owner_id}/{doc_id}/{local_pdf.name}"
            )

            try:
                client.storage.from_(BUCKET).upload(
                    storage_path,
                    local_pdf.read_bytes(),
                    file_options={
                        "content-type": "application/pdf",
                        "upsert": "true",
                        "cache-control": "3600",
                    },
                )
            except Exception as exc:
                raise RuntimeError(
                    f"PDF upload failed for {filename}: {exc}"
                ) from exc

        else:
            print(
                f"WARNING: Local PDF missing: {filename}. "
                "Migrating metadata and chunks only."
            )

        # Insert or update document metadata.
        document_payload = {
            "id": doc_id,
            "owner_id": owner_id,
            "filename": filename,
            "storage_path": storage_path,
            "file_size": doc["file_size"],
            "page_count": doc["page_count"],
            "extracted_chars": doc["extracted_chars"],
            "uploaded_at": doc["uploaded_at"],
        }

        client.table("documents").upsert(
            document_payload
        ).execute()

        # Read existing local chunks.
        old_chunks = rows_to_dicts(
            conn.execute(
                """
                SELECT page_number, chunk_index, content
                FROM chunks
                WHERE document_id = ?
                ORDER BY page_number, chunk_index
                """,
                (doc["id"],),
            ).fetchall()
        )

        # Delete previously migrated chunks for this document.
        client.table("document_chunks") \
            .delete() \
            .eq("document_id", doc_id) \
            .eq("owner_id", owner_id) \
            .execute()

        # Generate and insert embeddings in batches.
        for start in range(0, len(old_chunks), BATCH_SIZE):
            batch = old_chunks[start:start + BATCH_SIZE]

            texts = [
                str(row["content"])
                for row in batch
            ]

            if not texts:
                continue

            if model is None:
                model = get_embedding_model()

            # FastEmbed returns a generator of NumPy vectors.
            embeddings = list(
                model.embed(
                    texts,
                    batch_size=BATCH_SIZE,
                )
            )

            if len(embeddings) != len(batch):
                raise RuntimeError(
                    f"Embedding count mismatch for {filename}."
                )

            payload = []

            for row, vector in zip(batch, embeddings):
                payload.append({
                    "owner_id": owner_id,
                    "document_id": doc_id,
                    "page_number": row["page_number"],
                    "chunk_index": row["chunk_index"],
                    "content": row["content"],
                    "embedding": vector.tolist(),
                })

            client.table("document_chunks") \
                .insert(payload) \
                .execute()

        print(
            f"Migrated {filename}: {len(old_chunks)} chunks."
        )


# ---------------------------------------------------------
# 5. Migrate chat sessions
# ---------------------------------------------------------

def migrate_chat_sessions(client, owner_id, conn):
    chats = rows_to_dicts(
        conn.execute(
            """
            SELECT * FROM chat_sessions
            ORDER BY created_at
            """
        ).fetchall()
    )

    print(f"Found {len(chats)} chat sessions.")

    for chat in chats:
        payload = {
            "id": str(chat["id"]),
            "owner_id": owner_id,
            "title": chat["title"],
            "created_at": chat["created_at"],
            "updated_at": chat["updated_at"],
        }

        client.table("chat_sessions").upsert(
            payload
        ).execute()

    return chats


# ---------------------------------------------------------
# 6. Migrate chat messages
# ---------------------------------------------------------

def migrate_chat_messages(client, owner_id, conn, chats):
    messages = rows_to_dicts(
        conn.execute(
            """
            SELECT * FROM chat_messages
            ORDER BY id
            """
        ).fetchall()
    )

    print(f"Found {len(messages)} chat messages.")

    chat_ids = [
        str(chat["id"])
        for chat in chats
    ]

    # Remove old migrated messages to avoid duplicates
    # when rerunning the migration.
    for chat_id in chat_ids:
        client.table("chat_messages") \
            .delete() \
            .eq("chat_id", chat_id) \
            .eq("owner_id", owner_id) \
            .execute()

    payload = []

    for msg in messages:
        payload.append({
            "chat_id": str(msg["chat_id"]),
            "owner_id": owner_id,
            "role": msg["role"],
            "content": msg["content"],
            "created_at": msg["created_at"],
        })

    for start in range(0, len(payload), MESSAGE_BATCH_SIZE):
        batch = payload[start:start + MESSAGE_BATCH_SIZE]

        if batch:
            client.table("chat_messages") \
                .insert(batch) \
                .execute()

    print(f"Migrated {len(messages)} chat messages.")


# ---------------------------------------------------------
# 7. Main migration
# ---------------------------------------------------------

def main():
    if not DB_PATH.is_file():
        raise SystemExit(
            f"SQLite database not found: {DB_PATH}"
        )

    if not UPLOAD_DIR.exists():
        print(
            f"WARNING: Upload directory does not exist: {UPLOAD_DIR}"
        )

    client, owner_id = create_authenticated_client()

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    try:
        print("\nStarting IntelliDoc AI migration...\n")

        migrate_documents(client, owner_id, conn)

        chats = migrate_chat_sessions(
            client,
            owner_id,
            conn,
        )

        migrate_chat_messages(
            client,
            owner_id,
            conn,
            chats,
        )

        print("\nMigration completed successfully.")
        print(f"Supabase user ID: {owner_id}")
        print("SQLite database was not modified.")
        print("Local PDF files were not deleted.")

    except Exception as exc:
        print("\nMigration failed.")
        print(f"Error: {exc}")
        raise

    finally:
        conn.close()


if __name__ == "__main__":
    main()
