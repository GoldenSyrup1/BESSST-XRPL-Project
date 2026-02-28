# Trustline XRP Frontend

This project now uses a clean split between templates and static assets.

## Structure

- `templates/index.html`: Landing + Auth + About experience
- `templates/app.html`: Dashboard app (trade/send/trustline/escrows/records/auth)
- `static/css/landing-auth-about.css`: Styles for marketing/auth/about
- `static/css/app.css`: Styles for dashboard app
- `static/js/landing-auth-about.js`: Landing/auth/about logic
- `static/js/app.js`: Dashboard app logic
- `archive/`: Original monolithic source files kept for reference

## Run locally

```bash
python3 -m http.server 4173
```

Open:

- `http://127.0.0.1:4173/templates/index.html`
- `http://127.0.0.1:4173/templates/app.html`
