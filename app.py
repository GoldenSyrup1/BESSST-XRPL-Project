from flask import Flask, render_template, request, jsonify
import asyncio
from typing import Any, Dict, Optional

from XRPL_Functions import XRPAccount, now_utc
from xrpl.asyncio.clients import AsyncJsonRpcClient, XRPLRequestFailureException
from xrpl.wallet import Wallet
from xrpl.models.requests import AccountInfo, AccountLines

import database
from werkzeug.security import check_password_hash, generate_password_hash

app = Flask(__name__)
XRPL_CLIENT_URL = "https://s.altnet.rippletest.net:51234"

TOKEN_REGISTRY: Dict[str, str] = {
    "USD": "rhub8VRN55s94qWKDv6jmDy1pUykJzF3wq",
    "EUR": "rvYAfWj5gh67oV6fW32ZzP3Aw4Eubs59B",
    "BTC": "rMwjYedjc7qqtKYVLiAccJSmCwih4LnE2q",
    "ETH": "r9cZA1mLK5R5Am25ArfXFmqgNwjZgnfk59",
}

BLACKLISTED_ADDRESSES = {
    "rScammer123456789XRP",
    "rSuspiciousXYZ12345",
    "rMuJUj7gnDHeqrzsFPDVm4TxAHbnyzjuiC",
}


def ok(data: Any, status: int = 200):
    return jsonify({"success": True, "data": data}), status


def err(message: str, status: int = 400):
    return jsonify({"success": False, "error": message}), status


def payload() -> Dict[str, Any]:
    return request.get_json(silent=True) or {}


def normalize_username(raw: Any) -> str:
    return str(raw or "").strip().lower()


def normalize_currency(raw: Any) -> str:
    return str(raw or "").strip().upper()


def normalize_optional(raw: Any) -> Optional[str]:
    value = str(raw or "").strip()
    return value or None


def has_required_value(value: Any) -> bool:
    return str(value or "").strip() != ""


def resolve_issuer(currency: str, issuer_override: Optional[str] = None) -> Optional[str]:
    if currency == "XRP":
        return ""
    override = normalize_optional(issuer_override)
    if override:
        return override
    return TOKEN_REGISTRY.get(currency)


def normalize_amount(amount: Any) -> Dict[str, Any]:
    if isinstance(amount, str):
        # XRP in drops
        try:
            return {
                "currency": "XRP",
                "issuer": "",
                "value": str(float(amount) / 1_000_000),
            }
        except Exception:
            return {"currency": "XRP", "issuer": "", "value": str(amount)}

    if isinstance(amount, dict):
        return {
            "currency": amount.get("currency", ""),
            "issuer": amount.get("issuer", ""),
            "value": str(amount.get("value", "0")),
        }

    return {"currency": "", "issuer": "", "value": "0"}


def normalize_offer(offer: Dict[str, Any]) -> Dict[str, Any]:
    taker_gets = normalize_amount(offer.get("taker_gets"))
    taker_pays = normalize_amount(offer.get("taker_pays"))

    funded_gets_raw = offer.get("taker_gets_funded")
    funded_pays_raw = offer.get("taker_pays_funded")

    partially_filled = (
        funded_gets_raw is not None and str(funded_gets_raw) != str(offer.get("taker_gets"))
    ) or (
        funded_pays_raw is not None and str(funded_pays_raw) != str(offer.get("taker_pays"))
    )

    return {
        "offer_sequence": int(offer.get("seq", 0)),
        "sell": taker_gets,
        "buy": taker_pays,
        "status": "partially_filled" if partially_filled else "open",
        "funded": {
            "taker_gets_funded": funded_gets_raw,
            "taker_pays_funded": funded_pays_raw,
        },
    }


def ripple_to_unix_seconds(ripple_seconds: Any) -> Optional[int]:
    try:
        return int(ripple_seconds) + 946684800
    except Exception:
        return None


async def get_account(username: str) -> XRPAccount:
    user = database.get_user_by_username(username)
    if not user:
        raise ValueError("User not found")

    wallet_data = database.get_wallet_by_user_id(user["id"])
    if not wallet_data:
        raise ValueError("Wallet not found")

    wallet = Wallet.from_seed(wallet_data["seed"])
    client = AsyncJsonRpcClient(XRPL_CLIENT_URL)
    return XRPAccount(username=username, wallet=wallet, client=client)


