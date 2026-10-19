"""Binance kline websocket streaming (Phase 0: log-only, no signal logic yet)."""

from collections.abc import Awaitable, Callable

from binance import AsyncClient, BinanceSocketManager

from tradingbot.logging_setup import get_logger

log = get_logger(__name__)

OnCandleClose = Callable[[dict], Awaitable[None]]


async def stream_klines(
    client: AsyncClient,
    market: str,
    symbol: str,
    interval: str = "1m",
    on_close: OnCandleClose | None = None,
) -> None:
    """Runs until cancelled. Logs every candle close; optionally invokes on_close(candle)."""
    bsm = BinanceSocketManager(client)
    socket = bsm.kline_futures_socket(symbol, interval=interval) if market == "futures" else bsm.kline_socket(symbol, interval=interval)

    async with socket as stream:
        log.info("kline_stream_connected", market=market, symbol=symbol, interval=interval)
        while True:
            msg = await stream.recv()
            k = msg.get("k", {})
            if not k.get("x"):
                continue  # candle not yet closed
            candle = {
                "market": market,
                "symbol": k.get("s", symbol),
                "interval": k.get("i", interval),
                "open_time": k.get("t"),
                "close_time": k.get("T"),
                "open": k.get("o"),
                "high": k.get("h"),
                "low": k.get("l"),
                "close": k.get("c"),
                "volume": k.get("v"),
            }
            log.info("candle_closed", **candle)
            if on_close is not None:
                await on_close(candle)
