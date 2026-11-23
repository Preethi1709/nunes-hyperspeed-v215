let mailboxId = null;
let productsCache = [];
let inboxCache = [];
let mailboxesCache = [];
let liveStream = null;
let fallbackTimer = null;
let refreshBusy = false;
let refreshRequested = false;
let currentPage = 'overview';
let lastStats = null;
let serverClockEpochMs = 0;
let serverClockSyncedAtMs = 0;
let lastLiveMessageAt = 0;
let productPage = 1;
let mailboxRetryTimer = null;
let bootstrapOnline = false;
const PRODUCT_PAGE_SIZE = 100;
const MAILBOX_CACHE_KEY = 'nunes_mailboxes_cache_v207';

const $ = id => document.getElementById(id);
const esc = v => String(v ?? '').replace(/[&<>"']/g, m => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));

async function api(url, opts={}) {
  const method = String(opts.method || 'GET').toUpperCase();
  const retries = Number.isFinite(Number(opts.retries)) ? Number(opts.retries) : (method === 'GET' ? 2 : 0);
  const timeoutMs = Number(opts.timeoutMs || 15000);
  let lastError = null;
  for (let attempt = 0; attempt <= retries; attempt++) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
      const cleanOpts = {...opts};
      delete cleanOpts.timeoutMs; delete cleanOpts.retries;
      const r = await fetch(url, {...cleanOpts, signal: opts.signal || controller.signal, cache: 'no-store'});
      if (!r.ok) {
        let msg = await r.text();
        try { const parsed = JSON.parse(msg); msg = parsed.error || parsed.detail || msg; } catch (_e) {}
        const err = new Error(msg || r.statusText);
        err.httpStatus = r.status;
        throw err;
      }
      return await r.json();
    } catch (err) {
      lastError = err?.name === 'AbortError'
        ? new Error('Local server response timed out. Retrying safely…')
        : err;
      const retryable = method === 'GET' && attempt < retries && (!lastError.httpStatus || lastError.httpStatus >= 500);
      if (!retryable) break;
      await new Promise(resolve => setTimeout(resolve, 450 * (attempt + 1)));
    } finally {
      clearTimeout(timer);
    }
  }
  throw lastError || new Error('Local server request failed.');
}

function toast(text, error=false) {
  const el = document.createElement('div');
  el.className = `toast${error ? ' error' : ''}`;
  el.textContent = text;
  $('toastStack').appendChild(el);
  setTimeout(() => el.remove(), 3400);
}

function money(v, c='INR') {
  if (v === null || v === undefined || v === '') return '—';
  const n = Number(v);
  if (!Number.isFinite(n)) return esc(v);
  const f = n.toLocaleString('en-IN', {minimumFractionDigits:0, maximumFractionDigits:2});
  return c === 'INR' ? `₹${f}` : `${esc(c || '')} ${f}`;
}

function verifyBadge(s) {
  s = (s || 'CONFIRMED').toUpperCase();
  const cls = s === 'CONFIRMED' ? 'confirmed' : s === 'PROBABLE' ? 'probable' : 'review';
  return `<span class="badge ${cls}">${esc(s)}</span>`;
}

function productHTML(rows) {
  const heads = ['PRODUCT','MODEL','ORIGINAL PRICE','DISCOUNTED PRICE','SUPPLIER','PRICE DATE','VERIFIED','ACTION'];
  let h = '<thead><tr>' + heads.map(x => `<th>${x}</th>`).join('') + '</tr></thead><tbody>';
  if (!rows.length) return h + '<tr><td colspan="8" class="empty">No confirmed product records yet.</td></tr></tbody>';
  for (const p of rows) {
    h += `<tr class="product-row" data-product-id="${p.id}">
      <td class="product-name-cell"><strong class="product-name-clamp" title="${esc(p.product_name)}">${esc(p.product_name)}</strong></td>
      <td class="model-cell">${esc(p.model || '—')}</td>
      <td class="price-cell original-price-cell"><strong>${money(p.original_price, p.currency)}</strong></td>
      <td class="price-cell discounted-price-cell"><strong>${money(p.purchase_price, p.currency)}</strong>${p.discount_percent !== null && p.discount_percent !== undefined && p.discount_percent !== '' ? `<small class="discount-note">${esc(p.discount_percent)}% off</small>` : ''}</td>
      <td class="supplier-cell"><span class="supplier-clamp" title="${esc(p.supplier_name || '')}">${esc(p.supplier_name || '—')}</span></td>
      <td class="date-cell">${esc(p.price_date || '—')}</td>
      <td>${verifyBadge(p.verification_status)}</td>
      <td class="action-cell"><button class="button small product-detail" data-id="${p.id}">View details</button></td>
    </tr>`;
  }
  return h + '</tbody>';
}

function inboxHTML(rows) {
  let h = '<thead><tr><th>Date</th><th>From</th><th>Subject</th><th>Attachments</th><th>Status</th><th>Error</th></tr></thead><tbody>';
  if (!rows.length) return h + '<tr><td colspan="6" class="empty">No emails match this view.</td></tr></tbody>';
  for (const x of rows) {
    const cls = (x.status || '').split(' ')[0];
    h += `<tr><td>${esc(x.received_at)}</td><td>${esc(x.sender)}</td><td class="wrap">${esc(x.subject)}</td><td class="wrap">${esc(x.attachment_names)}</td><td class="status ${cls}">${esc(x.status)}</td><td class="wrap">${esc(x.error)}</td></tr>`;
  }
  return h + '</tbody>';
}

function settingsHTML(obj) {
  return Object.entries(obj).map(([k,v]) => `<div><b>${esc(k)}</b><span>${esc(v)}</span></div>`).join('');
}

function terminalStatus(status='') {
  return status.startsWith('EXTRACTED ') || ['IRRELEVANT','NO PRODUCT','NEEDS REVIEW','NEEDS OCR','DUPLICATE / ALREADY EXISTS'].includes(status);
}

function formatEta(minutes) {
  if (minutes === null || minutes === undefined || !Number.isFinite(Number(minutes))) return 'No throughput yet';
  const n = Math.max(0, Math.round(Number(minutes)));
  if (n <= 0) return 'Complete';
  if (n < 60) return `~${n} min`;
  const totalHours = Math.floor(n / 60), mins = n % 60;
  if (totalHours < 24) return mins ? `~${totalHours}h ${mins}m` : `~${totalHours}h`;
  const days = Math.floor(totalHours / 24), hours = totalHours % 24;
  return hours ? `~${days}d ${hours}h` : `~${days}d`;
}

function formatUptime(seconds) {
  const s = Math.max(0, Number(seconds) || 0);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (h) return `${h}h ${m}m`;
  return `${m} min`;
}


