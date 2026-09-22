import { type FormEvent, useEffect, useRef, useState } from "react";
import Markdown from "react-markdown";

import { AuthError, askAgent, newSessionId } from "./agent";
import { accessToken, currentSession, signIn, signOut } from "./auth";

type Message = {
  role: "user" | "assistant";
  text: string;
  tools: string[];
  requestId?: string;
  error?: string;
  streaming?: boolean;
};

const EXAMPLES = [
  "What's the biggest news in the market today?",
  "How is NVDA doing today?",
  "How has TSLA moved over the last 3 months?",
];

const TOOL_LABELS: Record<string, string> = {
  search_ticker: "ticker",
  get_stock_price: "latest price",
  get_price_history: "price history",
  get_market_overview: "market movers",
  get_news: "news",
};

type AuthState = { status: "loading" } | { status: "signedOut" } | { status: "signedIn"; email?: string };

export default function App() {
  const [auth, setAuth] = useState<AuthState>({ status: "loading" });
  const [authError, setAuthError] = useState<string>();
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [sessionId, setSessionId] = useState(newSessionId);
  const abortRef = useRef<AbortController | null>(null);
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    currentSession().then((s) =>
      setAuth(s ? { status: "signedIn", email: s.email } : { status: "signedOut" }),
    );
  }, []);

  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "end" });
  }, [messages]);

  function updateLast(change: (m: Message) => Message) {
    setMessages((all) => [...all.slice(0, -1), change(all[all.length - 1])]);
  }

  async function handleSignIn() {
    setAuthError(undefined);
    try {
      const session = await signIn();
      setAuth({ status: "signedIn", email: session.email });
    } catch (e) {
      setAuthError((e as Error).message);
    }
  }

  async function handleSignOut() {
    await signOut();
    setAuth({ status: "signedOut" });
  }

  function newChat() {
    abortRef.current?.abort();
    setMessages([]);
    setSessionId(newSessionId());
  }

  async function ask(question: string) {
    const prompt = question.trim();
    if (!prompt || busy) return;
    setInput("");
    setBusy(true);
    setMessages((all) => [
      ...all,
      { role: "user", text: prompt, tools: [] },
      { role: "assistant", text: "", tools: [], streaming: true },
    ]);

    const controller = new AbortController();
    abortRef.current = controller;
    try {
      const token = await accessToken();
      if (!token) throw new AuthError("Sign in to ask a question.");
      await askAgent(
        prompt,
        sessionId,
        token,
        (event) => {
          if (event.type === "text") updateLast((m) => ({ ...m, text: m.text + event.text }));
          // Text before a tool call is the model narrating ("let me check"),
          // not the answer; drop it so only the final answer remains.
          else if (event.type === "tool")
            updateLast((m) => ({ ...m, text: "", tools: [...m.tools, event.name] }));
          else if (event.type === "done") updateLast((m) => ({ ...m, requestId: event.request_id }));
          else if (event.type === "error")
            updateLast((m) => ({ ...m, error: event.message, requestId: event.request_id }));
        },
        controller.signal,
      );
    } catch (e) {
      if (e instanceof AuthError) {
        await signOut();
        setAuth({ status: "signedOut" });
        setAuthError(e.message);
      }
      if ((e as Error).name !== "AbortError") {
        updateLast((m) => ({ ...m, error: (e as Error).message }));
      }
    } finally {
      updateLast((m) => ({ ...m, streaming: false }));
      setBusy(false);
      abortRef.current = null;
    }
  }

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    void ask(input);
  }

  if (auth.status === "loading") return <main className="panel" aria-busy="true" />;

  if (auth.status === "signedOut") {
    return (
      <main className="panel center">
        <h1>Market Sidebar</h1>
        <p className="muted">Live quotes and price history while you trade.</p>
        <button className="primary" onClick={handleSignIn}>
          Sign in
        </button>
        {authError && <p className="error" role="alert">{authError}</p>}
      </main>
    );
  }

  return (
    <main className="panel">
      <header>
        <h1>Market Sidebar</h1>
        <div className="header-actions">
          <button onClick={newChat} disabled={messages.length === 0}>New chat</button>
          <button onClick={handleSignOut} title={auth.email}>Sign out</button>
        </div>
      </header>

      <section className="messages" aria-live="polite">
        {messages.length === 0 && (
          <div className="empty">
            <p className="muted">Try one of these:</p>
            {EXAMPLES.map((q) => (
              <button key={q} className="example" onClick={() => void ask(q)}>
                {q}
              </button>
            ))}
          </div>
        )}
        {messages.map((m, i) =>
          m.role === "user" ? (
            <div key={i} className="bubble user">{m.text}</div>
          ) : (
            <AssistantMessage key={i} message={m} />
          ),
        )}
        <div ref={endRef} />
      </section>

      <form onSubmit={onSubmit}>
        <label htmlFor="question" className="sr-only">Question</label>
        <input
          id="question"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Ask about a ticker…"
          maxLength={2000}
          autoComplete="off"
        />
        {busy ? (
          <button type="button" onClick={() => abortRef.current?.abort()}>Stop</button>
        ) : (
          <button type="submit" className="primary" disabled={!input.trim()}>Ask</button>
        )}
      </form>

      <footer className="muted">
        Information only, not financial advice. Quotes may be delayed. Each question
        is answered on its own for now.
      </footer>
    </main>
  );
}

function AssistantMessage({ message: m }: { message: Message }) {
  const [copied, setCopied] = useState(false);
  const looking = m.streaming && !m.text && m.tools.length > 0;
  const uniqueTools = [...new Set(m.tools)];

  async function copy() {
    await navigator.clipboard.writeText(m.text);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  }

  return (
    <div className="bubble assistant">
      {m.streaming && !m.text && !looking && <p className="muted">Thinking…</p>}
      {looking && (
        <p className="muted">
          Looking up {uniqueTools.map((t) => TOOL_LABELS[t] ?? t).join(" and ")}…
        </p>
      )}
      {m.text && (
        <Markdown
          components={{
            // Only real web links are clickable; anything else the model
            // shaped like a link (a ticker, a relative path) renders as text.
            a: ({ href, children }) =>
              href?.startsWith("https://") ? (
                <a href={href} target="_blank" rel="noopener noreferrer">{children}</a>
              ) : (
                <span>{children}</span>
              ),
          }}
        >
          {m.text}
        </Markdown>
      )}
      {m.error && <p className="error" role="alert">{m.error}</p>}
      {!m.streaming && (m.text || m.error) && (
        <div className="meta">
          {uniqueTools.length > 0 && (
            <span className="chip" title={uniqueTools.join(", ")}>
              Source: Yahoo Finance ({uniqueTools.map((t) => TOOL_LABELS[t] ?? t).join(", ")})
            </span>
          )}
          {m.text && (
            <button className="link" onClick={copy}>{copied ? "Copied" : "Copy"}</button>
          )}
          {m.requestId && (
            <span className="rid" title={`Request ID ${m.requestId}`}>
              id {m.requestId.slice(0, 8)}
            </span>
          )}
        </div>
      )}
    </div>
  );
}
