
from __future__ import annotations

import os
import re
import logging

from .duckduckgo import search_web
from .rag import (
    generate_answer,
    generate_followups,
    plan_retrieval,
)

from datetime import date
logger = logging.getLogger(__name__)


# Detect requests that need document overviews.
SUMMARY_INTENT = re.compile(
    r"\b(summarize|summary|overview|main points|"
    r"key findings|what is this document about|"
    r"summarize all documents|summarize both)\b",
    re.I,
)


# Detect course discovery requests.
COURSE_INTENT = re.compile(
    r"\b(courses?|certifications?|speciali[sz]ation|"
    r"training programs?|training courses?|"
    r"learning programs?|bootcamps?|"
    r"beginner courses?|online courses?|"
    r"where should i start|complete beginner)\b",
    re.I,
)


# Detect requests for current information.
CURRENT_INTENT = re.compile(
    r"\b(latest|recent|current|today|"
    r"202[6-9]|upcoming|newest|updated|"
    r"currently available|enrollment|enrolment)\b",
    re.I,
)


def _get_previous_user_question(history):
    """Get the latest user question from conversation history."""

    for item in reversed(history or []):
        if item.get("role") == "user":
            content = str(item.get("content") or "").strip()

            if content:
                return content

    return ""


def _build_retrieval_query(question, history):
    """
    Add previous conversational context to short follow-up
    questions so PDF retrieval can understand references.
    """

    question = question.strip()

    if len(question.split()) > 8:
        return question

    previous = _get_previous_user_question(history)

    if previous:
        return (
            f"Previous user question: {previous}\n"
            f"Current follow-up question: {question}"
        )

    return question


def _filter_document_chunks(chunks):
    """
    Filter low-relevance PDF chunks.

    Retain the best chunk from each document if it falls
    below the threshold, so a document is not silently
    discarded when a multi-document comparison is requested.
    """

    threshold = float(
        os.getenv("RAG_MIN_SCORE", "0.20")
    )

    grouped = {}

    for chunk in chunks or []:
        document_id = chunk.get("document_id", "unknown")

        grouped.setdefault(document_id, []).append(chunk)

    useful = []

    for document_id, document_chunks in grouped.items():

        relevant = [
            chunk
            for chunk in document_chunks
            if float(chunk.get("score", 0)) >= threshold
        ]

        if relevant:
            useful.extend(relevant)
        elif document_chunks:
            # Preserve the strongest available passage
            # from this document instead of dropping it.
            best = max(
                document_chunks,
                key=lambda item: float(item.get("score", 0)),
            )
            useful.append(best)

    return useful


def _merge_chunks(existing_chunks, additional_chunks):
    """Merge chunks without duplicating the same PDF passage."""

    existing_keys = {
        (
            item.get("document_id"),
            item.get("page_number"),
            item.get("chunk_index"),
        )
        for item in existing_chunks
    }

    for item in additional_chunks or []:
        key = (
            item.get("document_id"),
            item.get("page_number"),
            item.get("chunk_index"),
        )

        if key not in existing_keys:
            existing_chunks.append(item)
            existing_keys.add(key)

    return existing_chunks


def _empty_response(answer, mode="clarification"):
    """Return the standard chat response structure."""

    return {
        "answer": answer,
        "mode": mode,
        "document_chunks": [],
        "web_sources": [],
        "suggested_questions": [],
        "used_web_search": False,
    }


