let trustlineSet = false;
let tradeData = {};
let openOffers = [];
let trackInterval = null;
let topupPayInterval = null;
let tlCurrentStep = 1;
let tlConfirmInFlight = false;

const appState = {
  username: localStorage.getItem('xrpl_username') || '',
  address: '',
  xrpBalance: 0,
  trustlines: [],
  tokenBalances: [],
  openOffers: [],
  history: [],
  normalizedHistory: [],
  tokenRegistry: {},
  activeTrade: null,
};

const RIPPLE_EPOCH_OFFSET = 946684800;
const SIDEBAR_COLLAPSED_KEY = 'xrpl_sidebar_collapsed';

function shortAddress(addr) {
  if (!addr || typeof addr !== 'string') return '—';
  if (addr.length < 12) return addr;
  return `${addr.slice(0, 6)}...${addr.slice(-6)}`;
}

function formatNum(value, decimals = 2) {
  const num = Number(value);
  if (!Number.isFinite(num)) return '0.00';
  return num.toLocaleString(undefined, {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  });
}

function setInlineMessage(id, message, isError = false) {
  const el = document.getElementById(id);
  if (!el) return;
  el.textContent = message;
  el.style.color = isError ? '#991b1b' : 'var(--muted)';
}

async function fetchJson(url, options = {}) {
  const response = await fetch(url, options);
  let body = {};
  try {
    body = await response.json();
  } catch (_) {
    body = {};
  }

  if (!response.ok || body.success === false) {
    throw new Error(body.error || 'Request failed');
  }

  return body.data;
}

function getRequiredUsername() {
  const username = (appState.username || '').trim().toLowerCase();
  if (!username) {
    window.location.href = '/';
    throw new Error('No active username session');
  }
  return username;
}

async function apiGet(path, query = {}) {
  const params = new URLSearchParams(query);
  const qs = params.toString();
  const url = qs ? `${path}?${qs}` : path;
  return fetchJson(url);
}

