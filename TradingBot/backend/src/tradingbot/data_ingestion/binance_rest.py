"""Binance REST connectivity: client construction and account balance checks.

Spot and USDT-M Futures are separate wallets/APIs on Binance, and Spot Testnet /
Futures Testnet are two separate testnets with separate keys. Every call site
must be explicit about which (market, network) pair it's talking to - there is
no shared/implicit "the client".
"""

from binance import AsyncClient

from tradingbot.config.settings import Network, Secrets

Market = str  # "spot" | "futures"


async def create_client(market: Market, network: Network, secrets: Secrets) -> AsyncClient:
    api_key, api_secret = secrets.credentials_for(market, network)
    return await AsyncClient.create(
        api_key=api_key,
        api_secret=api_secret,
        testnet=(network == Network.TESTNET),
    )


async def fetch_spot_balance_summary(client: AsyncClient) -> list[dict]:
    """Non-zero spot balances, e.g. [{"asset": "USDT", "free": "10000.0", "locked": "0.0"}]."""
    account = await client.get_account()
    return [b for b in account["balances"] if float(b["free"]) or float(b["locked"])]


async def fetch_futures_balance_summary(client: AsyncClient) -> list[dict]:
    """Non-zero USDT-M futures wallet balances."""
    balances = await client.futures_account_balance()
    return [b for b in balances if float(b["balance"])]
