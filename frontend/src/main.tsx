import React, { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  ArrowDownLeft,
  ArrowUpRight,
  ArrowRight,
  Search,
  ChevronDown,
  LayoutDashboard,
  Files,
  Puzzle,
  Activity,
  Settings,
  ShieldCheck,
  X,
  Check,
  RefreshCw,
  CircleHelp,
  Command,
  LogOut,
} from "lucide-react";
import "./style.css";
import catalogue from "../../src/cbin/connectors/catalogue.json";

type Doc = {
  id: string;
  status: string;
  created_at: string;
  posted_reference?: string;
  fiscal_verification?: string;
  payload: {
    document_type: string;
    external_reference: string;
    currency: string;
    issued_at: string;
    seller: { cbin_id: string; tin: string };
    buyer: { cbin_id: string; tin: string };
    line_items: {
      item_code: string;
      description: string;
      quantity: string;
      unit_price_minor: number;
      tax_rate: string;
    }[];
    totals: {
      subtotal_minor: number;
      tax_minor: number;
      grand_total_minor: number;
    };
  };
  timeline?: Event[];
};
type Event = {
  id: number;
  kind: string;
  created_at: string;
  document_id?: string;
  data?: unknown;
};
type Connector = {
  id: string;
  name: string;
  category: string;
  cbin_capture_status: string;
  cbin_posting_status: string;
  live_verified: boolean;
};
const sampleNames = [
  "Mavambo Supplies",
  "Northline Logistics",
  "Acacia Trading",
  "Harare Office Co.",
  "Greenfield Foods",
  "Kopano Services",
];
const samples: Doc[] = sampleNames.map((name, i) => ({
  id: `sample-${i}`,
  status: [
    "delivered",
    "posted",
    "delivered",
    "rejected_by_buyer",
    "accepted",
    "posted",
  ][i],
  created_at: "2026-10-04T08:30:00Z",
  fiscal_verification: "not_verified",
  posted_reference: i === 1 || i === 5 ? "sandbox:example" : undefined,
  payload: {
    document_type: "B2B_INVOICE",
    external_reference: `INV-2026-${1048 - i}`,
    currency: "USD",
    issued_at: "2026-10-04",
    seller: { cbin_id: name, tin: "Sample TIN" },
    buyer: { cbin_id: "Your business", tin: "Sample TIN" },
    line_items: [
      {
        item_code: "ITEM-01",
        description: [
          "Office equipment",
          "Logistics services",
          "Packaging materials",
          "Office supplies",
          "Food supplies",
          "Professional services",
        ][i],
        quantity: "1",
        unit_price_minor: [245000, 78000, 132000, 46800, 318000, 92000][i],
        tax_rate: "0",
      },
    ],
    totals: {
      subtotal_minor: [245000, 78000, 132000, 46800, 318000, 92000][i],
      tax_minor: 0,
      grand_total_minor: [245000, 78000, 132000, 46800, 318000, 92000][i],
    },
  },
}));
const label = (s: string) =>
  s === "delivered" || s === "under_review"
    ? "Pending review"
    : s === "rejected_by_buyer"
      ? "Rejected"
      : s
          .toLowerCase()
          .replaceAll("_", " ")
          .replace(/^./, (c) => c.toUpperCase());
const money = (n: number, c: string) =>
  ["USD", "ZAR", "EUR", "GBP", "ZWG"].includes(c)
    ? new Intl.NumberFormat("en", { style: "currency", currency: c }).format(
        n / 100,
      )
    : `${n.toLocaleString()} minor units (${c})`;
