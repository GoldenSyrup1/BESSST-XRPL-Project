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

## Run locally (Flask app)

```bash
python3 -m pip install -r requirements.txt
python3 scripts/create_enabled_tokens_db.py
python3 app.py
```

Open:

- `http://127.0.0.1:5050/`
- `http://127.0.0.1:5050/dashboard`
