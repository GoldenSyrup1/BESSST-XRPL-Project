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

  async function postJson(url, payload) {
    const response = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });

    let data = {};
    try {
      data = await response.json();
    } catch (_) {
      data = { error: 'Invalid server response' };
    }

    if (!response.ok || data.success === false) {
      throw new Error(data.error || 'Request failed');
    }
    return data.data || data;
  }

  function goToDashboard(username) {
    if (username) {
      localStorage.setItem('xrpl_username', username);
    }
    window.location.href = '/dashboard';
  }

  async function handleLogin(event) {
    event.preventDefault();

    const username = document.getElementById('loginUsername')?.value.trim().toLowerCase();
    const password = document.getElementById('loginPwd')?.value;

    if (!username || !password) {
      alert('field cannot be empty');
      return;
    }

    const loginBtn = document.getElementById('loginSubmit');
    const originalText = loginBtn.textContent;
    loginBtn.disabled = true;
    loginBtn.textContent = 'Logging in...';

    try {
      await postJson('/api/auth/login', { username, password });
      goToDashboard(username);
    } catch (err) {
      alert(err.message || 'Login failed.');
    } finally {
      loginBtn.disabled = false;
      loginBtn.textContent = originalText;
    }
  }

  async function handleRegister(event) {
    event.preventDefault();

    const username = document.getElementById('regUsername')?.value.trim().toLowerCase();
    const phone = document.getElementById('regPhone')?.value.trim();
    const password = document.getElementById('regPwd')?.value;
    const confirm = document.getElementById('regPwd2')?.value;

    if (!username || !password) {
      alert('field cannot be empty');
      return;
    }
    if (password !== confirm) {
      alert('Password and confirm password do not match.');
      return;
    }

    const registerBtn = document.getElementById('registerSubmit');
    const originalText = registerBtn.textContent;
    registerBtn.disabled = true;
    registerBtn.textContent = 'Creating account...';

    try {
      await postJson('/api/auth/register', { username, password, phone });
      goToDashboard(username);
    } catch (err) {
      alert(err.message || 'Registration failed.');
    } finally {
      registerBtn.disabled = false;
      registerBtn.textContent = originalText;
    }
  }

  document.getElementById('loginSubmit')?.addEventListener('click', handleLogin);
  document.getElementById('registerSubmit')?.addEventListener('click', handleRegister);