async function loadConfig() {
  const c = await api('/api/config');
  const missing = [];
  if (!c.oauth_configured) missing.push('Google OAuth');
  $('configWarning').textContent = missing.length
    ? `Setup required: ${missing.join(', ')}. Open Settings → Credential setup.`
    : '';
  $('configWarning').classList.toggle('hidden', missing.length === 0 && !c.ai_provider_error);
  $('settingsStatus').innerHTML = settingsHTML({
    'Build': c.build_id || 'V21.5 SURREAL HYPERSPEED',
    'PC time': c.server_local_time || '—',
    'Timezone': c.server_timezone || '—',
    'Google OAuth': c.oauth_configured ? 'Configured' : 'Missing',
    'Fast AI': c.ai_model || 'qwen3:1.7b',
    'Deep AI': c.ai_deep_model || 'qwen3:4b',
    'Vision AI': c.ai_vision_model || 'qwen3-vl:4b',
    'AI health': c.ai_provider_error ? 'Waiting for recovery' : 'Ready',
    'Google Sheets': c.sheet_configured ? 'Configured' : 'Not configured',
    'Direct workers': String(c.direct_workers || 10),
    'AI batch': String(c.ai_batch_size || 4),
    'Server uptime': formatUptime(c.uptime_seconds || 0),
    'Busy deferrals': String(c.worker_busy_deferrals || 0),
    'PDF price re-audit': Number(c.pdf_reaudit_count || 0) ? `${c.pdf_reaudit_count} source email(s) queued` : 'Ready',
    'Last recovery': c.last_manual_recovery || c.last_auto_recovery || 'Not needed',
    'Architecture': 'Python Main → specialist Stage 2/OCR → exception AI',
    'Local AI': c.local_ai ? `${c.local_ai.fast_model_ready?'Fast ✓':'Fast —'} · ${c.local_ai.deep_model_ready?'Deep ✓':'Deep —'} · ${c.local_ai.vision_model_ready?'Vision ✓':'Vision —'}` : 'Checking',
    'Local AI self-test': c.last_local_ai_self_test_at || 'Not run',
    'Last worker warning': c.last_worker_error || 'None'
  });
}

async function loadHealth() {
  try {
    const h = await api('/api/health');
    for (const [id,key,label] of [['healthGmail','gmail','Gmail'],['healthLocalAi','local_ai','Local AI'],['healthDb','database','Database'],['healthSheets','sheets','Sheets'],['healthEngine','engine','Engine']]) {
      const el = $(id), x = h[key] || {};
      el.classList.toggle('ok', !!x.ok && !x.neutral);
      el.classList.toggle('bad', !x.ok && !x.neutral);
      el.classList.toggle('neutral', !!x.neutral);
      el.textContent = `${label} · ${x.state || (x.ok ? 'Healthy' : 'Attention')}`;
      if (x.detail) el.title = x.detail;
    }
  } catch (_e) {}
}

function accountCards(boxes) {
  if (!boxes.length) return '<div class="empty-card">No Gmail accounts connected.</div>';
  return boxes.map(b => `<div class="account-card ${Number(b.id) === Number(mailboxId) ? 'active' : ''}">
    <strong>${esc(b.email)}</strong><small>Last sync: ${esc(b.last_sync_at || 'Never')}</small>
    <div class="account-card-actions"><button class="button switch-mail" data-id="${b.id}">Use account</button><button class="button disconnect-mail" data-id="${b.id}">Disconnect</button></div>
  </div>`).join('');
}

function bindAccountButtons() {
  document.querySelectorAll('.switch-mail').forEach(btn => btn.onclick = async () => {
    mailboxId = Number(btn.dataset.id);
    localStorage.setItem('nunes_selected_mailbox_id', String(mailboxId));
    $('mailboxSelect').value = String(mailboxId);
    await refreshAll(true);
    startLive();
  });
  document.querySelectorAll('.disconnect-mail').forEach(btn => btn.onclick = async () => {
    const id = Number(btn.dataset.id), box = mailboxesCache.find(x => Number(x.id) === id);
    if (!confirm(`Disconnect ${box?.email || 'this Gmail account'}? Local data is retained.`)) return;
    try {
      await api(`/api/mailboxes/${id}`, {method:'DELETE'});
      toast('Gmail disconnected. Local data retained.');
      await loadMailboxes();
      startLive();
    } catch (e) { toast(e.message, true); }
  });
}


function saveMailboxCache(boxes) {
  try {
    localStorage.setItem(MAILBOX_CACHE_KEY, JSON.stringify((boxes || []).map(b => ({
      id:b.id,email:b.email,last_sync_at:b.last_sync_at || ''
    }))));
  } catch (_e) {}
}

function restoreMailboxCache(preferred=0) {
  let boxes = [];
  try { boxes = JSON.parse(localStorage.getItem(MAILBOX_CACHE_KEY) || '[]') || []; } catch (_e) {}
  if (!boxes.length) {
    $('mailboxSelect').innerHTML = '<option value="">Reconnecting to local engine…</option>';
    $('overviewMailbox').textContent = 'Reconnecting…';
    $('sidebarLiveText').textContent = 'Reconnecting to local engine…';
    return false;
  }
  mailboxesCache = boxes;
  const saved = Number(localStorage.getItem('nunes_selected_mailbox_id') || 0);
  const requested = Number(preferred || saved || boxes[0].id);
  const valid = boxes.some(b => Number(b.id) === requested) ? requested : Number(boxes[0].id);
  mailboxId = valid;
  $('mailboxSelect').innerHTML = boxes.map(b => `<option value="${b.id}" ${Number(b.id) === valid ? 'selected' : ''}>${esc(b.email)}</option>`).join('');
  $('gmailAccountCards').innerHTML = accountCards(boxes);
  bindAccountButtons();
  $('overviewMailbox').textContent = boxes.find(b => Number(b.id) === valid)?.email || 'Reconnecting…';
  $('sidebarLiveText').textContent = 'Restoring live connection…';
  return true;
}

function scheduleMailboxReconnect(preferred=0) {
  if (mailboxRetryTimer) return;
  mailboxRetryTimer = setTimeout(async () => {
    mailboxRetryTimer = null;
    try {
      await loadMailboxes(preferred, {preserveOnError:true});
      startLive();
    } catch (_e) {
      scheduleMailboxReconnect(preferred);
    }
  }, 3000);
}

