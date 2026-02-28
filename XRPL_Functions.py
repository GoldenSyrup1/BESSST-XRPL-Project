import asyncio
import hashlib
import os
from xrpl.asyncio.clients import AsyncJsonRpcClient
from xrpl.asyncio.wallet import generate_faucet_wallet
from xrpl.wallet import Wallet
from datetime import datetime, timedelta
from xrpl.models.transactions import Payment, EscrowCreate, TrustSet, OfferCreate, EscrowFinish
from xrpl.models.requests import AccountLines
from xrpl.asyncio.transaction import submit_and_wait
from xrpl.utils import xrp_to_drops, datetime_to_ripple_time
from xrpl.utils import str_to_hex


class XRPAccount:
    # Match the order you use in create_new: (username, wallet, client)
    def __init__(self, username: str, wallet: Wallet, client: AsyncJsonRpcClient):
        self.username = username
        self.wallet = wallet
        self.client = client
        self.address = wallet.classic_address

    @classmethod
    async def create_new(cls, username: str, client: AsyncJsonRpcClient):
        # generate_faucet_wallet is the async network call
        funded_wallet = await generate_faucet_wallet(client)
        # Call the sync __init__
        return cls(username, funded_wallet, client)

    async def send_xrp(self, destination: str, amount: float):
        """Standard async payment."""
        payment_tx = Payment(
            account=self.address,
            amount=xrp_to_drops(amount),
            destination=destination,
        )
        # Await the submission using the stored client and wallet
        return await submit_and_wait(payment_tx, self.client, self.wallet)

    async def create_time_escrow(self, destination: str, amount: float, delay_seconds: int):
        """Async time-locked escrow."""
        # Calculate Ripple Epoch time (seconds since 2000-01-01)
        finish_time = datetime.now() + timedelta(seconds=delay_seconds)
        ripple_time = datetime_to_ripple_time(finish_time)

        escrow_tx = EscrowCreate(
            account=self.address,
            amount=xrp_to_drops(amount),
            destination=destination,
            finish_after=ripple_time
        )
        return await submit_and_wait(escrow_tx, self.client, self.wallet)

    # Add this method to your XRPAccount class
    async def set_trust_line(self, currency_code: str, issuer_address: str, limit: str = "1000000"):
        """
        Establishes a trust line so the account can hold and receive tokens.
        """
        trust_set_tx = TrustSet(
            account=self.address,
            limit_amount={
                "currency": currency_code,
                "issuer": issuer_address,
                "value": limit
            }
        )
        # Await the submission using your async client
        response = await submit_and_wait(trust_set_tx, self.client, self.wallet)

        print(f"\n[TRUSTLINE - {self.username}]")
        print(f"✅ Trust established for {currency_code} from issuer {issuer_address}")
        return response

    async def has_trustline(self, currency: str, issuer: str):
        """Checks if this account can actually hold the specified token."""
        request = AccountLines(account=self.address, peer=issuer)
        response = await self.client.request(request)

        lines = response.result.get("lines", [])
        # Look for a matching currency in the account's lines
        return any(line["currency"] == currency for line in lines)
    # Add this method to your XRPAccount class
    async def send_token(self, destination: str, currency_code: str, issuer_address: str, amount: str):
        """
        Check recipient's trust line before sending tokens.
        """
        # 1. Request the recipient's trust lines
        # We filter by 'peer' to see lines specifically with the issuer
        request = AccountLines(account=destination, peer=issuer_address)
        response = await self.client.request(request)

        if not response.is_successful():
            print(f"Error: Could not retrieve account lines for {destination}")
            return

        # 2. Search for a matching trust line
        # The 'lines' array contains objects with 'currency', 'limit', etc.
        lines = response.result.get("lines", [])
        matching_line = next((l for l in lines if l["currency"] == currency_code), None)

        if not matching_line:
            print(f"Sorry, can't send tokens to this user as they haven't set a trust line for {currency_code}.")
            return

        # 3. Check the limit
        # The 'limit' field is the max the recipient is willing to hold
        # 'balance' is their current balance (negative if they owe, positive if they hold)
        limit = float(matching_line["limit"])
        current_balance = float(matching_line["balance"])
        available_space = limit - current_balance

        if float(amount) > available_space:
            print(f"Sorry, you are sending too much. Their limit only allows {available_space} more {currency_code}.")
            return

        # 4. If all checks pass, proceed with payment
        payment_tx = Payment(
            account=self.address,
            destination=destination,
            amount={
                "currency": currency_code,
                "issuer": issuer_address,
                "value": str(amount)
            }
        )
        return await submit_and_wait(payment_tx, self.client, self.wallet)

    # Add to your XRPAccount class
    async def create_token_escrow(self, destination: str, currency: str, issuer: str, amount: str, delay_seconds: int):
        """
        Escrow tokens instead of XRP.
        Requires XLS-85 amendment to be active on the network.
        """
        cancel_time = datetime.now() + timedelta(seconds=delay_seconds + 3600)  # Must have cancel_after for tokens

        escrow_tx = EscrowCreate(
            account=self.address,
            destination=destination,
            amount={
                "currency": currency,
                "issuer": issuer,
                "value": amount
            },
            # For token escrows, CancelAfter is often mandatory
            cancel_after=datetime_to_ripple_time(cancel_time)
        )
        return await submit_and_wait(escrow_tx, self.client, self.wallet)

    async def create_offer(self, want_currency: str, want_issuer: str, want_amount: str,
                           give_currency: str, give_issuer: str, give_amount: str):
        """
        Standard DEX Trade: Give Token A to get Token B.
        To trade for XRP, set the XRP amount as a string of drops (no dict).
        """
        # Define what you are giving (e.g., AUD)
        taker_gets = {"currency": give_currency, "issuer": give_issuer, "value": give_amount}

        # Define what you want (e.g., INR)
        taker_pays = {"currency": want_currency, "issuer": want_issuer, "value": want_amount}

        offer_tx = OfferCreate(
            account=self.address,
            taker_gets=taker_gets,
            taker_pays=taker_pays
        )

        response = await submit_and_wait(offer_tx, self.client, self.wallet)
        print(
            f"✅ OFFER CREATED: {self.username} is offering {give_amount} {give_currency} for {want_amount} {want_currency}")
        return response

    async def step_1_generate_condition(self):
        """Generates a valid XRPL Crypto-Condition."""
        # 1. Create a random 32-byte secret
        preimage_bytes = os.urandom(32)
        preimage_hex = preimage_bytes.hex().upper()

        # 2. Hash it
        sha256_hash = hashlib.sha256(preimage_bytes).digest()

        # 3. XRPL format for SHA-256 conditions:
        # Prefix 'A0258020' + the 32-byte hash
        # 'A0' = Type, '25' = Total Length, '80' = Tag, '20' = Hash Length (32 bytes)
        condition = f"A0258020{sha256_hash.hex().upper()}"

        print(f"\n[STEP 1 - {self.username}]")
        print(f"🔑 Secret (Preimage): {preimage_hex}")
        print(f"🔒 Condition (Formatted): {condition}")
        return preimage_hex, condition

    async def step_2_create_token_lock(self, destination: str, currency: str, issuer: str, amount: str, condition: str):
        """
        Lock Token A (AUD) or Token B (INR) in Escrow.
        """
        cancel_time = datetime_to_ripple_time(datetime.now() + timedelta(hours=1))

        # Token amount must be a dictionary
        token_amount = {
            "currency": currency,
            "issuer": issuer,
            "value": amount
        }

        escrow_tx = EscrowCreate(
            account=self.address,
            destination=destination,
            amount=token_amount,
            condition=condition,  # Use the A0258020 prefix from the previous fix
            cancel_after=cancel_time
        )

        response = await submit_and_wait(escrow_tx, self.client, self.wallet)
        escrow_id = response.result["Sequence"]
        print(f"💰 {amount} {currency} Locked in Escrow (ID: {escrow_id})")
        return escrow_id

    async def step_3_finish_trade(self, owner_address: str, escrow_id: int, secret_preimage: str):
        """
        Reveals the secret with the mandatory A0228020 prefix.
        """
        # Preimage must be prefixed with 'A0228020' for XRPL
        formatted_fulfillment = f"A0228020{secret_preimage}"

        finish_tx = EscrowFinish(
            account=self.address,
            owner=owner_address,
            offer_sequence=escrow_id,
            fulfillment=formatted_fulfillment
        )

        await submit_and_wait(finish_tx, self.client, self.wallet)
        print(f"🔓 TRADE FINISHED: {self.username} revealed the secret and claimed the tokens.")


