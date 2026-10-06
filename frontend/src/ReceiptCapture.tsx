import { useEffect, useState } from 'react';
import type { Doc } from './types';
type Capture = {id: string; filename: string; candidate: Doc['payload']; document_id?: string; provider: string};
type Props = {token: string; request: <T>(path: string, body?: unknown) => Promise<T>; onConfirmed: () => Promise<void>};
export function ReceiptCapture({token, request, onConfirmed}: Props) {
  const [captures, setCaptures] = useState<Capture[]>([]), [selected, setSelected] = useState<Capture | null>(null);
  const [busy, setBusy] = useState(false), [error, setError] = useState(''), [reviewed, setReviewed] = useState(false);
  async function load() { setCaptures((await request<{items: Capture[]}>('/v1/receipt-captures')).items); }
  useEffect(() => { void load().catch(e => setError(e.message)); }, []);
  async function run(action: () => Promise<void>) {setBusy(true); setError(''); try { await action(); } catch(e) {setError((e as Error).message);} finally {setBusy(false);}}
  async function upload(file: File) {
    if(file.size > 1_000_000) throw Error('Choose a PDF, PNG or JPEG up to 1 MB.');
    let text = ''; for(const byte of new Uint8Array(await file.arrayBuffer())) text += String.fromCharCode(byte);
    const row = await request<Capture>('/v1/receipt-captures', {filename:file.name, media_type:file.type, content_base64:btoa(text)});
    setSelected(row); setReviewed(false); await load();
  }
  async function original(row: Capture) {
    const response = await fetch(`/v1/receipt-captures/${row.id}/original`, {headers:{Authorization:`Bearer ${token}`}});
    if(!response.ok) throw Error('Original could not be downloaded');
    const url = URL.createObjectURL(await response.blob()); const a = document.createElement('a'); a.href=url; a.download=row.filename; a.click(); setTimeout(()=>URL.revokeObjectURL(url),1000);
  }
  return <section className="receipt-capture">
    <h3>Paper invoices</h3><p>Upload a receipt photo or PDF. Review the extracted details before it enters the purchase inbox.</p>
    {error && <p role="alert" className="error">{error}</p>}
    <label>Scan or upload invoice<input type="file" accept="application/pdf,image/png,image/jpeg" disabled={busy} onChange={e=>{const file=e.target.files?.[0];if(file) void run(()=>upload(file));e.target.value='';}} /></label>
    {captures.filter(c=>!c.document_id).map(row=><button key={row.id} onClick={()=>{setSelected(row);setReviewed(false);}}>Review {row.filename}</button>)}
    {selected && !selected.document_id && <div className="capture-review">
      <h4>Check against the original</h4><button disabled={busy} onClick={()=>void run(()=>original(selected))}>Download scanned original</button>
      <p>{selected.candidate.external_reference} · {selected.candidate.issued_at} · {selected.candidate.currency}</p>
      <p>Seller TIN: {selected.candidate.seller.tin}<br/>Buyer TIN: {selected.candidate.buyer.tin}</p>
      <table><thead><tr><th>Description</th><th>Quantity</th><th>Unit price</th></tr></thead><tbody>{selected.candidate.line_items.map((line,i)=><tr key={i}><td>{line.description}</td><td>{line.quantity}</td><td>{(line.unit_price_minor/100).toFixed(2)}</td></tr>)}</tbody></table>
      <p>Total: {selected.candidate.currency} {(selected.candidate.totals.grand_total_minor/100).toFixed(2)} · tax {(selected.candidate.totals.tax_minor/100).toFixed(2)}</p>
      <label><input type="checkbox" checked={reviewed} onChange={e=>setReviewed(e.target.checked)}/>I compared the buyer, supplier, items, amounts and tax with the original.</label>
      <p>If any detail is wrong, leave this capture unconfirmed and correct the extraction upstream. Fiscal validation is a separate check.</p>
      <button disabled={busy || !reviewed} onClick={()=>void run(async()=>{await request(`/v1/receipt-captures/${selected.id}/confirm`,{reviewed:true});setSelected(null);await load();await onConfirmed();})}>Confirm & send to purchase inbox</button>
    </div>}
  </section>;
}
