-- Allow authenticated users to upload PDFs
-- only into their own user-ID folder.

DROP POLICY IF EXISTS "Users upload own documents"
ON storage.objects;

CREATE POLICY "Users upload own documents"
ON storage.objects
FOR INSERT
TO authenticated
WITH CHECK (
    bucket_id = 'intellidoc-documents'
    AND (storage.foldername(name))[1] = auth.uid()::text
);

-- Allow users to view their own PDFs.
DROP POLICY IF EXISTS "Users read own documents"
ON storage.objects;

CREATE POLICY "Users read own documents"
ON storage.objects
FOR SELECT
TO authenticated
USING (
    bucket_id = 'intellidoc-documents'
    AND (storage.foldername(name))[1] = auth.uid()::text
);

-- Allow users to delete their own PDFs.
DROP POLICY IF EXISTS "Users delete own documents"
ON storage.objects;

CREATE POLICY "Users delete own documents"
ON storage.objects
FOR DELETE
TO authenticated
USING (
    bucket_id = 'intellidoc-documents'
    AND (storage.foldername(name))[1] = auth.uid()::text
);