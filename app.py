from flask import Flask, render_template, request, jsonify
import asyncio
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional
import os

from XRPL_Functions import XRPAccount, now_utc
from xrpl.asyncio.clients import AsyncJsonRpcClient, XRPLRequestFailureException
from xrpl.wallet import Wallet
from xrpl.models.requests import AccountInfo, AccountLines, AccountTx, BookOffers
from xrpl.models.currencies import XRP, IssuedCurrency

import database
import enabled_tokens_store
from werkzeug.security import check_password_hash, generate_password_hash

app = Flask(__name__)
XRPL_CLIENT_URL = "https://s.altnet.rippletest.net:51234"
DEMO_FAKE_TRANSACTIONS = True

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
RIPPLE_EPOCH_OFFSET = 946684800


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


def xrpl_error_status(exc: Exception) -> int:
    message = str(exc)
    if "Transaction failed:" in message:
        return 400
    return 500


def looks_like_xrpl_address(raw: Any) -> bool:
    value = str(raw or "").strip()
    return value.lower().startswith("r")


def resolve_wallet_by_phone(phone: str) -> Dict[str, str]:
    user = database.get_user_by_phone(phone)
    if not user:
        raise ValueError("No wallet found for this phone number")

    wallet = database.get_wallet_by_user_id(user["id"])
    if not wallet:
        raise ValueError("Wallet not found for this user")

    return {"address": wallet["address"], "username": user["username"]}


def resolve_phone_to_address(phone: Optional[str]) -> Dict[str, Optional[str]]:
    phone_value = normalize_optional(phone)
    if not phone_value:
        raise ValueError("field cannot be empty")
    resolved = resolve_wallet_by_phone(phone_value)
    return {"phone": phone_value, "address": resolved["address"], "username": resolved["username"]}


def resolve_issuer_input(currency: str, issuer_raw: Any = None, issuer_phone_raw: Any = None) -> Dict[str, Optional[str]]:
    issuer_phone = normalize_optional(issuer_phone_raw)
    if issuer_phone:
        resolved = resolve_phone_to_address(issuer_phone)
        return {
            "issuer": resolved["address"],
            "issuer_phone": resolved["phone"],
            "issuer_username": resolved["username"],
        }

    issuer = resolve_issuer(currency, issuer_raw)
    return {"issuer": issuer, "issuer_phone": None, "issuer_username": None}


def resolve_destination_input(destination: Any = None, destination_phone: Any = None) -> Dict[str, Optional[str]]:
    destination_value = normalize_optional(destination)
    destination_phone_value = normalize_optional(destination_phone)

    if destination_phone_value:
        resolved = resolve_wallet_by_phone(destination_phone_value)
        return {
            "address": resolved["address"],
            "resolved_phone": destination_phone_value,
            "resolved_username": resolved["username"],
        }

    if not destination_value:
        raise ValueError("field cannot be empty")

    if looks_like_xrpl_address(destination_value):
        return {"address": destination_value, "resolved_phone": None, "resolved_username": None}

    resolved = resolve_wallet_by_phone(destination_value)
    return {
        "address": resolved["address"],
        "resolved_phone": destination_value,
        "resolved_username": resolved["username"],
    }


def resolve_issuer(currency: str, issuer_override: Optional[str] = None) -> Optional[str]:
    if currency == "XRP":
        return ""
    override = normalize_optional(issuer_override)
    if override:
        return override
    return TOKEN_REGISTRY.get(currency)


def build_book_currency(currency: str, issuer: Optional[str]):
    currency_value = normalize_currency(currency)
    if currency_value == "XRP":
        return XRP()

    issuer_value = normalize_optional(issuer)
    if not issuer_value:
        raise ValueError(f"Issuer is required for {currency_value}")
    return IssuedCurrency(currency=currency_value, issuer=issuer_value)


