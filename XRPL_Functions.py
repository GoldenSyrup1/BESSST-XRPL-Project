import asyncio
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, Tuple, List
from xrpl.asyncio.clients import AsyncJsonRpcClient
from xrpl.asyncio.wallet import generate_faucet_wallet
from xrpl.wallet import Wallet
from xrpl.models.requests import AccountInfo, AccountLines, AccountOffers, AccountTx, ServerState
from xrpl.models.transactions import Payment, TrustSet, OfferCreate, OfferCancel, EscrowCreate, EscrowFinish
from xrpl.models.amounts import IssuedCurrencyAmount
from xrpl.asyncio.transaction import submit_and_wait
from xrpl.utils import xrp_to_drops, datetime_to_ripple_time
from cryptoconditions import PreimageSha256
import os


# -------------------------
# Time helpers (always UTC)
# -------------------------

def now_utc() -> datetime:
    return datetime.now(timezone.utc)

def to_ripple_time(dt: datetime) -> int:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return datetime_to_ripple_time(dt.astimezone(timezone.utc))

def make_condition_and_fulfillment() -> Tuple[str, str]:
    """
    Proper XRPL condition & fulfillment using PreimageSha256.
    """
    preimage = os.urandom(32)
    cc = PreimageSha256(preimage)
    condition_hex = cc.condition_binary.hex().upper()
    fulfillment_hex = cc.serialize_binary().hex().upper()
    return condition_hex, fulfillment_hex


# -------------------------
# XRPL Account (Async)
# -------------------------