type Job = {
  id: string;
  kind: string;
  state: string;
  attempts: number;
  last_error?: string;
  document_id?: string;
};
function App() {
  const [jobs, setJobs] = useState<Job[]>([]),
    [jobReason, setJobReason] = useState("");
  const [page, setPage] = useState("Overview"),
    [demo, setDemo] = useState(true),
    [token, setToken] = useState(""),
    [draft, setDraft] = useState(""),
    [login, setLogin] = useState(false),
    [docs, setDocs] = useState<Doc[]>(samples),
    [events, setEvents] = useState<Event[]>([]),
    [connectors, setConnectors] = useState<Connector[]>(catalogue.items),
    [query, setQuery] = useState(""),
    [filter, setFilter] = useState("All documents"),
    [selected, setSelected] = useState<Doc | null>(null),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [offset, setOffset] = useState(0),
    [reason, setReason] = useState(""),
    [supplier, setSupplier] = useState(""),
    [account, setAccount] = useState(""),
    [sku, setSku] = useState<Record<string, string>>({}),
    [tax, setTax] = useState<Record<string, string>>({});
  async function api<T>(
    path: string,
    body?: unknown,
    signal?: AbortSignal,
  ): Promise<T> {
    const r = await fetch(path, {
      signal,
      method: body ? "POST" : "GET",
      headers: {
        Authorization: `Bearer ${token}`,
        ...(body ? { "Content-Type": "application/json" } : {}),
      },
      ...(body ? { body: JSON.stringify(body) } : {}),
    });
    const data = await r.json();
    if (!r.ok)
      throw new Error(data.error?.message || `Request failed (${r.status})`);
    return data;
  }
  async function load(signal?: AbortSignal) {
    if (demo) return;
    setBusy(true);
    setError("");
    try {
      const [d, e, c] = await Promise.all([
        api<{ items: Doc[] }>(
          `/v1/documents?limit=50&offset=${offset}`,
          undefined,
          signal,
        ),
        api<{ items: Event[] }>("/v1/events?limit=100", undefined, signal),
        api<{ items: Connector[] }>("/v1/connectors", undefined, signal),
      ]);
      if (signal?.aborted) return;
      setDocs(d.items);
      setEvents(e.items);
      setConnectors(c.items);
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
  }, [demo, token, offset]);
  useEffect(() => {
    if (!login && !selected) return;
    const previous = document.activeElement as HTMLElement | null;
    const dialog = document.querySelector<HTMLElement>('[role="dialog"]');
    const focusables = () =>
      Array.from(
        dialog?.querySelectorAll<HTMLElement>(
          'button:not(:disabled),input,textarea,[tabindex="0"]',
        ) || [],
      );
    focusables()[0]?.focus();
    const key = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setLogin(false);
        setSelected(null);
      }
      if (e.key === "Tab") {
        const els = focusables(),
          first = els[0],
          last = els[els.length - 1];
        if (e.shiftKey && document.activeElement === first) {
          e.preventDefault();
          last?.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault();
          first?.focus();
        }
      }
    };
    document.addEventListener("keydown", key);
    return () => {
      document.removeEventListener("keydown", key);
      previous?.focus();
    };
  }, [login, selected?.id]);
  useEffect(() => {
    if (page !== "Operations" || demo) return;
    const controller = new AbortController();
    setError("");
    api<{ items: Job[] }>("/v1/operations/jobs", undefined, controller.signal)
      .then((d) => setJobs(d.items))
      .catch((e) => {
        if (!controller.signal.aborted) setError((e as Error).message);
      });
    return () => controller.abort();
  }, [page, demo, token]);
  async function recover(job: Job) {
    setBusy(true);
    setError("");
    try {
      await api(
        `/v1/operations/jobs/${job.id}/${job.state === "needs_reconciliation" ? "reconcile" : "replay"}`,
        { reason: jobReason },
      );
      const d = await api<{ items: Job[] }>("/v1/operations/jobs");
      setJobs(d.items);
      setJobReason("");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function open(d: Doc) {
    setError("");
    setSupplier("");
    setAccount("");
    setSku(
      Object.fromEntries(d.payload.line_items.map((l) => [l.item_code, ""])),
    );
    setTax(
      Object.fromEntries(d.payload.line_items.map((l) => [l.tax_rate, ""])),
    );
    setReason("");
    if (demo) {
      setSelected(d);
      return;
    }
    try {
      setSelected(await api<Doc>(`/v1/documents/${d.id}`));
    } catch (e) {
      setError((e as Error).message);
    }
  }
  async function decide(accept: boolean) {
    if (!selected) return;
    setBusy(true);
    setError("");
    try {
      if (demo) {
        const updated = {
          ...selected,
          status: accept ? "accepted" : "rejected_by_buyer",
        };
        setDocs(docs.map((d) => (d.id === selected.id ? updated : d)));
        setSelected(updated);
      } else {
        let body: unknown;
        if (accept) {
          if (Object.values(sku).some((v) => !v.trim()))
            throw new Error("Map every item to an ERP item code.");
          body = {
            supplier_reference: supplier,
            account_reference: account,
            sku_mapping: sku,
            tax_mapping: tax,
          };
        } else body = { reason };
        await api(
          `/v1/documents/${selected.id}/${accept ? "accept" : "reject"}`,
          body,
        );
        setSelected(null);
        await load();
      }
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  const pending = docs.filter((d) =>
      ["delivered", "under_review"].includes(d.status),
    ),
    posted = docs.filter((d) => d.status === "posted"),
    rejected = docs.filter((d) => d.status === "rejected_by_buyer");
  const filtered = docs.filter(
    (d) =>
      (filter === "All documents" || label(d.status) === filter) &&
      `${d.payload.external_reference} ${d.payload.seller.cbin_id}`
        .toLowerCase()
        .includes(query.toLowerCase()),
  );
  const nav = [
    { name: "Overview", icon: LayoutDashboard },
    { name: "Documents", icon: Files },
    { name: "Integrations", icon: Puzzle },
    { name: "Activity", icon: Activity },
    { name: "Operations", icon: RefreshCw },
    { name: "Settings", icon: Settings },
  ];
  return (
    <div className="app">
      <aside className="sidebar">
        <a
          className="brand"
          href="#"
          onClick={(e) => {
            e.preventDefault();
            setPage("Overview");
          }}
        >
          <span className="brandmark">
            <Command size={22} />
          </span>
          corebridge<span className="branddot">.</span>
        </a>
        <button className="workspace" onClick={() => setLogin(true)}>
          <span className="workspace-avatar">CB</span>
          <span>
            <strong>{demo ? "Demo business" : "Business workspace"}</strong>
            <small>{demo ? "Demo environment" : "Authenticated session"}</small>
          </span>
          <ChevronDown size={14} />
        </button>
        <div className="navlabel">WORKSPACE</div>
        <nav>
          {nav.map((n) => (
            <button
              key={n.name}
              className={page === n.name ? "nav active" : "nav"}
              onClick={() => {
                setPage(n.name);
                setQuery("");
              }}
            >
              <n.icon size={18} />
              {n.name}
              {n.name === "Documents" && (
                <span className="navcount">{docs.length}</span>
              )}
            </button>
          ))}
        </nav>
        <div className="sidebottom">
          <div className="helpbox">
            <ShieldCheck size={22} />
            <strong>A clear record, every step.</strong>
            <p>
              Follow each document from submission to a confirmed ERP outcome.
            </p>
            <button onClick={() => setPage("Activity")}>
              View audit activity <ArrowRight size={14} />
            </button>
          </div>
          <button className="nav" onClick={() => setLogin(true)}>
            <CircleHelp size={18} />
            Connect your workspace
          </button>
          <div className="profile">
            <span className="avatar">{demo ? "DM" : "CB"}</span>
            <span>
              <strong>{demo ? "Demo workspace" : "Connected workspace"}</strong>
              <small>
                {demo ? "Sample data only" : "Credential held in memory"}
              </small>
            </span>
            <button
              aria-label="Disconnect"
              onClick={() => {
                setToken("");
                setDemo(true);
                setDocs(samples);
                setEvents([]);
                setConnectors(catalogue.items);
              }}
            >
              <LogOut size={16} />
            </button>
          </div>
        </div>
      </aside>
      <div className="main">
        <header className="topbar">
          <div className="breadcrumb">
            Workspace <span>/</span>
            <strong>{page}</strong>
          </div>
          <div className="topactions">
            <span className="environment">
              <i />
              {demo ? "Demo preview" : "API session"}
            </span>
            <button
              className="iconbutton"
              aria-label="Refresh"
              onClick={() => (demo ? setDocs(samples) : void load())}
            >
              <RefreshCw size={17} />
            </button>
            <span className="avatar small">{demo ? "DM" : "CB"}</span>
          </div>
        </header>
        <main>
          <div className="pagehead">
            <div className="eyebrow">BUSINESS INTEROPERABILITY NETWORK</div>
            <div className="titleline">
              <div>
                <h1>
                  {page === "Overview" ? "Your business, in sync." : page}
                </h1>
                <p>
                  {page === "Overview"
                    ? "A clear view of the documents moving through your business."
                    : page === "Integrations"
                      ? "Explore the software catalogue and understand adapter readiness."
                      : page === "Activity"
                        ? "Trace the events behind your documents."
                        : "Manage your business exchange workspace."}
                </p>
              </div>
              <button className="primary" onClick={() => setLogin(true)}>
                {demo ? "Connect workspace" : "Change credential"}
                <ArrowUpRight size={16} />
              </button>
            </div>
          </div>
          <div className="notice">
            <span className="notice-dot" />
            <strong>
              {demo ? "You’re exploring a product demo." : "Live API session."}
            </strong>
            <span>
              {demo
                ? "All names, amounts and actions here are sample data."
                : "This view is scoped to your credential’s business and environment."}
            </span>
            {demo && (
              <button onClick={() => setLogin(true)}>
                Use your data <ArrowRight size={14} />
              </button>
            )}
          </div>
          {error && (
            <div role="alert" className="error">
              {error}
            </div>
          )}
          {(page === "Overview" || page === "Documents") && (
            <>
              <div className="metrics">
                {[
                  {
                    name: "Documents in view",
                    value: docs.length,
                    icon: Files,
                    note: "Current page of exchange records",
                    color: "neutral",
                  },
                  {
                    name: "Awaiting review",
                    value: pending.length,
                    icon: ArrowDownLeft,
                    note: "Ready for a buyer decision",
                    color: "amber",
                  },
                  {
                    name: "Posted to ERP",
                    value: posted.length,
                    icon: Check,
                    note: demo
                      ? "Simulated posting outcomes"
                      : "Confirmed posting outcomes",
                    color: "green",
                  },
                  {
                    name: "Rejected",
                    value: rejected.length,
                    icon: X,
                    note: "Returned with a reason",
                    color: "red",
                  },
                ].map((m) => (
                  <div className="metric" key={m.name}>
                    <div className="metric-top">
                      <span>{m.name}</span>
                      <span className={`metric-icon ${m.color}`}>
                        <m.icon size={17} />
                      </span>
                    </div>
                    <strong>{String(m.value).padStart(2, "0")}</strong>
                    <small>{m.note}</small>
                  </div>
                ))}
              </div>
              <div
                className={
                  page === "Documents"
                    ? "contentgrid documentswide"
                    : "contentgrid"
                }
              >
                <section className="panel documents">
                  <div className="panelhead">
                    <div>
                      <h2>
                        {page === "Overview"
                          ? "Recent documents"
                          : "Document inbox"}
                      </h2>
                      <p>Review, approve and follow the outcome.</p>
                    </div>
                    <span className="pill muted">{docs.length} records</span>
                  </div>
                  <div className="tabletools">
                    <div className="tabs">
                      {[
                        "All documents",
                        "Pending review",
                        "Posted",
                        "Rejected",
                      ].map((f) => (
                        <button
                          className={filter === f ? "selected" : ""}
                          key={f}
                          onClick={() => setFilter(f)}
                        >
                          {f}
                        </button>
                      ))}
                    </div>
                    <label className="search">
                      <Search size={16} />
                      <input
                        aria-label="Search documents"
                        placeholder="Search documents…"
                        value={query}
                        onChange={(e) => setQuery(e.target.value)}
                      />
                    </label>
                  </div>
                  <div className="tablewrap">
                    <table>
                      <thead>
                        <tr>
                          <th>Document / business</th>
                          <th>Status</th>
                          <th>Amount</th>
                          <th>Issued</th>
                          <th />
                        </tr>
                      </thead>
                      <tbody>
                        {filtered.map((d, i) => (
                          <tr key={d.id} onClick={() => void open(d)}>
                            <td>
                              <button
                                className="doclink"
                                onClick={(e) => {
                                  e.stopPropagation();
                                  void open(d);
                                }}
                              >
                                <span className={`business-icon tone${i % 3}`}>
                                  <Files size={17} />
                                </span>
                                <span>
                                  <strong>
                                    {d.payload.external_reference}
                                  </strong>
                                  <small>{d.payload.seller.cbin_id}</small>
                                </span>
                              </button>
                            </td>
                            <td>
                              <span
                                className={`pill ${d.status.toLowerCase()}`}
                              >
                                <i />
                                {label(d.status)}
                              </span>
                            </td>
                            <td className="amount">
                              {money(
                                d.payload.totals.grand_total_minor,
                                d.payload.currency,
                              )}
                            </td>
                            <td className="date">
                              {d.payload.issued_at.slice(0, 10)}
                            </td>
                            <td>
                              <ArrowRight size={16} />
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                    {filtered.length === 0 && (
                      <div className="empty">
                        <Files size={28} />
                        <h3>
                          {busy
                            ? "Loading documents…"
                            : "No documents in this view"}
                        </h3>
                        <p>
                          Try another filter or submit a document through the
                          API.
                        </p>
                      </div>
                    )}
                  </div>
                  <div className="tablefooter">
                    <span>
                      {demo
                        ? "Sample records"
                        : "50 records maximum per page · counts cover this page"}
                    </span>
                    {!demo && (
                      <div>
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
                    )}
                    <span>
                      Open a document to review <ArrowRight size={13} />
                    </span>
                  </div>
                </section>
                {page === "Overview" && (
                  <aside className="rail">
                    <section className="panel workflow">
                      <div className="panelhead">
                        <h2>The exchange journey</h2>
                        <ShieldCheck size={18} />
                      </div>
                      <div className="journey">
                        {[
                          ["01", "Capture", "Submit a canonical document"],
                          ["02", "Review", "Buyer checks and maps items"],
                          ["03", "Post", "Queue sends the accepted record"],
                          [
                            "04",
                            "Confirm",
                            "ERP outcome enters the audit trail",
                          ],
                        ].map(([n, t, p]) => (
                          <div key={n}>
                            <span>{n}</span>
                            <section>
                              <strong>{t}</strong>
                              <p>{p}</p>
                            </section>
                          </div>
                        ))}
                      </div>
                      <div className="workflowfoot">
                        Fiscal status is tracked separately from document
                        approval.
                      </div>
                    </section>
                    <section className="integrationcallout">
                      <div className="integrationlogos">
                        <span>Z</span>
                        <span>o</span>
                        <span>X</span>
                      </div>
                      <h2>Your tools. One exchange.</h2>
                      <p>
                        Browse 34 Fiscal Harmony catalogue entries, with honest
                        capture and posting readiness.
                      </p>
                      <button onClick={() => setPage("Integrations")}>
                        Explore integrations <ArrowRight size={16} />
                      </button>
                    </section>
                  </aside>
                )}
              </div>
            </>
          )}
          {page === "Integrations" && (
            <>
              <div className="sectionintro">
                <h2>Software catalogue</h2>
                <p>
                  Catalogue inclusion does not mean a live connection. Each
                  adapter still needs credentials, mappings and vendor testing.
                </p>
              </div>
              {
                <>
                  <label className="search catalogue-search">
                    <Search size={16} />
                    <input
                      placeholder="Find software…"
                      aria-label="Find software"
                      value={query}
                      onChange={(e) => setQuery(e.target.value)}
                    />
                  </label>
                  <div className="connectorgrid">
                    {connectors
                      .filter((c) =>
                        c.name.toLowerCase().includes(query.toLowerCase()),
                      )
                      .map((c) => (
                        <article className="panel connector" key={c.id}>
                          <span className="connectorletter">
                            {c.name.slice(0, 1)}
                          </span>
                          <h2>{c.name}</h2>
                          <p>{c.category}</p>
                          <dl>
                            <dt>Capture</dt>
                            <dd>{label(c.cbin_capture_status)}</dd>
                            <dt>Posting</dt>
                            <dd>{label(c.cbin_posting_status)}</dd>
                          </dl>
                          <span className="pill muted">
                            {c.live_verified
                              ? "Live verified"
                              : "Vendor validation required"}
                          </span>
                        </article>
                      ))}
                  </div>
                </>
              }
            </>
          )}
          {page === "Activity" && (
            <section className="panel">
              <div className="panelhead">
                <h2>Audit activity</h2>
                <span className="pill muted">
                  {demo ? "Sample timeline" : "First 100 visible events"}
                </span>
              </div>
              {(demo
                ? [
                    {
                      id: 1,
                      kind: "document.submitted",
                      created_at: "2026-10-04T08:30:00Z",
                      document_id: "INV-2026-1048",
                    },
                    {
                      id: 2,
                      kind: "document.accepted",
                      created_at: "2026-10-04T08:35:00Z",
                      document_id: "INV-2026-1047",
                    },
                    {
                      id: 3,
                      kind: "posting.confirmed",
                      created_at: "2026-10-04T08:36:00Z",
                      document_id: "INV-2026-1047",
                    },
                  ]
                : events
              ).map((e) => (
                <div className="event" key={e.id}>
                  <span className="eventicon">
                    <Activity size={17} />
                  </span>
                  <div>
                    <strong>{e.kind.replaceAll(".", " · ")}</strong>
                    <small>{e.document_id || "Workspace event"}</small>
                  </div>
                  <time>{new Date(e.created_at).toLocaleString()}</time>
                </div>
              ))}
              {!demo && !events.length && (
                <div className="empty">No events loaded.</div>
              )}
            </section>
          )}
          {page === "Operations" && (
            <section className="panel">
              <div className="panelhead">
                <div>
                  <h2>Delivery & posting jobs</h2>
                  <p>
                    Operator credentials are required. Uncertain postings use
                    lookup-only reconciliation.
                  </p>
                </div>
                <span className="pill muted">
                  {demo ? "Demo mode" : "First 50 jobs"}
                </span>
              </div>
              {demo ? (
                <div className="empty">
                  <RefreshCw size={26} />
                  <h3>Connect as an operator</h3>
                  <p>
                    View job attempts and recover eligible failed deliveries.
                  </p>
                  <button className="primary" onClick={() => setLogin(true)}>
                    Connect workspace
                  </button>
                </div>
              ) : (
                <>
                  <div className="operationreason">
                    <label>
                      Recovery reason
                      <input
                        value={jobReason}
                        onChange={(e) => setJobReason(e.target.value)}
                        placeholder="Describe why this job should be recovered"
                      />
                    </label>
                  </div>
                  <div className="tablewrap">
                    <table>
                      <thead>
                        <tr>
                          <th>Job / document</th>
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
                              {j.id.slice(0, 16)}
                              <small>{j.document_id || "Workspace job"}</small>
                              {j.last_error && <small>{j.last_error}</small>}
                            </td>
                            <td>{label(j.kind)}</td>
                            <td>
                              <span className="pill muted">
                                {label(j.state)}
                              </span>
                            </td>
                            <td>{j.attempts}</td>
                            <td>
                              {["dead_letter", "needs_reconciliation"].includes(
                                j.state,
                              ) ? (
                                <button
                                  disabled={busy || !jobReason.trim()}
                                  onClick={() => void recover(j)}
                                >
                                  {j.state === "needs_reconciliation"
                                    ? "Reconcile"
                                    : "Replay"}
                                </button>
                              ) : (
                                "—"
                              )}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  {!jobs.length && !error && (
                    <div className="empty">No jobs loaded.</div>
                  )}
                </>
              )}
            </section>
          )}
          {page === "Settings" && (
            <section className="panel settings">
              <h2>Workspace connection</h2>
              <p>
                Use a business credential issued by your administrator. Your key
                stays in memory and clears when you refresh or disconnect.
              </p>
              <dl>
                <dt>Mode</dt>
                <dd>{demo ? "Demonstration" : "Authenticated API"}</dd>
                <dt>Access scope</dt>
                <dd>Enforced by the server credential</dd>
                <dt>Frontend</dt>
                <dd>React · TypeScript</dd>
              </dl>
              <button className="primary" onClick={() => setLogin(true)}>
                Connect workspace <ArrowRight size={16} />
              </button>
            </section>
          )}
          <footer className="footer">
            <span>COREBRIDGE · BUSINESS EXCHANGE</span>
            <span>Built for clarity. Designed for trust.</span>
          </footer>
        </main>
      </div>
      {login && (
        <div className="overlay">
          <section
            className="modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="connection-title"
          >
            <button
              className="close"
              aria-label="Close connection"
              onClick={() => setLogin(false)}
            >
              <X size={20} />
            </button>
            <div className="modalicon">
              <ShieldCheck size={24} />
            </div>
            <h2 id="connection-title">Connect your workspace</h2>
            <p>
              Enter a CBIN credential to load your business documents. Access is
              determined by the role attached to the key.
            </p>
            <form
              onSubmit={(e) => {
                e.preventDefault();
                setToken(draft.trim());
                setDraft("");
                setDemo(false);
                setDocs([]);
                setEvents([]);
                setConnectors([]);
                setOffset(0);
                setLogin(false);
              }}
            >
              <label>
                Business credential
                <input
                  type="password"
                  autoComplete="off"
                  value={draft}
                  onChange={(e) => setDraft(e.target.value)}
                  required
                  placeholder="Paste your API credential"
                />
              </label>
              <button className="primary" type="submit">
                Connect securely <ArrowRight size={16} />
              </button>
            </form>
            <small>No credentials are saved in browser storage.</small>
          </section>
        </div>
      )}
      {selected && (
        <div className="overlay detailoverlay">
          <section
            className="detail"
            role="dialog"
            aria-modal="true"
            aria-labelledby="document-title"
          >
            <div className="detailhead">
              <div>
                <div className="eyebrow">DOCUMENT REVIEW</div>
                <h2 id="document-title">
                  {selected.payload.external_reference}
                </h2>
              </div>
              <button
                className="iconbutton"
                aria-label="Close document"
                onClick={() => setSelected(null)}
              >
                <X size={22} />
              </button>
            </div>
            <div className="detailbody">
              <div className="invoice">
                <div className="invoicebrand">
                  <span className="brandmark">
                    <Command size={20} />
                  </span>
                  <span>Business document</span>
                  <span className={`pill ${selected.status.toLowerCase()}`}>
                    {label(selected.status)}
                  </span>
                </div>
                <h3>
                  {selected.payload.document_type === "CREDIT_NOTE"
                    ? "Credit note"
                    : "Invoice"}
                </h3>
                <div className="parties">
                  <div>
                    <small>FROM</small>
                    <strong>{selected.payload.seller.cbin_id}</strong>
                    <span>TIN {selected.payload.seller.tin}</span>
                  </div>
                  <div>
                    <small>TO</small>
                    <strong>{selected.payload.buyer.cbin_id}</strong>
                    <span>TIN {selected.payload.buyer.tin}</span>
                  </div>
                </div>
                <div className="invoice-meta">
                  <span>Issued {selected.payload.issued_at.slice(0, 10)}</span>
                  <span>{selected.payload.currency}</span>
                </div>
                <table>
                  <thead>
                    <tr>
                      <th>Description</th>
                      <th>Qty</th>
                      <th>Unit price</th>
                    </tr>
                  </thead>
                  <tbody>
                    {selected.payload.line_items.map((l, i) => (
                      <tr key={i}>
                        <td>
                          <strong>{l.description}</strong>
                          <small>
                            {l.item_code} · tax rate {l.tax_rate}
                          </small>
                        </td>
                        <td>{l.quantity}</td>
                        <td>
                          {money(l.unit_price_minor, selected.payload.currency)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                <dl className="totals">
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
                  <dt className="grand">Total amount</dt>
                  <dd className="grand">
                    {money(
                      selected.payload.totals.grand_total_minor,
                      selected.payload.currency,
                    )}
                  </dd>
                </dl>
                <div className="fiscal">
                  <ShieldCheck size={18} />
                  <span>
                    Fiscal verification:{" "}
                    {label(
                      String(selected.fiscal_verification || "not_verified"),
                    )}
                  </span>
                </div>
                {selected.posted_reference && (
                  <p className="posting">
                    Posting reference: {selected.posted_reference}
                  </p>
                )}
              </div>
              <aside className="review">
                <h3>Review & decision</h3>
                <p>
                  Confirm the supplier and map the document to your accounting
                  records.
                </p>
                {error && (
                  <div role="alert" className="error">
                    {error}
                  </div>
                )}
                {["delivered", "under_review"].includes(selected.status) ? (
                  <>
                    <label>
                      Supplier reference
                      <input
                        value={supplier}
                        onChange={(e) => setSupplier(e.target.value)}
                        placeholder="ERP supplier code"
                      />
                    </label>
                    <label>
                      Account reference
                      <input
                        value={account}
                        onChange={(e) => setAccount(e.target.value)}
                        placeholder="ERP account code"
                      />
                    </label>
                    <div className="mappingheading">Item mapping</div>
                    {Object.keys(sku).map((code) => (
                      <label key={code}>
                        {" "}
                        {code} → your ERP item
                        <input
                          value={sku[code]}
                          onChange={(e) =>
                            setSku({ ...sku, [code]: e.target.value })
                          }
                          placeholder="ERP item code"
                        />
                      </label>
                    ))}
                    <div className="mappingheading">Tax mapping</div>
                    {Object.keys(tax).map((rate) => (
                      <label key={rate}>
                        Tax rate {rate}%
                        <input
                          value={tax[rate]}
                          onChange={(e) =>
                            setTax({ ...tax, [rate]: e.target.value })
                          }
                          placeholder="ERP tax code"
                        />
                      </label>
                    ))}
                    <button
                      className="primary"
                      disabled={
                        busy || (!demo && (!supplier.trim() || !account.trim()))
                      }
                      onClick={() => void decide(true)}
                    >
                      <Check size={16} />
                      Approve & queue posting
                    </button>
                    <label>
                      Rejection reason
                      <input
                        value={reason}
                        onChange={(e) => setReason(e.target.value)}
                        placeholder="Explain the issue"
                      />
                    </label>
                    <button
                      className="rejectbutton"
                      disabled={busy || !reason.trim()}
                      onClick={() => void decide(false)}
                    >
                      Reject document
                    </button>
                  </>
                ) : (
                  <div className="decisiondone">
                    <Check size={24} />
                    <strong>{label(selected.status)}</strong>
                    <p>This document has moved beyond buyer review.</p>
                  </div>
                )}
                {demo && (
                  <small className="demo-warning">
                    Demo actions update sample data only.
                  </small>
                )}
                {selected.timeline?.map((e) => (
                  <div className="mini-event" key={e.id}>
                    <strong>{e.kind}</strong>
                    <small>{new Date(e.created_at).toLocaleString()}</small>
                  </div>
                ))}
              </aside>
            </div>
          </section>
        </div>
      )}
    </div>
  );
}
createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