def parse_book_offer_amounts(offer: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    owner_give = normalize_amount(offer.get("TakerGets") or offer.get("taker_gets"))
    owner_want = normalize_amount(offer.get("TakerPays") or offer.get("taker_pays"))
    return {"owner_give": owner_give, "owner_want": owner_want}


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


def utc_to_ripple_seconds(dt_utc: datetime) -> int:
    return int(dt_utc.timestamp()) - RIPPLE_EPOCH_OFFSET


def build_fake_history_entries(username: str, account_address: str):
    now = datetime.now(timezone.utc)
    usd_issuer = TOKEN_REGISTRY.get("USD", "")

    tx_rows = [
        {
            "tx": {
                "TransactionType": "OfferCreate",
                "Account": account_address,
                "TakerGets": "2500000",
                "TakerPays": {"currency": "USD", "issuer": usd_issuer, "value": "5"},
                "Sequence": 990001,
                "hash": f"DEMO-{username.upper()}-SWAP-1",
                "date": utc_to_ripple_seconds(now - timedelta(minutes=20)),
            },
            "meta": {"TransactionResult": "tesSUCCESS"},
        },
        {
            "tx": {
                "TransactionType": "Payment",
                "Account": account_address,
                "Destination": "rDemoRecipient0000000000000000001",
                "Amount": "1200000",
                "hash": f"DEMO-{username.upper()}-PAY-OUT-1",
                "date": utc_to_ripple_seconds(now - timedelta(hours=2)),
            },
            "meta": {"TransactionResult": "tesSUCCESS"},
        },
        {
            "tx": {
                "TransactionType": "Payment",
                "Account": "rDemoSender000000000000000000001",
                "Destination": account_address,
                "Amount": "3400000",
                "hash": f"DEMO-{username.upper()}-PAY-IN-1",
                "date": utc_to_ripple_seconds(now - timedelta(hours=6)),
            },
            "meta": {"TransactionResult": "tesSUCCESS"},
        },
        {
            "tx": {
                "TransactionType": "TrustSet",
                "Account": account_address,
                "LimitAmount": {"currency": "USD", "issuer": usd_issuer, "value": "1000"},
                "hash": f"DEMO-{username.upper()}-TRUST-1",
                "date": utc_to_ripple_seconds(now - timedelta(days=1, hours=3)),
            },
            "meta": {"TransactionResult": "tesSUCCESS"},
        },
    ]
    return tx_rows


def build_demo_trade_amount(currency: str, issuer: str, amount: Any):
    currency_value = normalize_currency(currency)
    amount_value = str(amount)
    if currency_value == "XRP":
        try:
            return str(max(0, int(float(amount_value) * 1_000_000)))
        except Exception:
            return "0"
    return {
        "currency": currency_value,
        "issuer": normalize_optional(issuer) or TOKEN_REGISTRY.get(currency_value, ""),
        "value": amount_value,
    }


def build_demo_trade_response(
    username: str,
    give_currency: str,
    give_issuer: str,
    give_amount: Any,
    want_currency: str,
    want_issuer: str,
    want_amount: Any,
) -> Dict[str, Any]:
    timestamp_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    user_offset = sum(ord(ch) for ch in username)
    offer_sequence = ((timestamp_ms + user_offset) % 90_000_000) + 1_000_000
    tx_hash = f"DEMO-SWAP-{username.upper()}-{timestamp_ms}"
    tx_json = {
        "TransactionType": "OfferCreate",
        "Sequence": offer_sequence,
        "TakerGets": build_demo_trade_amount(give_currency, give_issuer, give_amount),
        "TakerPays": build_demo_trade_amount(want_currency, want_issuer, want_amount),
    }
    return {
        "result": {
            "engine_result": "tesSUCCESS",
            "hash": tx_hash,
            "tx_json": tx_json,
        },
        "offer_sequence": offer_sequence,
        "tx_hash": tx_hash,
        "simulated": True,
    }


def ripple_to_unix_seconds(ripple_seconds: Any) -> Optional[int]:
    try:
        return int(ripple_seconds) + RIPPLE_EPOCH_OFFSET
    except Exception:
        return None


def ripple_to_utc_datetime(ripple_seconds: Any) -> Optional[datetime]:
    unix_seconds = ripple_to_unix_seconds(ripple_seconds)
    if unix_seconds is None:
        return None
    try:
        return datetime.fromtimestamp(unix_seconds, tz=timezone.utc)
    except Exception:
        return None


def full_months_between(start_utc: datetime, end_utc: datetime) -> int:
    if end_utc <= start_utc:
        return 0

    months = (end_utc.year - start_utc.year) * 12 + (end_utc.month - start_utc.month)
    if end_utc.day < start_utc.day:
        months -= 1
    return max(0, months)


async def estimate_account_age_months(client: AsyncJsonRpcClient, account: str) -> int:
    req = AccountTx(
        account=account,
        ledger_index_min=-1,
        ledger_index_max=-1,
        limit=1,
        forward=True,
    )
    resp = await client.request(req)
    if not resp.is_successful():
        return 0

    txs = resp.result.get("transactions", [])
    if not txs:
        return 0

    first_tx = txs[0].get("tx", {})
    created_at = ripple_to_utc_datetime(first_tx.get("date"))
    if not created_at:
        return 0

    # XRPL started in 2012; treat out-of-range timestamps as invalid.
    if created_at < datetime(2012, 1, 1, tzinfo=timezone.utc):
        return 0

    now = now_utc()
    if created_at > now + timedelta(days=1):
        return 0

    return full_months_between(created_at, now)


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

    return ok(
        {
            "message": "Login successful",
            "username": username,
        }
    )


@app.route("/api/resolve_phone", methods=["POST"])
def resolve_phone():
    data = payload()
    phone = normalize_optional(data.get("phone"))
    if not phone:
        return err("field cannot be empty", 400)

    try:
        resolved = resolve_wallet_by_phone(phone)
        return ok({"username": resolved["username"], "phone": phone})
    except ValueError as exc:
        return err(str(exc), 404)


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
        chain_history = await account.get_transaction_history(limit=200)
        return chain_history, account.address

    try:
        chain_history, account_address = asyncio.run(_history())
        fake_history = build_fake_history_entries(username, account_address)

        merged_history = []
        seen_hashes = set()
        for entry in fake_history + chain_history:
            tx_hash = str(entry.get("tx", {}).get("hash") or "")
            dedupe_key = tx_hash or str(len(merged_history))
            if dedupe_key in seen_hashes:
                continue
            seen_hashes.add(dedupe_key)
            merged_history.append(entry)

        def _date_key(item: Dict[str, Any]) -> int:
            try:
                return int(item.get("tx", {}).get("date", 0))
            except Exception:
                return 0

        merged_history.sort(key=_date_key, reverse=True)
        return ok({"history": merged_history})
    except Exception as exc:
        return err(str(exc), 500)


@app.route("/api/wallet/summary", methods=["GET"])
def wallet_summary():
    username = normalize_username(request.args.get("username"))
    if not username:
        return err("field cannot be empty", 400)
    user = database.get_user_by_username(username)

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
            "phone": user["phone"] if user else None,
            "xrp_balance": xrp_balance,
            "token_balances": token_balances,
            "trustlines": normalized_trustlines,
            "enabled_tokens": enabled_tokens_store.get_enabled_tokens_by_username(username),
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
    issuer_input = normalize_optional(data.get("issuer") or data.get("issuer_override"))
    issuer_phone = normalize_optional(data.get("issuer_phone"))
    currency = normalize_currency(data.get("currency"))

    if not username or not currency:
        return err("field cannot be empty", 400)

    try:
        issuer_resolution = resolve_issuer_input(currency, issuer_input, issuer_phone)
    except ValueError as exc:
        status = 404 if "No wallet found for this phone number" in str(exc) else 400
        return err(str(exc), status)

    issuer = issuer_resolution["issuer"]
    if not issuer:
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
                "issuer_phone": issuer_resolution.get("issuer_phone"),
                "issuer_username": issuer_resolution.get("issuer_username"),
                "currency": currency,
            },
        }

        try:
            acct_info = await client.request(AccountInfo(account=issuer, ledger_index="validated"))
            if acct_info.is_successful():
                result["valid"] = True
                result["age_months"] = await estimate_account_age_months(client, issuer)

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
    issuer_input = data.get("issuer") or data.get("issuer_override")
    issuer_phone = data.get("issuer_phone")

    if not username or not currency:
        return err("field cannot be empty", 400)

    if currency == "XRP":
        return err("XRP does not use trust lines", 400)

    try:
        issuer_resolution = resolve_issuer_input(currency, issuer_input, issuer_phone)
    except ValueError as exc:
        status = 404 if "No wallet found for this phone number" in str(exc) else 400
        return err(str(exc), status)

    issuer = issuer_resolution["issuer"]
    if not issuer:
        return err("Issuer is required for non-XRP trust lines", 400)

    async def _trust():
        account = await get_account(username)
        if account.address == issuer:
            raise ValueError("Issuer cannot be your own wallet address")
        result = await account.set_trust_line(currency, issuer, limit)
        return {"result": result, "wallet_address": account.address}

    try:
        trust_response = asyncio.run(_trust())
        result = trust_response["result"]
        wallet_address = trust_response["wallet_address"]

        tx_outcome = str(result.get("engine_result") or result.get("meta", {}).get("TransactionResult") or "")
        if tx_outcome == "temDST_IS_SRC":
            return err("Issuer cannot be your own wallet address", 400)

        warning = None
        if tx_outcome.startswith("tes"):
            try:
                enabled_tokens_store.upsert_enabled_token(
                    username=username,
                    wallet_address=wallet_address,
                    currency=currency,
                    issuer=issuer,
                    trust_limit=limit,
                    tx_hash=result.get("hash"),
                )
            except Exception as storage_error:
                warning = f"Trustline created but token was not persisted: {storage_error}"

        response_data = {"message": "Trustline created", "result": result}
        if warning:
            response_data["warning"] = warning
        return ok(response_data)
    except ValueError as exc:
        return err(str(exc), 400)
    except Exception as exc:
        return err(str(exc), 500)


