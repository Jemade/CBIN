'use strict';
let credential = '';
const $ = (id) => document.getElementById(id);
const el = (tag, text, className) => {const node = document.createElement(tag); if(text !== undefined) node.textContent = text; if(className) node.className = className; return node;};
const message = (text) => {$('message').textContent = text;};
async function api(path, options = {}) {
  if(!credential) throw new Error('Connect your business first.');
  const response = await fetch(path, {...options, headers: {'Authorization': `Bearer ${credential}`, 'Content-Type':'application/json'}});
  const result = await response.json();
  if(!response.ok) throw new Error(result.error?.message || `Request failed (${response.status})`);
  return result;
}
async function run(action) {try {await action();} catch(error) {message(error.message);}}
function money(minor, currency) {return `${currency} ${minor} minor units`;}
async function refresh() {
  const result = await api('/v1/documents?status=' + encodeURIComponent($('filter').value));
  $('documents').replaceChildren();
  for(const doc of result.items) {
    const card = el('article', undefined, 'card');
    card.append(el('span', doc.status.replaceAll('_',' '), 'badge'), el('h3', doc.payload.external_reference), el('p', money(doc.payload.totals.grand_total_minor, doc.payload.currency)));
    const button = el('button', 'Review document');button.onclick = () => run(() => review(doc.id));card.append(button);$('documents').append(card);
  }
  if(!result.items.length) $('documents').append(el('p','No documents match this view.'));
  message(`${result.items.length} document(s) loaded.`);
}
async function review(id) {
  const doc = await api(`/v1/documents/${id}`);const box = $('detail');box.replaceChildren();box.hidden = false;
  box.append(el('h2', doc.payload.external_reference), el('p', `Seller ${doc.payload.seller.cbin_id} → Buyer ${doc.payload.buyer.cbin_id}`), el('p', `Status: ${doc.status}`), el('p', `Fiscal signature: ${doc.fiscal_verification.replaceAll('_',' ')}. CBIN does not perform fiscalisation.`, 'warning'));
  if(doc.posted_reference?.startsWith('sandbox:')) box.append(el('p','Simulated posting only. No real ERP was changed.', 'warning'));
  const table = el('table');const head=el('tr');['Product','Description','Quantity','Unit price (minor)','Tax rate'].forEach(t=>head.append(el('th',t)));table.append(head);
  for(const line of doc.payload.line_items){const row=el('tr');[line.item_code,line.description,line.quantity,line.unit_price_minor,line.tax_rate].forEach(t=>row.append(el('td',String(t))));table.append(row);}box.append(table, el('p',`Total: ${money(doc.payload.totals.grand_total_minor,doc.payload.currency)}`));
  if(['delivered','under_review'].includes(doc.status)) {
    const form=el('form');const mapping={};
    for(const line of doc.payload.line_items){if(mapping[line.item_code])continue;const label=el('label',`Buyer product reference for ${line.item_code}`);const input=el('input');input.required=true;label.append(input);form.append(label);mapping[line.item_code]=input;}
    const supplier=el('input');supplier.required=true;const supplierLabel=el('label','Buyer ERP supplier reference');supplierLabel.append(supplier);form.append(supplierLabel);
    const account=el('input');account.required=true;const accountLabel=el('label','Buyer ERP expense account reference');accountLabel.append(account);form.append(accountLabel);
    const tax=el('textarea');tax.value='{}';const taxLabel=el('label','Tax rate to ERP tax ID mapping (JSON, e.g. {"15":"12345"})');taxLabel.append(tax);form.append(taxLabel);
    const accept=el('button','Accept and queue draft bill');form.append(accept);form.onsubmit=(e)=>{e.preventDefault();run(async()=>{accept.disabled=true;try{await api(`/v1/documents/${id}/accept`,{method:'POST',body:JSON.stringify({sku_mapping:Object.fromEntries(Object.entries(mapping).map(([k,v])=>[k,v.value])),supplier_reference:supplier.value,account_reference:account.value,tax_mapping:JSON.parse(tax.value)})});await review(id);await refresh();}finally{accept.disabled=false;}});};box.append(form);
    const rejectForm=el('form');const reason=el('textarea');reason.required=true;reason.minLength=3;const label=el('label','Reason for rejection');label.append(reason);rejectForm.append(label,el('button','Reject invoice'));rejectForm.onsubmit=(e)=>{e.preventDefault();run(async()=>{await api(`/v1/documents/${id}/reject`,{method:'POST',body:JSON.stringify({reason:reason.value})});await review(id);await refresh();});};box.append(rejectForm);
  }
  box.append(el('h3','Audit timeline'));const list=el('ol');for(const event of doc.timeline)list.append(el('li',`${new Date(event.created_at*1000).toLocaleString()} · ${event.kind}`));box.append(list);box.scrollIntoView({behavior:'smooth',block:'start'});
}
$('login').onsubmit=(e)=>{e.preventDefault();credential=$('key').value;$('key').value='';run(refresh);};
$('logout').onclick=()=>{credential='';$('documents').replaceChildren(el('p','Disconnected.'));$('detail').hidden=true;$('detail').replaceChildren();$('jobs').replaceChildren();message('Credential cleared.');};
$('refresh').onclick=()=>run(refresh);$('filter').onchange=()=>run(refresh);
$('load-jobs').onclick=()=>run(async()=>{const result=await api('/v1/operations/jobs');$('jobs').replaceChildren();for(const job of result.items){const card=el('article',undefined,'card');card.append(el('h3',`${job.kind} · ${job.state}`),el('p',`Attempts: ${job.attempts} · ${job.last_error || 'No error'}`));if(job.state==='dead_letter'){const label=el('label','Replay reason');const reason=el('input');label.append(reason);const button=el('button','Replay');button.onclick=()=>run(async()=>{await api(`/v1/operations/jobs/${job.id}/replay`,{method:'POST',body:JSON.stringify({reason:reason.value})});message('Replay queued with audit record.');});card.append(label,button);}$('jobs').append(card);}});

let softwareCatalogue = [];
function renderConnectors() {
  const search = $('connector-search').value.toLowerCase();
  $('connectors').replaceChildren();
  for(const software of softwareCatalogue.filter(s => `${s.name} ${s.category}`.toLowerCase().includes(search))) {
    const card = el('article', undefined, 'card');
    card.append(el('h3', software.name), el('span', software.category, 'badge'),
      el('p', `Capture: ${software.cbin_capture_status.replaceAll('_',' ')}`),
      el('p', `Posting: ${software.cbin_posting_status.replaceAll('_',' ')}`),
      el('p', 'Live integration: not verified'));
    $('connectors').append(card);
  }
}
$('load-connectors').onclick = () => run(async () => {
  const result = await api('/v1/connectors');
  softwareCatalogue = result.items;
  $('catalogue-notice').textContent = `${result.observed_count} entries checked ${result.checked_at}. ${result.notice}`;
  renderConnectors();
});
$('connector-search').oninput = renderConnectors;
$('logout').addEventListener('click', () => {softwareCatalogue = []; $('connectors').replaceChildren();});
