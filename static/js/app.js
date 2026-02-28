
    // ── Trust line state ──
    let trustlineSet = false;

    function requireTrustline(page) {
      // XRP never needs a trust line — always allow
      // Gate only shows if user picks a non-XRP currency
      showPage(page);
      if (page === 'trade') resetTrade();
      checkCurrencyGate(page);
    }

    function checkCurrencyGate(page) {
      const gate = document.getElementById(page + '-gate');
      if (!gate) return;

      if (page === 'trade') {
        const sell = document.getElementById('sell-currency');
        const buy  = document.getElementById('buy-currency');
        const needsGate = !trustlineSet && (
          (sell && sell.value !== 'XRP') || (buy && buy.value !== 'XRP')
        );
        gate.style.display = needsGate ? 'flex' : 'none';
      }

      if (page === 'send') {
        const curr = document.getElementById('send-currency');
        const needsGate = !trustlineSet && curr && curr.value !== 'XRP';
        gate.style.display = needsGate ? 'flex' : 'none';
      }
    }

    // ── Trust line progress helpers ──
    function tlSetStep(n) {
      [1,2,3].forEach(i => {
        const el = document.getElementById('tl-step-' + i);
        if (el) el.style.display = i === n ? 'block' : 'none';
        const prog = document.getElementById('tl-prog-' + i);
        if (prog) {
          prog.className = 'step' + (i < n ? ' done' : i === n ? ' active' : '');
          prog.querySelector('.step-num').textContent = i < n ? '✓' : i;
        }
      });
    }

    function tlGoToVerify() {
      const issuer   = document.getElementById('tl-issuer').value.trim();
      const currency = (document.getElementById('tl-currency').value.trim() || 'USD').toUpperCase();

      // Show step 2 and populate display fields
      tlSetStep(2);
      document.getElementById('tl-verify-issuer-display').textContent   = issuer || 'r...';
      document.getElementById('tl-verify-currency-display').textContent = currency;
      document.getElementById('tl-verify-results').style.display  = 'none';
      document.getElementById('tl-verify-verdict').style.display  = 'none';
      document.getElementById('tl-verify-loading').style.display  = 'block';
      document.getElementById('tl-verify-badge').className = 'badge badge-pending';
      document.getElementById('tl-verify-badge').innerHTML = '<span class="badge-dot pulse"></span> Checking...';

      // ─────────────────────────────────────────────
      // BACKEND HOOK POINT
      // Replace setTimeout with:
      //   const res = await fetch('/api/check-issuer', {
      //     method: 'POST',
      //     headers: { 'Content-Type': 'application/json' },
      //     body: JSON.stringify({ issuer, currency })
      //   });
      //   const data = await res.json();
      //   // data: { valid, blacklisted, age_months, issues_currency, risk }
      //   showIssuerResults(data, currency);
      // ─────────────────────────────────────────────

      setTimeout(() => {
        const sim = {
          valid:           issuer.startsWith('r') && issuer.length >= 25,
          blacklisted:     Math.random() < 0.15,
          age_months:      Math.floor(Math.random() * 60 + 6),
          issues_currency: Math.random() > 0.2,
        };
        sim.risk = (!sim.valid || sim.blacklisted) ? 'high'
                 : !sim.issues_currency ? 'medium' : 'low';
        showIssuerResults(sim, currency);
      }, 1800);
    }

    function showIssuerResults(data, currency) {
      // ─────────────────────────────────────────────
      // BACKEND HOOK POINT
      // Called with API response. Shape: { valid, blacklisted, age_months, issues_currency, risk }
      // ─────────────────────────────────────────────
      document.getElementById('tl-verify-loading').style.display = 'none';
      document.getElementById('tl-verify-results').style.display = 'block';

      setTlVerifyCheck('validity', data.valid,
        data.valid ? 'This issuer account exists on the XRPL ledger.'
                   : 'This account does not exist on the XRPL ledger. Do not trust this issuer.');

      setTlVerifyCheck('blacklist', !data.blacklisted,
        !data.blacklisted ? 'Not found in any known scam or fraud database — low risk.'
                          : 'This issuer has been flagged as fraudulent. Do not set this trust line.');

      setTlVerifyCheck('age', data.age_months >= 6,
        data.age_months >= 6
          ? 'Issuer account is ' + data.age_months + ' months old — established presence.'
          : 'Account is only ' + data.age_months + ' month(s) old — very new issuers carry higher risk.');

      setTlVerifyCheck('currency', data.issues_currency,
        data.issues_currency
          ? 'This issuer does issue ' + currency + ' — trust line is relevant.'
          : 'No evidence this issuer issues ' + currency + '. Setting this trust line may do nothing.');

      // Verdict box
      const box   = document.getElementById('tl-verdict-box');
      const icon  = document.getElementById('tl-verdict-icon');
      const title = document.getElementById('tl-verdict-title');
      const desc  = document.getElementById('tl-verdict-desc');
      const warn  = document.getElementById('tl-confirm-warning');
      const btn   = document.getElementById('tl-confirm-btn');
      const badge = document.getElementById('tl-verify-badge');

      box.className = 'auth-overall';
      if (data.risk === 'low') {
        box.classList.add('safe');
        icon.textContent  = '✓';
        title.textContent = 'Issuer looks legitimate';
        desc.textContent  = 'All checks passed. Safe to proceed with this trust line.';
        badge.className   = 'badge badge-confirmed';
        badge.innerHTML   = '<span class="badge-dot"></span> Safe';
        warn.textContent  = '';
        btn.style.opacity = '1';
        btn.disabled      = false;
      } else if (data.risk === 'medium') {
        box.classList.add('warn');
        icon.textContent  = '⚠';
        title.textContent = 'Proceed with caution';
        desc.textContent  = 'Some checks raised concerns. You can still proceed, but verify this issuer through other channels first.';
        badge.className   = 'badge badge-pending';
        badge.innerHTML   = '<span class="badge-dot"></span> Caution';
        warn.textContent  = 'You can still confirm, but we recommend verifying this issuer independently first.';
        btn.style.opacity = '1';
        btn.disabled      = false;
      } else {
        box.classList.add('danger');
        icon.textContent  = '✕';
        title.textContent = 'High risk — we recommend backing out';
        desc.textContent  = 'This issuer failed multiple checks. Setting this trust line could expose you to fraud.';
        badge.className   = 'badge badge-expired';
        badge.innerHTML   = '<span class="badge-dot"></span> High risk';
        warn.textContent  = 'We strongly advise against this. If you still want to proceed, you can — but do so at your own risk.';
        btn.style.opacity = '0.5';
        btn.disabled      = false; // still let them if they want
      }

      document.getElementById('tl-verify-verdict').style.display = 'block';
    }

    function setTlVerifyCheck(id, passed, desc) {
      const icon   = document.getElementById('tl-v-' + id + '-icon');
      const descEl = document.getElementById('tl-v-' + id + '-desc');
      if (!icon || !descEl) return;
      icon.className   = 'auth-check-icon ' + (passed ? 'safe' : 'danger');
      icon.textContent = passed ? '✓' : '✕';
      descEl.textContent = desc;
    }

    function tlBackOut() {
      tlSetStep(1);
      // Reset verify state
      document.getElementById('tl-verify-results').style.display = 'none';
      document.getElementById('tl-verify-verdict').style.display = 'none';
      document.getElementById('tl-verify-loading').style.display = 'block';
    }

    function tlConfirm() {
      const issuer   = document.getElementById('tl-issuer').value.trim();
      const currency = (document.getElementById('tl-currency').value.trim() || 'USD').toUpperCase();

      // ─────────────────────────────────────────────
      // BACKEND HOOK POINT
      // Replace with real XRPL trust line submission:
      //   const res = await fetch('/api/set-trustline', {
      //     method: 'POST',
      //     headers: { 'Content-Type': 'application/json' },
      //     body: JSON.stringify({ issuer, currency, limit })
      //   });
      //   const data = await res.json();
      //   if (data.success) setTrustlineDone(currency, issuer);
      // ─────────────────────────────────────────────

      setTrustlineDone(currency, issuer || 'r...abc');
    }

    function submitTrustline() { tlGoToVerify(); }

    function setTrustlineDone(currency, issuer) {
      trustlineSet = true;

      // Update step 3 display
      const el_c = document.getElementById('tl-confirm-currency');
      const el_i = document.getElementById('tl-confirm-issuer');
      const el_d = document.getElementById('tl-dest-currency');
      if (el_c) el_c.textContent = currency || 'USD';
      if (el_i) el_i.textContent = issuer   || 'r...abc';
      if (el_d) el_d.textContent = currency || 'USD';

      tlSetStep(3);

      // Hide gates on gated pages
      ['trade-gate','send-gate'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.style.display = 'none';
      });
    }

        // ── Authenticator ──
    function runAuthCheck() {
      const input = document.getElementById('auth-input').value.trim();
      if (!input) return;

      document.getElementById('auth-loading').style.display = 'block';
      document.getElementById('auth-result').classList.remove('visible');

      setTimeout(() => {
        document.getElementById('auth-loading').style.display = 'none';
        showAuthResults(input);
      }, 1800);
    }

    function showAuthResults(address) {
      // ─────────────────────────────────────────────
      // BACKEND HOOK POINT
      // Expected shape: { valid, age_months, blacklisted, tx_count, has_trustline, currency, risk }
      //
      // Python example for trust line check:
      //   from xrpl.clients import JsonRpcClient
      //   from xrpl.models.requests import AccountLines
      //   client = JsonRpcClient("https://xrplcluster.com")
      //   resp = client.request(AccountLines(account=address))
      //   lines = resp.result.get("lines", [])
      //   has_trustline = any(l["currency"] == currency for l in lines)
      // ─────────────────────────────────────────────

      const isValid   = address.startsWith('r') && address.length >= 25;
      const isOld     = Math.random() > 0.3;
      const isClean   = Math.random() > 0.25;
      const isActive  = Math.random() > 0.2;
      const hasTl     = Math.random() > 0.35; // BACKEND: real trust line check

      // Detect what currency user is trying to send (from send page if active)
      const sendCurr  = document.getElementById('send-currency');
      const currency  = (sendCurr && sendCurr.value) ? sendCurr.value : 'XRP';

      setCheck('validity', isValid,
        isValid
          ? 'This address exists on the XRPL ledger, indicating positive safety levels.'
          : 'This address does not appear to exist on the XRPL ledger. Do not send funds to it.');

      const months = isOld ? Math.floor(Math.random()*36+6) : Math.floor(Math.random()*3+1);
      setCheck('age', isOld,
        isOld
          ? 'This account was created ' + months + ' months ago, classifying it as low risk.'
          : 'This account was created only ' + months + ' month(s) ago — new accounts can indicate scam activity.');

      setCheck('blacklist', isClean,
        isClean
          ? 'This account has not been flagged within blacklist databases — low risk.'
          : 'This account has been flagged in known scam/fraud databases — high risk. Do not send funds.');

      const txCount = isActive ? Math.floor(Math.random()*500+50) : Math.floor(Math.random()*5);
      setCheck('activity', isActive,
        isActive
          ? 'This account has ' + txCount + ' transactions on record, showing consistent usage.'
          : 'This account has very few transactions (' + txCount + '). Limited history increases risk.');

      // Trust line check
      const tlLabel = document.getElementById('chk-trustline-currency');
      if (tlLabel) tlLabel.textContent = currency !== 'XRP' ? '· ' + currency : '';

      if (currency === 'XRP') {
        setCheck('trustline', true,
          'XRP is native to the XRPL — no trust line required on either side.');
      } else {
        setCheck('trustline', hasTl,
          hasTl
            ? 'Recipient has an active ' + currency + ' trust line — they can receive this currency.'
            : 'Recipient does not have a ' + currency + ' trust line. They cannot receive this token — the transaction will fail.');
      }

      const tlOk = currency === 'XRP' || hasTl;
      const safeCount = [isValid, isOld, isClean, isActive, tlOk].filter(Boolean).length;

      const overall      = document.getElementById('auth-overall');
      const overallIcon  = document.getElementById('auth-overall-icon');
      const overallTitle = document.getElementById('auth-overall-title');
      const overallDesc  = document.getElementById('auth-overall-desc');

      overall.className = 'auth-overall';
      if (!tlOk) {
        overall.classList.add('warn');
        overallIcon.textContent  = '⚠';
        overallTitle.textContent = 'Cannot Receive ' + currency;
        overallDesc.textContent  = 'Recipient has no ' + currency + ' trust line. Ask them to set one up before you send.';
      } else if (safeCount >= 5) {
        overall.classList.add('safe');
        overallIcon.textContent  = '✓';
        overallTitle.textContent = 'Low Risk — Looks Safe';
        overallDesc.textContent  = 'All checks passed. This address appears safe to transact with.';
      } else if (safeCount >= 3) {
        overall.classList.add('warn');
        overallIcon.textContent  = '⚠';
        overallTitle.textContent = 'Medium Risk — Proceed with Caution';
        overallDesc.textContent  = 'Some checks raised concerns. Verify this address through another channel before sending large amounts.';
      } else {
        overall.classList.add('danger');
        overallIcon.textContent  = '✕';
        overallTitle.textContent = 'High Risk — Do Not Send';
        overallDesc.textContent  = 'Multiple checks failed. This address shows strong indicators of fraudulent activity.';
      }

      document.getElementById('auth-result').classList.add('visible');
    }

    function setCheck(id, passed, desc) {
      const icon = document.getElementById('chk-' + id + '-icon');
      const descEl = document.getElementById('chk-' + id + '-desc');
      icon.className = 'auth-check-icon ' + (passed ? 'safe' : 'danger');
      icon.textContent = passed ? '✓' : '✕';
      descEl.textContent = desc;
    }

    let tradeData = {};
    let trackInterval = null;
    let openOffers = [];

    function showPage(id) {
      document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
      document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
      document.getElementById('page-' + id).classList.add('active');
      const navEl = document.getElementById('nav-' + id);
      if (navEl) navEl.classList.add('active');
    }

    function goTradeScreen(n) {
      document.querySelectorAll('.trade-screen').forEach(s => s.style.display = 'none');
      document.getElementById('trade-screen-' + n).style.display = 'block';
      window.scrollTo(0, 0);
      if (n === 2) startPending();
      if (n === 3) populateMatch();
      if (n === 4) populateConfirm();
    }

    function resetTrade() {
      if (trackInterval) clearInterval(trackInterval);
      goTradeScreen(1);
    }

    function captureForm() {
      tradeData.sellCurrency = document.getElementById('sell-currency').value;
      tradeData.sellAmount   = document.getElementById('sell-amount').value || '100';
      tradeData.buyCurrency  = document.getElementById('buy-currency').value;
      tradeData.buyAmount    = document.getElementById('buy-amount').value || '250';
      tradeData.offerId      = 'OFF-' + Math.random().toString(36).substr(2,8).toUpperCase();
      tradeData.txHash       = Array.from({length:64}, () => '0123456789ABCDEF'[Math.floor(Math.random()*16)]).join('');
      tradeData.ledgerIdx    = (87432100 + Math.floor(Math.random()*9999)).toLocaleString();
      tradeData.time         = new Date().toLocaleTimeString();
    }

    function startPending() {
      captureForm();
      document.getElementById('s2-offer-id').textContent = tradeData.offerId;
      document.getElementById('s2-sell').textContent     = tradeData.sellAmount + ' ' + tradeData.sellCurrency;
      document.getElementById('s2-buy').textContent      = tradeData.buyAmount  + ' ' + tradeData.buyCurrency;
      document.getElementById('s2-time').textContent     = tradeData.time;
      document.getElementById('s2-ledger').textContent   = 'Searching...';
      document.getElementById('track-fill').style.width  = '0%';
      document.getElementById('track-pct').textContent   = '0%';

      for (let i = 0; i < 4; i++) {
        const el = document.getElementById('stage-' + i);
        el.className = 'ledger-stage' + (i === 0 ? ' active-stage' : '');
        el.querySelector('.stage-icon').textContent = i === 0 ? '→' : i + 1;
      }

      if (trackInterval) clearInterval(trackInterval);
      const stages = [
        { pct: 25, ledger: 'Ledger #87432' + Math.floor(Math.random()*99) },
        { pct: 55, ledger: 'Scanning 2,400 offers...' },
        { pct: 80, ledger: 'Counterparty found' },
        { pct: 100, ledger: 'Match confirmed' },
      ];

      let step = 0;
      trackInterval = setInterval(() => {
        if (step >= stages.length) {
          clearInterval(trackInterval);
          setTimeout(() => goTradeScreen(3), 800);
          return;
        }
        const s = stages[step];
        document.getElementById('track-fill').style.width = s.pct + '%';
        document.getElementById('track-pct').textContent  = s.pct + '%';
        document.getElementById('s2-ledger').textContent  = s.ledger;
        for (let i = 0; i <= step; i++) {
          const el = document.getElementById('stage-' + i);
          el.className = 'ledger-stage ' + (i < step ? 'done-stage' : 'active-stage');
          el.querySelector('.stage-icon').textContent = i < step ? '✓' : '→';
        }
        step++;
      }, 1800);
    }

    function noMatchFound() {
      if (trackInterval) clearInterval(trackInterval);
      openOffers.push({
        id: tradeData.offerId,
        sell: tradeData.sellAmount + ' ' + tradeData.sellCurrency,
        buy:  tradeData.buyAmount  + ' ' + tradeData.buyCurrency,
        time: tradeData.time,
      });
      renderOpenOffers();
      showPage('dashboard');
    }

    function cancelOffer() {
      if (trackInterval) clearInterval(trackInterval);
      resetTrade();
      showPage('dashboard');
    }

    function renderOpenOffers() {
      const container = document.getElementById('open-offers-list');
      if (openOffers.length === 0) {
        container.innerHTML = '<div class="empty-state"><div class="empty-icon">⇄</div>No open offers yet.<br/>Create a trade to get started.</div>';
        return;
      }
      container.innerHTML = openOffers.map((o, i) => `
        <div class="offer-card">
          <div class="offer-info">
            <div class="offer-id">${o.id}</div>
            <div class="offer-pair">${o.sell} → ${o.buy}</div>
            <div class="offer-time">Submitted at ${o.time}</div>
          </div>
          <div class="offer-actions">
            <span class="badge badge-open"><span class="badge-dot"></span> Open</span>
            <button class="btn-sm" onclick="cancelOfferById(${i})">Cancel</button>
          </div>
        </div>
      `).join('');
    }

    function cancelOfferById(i) {
      openOffers.splice(i, 1);
      renderOpenOffers();
    }

    function populateMatch() {
      document.getElementById('match-id').textContent   = tradeData.offerId;
      document.getElementById('match-sell').textContent = tradeData.sellAmount + ' ' + tradeData.sellCurrency;
      document.getElementById('match-buy').textContent  = tradeData.buyAmount  + ' ' + tradeData.buyCurrency;
      const rate = (parseFloat(tradeData.buyAmount) / parseFloat(tradeData.sellAmount)).toFixed(4);
      document.getElementById('match-rate').textContent = rate + ' ' + tradeData.buyCurrency + ' per ' + tradeData.sellCurrency;
    }

    function populateConfirm() {
      document.getElementById('tx-hash').textContent       = tradeData.txHash;
      document.getElementById('confirm-sell').textContent  = tradeData.sellAmount + ' ' + tradeData.sellCurrency;
      document.getElementById('confirm-buy').textContent   = tradeData.buyAmount  + ' ' + tradeData.buyCurrency;
      document.getElementById('confirm-ledger').textContent = tradeData.ledgerIdx;
    }

    function copyHash() {
      navigator.clipboard.writeText(tradeData.txHash).then(() => {
        const el = document.getElementById('tx-hash');
        el.style.borderColor = '#22c55e';
        setTimeout(() => el.style.borderColor = '', 1500);
      });
    }

    function toggleEscrow(el) {
      el.classList.toggle('expanded');
    }

    function filterEscrows(status, btn) {
      document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      document.querySelectorAll('.escrow-row').forEach(row => {
        if (status === 'all' || row.dataset.status === status) {
          row.style.display = '';
        } else {
          row.style.display = 'none';
        }
      });
    }
  