async function loadMailboxes(preferred=0, options={}) {
  let boxes;
  try {
    boxes = await api('/api/mailboxes', {timeoutMs:10000, retries:3});
  } catch (err) {
    bootstrapOnline = false;
    $('sidebarLiveText').textContent = 'Reconnecting to local engine…';
    const health = $('healthEngine');
    if (health) {
      health.textContent = 'Engine · reconnecting';
      health.classList.add('neutral');
    }
    if (!mailboxesCache.length) restoreMailboxCache(preferred);
    scheduleMailboxReconnect(preferred);
    if (!options.preserveOnError) throw err;
    return false;
  }

  bootstrapOnline = true;
  mailboxesCache = boxes;
  saveMailboxCache(boxes);
  const sel = $('mailboxSelect');
  $('gmailAccountCards').innerHTML = accountCards(boxes);
  bindAccountButtons();

  // "No Gmail connected" is shown ONLY after a successful server response
  // explicitly confirms that there are no active mailboxes.
  if (!boxes.length) {
    localStorage.removeItem(MAILBOX_CACHE_KEY);
    sel.innerHTML = '<option value="">No Gmail connected</option>';
    mailboxId = null;
    setEmpty();
    return true;
  }

  const saved = Number(localStorage.getItem('nunes_selected_mailbox_id') || 0);
  const requested = Number(preferred || mailboxId || saved || boxes[0].id);
  const valid = boxes.some(b => Number(b.id) === requested) ? requested : Number(boxes[0].id);
  sel.innerHTML = boxes.map(b => `<option value="${b.id}" ${Number(b.id) === valid ? 'selected' : ''}>${esc(b.email)}</option>`).join('');
  mailboxId = valid;
  localStorage.setItem('nunes_selected_mailbox_id', String(mailboxId));
  await refreshAll(true);
  return true;
}

function setEmpty() {
  for (const id of ['statInbox','statIndexed','statAnalyzed','statWaiting','statFailures','statProducts','statReview','statSheetPending']) $(id).textContent = '0';
  $('analysisText').textContent = '0 of 0 emails processed';
  $('analysisPct').textContent = '0%';
  $('analysisBar').style.width = '0%';
  $('coverageStatus').textContent = 'Inbox coverage · 0%';
  $('statRate').textContent = '—';
  $('statEta').textContent = '—';
  $('statLastProduct').textContent = '—';
  $('statLastProductTime').textContent = 'No confirmed product yet';
  $('overviewMailbox').textContent = 'No Gmail connected';
  $('sidebarLiveText').textContent = 'Waiting for Gmail';
  $('productTable').innerHTML = productHTML([]);
  $('productTable2').innerHTML = productHTML([]);
  $('inboxTable').innerHTML = inboxHTML([]);
  $('activityList').innerHTML = '<div class="empty-card">Waiting for processing activity.</div>';
  $('reviewList').innerHTML = '<div class="empty-card">No review items.</div>';
}

function updateStats(s) {
  lastStats = s;
  const completed = s.completed_emails ?? s.analyzed_emails ?? 0;
  const pending = s.pending_emails ?? s.waiting_emails ?? 0;
  $('statInbox').textContent = s.inbox_total || 0;
  $('statIndexed').textContent = s.indexed_emails || 0;
  $('statAnalyzed').textContent = completed;
  $('statWaiting').textContent = pending;
  $('statFailures').textContent = s.failures || 0;
  $('statProducts').textContent = s.confirmed_products || 0;
  $('statReview').textContent = s.review_open || 0;
  $('statSheetPending').textContent = s.sheet_pending || 0;
  $('analysisText').textContent = `${completed.toLocaleString()} of ${(s.indexed_emails || 0).toLocaleString()} indexed emails processed`;
  $('analysisPct').textContent = `${s.analysis_pct || 0}%`;
  $('analysisBar').style.width = `${s.analysis_pct || 0}%`;
  $('coverageStatus').textContent = `Inbox coverage · ${s.coverage_pct || 0}%`;
  const observedRate = Number(s.processing_rate_per_min || 0);
  const observedHourly = Number(s.processing_rate_per_hour || 0);
  const rateWindow = Number(s.rate_window_minutes || 0);
  const rateConfidence = String(s.rate_confidence || 'NONE').toLowerCase();
  $('statRate').textContent = observedRate > 0 ? `${observedRate.toFixed(observedRate < 1 ? 2 : 1)}/min` : '0/min';
  $('statEta').textContent = pending === 0 ? 'Complete' : (observedRate > 0 ? formatEta(s.eta_minutes) : 'No recent throughput');
  $('statLastProduct').textContent = s.last_product_name || '—';
  $('statLastProductTime').textContent = s.last_product_at ? `Saved ${s.last_product_at}` : 'No confirmed product yet';
  $('processingHeadline').textContent = pending === 0 && s.indexed_emails ? 'All indexed emails processed' : `${pending.toLocaleString()} emails remaining`;
  $('processedSub').textContent = observedRate > 0
    ? `${observedHourly.toFixed(1)}/hour · ${rateWindow}m window · ${rateConfidence} confidence`
    : (Number(s.processing_now || 0) > 0
        ? `${Number(s.processing_now || 0)} active · waiting for completion`
        : (pending > 0 ? 'Queued · workers preparing next completion' : 'Complete'));

  const reviewCount = Number(s.review_open || 0);
  $('reviewNavCount').textContent = reviewCount;
  $('reviewNavCount').classList.toggle('hidden', reviewCount === 0);

  const mb = mailboxesCache.find(x => Number(x.id) === Number(mailboxId));
  $('overviewMailbox').textContent = mb?.email || 'Selected Gmail';
  $('sidebarLiveText').textContent = s.coverage_pct === 100 ? 'Live · Inbox verified' : `Live · coverage ${s.coverage_pct || 0}%`;
  $('overviewLiveState').textContent = s.coverage_pct === 100 ? 'Live monitoring · Inbox verified' : 'Live monitoring · verifying Inbox';

  const alert = $('engineAlert');
  if (!s.pipeline_balanced) {
    alert.className = 'engine-alert error';
    alert.textContent = 'Counter integrity warning: indexed emails do not balance with completed, pending and failed states. Open Settings for diagnostics.';
    $('healthEngine').classList.remove('ok','neutral'); $('healthEngine').classList.add('bad'); $('healthEngine').textContent = 'Engine · Counter warning';
  } else if (s.stalled) {
    alert.className = 'engine-alert error';
    alert.textContent = `Processing is stalled: ${pending.toLocaleString()} email(s) remain, but no completion was recorded in the recent window. Gmail collection remains safe.`;
    $('healthEngine').classList.remove('ok','neutral'); $('healthEngine').classList.add('bad'); $('healthEngine').textContent = 'Engine · Stalled';
  } else if (s.coverage_pct === 100 && pending === 0 && s.indexed_emails) {
    alert.className = 'engine-alert success';
    alert.textContent = 'Inbox is fully indexed and all current work is processed.';
  } else {
    alert.className = 'engine-alert hidden';
    alert.textContent = '';
  }
}

function bindProductButtons() {
  document.querySelectorAll('.product-detail').forEach(btn => btn.onclick = e => {
    e.stopPropagation();
    openProduct(Number(btn.dataset.id));
  });
  document.querySelectorAll('.product-row').forEach(row => row.onclick = () => openProduct(Number(row.dataset.productId)));
}

