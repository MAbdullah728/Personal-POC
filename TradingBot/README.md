# AI Crypto Trading Bot (Binance Spot + Futures)

Architecture and phased build plan: see `docs/PLAN.md`.

## Status

**Phase 0 — scaffolding + testnet connectivity + config + logging.** No trading logic yet.

## Backend setup

```
cd backend
python -m venv .venv
.venv\Scripts\activate        # Windows
pip install -e ".[dev]"
copy .env.example .env        # then fill in your Binance testnet keys
python -m tradingbot.main
```

Requires Binance **Spot Testnet** (`https://testnet.binance.vision`) and **Futures Testnet**
(`https://testnet.binancefuture.com`) API keys — these are separate testnets with separate
keys. Generate them from Binance's respective testnet portals and place them in `.env`
(never commit real or testnet keys).
