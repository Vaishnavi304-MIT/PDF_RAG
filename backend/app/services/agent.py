from __future__ import annotations

import os
import re

from .duckduckgo import search_web
from .rag import generate_answer, generate_followups

WEB_INTENT = re.compile(
    r"\b(latest|recent|current|today|news|202[5-9]|"
    r"202[0-9]|online|web|internet|updated|new version)\b",
    re.I
)

EXTERNAL_COMPARISON = re.compile(
    r"\b(compare|comparison|versus|vs\.?|difference|"
    r"better than|alternatives|alternative)\b",
    re.I
)

EXTERNAL_TECH = re.compile(
    r"\b(python|javascript|typescript|react|node\.?js|"
    r"tensorflow|pytorch|vision transformer|vit|"
    r"efficientnet|mobilenet|llama|gpt|gemini|"
    r"machine learning|deep learning)\b",
    re.I
)

SUMMARY_INTENT = re.compile(
    r"\b(summarize|summary|overview|main points|"
    r"key findings|what is this document about)\b",
    re.I
)

def answer_question(
    question,
    history,
    retrieve,
    top_k=5,
    retrieve_overview=None
):
    history = history or []

    retrieval_query = question

    # Add context for short follow-up questions.
    if len(question.split()) <= 7 and history:
        previous = next(
            (
                m.get("content", "")
                for m in reversed(history)
                if m.get("role") == "user"
            ),
            ""
        )

        if previous:
            retrieval_query = (
                f"{previous}\nFollow-up: {question}"
            )

    # Retrieve relevant PDF passages first.
    chunks = retrieve(retrieval_query, top_k)

    threshold = float(
        os.getenv("RAG_MIN_SCORE", "0.20")
    )

    useful = [
        c for c in chunks
        if float(c.get("score", 0)) >= threshold
    ]

    # For summaries, retrieve document overview as well.
    if SUMMARY_INTENT.search(question) and retrieve_overview:
        overview = retrieve_overview()

        existing = {
            (
                c.get("document_id"),
                c.get("page_number"),
                c.get("chunk_index")
            )
            for c in useful
        }

        for item in overview:
            key = (
                item.get("document_id"),
                item.get("page_number"),
                item.get("chunk_index")
            )

            if key not in existing:
                useful.append(item)
                existing.add(key)

    # Determine whether outside information is needed.
    wants_current = bool(WEB_INTENT.search(question))

    wants_external_comparison = (
        bool(EXTERNAL_COMPARISON.search(question))
        and bool(EXTERNAL_TECH.search(question))
    )

    pdf_is_weak = not useful

    should_search_web = (
        wants_current
        or wants_external_comparison
        or pdf_is_weak
    )

    # Retrieve external information when appropriate.
    web_results = []

    if should_search_web:
        web_results = search_web(
            question,
            max_results=5
        )

    # Determine evidence mode.
    if useful and web_results:
        mode = "hybrid"
    elif useful:
        mode = "documents"
    elif web_results:
        mode = "web_and_knowledge"
    else:
        mode = "general"

    # Send both evidence sources to Groq.
    answer = generate_answer(
        question=question,
        history=history,
        document_chunks=useful,
        web_results=web_results,
        mode=mode
    )

    suggestions = generate_followups(
        question,
        answer
    )

    return {
        "answer": answer,
        "mode": mode,
        "document_chunks": useful,
        "web_sources": web_results,
        "suggested_questions": suggestions,
        "used_web_search": bool(web_results)
    }