function sortedProducts(rows) {
  const mode = $('productSort')?.value || 'newest';
  const copy = [...rows];
  if (mode === 'name') copy.sort((a,b) => String(a.product_name||'').localeCompare(String(b.product_name||'')));
  if (mode === 'supplier') copy.sort((a,b) => String(a.supplier_name||'').localeCompare(String(b.supplier_name||'')));
  if (mode === 'priceAsc') copy.sort((a,b) => Number(a.purchase_price||Infinity) - Number(b.purchase_price||Infinity));
  if (mode === 'priceDesc') copy.sort((a,b) => Number(b.purchase_price||-Infinity) - Number(a.purchase_price||-Infinity));
  return copy;
}

function renderProductsPage(rows=productsCache) {
  const out = sortedProducts(rows);
  const pages = Math.max(1, Math.ceil(out.length / PRODUCT_PAGE_SIZE));
  productPage = Math.min(Math.max(1, productPage), pages);
  const start = (productPage - 1) * PRODUCT_PAGE_SIZE;
  const visible = out.slice(start, start + PRODUCT_PAGE_SIZE);
  $('productTable2').innerHTML = productHTML(visible);
  $('productResultCount').textContent = `${out.length.toLocaleString()} product${out.length === 1 ? '' : 's'} · showing ${out.length ? start + 1 : 0}–${Math.min(start + visible.length, out.length)}`;
  $('productPageLabel').textContent = `Page ${productPage} of ${pages}`;
  $('productPrev').disabled = productPage <= 1;
  $('productNext').disabled = productPage >= pages;
  bindProductButtons();
}

function renderInbox() {
  const q = ($('inboxSearch')?.value || '').trim().toLowerCase();
  const filter = $('inboxFilter')?.value || 'all';
  let rows = inboxCache.filter(x => !q || [x.sender,x.subject,x.attachment_names,x.status,x.error].some(v => String(v||'').toLowerCase().includes(q)));
  if (filter === 'error') rows = rows.filter(x => x.status === 'ERROR');
  if (filter === 'completed') rows = rows.filter(x => terminalStatus(x.status || ''));
  if (filter === 'pending') rows = rows.filter(x => x.status !== 'ERROR' && !terminalStatus(x.status || ''));
  $('inboxTable').innerHTML = inboxHTML(rows);
}

async function loadActivity() {
  if (!mailboxId) return;
  try {
    const rows = await api(`/api/mailboxes/${mailboxId}/activity?limit=12`);
    $('activityUpdated').textContent = 'Live';
    if (!rows.length) {
      $('activityList').innerHTML = '<div class="empty-card">No completed activity yet.</div>';
      return;
    }
    $('activityList').innerHTML = rows.map(r => {
      const error = r.status === 'ERROR';
      const product = Number(r.product_count || 0) > 0;
      const label = error ? 'Processing failed' : product ? `${r.product_count} product${Number(r.product_count)===1?'':'s'} confirmed` : r.status;
      return `<div class="activity-item"><i class="activity-dot ${error?'error':product?'product':''}"></i><div><strong>${esc(r.subject || '(no subject)')}</strong><p>${esc(r.sender || '')}</p><p>${esc(r.event_at || '')} · <b>${esc(label)}</b></p></div></div>`;
    }).join('');
  } catch (_e) {}
}

async function openReviewEvidence(reviewId) {
  const modal = $('productModal');
  const body = $('productModalBody');
  modal.classList.remove('hidden');
  modal.setAttribute('aria-hidden','false');
  body.innerHTML = '<div class="modal-loading">Loading review evidence...</div>';
  try {
    const d = await api(`/api/review/${reviewId}/details`);
    const p = d.product || {};
    const e = d.email || {};
    const ev = d.evidence || [];
    const gmailUrl = d.gmail?.thread_url || d.gmail?.message_url || d.gmail?.search_url || '';
    body.innerHTML = `
      <div class="detail-head">
        <div class="k">${p.id ? verifyBadge(p.verification_status || 'NEEDS REVIEW') : '<span class="badge review">EMAIL REVIEW</span>'}</div>
        <h2>${esc(p.product_name || e.subject || 'Email needs review')}</h2>
        <p>${esc(d.review?.reason || e.error || 'Needs verification')}</p>
      </div>
      <div class="detail-actions">${gmailUrl ? `<a class="button dark" href="${esc(gmailUrl)}" target="_blank" rel="noopener">Open source Gmail</a>` : ''}</div>
      ${p.id ? `<div class="detail-grid">${[
        ['Model',p.model || '—'],['Original price',money(p.original_price,p.currency)],
        ['Discount',p.discount_percent !== null && p.discount_percent !== undefined && p.discount_percent !== '' ? `${p.discount_percent}%` : '—'],
        ['Discounted price',money(p.purchase_price,p.currency)],['Supplier',p.supplier_name || '—'],
        ['Price date',p.price_date || '—'],['Status',p.verification_status || 'NEEDS REVIEW'],['Price basis',p.price_basis || '—']
      ].map(x => `<div class="detail-field"><span>${esc(x[0])}</span><strong>${esc(x[1])}</strong></div>`).join('')}</div>` : ''}
      <div class="detail-section"><h3>Source email</h3><div class="email-source">
        <div><b>From</b>${esc(e.sender || '—')}</div><div><b>Date</b>${esc(e.received_at || '—')}</div>
        <div><b>Subject</b>${esc(e.subject || '—')}</div><div><b>Attachments</b>${esc(e.attachment_names || '—')}</div>
        <div><b>Status</b>${esc(e.status || '—')}</div>${e.error ? `<div><b>Error</b>${esc(e.error)}</div>` : ''}
      </div></div>
      ${ev.length ? `<div class="detail-section"><h3>Extracted evidence</h3>${ev.map(x => `<div class="evidence-row"><b>${esc(x.field_name || 'Evidence')}</b><p>${esc(x.evidence_text || '')}</p></div>`).join('')}</div>` : '<div class="detail-section"><h3>Evidence status</h3><div class="email-source">No product candidate is available yet. V21.5 can retry this source through Stage 0 → Stage 1 → specialist Stage 2.</div></div>'}
      <div class="detail-section"><h3>Email body</h3><div class="email-source full-text">${esc(e.body || 'No body text available.')}</div></div>`;
  } catch (err) {
    body.innerHTML = `<div class="empty-card">${esc(err.message || 'Unable to load review evidence.')}</div>`;
  }
}

