import sqlite3
import hashlib
import difflib
import random 
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, session, flash
from xrpl.clients import JsonRpcClient
from xrpl.wallet import Wallet, generate_faucet_wallet
from xrpl.models.transactions import Payment
from xrpl.models.amounts import IssuedCurrencyAmount
from xrpl.models.requests import AccountLines, AccountInfo
from xrpl.transaction import submit_and_wait
from xrpl.utils import xrp_to_drops

app = Flask(__name__)
app.secret_key = 'xrpl_secret_safety_demo'

# XRPL Testnet
JSON_RPC_URL = "https://s.altnet.rippletest.net:51234/"
client = JsonRpcClient(JSON_RPC_URL)

def get_user_by_phone(phone):
    """Finds a user's address and name by their phone number."""
    conn = get_db()
    user = conn.execute("SELECT wallet_address, username FROM users WHERE phone = ?", (phone,)).fetchone()
    conn.close()
    return user

def init_db():
    conn = sqlite3.connect('xrpl_app.db')
    c = conn.cursor()
    
    # 1. Create Tables (Standard Setup)
    # Note: If table exists, this is skipped, so we handle columns below
    conn.execute('''CREATE TABLE IF NOT EXISTS users 
                 (id INTEGER PRIMARY KEY, username TEXT, phone TEXT UNIQUE, password TEXT, 
                  wallet_seed TEXT, wallet_address TEXT, preferred_token TEXT)''')
                  
    c.execute('''CREATE TABLE IF NOT EXISTS illegal_tokens 
                 (id INTEGER PRIMARY KEY, currency TEXT, issuer TEXT)''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS scammer_list 
                 (id INTEGER PRIMARY KEY, address TEXT, reason TEXT)''')

    c.execute('''CREATE TABLE IF NOT EXISTS notifications 
                 (id INTEGER PRIMARY KEY, user_address TEXT, message TEXT, 
                  related_name TEXT, timestamp TEXT)''')

    # --- MIGRATION LOGIC: Fix missing 'phone' column ---
    c.execute("PRAGMA table_info(users)")
    columns = [info[1] for info in c.fetchall()]
    
    if 'phone' not in columns:
        print("⚠️ Migrating Database: Adding missing 'phone' column...")
        try:
            # 1. Add the column
            c.execute("ALTER TABLE users ADD COLUMN phone TEXT")
            
            # 2. Generate random phones for existing users
            c.execute("SELECT id FROM users")
            existing_users = c.fetchall()
            
            for row in existing_users:
                user_id = row[0]
                # Generate a random mock phone number
                rand_phone = f"555-{random.randint(100, 999)}-{random.randint(1000, 9999)}"
                c.execute("UPDATE users SET phone = ? WHERE id = ?", (rand_phone, user_id))
                print(f" -> Assigned {rand_phone} to User ID {user_id}")
                
            conn.commit()
            print("✅ Migration Complete.")
        except Exception as e:
            print(f"❌ Migration Failed: {e}")

    # --- TEST DATA (Ensure these exist) ---
    c.execute("INSERT OR IGNORE INTO illegal_tokens (currency, issuer) VALUES (?, ?)", 
              ("XRPP", "Any")) 
    c.execute("INSERT OR IGNORE INTO scammer_list (address, reason) VALUES (?, ?)", 
              ("rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh", "Reported Phishing Scam"))

    conn.commit()
    conn.close()

init_db()

# --- Helpers ---
def get_db():
    conn = sqlite3.connect('xrpl_app.db')
    conn.row_factory = sqlite3.Row
    return conn

def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

def resolve_user(address):
    """
    Look up a wallet address in our DB. 
    Returns dict {username, phone} or None.
    """
    conn = get_db()
    user = conn.execute("SELECT username, phone FROM users WHERE wallet_address = ?", (address,)).fetchone()
    conn.close()
    if user:
        return {'name': user['username'], 'phone': user['phone']}
    return None

# --- Safety Check Logic ---
def check_safety(destination_address, proposed_currency):
    conn = get_db()
    warnings = []
    
    # 0. Check if it's a known user (Friendly Check)
    known_user = resolve_user(destination_address)
    recipient_display = f"{known_user['name']} ({known_user['phone']})" if known_user else "Unknown / External User"

    # 1. CHECK: Is destination a Proven Scammer?
    scammer = conn.execute("SELECT * FROM scammer_list WHERE address = ?", (destination_address,)).fetchone()
    if scammer:
        conn.close()
        return "SCAMMER", "Likely Scammer (Proven)", [f"Address is in global denylist: {scammer['reason']}"], recipient_display

    # 2. CHECK: Token Mimicry (Typosquatting)
    # If user tries to send "XRPP", "US0", etc.
    whitelist = ["XRP", "USD", "EUR", "SOLO"] 
    curr = proposed_currency.upper()
    
    for good_token in whitelist:
        if curr != good_token:
            ratio = difflib.SequenceMatcher(None, curr, good_token).ratio()
            if 0.75 < ratio < 1.0:
                warnings.append(f"Token '{curr}' looks deceptively similar to legitimate token '{good_token}'")
                conn.close()
                return "SUSPICIOUS", "Potentially Fake Token", warnings, recipient_display

    # 3. CHECK: Does destination hold Illegal Tokens? (Wallet Scan)
    try:
        req = AccountLines(account=destination_address, ledger_index="validated")
        response = client.request(req)
        lines = response.result.get('lines', [])
        
        bad_tokens = conn.execute("SELECT currency FROM illegal_tokens").fetchall()
        bad_token_list = [t['currency'] for t in bad_tokens]
        
        for line in lines:
            if line['currency'] in bad_token_list:
                warnings.append(f"Destination wallet holds illegal token: {line['currency']}")
                conn.close()
                return "SUSPICIOUS", "Suspicious History", warnings, recipient_display

    except Exception as e:
        # If address is not found on ledger (unfunded), it's high risk
        warnings.append(f"Address not found on ledger or error scanning: {e}")

    conn.close()
    
    if warnings:
        return "SUSPICIOUS", "Caution Required", warnings, recipient_display
        
    return "SAFE", "Safe", ["Destination appears clean.", "Token checks passed."], recipient_display

