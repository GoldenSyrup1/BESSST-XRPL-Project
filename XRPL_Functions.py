import asyncio
from xrpl.asyncio.clients import AsyncJsonRpcClient
from xrpl.asyncio.wallet import generate_faucet_wallet
from xrpl.wallet import Wallet
from datetime import datetime, timedelta
from xrpl.models.transactions import Payment, EscrowCreate, TrustSet, OfferCreate
from xrpl.models.requests import AccountLines
from xrpl.asyncio.transaction import submit_and_wait
from xrpl.utils import xrp_to_drops, datetime_to_ripple_time


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
    async def set_trust_line(self, currency_code: str, issuer_address: str, limit: str):
        """
        Establish a trust line so this account can hold a specific token.
        limit: The max amount you are willing to hold (e.g., "1000000")
        """
        trust_set_tx = TrustSet(
            account=self.address,
            limit_amount={
                "currency": currency_code,
                "issuer": issuer_address,
                "value": limit
            }
        )
        # Await the submission
        return await submit_and_wait(trust_set_tx, self.client, self.wallet)

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

    async def place_limit_order(self, get_amount: dict, pay_amount: dict):
        """
        Standard DEX trade.
        get_amount: What you want to receive (TakerPays)
        pay_amount: What you are offering (TakerGets)
        """
        offer_tx = OfferCreate(
            account=self.address,
            taker_gets=pay_amount,  # e.g., {"currency": "USD", "issuer": "...", "value": "10"}
            taker_pays=get_amount  # e.g., xrp_to_drops(50)
        )
        return await submit_and_wait(offer_tx, self.client, self.wallet)

async def main():
    # DON'T use 'async with' here
    client = AsyncJsonRpcClient("https://s.altnet.rippletest.net:51234")

    # Create Alice
    user = await XRPAccount.create_new("Alice", client)

    print(f"User: {user.username}")
    print(f"Wallet Address: {user.address}")
    print(f"Ready for Escrow: Yes")


if __name__ == "__main__":
    asyncio.run(main())