async function loadReview() {
  if (!mailboxId) return;
  const rows = await api(`/api/mailboxes/${mailboxId}/review`);
  if (!rows.length) {
    $('reviewList').innerHTML = '<div class="empty-card">No uncertain records need review.</div>';
    return;
  }
  $('reviewList').innerHTML = rows.map(r => {
    const hasProduct = !!r.product_id;
    const failure = !hasProduct && (String(r.reason || '').toLowerCase().includes('failed') || String(r.email_error || '').trim());
    return `<div class="review-item"><div><h3>${esc(r.product_name || r.subject || 'Email needs review')}</h3>
      <p>${r.model ? `Model: ${esc(r.model)} · ` : ''}${r.purchase_price ? `Price: ${money(r.purchase_price,r.currency)} · ` : ''}${esc(r.reason || r.email_error || 'Needs verification')}</p>
      <p>${esc(r.sender || '')} ${r.received_at ? `· ${esc(r.received_at)}` : ''}</p></div><div class="review-actions">
      <button class="button small review-evidence" data-id="${r.id}">View evidence</button>
      ${hasProduct ? `<button class="button small blue approve-review" data-id="${r.id}">Approve verified</button>` : `<button class="button small blue retry-review" data-id="${r.id}">${failure ? 'Retry extraction' : 'Retry processing'}</button>`}
      <button class="button small resolve-review" data-id="${r.id}">Dismiss</button></div></div>`;
  }).join('');
  document.querySelectorAll('.review-evidence').forEach(btn => btn.onclick = () => openReviewEvidence(btn.dataset.id));
  document.querySelectorAll('.approve-review').forEach(btn => btn.onclick = async () => {
    if (!confirm('Approve this product after checking the evidence/original email?')) return;
    try { await api(`/api/review/${btn.dataset.id}/approve`, {method:'POST'}); toast('Product approved as verified.'); await refreshAll(true); } catch(e){ toast(e.message,true); }
  });
  document.querySelectorAll('.retry-review').forEach(btn => btn.onclick = async () => {
    if (!confirm('Retry this email through V21.5 Stage 0 → Stage 1 → specialist Stage 2?')) return;
    try { await api(`/api/review/${btn.dataset.id}/retry`, {method:'POST'}); toast('Email requeued into specialist Stage 2.'); await refreshAll(true); } catch(e){ toast(e.message,true); }
  });
  document.querySelectorAll('.resolve-review').forEach(btn => btn.onclick = async () => {
    try { await api(`/api/review/${btn.dataset.id}/resolve`, {method:'POST'}); toast('Review item dismissed.'); await refreshAll(true); } catch(e){ toast(e.message,true); }
  });
}

async function refreshCore(includeProducts=true) {
  if (!mailboxId || document.hidden) return;
  if (refreshBusy) { refreshRequested = true; return; }
  refreshBusy = true;
  try {
    const calls = [api(`/api/mailboxes/${mailboxId}/stats`)];
    if (includeProducts) calls.push(api(`/api/mailboxes/${mailboxId}/products?limit=${currentPage === 'products' ? 500 : 120}`));
    const result = await Promise.all(calls);
    updateStats(result[0]);
    if (includeProducts) {
      productsCache = result[1];
      if (currentPage === 'overview') {
        $('productTable').innerHTML = productHTML(productsCache.slice(0,60));
        bindProductButtons();
      }
      if (currentPage === 'products') renderProductsPage();
    }
    if (currentPage === 'overview') await loadActivity();
  } catch (_e) {
    // A temporary slow response must never freeze the UI or create a request pile-up.
  } finally {
    refreshBusy = false;
    if (refreshRequested) {
      refreshRequested = false;
      setTimeout(() => refreshCore(currentPage === 'overview' || currentPage === 'products'), 250);
    }
  }
}

async function refreshAll(force=false) {
  if (!mailboxId || document.hidden) return;
  if (refreshBusy) { refreshRequested = true; return; }
  refreshBusy = true;
  try {
    const [s,p,e,boxes] = await Promise.all([
      api(`/api/mailboxes/${mailboxId}/stats`),
      api(`/api/mailboxes/${mailboxId}/products?limit=500`),
      api(`/api/mailboxes/${mailboxId}/emails?limit=300`),
      api('/api/mailboxes')
    ]);
    mailboxesCache = boxes;
    productsCache = p;
    inboxCache = e;
    updateStats(s);
    $('productTable').innerHTML = productHTML(p.slice(0,60));
    renderProductsPage(p);
    renderInbox();
    bindProductButtons();
    $('gmailAccountCards').innerHTML = accountCards(boxes);
    bindAccountButtons();
    await Promise.all([loadActivity(), loadReview(), loadHealth()]);
  } catch (_e) {
  } finally {
    refreshBusy = false;
    if (refreshRequested) {
      refreshRequested = false;
      setTimeout(() => refreshCore(currentPage === 'overview' || currentPage === 'products'), 250);
    }
  }
}

function showSheetMessage(text,error=false) {
  const el = $('sheetMessage');
  el.classList.remove('hidden');
  el.style.background = error ? '#fff0f0' : '#eff8f2';
  el.style.borderColor = error ? '#f2c2c2' : '#cbe7d3';
  el.style.color = error ? '#a2151b' : '#176c3b';
  el.textContent = text;
}

async function loadSheetMappings() {
  const holder = $('sheetAccountTabs');
  try {
    const data = await api('/api/sheets/accounts');
    if (!data.accounts?.length) { holder.innerHTML = '<div class="empty-card">Connect Gmail accounts first.</div>'; return; }
    holder.innerHTML = data.accounts.map(a => `<div class="tab-row"><div class="tab-email">${esc(a.email)}</div><input data-tab-id="${a.mailbox_id}" value="${esc(a.tab)}" maxlength="100"><button class="button save-tab" data-id="${a.mailbox_id}">Save name</button></div>`).join('');
    document.querySelectorAll('.save-tab').forEach(btn => btn.onclick = async () => {
      const id = Number(btn.dataset.id), input = document.querySelector(`input[data-tab-id="${id}"]`), tab = (input?.value || '').trim();
      if (!tab) return showSheetMessage('Tab name cannot be empty.', true);
      btn.disabled = true; btn.textContent = 'Saving…';
      try {
        const r = await api(`/api/mailboxes/${id}/sheets/tab`, {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({tab})});
        input.value = r.tab;
        showSheetMessage(`Saved tab: ${r.tab}`);
        toast('Google Sheet tab renamed.');
        await loadSheetMappings();
        if (id === mailboxId) await loadSheetStatus();
      } catch (err) { showSheetMessage(err.message, true); }
      finally { btn.disabled = false; btn.textContent = 'Save name'; }
    });
  } catch(err) { holder.innerHTML = `<div class="empty-card">Could not load mappings: ${esc(err.message)}</div>`; }
}