@app.route("/")
@app.route("/index.html")
def landing():
    return render_template("index.html")


@app.route("/dashboard")
@app.route("/app.html")
def dashboard():
    return render_template("app.html")


@app.route("/api/config/tokens", methods=["GET"])
def get_tokens_config():
    return ok({"tokens": TOKEN_REGISTRY})


@app.route("/api/auth/register", methods=["POST"])
def register():
    data = payload()
    username = normalize_username(data.get("username"))
    password = str(data.get("password") or "").strip()
    phone = normalize_optional(data.get("phone"))

    if not username or not password:
        return err("field cannot be empty", 400)

    async def _create():
        client = AsyncJsonRpcClient(XRPL_CLIENT_URL)
        return await XRPAccount.create_new(username, client)

    try:
        if database.get_user_by_username(username):
            return err(f"{username} has already been registered. Please log in.", 400)

        if phone and database.get_user_by_phone(phone):
            return err("Phone number already registered", 400)

        account = asyncio.run(_create())
        password_hash = generate_password_hash(password)
        database.add_user_and_wallet(username, password_hash, account.address, account.wallet.seed, phone)

        return ok(
            {
                "message": "User registered and wallet created successfully",
                "username": account.username,
                "address": account.address,
            },
            201,
        )
    except Exception as exc:
        return err(str(exc), 500)


@app.route("/api/auth/login", methods=["POST"])
def login():
    data = payload()
    username = normalize_username(data.get("username"))
    password = str(data.get("password") or "").strip()

    if not username or not password:
        return err("field cannot be empty", 400)

    user = database.get_user_by_username(username)
    if not user or not check_password_hash(user["password"], password):
        return err("Invalid username or password", 401)

    wallet = database.get_wallet_by_user_id(user["id"])
    return ok(
        {
            "message": "Login successful",
            "username": username,
            "address": wallet["address"] if wallet else None,
        }
    )


@app.route("/api/resolve_phone", methods=["POST"])
def resolve_phone():
    data = payload()
    phone = normalize_optional(data.get("phone"))
    if not phone:
        return err("field cannot be empty", 400)

    user = database.get_user_by_phone(phone)
    if not user:
        return err("No wallet found for this phone number", 404)

    wallet = database.get_wallet_by_user_id(user["id"])
    return ok({"address": wallet["address"], "username": user["username"]})


@app.route("/api/wallet/balance", methods=["GET"])
def wallet_balance():
    username = normalize_username(request.args.get("username"))
    if not username:
        return err("field cannot be empty", 400)

    async def _balance():
        account = await get_account(username)
        return await account.get_xrp_balance()

    try:
        balance = asyncio.run(_balance())
        return ok({"balance": balance, "currency": "XRP"})
    except Exception as exc:
        return err(str(exc), 500)


@app.route("/api/wallet/history", methods=["GET"])
def wallet_history():
    username = normalize_username(request.args.get("username"))
    if not username:
        return err("field cannot be empty", 400)

    async def _history():
        account = await get_account(username)
        return await account.get_transaction_history(limit=200)

    try:
        history = asyncio.run(_history())
        return ok({"history": history})
    except Exception as exc:
        return err(str(exc), 500)


@app.route("/api/wallet/summary", methods=["GET"])
def wallet_summary():
    username = normalize_username(request.args.get("username"))
    if not username:
        return err("field cannot be empty", 400)

    async def _summary():
        account = await get_account(username)
        xrp_balance = await account.get_xrp_balance()
        trustlines = await account.get_trustlines()
        open_offers = await account.get_open_offers()

        token_balances = []
        normalized_trustlines = []
        for line in trustlines:
            currency = str(line.get("currency", "")).upper()
            issuer = line.get("account") or line.get("peer") or ""
            balance_value = str(line.get("balance", "0"))
            limit_value = str(line.get("limit", "0"))

            normalized = {
                "currency": currency,
                "issuer": issuer,
                "balance": balance_value,
                "limit": limit_value,
            }
            normalized_trustlines.append(normalized)

            token_balances.append(
                {
                    "currency": currency,
                    "issuer": issuer,
                    "balance": balance_value,
                    "limit": limit_value,
                }
            )

        return {
            "username": username,
            "address": account.address,
            "xrp_balance": xrp_balance,
            "token_balances": token_balances,
            "trustlines": normalized_trustlines,
            "open_offers_count": len(open_offers),
            "token_registry": TOKEN_REGISTRY,
        }

    try:
        return ok(asyncio.run(_summary()))
    except Exception as exc:
        return err(str(exc), 500)