@dataclass
class XRPAccount:
    username: str
    wallet: Wallet
    client: AsyncJsonRpcClient

    @property
    def address(self) -> str:
        return self.wallet.classic_address

    @classmethod
    async def create_new(cls, username: str, client: AsyncJsonRpcClient) -> "XRPAccount":
        funded_wallet = await generate_faucet_wallet(client)
        return cls(username=username, wallet=funded_wallet, client=client)

    @staticmethod
    def _issued_currency_amount(currency: str, issuer: str, value: str) -> IssuedCurrencyAmount:
        if not str(issuer or "").strip():
            raise ValueError(f"Issuer is required for non-XRP currency {currency}")
        return IssuedCurrencyAmount(
            currency=str(currency).upper(),
            issuer=str(issuer).strip(),
            value=str(value),
        )

    @staticmethod
    def _offer_amount(currency: str, issuer: str, value: str):
        if str(currency).upper() == "XRP":
            try:
                return xrp_to_drops(Decimal(str(value)))
            except (InvalidOperation, TypeError, ValueError) as exc:
                raise ValueError(f"Invalid XRP amount: {value}") from exc
        return XRPAccount._issued_currency_amount(currency, issuer, value)

    @staticmethod
    def _ioc_flag() -> int:
        # Immediate-or-Cancel: submit and consume available liquidity now; do not wait on book.
        return 0x00020000

    # ---------- Basic info ----------
    async def get_xrp_balance(self) -> float:
        resp = await self.client.request(AccountInfo(account=self.address, ledger_index="validated"))
        drops = int(resp.result["account_data"]["Balance"])
        return drops / 1_000_000

    async def _get_trustline_line(self, account_address: str, currency: str, issuer: str) -> Optional[Dict[str, Any]]:
        """
        Reads trustline data from `account_address` with peer=issuer, returns the matching line if exists.
        """
        req = AccountLines(account=account_address, peer=issuer)
        resp = await self.client.request(req)
        lines = resp.result.get("lines", [])
        for line in lines:
            if line.get("currency") == currency and line.get("account") == issuer:
                return line
        # Some servers return lines without "account" matching logic exactly; currency+peer is usually enough:
        for line in lines:
            if line.get("currency") == currency:
                return line
        return None

    async def has_trustline(self, account_address: str, currency: str, issuer: str) -> bool:
        return (await self._get_trustline_line(account_address, currency, issuer)) is not None

    async def trustline_remaining_space(self, account_address: str, currency: str, issuer: str) -> Optional[float]:
        """
        Returns how much more of this token the account can receive (limit - balance).
        If no trustline exists, returns None.
        """
        line = await self._get_trustline_line(account_address, currency, issuer)
        if not line:
            return None

        limit = float(line.get("limit", "0"))
        balance = float(line.get("balance", "0"))
        # For typical holders, balance is >= 0. Remaining receiving capacity:
        return limit - balance

    # ---------- XRP: instant ----------
    async def send_xrp(self, destination: str, amount_xrp: float) -> Dict[str, Any]:
        tx = Payment(
            account=self.address,
            destination=destination,
            amount=xrp_to_drops(amount_xrp),
        )
        resp = await submit_and_wait(tx, self.client, self.wallet)
        return resp.result

    # ---------- XRP: timed escrow ----------
    async def create_time_escrow_xrp(
        self,
        destination: str,
        amount_xrp: float,
        release_time_utc: datetime,
        cancel_after_utc: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """
        Creates an XRP escrow. Destination can finish AFTER release_time_utc.
        Returns escrow_sequence (the EscrowCreate tx Sequence).
        """
        tx = EscrowCreate(
            account=self.address,
            destination=destination,
            amount=xrp_to_drops(amount_xrp),
            finish_after=to_ripple_time(release_time_utc),
            cancel_after=to_ripple_time(cancel_after_utc) if cancel_after_utc else None,
        )
        resp = await submit_and_wait(tx, self.client, self.wallet)
        escrow_sequence = resp.result.get("tx_json", {}).get("Sequence")
        if escrow_sequence is None:
            raise RuntimeError(f"Could not read escrow sequence from: {resp.result}")
        return {"escrow_sequence": int(escrow_sequence), "tx_result": resp.result}

    async def finish_escrow(self, owner_address: str, escrow_sequence: int, fulfillment_hex: Optional[str] = None) -> Dict[str, Any]:
        """
        Finish an escrow. If it has a Condition, you must pass fulfillment_hex.
        """
        tx = EscrowFinish(
            account=self.address,   # the finisher
            owner=owner_address,    # escrow creator
            offer_sequence=int(escrow_sequence),
            fulfillment=fulfillment_hex,
        )
        resp = await submit_and_wait(tx, self.client, self.wallet)
        return resp.result

    # ---------- Trustlines ----------
    async def set_trust_line(self, currency: str, issuer: str, limit: str = "1000000") -> Dict[str, Any]:
        tx = TrustSet(
            account=self.address,
            limit_amount=self._issued_currency_amount(currency, issuer, limit),
        )
        resp = await submit_and_wait(tx, self.client, self.wallet)
        return resp.result

    # ---------- Tokens (IOUs): safe send ----------
    async def send_token_checked(self, destination: str, currency: str, issuer: str, amount: str) -> Dict[str, Any]:
        """
        Sends token only if the destination has a trustline AND has enough remaining space.
        """
        remaining = await self.trustline_remaining_space(destination, currency, issuer)
        if remaining is None:
            raise ValueError(f"Destination has NO trustline for {currency}.{issuer}")
        if float(amount) > remaining:
            raise ValueError(f"Destination trustline limit too small. Remaining space: {remaining} {currency}")

        tx = Payment(
            account=self.address,
            destination=destination,
            amount=self._issued_currency_amount(currency, issuer, str(amount)),
        )
        resp = await submit_and_wait(tx, self.client, self.wallet)
        return resp.result

    # ---------- DEX OfferCreate: checked ----------
    async def create_offer_checked(
        self,
        give_currency: str,
        give_issuer: str,
        give_amount: str,
        want_currency: str,
        want_issuer: str,
        want_amount: str,
    ) -> Dict[str, Any]:
        """
        Creates a public DEX offer: "I give give_amount give_currency to receive want_amount want_currency".
        Safety checks:
          - Must have trustline to RECEIVE want token (and space)
          - If giving token, it's your responsibility to have balance; XRPL will enforce funding anyway
        """
        give_currency = str(give_currency or "").upper()
        want_currency = str(want_currency or "").upper()

        # Check you can FUND what you are offering.
        if give_currency == "XRP":
            xrp_balance = await self.get_xrp_balance()
            if float(give_amount) > xrp_balance:
                raise ValueError(
                    f"{self.username} has insufficient XRP balance to offer {give_amount} XRP "
                    f"(balance: {xrp_balance} XRP)"
                )
        else:
            give_line = await self._get_trustline_line(self.address, give_currency, give_issuer)
            if give_line is None:
                raise ValueError(f"{self.username} has NO trustline for offered token {give_currency}.{give_issuer}")
            give_balance = float(give_line.get("balance", "0"))
            if float(give_amount) > give_balance:
                raise ValueError(
                    f"{self.username} has insufficient {give_currency} balance to offer {give_amount} "
                    f"(balance: {give_balance})"
                )

        # Check you can RECEIVE what you want (trustline + space) for non-XRP.
        if want_currency != "XRP":
            remaining = await self.trustline_remaining_space(self.address, want_currency, want_issuer)
            if remaining is None:
                raise ValueError(f"{self.username} has NO trustline for wanted token {want_currency}.{want_issuer}")
            if float(want_amount) > remaining:
                raise ValueError(f"{self.username} cannot receive {want_amount}; remaining space is {remaining} {want_currency}")

        taker_gets = self._offer_amount(give_currency, give_issuer, str(give_amount))
        taker_pays = self._offer_amount(want_currency, want_issuer, str(want_amount))

        tx = OfferCreate(
            account=self.address,
            taker_gets=taker_gets,
            taker_pays=taker_pays,
            flags=self._ioc_flag(),
        )
        resp = await submit_and_wait(tx, self.client, self.wallet)
        return resp.result

    async def cancel_offer(self, offer_sequence: int) -> Dict[str, Any]:
        tx = OfferCancel(
            account=self.address,
            offer_sequence=int(offer_sequence),
        )
        resp = await submit_and_wait(tx, self.client, self.wallet)
        return resp.result

    # ---------- Token Escrow support check ----------
    async def token_escrow_enabled(self) -> bool:
        """
        Token escrow (IOU escrow) requires TokenEscrow (XLS-85) amendment.
        Not always enabled on every network/server.
        """
        resp = await self.client.request(ServerState())
        amendments = resp.result.get("state", {}).get("validated_ledger", {}).get("amendments", [])

        # TokenEscrow amendment ID (XLS-85). If your server returns only IDs, this works.
        # If your server returns names differently, you may need to print amendments to confirm.
        TOKEN_ESCROW_ID = "138B968F25822EFBF54C00F97031221C47B1EAB8321D93C7C2AEAF85F04EC5DF"
        return TOKEN_ESCROW_ID in amendments

    # ---------- Private swap using Token Escrow (ONLY if supported) ----------
    async def create_conditional_token_escrow(
        self,
        destination: str,
        currency: str,
        issuer: str,
        amount: str,
        condition_hex: str,
        cancel_after_utc: datetime,
    ) -> Dict[str, Any]:
        """
        Locks IOU tokens in escrow with a crypto-condition (requires TokenEscrow enabled).
        """
        if not await self.token_escrow_enabled():
            raise RuntimeError("TokenEscrow (XLS-85) is NOT enabled on this server/network. Use DEX offers instead.")

        # Also ensure destination trustline exists & has space (so finish will succeed)
        remaining = await self.trustline_remaining_space(destination, currency, issuer)
        if remaining is None:
            raise ValueError(f"Destination has NO trustline for {currency}.{issuer}")
        if float(amount) > remaining:
            raise ValueError(f"Destination trustline cannot receive {amount}; remaining is {remaining} {currency}")

        tx = EscrowCreate(
            account=self.address,
            destination=destination,
            amount=self._issued_currency_amount(currency, issuer, str(amount)),
            condition=condition_hex,
            cancel_after=to_ripple_time(cancel_after_utc),
        )
        resp = await submit_and_wait(tx, self.client, self.wallet)
        escrow_sequence = resp.result.get("tx_json", {}).get("Sequence")
        if escrow_sequence is None:
            raise RuntimeError(f"Could not read escrow sequence from: {resp.result}")
        return {"escrow_sequence": int(escrow_sequence), "tx_result": resp.result}

    async def take_offer_exact(
            self,
            offer_owner_give_currency: str,
            offer_owner_give_issuer: str,
            offer_owner_give_amount: str,
            offer_owner_want_currency: str,
            offer_owner_want_issuer: str,
            offer_owner_want_amount: str,
    ) -> Dict[str, Any]:
        """
        Accept an existing offer by crossing it:
        If Alice offered: give 10 TKA for 50 TKB,
        then Bob submits: give 50 TKB for 10 TKA.
        """
        tx = OfferCreate(
            account=self.address,
            taker_gets=self._offer_amount(
                offer_owner_want_currency,
                offer_owner_want_issuer,
                str(offer_owner_want_amount),
            ),
            taker_pays=self._offer_amount(
                offer_owner_give_currency,
                offer_owner_give_issuer,
                str(offer_owner_give_amount),
            ),
        )
        resp = await submit_and_wait(tx, self.client, self.wallet)
        return resp.result

    # ---------- History ----------
    async def get_transaction_history(self, limit: int = 20) -> List[Dict[str, Any]]:
        req = AccountTx(account=self.address, limit=limit, forward=False)
        resp = await self.client.request(req)
        if resp.is_successful() and "transactions" in resp.result:
            return resp.result["transactions"]
        return []

    async def get_open_offers(self) -> List[Dict[str, Any]]:
        req = AccountOffers(account=self.address, ledger_index="validated")
        resp = await self.client.request(req)
        if resp.is_successful():
            return resp.result.get("offers", [])
        return []

    async def get_trustlines(self) -> List[Dict[str, Any]]:
        req = AccountLines(account=self.address, ledger_index="validated")
        resp = await self.client.request(req)
        if resp.is_successful():
            return resp.result.get("lines", [])
        return []

    async def get_offer_status(self, offer_sequence: int) -> Dict[str, Any]:
        offer_sequence = int(offer_sequence)
        offers = await self.get_open_offers()
        for offer in offers:
            seq = int(offer.get("seq", 0))
            if seq != offer_sequence:
                continue

            taker_gets = offer.get("taker_gets")
            taker_pays = offer.get("taker_pays")
            funded_gets = offer.get("taker_gets_funded")
            funded_pays = offer.get("taker_pays_funded")
            partially_filled = (
                funded_gets is not None and str(funded_gets) != str(taker_gets)
            ) or (
                funded_pays is not None and str(funded_pays) != str(taker_pays)
            )

            return {
                "status": "partially_filled" if partially_filled else "open",
                "offer_sequence": offer_sequence,
                "tx_hash": None,
                "filled_amounts": {
                    "taker_gets_funded": funded_gets,
                    "taker_pays_funded": funded_pays,
                },
                "remaining_amounts": {
                    "taker_gets": taker_gets,
                    "taker_pays": taker_pays,
                },
                "last_ledger": offer.get("ledger_current_index"),
            }

        txs = await self.get_transaction_history(limit=200)
        create_tx = None
        create_meta = None
        cancel_tx = None

        for entry in txs:
            tx = entry.get("tx", {})
            meta = entry.get("meta", {})
            tx_type = tx.get("TransactionType")

            if tx_type == "OfferCancel" and int(tx.get("OfferSequence", -1)) == offer_sequence:
                if meta.get("TransactionResult") == "tesSUCCESS":
                    cancel_tx = tx
                    break

            if tx_type == "OfferCreate" and int(tx.get("Sequence", -1)) == offer_sequence:
                create_tx = tx
                create_meta = meta

        if cancel_tx is not None:
            return {
                "status": "cancelled",
                "offer_sequence": offer_sequence,
                "tx_hash": cancel_tx.get("hash"),
                "filled_amounts": None,
                "remaining_amounts": None,
                "last_ledger": cancel_tx.get("ledger_index"),
            }

        if create_tx is None:
            return {
                "status": "failed",
                "offer_sequence": offer_sequence,
                "tx_hash": None,
                "filled_amounts": None,
                "remaining_amounts": None,
                "last_ledger": None,
            }

        if create_meta and create_meta.get("TransactionResult") != "tesSUCCESS":
            return {
                "status": "failed",
                "offer_sequence": offer_sequence,
                "tx_hash": create_tx.get("hash"),
                "filled_amounts": None,
                "remaining_amounts": None,
                "last_ledger": create_tx.get("ledger_index"),
            }

        return {
            "status": "filled",
            "offer_sequence": offer_sequence,
            "tx_hash": create_tx.get("hash"),
            "filled_amounts": None,
            "remaining_amounts": None,
            "last_ledger": create_tx.get("ledger_index"),
        }

    async def get_token_balance(self, currency: str, issuer: str) -> float:
        """
        Returns how much of a token THIS account holds.
        If no trustline exists, returns 0.0
        """
        request = AccountLines(account=self.address, peer=issuer)
        response = await self.client.request(request)

        lines = response.result.get("lines", [])

        for line in lines:
            if line.get("currency") == currency:
                return float(line.get("balance", "0"))

        return 0.0




# -------------------------
# Demo / Tests in main()
# -------------------------

async def main():
    client = AsyncJsonRpcClient("https://s.altnet.rippletest.net:51234")