async function loadSheetStatus() {
  await loadSheetMappings();
  if (!mailboxId) return;
  try {
    const st = await api(`/api/mailboxes/${mailboxId}/sheets/status`);
    $('sheetExtractedEmails').textContent = st.extracted_emails || 0;
    $('sheetTotal').textContent = st.total_products || 0;
    $('sheetExportedEmails').textContent = st.exported_emails || 0;
    $('sheetExported').textContent = st.exported || 0;
    $('sheetPending').textContent = st.pending || 0;
    $('sheetErrors').textContent = st.errors || 0;
    const cols = st.columns || [];
    $('sheetColumnList').innerHTML = cols.length ? cols.map(x => `<span>${esc(x)}</span>`).join('') : '<span>Schema unavailable</span>';
    const reverify = Number(st.reverifying || 0);
    $('sheetAccuracyState').textContent = reverify
      ? `${reverify.toLocaleString()} historical PDF product row(s) are being re-verified. They are withheld from Sheet export until confirmed.`
      : (st.rebuild_required ? 'Verified Sheet rebuild is queued.' : 'Only evidence-confirmed prices are eligible for Sheet export.');
    $('sheetTabInput').value = st.tab || '';
    if (st.configured) {
      $('sheetStatusTitle').textContent = 'Automatic export active';
      $('sheetStatusSub').textContent = `${(st.exported_emails || 0).toLocaleString()} extracted email(s) are represented by ${(st.exported || 0).toLocaleString()} product row(s) in “${st.tab}”.`;
      $('openSheetBtn').href = st.url;
      $('openSheetBtn').classList.remove('hidden');
      $('existingSheetInput').value = st.url || st.sheet_id || '';
    } else {
      $('sheetStatusTitle').textContent = 'Not configured';
      $('sheetStatusSub').textContent = 'Create a shared Sheet or connect an existing one. V21.5 exports only evidence-verified rows using seven procurement columns with Original Price and Discounted Price.';
      $('openSheetBtn').classList.add('hidden');
    }
  } catch (_e) {}
}

async function openProduct(id) {
  const modal = $('productModal');
  modal.classList.remove('hidden');
  modal.setAttribute('aria-hidden','false');
  $('productModalBody').innerHTML = '<div class="modal-loading">Loading product details and source evidence…</div>';
  try {
    const d = await api(`/api/products/${id}/details`), p = d.product || {}, e = d.email || {}, ev = d.evidence || [], thread = d.gmail?.thread || '', search = d.gmail?.search || '';
    const copyText = [p.product_name, p.model ? `Model: ${p.model}` : '', p.original_price ? `Original Price: ${money(p.original_price,p.currency)}` : '', p.discount_percent !== null && p.discount_percent !== undefined && p.discount_percent !== '' ? `Discount: ${p.discount_percent}%` : '', p.purchase_price ? `Discounted Price: ${money(p.purchase_price,p.currency)}` : '', p.supplier_name ? `Supplier: ${p.supplier_name}` : '', p.specifications ? `Specifications: ${p.specifications}` : ''].filter(Boolean).join('\n');
    $('productModalBody').innerHTML = `<div class="detail-head"><div class="k">${verifyBadge(p.verification_status)}</div><h2>${esc(p.product_name || 'Product')}</h2><p>${esc(p.model || 'No model')} · Original ${money(p.original_price,p.currency)} · Discounted ${money(p.purchase_price,p.currency)}</p></div>
      <div class="detail-actions">${thread ? `<a class="button blue" href="${esc(thread)}" target="_blank" rel="noopener">Open source email in Gmail</a>` : ''}${search ? `<a class="button" href="${esc(search)}" target="_blank" rel="noopener">Search source email</a>` : ''}<button class="button" id="copyProductBtn">Copy product details</button></div>
      <div class="detail-grid">${[['Supplier',p.supplier_name],['Original price',money(p.original_price,p.currency)],['Discount',p.discount_percent !== null && p.discount_percent !== undefined && p.discount_percent !== '' ? `${p.discount_percent}%` : '—'],['Discounted price',money(p.purchase_price,p.currency)],['Price date',p.price_date],['Source',p.source_filename || p.source_type || 'Email'],['Confidence',p.verification_confidence ? `${Math.round(Number(p.verification_confidence)*100)}%` : '—'],['Sheet eligible',Number(p.export_eligible || 0) === 1 ? 'Yes' : 'No'],['Price basis',p.price_basis || '—']].map(x => `<div class="detail-field"><span>${esc(x[0])}</span><strong>${esc(x[1] || '—')}</strong></div>`).join('')}</div>
      <div class="detail-section"><h3>Quality decision</h3><div class="email-source full-text"><b>Flags</b><br>${esc(p.quality_flags || 'None')}<br><br><b>Two-price evidence</b><br>${esc(p.price_evidence || 'No final price evidence stored.')}</div></div>
      <div class="detail-section"><h3>Specifications</h3><div class="email-source full-text">${esc(p.specifications || 'No specifications extracted.')}</div></div>
      <div class="detail-section"><h3>Extraction evidence</h3>${ev.length ? ev.map(x => `<div class="evidence-row"><b>${esc(x.field_name)}</b><p>${esc(x.evidence_text)}</p></div>`).join('') : `<div class="email-source">${esc(p.evidence_summary || 'Evidence will be generated for newly verified records.')}</div>`}</div>
      <div class="detail-section"><h3>Price history / supplier comparison</h3>${(d.price_history || []).length ? `<div class="email-source history-list">${(d.price_history || []).map(x => `<div><b>${esc(x.price_date || 'Date')}</b><span>${money(x.original_price,x.currency)} → ${money(x.purchase_price,x.currency)}${x.discount_percent !== null && x.discount_percent !== undefined && x.discount_percent !== '' ? ` (${esc(x.discount_percent)}% off)` : ''}</span><span>${esc(x.supplier_name || 'Unknown supplier')}</span><span>${esc(x.verification_status || '')}</span></div>`).join('')}</div>` : '<div class="email-source">No related price history yet.</div>'}</div>
      <div class="detail-section"><h3>Source email</h3><div class="email-source"><div><b>Mailbox</b>${esc(d.mailbox_email || '')}</div><div><b>Date</b>${esc(e.received_at || '')}</div><div><b>From</b>${esc(e.sender || '')}</div><div><b>Subject</b>${esc(e.subject || '')}</div><div><b>Attachments</b>${esc(e.attachment_names || 'None')}</div><div><b>Status</b>${esc(e.status || '')}</div></div></div>`;
    const copyBtn = $('copyProductBtn');
    if (copyBtn) copyBtn.onclick = async () => { try { await navigator.clipboard.writeText(copyText); toast('Product details copied.'); } catch(_e) { toast('Could not copy to clipboard.', true); } };
  } catch(err) { $('productModalBody').innerHTML = `<div class="warning">${esc(err.message)}</div>`; }
}

function closeModal() { $('productModal').classList.add('hidden'); $('productModal').setAttribute('aria-hidden','true'); }
function stopLive() { if (liveStream) { liveStream.close(); liveStream = null; } if (fallbackTimer) { clearInterval(fallbackTimer); fallbackTimer = null; } }