@app.route("/api/xrp/send", methods=["POST"])
def send_xrp():
    data = payload()
    username = normalize_username(data.get("username"))
    destination = normalize_optional(data.get("destination"))
    destination_phone = normalize_optional(data.get("destination_phone"))
    amount = data.get("amount")

    if not username or not has_required_value(amount):
        return err("field cannot be empty", 400)

    if not destination and not destination_phone:
        return err("field cannot be empty", 400)

    try:
        resolved_destination = resolve_destination_input(destination, destination_phone)
        destination = resolved_destination["address"]
    except ValueError as exc:
        status = 404 if "No wallet found for this phone number" in str(exc) else 400
        return err(str(exc), status)

    if destination in BLACKLISTED_ADDRESSES:
        return err(
            "Security Alert: The destination account has been flagged for suspicious activity. Transaction blocked.",
            403,
        )

    async def _send():
        account = await get_account(username)
        return await account.send_xrp(destination, float(amount))

    try:
        result = asyncio.run(_send())
        return ok(
            {
                "message": "XRP sent successfully",
                "result": result,
                "tx_hash": result.get("hash"),
                "resolved_phone": resolved_destination.get("resolved_phone"),
                "resolved_username": resolved_destination.get("resolved_username"),
            }
        )
    except Exception as exc:
        return err(str(exc), 500)


