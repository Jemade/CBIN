import { useEffect, useId, useState } from "react";
import type { Doc } from "./main";
import demoReferences from "./demo-bookkeeping.json";

type Ref = {
  id: string;
  name: string;
  code?: string;
  type?: string;
  rate?: string;
};
type References = {
  source: string;
  refreshed_at?: number;
  currency_exponents: Record<string, number>;
  suppliers: Ref[];
  accounts: Ref[];
  items: Ref[];
  taxes: Ref[];
  purchase_orders: Ref[];
  supported_documents: string[];
};
export type AccountingChoice = {
  supplier_reference: string;
  account_reference: string;
  sku_mapping: Record<string, string>;
  tax_mapping: Record<string, string>;
  line_allocations: { line_index: number; account_reference: string }[];
  bookkeeping: true;
  remember_mapping: boolean;
  purchase_order_reference: string | null;
};
type Suggestions = {
  supplier_reference: string;
  sku_mapping: Record<string, string>;
  tax_mapping: Record<string, string>;
  line_allocations: { line_index: number; account_reference: string }[];
  saved: boolean;
};
type Preview = {
  currency: string;
  currency_exponent: number;
  entries: {
    account_id: string;
    account_name: string;
    debit_minor: number;
    credit_minor: number;
  }[];
  balanced: boolean;
  notice: string;
  purchase_order?: { status: string; notice: string } | null;
};
type Props = {
  document: Doc;
  demo: boolean;
  request: <T>(
    path: string,
    body?: unknown,
    signal?: AbortSignal,
  ) => Promise<T>;
  onApprove: (choice: AccountingChoice) => Promise<void>;
  onReject: (reason: string) => Promise<void>;
  demoSaved?: AccountingChoice;
};
const rateKey = (v: string) => String(Number(v));
function SearchSelect({
  label,
  options,
  value,
  onChange,
  disabled = false,
}: {
  label: string;
  options: Ref[];
  value: string;
  onChange: (id: string) => void;
  disabled?: boolean;
}) {
  const id = useId(),
    [query, setQuery] = useState(""),
    [open, setOpen] = useState(false),
    [active, setActive] = useState(0);
  const chosen = options.find((o) => o.id === value),
    filtered = options
      .filter((o) =>
        `${o.name} ${o.code || ""} ${o.type || ""}`
          .toLowerCase()
          .includes(query.toLowerCase()),
      )
      .slice(0, 30);
  function choose(o: Ref) {
    onChange(o.id);
    setOpen(false);
    setQuery("");
  }
  return (
    <div className="accounting-select">
      <label htmlFor={id}>{label}</label>
      <div className="select-input">
        <input
          id={id}
          role="combobox"
          aria-autocomplete="list"
          aria-expanded={open}
          aria-controls={`${id}-list`}
          aria-activedescendant={
            open && filtered[active] ? `${id}-${active}` : undefined
          }
          disabled={disabled}
          placeholder={`Search ${label.toLowerCase()}…`}
          autoComplete="off"
          value={
            open
              ? query
              : chosen
                ? `${chosen.name}${chosen.code ? " · " + chosen.code : ""}`
                : ""
          }
          onFocus={() => {
            setOpen(true);
            setQuery("");
            setActive(0);
          }}
          onChange={(e) => {
            setQuery(e.target.value);
            setOpen(true);
            setActive(0);
          }}
          onBlur={() => setOpen(false)}
          onKeyDown={(e) => {
            if (e.key === "Escape" && open) {
              e.preventDefault();
              e.stopPropagation();
              setOpen(false);
            }
            if (e.key === "ArrowDown") {
              e.preventDefault();
              setOpen(true);
              setActive(Math.min(active + 1, filtered.length - 1));
            }
            if (e.key === "ArrowUp") {
              e.preventDefault();
              setActive(Math.max(0, active - 1));
            }
            if (e.key === "Enter" && open) {
              e.preventDefault();
              if (filtered[active]) choose(filtered[active]);
            }
          }}
        />
        {value && (
          <button
            type="button"
            aria-label={`Clear ${label.toLowerCase()}`}
            onClick={() => {
              onChange("");
              setOpen(false);
            }}
          >
            ×
          </button>
        )}
      </div>
      {open && (
        <div
          id={`${id}-list`}
          className="select-options"
          role="listbox"
          aria-label={`${label} options`}
        >
          {filtered.length ? (
            filtered.map((o, i) => (
              <button
                key={o.id}
                id={`${id}-${i}`}
                type="button"
                role="option"
                aria-selected={o.id === value}
                className={active === i ? "highlighted" : ""}
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => choose(o)}
              >
                <strong>{o.name}</strong>
                <small>{o.code || o.type || o.id}</small>
              </button>
            ))
          ) : (
            <p>
              No matching records. Refresh accounting data if a record is
              missing.
            </p>
          )}
        </div>
      )}
    </div>
  );
}
export function BookkeepingReview({
  document: doc,
  demo,
  request,
  onApprove,
  onReject,
  demoSaved,
}: Props) {
  const [refs, setRefs] = useState<References | null>(null),
    [choice, setChoice] = useState<AccountingChoice | null>(null),
    [preview, setPreview] = useState<Preview | null>(null),
    [saved, setSaved] = useState(false),
    [busy, setBusy] = useState(false),
    [loading, setLoading] = useState(true),
    [error, setError] = useState(""),
    [reason, setReason] = useState("");
  function apply(r: References, s?: Suggestions) {
    setRefs(r);
    setSaved(Boolean(s?.saved));
    setChoice({
      bookkeeping: true,
      remember_mapping: true,
      supplier_reference: s?.supplier_reference || "",
      account_reference: s?.line_allocations[0]?.account_reference || "",
      sku_mapping: Object.fromEntries(
        doc.payload.line_items.map((l) => [
          l.item_code,
          s?.sku_mapping[l.item_code] || "",
        ]),
      ),
      tax_mapping: Object.fromEntries(
        doc.payload.line_items.map((l) => [
          rateKey(l.tax_rate),
          s?.tax_mapping[rateKey(l.tax_rate)] || "",
        ]),
      ),
      line_allocations: doc.payload.line_items.map((_, i) => ({
        line_index: i,
        account_reference:
          s?.line_allocations.find((a) => a.line_index === i)
            ?.account_reference || "",
      })),
      purchase_order_reference: null,
    });
  }
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setPreview(null);
    setError("");
    if (demo) {
      apply(
        demoReferences,
        demoSaved ? { ...demoSaved, saved: true } : undefined,
      );
      setLoading(false);
      return;
    }
    request<{ references: References; suggestions: Suggestions }>(
      `/v1/documents/${doc.id}/bookkeeping`,
      undefined,
      controller.signal,
    )
      .then((c) => {
        if (!controller.signal.aborted) apply(c.references, c.suggestions);
      })
      .catch((e) => {
        if (!controller.signal.aborted) setError((e as Error).message);
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [doc.id, demo]);
  function update(part: Partial<AccountingChoice>) {
    if (!choice) return;
    setChoice({ ...choice, ...part });
    setPreview(null);
    setError("");
  }
  const complete =
    choice &&
    Boolean(choice.supplier_reference) &&
    choice.line_allocations.every((a) => a.account_reference) &&
    Object.values(choice.sku_mapping).every(Boolean) &&
    Object.values(choice.tax_mapping).every(Boolean);
  async function refresh() {
    setBusy(true);
    setPreview(null);
    setError("");
    try {
      if (demo) {
        apply(demoReferences);
        return;
      }
      await request("/v1/accounting/references/refresh", {});
      const c = await request<{
        references: References;
        suggestions: Suggestions;
      }>(`/v1/documents/${doc.id}/bookkeeping`);
      apply(c.references, c.suggestions);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  function demoPreview(): Preview {
    if (!choice || !refs) throw Error("Select the accounting records first.");
    const exp = refs.currency_exponents[doc.payload.currency];
    if (exp === undefined) throw Error("Currency is not configured.");
    const entries: Preview["entries"] = [];
    let subtotal = 0,
      tax = 0;
    doc.payload.line_items.forEach((l, i) => {
      const amount = Math.round(Number(l.quantity) * l.unit_price_minor);
      subtotal += amount;
      tax += Math.round((amount * Number(l.tax_rate)) / 100);
      const account = refs.accounts.find(
        (a) => a.id === choice.line_allocations[i].account_reference,
      )!;
      entries.push({
        account_id: account.id,
        account_name: account.name,
        debit_minor: amount,
        credit_minor: 0,
      });
    });
    if (choice.purchase_order_reference) {
      const po = demoReferences.purchase_orders.find(
        (p) => p.id === choice.purchase_order_reference,
      );
      if (
        !po ||
        po.supplier_id !== choice.supplier_reference ||
        po.currency !== doc.payload.currency ||
        po.total_minor !== doc.payload.totals.grand_total_minor
      )
        throw Error("Invoice and purchase order headers do not match.");
    }
    if (tax)
      entries.push({
        account_id: "TAX",
        account_name: "Demo input tax",
        debit_minor: tax,
        credit_minor: 0,
      });
    entries.push({
      account_id: "AP",
      account_name: "Accounts payable",
      debit_minor: 0,
      credit_minor: subtotal + tax,
    });
    if (doc.payload.document_type === "CREDIT_NOTE")
      entries.forEach((e) => {
        [e.debit_minor, e.credit_minor] = [e.credit_minor, e.debit_minor];
      });
    return {
      currency: doc.payload.currency,
      currency_exponent: exp,
      entries,
      balanced: true,
      notice:
        "Demo allocation only. No payment or real accounting entry is created.",
      purchase_order: choice.purchase_order_reference
        ? {
            status: "supplier_currency_total_match",
            notice:
              "Header match only; check quantities, receipts and prior billed balances.",
          }
        : null,
    };
  }
  async function prepare() {
    if (!choice) return;
    setBusy(true);
    setError("");
    try {
      setPreview(
        demo
          ? demoPreview()
          : await request<Preview>(
              `/v1/documents/${doc.id}/bookkeeping/preview`,
              choice,
            ),
      );
    } catch (e) {
      setError((e as Error).message);
      setPreview(null);
    } finally {
      setBusy(false);
    }
  }
  async function approve() {
    if (!choice || !preview) return;
    setBusy(true);
    setError("");
    try {
      await onApprove(choice);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  const formatted = (amount: number) =>
    preview
      ? `${preview.currency} ${(amount / 10 ** preview.currency_exponent).toLocaleString("en", { minimumFractionDigits: preview.currency_exponent, maximumFractionDigits: preview.currency_exponent })}`
      : "";
  return (
    <section className="bookkeeping">
      <div className="bookkeeping-heading">
        <div>
          <h3>Record this purchase</h3>
          <p>
            Choose the records in your accounting system, then review the
            proposed entry.
          </p>
        </div>
        <button
          type="button"
          disabled={busy || loading}
          onClick={() => void refresh()}
        >
          Refresh records
        </button>
      </div>
      {loading && <p role="status">Loading accounting records…</p>}
      {error && (
        <div role="alert" className="error">
          {error}
        </div>
      )}
      {refs && choice && (
        <fieldset className="bookkeeping-fields" disabled={busy}>
          <div className="reference-source">
            {refs.source === "sandbox_simulation"
              ? "Sample accounting records"
              : "Records from your configured accounting connection"}
            {saved && <strong> · Previous approved choices applied</strong>}
          </div>
          <SearchSelect
            label="Supplier"
            options={refs.suppliers}
            value={choice.supplier_reference}
            onChange={(supplier_reference) =>
              update({ supplier_reference, purchase_order_reference: null })
            }
          />
          <div className="allocation-list">
            {doc.payload.line_items.map((l, i) => (
              <section className="allocation" key={i}>
                <div className="allocation-title">
                  <strong>{l.description}</strong>
                  <span>
                    {l.quantity} × {doc.payload.currency}{" "}
                    {(
                      l.unit_price_minor /
                      10 ** (refs.currency_exponents[doc.payload.currency] ?? 2)
                    ).toFixed(
                      refs.currency_exponents[doc.payload.currency] ?? 2,
                    )}
                  </span>
                </div>
                <div className="allocation-fields">
                  <SearchSelect
                    label={`Accounting item · line ${i + 1}`}
                    options={refs.items}
                    value={choice.sku_mapping[l.item_code]}
                    onChange={(value) =>
                      update({
                        sku_mapping: {
                          ...choice.sku_mapping,
                          [l.item_code]: value,
                        },
                      })
                    }
                  />
                  <SearchSelect
                    label={`Purchase account · line ${i + 1}`}
                    options={refs.accounts}
                    value={choice.line_allocations[i].account_reference}
                    onChange={(value) => {
                      const line_allocations = choice.line_allocations.map(
                        (a, index) =>
                          index === i ? { ...a, account_reference: value } : a,
                      );
                      update({
                        line_allocations,
                        account_reference:
                          line_allocations[0].account_reference,
                      });
                    }}
                  />
                </div>
              </section>
            ))}
          </div>
          <div className="tax-fields">
            {Object.keys(choice.tax_mapping).map((rate) => (
              <SearchSelect
                key={rate}
                label={`Tax code · ${rate}%`}
                options={refs.taxes.filter(
                  (t) => Number(t.rate) === Number(rate),
                )}
                value={choice.tax_mapping[rate]}
                onChange={(value) =>
                  update({
                    tax_mapping: { ...choice.tax_mapping, [rate]: value },
                  })
                }
              />
            ))}
          </div>
          <SearchSelect
            label="Purchase order (optional)"
            options={refs.purchase_orders}
            value={choice.purchase_order_reference || ""}
            onChange={(value) =>
              update({ purchase_order_reference: value || null })
            }
          />
          <p className="po-help">
            Matches supplier, currency and total only. It does not confirm
            receipt of goods or prevent repeat billing against an order.
          </p>
          <label className="remember-choice">
            <input
              type="checkbox"
              checked={choice.remember_mapping}
              onChange={(e) =>
                setChoice({ ...choice, remember_mapping: e.target.checked })
              }
            />
            Remember these choices for this supplier
          </label>
          <button
            className="preview-button"
            disabled={
              !complete ||
              busy ||
              !refs.supported_documents.includes(doc.payload.document_type)
            }
            onClick={() => void prepare()}
          >
            Review proposed entry
          </button>
          {!refs.supported_documents.includes(doc.payload.document_type) && (
            <p>This accounting adapter does not support this document type.</p>
          )}
          {preview && (
            <div className="entry-preview">
              <div className="entry-heading">
                <h4>Proposed accounting entry</h4>
                <span>Balanced</span>
              </div>
              <div className="tablewrap">
                <table>
                  <thead>
                    <tr>
                      <th>Account</th>
                      <th>Debit</th>
                      <th>Credit</th>
                    </tr>
                  </thead>
                  <tbody>
                    {preview.entries.map((e, i) => (
                      <tr key={i}>
                        <td>{e.account_name}</td>
                        <td>
                          {e.debit_minor ? formatted(e.debit_minor) : "—"}
                        </td>
                        <td>
                          {e.credit_minor ? formatted(e.credit_minor) : "—"}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <p>{preview.notice}</p>
              {preview.purchase_order && (
                <p className="po-match">
                  Purchase order header matched. {preview.purchase_order.notice}
                </p>
              )}
              <button
                className="primary"
                disabled={busy}
                onClick={() => void approve()}
              >
                {busy ? "Saving…" : "Approve & queue bill"}
              </button>
            </div>
          )}
        </fieldset>
      )}
      <div className="reject-area">
        <label>
          Reason for rejection
          <input
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder="Explain what needs correcting"
          />
        </label>
        <button
          className="rejectbutton"
          disabled={busy || reason.trim().length < 3}
          onClick={async () => {
            setBusy(true);
            setError("");
            try {
              await onReject(reason.trim());
            } catch (e) {
              setError((e as Error).message);
            } finally {
              setBusy(false);
            }
          }}
        >
          Reject invoice
        </button>
      </div>
    </section>
  );
}