function startLive() {
  stopLive();
  if (!mailboxId || document.hidden) return;
  liveStream = new EventSource(`/api/mailboxes/${mailboxId}/stream`);
  liveStream.onopen = () => {
    lastLiveMessageAt = Date.now();
    $('sidebarLiveText').textContent = 'Live · Inbox verified';
  };
  liveStream.onmessage = async e => {
    lastLiveMessageAt = Date.now();
    try {
      const d = JSON.parse(e.data || '{}');
      if (d.type === 'update') await refreshCore(currentPage === 'overview' || currentPage === 'products');
    } catch (_e) {}
  };
  liveStream.onerror = () => { $('sidebarLiveText').textContent = 'Reconnecting live updates…'; };
  // Fallback is used ONLY when SSE is not healthy; it no longer duplicates a healthy live stream.
  fallbackTimer = setInterval(() => {
    if (!mailboxId || document.hidden) return;
    const stale = !liveStream || liveStream.readyState !== EventSource.OPEN;
    if (stale) refreshCore(currentPage === 'overview' || currentPage === 'products');
  }, 12000);
}

function showPage(page) {
  currentPage = page;
  document.querySelectorAll('.nav').forEach(x => x.classList.toggle('active', x.dataset.page === page));
  document.querySelectorAll('.page').forEach(x => x.classList.add('hidden'));
  $(`${page}Page`).classList.remove('hidden');
  if (page === 'sheets') loadSheetStatus();
  if (page === 'settings') { loadConfig(); loadHealth(); }
  if (page === 'review') loadReview();
  if (page === 'inbox') { renderInbox(); }
  if (page === 'products') { renderProductsPage(); }
  if (page === 'overview') { loadActivity(); }
}

function bind() {
  document.querySelectorAll('[data-close-modal]').forEach(x => x.onclick = closeModal);
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape') closeModal();
    if (e.key === '/' && !['INPUT','TEXTAREA','SELECT'].includes(document.activeElement?.tagName)) { e.preventDefault(); (currentPage === 'products' ? $('searchInput2') : $('searchInput'))?.focus(); }
  });
  document.querySelectorAll('.nav').forEach(btn => btn.onclick = () => showPage(btn.dataset.page));
  document.querySelectorAll('[data-jump]').forEach(btn => btn.onclick = () => showPage(btn.dataset.jump));

  $('mailboxSelect').onchange = async e => {
    mailboxId = Number(e.target.value) || null;
    if (mailboxId) {
      localStorage.setItem('nunes_selected_mailbox_id', String(mailboxId));
      await refreshAll(true);
      startLive();
    } else setEmpty();
  };

  $('syncBtn').onclick = async () => {
    if (!mailboxId) return toast('Connect Gmail first.', true);
    const b = $('syncBtn'); b.disabled = true; b.textContent = 'Checking…';
    try { await api(`/api/mailboxes/${mailboxId}/sync`, {method:'POST'}); toast('Inbox check started.'); await refreshAll(true); }
    catch(e) { toast(e.message, true); }
    finally { b.disabled = false; b.textContent = 'Check now'; }
  };

  $('retryErrorsBtn').onclick = async () => { if (!mailboxId) return; try { await api(`/api/mailboxes/${mailboxId}/retry-errors`, {method:'POST'}); toast('Failures returned to the processing queue.'); await refreshAll(true); } catch(e){toast(e.message,true);} };
  $('restartHistoryBtn').onclick = async () => { if (!mailboxId) return; if (!confirm('Verify every current Inbox message ID again?')) return; try { await api(`/api/mailboxes/${mailboxId}/history/restart`, {method:'POST'}); toast('Full Inbox verification restarted.'); await refreshAll(true); } catch(e){toast(e.message,true);} };
  $('cleanDupesBtn').onclick = async () => { if (!mailboxId) return; try { await api(`/api/mailboxes/${mailboxId}/clean-duplicates`, {method:'POST'}); toast('Exact duplicate cleanup completed.'); await refreshAll(true); } catch(e){toast(e.message,true);} };
  $('recoverEngineBtn').onclick = async () => {
    if (!mailboxId) return toast('Select a Gmail account first.', true);
    const b = $('recoverEngineBtn');
    const before = lastStats ? Number(lastStats.completed_emails || 0) : null;
    b.disabled = true; b.textContent = 'Recovering…';
    $('engineAlert').className = 'engine-alert';
    $('engineAlert').textContent = 'Recovery is rebuilding the selected mailbox queue and waking dedicated direct/AI workers…';
    try {
      const r = await api(`/api/engine/recover?mailbox_id=${mailboxId}`, {method:'POST', timeoutMs:15000});
      toast(`Recovery started · ${r.requeued_active || 0} active state(s) requeued · ${r.ready_jobs || 0} direct job(s) ready.`);
      let progressed = false;
      for (let i=0; i<30; i++) {
        await new Promise(resolve => setTimeout(resolve, 2000));
        try {
          const st = await api(`/api/mailboxes/${mailboxId}/stats`, {timeoutMs:7000, retries:1});
          updateStats(st);
          const completed = Number(st.completed_emails || 0);
          if ((before !== null && completed > before) || !st.stalled) {
            progressed = true;
            break;
          }
        } catch (_e) {}
      }
      await Promise.allSettled([loadHealth(), loadConfig()]);
      if (progressed) {
        toast('Processing activity resumed.');
      } else {
        $('engineAlert').className = 'engine-alert error';
        $('engineAlert').textContent = 'Recovery ran, but no completion was detected yet. The queue is preserved; check Review/Inbox. A repeatedly failing document is quarantined automatically instead of blocking the mailbox.';
        toast('Recovery completed but processing has not advanced yet.', true);
      }
    } catch(e) {
      toast(e.message, true);
    } finally {
      b.disabled = false; b.textContent = 'Recover engine';
    }
  };

  $('searchInput').oninput = e => {
    const q = e.target.value.toLowerCase();
    const rows = productsCache.filter(p => [p.product_name,p.model,p.supplier_name,p.specifications].some(v => String(v||'').toLowerCase().includes(q))).slice(0,60);
    $('productTable').innerHTML = productHTML(rows); bindProductButtons();
  };

  let searchTimer = null;
  $('searchInput2').oninput = e => {
    clearTimeout(searchTimer);
    const q = e.target.value.trim();
    searchTimer = setTimeout(async () => {
      if (!mailboxId) return;
      try {
        const rows = await api(`/api/mailboxes/${mailboxId}/products?q=${encodeURIComponent(q)}&limit=1000`);
        productsCache = rows;
        productPage = 1;
        renderProductsPage(rows);
      } catch(_e) {}
    }, 220);
  };
  $('productSort').onchange = () => { productPage = 1; renderProductsPage(); };
  $('productPrev').onclick = () => { if (productPage > 1) { productPage--; renderProductsPage(); } };
  $('productNext').onclick = () => { productPage++; renderProductsPage(); };
  $('inboxSearch').oninput = renderInbox;
  $('inboxFilter').onchange = renderInbox;

  $('createSheetBtn').onclick = async () => {
    if (!mailboxId) return toast('Connect/select Gmail first.', true);
    const b = $('createSheetBtn'); b.disabled = true; b.textContent = 'Creating…'; showSheetMessage('Creating spreadsheet and Gmail tabs…');
    try {
      const r = await api(`/api/mailboxes/${mailboxId}/sheets/create`, {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({tab:$('sheetTabInput').value || undefined})});
      showSheetMessage('Sheet ready. Confirmed products export in the background.'); toast('Google Sheet created.');
      if (r.url) window.open(r.url, '_blank');
      await loadSheetStatus();
    } catch(e) { showSheetMessage(e.message, true); toast(e.message, true); }
    finally { b.disabled = false; b.textContent = 'Create Sheet'; }
  };

  $('connectSheetBtn').onclick = async () => {
    if (!mailboxId) return;
    const sheet = $('existingSheetInput').value.trim();
    if (!sheet) return showSheetMessage('Paste a Sheet URL or ID.', true);
    try { await api(`/api/mailboxes/${mailboxId}/sheets/configure`, {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({sheet,tab:$('sheetTabInput').value || undefined})}); showSheetMessage('Google Sheet connected.'); toast('Google Sheet connected.'); await loadSheetStatus(); }
    catch(e) { showSheetMessage(e.message, true); }
  };

  $('exportAllSheetBtn').onclick = async () => {
    if (!mailboxId) return;
    try { const r = await api(`/api/mailboxes/${mailboxId}/sheets/export-all`, {method:'POST'}); showSheetMessage(`Exported ${r.exported || 0} confirmed pending product(s).`); toast('Pending Sheet export completed.'); await loadSheetStatus(); }
    catch(e) { showSheetMessage(e.message, true); }
  };




  $('rebuildSheetBtn').onclick = async () => {
    if (!mailboxId) return toast('Select a Gmail account first.', true);
    if (!confirm('Replace this Gmail tab with exactly the currently CONFIRMED database products using the seven-column V21.5 two-price verified schema?')) return;
    const b = $('rebuildSheetBtn'); b.disabled = true; b.textContent = 'Rebuilding…';
    try {
      const r = await api(`/api/mailboxes/${mailboxId}/sheets/rebuild`, {method:'POST', timeoutMs:60000});
      showSheetMessage(`Verified Sheet rebuilt with ${Number(r.rows || 0).toLocaleString()} product row(s).`);
      toast('Verified Sheet rebuilt.');
      await loadSheetStatus();
    } catch(e) {
      showSheetMessage(e.message, true); toast(e.message, true);
    } finally { b.disabled = false; b.textContent = 'Rebuild verified Sheet'; }
  };

  window.addEventListener('offline', () => { $('engineAlert').className = 'engine-alert error'; $('engineAlert').textContent = 'This PC is offline. Existing data remains safe; Gmail/Local AI/Sheets will resume when the connection returns.'; });
  window.addEventListener('online', () => { toast('Internet connection restored. Live services are resuming.'); refreshAll(true); });
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) {
      stopLive();
      return;
    }
    updateServerClock(true);
    loadHealth();
    startLive();
    refreshCore(currentPage === 'overview' || currentPage === 'products');
  });
}

