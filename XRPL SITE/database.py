import sqlite3
import os
from werkzeug.security import generate_password_hash, check_password_hash
import asyncio
from XRPL_Functions import XRPAccount
from xrpl.asyncio.clients import AsyncJsonRpcClient

DB_FILE = "xrpl_app.db"
XRPL_CLIENT_URL = "https://s.altnet.rippletest.net:51234"

def get_db_connection():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    c = conn.cursor()
    
    # Create Users table
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            phone TEXT UNIQUE
        )
    ''')
    
    # Create Wallets table (1-to-1 with user for simplicity)
    c.execute('''
        CREATE TABLE IF NOT EXISTS wallets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            address TEXT NOT NULL,
            seed TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    ''')
    conn.commit()
    conn.close()

def get_user_by_username(username: str):
    conn = get_db_connection()
    user = conn.execute('SELECT * FROM users WHERE LOWER(username) = LOWER(?)', (username,)).fetchone()
    conn.close()
    return user

def get_user_by_phone(phone: str):
    conn = get_db_connection()
    user = conn.execute('SELECT * FROM users WHERE phone = ?', (phone,)).fetchone()
    conn.close()
    return user

def get_wallet_by_user_id(user_id: int):
    conn = get_db_connection()
    wallet = conn.execute('SELECT * FROM wallets WHERE user_id = ?', (user_id,)).fetchone()
    conn.close()
    return wallet

def add_user_and_wallet(username: str, password_hash: str, address: str, seed: str, phone: str = None):
    conn = get_db_connection()
    c = conn.cursor()
    try:
        username = username.lower()
        c.execute('INSERT INTO users (username, password, phone) VALUES (?, ?, ?)', (username, password_hash, phone))
        user_id = c.lastrowid
        c.execute('INSERT INTO wallets (user_id, address, seed) VALUES (?, ?, ?)', (user_id, address, seed))
        conn.commit()
    except sqlite3.IntegrityError:
        conn.rollback()
        raise ValueError("Username already exists")
    finally:
        conn.close()
    return user_id

async def _seed_testnet_users():
    """Create 2 testnet users and perform an initial transaction between them to populate history."""
    conn = get_db_connection()
    user_count = conn.execute('SELECT COUNT(*) FROM users').fetchone()[0]
    conn.close()
    
    if user_count == 0:
        print("Seeding database with Testnet users... (This will take a moment)")
        client = AsyncJsonRpcClient(XRPL_CLIENT_URL)
        
        # User 1: Alice
        alice_password = generate_password_hash("password123")
        alice_account = await XRPAccount.create_new("alice", client)
        add_user_and_wallet("alice", alice_password, alice_account.address, alice_account.wallet.seed, "555-0101")
        print(f"Created user 'alice' with address: {alice_account.address}")
        
        # User 2: Bob
        bob_password = generate_password_hash("password123")
        bob_account = await XRPAccount.create_new("bob", client)
        add_user_and_wallet("bob", bob_password, bob_account.address, bob_account.wallet.seed, "555-0202")
        print(f"Created user 'bob' with address: {bob_account.address}")

        # User 3: Scammer (High Risk)
        scammer_password = generate_password_hash("password123")
        scammer_account = await XRPAccount.create_new("scammer", client)
        add_user_and_wallet("scammer", scammer_password, scammer_account.address, scammer_account.wallet.seed, "555-6666")
        print(f"Created user 'scammer' with address: {scammer_account.address}")

        # User 4: Shady (Medium Risk)
        shady_password = generate_password_hash("password123")
        shady_account = await XRPAccount.create_new("shady", client)
        add_user_and_wallet("shady", shady_password, shady_account.address, shady_account.wallet.seed, "555-9999")
        print(f"Created user 'shady' with address: {shady_account.address}")
        
        # Perform a transaction Alice -> Bob so they have history
        print("Sending 50 XRP from alice to bob to generate history...")
        await alice_account.send_xrp(bob_account.address, 50.0)
        print("Database seeding complete!")

def seed_db_if_empty():
    asyncio.run(_seed_testnet_users())

if __name__ == '__main__':
    init_db()
    seed_db_if_empty()
