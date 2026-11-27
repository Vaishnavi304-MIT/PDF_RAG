import { createClient } from "@supabase/supabase-js";

const supabaseUrl = import.meta.env.VITE_SUPABASE_URL;
const supabaseAnonKey = import.meta.env.VITE_SUPABASE_ANON_KEY;

if (!supabaseUrl || !supabaseAnonKey) {
  throw new Error(
    "Supabase is not configured. Set VITE_SUPABASE_URL and VITE_SUPABASE_ANON_KEY in frontend/.env.local."
  );
}

export const supabase = createClient(supabaseUrl, supabaseAnonKey, {
  auth: {
    persistSession: true,
    autoRefreshToken: true,
    detectSessionInUrl: true,
  },
});

// Get a current access token and attach it to every protected FastAPI request.
// If the stored token is expired or the API rejects it once, refresh and retry once.
async function getAccessToken() {
  let { data, error } = await supabase.auth.getSession();
  if (error) throw new Error(`Could not read Supabase session: ${error.message}`);

  let session = data.session;
  if (!session?.access_token) {
    throw new Error("Your session is missing. Please sign in again.");
  }

  const expiresAt = session.expires_at;
  const expiresSoon = !expiresAt || expiresAt <= Math.floor(Date.now() / 1000) + 45;

  if (expiresSoon) {
    const refreshed = await supabase.auth.refreshSession();
    if (refreshed.error) {
      throw new Error(`Your login session expired. Please sign in again: ${refreshed.error.message}`);
    }
    session = refreshed.data.session;
  }

  if (!session?.access_token) {
    throw new Error("Supabase did not return an access token. Please sign in again.");
  }

  return session.access_token;
}

export async function apiFetch(url, options = {}) {
  const token = await getAccessToken();

  const makeRequest = (accessToken) => {
    const headers = new Headers(options.headers || {});
    headers.set("Authorization", `Bearer ${accessToken}`);
    return fetch(url, { ...options, headers });
  };

  let response = await makeRequest(token);

  // A single refresh/retry handles a token that became invalid between
  // session retrieval and the API request. Do not retry other errors.
  if (response.status === 401) {
    const refreshed = await supabase.auth.refreshSession();
    if (refreshed.error || !refreshed.data.session?.access_token) {
      await supabase.auth.signOut();
      throw new Error("Your login session is no longer valid. Please sign in again.");
    }
    response = await makeRequest(refreshed.data.session.access_token);
  }

  return response;
}
