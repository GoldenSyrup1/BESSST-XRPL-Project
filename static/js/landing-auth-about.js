  // ── Page navigation ──────────────────────────
  function showPage(name, authMode) {
    const landing = document.getElementById('page-landing');
    const auth    = document.getElementById('page-auth');
    const about   = document.getElementById('page-about');

    // Hide all
    [landing, auth, about].forEach(p => p.classList.add('hidden'));

    if (name === 'landing') {
      landing.classList.remove('hidden');
      landing.scrollTop = 0;
    } else if (name === 'about') {
      about.classList.remove('hidden');
      about.scrollTop = 0;
    } else {
      auth.classList.remove('hidden');
      if (authMode === 'register') {
        setAuthMode('register');
      } else {
        setAuthMode('login');
      }
    }
  }

  // ── Auth mode toggle ──────────────────────────
  let isLogin = true;

  function setAuthMode(mode) {
    const container = document.getElementById('authContainer');
    const text = document.getElementById('sidePanelText');
    const btn  = document.getElementById('switchBtn');

    if (mode === 'login') {
      isLogin = true;
      container.classList.remove('register-mode');
      container.classList.add('login-mode');
      text.textContent = "Don't have an account?";
      btn.textContent  = 'Register';
    } else {
      isLogin = false;
      container.classList.remove('login-mode');
      container.classList.add('register-mode');
      text.textContent = 'Already have an account?';
      btn.textContent  = 'Log in';
    }
  }

  function toggleAuthMode() {
    setAuthMode(isLogin ? 'register' : 'login');
  }

  // ── Password toggle ───────────────────────────
  function togglePwd(id, el) {
    const input = document.getElementById(id);
    const isHidden = input.type === 'password';
    input.type = isHidden ? 'text' : 'password';
    el.style.color = isHidden ? '#0a0a0a' : '#888';
  }

  // ── Typewriter effect ─────────────────────────
  const phrases = ['Effortless\nTransactions', 'Instant\nPayments', 'Secure\nTransfers'];
  let pi = 0, ci = 0, deleting = false;
  const titleEl = document.querySelector('.hero-title');

  function type() {
    const current = phrases[pi];
    if (!deleting) {
      ci++;
      titleEl.innerHTML = current.slice(0, ci).replace('\n', '<br>') + '<span class="cursor"></span>';
      if (ci === current.length) { setTimeout(() => { deleting = true; type(); }, 2200); return; }
    } else {
      ci--;
      titleEl.innerHTML = (current.slice(0, ci) || '').replace('\n', '<br>') + '<span class="cursor"></span>';
      if (ci === 0) { deleting = false; pi = (pi + 1) % phrases.length; }
    }
    setTimeout(type, deleting ? 40 : 65);
  }
  type();

  // Route successful auth actions into the app page.
  document.querySelectorAll('.submit-btn').forEach((btn) => {
    btn.addEventListener('click', (event) => {
      event.preventDefault();
      window.location.href = './app.html';
    });
  });