@app.route("/api/token/send", methods=["POST"])
def send_token():
    data = payload()
    username = normalize_username(data.get("username"))
    destination = normalize_optional(data.get("destination"))
    destination_phone = normalize_optional(data.get("destination_phone"))
    currency = normalize_currency(data.get("currency"))
    amount = data.get("amount")
    issuer_input = data.get("issuer") or data.get("issuer_override")
    issuer_phone = data.get("issuer_phone")

    if not username or not currency or not has_required_value(amount):
        return err("field cannot be empty", 400)

    if not destination and not destination_phone:
        return err("field cannot be empty", 400)

    try:
        resolved_destination = resolve_destination_input(destination, destination_phone)
        destination = resolved_destination["address"]
    except ValueError as exc:
        status = 404 if "No wallet found for this phone number" in str(exc) else 400
        return err(str(exc), status)

    if destination in BLACKLISTED_ADDRESSES:
        return err(
            "Security Alert: The destination account has been flagged for suspicious activity. Transaction blocked.",
            403,
        )

    if currency == "XRP":
        return err("Use /api/xrp/send for XRP transfers", 400)

    try:
        issuer_resolution = resolve_issuer_input(currency, issuer_input, issuer_phone)
    except ValueError as exc:
        status = 404 if "No wallet found for this phone number" in str(exc) else 400
        return err(str(exc), status)

    issuer = issuer_resolution["issuer"]
    if not issuer:
        return err("Issuer is required for non-XRP transfers", 400)

    async def _send_token():
        account = await get_account(username)
        return await account.send_token_checked(destination, currency, issuer, str(amount))

    try:
        result = asyncio.run(_send_token())
        return ok(
            {
                "message": "Token sent successfully",
                "result": result,
                "tx_hash": result.get("hash"),
                "resolved_phone": resolved_destination.get("resolved_phone"),
                "resolved_username": resolved_destination.get("resolved_username"),
                "issuer_phone": issuer_resolution.get("issuer_phone"),
                "issuer_username": issuer_resolution.get("issuer_username"),
            }
        )
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

    give_issuer_input = data.get("give_issuer")
    want_issuer_input = data.get("want_issuer")
    give_issuer_phone = data.get("give_issuer_phone")
    want_issuer_phone = data.get("want_issuer_phone")

    if not username or not give_currency or not want_currency or not has_required_value(give_amount) or not has_required_value(want_amount):
        return err("field cannot be empty", 400)

    try:
        if float(str(give_amount)) <= 0 or float(str(want_amount)) <= 0:
            return err("Amounts must be greater than 0", 400)
    except ValueError:
        return err("Amounts must be numeric", 400)

    try:
        give_issuer_resolution = resolve_issuer_input(give_currency, give_issuer_input, give_issuer_phone)
        want_issuer_resolution = resolve_issuer_input(want_currency, want_issuer_input, want_issuer_phone)
    except ValueError as exc:
        if not DEMO_FAKE_TRANSACTIONS:
            status = 404 if "No wallet found for this phone number" in str(exc) else 400
            return err(str(exc), status)
        give_issuer_resolution = {"issuer": resolve_issuer(give_currency, give_issuer_input) or ""}
        want_issuer_resolution = {"issuer": resolve_issuer(want_currency, want_issuer_input) or ""}

    give_issuer = give_issuer_resolution["issuer"]
    want_issuer = want_issuer_resolution["issuer"]

    if DEMO_FAKE_TRANSACTIONS:
        simulated = build_demo_trade_response(
            username=username,
            give_currency=give_currency,
            give_issuer=give_issuer or "",
            give_amount=give_amount,
            want_currency=want_currency,
            want_issuer=want_issuer or "",
            want_amount=want_amount,
        )
        return ok({"message": "Trade simulated", **simulated}, 201)

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
    except ValueError as exc:
        return err(str(exc), 400)
    except Exception as exc:
        return err(str(exc), xrpl_error_status(exc))


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


