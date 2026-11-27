import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import "../markdown.css";
import { supabase, apiFetch } from "./supabase";
import { Turnstile } from "@marsidev/react-turnstile";


import { useEffect, useRef, useState } from "react";
import {
  Sparkles, Plus, FileText, Search, Settings, PanelLeftClose,
  Paperclip, Send, Globe2, Database, X, FileUp, Menu, User,
  Trash2, RefreshCw, CheckCircle2, ArrowLeft, SearchCheck,
  MessageSquare, Copy, Pencil, Check
} from "lucide-react";

const API = import.meta.env.VITE_API_BASE_URL || "http://127.0.0.1:8000";

function AuthScreen() {
  const [mode, setMode] = useState("signin");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [captchaNotice, setCaptchaNotice] = useState("");
  const [captchaToken, setCaptchaToken] = useState("");
  const turnstileRef = useRef(null);
  const submitInProgressRef = useRef(false);
  const turnstileSiteKey = import.meta.env.VITE_TURNSTILE_SITE_KEY;

  async function signInWithGoogle() {
    setNotice("");
    setBusy(true);

    try {
      const { error } = await supabase.auth.signInWithOAuth({
        provider: "google",
        options: {
          redirectTo: window.location.origin,
        },
      });

      if (error) throw error;
      // Supabase redirects the browser to Google's consent screen.
    } catch (error) {
      setNotice(error.message || "Google sign-in could not be started.");
      setBusy(false);
    }
  }

  async function resendConfirmation() {
    if (!email.trim()) {
      setNotice("Enter your email address first.");
      return;
    }
    setBusy(true);
    try {
      const { error } = await supabase.auth.resend({
        type: "signup",
        email: email.trim(),
        options: { emailRedirectTo: window.location.origin },
      });
      if (error) throw error;
      setNotice("Confirmation email sent. Check your inbox and spam folder, then return here to sign in.");
    } catch (error) {
      setNotice(error.message || "Could not resend the confirmation email.");
    } finally {
      setBusy(false);
    }
  }

  async function submit(event) {
    event.preventDefault();

    // Prevent duplicate requests while this attempt is running.
    if (submitInProgressRef.current) return;

    setCaptchaNotice("");

    if (!email.trim() || !password) {
      setNotice("Enter your email address and password.");
      return;
    }

    // CAPTCHA is required only for email/password SIGN-IN.
    // Signup intentionally remains CAPTCHA-free.
    if (mode === "signin" && (!turnstileSiteKey || !captchaToken)) {
      setCaptchaNotice(
        !turnstileSiteKey
          ? "Email sign-in CAPTCHA is not configured. Set VITE_TURNSTILE_SITE_KEY."
          : "Complete the Cloudflare check before signing in."
      );
      return;
    }

    submitInProgressRef.current = true;
    setBusy(true);

    try {
      if (mode === "signup") {
        const { data, error } = await supabase.auth.signUp({
          email: email.trim(),
          password,
        });

        if (error) throw error;

        const alreadyRegistered = Boolean(
          data.user && Array.isArray(data.user.identities) && data.user.identities.length === 0
        );
        setNotice(
          data.session
            ? "Account created. You are now signed in."
            : alreadyRegistered
              ? "An account with this email may already exist. Try signing in, or resend the confirmation email if the account is not confirmed."
              : "Account created. Confirm your email using the link we sent, then sign in. You can resend the confirmation email below."
        );

        if (!data.session) {
          setMode("signin");
        }
      } else {
        // Verify the single-use Turnstile token exactly once per login attempt.
        const verifyResponse = await fetch(`${API}/api/auth/verify-captcha`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ token: captchaToken }),
        });

        const verification = await verifyResponse.json().catch(() => ({}));
        if (!verifyResponse.ok || verification.verified !== true) {
          throw new Error(
            verification.detail || "Cloudflare verification failed. Please try again."
          );
        }

        // Do not pass captchaToken here: FastAPI has already verified it.
        const { error } = await supabase.auth.signInWithPassword({
          email: email.trim(),
          password,
        });

        if (error) {
          if (error.code === "email_not_confirmed" || /email not confirmed/i.test(error.message || "")) {
            throw new Error("Your email address is not confirmed yet. Use the 'Resend confirmation email' option below, then sign in.");
          }
          throw error;
        }
      }
    } catch (error) {
      setNotice(error.message || "Authentication failed.");
    } finally {
      // Always unlock the form, including after an incorrect password/API error.
      submitInProgressRef.current = false;
      setBusy(false);

      // Turnstile tokens are single-use. Reset after each email login attempt
      // so the user can complete a fresh challenge before retrying.
      if (mode === "signin") {
        setCaptchaToken("");
        turnstileRef.current?.reset();
      }
    }
  }

  function switchMode() {
    setMode(current => current === "signin" ? "signup" : "signin");
    setNotice("");
    setCaptchaNotice("");
    setCaptchaToken("");
  }

  return (
    <main style={{ minHeight: "100vh", display: "grid", placeItems: "center", padding: 24, background: "#f7f8fc" }}>
      <form onSubmit={submit} style={{ width: "100%", maxWidth: 420, padding: 32, borderRadius: 20, background: "#fff", boxShadow: "0 12px 40px rgba(15,23,42,.08)", display: "grid", gap: 16 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <div style={{ width: 42, height: 42, borderRadius: 12, display: "grid", placeItems: "center", background: "#eef2ff", color: "#4f46e5" }}>
            <Sparkles size={22}/>
          </div>
          <div>
            <h1 style={{ margin: 0, fontSize: 22, color: "#111827" }}>IntelliDoc AI</h1>
            <p style={{ margin: "3px 0 0", color: "#64748b", fontSize: 13 }}>Your secure knowledge assistant</p>
          </div>
        </div>

        <h2 style={{ margin: "8px 0 0", fontSize: 19, color: "#111827" }}>
          {mode === "signin" ? "Welcome back" : "Create your account"}
        </h2>

        <label style={{ display: "grid", gap: 7, color: "#374151", fontSize: 14 }}>
          Email
          <input
            type="email"
            required
            autoComplete="email"
            value={email}
            onChange={e => setEmail(e.target.value)}
            placeholder="you@example.com"
            style={{ padding: 12, border: "1px solid #dbe1ea", borderRadius: 9, fontSize: 14 }}
          />
        </label>

        <label style={{ display: "grid", gap: 7, color: "#374151", fontSize: 14 }}>
          Password
          <input
            type="password"
            required
            minLength={6}
            autoComplete={mode === "signin" ? "current-password" : "new-password"}
            value={password}
            onChange={e => setPassword(e.target.value)}
            placeholder="At least 6 characters"
            style={{ padding: 12, border: "1px solid #dbe1ea", borderRadius: 9, fontSize: 14 }}
          />
        </label>

        {mode === "signin" && (
          <div>
            {turnstileSiteKey ? (
              <Turnstile
                ref={turnstileRef}
                key="email-signin-turnstile"
                siteKey={turnstileSiteKey}
                onSuccess={token => { setCaptchaToken(token); setCaptchaNotice(""); }}
                onExpire={() => setCaptchaToken("")}
                onError={() => {
                  setCaptchaToken("");
                  setCaptchaNotice("Cloudflare verification failed. Check the Turnstile hostname and sitekey.");
                }}
                options={{ theme: "auto", retry: "auto" }}
              />
            ) : (
              <p role="alert" style={{ margin: "8px 0 0", color: "#b45309", fontSize: 12 }}>
                Email sign-in CAPTCHA is not configured. Add VITE_TURNSTILE_SITE_KEY to frontend/.env.local.
              </p>
            )}
          </div>
        )}

        {notice && (
          <p role="status" style={{ margin: 0, padding: 11, borderRadius: 8, background: "#f1f5f9", color: "#334155", fontSize: 13 }}>
            {notice}
          </p>
        )}
        {captchaNotice && (
          <p role="alert" style={{ margin: 0, padding: 10, borderRadius: 8, background: "#fff7ed", color: "#9a3412", fontSize: 13 }}>
            {captchaNotice}
          </p>
        )}
        {mode === "signin" && notice && /confirm|confirmation|not confirmed|Account created/i.test(notice) && (
          <button type="button" disabled={busy} onClick={resendConfirmation}
            style={{ border: 0, background: "transparent", color: "#4f46e5", cursor: busy ? "not-allowed" : "pointer", fontSize: 13 }}>
            Resend confirmation email
          </button>
        )}

        <button
          type="submit"
          disabled={busy || (mode === "signin" && (!captchaToken || !turnstileSiteKey))}
          style={{
            padding: 13,
            border: 0,
            borderRadius: 9,
            background: "#4f46e5",
            color: "#fff",
            fontWeight: 600,
            cursor: busy ? "not-allowed" : "pointer",
            opacity: busy || (mode === "signin" && !captchaToken) ? 0.65 : 1
          }}
        >
          {busy ? "Please wait…" : mode === "signin" ? "Sign in with email" : "Create account"}
        </button>

        <div style={{ display: "flex", alignItems: "center", gap: 12, color: "#94a3b8", fontSize: 12 }}>
          <span style={{ height: 1, background: "#e2e8f0", flex: 1 }} />
          OR
          <span style={{ height: 1, background: "#e2e8f0", flex: 1 }} />
        </div>

        <button
          type="button"
          disabled={busy}
          onClick={signInWithGoogle}
          style={{
            padding: 12,
            border: "1px solid #dbe1ea",
            borderRadius: 9,
            background: "#fff",
            color: "#1f2937",
            fontWeight: 600,
            cursor: busy ? "not-allowed" : "pointer",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            gap: 10
          }}
        >
          <span aria-hidden="true" style={{ fontWeight: 700, fontSize: 18, color: "#4285F4" }}>G</span>
          Continue with Google
        </button>

        <button
          type="button"
          disabled={busy}
          onClick={switchMode}
          style={{ border: 0, background: "transparent", color: "#4f46e5", cursor: "pointer" }}
        >
          {mode === "signin" ? "New here? Create an account" : "Already have an account? Sign in"}
        </button>
      </form>
    </main>
  );
}

const prompts = [
  "Summarize my document",
  "What are the key findings?",
  "Explain the main concepts simply"
];

const formatSize = bytes =>
  bytes < 1024 * 1024
    ? `${(bytes / 1024).toFixed(0)} KB`
    : `${(bytes / (1024 * 1024)).toFixed(1)} MB`;

export default function App() {
  const [session, setSession] = useState(null);
  const [authReady, setAuthReady] = useState(false);
  const [apiStatus, setApiStatus] = useState("checking");
  const [message, setMessage] = useState("");
  const [messages, setMessages] = useState([]);
  const [chatSessionId, setChatSessionId] = useState(0);
  const [chatId, setChatId] = useState(null);
  const [chatSessions, setChatSessions] = useState([]);
  const [documents, setDocuments] = useState([]);
  const [title, setTitle] = useState("New conversation");
  const [sidebar, setSidebar] = useState(true);
  const [notice, setNotice] = useState("");
  const [uploading, setUploading] = useState(false);
  const [isSending, setIsSending] = useState(false);
  const [loadingDocs, setLoadingDocs] = useState(false);
  const [view, setView] = useState("chat");
  const [searchQuery, setSearchQuery] = useState("");
  const [searchResults, setSearchResults] = useState([]);
  const [searching, setSearching] = useState(false);
  const [searchMessage, setSearchMessage] = useState("");
  const [indexedChunks, setIndexedChunks] = useState(0);

  const picker = useRef(null);
  const endRef = useRef(null);
  const [copiedMessage, setCopiedMessage] = useState(null);
  const [editingMessage, setEditingMessage] = useState(null);

  async function deleteAccount() {
    const confirmed = window.confirm(
      "Permanently delete your IntelliDoc AI account, uploaded PDFs, document indexes, and chat history? This cannot be undone."
    );
    if (!confirmed) return;
    const typed = window.prompt('Type DELETE to permanently remove your account:');
    if (typed !== "DELETE") {
      setNotice("Account deletion cancelled. Type DELETE exactly to confirm.");
      return;
    }
    setNotice("Deleting your account and data…");
    try {
      // The Edge Function validates the current user's session and performs
      // privileged cleanup server-side. No service-role key is exposed here.
      const { data, error } = await supabase.functions.invoke("delete-account", {
        method: "POST",
      });
      if (error) throw new Error(error.message || "Account deletion failed.");
      if (!data?.deleted) throw new Error(data?.error || "Account deletion could not be confirmed.");
      await supabase.auth.signOut();
      setNotice("");
    } catch (error) {
      setNotice(error.message || "Could not delete your account.");
    }
  }

  async function copyMessage(content, id) {
    try {
      await navigator.clipboard.writeText(content);
      setCopiedMessage(id);

      setTimeout(() => {
        setCopiedMessage(null);
      }, 1800);
    } catch {
      setNotice("Could not copy this message. Check browser permissions.");
    }
  }

  function editMessage(content, index) {
    setMessage(content);
    setEditingMessage(index);

    // Move focus to the message composer.
    requestAnimationFrame(() => {
      document.querySelector(".composer textarea")?.focus();
    });
  }

  async function loadDocuments() {
    setLoadingDocs(true);

    try {
      const response = await apiFetch(`${API}/api/documents`);

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}));
        console.error("GET /api/documents failed:", response.status, errorData);
        throw new Error(
          errorData.detail || `Could not load documents (HTTP ${response.status}).`
        );
      }

      const data = await response.json();
      setDocuments(data.documents || []);

    } catch (error) {
      setNotice(error.message || "Could not load documents.");
    } finally {
      setLoadingDocs(false);
    }
  }

  async function loadSearchStatus() {
    try {
      const response = await apiFetch(`${API}/api/search/status`);

      if (response.ok) {
        const data = await response.json();
        setIndexedChunks(data.indexed_chunks || 0);
      }
    } catch {
      // API status indicator handles connectivity.
    }
  }

  async function checkApi() {
    try {
      // /health is public and must not depend on a browser auth session.
      const response = await fetch(`${API}/health`);

      if (!response.ok) {
        throw new Error();
      }

      const data = await response.json();

      if (data.status !== "ok") {
        throw new Error();
      }

      setApiStatus("online");

      await Promise.all([
        loadDocuments(),
        loadSearchStatus()
      ]);

    } catch {
      setApiStatus("offline");
    }
  }

  // Subscribe before reading persisted auth state so an OAuth/refresh event
  // cannot be overwritten by a slower getSession() result.
  useEffect(() => {
    let mounted = true;
    let authEventReceived = false;

    const { data: listener } = supabase.auth.onAuthStateChange((_event, nextSession) => {
      authEventReceived = true;
      if (mounted) {
        setSession(nextSession);
        setAuthReady(true);
      }
    });

    supabase.auth.getSession().then(({ data, error }) => {
      if (!mounted || authEventReceived) return;
      if (error) {
        console.error("Could not restore Supabase session:", error.message);
      }
      setSession(data?.session ?? null);
      setAuthReady(true);
    }).catch((error) => {
      if (!mounted || authEventReceived) return;
      console.error("Could not restore Supabase session:", error);
      setSession(null);
      setAuthReady(true);
    });

    return () => {
      mounted = false;
      listener.subscription.unsubscribe();
    };
  }, []);

  // Load each user's workspace only after Supabase Auth has restored a session.
  useEffect(() => {
    if (!session) {
      setDocuments([]);
      setChatSessions([]);
      setMessages([]);
      setChatId(null);
      return;
    }
    const initializeApp = async () => {
      await checkApi();
      await loadChats();
    };
    initializeApp();
  }, [session?.user?.id]);

  // UPDATED: Safe message scrolling effect
  useEffect(() => {
    if (endRef.current) {
      endRef.current.scrollIntoView({
        behavior: "smooth",
        block: "end"
      });
    }
  }, [messages]);

  async function loadChats() {
    try {
      const response = await apiFetch(`${API}/api/chats`);
      if (!response.ok) return;
      const data = await response.json();
      const chats = data.chats || [];
      setChatSessions(chats);
      if (!chatId && chats.length) {
        await openChat(chats[0].id);
      } else if (!chatId && !chats.length) {
        await createNewChat();
      }
    } catch (error) {
      console.error("Could not load conversations:", error);
    }
  }

  async function createNewChat() {
    try {
      const response = await apiFetch(`${API}/api/chats`, { method: "POST" });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "Could not create conversation.");
      setChatId(data.id);
      setChatSessionId(id => id + 1);
      setMessages([]);
      setTitle(data.title || "New conversation");
      setMessage("");
      setNotice("");
      setSearchQuery("");
      setSearchResults([]);
      setSearchMessage("");
      setView("chat");
      await loadChatListOnly();
    } catch (error) {
      setNotice(error.message);
    }
  }

  async function loadChatListOnly() {
    try {
      const response = await apiFetch(`${API}/api/chats`);
      if (response.ok) {
        const data = await response.json();
        setChatSessions(data.chats || []);
      }
    } catch (error) {
      console.error(error);
    }
  }

  async function openChat(id) {
    try {
      const response = await apiFetch(`${API}/api/chats/${id}`);
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "Could not open conversation.");
      setChatId(id);
      setTitle(data.chat?.title || "New conversation");
      setMessages((data.messages || []).map(item => ({
        role: item.role, content: item.content
      })));
      setMessage("");
      setView("chat");
      setChatSessionId(value => value + 1);
    } catch (error) {
      setNotice(error.message);
    }
  }

  async function removeChat(id, event) {
    event?.stopPropagation();
    if (!window.confirm("Delete this conversation and its messages? Your uploaded PDFs will not be deleted.")) return;
    try {
      const response = await apiFetch(`${API}/api/chats/${id}`, { method: "DELETE" });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "Could not delete conversation.");
      const remaining = chatSessions.filter(chat => chat.id !== id);
      setChatSessions(remaining);
      if (chatId === id) {
        if (remaining.length) await openChat(remaining[0].id);
        else await createNewChat();
      }
    } catch (error) {
      setNotice(error.message);
    }
  }

  function newChat() {
    createNewChat();
  }

  async function chooseFiles(list) {
    const selected = Array.from(list || []);

    if (!selected.length) return;

    const invalid = selected.find(
      f => !f.name.toLowerCase().endsWith(".pdf")
    );

    if (invalid) {
      setNotice(
        `${invalid.name} is not a PDF. Please select PDF files only.`
      );
      return;
    }

    if (apiStatus !== "online") {
      setNotice("Backend is offline. Start FastAPI and try again.");
      return;
    }

    setUploading(true);
    setNotice("");

    let successes = 0;

    for (const file of selected) {
      if (file.size > 50 * 1024 * 1024) {
        setNotice(`${file.name} exceeds the 20 MB limit.`);
        continue;
      }

      const form = new FormData();
      form.append("file", file);

      try {
        const response = await apiFetch(
          `${API}/api/documents/upload`,
          {
            method: "POST",
            body: form
          }
        );

        const data = await response.json();

        if (!response.ok) {
          throw new Error(data.detail || "Upload failed.");
        }

        successes++;

        setNotice(
          `${data.document.filename}: ${data.document.chunk_count} chunks embedded and indexed.`
        );

      } catch (error) {
        setNotice(`${file.name}: ${error.message}`);
      }
    }

    await Promise.all([
      loadDocuments(),
      loadSearchStatus()
    ]);

    setUploading(false);

    if (successes) {
      setNotice(
        `${successes} PDF${successes > 1 ? "s" : ""} uploaded and indexed successfully.`
      );
    }

    if (picker.current) {
      picker.current.value = "";
    }
  }

  async function deleteDocument(id, name) {
    if (
      !window.confirm(
        `Delete "${name}" from your local document library?`
      )
    ) {
      return;
    }

    try {
      const response = await apiFetch(
        `${API}/api/documents/${id}`,
        { method: "DELETE" }
      );

      const data = await response.json();

      if (!response.ok) {
        throw new Error(data.detail || "Delete failed.");
      }

      setDocuments(old =>
        old.filter(doc => doc.id !== id)
      );

      await loadSearchStatus();

      setNotice(`${name} deleted and vector index updated.`);

    } catch (error) {
      setNotice(error.message);
    }
  }

  async function runSearch(event) {
    event?.preventDefault();

    if (!searchQuery.trim()) return;

    setSearching(true);
    setSearchMessage("");
    setSearchResults([]);

    try {
      const response = await apiFetch(`${API}/api/search`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json"
        },
        body: JSON.stringify({
          query: searchQuery.trim(),
          top_k: 5
        })
      });

      const data = await response.json();

      if (!response.ok) {
        throw new Error(
          data.detail || "Semantic search failed."
        );
      }

      setSearchResults(data.results || []);
      setSearchMessage(data.message || "");

    } catch (error) {
      setSearchMessage(error.message);
    } finally {
      setSearching(false);
    }
  }

  async function send(text = message) {
    const value = text.trim();
    if (!value || isSending) return;

    // A non-null index means the user is editing an existing question.
    const editIndex = editingMessage;
    const previousMessages = messages;

    // The AI receives only the conversation before the edited question.
    const conversationBeforeEdit =
      editIndex !== null ? messages.slice(0, editIndex) : messages;

    const history = conversationBeforeEdit
      .filter(item =>
        !item.loading &&
        (item.role === "user" || item.role === "assistant")
      )
      .slice(-12)
      .map(({ role, content }) => ({ role, content }));

    const replacementMessages = [
      { role: "user", content: value },
      { role: "assistant", content: "", loading: true }
    ];

    // Show the edited turn in its original position and discard later turns.
    setMessages(
      editIndex !== null
        ? [...messages.slice(0, editIndex), ...replacementMessages]
        : [...messages, ...replacementMessages]
    );

    setTitle(value.length > 32 ? value.slice(0, 32) + "…" : value);
    setMessage("");
    setEditingMessage(null);
    setIsSending(true);
    setNotice("");

    try {
      const response = await apiFetch(`${API}/api/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          question: value,
          chat_id: chatId,
          history,
          top_k: 8,
          edit_from_index: editIndex
        })
      });

      const data = await response.json().catch(() => {
        throw new Error("Backend returned an invalid response.");
      });

      if (!response.ok) {
        throw new Error(data.detail || "Could not generate an answer.");
      }

      if (data.chat_id && !chatId) setChatId(data.chat_id);

      // Replace the temporary loading row with the new AI answer.
      setMessages(current => {
        const updated = [...current];
        const lastIndex = updated.length - 1;
        updated[lastIndex] = {
          role: "assistant",
          content: data.answer,
          mode: data.mode,
          document_sources: data.document_sources || [],
          web_sources: data.web_sources || [],
          suggested_questions: data.suggested_questions || []
        };
        return updated;
      });

      await loadChatListOnly();
    } catch (error) {
      // Restore the original conversation if an edit cannot be regenerated.
      if (editIndex !== null) {
        setMessages(previousMessages);
        setMessage(value);
        setEditingMessage(editIndex);
      } else {
        setMessages(current =>
          current.map((item, index) =>
            index === current.length - 1
              ? {
                  role: "assistant",
                  content: `I couldn't complete that request. ${error.message}`,
                  mode: "error"
                }
              : item
          )
        );
      }
      setNotice(error.message || "Could not generate an answer.");
    } finally {
      setIsSending(false);
    }
  }

  if (!authReady) {
    return <main style={{ minHeight: "100vh", display: "grid", placeItems: "center", color: "#64748b" }}>Loading secure workspace…</main>;
  }
  if (!session) return <AuthScreen />;

  return (
    <div className="shell">

      {sidebar && (
        <aside className="sidebar">

          <div className="brand">
            <div className="brand-icon">
              <Sparkles size={21} />
            </div>

            <div>
              <b>IntelliDoc AI</b>
              <small>Your knowledge assistant</small>
            </div>

            <button
              className="icon-btn collapse"
              onClick={() => setSidebar(false)}
              title="Collapse sidebar"
            >
              <PanelLeftClose size={17} />
            </button>
          </div>

          <button className="new-chat" onClick={newChat}>
            <Plus size={17} /> New conversation
          </button>

          <div className="section-label">WORKSPACE</div>

          <button
            className={`nav ${view === "chat" ? "active" : ""}`}
            onClick={() => setView("chat")}
          >
            <MessageSquare size={17} /> Chat
          </button>

          <button
            className={`nav ${view === "documents" ? "active" : ""}`}
            onClick={() => setView("documents")}
          >
            <FileText size={17} /> Documents
            <span className="nav-count">{documents.length}</span>
          </button>

          <button
            className={`nav ${view === "search" ? "active" : ""}`}
            onClick={() => setView("search")}
          >
            <Search size={17} /> Semantic search
          </button>

          <button
            className={`nav ${view === "settings" ? "active" : ""}`}
            onClick={() => setView("settings")}
          >
            <Settings size={17} /> Settings
          </button>

          <div className="section-label recent-label">YOUR CONVERSATIONS</div>
          <div className="chat-history">
            {chatSessions.length === 0 && (
              <p className="empty-history">Your conversations will appear here.</p>
            )}
            {chatSessions.map(chat => (
              <div
                className={`history-row ${chat.id === chatId ? "selected" : ""}`}
                key={chat.id}
                onClick={() => openChat(chat.id)}
                role="button"
                tabIndex={0}
                onKeyDown={e => { if (e.key === "Enter") openChat(chat.id); }}
              >
                <MessageSquare size={14} />
                <span title={chat.title}>{chat.title}</span>
                <button
                  className="delete-chat"
                  title="Delete conversation"
                  onClick={e => removeChat(chat.id, e)}
                >
                  <Trash2 size={13} />
                </button>
              </div>
            ))}
          </div>

          <div className="section-label recent-label">
            DOCUMENT LIBRARY
            <button
              className="refresh-docs"
              onClick={loadDocuments}
              title="Refresh documents"
            >
              <RefreshCw size={12} />
            </button>
          </div>

          <div className="document-list">

            {loadingDocs && (
              <p className="empty-history">
                Loading documents…
              </p>
            )}

            {!loadingDocs && documents.length === 0 && (
              <p className="empty-history">
                No PDFs uploaded yet.
              </p>
            )}

            {documents.map(doc => (
              <div className="document-item" key={doc.id}>
                <FileText size={15} />

                <div className="document-info">
                  <b title={doc.filename}>{doc.filename}</b>
                  <small>
                    {doc.page_count} pages · {formatSize(doc.file_size)}
                  </small>
                </div>

                <button
                  className="delete-doc"
                  onClick={() =>
                    deleteDocument(doc.id, doc.filename)
                  }
                  title="Delete document"
                >
                  <Trash2 size={13} />
                </button>
              </div>
            ))}

          </div>

          <div className="profile">
            <div className="avatar"><User size={16}/></div>
            <div style={{ minWidth: 0 }}>
              <b>Workspace</b>
              <small title={session?.user?.email}>{session?.user?.email || "Signed in"}</small>
            </div>
            <button className="icon-btn" title="Account settings" onClick={() => setView("settings")}>
              <Settings size={16}/>
            </button>
            <button className="icon-btn" title="Sign out" onClick={() => supabase.auth.signOut()}>
              <ArrowLeft size={16}/>
            </button>
          </div>

        </aside>
      )}

      <main className="main">

        <input
          ref={picker}
          type="file"
          accept=".pdf,application/pdf"
          multiple
          hidden
          onChange={e => {
            chooseFiles(e.target.files);
            e.target.value = "";
          }}
        />

        <header className="topbar">

          <div className="top-left">

            {!sidebar && (
              <button
                className="icon-btn"
                onClick={() => setSidebar(true)}
              >
                <Menu size={19} />
              </button>
            )}

            <div>
              <b>
                {view === "search"
                  ? "Semantic search"
                  : view === "documents"
                    ? "Documents"
                    : view === "settings"
                      ? "Settings"
                      : title}
              </b>

              <small>
                {view === "search"
                  ? "Find meaning across your indexed PDFs"
                  : view === "settings"
                    ? "Manage your profile and account"
                    : "Ask questions about your knowledge"}
              </small>
            </div>

          </div>

          <div className="top-right">

            <span className={`api ${apiStatus}`}>
              <i />
              {apiStatus === "checking"
                ? "Checking API"
                : apiStatus === "online"
                  ? "API connected"
                  : "API offline"}
            </span>

            <button
              className="icon-btn"
              onClick={newChat}
              title="New chat"
            >
              <Plus size={18} />
            </button>

          </div>
        </header>

        {view === "settings" ? (
          <section className="documents-workspace">
            <div className="workspace-heading">
              <div>
                <h1>Account settings</h1>
                <p>Manage your IntelliDoc AI profile and account security.</p>
              </div>
            </div>
            <div className="document-grid">
              <article className="document-card" style={{ gridColumn: "1 / -1", alignItems: "flex-start", gap: 12 }}>
                <User size={25} />
                <b>Profile</b>
                <small>Signed in as</small>
                <p style={{ margin: 0, overflowWrap: "anywhere" }}>{session?.user?.email || "Signed in"}</p>
                <small>User ID: {session?.user?.id || "Unavailable"}</small>
              </article>
              <article className="document-card" style={{ gridColumn: "1 / -1", alignItems: "flex-start", gap: 12, border: "1px solid #fecaca" }}>
                <Trash2 size={25} color="#dc2626" />
                <b>Delete account</b>
                <small>Permanently delete your account, uploaded PDFs, indexed document content, and chat history. This action cannot be undone.</small>
                <button onClick={deleteAccount} style={{ background: "#dc2626", color: "white", border: 0, borderRadius: 8, padding: "10px 16px", cursor: "pointer" }}>
                  Permanently delete my account
                </button>
              </article>
            </div>
          </section>
        ) : view === "search" ? (

          <section className="search-workspace">

            <div className="search-hero">

              <div className="hero-icon">
                <SearchCheck size={27} />
              </div>

              <div className="eyebrow">
                PHASE 4 · VECTOR RETRIEVAL
              </div>

              <h1>
                Search by <span>meaning.</span>
              </h1>

              <p>
                Ask a question or enter a concept. IntelliDoc AI
                will retrieve the most relevant passages from
                your uploaded PDFs.
              </p>

              <form
                className="semantic-form"
                onSubmit={runSearch}
              >
                <Search size={19} />

                <input
                  value={searchQuery}
                  onChange={e => setSearchQuery(e.target.value)}
                  placeholder="e.g. What are the main causes of network attacks?"
                />

                <button
                  disabled={
                    searching || searchQuery.trim().length < 2
                  }
                >
                  {searching ? "Searching…" : "Search"}
                </button>
              </form>

              <div className="index-status">
                <Database size={14} />
                {indexedChunks} text chunks indexed · Top 5 results
              </div>

            </div>

            {searchMessage && (
              <div className="search-message">
                {searchMessage}
              </div>
            )}

            <div className="search-results">

              {searchResults.map((result, i) => (
                <article
                  className="result-card"
                  key={`${result.document_id}-${result.chunk_index}-${i}`}
                >
                  <div className="result-head">
                    <b>{result.filename}</b>
                    <span>Page {result.page_number}</span>
                  </div>

                  <p>{result.content}</p>

                  <div className="result-foot">
                    <span>
                      Similarity score: {result.score.toFixed(3)}
                    </span>
                    <span>
                      Chunk {result.chunk_index + 1}
                    </span>
                  </div>
                </article>
              ))}

            </div>

            {!searchResults.length && !searching && (
              <div className="search-empty">
                <FileText size={25} />
                <b>Relevant passages will appear here</b>
                <small>
                  Upload PDFs, then search using natural language.
                </small>
              </div>
            )}

          </section>

        ) : view === "documents" ? (

          <section className="documents-workspace">

            <div className="workspace-heading">

              <div>
                <h1>Your documents</h1>
                <p>
                  Manage PDFs stored in your local knowledge base.
                </p>
              </div>

              <button
                className="upload-main"
                onClick={() => picker.current?.click()}
                disabled={uploading}
              >
                <FileUp size={16} />
                {uploading ? "Processing…" : "Upload PDFs"}
              </button>

            </div>

            <div className="document-grid">

              {documents.map(doc => (
                <article
                  className="document-card"
                  key={doc.id}
                >
                  <FileText size={25} />

                  <b title={doc.filename}>
                    {doc.filename}
                  </b>

                  <small>
                    {doc.page_count} pages · {formatSize(doc.file_size)}
                  </small>

                  <small>
                    {doc.extracted_chars.toLocaleString()} extracted characters
                  </small>

                  <button
                    onClick={() =>
                      deleteDocument(doc.id, doc.filename)
                    }
                  >
                    <Trash2 size={14} /> Delete
                  </button>

                </article>
              ))}

            </div>

            {!documents.length && (
              <div className="search-empty">
                <FileText size={25} />
                <b>No documents yet</b>
                <small>
                  Upload a PDF to begin building your knowledge base.
                </small>
              </div>
            )}

          </section>

        ) : (

          <>

            <section className="chat" key={chatSessionId}>

              {!messages.length ? (

                <div className="welcome">

                  <div className="hero-icon">
                    <Sparkles size={28} />
                  </div>

                  <div className="eyebrow">
                    YOUR AI DOCUMENT WORKSPACE
                  </div>

                  <h1>
                    What would you like
                    <br />
                    <span>to understand today?</span>
                  </h1>

                  <p className="intro">
                    Upload your PDF documents and ask questions
                    in natural language. IntelliDoc AI will help
                    you find answers across your files and beyond.
                  </p>

                  <div className="features">

                    <div className="feature">
                      <div className="feature-icon purple">
                        <FileText size={19} />
                      </div>
                      <b>Document intelligence</b>
                      <small>Upload and process PDF files</small>
                    </div>

                    <div className="feature">
                      <div className="feature-icon blue">
                        <Database size={19} />
                      </div>
                      <b>Semantic retrieval</b>
                      <small>
                        {indexedChunks} indexed text chunks
                      </small>
                    </div>

                    <div className="feature">
                      <div className="feature-icon green">
                        <Globe2 size={19} />
                      </div>
                      <b>Web knowledge</b>
                      <small>
                        DuckDuckGo + general knowledge fallback
                      </small>
                    </div>

                  </div>

                  <div className="prompt-list">

                    {prompts.map(p => (
                      <button
                        key={p}
                        onClick={() => send(p)}
                      >
                        {p}
                        <span>↗</span>
                      </button>
                    ))}

                  </div>

                  {documents.length > 0 && (
                    <div className="ready-note">
                      <CheckCircle2 size={15} />
                      {documents.length} document
                      {documents.length === 1 ? "" : "s"} in your library
                    </div>
                  )}

                </div>

              ) : (

                <div className="messages">

                  {messages.map((m, i) => (

                    <div className="message" key={i}>

                      <div className={`message-avatar ${m.role}`}>
                        {m.role === "assistant"
                          ? <Sparkles size={17} />
                          : <User size={17} />}
                      </div>

                      <div className="message-body">

                        <b>
                          {m.role === "assistant"
                            ? "IntelliDoc AI"
                            : "You"}
                        </b>
                        <div className="message-actions">

                        <button
                          type="button"
                          className="message-action"
                          onClick={() => copyMessage(m.content, i)}
                          disabled={!m.content || m.loading}
                          title="Copy message"
                        >
                          {copiedMessage === i
                            ? <Check size={14} />
                            : <Copy size={14} />}
                          {copiedMessage === i ? "Copied" : "Copy"}
                        </button>

                        {m.role === "user" && !m.loading && (
                          <button
                            type="button"
                            className="message-action"
                            onClick={() => editMessage(m.content, i)}
                            title="Edit question"
                          >
                            <Pencil size={14} />
                            Edit
                          </button>
                        )}

                      </div>

                        {m.loading ? (
                          <p>Thinking…</p>
                        ) : (
                          
                            <div className="markdown-answer">
                              <ReactMarkdown remarkPlugins={[remarkGfm]}>
                                {m.content}
                              </ReactMarkdown>
                            </div>

                        )}
                        
                        {m.sources?.length > 0 && (
                          <div className="message-sources">
                            <b>Sources</b>
                            {m.sources.map((source, j) => (
                              <span
                                key={`${source.document_id}-${source.page_number}-${j}`}
                              >
                                {source.filename} · Page {source.page_number}
                              </span>
                            ))}

                          </div>
                        )}

                        {m.suggested_questions?.length > 0 && i === messages.length - 1 && (
                          <div className="suggested-questions">
                            <b>Continue exploring</b>
                            <div>{m.suggested_questions.map((q, j) => (
                              <button key={j} onClick={() => send(q)} disabled={isSending}>{q}</button>
                            ))}</div>
                          </div>
                        )}

                      </div>

                    </div>

                  ))}

                  <div ref={endRef} />

                </div>

              )}

            </section>

            <section className="composer-area">

              {notice && (
                <div className="notice">
                  {notice}
                  <button onClick={() => setNotice("")}>
                    <X size={13} />
                  </button>
                </div>
              )}
              {editingMessage !== null && (
                <div className="editing-indicator">
                  <span>Editing your question</span>

                  <button
                    type="button"
                    onClick={() => {
                      setEditingMessage(null);
                      setMessage("");
                    }}
                  >
                    Cancel <X size={13} />
                  </button>
                </div>
              )}
              <div className="composer">

                <button
                  className="attach"
                  onClick={() => picker.current?.click()}
                  disabled={uploading}
                  title="Upload PDF"
                >
                  <Paperclip size={19} />
                </button>

                <textarea
                  rows="1"
                  value={message}
                  onChange={e => setMessage(e.target.value)}
                  onKeyDown={e => {
                    if (
                      e.key === "Enter" &&
                      !e.shiftKey
                    ) {
                      e.preventDefault();
                      send();
                    }
                  }}
                  disabled={isSending}
                  placeholder="Ask anything about your documents..."
                />

                <button
                  className="send"
                  disabled={!message.trim() || isSending}
                  onClick={() => send()}
                  title="Send message"
                >
                  <Send size={17} />
                </button>

              </div>

              <div className="composer-hint">
                <span>
                  <FileUp size={13} />
                  {uploading
                    ? "Uploading, embedding and indexing…"
                    : "Upload PDF · Max 50 MB each"}
                </span>

                <span>
                  Enter to send · Shift + Enter for new line
                </span>
              </div>

            </section>

          </>

        )}

        {notice && view !== "chat" && (
          <div className="notice floating-notice">
            {notice}
            <button onClick={() => setNotice("")}>
              <X size={13} />
            </button>
          </div>
        )}

        <footer className="footer">
          IntelliDoc AI can make mistakes. Verify important information.
          <span>Phase 5 · Groq + RAG + DuckDuckGo</span>
        </footer>

      </main>

    </div>
  );
}