-- IntelliDoc AI: user-scoped Supabase schema for anon/publishable key + Supabase Auth.
-- Run in Supabase SQL Editor. No service-role key is required by the app.

create extension if not exists vector with schema extensions;

-- Existing tables are retained. Add owner columns for per-user isolation.
alter table public.documents add column if not exists owner_id uuid references auth.users(id) on delete cascade;
alter table public.documents add column if not exists storage_path text;
alter table public.documents alter column storage_path drop not null;

alter table public.document_chunks add column if not exists owner_id uuid references auth.users(id) on delete cascade;
alter table public.chat_sessions add column if not exists owner_id uuid references auth.users(id) on delete cascade;
alter table public.chat_messages add column if not exists owner_id uuid references auth.users(id) on delete cascade;

create index if not exists documents_owner_uploaded_idx
    on public.documents(owner_id, uploaded_at desc);
create index if not exists chunks_owner_document_idx
    on public.document_chunks(owner_id, document_id);
create index if not exists chats_owner_updated_idx
    on public.chat_sessions(owner_id, updated_at desc);
create index if not exists messages_owner_chat_idx
    on public.chat_messages(owner_id, chat_id, id);

-- Vector index: 384 dimensions from all-MiniLM-L6-v2.
create index if not exists document_chunks_embedding_idx
    on public.document_chunks using hnsw (embedding extensions.vector_cosine_ops);

-- Enable RLS on every user-owned table.
alter table public.documents enable row level security;
alter table public.document_chunks enable row level security;
alter table public.chat_sessions enable row level security;
alter table public.chat_messages enable row level security;

-- Remove policies from the previous setup before installing the user-scoped policies.
do $$
declare p record;
begin
  for p in
    select schemaname, tablename, policyname
    from pg_policies
    where schemaname = 'public'
      and tablename in ('documents','document_chunks','chat_sessions','chat_messages')
  loop
    execute format('drop policy if exists %I on %I.%I', p.policyname, p.schemaname, p.tablename);
  end loop;
end $$;

create policy "users manage own documents"
on public.documents for all to authenticated
using (owner_id = (select auth.uid()))
with check (owner_id = (select auth.uid()));

create policy "users manage own chunks"
on public.document_chunks for all to authenticated
using (owner_id = (select auth.uid()))
with check (
  owner_id = (select auth.uid())
  and exists (
    select 1 from public.documents d
    where d.id = document_chunks.document_id
      and d.owner_id = (select auth.uid())
  )
);

create policy "users manage own chat sessions"
on public.chat_sessions for all to authenticated
using (owner_id = (select auth.uid()))
with check (owner_id = (select auth.uid()));

create policy "users manage own chat messages"
on public.chat_messages for all to authenticated
using (
  owner_id = (select auth.uid())
  and exists (
    select 1 from public.chat_sessions s
    where s.id = chat_messages.chat_id
      and s.owner_id = (select auth.uid())
  )
)
with check (
  owner_id = (select auth.uid())
  and exists (
    select 1 from public.chat_sessions s
    where s.id = chat_messages.chat_id
      and s.owner_id = (select auth.uid())
  )
);

-- Grants are necessary before RLS policies are evaluated.
grant usage on schema public to authenticated;
grant usage on schema extensions to authenticated;
grant select, insert, update, delete on public.documents to authenticated;
grant select, insert, update, delete on public.document_chunks to authenticated;
grant select, insert, update, delete on public.chat_sessions to authenticated;
grant select, insert, update, delete on public.chat_messages to authenticated;
grant usage, select on all sequences in schema public to authenticated;

-- Remove any accidental table grants to anon. This app requires sign-in.
revoke all on public.documents from anon;
revoke all on public.document_chunks from anon;
revoke all on public.chat_sessions from anon;
revoke all on public.chat_messages from anon;

-- Authenticated users can only upload/read/delete objects under their own UUID folder.
drop policy if exists "Users upload own IntelliDoc PDFs" on storage.objects;
drop policy if exists "Users read own IntelliDoc PDFs" on storage.objects;
drop policy if exists "Users delete own IntelliDoc PDFs" on storage.objects;

create policy "Users upload own IntelliDoc PDFs"
on storage.objects for insert to authenticated
with check (
  bucket_id = 'intellidoc-documents'
  and (storage.foldername(name))[1] = (select auth.uid())::text
);

create policy "Users read own IntelliDoc PDFs"
on storage.objects for select to authenticated
using (
  bucket_id = 'intellidoc-documents'
  and (storage.foldername(name))[1] = (select auth.uid())::text
);

create policy "Users delete own IntelliDoc PDFs"
on storage.objects for delete to authenticated
using (
  bucket_id = 'intellidoc-documents'
  and (storage.foldername(name))[1] = (select auth.uid())::text
);

-- Ensure the bucket is private and limits uploads to PDF files of 20 MiB.
insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values ('intellidoc-documents','intellidoc-documents',false,20971520,array['application/pdf'])
on conflict (id) do update
set public = false, file_size_limit = 20971520,
    allowed_mime_types = array['application/pdf'];

-- Recreate the search RPC. Default SECURITY INVOKER means RLS is respected.
drop function if exists public.match_document_chunks(extensions.vector, integer);
create function public.match_document_chunks(
    query_embedding extensions.vector(384),
    match_count integer default 5
)
returns table (
    document_id uuid,
    filename text,
    page_number integer,
    chunk_index integer,
    content text,
    similarity double precision
)
language sql
stable
security invoker
set search_path = public, extensions
as $$
    select dc.document_id, d.filename, dc.page_number, dc.chunk_index, dc.content,
           1 - (dc.embedding <=> query_embedding) as similarity
    from public.document_chunks dc
    join public.documents d on d.id = dc.document_id
    where dc.owner_id = (select auth.uid())
      and d.owner_id = (select auth.uid())
    order by dc.embedding <=> query_embedding
    limit greatest(1, least(coalesce(match_count, 5), 20));
$$;

grant execute on function public.match_document_chunks(extensions.vector, integer) to authenticated;
revoke execute on function public.match_document_chunks(extensions.vector, integer) from anon;