@app.route("/api/trade/book", methods=["GET"])
def trade_book():
    username = normalize_username(request.args.get("username"))
    sell_currency = normalize_currency(request.args.get("sell_currency"))
    buy_currency = normalize_currency(request.args.get("buy_currency"))
    sell_issuer_input = normalize_optional(request.args.get("sell_issuer"))
    buy_issuer_input = normalize_optional(request.args.get("buy_issuer"))
    sell_issuer_phone = normalize_optional(request.args.get("sell_issuer_phone"))
    buy_issuer_phone = normalize_optional(request.args.get("buy_issuer_phone"))
    limit_raw = request.args.get("limit", 10)

    if not sell_currency or not buy_currency:
        return err("field cannot be empty", 400)

    try:
        limit = int(str(limit_raw))
    except ValueError:
        return err("Invalid limit", 400)
    limit = max(1, min(limit, 50))

    try:
        sell_issuer_resolution = resolve_issuer_input(sell_currency, sell_issuer_input, sell_issuer_phone)
        buy_issuer_resolution = resolve_issuer_input(buy_currency, buy_issuer_input, buy_issuer_phone)
    except ValueError as exc:
        status = 404 if "No wallet found for this phone number" in str(exc) else 400
        return err(str(exc), status)

    sell_issuer = sell_issuer_resolution["issuer"]
    buy_issuer = buy_issuer_resolution["issuer"]

    async def _book():
        client = AsyncJsonRpcClient(XRPL_CLIENT_URL)
        exclude_address = None
        if username:
            requester = await get_account(username)
            exclude_address = requester.address
        req = BookOffers(
            taker_gets=build_book_currency(buy_currency, buy_issuer),
            taker_pays=build_book_currency(sell_currency, sell_issuer),
            ledger_index="validated",
            limit=limit,
        )
        resp = await client.request(req)
        if not resp.is_successful():
            raise RuntimeError("Could not fetch order book")

        offers = []
        for offer in resp.result.get("offers", []):
            owner = offer.get("Account") or offer.get("account")
            if exclude_address and owner == exclude_address:
                continue
            parsed_amounts = parse_book_offer_amounts(offer)
            offers.append(
                {
                    "owner": owner,
                    "offer_sequence": int(offer.get("Sequence") or offer.get("seq") or 0),
                    "quality": str(offer.get("quality", "")),
                    "owner_give": parsed_amounts["owner_give"],
                    "owner_want": parsed_amounts["owner_want"],
                }
            )

        return {
            "offers": offers,
            "sell_currency": sell_currency,
            "buy_currency": buy_currency,
        }

    try:
        return ok(asyncio.run(_book()))
    except ValueError as exc:
        return err(str(exc), 400)
    except Exception as exc:
        return err(str(exc), 500)