function renderServerClock() {
  if (!serverClockEpochMs) return;
  const now = new Date(serverClockEpochMs + (Date.now() - serverClockSyncedAtMs));
  const text = now.toLocaleString('en-IN', {hour12:false});
  $('serverClock').textContent = `Server time · ${text} IST`;
}

async function updateServerClock(force=false) {
  if (document.hidden && !force) return;
  try {
    const t = await api('/api/server-time', {timeoutMs:5000});
    const parsed = Number(t.epoch_ms) || Date.parse(t.iso || '');
    if (Number.isFinite(parsed)) {
      serverClockEpochMs = parsed;
      serverClockSyncedAtMs = Date.now();
      renderServerClock();
    } else if (t.text) {
      $('serverClock').textContent = `Server time · ${t.text}`;
    }
  } catch(_e) {}
}

// The clock renders locally every second. The server is contacted only once per minute.
setInterval(renderServerClock, 1000);
setInterval(() => !document.hidden && updateServerClock(), 60000);
setInterval(() => !document.hidden && loadHealth(), 20000);
setInterval(() => !document.hidden && currentPage === 'settings' && loadConfig(), 30000);
setInterval(() => !document.hidden && currentPage === 'sheets' && loadSheetStatus(), 45000);

(async () => {
  bind();
  const params = new URLSearchParams(location.search), preferred = Number(params.get('mailbox_id') || 0);

  // Render a previously known mailbox immediately. A transient API timeout
  // must never repaint the dashboard as a fresh "No Gmail connected" install.
  restoreMailboxCache(preferred);

  await Promise.allSettled([
    loadConfig(),
    updateServerClock(true),
    loadHealth()
  ]);

  try {
    await loadMailboxes(preferred, {preserveOnError:true});
  } catch (_e) {
    scheduleMailboxReconnect(preferred);
  }
  startLive();
})();
// V21 free multi-AI ensemble health. Non-blocking and optional.
async function v21LoadEnsembleHealth(){
  try{
    const r = await fetch('/api/ai/ensemble-health', {cache:'no-store'});
    if(!r.ok) return;
    const d = await r.json();
    const target = document.getElementById('settingsStatus2') || document.getElementById('settingsStatus');
    if(!target || document.getElementById('v21AiEnsemble')) return;
    const local = d.local || {};
    const box = document.createElement('div');
    box.id='v21AiEnsemble'; box.className='ai-ensemble-note';
    box.innerHTML = `<strong>V21.5 exception AI</strong><br>Python handles normal work first.<br>Local Ollama: <span class="${local.online?'ok':'muted'}">${local.online?'Online':'Optional / offline'}</span><br><span class="muted">AI proposes only; Python evidence remains final authority.</span>`;
    target.parentElement?.appendChild(box);
  }catch(_e){}
}
setTimeout(v21LoadEnsembleHealth, 1800);


// V21.5 local exception-AI runtime verification.
const localAiTestBtn = document.getElementById('localAiTestBtn');
if (localAiTestBtn) localAiTestBtn.addEventListener('click', async () => {
  const old=localAiTestBtn.textContent; localAiTestBtn.disabled=true; localAiTestBtn.textContent='Testing models…';
  try { const r=await api('/api/ai/local-self-test',{method:'POST'}); toast(r.ok?'Local AI runtime test passed':'Local AI needs attention', r.ok?'success':'error'); await loadConfig(); }
  catch(e){ toast(`Local AI test failed: ${e.message||e}`,'error'); }
  finally { localAiTestBtn.disabled=false; localAiTestBtn.textContent=old; }
});
