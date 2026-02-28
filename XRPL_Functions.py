import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, Tuple
from xrpl.asyncio.clients import AsyncJsonRpcClient
from xrpl.asyncio.wallet import generate_faucet_wallet
from xrpl.wallet import Wallet
from xrpl.models.requests import AccountInfo, AccountLines, ServerState
from xrpl.models.transactions import Payment, TrustSet, OfferCreate, EscrowCreate, EscrowFinish
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
            limit_amount={
                "currency": currency,
                "issuer": issuer,
                "value": limit,
            },
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
            amount={"currency": currency, "issuer": issuer, "value": str(amount)},
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
        # Check you can RECEIVE what you want (trustline + space)
        remaining = await self.trustline_remaining_space(self.address, want_currency, want_issuer)
        if remaining is None:
            raise ValueError(f"{self.username} has NO trustline for wanted token {want_currency}.{want_issuer}")
        if float(want_amount) > remaining:
            raise ValueError(f"{self.username} cannot receive {want_amount}; remaining space is {remaining} {want_currency}")

        taker_gets = {"currency": give_currency, "issuer": give_issuer, "value": str(give_amount)}
        taker_pays = {"currency": want_currency, "issuer": want_issuer, "value": str(want_amount)}

        tx = OfferCreate(
            account=self.address,
            taker_gets=taker_gets,
            taker_pays=taker_pays,
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
            amount={"currency": currency, "issuer": issuer, "value": str(amount)},
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
            taker_gets={"currency": offer_owner_want_currency, "issuer": offer_owner_want_issuer,
                        "value": str(offer_owner_want_amount)},
            taker_pays={"currency": offer_owner_give_currency, "issuer": offer_owner_give_issuer,
                        "value": str(offer_owner_give_amount)},
        )
        resp = await submit_and_wait(tx, self.client, self.wallet)
        return resp.result

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

    alice = await XRPAccount.create_new("Alice", client)
    bob = await XRPAccount.create_new("Bob", client)
    issuer = await XRPAccount.create_new("Issuer", client)  # we control issuer in this demo

    print("Alice:", alice.address)
    print("Bob:  ", bob.address)
    print("Iss:  ", issuer.address)

    # Define tokens issued by issuer
    TOKEN_A = {"currency": "TKA", "issuer": issuer.address}
    TOKEN_B = {"currency": "TKB", "issuer": issuer.address}

    # 1) Trustlines (limits)
    print("\n[1] Setting trustlines...")
    await alice.set_trust_line(TOKEN_A["currency"], TOKEN_A["issuer"], limit="1000")
    await alice.set_trust_line(TOKEN_B["currency"], TOKEN_B["issuer"], limit="1000")
    await bob.set_trust_line(TOKEN_A["currency"], TOKEN_A["issuer"], limit="1000")
    await bob.set_trust_line(TOKEN_B["currency"], TOKEN_B["issuer"], limit="1000")

    # 2) Issue tokens to Alice and Bob (issuer sends IOUs)
    # NOTE: Recipient must have trustline, or it will fail.
    print("\n[2] Issuing tokens...")
    await issuer.send_token_checked(alice.address, TOKEN_A["currency"], TOKEN_A["issuer"], "100")
    await issuer.send_token_checked(bob.address, TOKEN_B["currency"], TOKEN_B["issuer"], "500")

    # 3) Instant XRP
    print("\n[3] Instant XRP payment Alice -> Bob...")
    res_xrp = await alice.send_xrp(bob.address, 1.0)
    print("result:", res_xrp["meta"]["TransactionResult"])
    print("\n--- Balances before trade ---")
    print("Alice TKA:", await alice.get_token_balance("TKA", issuer.address))
    print("Alice TKB:", await alice.get_token_balance("TKB", issuer.address))
    print("Bob   TKA:", await bob.get_token_balance("TKA", issuer.address))
    print("Bob   TKB:", await bob.get_token_balance("TKB", issuer.address))

    # 4) Timed XRP escrow (release in 45s)
    print("\n[4] Timed XRP escrow Alice -> Bob (release in 45s)...")
    release_time = now_utc() + timedelta(seconds=45)
    cancel_time = release_time + timedelta(hours=1)
    esc = await alice.create_time_escrow_xrp(bob.address, 2.0, release_time_utc=release_time, cancel_after_utc=cancel_time)
    print("escrow_sequence:", esc["escrow_sequence"])
    print("result:", esc["tx_result"]["meta"]["TransactionResult"])


    # Wait for unlock time (+2s buffer)
    wait_s = 47
    print(f"\nWaiting {wait_s}s...")
    await asyncio.sleep(wait_s)

    print("\n[4c] Finish escrow after unlock (should succeed)...")
    finish = await bob.finish_escrow(owner_address=alice.address, escrow_sequence=esc["escrow_sequence"])
    print("result:", finish["meta"]["TransactionResult"])
    # 5) DEX Offer: Alice offers 10 TKA for 50 TKB
    print("\n[5] DEX offer: Alice offers 10 TKA for 50 TKB...")
    offer = await alice.create_offer_checked(
        give_currency=TOKEN_A["currency"], give_issuer=TOKEN_A["issuer"], give_amount="10",
        want_currency=TOKEN_B["currency"], want_issuer=TOKEN_B["issuer"], want_amount="50",
    )
    print("result:", offer["meta"]["TransactionResult"])
    print("\n[5b] Bob takes Alice's offer (trade executes)...")
    take = await bob.take_offer_exact(
        offer_owner_give_currency=TOKEN_A["currency"],
        offer_owner_give_issuer=TOKEN_A["issuer"],
        offer_owner_give_amount="10",
        offer_owner_want_currency=TOKEN_B["currency"],
        offer_owner_want_issuer=TOKEN_B["issuer"],
        offer_owner_want_amount="50",
    )
    print("\n--- Balances after trade ---")
    print("Alice TKA:", await alice.get_token_balance("TKA", issuer.address))
    print("Alice TKB:", await alice.get_token_balance("TKB", issuer.address))
    print("Bob   TKA:", await bob.get_token_balance("TKA", issuer.address))
    print("Bob   TKB:", await bob.get_token_balance("TKB", issuer.address))
    print("result:", take["meta"]["TransactionResult"])
    # 6) Private swap via token escrow (if enabled)
    print("\n[6] Private swap via token escrow (only if TokenEscrow enabled)...")

if __name__ == "__main__":
    asyncio.run(main())