@app.route("/api/trade/incoming", methods=["GET"])
def trade_incoming():
    username = normalize_username(request.args.get("username"))
    limit_raw = request.args.get("limit", 20)
    per_book_limit_raw = request.args.get("per_book_limit", 5)

    if not username:
        return err("field cannot be empty", 400)

    try:
        limit = int(str(limit_raw))
        per_book_limit = int(str(per_book_limit_raw))
    except ValueError:
        return err("Invalid limit", 400)

    limit = max(1, min(limit, 50))
    per_book_limit = max(1, min(per_book_limit, 20))

    async def _incoming():
        account = await get_account(username)
        client = AsyncJsonRpcClient(XRPL_CLIENT_URL)

        # Build currency->issuer-set from registry + live trustlines.
        currency_issuers = defaultdict(set)
        currency_issuers["XRP"].add("")
        for cur, issuer in TOKEN_REGISTRY.items():
            currency_issuers[str(cur).upper()].add(str(issuer))

        trustlines = await account.get_trustlines()
        for line in trustlines:
            cur = normalize_currency(line.get("currency"))
            issuer = normalize_optional(line.get("account") or line.get("peer"))
            if cur and cur != "XRP" and issuer:
                currency_issuers[cur].add(issuer)

        pairs = []
        currencies = sorted(currency_issuers.keys())
        for sell_currency in currencies:
            for buy_currency in currencies:
                if sell_currency == buy_currency:
                    continue
                # Keep request volume reasonable while covering common flows.
                if sell_currency != "XRP" and buy_currency != "XRP":
                    continue

                for sell_issuer in currency_issuers[sell_currency]:
                    for buy_issuer in currency_issuers[buy_currency]:
                        if sell_currency != "XRP" and not sell_issuer:
                            continue
                        if buy_currency != "XRP" and not buy_issuer:
                            continue
                        pairs.append((sell_currency, sell_issuer, buy_currency, buy_issuer))

        seen = set()
        offers_out = []

        for sell_currency, sell_issuer, buy_currency, buy_issuer in pairs:
            req = BookOffers(
                taker_gets=build_book_currency(buy_currency, buy_issuer),
                taker_pays=build_book_currency(sell_currency, sell_issuer),
                ledger_index="validated",
                limit=per_book_limit,
            )
            resp = await client.request(req)
            if not resp.is_successful():
                continue

            for offer in resp.result.get("offers", []):
                owner = offer.get("Account") or offer.get("account")
                if owner == account.address:
                    continue

                offer_sequence = int(offer.get("Sequence") or offer.get("seq") or 0)
                if offer_sequence <= 0:
                    continue

                dedupe_key = (owner, offer_sequence)
                if dedupe_key in seen:
                    continue
                seen.add(dedupe_key)

                parsed_amounts = parse_book_offer_amounts(offer)
                offers_out.append(
                    {
                        "owner": owner,
                        "offer_sequence": offer_sequence,
                        "quality": str(offer.get("quality", "")),
                        "owner_give": parsed_amounts["owner_give"],
                        "owner_want": parsed_amounts["owner_want"],
                        "status": "open",
                    }
                )

                if len(offers_out) >= limit:
                    return {"offers": offers_out}

        return {"offers": offers_out}

    try:
        return ok(asyncio.run(_incoming()))
    except ValueError as exc:
        return err(str(exc), 400)
    except Exception as exc:
        return err(str(exc), xrpl_error_status(exc))