def answer_question(
    question,
    history,
    retrieve,
    top_k=5,
    retrieve_overview=None,
):
    """
    Main IntelliDoc AI workflow.

    1. Understand the user's intended task.
    2. Retrieve relevant PDF evidence.
    3. Use the adaptive retrieval planner.
    4. Search the web when external information is needed.
    5. Generate an answer using PDF, web and general knowledge.
    6. Generate follow-up suggestions.
    """

    question = str(question or "").strip()
    history = history or []

    if not question:
        return _empty_response(
            "Please enter a question.",
            mode="error",
        )

    # --------------------------------------------------
    # STEP 1: Prepare conversational context
    # --------------------------------------------------

    retrieval_query = _build_retrieval_query(
        question,
        history,
    )

    # --------------------------------------------------
    # STEP 2: Retrieve relevant PDF passages
    # --------------------------------------------------

    try:
        chunks = retrieve(
            retrieval_query,
            max(int(top_k), 8),
        ) or []

    except Exception:
        logger.exception("PDF retrieval failed.")
        chunks = []

    useful = _filter_document_chunks(chunks)

    # --------------------------------------------------
    # STEP 3: Retrieve document overviews when required
    # --------------------------------------------------

    if (
        SUMMARY_INTENT.search(question)
        and retrieve_overview is not None
    ):
        try:
            overview = retrieve_overview() or []

            useful = _merge_chunks(
                useful,
                overview,
            )

        except Exception:
            logger.exception(
                "Document overview retrieval failed."
            )

    # --------------------------------------------------
    # STEP 4: Ask the adaptive planner to understand intent
    # --------------------------------------------------

    try:
        plan = plan_retrieval(
            question=question,
            history=history,
            has_pdf_evidence=bool(useful),
        )

    except Exception:
        logger.exception("Retrieval planner failed.")

        plan = {
            "intent": "general_question",
            "use_pdf": bool(useful),
            "use_web": False,
            "search_query": "",
            "needs_clarification": False,
            "clarification_question": "",
            "reason": "Planner unavailable",
        }

    intent = plan.get(
        "intent",
        "general_question",
    )

    # --------------------------------------------------
    # STEP 5: Detect course discovery requests
    # --------------------------------------------------

    is_course_request = (
        intent == "course_discovery"
        or bool(COURSE_INTENT.search(question))
    )

    if is_course_request:
        intent = "course_discovery"

        # Course discovery requires external listings.
        plan["use_web"] = True

        # The planner must identify the subject from
        # the current question or previous conversation.
        search_query = str(
            plan.get("search_query") or ""
        ).strip()

        if not search_query:
            # Use conversation context as a fallback.
            previous = _get_previous_user_question(
                history
            )

            search_query = (
                f"{previous} {question}".strip()
                if previous
                else question
            )

        plan["search_query"] = search_query

    # --------------------------------------------------
    # STEP 6: Ask for clarification when necessary
    # --------------------------------------------------

    if plan.get("needs_clarification"):

        clarification = str(
            plan.get("clarification_question") or ""
        ).strip()

        if not clarification:
            clarification = (
                "Which subject or specialization would "
                "you like to find courses for?"
            )

        return _empty_response(
            clarification,
            mode="clarification",
        )

    # --------------------------------------------------
    # STEP 7: Decide whether PDF evidence is needed
    # --------------------------------------------------

    if not plan.get("use_pdf", True):
        useful = []

    # For explicit multi-document comparison, retain
    # all retrieved document evidence.
    if intent == "document_comparison" and chunks:
        useful = _merge_chunks(
            useful,
            chunks,
        )

    # --------------------------------------------------
    # STEP 8: Search the web when required
    # --------------------------------------------------

    web_results = []

    should_search_web = bool(
        plan.get("use_web", False)
    )

    # Course discovery always requires real course listings.
    if is_course_request:
        should_search_web = True

    # Current information requests need web verification.
    if CURRENT_INTENT.search(question):
        should_search_web = True

    # If no PDF evidence exists, allow general web research.
    if not useful and intent not in (
        "document_question",
        "document_comparison",
    ):
        if plan.get("use_web") or is_course_request:
            should_search_web = True

    if should_search_web:

        search_query = str(
            plan.get("search_query") or ""
        ).strip()

        if not search_query:
            search_query = retrieval_query

        try:
            web_results = search_web(
                search_query,
                max_results=8 if is_course_request else 6,
            ) or []

        except Exception:
            logger.exception("Web search failed.")
            web_results = []

    # --------------------------------------------------
    # STEP 9: Determine response evidence mode
    # --------------------------------------------------

    if useful and web_results:
        mode = "hybrid"

    elif useful:
        mode = "documents"

    elif web_results:
        mode = "web_and_knowledge"

    else:
        mode = "general"


    answer_question_text = question

    if is_course_request:
        answer_question_text = f"""
    USER QUESTION:
    {question}
    Today's date is {date}.

    TASK TYPE: COURSE DISCOVERY

    The user wants actual courses, not a generic explanation.

    Understand the user's experience level from the conversation.

    If the user is a beginner:
    - Recommend foundation-level courses.
    - Explain prerequisites.
    - Prioritize courses starting from fundamentals.
    - Show the progression to advanced specialization.

    CURRENT COURSE REQUIREMENTS:
    - Today's date must be considered.
    - Prioritize current and upcoming offerings
    when the user requests or current courses.
    - Verify course availability, syllabus and enrolment
    from the retrieved sources.
    - Distinguish active offerings from archived courses.
    - Never present an old course as a newly updated course
    without evidence.
    - Never invent dates, fees or course links.

    PRESENTATION:
    Use a Markdown comparison table with:
    Course name, provider, level, duration,
    prerequisites, curriculum, fees,
    availability and official link.

    Explain which course is the best starting point
    and why.

    If no reliable current course information is available,
    say so clearly instead of inventing details.
    """

    try:
        answer = generate_answer(
            question=answer_question_text,
            history=history,
            document_chunks=useful,
            web_results=web_results,
            mode=mode,
        )

    except Exception:
        logger.exception("Answer generation failed.")
        raise

    # --------------------------------------------------
    # STEP 11: Generate follow-up suggestions
    # --------------------------------------------------

    try:
        suggestions = generate_followups(
            question,
            answer,
        )

    except Exception:
        logger.exception(
            "Follow-up generation failed."
        )

        suggestions = [
            "Can you explain this in more detail?",
            "What are the key points?",
            "Can you compare the alternatives?",
            "What should I do next?",
        ]

    # --------------------------------------------------
    # STEP 12: Return the API response
    # --------------------------------------------------

    return {
        "answer": answer,
        "mode": mode,
        "document_chunks": useful,
        "web_sources": web_results,
        "suggested_questions": suggestions,
        "used_web_search": bool(web_results),
    }