@app.route("/api/trustline/check-issuer", methods=["POST"])
def check_issuer():
    data = payload()
    username = normalize_username(data.get("username"))
    issuer = normalize_optional(data.get("issuer") or data.get("issuer_override"))
    currency = normalize_currency(data.get("currency"))

    if not username or not issuer or not currency:
        return err("field cannot be empty", 400)

    async def _check():
        client = AsyncJsonRpcClient(XRPL_CLIENT_URL)
        result = {
            "valid": False,
            "blacklisted": issuer in BLACKLISTED_ADDRESSES,
            "age_months": 0,
            "issues_currency": False,
            "risk": "high",
            "details": {
                "issuer": issuer,
                "currency": currency,
            },
        }

        try:
            acct_info = await client.request(AccountInfo(account=issuer, ledger_index="validated"))
            if acct_info.is_successful():
                result["valid"] = True
                seq = int(acct_info.result.get("account_data", {}).get("Sequence", 0))
                result["age_months"] = max(1, seq // 1000)

            lines_resp = await client.request(AccountLines(account=issuer, ledger_index="validated"))
            if lines_resp.is_successful():
                lines = lines_resp.result.get("lines", [])
                result["issues_currency"] = any(str(line.get("currency", "")).upper() == currency for line in lines)

        except XRPLRequestFailureException as exc:
            if getattr(exc, "error", "") == "actNotFound":
                result["valid"] = False
            else:
                raise

        if result["blacklisted"] or not result["valid"]:
            result["risk"] = "high"
        elif not result["issues_currency"] or result["age_months"] < 6:
            result["risk"] = "medium"
        else:
            result["risk"] = "low"

        return result

    try:
        return ok(asyncio.run(_check()))
    except Exception as exc:
        return err(str(exc), 500)


@app.route("/api/trustline/create", methods=["POST"])
def create_trustline():
    data = payload()
    username = normalize_username(data.get("username"))
    currency = normalize_currency(data.get("currency"))
    limit = str(data.get("limit") or "1000000")
    issuer = resolve_issuer(currency, data.get("issuer") or data.get("issuer_override"))

    if not username or not currency:
        return err("field cannot be empty", 400)

    if currency == "XRP":
        return err("XRP does not use trust lines", 400)

    if not issuer:
        return err("Issuer is required for non-XRP trust lines", 400)

    async def _trust():
        account = await get_account(username)
        return await account.set_trust_line(currency, issuer, limit)

    try:
        result = asyncio.run(_trust())
        return ok({"message": "Trustline created", "result": result})
    except Exception as exc:
        return err(str(exc), 500)


@app.route("/api/xrp/send", methods=["POST"])
def send_xrp():
    data = payload()
    username = normalize_username(data.get("username"))
    destination = normalize_optional(data.get("destination"))
    amount = data.get("amount")

    if not username or not destination or not has_required_value(amount):
        return err("field cannot be empty", 400)

    if destination in BLACKLISTED_ADDRESSES:
        return err(
            f"Security Alert: The destination address {destination} has been flagged for suspicious activity. Transaction blocked.",
            403,
        )

    async def _send():
        account = await get_account(username)
        return await account.send_xrp(destination, float(amount))

    try:
        result = asyncio.run(_send())
        return ok({"message": "XRP sent successfully", "result": result, "tx_hash": result.get("hash")})
    except Exception as exc:
        return err(str(exc), 500)


@app.route("/api/token/send", methods=["POST"])
def send_token():
    data = payload()
    username = normalize_username(data.get("username"))
    destination = normalize_optional(data.get("destination"))
    currency = normalize_currency(data.get("currency"))
    amount = data.get("amount")
    issuer = resolve_issuer(currency, data.get("issuer") or data.get("issuer_override"))

    if not username or not destination or not currency or not has_required_value(amount):
        return err("field cannot be empty", 400)

    if destination in BLACKLISTED_ADDRESSES:
        return err(
            f"Security Alert: The destination address {destination} has been flagged for suspicious activity. Transaction blocked.",
            403,
        )

    if currency == "XRP":
        return err("Use /api/xrp/send for XRP transfers", 400)

    if not issuer:
        return err("Issuer is required for non-XRP transfers", 400)

    async def _send_token():
        account = await get_account(username)
        return await account.send_token_checked(destination, currency, issuer, str(amount))

    try:
        result = asyncio.run(_send_token())
        return ok({"message": "Token sent successfully", "result": result, "tx_hash": result.get("hash")})
    except Exception as exc:
        return err(str(exc), 500)


@app.route("/api/trade/create", methods=["POST"])
def create_trade():
    data = payload()
    username = normalize_username(data.get("username"))
    give_currency = normalize_currency(data.get("give_currency"))
    give_amount = data.get("give_amount")
    want_currency = normalize_currency(data.get("want_currency"))
    want_amount = data.get("want_amount")

    give_issuer = resolve_issuer(give_currency, data.get("give_issuer"))
    want_issuer = resolve_issuer(want_currency, data.get("want_issuer"))

    if not username or not give_currency or not want_currency or not has_required_value(give_amount) or not has_required_value(want_amount):
        return err("field cannot be empty", 400)

    if give_currency != "XRP" and not give_issuer:
        return err(f"Issuer is required for {give_currency}", 400)

    if want_currency != "XRP" and not want_issuer:
        return err(f"Issuer is required for {want_currency}", 400)

    async def _offer():
        account = await get_account(username)
        result = await account.create_offer_checked(
            give_currency,
            give_issuer or "",
            str(give_amount),
            want_currency,
            want_issuer or "",
            str(want_amount),
        )
        offer_sequence = result.get("tx_json", {}).get("Sequence")
        return {
            "result": result,
            "offer_sequence": int(offer_sequence) if offer_sequence is not None else None,
            "tx_hash": result.get("hash"),
        }

    try:
        result = asyncio.run(_offer())
        return ok({"message": "Offer created", **result}, 201)
    except Exception as exc:
        return err(str(exc), 500)


@app.route("/api/trade/open", methods=["GET"])
def trade_open():
    username = normalize_username(request.args.get("username"))
    if not username:
        return err("field cannot be empty", 400)

    async def _open():
        account = await get_account(username)
        offers = await account.get_open_offers()
        return [normalize_offer(offer) for offer in offers]

    try:
        offers = asyncio.run(_open())
        return ok({"offers": offers})
    except Exception as exc:
        return err(str(exc), 500)


@app.route("/api/trade/status", methods=["GET"])
def trade_status():
    username = normalize_username(request.args.get("username"))
    offer_sequence = request.args.get("offer_sequence")

    if not username or not has_required_value(offer_sequence):
        return err("field cannot be empty", 400)

    try:
        sequence_int = int(str(offer_sequence))
    except ValueError:
        return err("Invalid offer_sequence", 400)

    async def _status():
        account = await get_account(username)
        return await account.get_offer_status(sequence_int)

    try:
        status = asyncio.run(_status())
        return ok(status)
    except Exception as exc:
        return err(str(exc), 500)


@app.route("/api/trade/cancel", methods=["POST"])
def trade_cancel():
    data = payload()
    username = normalize_username(data.get("username"))
    offer_sequence = data.get("offer_sequence")

    if not username or not has_required_value(offer_sequence):
        return err("field cannot be empty", 400)

    try:
        sequence_int = int(str(offer_sequence))
    except ValueError:
        return err("Invalid offer_sequence", 400)

    async def _cancel():
        account = await get_account(username)
        result = await account.cancel_offer(sequence_int)
        return {"result": result, "tx_hash": result.get("hash")}

    try:
        result = asyncio.run(_cancel())
        return ok({"message": "Offer cancelled", **result})
    except Exception as exc:
        return err(str(exc), 500)


@app.route("/api/escrow/create", methods=["POST"])
def create_escrow():
    data = payload()
    username = normalize_username(data.get("username"))
    destination = normalize_optional(data.get("destination"))
    amount_xrp = data.get("amount_xrp")
    release_minutes = data.get("release_minutes", 5)

    if not username or not destination or not has_required_value(amount_xrp):
        return err("field cannot be empty", 400)

    async def _escrow():
        account = await get_account(username)
        from datetime import timedelta

        release_time = now_utc() + timedelta(minutes=int(release_minutes))
        return await account.create_time_escrow_xrp(destination, float(amount_xrp), release_time)

    try:
        result = asyncio.run(_escrow())
        return ok({"message": "Escrow created successfully", "result": result})
    except Exception as exc:
        return err(str(exc), 500)


@app.route("/api/escrow/finish", methods=["POST"])
def finish_escrow():
    data = payload()
    username = normalize_username(data.get("username"))
    owner_address = normalize_optional(data.get("owner_address"))
    escrow_sequence = data.get("escrow_sequence")

    if not username or not owner_address or not has_required_value(escrow_sequence):
        return err("field cannot be empty", 400)

    async def _finish():
        account = await get_account(username)
        return await account.finish_escrow(owner_address, int(escrow_sequence))

    try:
        result = asyncio.run(_finish())
        return ok({"message": "Escrow finished successfully", "result": result})
    except Exception as exc:
        return err(str(exc), 500)


@app.route("/api/check-address", methods=["POST"])
def check_address():
    data = payload()
    address = normalize_optional(data.get("address"))
    currency = normalize_currency(data.get("currency") or "XRP")
    issuer = resolve_issuer(currency, data.get("issuer") or data.get("issuer_override"))

    if not address:
        return err("field cannot be empty", 400)

    if currency != "XRP" and not issuer:
        return err(f"Issuer is required for {currency}", 400)

    async def _check():
        client = AsyncJsonRpcClient(XRPL_CLIENT_URL)
        result = {
            "valid": False,
            "age_months": 0,
            "blacklisted": address in BLACKLISTED_ADDRESSES,
            "tx_count": 0,
            "has_trustline": currency == "XRP",
            "risk": "high",
            "currency": currency,
            "issuer": issuer,
        }

        if result["blacklisted"]:
            result["risk"] = "high"
            return result

        try:
            acct_info = await client.request(AccountInfo(account=address, ledger_index="validated"))
            if acct_info.is_successful():
                result["valid"] = True
                seq = int(acct_info.result.get("account_data", {}).get("Sequence", 0))
                result["tx_count"] = seq
                result["age_months"] = max(1, seq // 1000)

                if currency != "XRP":
                    lines_req = AccountLines(account=address, peer=issuer, ledger_index="validated")
                    lines_res = await client.request(lines_req)
                    if lines_res.is_successful():
                        lines = lines_res.result.get("lines", [])
                        result["has_trustline"] = any(
                            str(line.get("currency", "")).upper() == currency for line in lines
                        )

        except XRPLRequestFailureException as exc:
            if getattr(exc, "error", "") == "actNotFound":
                result["valid"] = False
            else:
                raise

        if not result["valid"]:
            result["risk"] = "high"
        elif currency != "XRP" and not result["has_trustline"]:
            result["risk"] = "high"
        elif result["age_months"] < 6 or result["tx_count"] < 10:
            result["risk"] = "medium"
        else:
            result["risk"] = "low"

        return result

    try:
        return ok(asyncio.run(_check()))
    except Exception as exc:
        return err(str(exc), 500)


@app.route("/api/send", methods=["POST"])
def send_any_currency():
    data = payload()
    currency = normalize_currency(data.get("currency") or "XRP")
    if currency == "XRP":
        return send_xrp()
    return send_token()


if __name__ == "__main__":
    database.init_db()
    database.seed_db_if_empty()
    app.run(debug=True, port=5000)