@app.route("/api/trade/take", methods=["POST"])
def take_trade_offer():
    data = payload()
    username = normalize_username(data.get("username"))
    owner_give_currency = normalize_currency(data.get("owner_give_currency"))
    owner_give_issuer = normalize_optional(data.get("owner_give_issuer")) or ""
    owner_give_amount = data.get("owner_give_amount")
    owner_want_currency = normalize_currency(data.get("owner_want_currency"))
    owner_want_issuer = normalize_optional(data.get("owner_want_issuer")) or ""
    owner_want_amount = data.get("owner_want_amount")

    if (
        not username
        or not owner_give_currency
        or not owner_want_currency
        or not has_required_value(owner_give_amount)
        or not has_required_value(owner_want_amount)
    ):
        return err("field cannot be empty", 400)

    try:
        if float(str(owner_give_amount)) <= 0 or float(str(owner_want_amount)) <= 0:
            return err("Amounts must be greater than 0", 400)
    except ValueError:
        return err("Amounts must be numeric", 400)

    if owner_give_currency != "XRP" and not owner_give_issuer:
        return err(f"Issuer is required for {owner_give_currency}", 400)
    if owner_want_currency != "XRP" and not owner_want_issuer:
        return err(f"Issuer is required for {owner_want_currency}", 400)

    async def _take():
        account = await get_account(username)
        result = await account.take_offer_exact(
            offer_owner_give_currency=owner_give_currency,
            offer_owner_give_issuer=owner_give_issuer,
            offer_owner_give_amount=str(owner_give_amount),
            offer_owner_want_currency=owner_want_currency,
            offer_owner_want_issuer=owner_want_issuer,
            offer_owner_want_amount=str(owner_want_amount),
        )
        return {"result": result, "tx_hash": result.get("hash")}

    try:
        result = asyncio.run(_take())
        return ok({"message": "Offer taken", **result}, 201)
    except ValueError as exc:
        return err(str(exc), 400)
    except Exception as exc:
        return err(str(exc), xrpl_error_status(exc))


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
    phone = normalize_optional(data.get("phone"))
    currency = normalize_currency(data.get("currency") or "XRP")
    issuer_input = data.get("issuer") or data.get("issuer_override")
    issuer_phone = data.get("issuer_phone")

    if phone:
        try:
            resolved = resolve_phone_to_address(phone)
            address = resolved["address"]
        except ValueError as exc:
            status = 404 if "No wallet found for this phone number" in str(exc) else 400
            return err(str(exc), status)

    if not address:
        return err("field cannot be empty", 400)

    try:
        issuer_resolution = resolve_issuer_input(currency, issuer_input, issuer_phone)
    except ValueError as exc:
        status = 404 if "No wallet found for this phone number" in str(exc) else 400
        return err(str(exc), status)

    issuer = issuer_resolution["issuer"]

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
            "issuer_phone": issuer_resolution.get("issuer_phone"),
            "issuer_username": issuer_resolution.get("issuer_username"),
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
                result["age_months"] = await estimate_account_age_months(client, address)

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
    enabled_tokens_store.init_db()
    database.seed_db_if_empty()
    port = int(os.getenv("PORT", "5050"))
    app.run(debug=True, host="127.0.0.1", port=port)
