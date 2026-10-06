import { useState } from 'react';
import type { Doc } from './types';

type Props = { document: Doc; token: string; mode: string; reload: () => Promise<void>; request: <T>(path: string, body?: unknown) => Promise<T> };
export function InvoiceEvidence({ document: doc, token, mode, reload, request }: Props) {
  const [busy, setBusy] = useState(false), [error, setError] = useState('');
  async function run(action: () => Promise<void>) {
    setBusy(true); setError('');
    try { await action(); } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  async function download(path: string, filename: string) {
    const response = await fetch(path, { headers: { Authorization: `Bearer ${token}` } });
    if (!response.ok) { const data = await response.json(); throw Error(data.error?.message || 'Download failed'); }
    const url = URL.createObjectURL(await response.blob());
    const anchor = window.document.createElement('a'); anchor.href = url; anchor.download = filename;
    anchor.click(); window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  async function upload(file: File) {
    if (file.size > 1_000_000) throw Error('Choose a PDF, PNG or JPEG up to 1 MB.');
    const bytes = new Uint8Array(await file.arrayBuffer());
    let binary = ''; for (const byte of bytes) binary += String.fromCharCode(byte);
    await request(`/v1/documents/${doc.id}/files`, { filename: file.name, media_type: file.type, content_base64: btoa(binary) });
    await reload();
  }
  const fiscal = doc.payload.fiscal_metadata;
  const sealed = !['queued', 'delivered', 'under_review'].includes(doc.status);
  return <section className="invoice-evidence" aria-label="Invoice documents">
    <h4>Invoice documents</h4>
    <p>Supplier originals and scanned copies stay separate from the generated exchange copy.</p>
    {error && <p role="alert" className="error">{error}</p>}
    <div className="document-tools">
      <button disabled={busy} onClick={() => void run(() => download(`/v1/documents/${doc.id}/print?layout=a4`, 'invoice-a4-copy.pdf'))}>A4 copy</button>
      <button disabled={busy} onClick={() => void run(() => download(`/v1/documents/${doc.id}/print?layout=receipt48`, 'invoice-receipt-copy.pdf'))}>Receipt copy</button>
      {mode === 'Seller' && <button disabled={busy} onClick={() => void run(() => download(`/v1/documents/${doc.id}/print?layout=escpos48`, 'invoice-epson.bin'))}>Epson print file</button>}
    </div>
    <ul className="evidence-list">{doc.files?.map(file => <li key={file.id}>
      <button disabled={busy} onClick={() => void run(() => download(`/v1/documents/${doc.id}/files/${file.id}`, file.filename))}>{file.filename}</button>
      <small>{file.kind.replaceAll('_', ' ')} · {file.erp_status?.replaceAll('_', ' ')}<br/>Keep until {new Date(file.retain_until * 1000).toLocaleDateString()}</small>
    </li>)}</ul>
    {!sealed && mode !== 'CBIN' && <label className="evidence-upload">Add original invoice or scan
      <input type="file" accept="application/pdf,image/png,image/jpeg" disabled={busy} onChange={e => { const file = e.target.files?.[0]; if (file) void run(() => upload(file)); e.target.value = ''; }} />
    </label>}
    {sealed && <p>Evidence is sealed after the buyer decision.</p>}
    <h4>Fiscal check</h4>
    <p>{doc.fiscal_check?.status?.replaceAll('_', ' ') || doc.fiscal_verification?.replaceAll('_', ' ') || 'Not supplied'}</p>
    {doc.fiscal_check?.provider && <p>{doc.fiscal_check.provider} · {doc.fiscal_check.evidence_reference}</p>}
    {fiscal?.verification_url && <a href={fiscal.verification_url} target="_blank" rel="noopener noreferrer">Open ZIMRA validation</a>}
    {mode !== 'Seller' && fiscal && <button disabled={busy} onClick={() => void run(async () => { await request(`/v1/documents/${doc.id}/fiscal/verify`, {}); await reload(); })}>Check fiscal evidence</button>}
    <p className="scope-note">The configured provider must confirm the invoice details. Buyer approval does not confirm fiscal validity or record a payment.</p>
  </section>;
}
