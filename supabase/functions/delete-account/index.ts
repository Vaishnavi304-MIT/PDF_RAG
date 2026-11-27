
import { createClient } from "https://esm.sh/@supabase/supabase-js@2";

const corsHeaders = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers":
    "authorization, x-client-info, apikey, content-type",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
};

// Extract a Supabase key from either a raw key or JSON dictionary.
function extractApiKey(
  value: string | undefined,
  prefix: string
): string | null {
  if (!value) return null;

  const trimmed = value.trim();

  // Support a directly supplied key.
  if (trimmed.startsWith(prefix)) return trimmed;

  // Support Supabase JSON dictionaries.
  try {
    const parsed = JSON.parse(trimmed);

    if (typeof parsed === "string" && parsed.startsWith(prefix)) {
      return parsed;
    }

    if (parsed && typeof parsed === "object") {
      const values = Object.values(parsed);

      const matchingKey = values.find(
        (item): item is string =>
          typeof item === "string" && item.startsWith(prefix)
      );

      if (matchingKey) return matchingKey;
    }
  } catch {
    // Invalid JSON or unsupported format.
  }

  return null;
}

function json(
  body: Record<string, unknown>,
  status = 200
) {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      ...corsHeaders,
      "Content-Type": "application/json",
    },
  });
}

Deno.serve(async (req: Request) => {
  if (req.method === "OPTIONS") {
    return new Response("ok", { headers: corsHeaders });
  }

  if (req.method !== "POST") {
    return json({ error: "Method not allowed." }, 405);
  }

  const authorization = req.headers.get("Authorization");

  if (!authorization?.startsWith("Bearer ")) {
    return json(
      { error: "A valid signed-in session is required." },
      401
    );
  }

  const supabaseUrl = Deno.env.get("SUPABASE_URL");

  // New Supabase environment variables.
  const publishableKey = extractApiKey(
    Deno.env.get("SUPABASE_PUBLISHABLE_KEYS"),
    "sb_publishable_"
  );

  const secretKey = extractApiKey(
    Deno.env.get("SUPABASE_SECRET_KEYS"),
    "sb_secret_"
  );

  const bucket =
    Deno.env.get("SUPABASE_STORAGE_BUCKET") ??
    "intellidoc-documents";

  if (!supabaseUrl || !publishableKey || !secretKey) {
    console.error("Supabase account deletion configuration is incomplete.");

    return json(
      { error: "Account deletion is not configured on the server." },
      503
    );
  }

  const token = authorization.slice("Bearer ".length);

  // Client representing the currently authenticated user.
  const userClient = createClient(
    supabaseUrl,
    publishableKey,
    {
      global: {
        headers: {
          Authorization: `Bearer ${token}`,
        },
      },
      auth: {
        persistSession: false,
        autoRefreshToken: false,
      },
    }
  );

  // Privileged client used only inside this Edge Function.
  const admin = createClient(
    supabaseUrl,
    secretKey,
    {
      auth: {
        persistSession: false,
        autoRefreshToken: false,
      },
    }
  );

  try {
    // Verify the user's JWT with Supabase Auth.
    const {
      data: userResult,
      error: userError,
    } = await userClient.auth.getUser(token);

    if (userError || !userResult.user) {
      return json(
        {
          error: "Session is invalid or expired. Please sign in again.",
        },
        401
      );
    }

    // Never accept a user ID from the frontend.
    const userId = userResult.user.id;

    // 1. Find documents owned by this user.
    const { data: docs, error: docsError } = await admin
      .from("documents")
      .select("id, storage_path")
      .eq("owner_id", userId);

    if (docsError) throw docsError;

    // 2. Delete the user's PDF files from Storage.
    const storagePaths = (docs ?? [])
      .map((doc) => doc.storage_path)
      .filter(
        (path): path is string =>
          typeof path === "string" && path.length > 0
      );

    if (storagePaths.length > 0) {
      const { error } = await admin.storage
        .from(bucket)
        .remove(storagePaths);

      if (error) throw error;
    }

    // 3. Delete document chunks.
    const documentIds = (docs ?? []).map((doc) => doc.id);

    if (documentIds.length > 0) {
      const { error } = await admin
        .from("document_chunks")
        .delete()
        .in("document_id", documentIds);

      if (error) throw error;
    }

    // 4. Find the user's chat sessions.
    const { data: chats, error: chatsError } = await admin
      .from("chat_sessions")
      .select("id")
      .eq("owner_id", userId);

    if (chatsError) throw chatsError;

    const chatIds = (chats ?? []).map((chat) => chat.id);

    // 5. Delete chat messages.
    if (chatIds.length > 0) {
      const { error } = await admin
        .from("chat_messages")
        .delete()
        .in("chat_id", chatIds);

      if (error) throw error;
    }

    // 6. Delete document records.
    const { error: docsDeleteError } = await admin
      .from("documents")
      .delete()
      .eq("owner_id", userId);

    if (docsDeleteError) throw docsDeleteError;

    // 7. Delete chat sessions.
    const { error: chatsDeleteError } = await admin
      .from("chat_sessions")
      .delete()
      .eq("owner_id", userId);

    if (chatsDeleteError) throw chatsDeleteError;

    // 8. Permanently delete the Supabase Auth account.
    const { error: authDeleteError } =
      await admin.auth.admin.deleteUser(userId);

    if (authDeleteError) throw authDeleteError;

    return json({
      deleted: true,
      message:
        "Your account and associated IntelliDoc AI data have been deleted.",
    });

  } catch (error) {
    console.error("delete-account failed:", error);

    return json(
      {
        error:
          "Account deletion could not be completed. Please try again or contact support.",
      },
      500
    );
  }
});