# --- Routes ---

@app.route('/')
def index():
    return redirect(url_for('login'))

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        phone = request.form['phone']
        password = request.form['password']
        
        try:
            # Generate Wallet
            wallet = generate_faucet_wallet(client, debug=True)
            conn = get_db()
            conn.execute('INSERT INTO users (username, phone, password, wallet_seed, wallet_address) VALUES (?, ?, ?, ?, ?)',
                         (username, phone, hash_password(password), wallet.seed, wallet.classic_address))
            conn.commit()
            conn.close()
            flash('Account created! Login now.')
            return redirect(url_for('login'))
        except Exception as e:
            flash(f"Error: {e}")
            
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        conn = get_db()
        user = conn.execute('SELECT * FROM users WHERE username = ?', (username,)).fetchone()
        conn.close()
        
        if user and user['password'] == hash_password(password):
            session['user_id'] = user['id']
            session['username'] = user['username']
            session['phone'] = user['phone']
            session['address'] = user['wallet_address']
            return redirect(url_for('dashboard'))
        else:
            flash('Invalid login')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/dashboard')
def dashboard():
    if 'user_id' not in session: return redirect(url_for('login'))
    
    # 1. Fetch Balance
    balance_xrp = "0"
    try:
        acct_info = AccountInfo(account=session['address'], ledger_index="validated")
        res = client.request(acct_info)
        balance_xrp = int(res.result['account_data']['Balance']) / 1_000_000
    except:
        balance_xrp = "Error"

    # 2. Fetch Notifications
    conn = get_db()
    notifs = conn.execute("SELECT * FROM notifications WHERE user_address = ? ORDER BY id DESC", 
                          (session['address'],)).fetchall()
    conn.close()

    return render_template('dashboard.html', 
                           address=session['address'], 
                           username=session['username'],
                           phone=session['phone'],
                           balance=balance_xrp,
                           notifications=notifs)

@app.route('/stage_transaction', methods=['POST'])
def stage_transaction():
    if 'user_id' not in session: return redirect(url_for('login'))
    
    recipient_phone = request.form.get('recipient_phone')
    amount = request.form.get('amount')
    currency = request.form.get('currency', 'XRP')

    # 1. Resolve Phone to Address
    user_match = get_user_by_phone(recipient_phone)
    
    if user_match:
        destination_address = user_match['wallet_address']
        recipient_display = f"{user_match['username']} ({recipient_phone})"
    else:
        # Fallback: If not in DB, we treat the input as a raw address 
        # but flag it as unknown in the safety check
        destination_address = recipient_phone 
        recipient_display = f"Unknown User ({recipient_phone})"

    tx_data = {
        'destination': destination_address,
        'amount': amount,
        'currency': currency,
        'issuer': request.form.get('issuer', ''),
        'recipient_phone': recipient_phone # Keep for display
    }

    # 2. Run Safety Checks
    status, short_msg, details, _ = check_safety(destination_address, currency)

    return render_template('safety_check.html', 
                           tx_data=tx_data, 
                           safety_status=status, 
                           safety_msg=short_msg, 
                           safety_details=details,
                           recipient_name=recipient_display)

@app.route('/finalize_transaction', methods=['POST'])
def finalize_transaction():
    if 'user_id' not in session: return redirect(url_for('login'))
    
    conn = get_db()
    user_row = conn.execute('SELECT wallet_seed, username, phone FROM users WHERE id = ?', (session['user_id'],)).fetchone()
    
    dest = request.form['destination']
    amt = request.form['amount']
    curr = request.form.get('currency', 'XRP')
    
    try:
        wallet = Wallet.from_seed(seed=user_row['wallet_seed'])
        
        # ... (Transaction Submission Logic same as before) ...
        tx = Payment(account=wallet.classic_address, destination=dest, amount=xrp_to_drops(float(amt)))
        response = submit_and_wait(tx, client, wallet)
        
        if response.result['meta']['TransactionResult'] == "tesSUCCESS":
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            
            # Identify Recipient for the Sender's Notification
            recipient_row = conn.execute("SELECT username, phone FROM users WHERE wallet_address = ?", (dest,)).fetchone()
            if recipient_row:
                recipient_label = f"{recipient_row['username']} ({recipient_row['phone']})"
            else:
                recipient_label = f"External Wallet ({dest[:8]}...)"

            sender_label = f"{user_row['username']} ({user_row['phone']})"

            # 1. Notify Recipient: "You received funds from User (Phone)"
            conn.execute("INSERT INTO notifications (user_address, message, related_name, timestamp) VALUES (?, ?, ?, ?)",
                         (dest, f"Received {amt} {curr}", sender_label, timestamp))
            
            # 2. Notify Sender: "Successfully sent funds to Recipient (Phone)"
            conn.execute("INSERT INTO notifications (user_address, message, related_name, timestamp) VALUES (?, ?, ?, ?)",
                         (session['address'], f"Sent {amt} {curr}", recipient_label, timestamp))
            
            conn.commit()
            flash(f"Success! Sent to {recipient_label}")
        
    except Exception as e:
        flash(f"Error: {str(e)}")
    
    conn.close()
    return redirect(url_for('dashboard'))

if __name__ == '__main__':
    app.run(debug=True)