from flask import Flask, render_template, request, jsonify
import asyncio
from XRPL_Functions import XRPAccount, now_utc
from xrpl.asyncio.clients import AsyncJsonRpcClient
from xrpl.wallet import Wallet
import database
from werkzeug.security import check_password_hash, generate_password_hash

app = Flask(__name__)
XRPL_CLIENT_URL = "https://s.altnet.rippletest.net:51234"

async def get_account(username: str) -> XRPAccount:
    user = database.get_user_by_username(username)
    if not user:
        raise ValueError("User not found")
    wallet_data = database.get_wallet_by_user_id(user['id'])
    if not wallet_data:
        raise ValueError("Wallet not found")
    
    wallet = Wallet.from_seed(wallet_data['seed'])
    client = AsyncJsonRpcClient(XRPL_CLIENT_URL)
    return XRPAccount(username=username, wallet=wallet, client=client)

@app.route('/')
def landing():
    return render_template('landing.html')

@app.route('/dashboard')
def dashboard():
    return render_template('dashboard.html')

@app.route('/api/auth/register', methods=['POST'])
def register():
    data = request.json
    username = data.get('username', '').lower()
    password = data.get('password')
    phone = data.get('phone')
    
    if not username or not password or not phone:
        return jsonify({"error": "Username, password and phone required"}), 400

    async def _create():
        client = AsyncJsonRpcClient(XRPL_CLIENT_URL)
        account = await XRPAccount.create_new(username, client)
        return account

    try:
        # Check if user exists
        if database.get_user_by_username(username):
            return jsonify({"error": "Username already exists"}), 400
            
        account = asyncio.run(_create())
        password_hash = generate_password_hash(password)
        database.add_user_and_wallet(username, password_hash, account.address, account.wallet.seed, phone)
        
        return jsonify({
            "message": "User registered and wallet created successfully",
            "username": account.username,
            "address": account.address
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/auth/login', methods=['POST'])
def login():
    data = request.json
    username = data.get('username', '').lower()
    password = data.get('password')
    
    if not username or not password:
        return jsonify({"error": "Username and password required"}), 400
        
    user = database.get_user_by_username(username)
    if not user or not check_password_hash(user['password'], password):
        return jsonify({"error": "Invalid username or password"}), 401
        
    return jsonify({"message": "Login successful", "username": username})

@app.route('/api/resolve_phone', methods=['POST'])
def resolve_phone():
    data = request.json
    phone = data.get('phone')
    if not phone:
        return jsonify({"error": "Phone number required"}), 400
        
    user = database.get_user_by_phone(phone)
    if not user:
        return jsonify({"error": "No wallet found for this phone number"}), 404
        
    wallet = database.get_wallet_by_user_id(user['id'])
    return jsonify({"address": wallet['address'], "username": user['username']})

@app.route('/api/wallet/history', methods=['GET'])
def get_history():
    username = request.args.get('username')
    if not username:
        return jsonify({"error": "Valid username is required"}), 400

    async def _get_history():
        account = await get_account(username)
        return await account.get_transaction_history()

    try:
        history = asyncio.run(_get_history())
        return jsonify({"history": history})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/wallet/balance', methods=['GET'])
def get_balance():
    username = request.args.get('username')
    if not username:
        return jsonify({"error": "Valid username is required"}), 400

    async def _get_balance():
        account = await get_account(username)
        balance = await account.get_xrp_balance()
        return balance

    try:
        balance = asyncio.run(_get_balance())
        return jsonify({"balance": balance, "currency": "XRP"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/xrp/send', methods=['POST'])
def send_xrp():
    data = request.json
    username = data.get('username')
    destination = data.get('destination')
    amount = data.get('amount')

    if not all([username, destination, amount]):
        return jsonify({"error": "Missing parameters"}), 400

    # Scam Check
    if destination in ['rScammer123456789XRP', 'rSuspiciousXYZ12345']:
        return jsonify({"error": f"Security Alert: The destination address {destination} has been flagged for suspicious activity. Transaction blocked."}), 403

    async def _send():
        account = await get_account(username)
        result = await account.send_xrp(destination, float(amount))
        return result

    try:
        result = asyncio.run(_send())
        return jsonify({"message": "XRP Sent successfully", "result": result})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/trustline/create', methods=['POST'])
def create_trustline():
    data = request.json
    username = data.get('username')
    currency = data.get('currency')
    issuer = data.get('issuer')
    limit = data.get('limit', "1000000")

    if not all([username, currency, issuer]):
        return jsonify({"error": "Missing parameters"}), 400

    async def _trust():
        account = await get_account(username)
        result = await account.set_trust_line(currency, issuer, limit)
        return result

    try:
        result = asyncio.run(_trust())
        return jsonify({"message": "Trustline created", "result": result})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/trade/create', methods=['POST'])
def create_trade():
    """Create a DEX offer"""
    data = request.json
    username = data.get('username')
    give_currency = data.get('give_currency')
    give_issuer = data.get('give_issuer', "")
    give_amount = data.get('give_amount')
    want_currency = data.get('want_currency')
    want_issuer = data.get('want_issuer', "")
    want_amount = data.get('want_amount')

    if not all([username, give_currency, give_amount, want_currency, want_amount]):
        return jsonify({"error": "Missing parameters"}), 400

    async def _offer():
        account = await get_account(username)
        result = await account.create_offer_checked(
            give_currency, give_issuer, str(give_amount),
            want_currency, want_issuer, str(want_amount)
        )
        return result

    try:
        result = asyncio.run(_offer())
        return jsonify({"message": "Offer created", "result": result})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/escrow/create', methods=['POST'])
def create_escrow():
    data = request.json
    username = data.get('username')
    destination = data.get('destination')
    amount_xrp = data.get('amount_xrp')
    # For simplicity of the example, release_time offset in minutes
    release_minutes = data.get('release_minutes', 5) 
    
    if not all([username, destination, amount_xrp]):
        return jsonify({"error": "Missing parameters"}), 400

    async def _escrow():
        account = await get_account(username)
        from datetime import timedelta
        release_time = now_utc() + timedelta(minutes=int(release_minutes))
        result = await account.create_time_escrow_xrp(destination, float(amount_xrp), release_time)
        return result

    try:
        result = asyncio.run(_escrow())
        return jsonify({"message": "Escrow created successfully", "result": result})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/escrow/finish', methods=['POST'])
def finish_escrow():
    data = request.json
    username = data.get('username')
    owner_address = data.get('owner_address')
    escrow_sequence = data.get('escrow_sequence')
    
    if not all([username, owner_address, escrow_sequence]):
        return jsonify({"error": "Missing parameters"}), 400

    async def _finish():
        account = await get_account(username)
        result = await account.finish_escrow(owner_address, int(escrow_sequence))
        return result

    try:
        result = asyncio.run(_finish())
        return jsonify({"message": "Escrow finished successfully", "result": result})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/token/send', methods=['POST'])
def send_token():
    data = request.json
    username = data.get('username')
    destination = data.get('destination')
    currency = data.get('currency')
    issuer = data.get('issuer')
    amount = data.get('amount')

    if not all([username, destination, currency, issuer, amount]):
        return jsonify({"error": "Missing parameters"}), 400

    # Scam Check
    if destination in ['rScammer123456789XRP', 'rSuspiciousXYZ12345']:
        return jsonify({"error": f"Security Alert: The destination address {destination} has been flagged for suspicious activity. Transaction blocked."}), 403

    async def _send_token():
        account = await get_account(username)
        result = await account.send_token_checked(destination, currency, issuer, str(amount))
        return result

    try:
        result = asyncio.run(_send_token())
        return jsonify({"message": "Token Sent successfully", "result": result})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/check-address', methods=['POST'])
def check_address():
    data = request.json
    address = data.get('address')
    currency = data.get('currency', 'XRP')
    
    if not address:
        return jsonify({"error": "Missing address parameter"}), 400

    async def _check():
        client = AsyncJsonRpcClient(XRPL_CLIENT_URL)
        from xrpl.models.requests import AccountInfo, AccountLines
        from xrpl.asyncio.clients import XRPLRequestFailureException
        
        result = {
            "valid": False,
            "age_months": 0,
            "blacklisted": False,
            "tx_count": 0,
            "has_trustline": False,
            "risk": "high"
        }
        
        # Hardcoded static checks for specific bad actors
        if address in ['rScammer123456789XRP', 'rSuspiciousXYZ12345', 'rMuJUj7gnDHeqrzsFPDVm4TxAHbnyzjuiC']:
            result["blacklisted"] = True
            result["risk"] = "high"
            return result
        
        if address == 'rURb8kkgrhcUZ7otsjHJG6AaVnGQeeww16':
            result["valid"] = True
            result["risk"] = "medium"
            return result
        
        try:
            # 1. Check if account exists and get info
            acct_info_req = AccountInfo(account=address, ledger_index="validated")
            acct_info_res = await client.request(acct_info_req)
            
            if acct_info_res.is_successful():
                result["valid"] = True
                # Estimate age based on sequence number (very rough heuristic)
                seq = acct_info_res.result.get("account_data", {}).get("Sequence", 0)
                result["tx_count"] = seq
                result["age_months"] = max(1, seq // 1000) # completely arbitrary for demo
                result["risk"] = "low" if seq > 100 else "medium"
                
                # 2. Check trustlines if currency is not XRP
                if currency and currency != 'XRP':
                    lines_req = AccountLines(account=address)
                    lines_res = await client.request(lines_req)
                    if lines_res.is_successful():
                        lines = lines_res.result.get("lines", [])
                        result["has_trustline"] = any(l.get("currency") == currency for l in lines)
                else:
                    result["has_trustline"] = True # XRP doesn't need a trustline
        
        except XRPLRequestFailureException as e:
            if e.error == "actNotFound":
                result["valid"] = False
                result["risk"] = "high"
            else:
                return {"error": str(e)}
        except Exception as e:
            return {"error": str(e)}
            
        return result

    try:
        res = asyncio.run(_check())
        if "error" in res:
            return jsonify(res), 500
        return jsonify(res)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == '__main__':
    database.init_db()
    database.seed_db_if_empty()
    app.run(debug=True, port=5000)