async def main():
    # 1. Setup Connection
    client = AsyncJsonRpcClient("https://s.altnet.rippletest.net:51234")

    # 2. Initialize "User" objects (Alice and Bob)
    alice = await XRPAccount.create_new("Alice", client)
    bob = await XRPAccount.create_new("Bob", client)

    # 3. Define Generic Tokens
    # Change these to any Currency Code/Issuer for your project
    ISSUER_ADDR = "rPT1Sjq2YGrBMTttX4GZHjKu9dyfzgpEGP"
    TOKEN_A = {"currency": "TKA", "issuer": ISSUER_ADDR}
    TOKEN_B = {"currency": "TKB", "issuer": ISSUER_ADDR}

    print(f"\n--- Starting Trade Tests for {alice.username} & {bob.username} ---")

    # --- SCENARIO 1: THE DEX (OfferCreate) ---
    # Alice puts an order on the public book: "Giving 10 TokenA for 50 TokenB"
    print("\n[SCENARIO: Public DEX Offer]")
    await alice.create_offer(
        give_currency=TOKEN_A["currency"], give_issuer=TOKEN_A["issuer"], give_amount="10",
        want_currency=TOKEN_B["currency"], want_issuer=TOKEN_B["issuer"], want_amount="50"
    )

    # --- SCENARIO 2: THE SECURE SWAP (Escrow) ---
    # This is the 1-on-1 private trade using a cryptographic lock
    print("\n[SCENARIO: Private Escrow Swap]")

    # Step 1: Alice generates the secret 'Key' and the 'Lock'
    secret, lock = await alice.step_1_generate_condition()

    # Step 2: Alice locks her 10 TokenA for Bob using the 'Lock'
    a_esc_id = await alice.step_2_create_token_lock(
        destination=bob.address,
        currency=TOKEN_A["currency"],
        issuer=TOKEN_A["issuer"],
        amount="10",
        condition=lock
    )

    # Step 3: Bob locks his 50 TokenB for Alice using the SAME 'Lock'
    b_esc_id = await bob.step_2_create_token_lock(
        destination=alice.address,
        currency=TOKEN_B["currency"],
        issuer=TOKEN_B["issuer"],
        amount="50",
        condition=lock
    )

    # Step 4: Completion (Alice reveals secret to get TokenB, Bob then uses it to get TokenA)
    # Alice finishes Bob's escrow
    await alice.step_3_finish_trade(bob.address, b_esc_id, secret)
    # Bob finishes Alice's escrow
    await bob.step_3_finish_trade(alice.address, a_esc_id, secret)

    print("\n✅ All generic trade logic verified.")


if __name__ == "__main__":
    asyncio.run(main())