async function apiPost(path, data) {
  return fetchJson(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
}

function normalizeCurrency(value) {
  return String(value || '').trim().toUpperCase();
}

function resolveIssuerClient(currency, overrideIssuer = '') {
  const upper = normalizeCurrency(currency);
  if (upper === 'XRP') return '';
  const override = String(overrideIssuer || '').trim();
  if (override) return override;
  return appState.tokenRegistry[upper] || '';
}

function hasTrustlineFor(currency, issuer = '') {
  const upper = normalizeCurrency(currency);
  if (upper === 'XRP') return true;

  const normalizedIssuer = String(issuer || '').trim();
  return appState.trustlines.some((line) => {
    const lineCurrency = normalizeCurrency(line.currency);
    const lineIssuer = String(line.issuer || '').trim();

    if (lineCurrency !== upper) return false;
    if (!normalizedIssuer) return true;
    return lineIssuer === normalizedIssuer;
  });
}

function rippleDateToLocalString(rippleDate) {
  const unixSeconds = Number(rippleDate);
  if (!Number.isFinite(unixSeconds)) return 'Unknown time';
  const date = new Date((unixSeconds + RIPPLE_EPOCH_OFFSET) * 1000);
  return date.toLocaleString();
}

function normalizeAmountValue(amount) {
  if (typeof amount === 'string') {
    const drops = Number(amount);
    if (!Number.isFinite(drops)) {
      return { currency: 'XRP', issuer: '', value: amount };
    }
    return { currency: 'XRP', issuer: '', value: (drops / 1_000_000).toString() };
  }

  if (amount && typeof amount === 'object') {
    return {
      currency: normalizeCurrency(amount.currency),
      issuer: amount.issuer || '',
      value: String(amount.value || '0'),
    };
  }

  return { currency: '', issuer: '', value: '0' };
}

function amountToDisplay(amountObj) {
  const amount = normalizeAmountValue(amountObj);
  return `${formatNum(amount.value, 6).replace(/\.?0+$/, '')} ${amount.currency || ''}`.trim();
}

function chooseTxType(txType, tx, appAddress) {
  if (txType === 'Payment') {
    if ((tx.Account || '') === appAddress) return 'out';
    return 'in';
  }
  if (txType === 'EscrowCreate' || txType === 'EscrowFinish') return 'escrow';
  if (txType === 'OfferCreate' || txType === 'OfferCancel') return 'trade';
  if (txType === 'TrustSet') return 'trustline';
  return 'other';
}

function normalizeHistory(historyEntries) {
  const address = appState.address;
  return (historyEntries || []).map((entry) => {
    const tx = entry.tx || {};
    const meta = entry.meta || {};
    const txType = tx.TransactionType || 'Unknown';
    const success = meta.TransactionResult === 'tesSUCCESS';
    const type = chooseTxType(txType, tx, address);

    let description = `${txType} transaction`;
    let amountLabel = '';

    if (txType === 'Payment') {
      const amount = normalizeAmountValue(tx.Amount);
      amountLabel = amountToDisplay(tx.Amount);
      if ((tx.Account || '') === address) {
        description = `Sent · ${amountLabel} to ${shortAddress(tx.Destination || '')}`;
      } else {
        description = `Received · ${amountLabel} from ${shortAddress(tx.Account || '')}`;
      }
    } else if (txType === 'TrustSet') {
      const limitAmount = tx.LimitAmount || {};
      const currency = normalizeCurrency(limitAmount.currency || 'TOKEN');
      const issuer = shortAddress(limitAmount.issuer || '');
      description = `Trust line · ${currency} (${issuer})`;
    } else if (txType === 'OfferCreate') {
      description = `Swap offer · ${amountToDisplay(tx.TakerGets)} → ${amountToDisplay(tx.TakerPays)}`;
    } else if (txType === 'OfferCancel') {
      description = `Swap offer cancelled · #${tx.OfferSequence || '—'}`;
    } else if (txType === 'EscrowCreate') {
      description = `Scheduled payment · ${amountToDisplay(tx.Amount)} to ${shortAddress(tx.Destination || '')}`;
    } else if (txType === 'EscrowFinish') {
      description = `Scheduled payment released · #${tx.OfferSequence || '—'}`;
    }

    return {
      txType,
      type,
      description,
      amountLabel,
      success,
      badgeText: success ? 'Confirmed' : 'Failed',
      badgeClass: success ? 'badge-confirmed' : 'badge-expired',
      timestamp: rippleDateToLocalString(tx.date),
      hash: tx.hash || '—',
      tx,
      meta,
    };
  });
}

function renderHistoryCards() {
  const records = document.getElementById('records-list');
  const recent = document.getElementById('dashboard-recent-activity');
  const walletTx = document.getElementById('wallet-tx-list');

  if (!records || !recent || !walletTx) return;

  if (appState.normalizedHistory.length === 0) {
    const empty = '<div class="info-row"><span class="label">No records yet.</span><span class="badge badge-open"><span class="badge-dot"></span> Empty</span></div>';
    records.innerHTML = empty;
    recent.innerHTML = empty;
    walletTx.innerHTML = '<div class="tx-row" data-type="all"><div class="tx-body"><div class="tx-desc">No transactions yet.</div></div></div>';
    return;
  }

  records.innerHTML = appState.normalizedHistory
    .map((item) => `
      <div class="info-row">
        <span class="label">${item.description}</span>
        <span class="badge ${item.badgeClass}"><span class="badge-dot"></span> ${item.badgeText}</span>
      </div>
    `)
    .join('');

  recent.innerHTML = appState.normalizedHistory
    .slice(0, 3)
    .map((item) => `
      <div class="info-row">
        <span class="label">${item.description}</span>
        <span class="badge ${item.badgeClass}"><span class="badge-dot"></span> ${item.badgeText}</span>
      </div>
    `)
    .join('');

  walletTx.innerHTML = appState.normalizedHistory
    .map((item) => {
      const dataType = item.type === 'in' || item.type === 'out' ? item.type : 'all';
      const icon = item.type === 'in' ? '↓' : item.type === 'out' ? '↑' : item.type === 'trade' ? '⇄' : item.type === 'escrow' ? '◷' : '●';
      const amountText = item.amountLabel || item.hash;
      const amountClass = item.type === 'out' ? 'tx-out-amt' : 'tx-in';

      return `
        <div class="tx-row" data-type="${dataType}">
          <div class="tx-icon tx-out">${icon}</div>
          <div class="tx-body">
            <div class="tx-desc">${item.description}</div>
            <div class="tx-meta">${item.timestamp}</div>
          </div>
          <div class="tx-amount ${amountClass}">${amountText}</div>
        </div>
      `;
    })
    .join('');
}

function renderEscrowHistory() {
  const escrowList = document.getElementById('escrow-list');
  const totalHeld = document.getElementById('escrow-total-held');
  const totalHeldSub = document.getElementById('escrow-total-held-sub');
  const readyHeld = document.getElementById('escrow-ready-to-release');
  const readyHeldSub = document.getElementById('escrow-ready-to-release-sub');
  const expiringSoon = document.getElementById('escrow-expiring-soon');

  if (!escrowList || !totalHeld || !readyHeld || !expiringSoon) return;

  const creates = appState.normalizedHistory
    .filter((item) => item.txType === 'EscrowCreate' && item.success)
    .map((item) => {
      const tx = item.tx;
      const finishAfter = Number(tx.FinishAfter || 0);
      const cancelAfter = Number(tx.CancelAfter || 0);
      const nowRipple = Math.floor(Date.now() / 1000) - RIPPLE_EPOCH_OFFSET;
      const amountObj = normalizeAmountValue(tx.Amount);
      const amount = Number(amountObj.value || 0);

      const finished = appState.normalizedHistory.some(
        (entry) =>
          entry.txType === 'EscrowFinish' &&
          Number(entry.tx.OfferSequence || -1) === Number(tx.Sequence || -2) &&
          entry.success,
      );

      let status = 'pending';
      if (finished) {
        status = 'claimable';
      } else if (cancelAfter && cancelAfter < nowRipple) {
        status = 'expired';
      } else if (finishAfter && finishAfter <= nowRipple) {
        status = 'claimable';
      }

      const expiresSoon = !!cancelAfter && cancelAfter > nowRipple && cancelAfter - nowRipple <= 7 * 24 * 3600;

      return {
        sequence: Number(tx.Sequence || 0),
        destination: tx.Destination || '',
        amount,
        amountLabel: `${formatNum(amount, 2)} ${amountObj.currency || 'XRP'}`,
        finishAfter,
        cancelAfter,
        status,
        expiresSoon,
      };
    });

  const active = creates.filter((escrow) => escrow.status !== 'expired');
  const ready = creates.filter((escrow) => escrow.status === 'claimable');
  const expSoonCount = creates.filter((escrow) => escrow.expiresSoon).length;

  const totalHeldAmount = active.reduce((sum, escrow) => sum + escrow.amount, 0);
  const readyAmount = ready.reduce((sum, escrow) => sum + escrow.amount, 0);

  totalHeld.textContent = `${formatNum(totalHeldAmount, 2)} XRP`;
  totalHeldSub.textContent = `across ${active.length} payments`;
  readyHeld.textContent = `${formatNum(readyAmount, 2)} XRP`;
  readyHeldSub.textContent = `${ready.length} payments ready`;
  expiringSoon.textContent = String(expSoonCount);

  if (creates.length === 0) {
    escrowList.innerHTML = '<div class="card"><div class="info-row"><span class="label">No escrow activity found in your history.</span><span class="badge badge-open"><span class="badge-dot"></span> Empty</span></div></div>';
    return;
  }

  escrowList.innerHTML = creates
    .map((escrow) => {
      const badgeClass =
        escrow.status === 'claimable'
          ? 'badge-matched'
          : escrow.status === 'expired'
            ? 'badge-expired'
            : 'badge-pending';

      const badgeText =
        escrow.status === 'claimable'
          ? 'Claimable'
          : escrow.status === 'expired'
            ? 'Expired'
            : 'Pending';

      const finishDate = escrow.finishAfter ? rippleDateToLocalString(escrow.finishAfter) : '—';
      const cancelDate = escrow.cancelAfter ? rippleDateToLocalString(escrow.cancelAfter) : '—';

      return `
        <div class="escrow-row" data-status="${escrow.status}" onclick="toggleEscrow(this)">
          <div class="escrow-row-top">
            <div class="escrow-row-left">
              <div class="escrow-row-id">Escrow Sequence #${escrow.sequence}</div>
              <div class="escrow-row-amount">${escrow.amountLabel}</div>
              <div class="escrow-row-dest">To: ${shortAddress(escrow.destination)}</div>
            </div>
            <div class="escrow-row-right">
              <span class="badge ${badgeClass}"><span class="badge-dot"></span> ${badgeText}</span>
              <span class="escrow-chevron">▾</span>
            </div>
          </div>
          <div class="escrow-row-detail">
            <div class="detail-grid">
              <div class="detail-cell"><div class="detail-label">Amount</div><div class="detail-value">${escrow.amountLabel}</div></div>
              <div class="detail-cell"><div class="detail-label">Release date</div><div class="detail-value">${finishDate}</div></div>
              <div class="detail-cell"><div class="detail-label">Cancel deadline</div><div class="detail-value">${cancelDate}</div></div>
              <div class="detail-cell"><div class="detail-label">Destination</div><div class="detail-value">${shortAddress(escrow.destination)}</div></div>
            </div>
          </div>
        </div>
      `;
    })
    .join('');
}

function renderTokenList() {
  const tokenList = document.getElementById('token-list');
  if (!tokenList) return;

  const rows = [];

  rows.push(`
    <div class="token-row">
      <div class="token-logo">XRP</div>
      <div class="token-body"><div class="token-name">XRP</div><div class="token-issuer">Main currency</div></div>
      <div class="token-right"><div class="token-amount">${formatNum(appState.xrpBalance, 6).replace(/\.?0+$/, '')}</div><div class="token-fiat">Ledger balance</div></div>
    </div>
  `);

  appState.tokenBalances.forEach((token) => {
    rows.push(`
      <div class="token-row">
        <div class="token-logo" style="background:#dbeafe;border-color:#bfdbfe;color:#1e40af">${token.currency}</div>
        <div class="token-body"><div class="token-name">${token.currency}</div><div class="token-issuer">${shortAddress(token.issuer)}</div></div>
        <div class="token-right"><div class="token-amount">${token.balance}</div><div class="token-fiat">Limit: ${token.limit}</div></div>
      </div>
    `);
  });

  tokenList.innerHTML = rows.join('');
}

function renderOpenOffers() {
  const container = document.getElementById('open-offers-list');
  if (!container) return;

  openOffers = appState.openOffers;

  if (openOffers.length === 0) {
    container.innerHTML = `
      <div class="empty-state empty-state-actions">
        <button class="empty-state-action" type="button" onclick="openSwapFromDashboard()">
          <div class="empty-icon">⇄</div>
          <div class="empty-action-title">No active swaps.</div>
          <div class="empty-action-desc">Start a swap to get going.</div>
        </button>
        <button class="empty-state-action secondary" type="button" onclick="openTokenEnableFromDashboard()">
          <div class="empty-icon">⬡</div>
          <div class="empty-action-title">Enable a token</div>
          <div class="empty-action-desc">Set up USD, BTC or other tokens first.</div>
        </button>
      </div>
    `;
    return;
  }

  container.innerHTML = openOffers
    .map((offer, index) => `
      <div class="offer-card">
        <div class="offer-info">
          <div class="offer-id">Offer #${offer.offer_sequence}</div>
          <div class="offer-pair">${offer.sell.value} ${offer.sell.currency} → ${offer.buy.value} ${offer.buy.currency}</div>
          <div class="offer-time">Status: ${offer.status}</div>
        </div>
        <div class="offer-actions">
          <span class="badge badge-open"><span class="badge-dot"></span> ${offer.status}</span>
          <button class="btn-sm" onclick="cancelOfferById(${index})">Cancel</button>
        </div>
      </div>
    `)
    .join('');
}

function openSwapFromDashboard() {
  showPage('trade');
  resetTrade();
}

function openTokenEnableFromDashboard() {
  showPage('trustline');
  tlResetFlow(true);
}

function renderSummary() {
  const addr = appState.address || '—';
  const balance = `${formatNum(appState.xrpBalance, 6).replace(/\.?0+$/, '')} XRP`;

  const dashboardAddr = document.getElementById('dashboard-wallet-address');
  const dashboardBal = document.getElementById('dashboard-xrp-balance');
  const walletMainAddr = document.getElementById('wallet-main-address');
  const walletTotalBal = document.getElementById('wallet-total-balance');

  if (dashboardAddr) dashboardAddr.textContent = addr;
  if (dashboardBal) dashboardBal.textContent = balance;
  if (walletTotalBal) walletTotalBal.innerHTML = `${formatNum(appState.xrpBalance, 6).replace(/\.?0+$/, '')} <span style="font-size:22px;opacity:0.5">XRP</span>`;

  if (walletMainAddr) {
    walletMainAddr.innerHTML = `<span>${addr}</span><span style="font-size:10px">⧉</span>`;
  }

  document.querySelectorAll('span').forEach((el) => {
    if (el.textContent === 'rConnected...abc') {
      el.textContent = shortAddress(addr);
    }
  });

  trustlineSet = appState.trustlines.some((line) => normalizeCurrency(line.currency) !== 'XRP');
}

function syncUserChip() {
  const username = appState.username;
  const chip = document.querySelector('.user-email');
  const avatar = document.querySelector('.avatar');
  if (chip) chip.textContent = username || chip.textContent;
  if (avatar && username) avatar.textContent = username[0].toUpperCase();
}

function applySidebarCollapsedState(collapsed, persist = true) {
  const shouldCollapse = !!collapsed && window.innerWidth > 900;
  document.body.classList.toggle('sidebar-collapsed', shouldCollapse);

  const toggleBtn = document.getElementById('sidebar-toggle');
  const toggleIcon = document.getElementById('sidebar-toggle-icon');
  const label = shouldCollapse ? 'Expand sidebar' : 'Collapse sidebar';

  if (toggleBtn) {
    toggleBtn.setAttribute('aria-label', label);
    toggleBtn.title = label;
  }

  if (toggleIcon) {
    toggleIcon.textContent = shouldCollapse ? '›' : '‹';
  }

  if (persist) {
    localStorage.setItem(SIDEBAR_COLLAPSED_KEY, collapsed ? '1' : '0');
  }
}

function initSidebarToggle() {
  const preferredCollapsed = localStorage.getItem(SIDEBAR_COLLAPSED_KEY) === '1';
  applySidebarCollapsedState(preferredCollapsed, false);

  window.addEventListener('resize', () => {
    const currentPreference = localStorage.getItem(SIDEBAR_COLLAPSED_KEY) === '1';
    applySidebarCollapsedState(currentPreference, false);
  });
}

function toggleSidebar() {
  const currentlyCollapsed = document.body.classList.contains('sidebar-collapsed');
  applySidebarCollapsedState(!currentlyCollapsed);
}

async function loadTokenRegistry() {
  const data = await apiGet('/api/config/tokens');
  appState.tokenRegistry = data.tokens || {};
}

async function refreshSummary() {
  const data = await apiGet('/api/wallet/summary', { username: getRequiredUsername() });
  appState.address = data.address || '';
  appState.xrpBalance = Number(data.xrp_balance || 0);
  appState.tokenBalances = data.token_balances || [];
  appState.trustlines = data.trustlines || [];

  if (!appState.tokenRegistry || Object.keys(appState.tokenRegistry).length === 0) {
    appState.tokenRegistry = data.token_registry || {};
  }

  renderSummary();
  renderTokenList();
  syncTradeIssuerFields();
  syncSendIssuerField();
  checkCurrencyGate('trade');
  checkCurrencyGate('send');
}

async function refreshHistory() {
  const data = await apiGet('/api/wallet/history', { username: getRequiredUsername() });
  appState.history = data.history || [];
  appState.normalizedHistory = normalizeHistory(appState.history);
  renderHistoryCards();
  renderEscrowHistory();
}

async function refreshOpenOffers() {
  const data = await apiGet('/api/trade/open', { username: getRequiredUsername() });
  appState.openOffers = data.offers || [];
  renderOpenOffers();
}

async function refreshAllData() {
  await Promise.all([refreshSummary(), refreshHistory(), refreshOpenOffers()]);
}

async function initApp() {
  if (!appState.username) {
    window.location.href = '/';
    return;
  }

  syncUserChip();
  initSidebarToggle();

  try {
    await loadTokenRegistry();
    await refreshAllData();
  } catch (error) {
    alert(error.message || 'Failed to initialize dashboard data.');
  }

  markDemoOnlyPanels();

  const sendCurrencySelect = document.getElementById('send-currency');
  if (sendCurrencySelect) {
    sendCurrencySelect.addEventListener('change', () => {
      syncSendIssuerField();
      checkCurrencyGate('send');
    });
  }

  const sellCurrencySelect = document.getElementById('sell-currency');
  if (sellCurrencySelect) {
    sellCurrencySelect.addEventListener('change', () => {
      syncTradeIssuerFields();
      checkCurrencyGate('trade');
    });
  }

  const buyCurrencySelect = document.getElementById('buy-currency');
  if (buyCurrencySelect) {
    buyCurrencySelect.addEventListener('change', () => {
      syncTradeIssuerFields();
      checkCurrencyGate('trade');
    });
  }
}

function markDemoOnlyPanels() {
  const footnote = document.getElementById('auth-result-footnote');
  if (footnote) footnote.textContent = 'Results are fetched from backend XRPL checks.';
}

function syncSendIssuerField() {
  const currency = normalizeCurrency(document.getElementById('send-currency')?.value);
  const issuerGroup = document.getElementById('send-issuer-group');
  const issuerInput = document.getElementById('send-issuer');

  if (!issuerGroup || !issuerInput) return;

  if (currency === 'XRP') {
    issuerGroup.style.display = 'none';
    issuerInput.value = '';
    return;
  }

  issuerGroup.style.display = 'block';
  if (!issuerInput.value.trim()) {
    issuerInput.value = appState.tokenRegistry[currency] || '';
  }
}

function syncTradeIssuerFields() {
  const sellCurrency = normalizeCurrency(document.getElementById('sell-currency')?.value);
  const buyCurrency = normalizeCurrency(document.getElementById('buy-currency')?.value);

  const sellGroup = document.getElementById('sell-issuer-group');
  const sellInput = document.getElementById('sell-issuer');
  const buyGroup = document.getElementById('buy-issuer-group');
  const buyInput = document.getElementById('buy-issuer');

  if (sellGroup && sellInput) {
    if (sellCurrency === 'XRP') {
      sellGroup.style.display = 'none';
      sellInput.value = '';
    } else {
      sellGroup.style.display = 'block';
      if (!sellInput.value.trim()) sellInput.value = appState.tokenRegistry[sellCurrency] || '';
    }
  }

  if (buyGroup && buyInput) {
    if (buyCurrency === 'XRP') {
      buyGroup.style.display = 'none';
      buyInput.value = '';
    } else {
      buyGroup.style.display = 'block';
      if (!buyInput.value.trim()) buyInput.value = appState.tokenRegistry[buyCurrency] || '';
    }
  }
}

function requireTrustline(page) {
  showPage(page);
  if (page === 'trade') resetTrade();
  checkCurrencyGate(page);
}

function checkCurrencyGate(page) {
  const gate = document.getElementById(`${page}-gate`);
  if (!gate) return;

  if (page === 'trade') {
    const sellCurrency = normalizeCurrency(document.getElementById('sell-currency')?.value);
    const buyCurrency = normalizeCurrency(document.getElementById('buy-currency')?.value);
    const sellIssuer = resolveIssuerClient(sellCurrency, document.getElementById('sell-issuer')?.value || '');
    const buyIssuer = resolveIssuerClient(buyCurrency, document.getElementById('buy-issuer')?.value || '');

    const sellNeedsTrustline = sellCurrency !== 'XRP' && !hasTrustlineFor(sellCurrency, sellIssuer);
    const buyNeedsTrustline = buyCurrency !== 'XRP' && !hasTrustlineFor(buyCurrency, buyIssuer);
    gate.style.display = sellNeedsTrustline || buyNeedsTrustline ? 'flex' : 'none';
  }

  if (page === 'send') {
    const currency = normalizeCurrency(document.getElementById('send-currency')?.value);
    const issuer = resolveIssuerClient(currency, document.getElementById('send-issuer')?.value || '');
    const needsGate = currency !== 'XRP' && !hasTrustlineFor(currency, issuer);
    gate.style.display = needsGate ? 'flex' : 'none';
  }
}

function tlSetStep(n) {
  tlCurrentStep = n;
  [1, 2, 3].forEach((i) => {
    const el = document.getElementById(`tl-step-${i}`);
    if (el) el.style.display = i === n ? 'block' : 'none';

    const prog = document.getElementById(`tl-prog-${i}`);
    if (prog) {
      prog.className = `step${i < n ? ' done' : i === n ? ' active' : ''}`;
      const num = prog.querySelector('.step-num');
      if (num) num.textContent = i < n ? '✓' : String(i);
    }
  });

  updateTlCancelVisibility();
}

function updateTlCancelVisibility() {
  const wrap = document.getElementById('tl-cancel-wrap');
  const btn = document.getElementById('tl-cancel-btn');
  if (!wrap || !btn) return;

  const canCancel = tlCurrentStep === 2 && !tlConfirmInFlight;
  wrap.style.display = canCancel ? 'flex' : 'none';
  btn.disabled = tlConfirmInFlight;
  btn.textContent = tlConfirmInFlight ? 'Please wait...' : 'Cancel process';
}

function tlResetFlow(clearInputs = false) {
  tlSetStep(1);
  window.__tlDraft = null;

  const verifyResults = document.getElementById('tl-verify-results');
  const verifyVerdict = document.getElementById('tl-verify-verdict');
  const verifyLoading = document.getElementById('tl-verify-loading');
  const verifyIssuer = document.getElementById('tl-verify-issuer-display');
  const verifyCurrency = document.getElementById('tl-verify-currency-display');
  const verifyBadge = document.getElementById('tl-verify-badge');
  const confirmBtn = document.getElementById('tl-confirm-btn');
  const confirmWarning = document.getElementById('tl-confirm-warning');
  const tlAuthLoading = document.getElementById('tl-auth-loading');
  const tlAuthResult = document.getElementById('tl-auth-result');

  if (verifyResults) verifyResults.style.display = 'none';
  if (verifyVerdict) verifyVerdict.style.display = 'none';
  if (verifyLoading) verifyLoading.style.display = 'block';
  if (verifyIssuer) verifyIssuer.textContent = '';
  if (verifyCurrency) verifyCurrency.textContent = '';
  if (confirmWarning) confirmWarning.textContent = '';

  if (verifyBadge) {
    verifyBadge.className = 'badge badge-pending';
    verifyBadge.innerHTML = '<span class="badge-dot pulse"></span> Checking...';
  }

  if (confirmBtn) {
    confirmBtn.disabled = false;
    confirmBtn.textContent = 'Enable token →';
  }

  if (tlAuthLoading) tlAuthLoading.style.display = 'none';
  if (tlAuthResult) tlAuthResult.classList.remove('visible');

  if (clearInputs) {
    ['tl-issuer', 'tl-currency', 'tl-limit', 'tl-auth-input'].forEach((id) => {
      const el = document.getElementById(id);
      if (el) el.value = '';
    });

    const confirmCurrency = document.getElementById('tl-confirm-currency');
    const confirmIssuer = document.getElementById('tl-confirm-issuer');
    const destCurrency = document.getElementById('tl-dest-currency');
    if (confirmCurrency) confirmCurrency.textContent = 'USD';
    if (confirmIssuer) confirmIssuer.textContent = 'r...abc';
    if (destCurrency) destCurrency.textContent = 'your currency';
  }

  window.scrollTo(0, 0);
}

function tlCancelFlow() {
  if (tlConfirmInFlight) return;
  tlResetFlow(true);
}

async function tlGoToVerify() {
  const issuer = (document.getElementById('tl-issuer')?.value || '').trim();
  const currency = normalizeCurrency(document.getElementById('tl-currency')?.value || 'USD');
  const limit = (document.getElementById('tl-limit')?.value || '1000000').trim() || '1000000';

  if (!issuer || !currency) {
    alert('field cannot be empty');
    return;
  }

  tlSetStep(2);
  document.getElementById('tl-verify-issuer-display').textContent = issuer;
  document.getElementById('tl-verify-currency-display').textContent = currency;
  document.getElementById('tl-verify-results').style.display = 'none';
  document.getElementById('tl-verify-verdict').style.display = 'none';
  document.getElementById('tl-verify-loading').style.display = 'block';

  try {
    const data = await apiPost('/api/trustline/check-issuer', {
      username: getRequiredUsername(),
      issuer,
      currency,
    });

    window.__tlDraft = { issuer, currency, limit };
    showIssuerResults(data, currency);
  } catch (error) {
    document.getElementById('tl-verify-loading').style.display = 'none';
    alert(error.message || 'Failed to verify issuer.');
    tlSetStep(1);
  }
}

function setTlVerifyCheck(id, passed, desc) {
  const icon = document.getElementById(`tl-v-${id}-icon`);
  const descEl = document.getElementById(`tl-v-${id}-desc`);
  if (!icon || !descEl) return;
  icon.className = `auth-check-icon ${passed ? 'safe' : 'danger'}`;
  icon.textContent = passed ? '✓' : '✕';
  descEl.textContent = desc;
}

function showIssuerResults(data, currency) {
  document.getElementById('tl-verify-loading').style.display = 'none';
  document.getElementById('tl-verify-results').style.display = 'block';

  setTlVerifyCheck('validity', data.valid, data.valid ? 'This issuer account exists on XRPL.' : 'This issuer account was not found on XRPL.');
  setTlVerifyCheck('blacklist', !data.blacklisted, data.blacklisted ? 'This issuer is flagged as high risk.' : 'This issuer is not in the known-risk list.');
  setTlVerifyCheck('age', Number(data.age_months || 0) >= 6, `Estimated account age: ${data.age_months || 0} month(s).`);
  setTlVerifyCheck('currency', !!data.issues_currency, data.issues_currency ? `Issuer appears to issue ${currency}.` : `No clear evidence this issuer issues ${currency}.`);

  const box = document.getElementById('tl-verdict-box');
  const icon = document.getElementById('tl-verdict-icon');
  const title = document.getElementById('tl-verdict-title');
  const desc = document.getElementById('tl-verdict-desc');
  const warn = document.getElementById('tl-confirm-warning');
  const badge = document.getElementById('tl-verify-badge');

  box.className = 'auth-overall';

  if (data.risk === 'low') {
    box.classList.add('safe');
    icon.textContent = '✓';
    title.textContent = 'Issuer looks legitimate';
    desc.textContent = 'Checks passed. You can proceed.';
    warn.textContent = '';
    badge.className = 'badge badge-confirmed';
    badge.innerHTML = '<span class="badge-dot"></span> Safe';
  } else if (data.risk === 'medium') {
    box.classList.add('warn');
    icon.textContent = '⚠';
    title.textContent = 'Proceed with caution';
    desc.textContent = 'Some checks are inconclusive. Verify independently before continuing.';
    warn.textContent = 'You can still proceed, but review issuer details carefully.';
    badge.className = 'badge badge-pending';
    badge.innerHTML = '<span class="badge-dot"></span> Caution';
  } else {
    box.classList.add('danger');
    icon.textContent = '✕';
    title.textContent = 'High risk issuer';
    desc.textContent = 'This issuer failed safety checks. We recommend not proceeding.';
    warn.textContent = 'Proceed only if you explicitly trust this issuer.';
    badge.className = 'badge badge-expired';
    badge.innerHTML = '<span class="badge-dot"></span> High risk';
  }

  document.getElementById('tl-verify-verdict').style.display = 'block';
}

function tlBackOut() {
  tlResetFlow(false);
}

async function tlConfirm() {
  if (tlConfirmInFlight) return;

  const draft = window.__tlDraft || {};
  const issuer = draft.issuer || (document.getElementById('tl-issuer')?.value || '').trim();
  const currency = normalizeCurrency(draft.currency || document.getElementById('tl-currency')?.value || 'USD');
  const limit = String(draft.limit || document.getElementById('tl-limit')?.value || '1000000').trim() || '1000000';

  if (!issuer || !currency) {
    alert('field cannot be empty');
    return;
  }

  const btn = document.getElementById('tl-confirm-btn');
  const original = btn.textContent;
  tlConfirmInFlight = true;
  updateTlCancelVisibility();
  btn.disabled = true;
  btn.textContent = 'Enabling...';

  try {
    await apiPost('/api/trustline/create', {
      username: getRequiredUsername(),
      issuer,
      currency,
      limit,
    });

    setTrustlineDone(currency, issuer);
    await refreshAllData();
  } catch (error) {
    alert(error.message || 'Failed to create trustline.');
  } finally {
    tlConfirmInFlight = false;
    updateTlCancelVisibility();
    btn.disabled = false;
    btn.textContent = original;
  }
}

function submitTrustline() {
  tlGoToVerify();
}

function setTrustlineDone(currency, issuer) {
  trustlineSet = true;

  const cur = document.getElementById('tl-confirm-currency');
  const iss = document.getElementById('tl-confirm-issuer');
  const destCur = document.getElementById('tl-dest-currency');

  if (cur) cur.textContent = currency || 'USD';
  if (iss) iss.textContent = issuer || 'r...';
  if (destCur) destCur.textContent = currency || 'USD';

  tlSetStep(3);
  ['trade-gate', 'send-gate'].forEach((id) => {
    const el = document.getElementById(id);
    if (el) el.style.display = 'none';
  });
}

function setCheck(id, passed, desc) {
  const icon = document.getElementById(`chk-${id}-icon`);
  const descEl = document.getElementById(`chk-${id}-desc`);
  if (!icon || !descEl) return;
  icon.className = `auth-check-icon ${passed ? 'safe' : 'danger'}`;
  icon.textContent = passed ? '✓' : '✕';
  descEl.textContent = desc;
}

function renderOverallVerdict(prefix, risk, currency) {
  const overall = document.getElementById(prefix === 'auth' ? 'auth-overall' : 'tl-auth-overall');
  const icon = document.getElementById(prefix === 'auth' ? 'auth-overall-icon' : 'tl-auth-overall-icon');
  const title = document.getElementById(prefix === 'auth' ? 'auth-overall-title' : 'tl-auth-overall-title');
  const desc = document.getElementById(prefix === 'auth' ? 'auth-overall-desc' : 'tl-auth-overall-desc');

  if (!overall || !icon || !title || !desc) return;

  overall.className = 'auth-overall';
  if (risk === 'low') {
    overall.classList.add('safe');
    icon.textContent = '✓';
    title.textContent = 'Low Risk — Looks Safe';
    desc.textContent = 'Checks passed and destination appears safe.';
    return;
  }

  if (risk === 'medium') {
    overall.classList.add('warn');
    icon.textContent = '⚠';
    title.textContent = 'Medium Risk — Proceed with Caution';
    desc.textContent = 'Some checks are inconclusive. Verify before sending large amounts.';
    return;
  }

  overall.classList.add('danger');
  icon.textContent = '✕';
  title.textContent = currency !== 'XRP' ? `High Risk / Cannot Receive ${currency}` : 'High Risk — Do Not Send';
  desc.textContent = 'Destination failed one or more critical checks.';
}

async function runAddressCheck(inputAddress, currency, issuer, useTrustlinePrefix = false) {
  const data = await apiPost('/api/check-address', {
    address: inputAddress,
    currency,
    issuer,
  });

  if (!useTrustlinePrefix) {
    setCheck('validity', !!data.valid, data.valid ? 'This address exists on XRPL.' : 'This address does not appear to exist on XRPL.');
    setCheck('age', Number(data.age_months || 0) >= 6, `Estimated account age: ${data.age_months || 0} month(s).`);
    setCheck('blacklist', !data.blacklisted, data.blacklisted ? 'Address appears in known risk list.' : 'Address is not in the known risk list.');
    setCheck('activity', Number(data.tx_count || 0) >= 10, `Estimated transaction count: ${data.tx_count || 0}.`);

    const label = document.getElementById('chk-trustline-currency');
    if (label) label.textContent = currency !== 'XRP' ? `· ${currency}` : '';

    const trustlineOk = currency === 'XRP' ? true : !!data.has_trustline;
    setCheck('trustline', trustlineOk, trustlineOk ? 'Recipient can receive this currency.' : `Recipient cannot receive ${currency}.`);

    renderOverallVerdict('auth', data.risk, currency);
    document.getElementById('auth-result').classList.add('visible');
    return data;
  }

  const setTlRow = (id, passed, text) => {
    const icon = document.getElementById(`tl-chk-${id}-icon`);
    const desc = document.getElementById(`tl-chk-${id}-desc`);
    if (!icon || !desc) return;
    icon.className = `auth-check-icon ${passed ? 'safe' : 'danger'}`;
    icon.textContent = passed ? '✓' : '✕';
    desc.textContent = text;
  };

  setTlRow('validity', !!data.valid, data.valid ? 'This address exists on XRPL.' : 'This address does not exist on XRPL.');
  setTlRow('age', Number(data.age_months || 0) >= 6, `Estimated account age: ${data.age_months || 0} month(s).`);
  setTlRow('blacklist', !data.blacklisted, data.blacklisted ? 'Address appears in known risk list.' : 'Address is not in known risk lists.');
  setTlRow('activity', Number(data.tx_count || 0) >= 10, `Estimated transaction count: ${data.tx_count || 0}.`);

  const tlOk = currency === 'XRP' ? true : !!data.has_trustline;
  setTlRow('trustline', tlOk, tlOk ? 'Recipient can receive this currency.' : `Recipient cannot receive ${currency}.`);

  const label = document.getElementById('tl-chk-trustline-currency');
  if (label) label.textContent = currency !== 'XRP' ? `· ${currency}` : '';

  renderOverallVerdict('tl', data.risk, currency);
  document.getElementById('tl-auth-result').classList.add('visible');
  return data;
}

async function runAuthCheck() {
  const input = (document.getElementById('auth-input')?.value || '').trim();
  if (!input) {
    alert('field cannot be empty');
    return;
  }

  const currency = normalizeCurrency(document.getElementById('send-currency')?.value || 'XRP');
  const issuer = resolveIssuerClient(currency, document.getElementById('send-issuer')?.value || '');

  document.getElementById('auth-loading').style.display = 'block';
  document.getElementById('auth-result').classList.remove('visible');

  try {
    await runAddressCheck(input, currency, issuer, false);
  } catch (error) {
    alert(error.message || 'Failed to check address.');
  } finally {
    document.getElementById('auth-loading').style.display = 'none';
  }
}

function showAuthResults(_address) {
  // Intentionally unused: live checks are performed in runAuthCheck().
}

async function runTlAuthCheck() {
  const input = (document.getElementById('tl-auth-input')?.value || '').trim();
  if (!input) {
    alert('field cannot be empty');
    return;
  }

  const currency = normalizeCurrency(document.getElementById('tl-dest-currency')?.textContent || 'XRP');
  const issuer = (window.__tlDraft && window.__tlDraft.issuer) || (document.getElementById('tl-issuer')?.value || '').trim();

  document.getElementById('tl-auth-loading').style.display = 'block';
  document.getElementById('tl-auth-result').classList.remove('visible');

  try {
    await runAddressCheck(input, currency, issuer, true);
  } catch (error) {
    alert(error.message || 'Failed to check destination address.');
  } finally {
    document.getElementById('tl-auth-loading').style.display = 'none';
  }
}

function showPage(id) {
  document.querySelectorAll('.page').forEach((p) => p.classList.remove('active'));
  document.querySelectorAll('.nav-item').forEach((n) => n.classList.remove('active'));
  document.getElementById(`page-${id}`)?.classList.add('active');
  const navEl = document.getElementById(`nav-${id}`);
  if (navEl) navEl.classList.add('active');
}

function logout() {
  localStorage.removeItem('xrpl_username');
  window.location.href = '/';
}

function renderTradeStepProgress(status) {
  const stagePct = {
    submitted: 15,
    open: 45,
    partially_filled: 75,
    filled: 100,
    cancelled: 0,
    failed: 0,
  };
  const pct = stagePct[status] || 0;

  const fill = document.getElementById('track-fill');
  const pctEl = document.getElementById('track-pct');
  if (fill) fill.style.width = `${pct}%`;
  if (pctEl) pctEl.textContent = `${pct}%`;

  const labels = ['Swap submitted', 'Offer open on ledger', 'Partial fill detected', 'Offer fully filled'];
  const activeIndex = status === 'submitted' ? 0 : status === 'open' ? 1 : status === 'partially_filled' ? 2 : 3;

  for (let i = 0; i < 4; i++) {
    const el = document.getElementById(`stage-${i}`);
    if (!el) continue;

    if (i < activeIndex) {
      el.className = 'ledger-stage done-stage';
      el.querySelector('.stage-icon').textContent = '✓';
    } else if (i === activeIndex) {
      el.className = 'ledger-stage active-stage';
      el.querySelector('.stage-icon').textContent = '→';
    } else {
      el.className = 'ledger-stage';
      el.querySelector('.stage-icon').textContent = String(i + 1);
    }

    const span = el.querySelector('span');
    if (span) span.textContent = labels[i];
  }
}

function showTradeScreen(n) {
  document.querySelectorAll('.trade-screen').forEach((s) => {
    s.style.display = 'none';
  });
  const target = document.getElementById(`trade-screen-${n}`);
  if (target) target.style.display = 'block';
  window.scrollTo(0, 0);
}

function captureForm() {
  tradeData.sellCurrency = normalizeCurrency(document.getElementById('sell-currency')?.value);
  tradeData.sellAmount = (document.getElementById('sell-amount')?.value || '').trim();
  tradeData.buyCurrency = normalizeCurrency(document.getElementById('buy-currency')?.value);
  tradeData.buyAmount = (document.getElementById('buy-amount')?.value || '').trim();
  tradeData.sellIssuer = (document.getElementById('sell-issuer')?.value || '').trim();
  tradeData.buyIssuer = (document.getElementById('buy-issuer')?.value || '').trim();
  tradeData.time = new Date().toLocaleTimeString();
}

function setTradeMessage(text, isError = false) {
  setInlineMessage('trade-status-message', text, isError);
}

async function startTradeFlow() {
  captureForm();

  if (!tradeData.sellAmount || !tradeData.buyAmount) {
    alert('field cannot be empty');
    return;
  }

  const startBtn = document.querySelector('#trade-screen-1 .btn.btn-primary.btn-full');
  const originalBtnText = startBtn ? startBtn.textContent : '';
  if (startBtn) {
    startBtn.disabled = true;
    startBtn.textContent = 'Submitting...';
  }

  try {
    const giveIssuer = resolveIssuerClient(tradeData.sellCurrency, tradeData.sellIssuer);
    const wantIssuer = resolveIssuerClient(tradeData.buyCurrency, tradeData.buyIssuer);

    const payload = {
      username: getRequiredUsername(),
      give_currency: tradeData.sellCurrency,
      give_amount: tradeData.sellAmount,
      want_currency: tradeData.buyCurrency,
      want_amount: tradeData.buyAmount,
      give_issuer: giveIssuer || undefined,
      want_issuer: wantIssuer || undefined,
    };

    const response = await apiPost('/api/trade/create', payload);

    tradeData.offerSequence = response.offer_sequence;
    tradeData.txHash = response.tx_hash || '—';
    tradeData.offerId = `#${response.offer_sequence}`;

    appState.activeTrade = {
      offerSequence: response.offer_sequence,
      txHash: response.tx_hash || null,
    };

    document.getElementById('s2-offer-id').textContent = `#${response.offer_sequence}`;
    document.getElementById('s2-sell').textContent = `${tradeData.sellAmount} ${tradeData.sellCurrency}`;
    document.getElementById('s2-buy').textContent = `${tradeData.buyAmount} ${tradeData.buyCurrency}`;
    document.getElementById('s2-time').textContent = tradeData.time;
    document.getElementById('s2-ledger').textContent = 'Offer submitted';

    showTradeScreen(2);
    renderTradeStepProgress('submitted');
    startTradePolling();
    setTradeMessage('Trade offer submitted to XRPL. Polling status...');

    await refreshAllData();
  } catch (error) {
    setTradeMessage(error.message || 'Failed to create trade offer.', true);
    alert(error.message || 'Failed to create trade offer.');
  } finally {
    if (startBtn) {
      startBtn.disabled = false;
      startBtn.textContent = originalBtnText;
    }
  }
}

async function refreshActiveTradeStatus() {
  if (!appState.activeTrade || !appState.activeTrade.offerSequence) return;

  const statusData = await apiGet('/api/trade/status', {
    username: getRequiredUsername(),
    offer_sequence: appState.activeTrade.offerSequence,
  });

  const status = statusData.status;
  const ledgerLabel = document.getElementById('s2-ledger');
  if (ledgerLabel) ledgerLabel.textContent = `Status: ${status}`;

  renderTradeStepProgress(status);

  if (status === 'open' || status === 'submitted') {
    showTradeScreen(2);
    setTradeMessage(`Offer #${appState.activeTrade.offerSequence} is ${status} on ledger.`);
    return;
  }

  if (status === 'partially_filled') {
    populateMatch(statusData);
    showTradeScreen(3);
    setTradeMessage('A partial fill was detected. Review details and continue monitoring.');
    return;
  }

  if (status === 'filled') {
    tradeData.txHash = statusData.tx_hash || tradeData.txHash || '—';
    populateConfirm(statusData);
    showTradeScreen(4);
    stopTradePolling();
    setTradeMessage('Trade filled successfully.');
    await refreshAllData();
    return;
  }

  if (status === 'cancelled') {
    stopTradePolling();
    appState.activeTrade = null;
    setTradeMessage('Offer cancelled.', true);
    await refreshAllData();
    showPage('dashboard');
    return;
  }

  if (status === 'failed') {
    stopTradePolling();
    appState.activeTrade = null;
    setTradeMessage('Offer failed or no longer available.', true);
    await refreshAllData();
    showPage('dashboard');
  }
}

function startTradePolling() {
  stopTradePolling();
  trackInterval = setInterval(() => {
    refreshActiveTradeStatus().catch((error) => {
      setTradeMessage(error.message || 'Trade status polling error.', true);
    });
  }, 4000);
}

function stopTradePolling() {
  if (trackInterval) {
    clearInterval(trackInterval);
    trackInterval = null;
  }
}

function populateMatch(statusData = {}) {
  document.getElementById('match-id').textContent = `#${tradeData.offerSequence || appState.activeTrade?.offerSequence || '—'}`;
  document.getElementById('match-sell').textContent = `${tradeData.sellAmount || '—'} ${tradeData.sellCurrency || ''}`;
  document.getElementById('match-buy').textContent = `${tradeData.buyAmount || '—'} ${tradeData.buyCurrency || ''}`;

  const sell = Number(tradeData.sellAmount || 0);
  const buy = Number(tradeData.buyAmount || 0);
  const rate = sell > 0 ? (buy / sell).toFixed(6) : '—';
  document.getElementById('match-rate').textContent = `${rate} ${tradeData.buyCurrency || ''} per ${tradeData.sellCurrency || ''}`;

  if (statusData.tx_hash) tradeData.txHash = statusData.tx_hash;
}

function populateConfirm(statusData = {}) {
  document.getElementById('tx-hash').textContent = statusData.tx_hash || tradeData.txHash || '—';
  document.getElementById('confirm-sell').textContent = `${tradeData.sellAmount || '—'} ${tradeData.sellCurrency || ''}`;
  document.getElementById('confirm-buy').textContent = `${tradeData.buyAmount || '—'} ${tradeData.buyCurrency || ''}`;
  document.getElementById('confirm-ledger').textContent = statusData.last_ledger || 'Validated ledger';
}

async function checkTradeCompletion() {
  try {
    await refreshActiveTradeStatus();
    const txHash = document.getElementById('tx-hash')?.textContent || '';
    if (!txHash || txHash === '—') {
      alert('Trade is still processing on ledger.');
    }
  } catch (error) {
    alert(error.message || 'Unable to refresh trade status.');
  }
}

function goTradeScreen(n) {
  if (n === 2) {
    startTradeFlow();
    return;
  }

  if (n === 4) {
    checkTradeCompletion();
    return;
  }

  showTradeScreen(n);
}

function resetTrade() {
  stopTradePolling();
  appState.activeTrade = null;
  showTradeScreen(1);
}

async function noMatchFound() {
  await cancelOffer();
}

async function cancelOffer() {
  if (!appState.activeTrade || !appState.activeTrade.offerSequence) {
    resetTrade();
    showPage('dashboard');
    return;
  }

  try {
    await apiPost('/api/trade/cancel', {
      username: getRequiredUsername(),
      offer_sequence: appState.activeTrade.offerSequence,
    });

    stopTradePolling();
    appState.activeTrade = null;
    resetTrade();
    await refreshAllData();
    showPage('dashboard');
    setTradeMessage('Offer cancelled.');
  } catch (error) {
    setTradeMessage(error.message || 'Unable to cancel offer.', true);
    alert(error.message || 'Unable to cancel offer.');
  }
}

async function cancelOfferById(index) {
  const offer = openOffers[index];
  if (!offer) return;

  try {
    await apiPost('/api/trade/cancel', {
      username: getRequiredUsername(),
      offer_sequence: offer.offer_sequence,
    });

    await refreshAllData();
  } catch (error) {
    alert(error.message || 'Unable to cancel selected offer.');
  }
}

async function sendCurrency() {
  const destination = (document.getElementById('send-destination')?.value || '').trim();
  const currency = normalizeCurrency(document.getElementById('send-currency')?.value || 'XRP');
  const amount = (document.getElementById('send-amount')?.value || '').trim();
  const issuerInput = (document.getElementById('send-issuer')?.value || '').trim();
  const issuer = resolveIssuerClient(currency, issuerInput);

  if (!destination || !amount) {
    alert('field cannot be empty');
    return;
  }

  const sendBtn = document.getElementById('send-btn');
  const originalText = sendBtn ? sendBtn.textContent : 'Send →';
  if (sendBtn) {
    sendBtn.disabled = true;
    sendBtn.textContent = 'Sending...';
  }

  try {
    const check = await apiPost('/api/check-address', {
      address: destination,
      currency,
      issuer,
    });

    if (!check.valid) {
      throw new Error('Destination address is not valid on XRPL.');
    }

    if (currency !== 'XRP' && !check.has_trustline) {
      throw new Error(`Recipient cannot receive ${currency} (missing trust line).`);
    }

    const endpoint = currency === 'XRP' ? '/api/xrp/send' : '/api/token/send';
    const payload = {
      username: getRequiredUsername(),
      destination,
      amount,
      currency,
      issuer,
    };

    const data = await apiPost(endpoint, payload);
    setInlineMessage('send-status', `Sent successfully. TX: ${data.tx_hash || 'submitted'}`);
    await refreshAllData();
  } catch (error) {
    setInlineMessage('send-status', error.message || 'Send failed.', true);
    alert(error.message || 'Send failed.');
  } finally {
    if (sendBtn) {
      sendBtn.disabled = false;
      sendBtn.textContent = originalText;
    }
  }
}

function copyHash() {
  const hash = document.getElementById('tx-hash')?.textContent || '';
  navigator.clipboard.writeText(hash).then(() => {
    const el = document.getElementById('tx-hash');
    if (!el) return;
    el.style.borderColor = '#22c55e';
    setTimeout(() => {
      el.style.borderColor = '';
    }, 1200);
  });
}

function toggleEscrow(el) {
  el.classList.toggle('expanded');
}

function walletSwitchPanel(id, btn) {
  document.querySelectorAll('.wpanel').forEach((panel) => panel.classList.remove('active'));
  document.querySelectorAll('.wallet-quick-btn').forEach((b) => b.classList.remove('wq-active'));
  document.getElementById(`wpanel-${id}`)?.classList.add('active');
  btn.classList.add('wq-active');
}

function copyWalletAddress() {
  const addr = appState.address || 'rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh';
  navigator.clipboard.writeText(addr).catch(() => {});
  document.querySelectorAll('.balance-address, .hash-box').forEach((el) => {
    const originalColor = el.style.color;
    el.style.color = '#166534';
    setTimeout(() => {
      el.style.color = originalColor;
    }, 1200);
  });
}

function previewCard() {
  const name = (document.getElementById('card-name')?.value || 'FULL NAME').toUpperCase();
  const num = document.getElementById('card-number')?.value || '•••• •••• •••• ••••';
  const exp = document.getElementById('card-exp')?.value || 'MM/YY';

  const prevName = document.getElementById('prev-name');
  const prevNum = document.getElementById('prev-number');
  const prevExp = document.getElementById('prev-exp');
  if (prevName) prevName.textContent = name;
  if (prevNum) prevNum.textContent = num;
  if (prevExp) prevExp.textContent = exp;
}

function formatCardNum(input) {
  let value = input.value.replace(/\D/g, '').substring(0, 16);
  input.value = value.replace(/(.{4})/g, '$1 ').trim();
}

function formatCardExp(input) {
  let value = input.value.replace(/\D/g, '').substring(0, 4);
  if (value.length >= 2) value = `${value.substring(0, 2)}/${value.substring(2)}`;
  input.value = value;
}

function goTopupScreen(n) {
  document.querySelectorAll('.topup-screen').forEach((screen) => screen.classList.remove('active'));
  document.getElementById(`topup-s${n}`)?.classList.add('active');

  ['wstep1', 'wstep2', 'wstep3'].forEach((id, index) => {
    const el = document.getElementById(id);
    if (!el) return;
    el.className = `step ${index + 1 < n ? 'done' : index + 1 === n ? 'active' : ''}`;
    const num = el.querySelector('.step-num');
    if (num) num.textContent = index + 1 < n ? '✓' : String(index + 1);
  });

  if (n === 3) populateTopupConfirm();
}

function selectTopupAmount(btn, val) {
  document.querySelectorAll('.amount-chip').forEach((chip) => chip.classList.remove('selected'));
  btn.classList.add('selected');
  document.getElementById('topup-amount').value = val;
  updateTopupConversion();
}

function updateTopupConversion() {
  const aud = Number(document.getElementById('topup-amount')?.value || 0);
  const notice = document.getElementById('topup-conversion-notice');
  if (!notice) return;

  if (!Number.isFinite(aud) || aud <= 0) {
    notice.textContent = 'Enter an amount above to see XRP estimate.';
    return;
  }

  const xrp = ((aud - 1.5) / 2.0).toFixed(2);
  notice.textContent = `$${aud.toFixed(2)} AUD → ~${xrp} XRP (after $1.50 fee, rate: 1 XRP = $2.00 AUD)`;
}

function populateTopupConfirm() {
  const aud = Number(document.getElementById('topup-amount')?.value || 0);
  const xrp = ((aud - 1.5) / 2.0).toFixed(2);
  const num = document.getElementById('card-number')?.value || '';
  const last4 = num ? num.replace(/\s/g, '').slice(-4) : '••••';

  document.getElementById('topup-confirm-aud').textContent = `$${aud.toFixed(2)} AUD`;
  document.getElementById('topup-confirm-xrp').textContent = `${xrp} XRP`;
  document.getElementById('topup-confirm-card').textContent = `•••• ${last4}`;
}

function processTopupPayment() {
  document.getElementById('topup-pay-btn').disabled = true;
  document.getElementById('topup-pay-btn').textContent = 'Processing...';
  document.getElementById('topup-proc-label').style.display = 'flex';
  document.getElementById('topup-proc-bar').style.display = 'block';

  let pct = 0;
  topupPayInterval = setInterval(() => {
    pct += Math.floor(Math.random() * 20) + 8;
    if (pct >= 100) {
      pct = 100;
      clearInterval(topupPayInterval);
      setTimeout(topupPaySuccess, 500);
    }
    document.getElementById('topup-proc-fill').style.width = `${pct}%`;
    document.getElementById('topup-proc-pct').textContent = `${pct}%`;
  }, 400);
}

function topupPaySuccess() {
  const aud = Number(document.getElementById('topup-amount')?.value || 0);
  const xrp = ((aud - 1.5) / 2.0).toFixed(2);
  const txId = `TXN-${Math.random().toString(36).substr(2, 10).toUpperCase()}`;
  const newBal = (appState.xrpBalance + Number(xrp)).toFixed(2);

  document.getElementById('topup-success-msg').textContent = `${xrp} XRP added to your wallet.`;
  document.getElementById('topup-success-tx').textContent = txId;
  document.getElementById('topup-success-bal').textContent = `${newBal} XRP`;
  goTopupScreen(4);
}

function resetTopupFlow() {
  document.getElementById('topup-amount').value = '';
  document.getElementById('topup-conversion-notice').textContent = 'Enter an amount above to see XRP estimate.';
  document.querySelectorAll('.amount-chip').forEach((chip) => chip.classList.remove('selected'));
  document.getElementById('topup-pay-btn').disabled = false;
  document.getElementById('topup-pay-btn').textContent = 'Pay now';
  document.getElementById('topup-proc-label').style.display = 'none';
  document.getElementById('topup-proc-bar').style.display = 'none';
  document.getElementById('topup-proc-fill').style.width = '0%';
  goTopupScreen(2);
}

function calcWithdrawEst() {
  const xrp = Number(document.getElementById('withdraw-xrp')?.value || 0);
  const el = document.getElementById('withdraw-est');
  if (!el) return;

  if (!Number.isFinite(xrp) || xrp <= 0) {
    el.textContent = "Enter an amount above to see how much AUD you'll get.";
    return;
  }

  const aud = (xrp * 2.0 - 2.5).toFixed(2);
  el.textContent = `${xrp} XRP → ~$${aud} AUD (after $2.50 withdrawal fee, rate: 1 XRP = $2.00 AUD)`;
}

function filterWalletTx(type, btn) {
  document.querySelectorAll('.wtab-btn').forEach((b) => {
    b.style.background = 'var(--surface)';
    b.style.color = 'var(--muted)';
  });
  btn.style.background = 'var(--text)';
  btn.style.color = '#fff';

  document.querySelectorAll('#wallet-tx-list .tx-row').forEach((row) => {
    row.style.display = type === 'all' || row.dataset.type === type || row.dataset.type === 'all' ? '' : 'none';
  });
}

function filterEscrows(status, btn) {
  document.querySelectorAll('.filter-btn').forEach((b) => b.classList.remove('active'));
  btn.classList.add('active');

  document.querySelectorAll('.escrow-row').forEach((row) => {
    row.style.display = status === 'all' || row.dataset.status === status ? '' : 'none';
  });
}

initApp();
