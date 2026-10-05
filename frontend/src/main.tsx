import { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { BookkeepingReview, type AccountingChoice } from "./BookkeepingReview";
import type { Doc, AuditEvent } from "./types";
import catalogue from "../../src/cbin/connectors/catalogue.json";
import "./workspace.css";
export type { Doc } from "./types";
type Mode = "Seller" | "Buyer" | "CBIN";
type Session = {
  business: { id: string; name: string; tin: string };
  role: string;
  environment: string;
};
type Source = {
  source_id: string;
  reference: string;
  issued_at: string;
  amount: string;
  currency: string;
  exchange_id?: string;
  exchange_status?: string;
};
type Job = {
  id: string;
  document_id?: string;
  kind: string;
  state: string;
  attempts: number;
  last_error?: string;
};
type ImportProfile = {
  software_id: string;
  profile_id: string;
  evidence_reference: string;
};
const status = (s: string) =>
  s === "delivered" || s === "under_review"
    ? "Awaiting review"
    : s === "rejected_by_buyer"
      ? "Rejected"
      : s === "posted"
        ? "Draft bill created"
        : s.replaceAll("_", " ").replace(/^./, (c) => c.toUpperCase());
const date = (v: string | number) =>
  new Date(typeof v === "number" ? v * 1000 : v).toLocaleString();
const money = (n: number, c: string) =>
  `${c} ${(n / 100).toLocaleString("en", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
function App() {
  const [token, setToken] = useState(""),
    [key, setKey] = useState(""),
    [connecting, setConnecting] = useState(false),
    [session, setSession] = useState<Session | null>(null),
    [mode, setMode] = useState<Mode>("Buyer"),
    [page, setPage] = useState("Documents"),
    [docs, setDocs] = useState<Doc[]>([]),
    [sources, setSources] = useState<Source[]>([]),
    [profiles, setProfiles] = useState<ImportProfile[]>([]),
    [jobs, setJobs] = useState<Job[]>([]),
    [events, setEvents] = useState<AuditEvent[]>([]),
    [selected, setSelected] = useState<Doc | null>(null),
    [query, setQuery] = useState(""),
    [filter, setFilter] = useState("all"),
    [offset, setOffset] = useState(0),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [sourceError, setSourceError] = useState(""),
    [notice, setNotice] = useState(""),
    [profile, setProfile] = useState(""),
    [file, setFile] = useState<File | null>(null),
    [recoveryReason, setRecoveryReason] = useState("");
  async function request<T>(
    path: string,
    body?: unknown,
    signal?: AbortSignal,
  ): Promise<T> {
    const response = await fetch(path, {
      method: body ? "POST" : "GET",
      signal,
      headers: {
        Authorization: `Bearer ${token}`,
        ...(body ? { "Content-Type": "application/json" } : {}),
      },
      ...(body ? { body: JSON.stringify(body) } : {}),
    });
    const result = await response.json();
    if (!response.ok)
      throw Error(
        result.error?.message || `Request failed (${response.status})`,
      );
    return result;
  }
  async function load(signal?: AbortSignal) {
    if (!session) return;
    setBusy(true);
    setError("");
    try {
      const d = await request<{ items: Doc[] }>(
        `/v1/documents?limit=50&offset=${offset}${mode === "CBIN" ? "" : "&direction=" + (mode === "Buyer" ? "incoming" : "outgoing")}`,
        undefined,
        signal,
      );
      if (signal?.aborted) return;
      setDocs(d.items);
      if (page === "Activity") {
        const e = await request<{ items: AuditEvent[] }>(
          "/v1/events?limit=100",
          undefined,
          signal,
        );
        if (!signal?.aborted) setEvents(e.items);
      }
      if (mode === "CBIN") {
        const j = await request<{ items: Job[] }>(
          "/v1/operations/jobs",
          undefined,
          signal,
        );
        if (!signal?.aborted) setJobs(j.items);
      }
      if (mode === "Seller") {
        setSourceError("");
        try {
          const s = await request<{ items: Source[] }>(
            "/v1/seller/source-documents",
            undefined,
            signal,
          );
          if (!signal?.aborted) setSources(s.items);
        } catch (e) {
          if (!signal?.aborted) {
            setSources([]);
            setSourceError((e as Error).message);
          }
        }
        const p = await request<{ items: ImportProfile[] }>(
          "/v1/seller/import-profiles",
          undefined,
          signal,
        );
        if (!signal?.aborted) setProfiles(p.items);
      }
    } catch (e) {
      if (!signal?.aborted) setError((e as Error).message);
    } finally {
      if (!signal?.aborted) setBusy(false);
    }
  }
  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    return () => controller.abort();
  }, [session, mode, page, offset]);
  useEffect(() => {
    if (!session || page !== "Documents" || mode !== "Buyer") return;
    const controller = new AbortController();
    let pending = false;
    const timer = window.setInterval(async () => {
      if (pending || document.hidden) return;
      pending = true;
      try {
        const result = await request<{ items: Doc[] }>(
          `/v1/documents?limit=50&offset=${offset}&direction=incoming`,
          undefined,
          controller.signal,
        );
        if (!controller.signal.aborted) setDocs(result.items);
      } catch {
        // Manual refresh reports errors; a background poll leaves the current view intact.
      } finally {
        pending = false;
      }
    }, 15000);
    return () => {
      window.clearInterval(timer);
      controller.abort();
    };
  }, [session, mode, page, offset]);
  useEffect(() => {
    if (!token) return;
    const controller = new AbortController();
    request<Session>("/v1/me", undefined, controller.signal)
      .then((s) => {
        setSession(s);
        setConnecting(false);
        setMode(
          s.role === "operator"
            ? "CBIN"
            : s.role === "submitter"
              ? "Seller"
              : "Buyer",
        );
      })
      .catch((e) => {
        if (!controller.signal.aborted) {
          setError((e as Error).message);
          setConnecting(false);
          setToken("");
        }
      });
    return () => controller.abort();
  }, [token]);
  useEffect(() => {
    if (!selected) return;
    const before = document.activeElement as HTMLElement | null;
    const dialog = document.querySelector<HTMLElement>('[role="dialog"]');
    const focusable = () =>
      Array.from(
        dialog?.querySelectorAll<HTMLElement>(
          "button:not(:disabled),input:not(:disabled),textarea:not(:disabled),select:not(:disabled)",
        ) || [],
      );
    focusable()[0]?.focus();
    const handler = (e: KeyboardEvent) => {
      if (e.defaultPrevented) return;
      if (e.key === "Escape") {
        setSelected(null);
        return;
      }
      if (e.key === "Tab") {
        const els = focusable(),
          first = els[0],
          last = els[els.length - 1];
        if (e.shiftKey && document.activeElement === first) {
          e.preventDefault();
          last?.focus();
        }
        if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault();
          first?.focus();
        }
      }
    };
    document.addEventListener("keydown", handler);
    return () => {
      document.removeEventListener("keydown", handler);
      before?.focus();
    };
  }, [selected?.id]);
  function disconnect() {
    setToken("");
    setSession(null);
    setDocs([]);
    setJobs([]);
    setSources([]);
    setEvents([]);
    setSelected(null);
    setError("");
    setNotice("");
  }
  async function open(d: Doc) {
    setError("");
    try {
      setSelected(await request<Doc>(`/v1/documents/${d.id}`));
    } catch (e) {
      setError((e as Error).message);
    }
  }
  async function decide(
    accept: boolean,
    body: AccountingChoice | { reason: string },
  ) {
    if (!selected) return;
    await request(
      `/v1/documents/${selected.id}/${accept ? "accept" : "reject"}`,
      body,
    );
    setSelected(null);
    setNotice(
      accept
        ? "Approved. The bill is queued for your accounting system."
        : "Rejected. The reason is recorded for the seller.",
    );
    await load();
  }
  async function send(source: Source) {
    setBusy(true);
    setError("");
    try {
      await request(`/v1/seller/source-documents/${source.source_id}/send`, {});
      setNotice(
        "Invoice sent. Follow its buyer review and accounting outcome below.",
      );
      await load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function importFile() {
    const p = profiles.find(
      (p) => `${p.software_id}:${p.profile_id}` === profile,
    );
    if (!p || !file) return;
    setBusy(true);
    setError("");
    try {
      if (file.size > 1900000)
        throw Error("Choose a JSON export smaller than 1.9 MB.");
      const payload = JSON.parse(await file.text());
      const response = await fetch(
        `/v1/connectors/${p.software_id}/documents`,
        {
          method: "POST",
          headers: {
            Authorization: `Bearer ${token}`,
            "Content-Type": "application/json",
            "Idempotency-Key": `export:${p.software_id}:${await crypto.subtle
              .digest(
                "SHA-256",
                new TextEncoder().encode(JSON.stringify(payload)),
              )
              .then((v) =>
                Array.from(new Uint8Array(v))
                  .map((b) => b.toString(16).padStart(2, "0"))
                  .join(""),
              )}`,
          },
          body: JSON.stringify({
            profile_id: p.profile_id,
            vendor_payload: payload,
          }),
        },
      );
      const result = await response.json();
      if (!response.ok) throw Error(result.error?.message || "Import failed");
      setNotice("Reviewed export submitted. No invoice details were retyped.");
      setFile(null);
      await load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function recover(job: Job) {
    setBusy(true);
    setError("");
    try {
      await request(
        `/v1/operations/jobs/${job.id}/${job.state === "needs_reconciliation" ? "reconcile" : "replay"}`,
        { reason: recoveryReason },
      );
      setRecoveryReason("");
      await load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  const visible = docs.filter(
    (d) =>
      (filter === "all" || d.status === filter) &&
      `${d.payload.external_reference} ${d.payload.seller.cbin_id} ${d.payload.buyer.cbin_id}`
        .toLowerCase()
        .includes(query.toLowerCase()),
  );
  if (!session)
    return (
      <div className="connection-page">
        <header>
          <span className="wordmark">COREBRIDGE</span>
          <span>Business exchange</span>
        </header>
        <main>
          <div className="connection-copy">
            <span className="kicker">CBIN</span>
            <h1>
              A shared document.
              <br />A clear record.
            </h1>
            <p>
              Send invoices from the seller’s system. Review and record
              purchases in the buyer’s system. Follow each decision in CBIN.
            </p>
          </div>
          <form
            className="connection-form"
            onSubmit={(e) => {
              e.preventDefault();
              setError("");
              setConnecting(true);
              setToken(key.trim());
              setKey("");
            }}
          >
            <h2>Connect workspace</h2>
            <p>Use the business credential issued for your test setup.</p>
            <label>
              Business credential
              <input
                type="password"
                autoComplete="off"
                value={key}
                onChange={(e) => setKey(e.target.value)}
                required
              />
            </label>
            {error && (
              <div role="alert" className="error">
                {error}
              </div>
            )}
            <button className="primary" disabled={connecting}>
              {connecting ? "Connecting…" : "Connect securely"}
            </button>
            <small>
              Your credential remains in memory. Refreshing closes the session.
            </small>
          </form>
        </main>
        <footer>Seller · CBIN exchange · Buyer</footer>
      </div>
    );
  return (
    <div className="workspace-app">
      <header className="workspace-header">
        <div>
          <span className="wordmark">COREBRIDGE</span>
          <span className="workspace-title">
            {mode === "CBIN" ? "Exchange operations" : mode + " workspace"}
          </span>
        </div>
        <div className="workspace-account">
          <span>{session.business.name}</span>
          <span className="environment">{session.environment}</span>
          <button onClick={disconnect}>Disconnect</button>
        </div>
      </header>
      <div className="workspace-layout">
        <aside className="workspace-sidebar">
          <div className="workspace-switch">
            {(["Seller", "Buyer", "CBIN"] as Mode[])
              .filter((m) =>
                session.role === "operator"
                  ? m === "CBIN"
                  : session.role === "submitter"
                    ? m === "Seller"
                    : session.role === "reviewer"
                      ? m === "Buyer"
                      : m !== "CBIN",
              )
              .map((m) => (
                <button
                  key={m}
                  className={mode === m ? "current" : ""}
                  onClick={() => {
                    setMode(m);
                    setOffset(0);
                    setQuery("");
                    setSelected(null);
                    setPage("Documents");
                  }}
                >
                  {m}
                </button>
              ))}
          </div>
          <nav>
            {["Documents", "Activity", "Software"].map((p) => (
              <button
                className={page === p ? "current" : ""}
                key={p}
                onClick={() => {
                  setPage(p);
                  setQuery("");
                }}
              >
                {p}
              </button>
            ))}
          </nav>
          <div className="workspace-identity">
            <strong>{session.business.name}</strong>
            <span>{session.business.tin}</span>
            <span>{session.role}</span>
          </div>
        </aside>
        <main className="workspace-content">
          <div className="workspace-heading">
            <div>
              <span className="kicker">
                {mode === "CBIN" ? "CBIN NETWORK" : mode.toUpperCase()}
              </span>
              <h1>
                {page === "Software"
                  ? "Software readiness"
                  : page === "Activity"
                    ? "Exchange activity"
                    : mode === "Seller"
                      ? "Invoices to exchange"
                      : mode === "Buyer"
                        ? "Purchases to record"
                        : "Document exchange"}
              </h1>
              <p>
                {page === "Documents"
                  ? mode === "Seller"
                    ? "Select an invoice from your accounting system and send it to the buyer."
                    : mode === "Buyer"
                      ? "Review the invoice, select your accounting records and approve the bill."
                      : "Follow delivery, buyer review and accounting jobs."
                  : page === "Software"
                    ? "Every catalogue entry is shown with its actual implementation status."
                    : "A record of submissions, decisions and posting outcomes."}
              </p>
            </div>
            <button
              className="secondary"
              aria-label="Refresh"
              disabled={busy}
              onClick={() => void load()}
            >
              Refresh
            </button>
          </div>
          {error && (
            <div role="alert" className="error">
              {error}
            </div>
          )}
          {notice && (
            <div role="status" className="success">
              {notice}
            </div>
          )}
          {page === "Documents" && (
            <>
              {mode === "Seller" && (
                <section className="source-section">
                  <div className="section-title">
                    <h2>From your accounting system</h2>
                    <span>Latest 50 posted sales invoices</span>
                  </div>
                  {sourceError ? (
                    <p className="unavailable">
                      {sourceError}. Configure a source adapter or use an
                      approved export profile below.
                    </p>
                  ) : (
                    <div className="tablewrap">
                      <table>
                        <thead>
                          <tr>
                            <th>Invoice</th>
                            <th>Date</th>
                            <th>Total</th>
                            <th>Exchange</th>
                            <th />
                          </tr>
                        </thead>
                        <tbody>
                          {sources.map((s) => (
                            <tr key={s.source_id}>
                              <td>{s.reference}</td>
                              <td>{s.issued_at}</td>
                              <td>
                                {s.currency} {s.amount}
                              </td>
                              <td>
                                {s.exchange_status
                                  ? status(s.exchange_status)
                                  : "Not sent"}
                              </td>
                              <td>
                                <button
                                  className="text-action"
                                  disabled={busy || Boolean(s.exchange_id)}
                                  onClick={() => void send(s)}
                                >
                                  Send invoice
                                </button>
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                      {!sources.length && (
                        <p className="empty">No source invoices loaded.</p>
                      )}
                    </div>
                  )}
                  {profiles.length > 0 && (
                    <div className="export-import">
                      <h3>Send a reviewed software export</h3>
                      <label>
                        Export profile
                        <select
                          value={profile}
                          onChange={(e) => setProfile(e.target.value)}
                        >
                          <option value="">Select a configured profile</option>
                          {profiles.map((p) => (
                            <option
                              key={`${p.software_id}:${p.profile_id}`}
                              value={`${p.software_id}:${p.profile_id}`}
                            >
                              {
                                catalogue.items.find(
                                  (c) => c.id === p.software_id,
                                )?.name
                              }{" "}
                              · {p.profile_id}
                            </option>
                          ))}
                        </select>
                      </label>
                      <label>
                        JSON export
                        <input
                          type="file"
                          accept="application/json,.json"
                          onChange={(e) => setFile(e.target.files?.[0] || null)}
                        />
                      </label>
                      <button
                        className="secondary"
                        disabled={busy || !profile || !file}
                        onClick={() => void importFile()}
                      >
                        Submit export
                      </button>
                    </div>
                  )}
                </section>
              )}
              <section className="document-section">
                <div className="section-title">
                  <h2>
                    {mode === "Seller"
                      ? "Sent documents"
                      : mode === "Buyer"
                        ? "Received documents"
                        : "Network documents"}
                  </h2>
                  <span>Current page · {docs.length} records</span>
                </div>
                <div className="document-toolbar">
                  <input
                    aria-label="Search documents"
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                    placeholder="Find an invoice or business"
                  />
                  <select
                    aria-label="Document status"
                    value={filter}
                    onChange={(e) => setFilter(e.target.value)}
                  >
                    <option value="all">All statuses</option>
                    {[
                      "queued",
                      "delivered",
                      "accepted",
                      "posted",
                      "rejected_by_buyer",
                    ].map((s) => (
                      <option key={s} value={s}>
                        {status(s)}
                      </option>
                    ))}
                  </select>
                </div>
                <div className="tablewrap">
                  <table>
                    <thead>
                      <tr>
                        <th>Invoice</th>
                        <th>{mode === "Seller" ? "Buyer" : "Supplier"}</th>
                        <th>Issued</th>
                        <th>Total</th>
                        <th>Status</th>
                        <th />
                      </tr>
                    </thead>
                    <tbody>
                      {visible.map((d) => (
                        <tr key={d.id}>
                          <td>
                            <button
                              className="doclink"
                              onClick={() => void open(d)}
                            >
                              {d.payload.external_reference}
                            </button>
                          </td>
                          <td>
                            {mode === "Seller"
                              ? d.buyer_name || d.payload.buyer.cbin_id
                              : d.seller_name || d.payload.seller.cbin_id}
                          </td>
                          <td>{d.payload.issued_at}</td>
                          <td>
                            {money(
                              d.payload.totals.grand_total_minor,
                              d.payload.currency,
                            )}
                          </td>
                          <td>
                            <span className={`status ${d.status}`}>
                              {status(d.status)}
                            </span>
                          </td>
                          <td>
                            <button
                              className="text-action"
                              onClick={() => void open(d)}
                            >
                              {mode === "Buyer" &&
                              ["delivered", "under_review"].includes(d.status)
                                ? "Review purchase"
                                : "View record"}
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  {!visible.length && (
                    <p className="empty">
                      {busy ? "Loading…" : "No documents in this view."}
                    </p>
                  )}
                </div>
                <div className="pagination">
                  <span>50 records per page</span>
                  <button
                    disabled={offset === 0 || busy}
                    onClick={() => setOffset(Math.max(0, offset - 50))}
                  >
                    Previous
                  </button>
                  <button
                    disabled={docs.length < 50 || busy}
                    onClick={() => setOffset(offset + 50)}
                  >
                    Next
                  </button>
                </div>
              </section>
              {mode === "CBIN" && (
                <section className="jobs-section">
                  <div className="section-title">
                    <h2>Delivery and accounting jobs</h2>
                    <span>Lookup-only recovery for uncertain writes</span>
                  </div>
                  <label>
                    Recovery reason
                    <input
                      value={recoveryReason}
                      onChange={(e) => setRecoveryReason(e.target.value)}
                      placeholder="Required for a recovery action"
                    />
                  </label>
                  <div className="tablewrap">
                    <table>
                      <thead>
                        <tr>
                          <th>Job</th>
                          <th>Type</th>
                          <th>State</th>
                          <th>Attempts</th>
                          <th>Action</th>
                        </tr>
                      </thead>
                      <tbody>
                        {jobs.map((j) => (
                          <tr key={j.id}>
                            <td>
                              {j.id.slice(0, 12)}
                              <small>{j.last_error}</small>
                            </td>
                            <td>{j.kind}</td>
                            <td>{status(j.state)}</td>
                            <td>{j.attempts}</td>
                            <td>
                              {["dead_letter", "needs_reconciliation"].includes(
                                j.state,
                              ) && (
                                <button
                                  disabled={
                                    busy || recoveryReason.trim().length < 3
                                  }
                                  onClick={() => void recover(j)}
                                >
                                  {j.state === "dead_letter"
                                    ? "Replay"
                                    : "Reconcile"}
                                </button>
                              )}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </section>
              )}
            </>
          )}
          {page === "Activity" && (
            <section className="activity-list">
              {events.map((e) => (
                <article key={e.id}>
                  <strong>{e.kind}</strong>
                  <span>{e.document_id || "Workspace event"}</span>
                  <time>{date(e.created_at)}</time>
                </article>
              ))}
              {!events.length && (
                <p className="empty">No visible events loaded.</p>
              )}
              <p className="scope-note">
                First 100 visible events. Further history is available through
                the cursor-paginated API.
              </p>
            </section>
          )}
          {page === "Software" && (
            <>
              <div className="software-notice">
                34 listed products and editions. Protocol compatibility and
                vendor-certified integration are separate checks.
              </div>
              <div className="tablewrap">
                <table>
                  <thead>
                    <tr>
                      <th>Software</th>
                      <th>Category</th>
                      <th>Capture</th>
                      <th>Buyer posting</th>
                      <th>Real vendor test</th>
                    </tr>
                  </thead>
                  <tbody>
                    {catalogue.items.map((c) => (
                      <tr key={c.id}>
                        <td>{c.name}</td>
                        <td>{c.category}</td>
                        <td>
                          {c.id === "odoo18"
                            ? "Test adapter"
                            : status(c.cbin_capture_status)}
                        </td>
                        <td>
                          {c.id === "odoo18"
                            ? "Test adapter"
                            : status(c.cbin_posting_status)}
                        </td>
                        <td>{c.live_verified ? "Verified" : "Pending"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </main>
      </div>
      {selected && (
        <div className="review-overlay">
          <section
            className="document-dialog"
            role="dialog"
            aria-modal="true"
            aria-labelledby="document-title"
          >
            <header>
              <div>
                <span className="kicker">
                  {mode === "Buyer" ? "PURCHASE REVIEW" : "EXCHANGE RECORD"}
                </span>
                <h2 id="document-title">
                  {selected.payload.external_reference}
                </h2>
              </div>
              <button
                aria-label="Close document"
                onClick={() => setSelected(null)}
              >
                Close
              </button>
            </header>
            <div className="document-columns">
              <article className="invoice-paper">
                <div className="invoice-heading">
                  <h3>
                    {selected.payload.document_type === "CREDIT_NOTE"
                      ? "Credit note"
                      : "Invoice"}
                  </h3>
                  <span className={`status ${selected.status}`}>
                    {status(selected.status)}
                  </span>
                </div>
                <div className="invoice-parties">
                  <div>
                    <small>SELLER</small>
                    <strong>
                      {selected.seller_name || selected.payload.seller.cbin_id}
                    </strong>
                    <span>{selected.payload.seller.tin}</span>
                  </div>
                  <div>
                    <small>BUYER</small>
                    <strong>
                      {selected.buyer_name || selected.payload.buyer.cbin_id}
                    </strong>
                    <span>{selected.payload.buyer.tin}</span>
                  </div>
                </div>
                <p>Issued {selected.payload.issued_at}</p>
                <div className="tablewrap">
                  <table>
                    <thead>
                      <tr>
                        <th>Description</th>
                        <th>Quantity</th>
                        <th>Unit price</th>
                      </tr>
                    </thead>
                    <tbody>
                      {selected.payload.line_items.map((l, i) => (
                        <tr key={i}>
                          <td>
                            {l.description}
                            <small>{l.item_code}</small>
                          </td>
                          <td>{l.quantity}</td>
                          <td>
                            {money(
                              l.unit_price_minor,
                              selected.payload.currency,
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <dl className="invoice-totals">
                  <dt>Subtotal</dt>
                  <dd>
                    {money(
                      selected.payload.totals.subtotal_minor,
                      selected.payload.currency,
                    )}
                  </dd>
                  <dt>Tax</dt>
                  <dd>
                    {money(
                      selected.payload.totals.tax_minor,
                      selected.payload.currency,
                    )}
                  </dd>
                  <dt>Total</dt>
                  <dd>
                    {money(
                      selected.payload.totals.grand_total_minor,
                      selected.payload.currency,
                    )}
                  </dd>
                </dl>
                <p className="scope-note">
                  Fiscal evidence:{" "}
                  {selected.fiscal_verification || "not supplied"}. Approval
                  does not verify fiscal compliance.
                </p>
                {selected.posted_reference && (
                  <p className="posting-reference">
                    Accounting reference: {selected.posted_reference}
                  </p>
                )}
              </article>
              <div className="record-review">
                {mode === "Buyer" &&
                ["delivered", "under_review"].includes(selected.status) ? (
                  <BookkeepingReview
                    document={selected}
                    demo={false}
                    request={request}
                    onApprove={(choice) => decide(true, choice)}
                    onReject={(reason) => decide(false, { reason })}
                  />
                ) : (
                  <section>
                    <h3>Current outcome</h3>
                    <p>{status(selected.status)}</p>
                    {selected.timeline?.map((e) => (
                      <div className="record-event" key={e.id}>
                        <strong>{e.kind}</strong>
                        <time>{date(e.created_at)}</time>
                        {Boolean(e.data) && (
                          <p>
                            {typeof e.data === "object" &&
                            e.data !== null &&
                            "reason" in e.data
                              ? String(e.data.reason)
                              : ""}
                          </p>
                        )}
                      </div>
                    ))}
                  </section>
                )}
              </div>
            </div>
          </section>
        </div>
      )}
    </div>
  );
}
createRoot(document.getElementById("root")!).render(<App />);
