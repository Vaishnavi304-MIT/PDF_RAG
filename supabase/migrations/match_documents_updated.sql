
CREATE OR REPLACE FUNCTION public.match_document_chunks_per_document(
    query_embedding extensions.vector(384),
    match_count_per_document integer DEFAULT 4
)
RETURNS TABLE (
    document_id uuid,
    filename text,
    page_number integer,
    chunk_index integer,
    content text,
    similarity double precision
)
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = public, extensions
AS $$
    SELECT
        ranked.document_id,
        ranked.filename,
        ranked.page_number,
        ranked.chunk_index,
        ranked.content,
        ranked.similarity
    FROM (
        SELECT
            dc.document_id,
            d.filename,
            dc.page_number,
            dc.chunk_index,
            dc.content,
            1 - (dc.embedding <=> query_embedding) AS similarity,
            ROW_NUMBER() OVER (
                PARTITION BY dc.document_id
                ORDER BY dc.embedding <=> query_embedding
            ) AS document_rank
        FROM public.document_chunks dc
        JOIN public.documents d
            ON d.id = dc.document_id
        WHERE dc.owner_id = (SELECT auth.uid())
          AND d.owner_id = (SELECT auth.uid())
    ) AS ranked
    WHERE ranked.document_rank <=
        GREATEST(1, LEAST(COALESCE(match_count_per_document, 4), 10))
    ORDER BY ranked.document_id, ranked.similarity DESC;
$$;

GRANT EXECUTE ON FUNCTION
public.match_document_chunks_per_document(
    extensions.vector, integer
) TO authenticated;

REVOKE EXECUTE ON FUNCTION
public.match_document_chunks_per_document(
    extensions.vector, integer
) FROM anon;
