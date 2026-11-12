import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import "../markdown.css";

import { useEffect, useRef, useState } from "react";
import {
  Sparkles, Plus, FileText, Search, Settings, PanelLeftClose,
  Paperclip, Send, Globe2, Database, X, FileUp, Menu, User,
  Trash2, RefreshCw, CheckCircle2, ArrowLeft, SearchCheck, MessageSquare
} from "lucide-react";

const API = import.meta.env.VITE_API_BASE_URL || "http://127.0.0.1:8000";

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

  async function loadDocuments() {
    setLoadingDocs(true);

    try {
      const response = await fetch(`${API}/api/documents`);

      if (!response.ok) {
        throw new Error("Could not load documents.");
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
      const response = await fetch(`${API}/api/search/status`);

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

  // UPDATED: Safe initialization effect
  useEffect(() => {
    const initializeApp = async () => {
      await checkApi();
      await loadChats();
    };

    initializeApp();
  }, []);

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
      const response = await fetch(`${API}/api/chats`);
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
      const response = await fetch(`${API}/api/chats`, { method: "POST" });
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
      const response = await fetch(`${API}/api/chats`);
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
      const response = await fetch(`${API}/api/chats/${id}`);
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
      const response = await fetch(`${API}/api/chats/${id}`, { method: "DELETE" });
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
      if (file.size > 20 * 1024 * 1024) {
        setNotice(`${file.name} exceeds the 20 MB limit.`);
        continue;
      }

      const form = new FormData();
      form.append("file", file);

      try {
        const response = await fetch(
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
      const response = await fetch(
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
      const response = await fetch(`${API}/api/search`, {
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

    const history = messages
      .filter(
        item =>
          !item.loading &&
          (item.role === "user" || item.role === "assistant")
      )
      .slice(-8)
      .map(({ role, content }) => ({
        role,
        content
      }));

    setMessages(old => [
      ...old,
      {
        role: "user",
        content: value
      },
      {
        role: "assistant",
        content: "",
        loading: true
      }
    ]);

    setTitle(
      value.length > 32
        ? value.slice(0, 32) + "…"
        : value
    );

    setMessage("");
    setIsSending(true);

    try {
      const response = await fetch(`${API}/api/chat`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json"
        },
        body: JSON.stringify({
          question: value,
          chat_id: chatId,
          history,
          top_k: 5
        })
      });

      // UPDATED: Handle invalid JSON responses safely
      let data;

      try {
        data = await response.json();
      } catch {
        throw new Error(
          "Backend returned an invalid response."
        );
      }

      if (!response.ok) {
        throw new Error(
          data.detail || "Could not generate an answer."
        );
      }

      if (data.chat_id && !chatId) setChatId(data.chat_id);
      await loadChatListOnly();

      setMessages(old =>
        old.map((item, index) =>
          index === old.length - 1
            ? {
                role: "assistant",
                content: data.answer,
                mode: data.mode,
                document_sources: data.document_sources || [],
                web_sources: data.web_sources || [],
                suggested_questions: data.suggested_questions || []
              }
            : item
        )
      );

    } catch (error) {
      setMessages(old =>
        old.map((item, index) =>
          index === old.length - 1
            ? {
                role: "assistant",
                content: `I couldn't complete that request. ${error.message}`,
                mode: "error"
              }
            : item
        )
      );

    } finally {
      setIsSending(false);
    }
  }

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
            <div className="avatar">U</div>
            <div>
              <b>Workspace</b>
              <small>Local development</small>
            </div>
            <Settings size={17} />
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
                    : title}
              </b>

              <small>
                {view === "search"
                  ? "Find meaning across your indexed PDFs"
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

        {view === "search" ? (

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

                        {m.loading ? (
                          <p>Thinking… searching your PDFs and preparing an answer…</p>
                        ) : (
                          
                            <div className="markdown-answer">
                              <ReactMarkdown remarkPlugins={[remarkGfm]}>
                                {m.content}
                              </ReactMarkdown>
                            </div>

                        )}
                        
                        {/*
                        {m.mode && (
                          <small className="source">
                            {m.mode === "documents"
                              ? "Answered from your PDFs"
                              : m.mode === "hybrid"
                                ? "PDF context + DuckDuckGo research"
                                : m.mode === "web_and_knowledge"
                                ? (m.web_sources?.length
                                  ? "General knowledge + DuckDuckGo web search"
                                  : "General knowledge · Web search unavailable")
                                : m.mode === "error"
                                  ? "Request error"
                                  : ""}
                          </small>
                        )}

                        {m.document_sources?.length > 0 && (

                          <div className="message-sources">
                            <b>Document sources</b>

                            {m.document_sources.map((source, j) => (
                              <span
                                key={`${source.document_id}-${source.page_number}-${j}`}
                              >
                                {source.filename} · Page {source.page_number}
                              </span>
                            ))}

                          </div>
                        )}

                        {m.web_sources?.length > 0 && (

                          <div className="message-sources">
                            <b>Web sources</b>

                            {m.web_sources.map((source, j) => (
                              <a
                                key={`${source.url}-${j}`}
                                href={source.url}
                                target="_blank"
                                rel="noreferrer"
                              >
                                {source.title} ↗
                              </a>
                            ))}

                          </div>
                        )}

                        */}

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
                    : "Upload PDF · Max 20 MB each"}
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
