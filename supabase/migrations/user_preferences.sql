
-- 1. Store chat retention preferences separately.
CREATE TABLE IF NOT EXISTS public.user_preferences (
    user_id UUID PRIMARY KEY
        REFERENCES auth.users(id) ON DELETE CASCADE,

    chat_retention_days INTEGER NOT NULL DEFAULT 0,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT valid_chat_retention
        CHECK (chat_retention_days IN (0, 30, 90, 365))
);

-- 2. Enable user-level security.
ALTER TABLE public.user_preferences
ENABLE ROW LEVEL SECURITY;

-- 3. Users can read their own preferences.
CREATE POLICY "Users can read own preferences"
ON public.user_preferences
FOR SELECT
TO authenticated
USING (user_id = auth.uid());

-- 4. Users can create their own preferences.
CREATE POLICY "Users can create own preferences"
ON public.user_preferences
FOR INSERT
TO authenticated
WITH CHECK (user_id = auth.uid());

-- 5. Users can update their own preferences.
CREATE POLICY "Users can update own preferences"
ON public.user_preferences
FOR UPDATE
TO authenticated
USING (user_id = auth.uid())
WITH CHECK (user_id = auth.uid());

-- 6. Improve chat-list query performance.
CREATE INDEX IF NOT EXISTS
idx_chat_sessions_owner_updated
ON public.chat_sessions(owner_id, updated_at DESC);

-- 7. Improve message retrieval performance.
CREATE INDEX IF NOT EXISTS
idx_chat_messages_chat_id_id
ON public.chat_messages(chat_id, id);

INSERT INTO public.user_preferences (
    user_id,
    chat_retention_days
)
VALUES (
    auth.uid(),
    0
)
ON CONFLICT (user_id)
DO NOTHING;

SELECT *
FROM public.user_preferences;