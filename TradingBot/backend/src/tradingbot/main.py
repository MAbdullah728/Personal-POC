"""Phase 0 entrypoint: connect to Spot Testnet + Futures Testnet, log balances,
stream candle closes for the watched symbols. No trading logic - connectivity only.
"""

import asyncio
import pathlib
import signal

from tradingbot.config.settings import Network, Secrets, load_config
from tradingbot.data_ingestion.binance_rest import (
    create_client,
    fetch_futures_balance_summary,
    fetch_spot_balance_summary,
)
from tradingbot.data_ingestion.binance_ws import stream_klines
from tradingbot.logging_setup import configure_logging, get_logger

CONFIG_PATH = pathlib.Path(__file__).resolve().parents[3] / "configs" / "settings.yaml"


async def run() -> None:
    config = load_config(CONFIG_PATH)
    configure_logging(config.log_dir, config.log_level)
    log = get_logger("main")
    log.info("phase0_starting", symbols=config.symbols)

    secrets = Secrets()

    spot_client = await create_client("spot", config.spot.network, secrets)
    futures_client = await create_client("futures", config.futures.network, secrets)

    try:
        spot_balances = await fetch_spot_balance_summary(spot_client)
        log.info("spot_balance", network=config.spot.network.value, balances=spot_balances)

        futures_balances = await fetch_futures_balance_summary(futures_client)
        log.info("futures_balance", network=config.futures.network.value, balances=futures_balances)

        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, stop.set)
            except NotImplementedError:
                pass  # signal handlers not supported on this platform/loop combo

        stream_tasks = [
            asyncio.create_task(stream_klines(spot_client, "spot", symbol))
            for symbol in config.symbols
        ] + [
            asyncio.create_task(stream_klines(futures_client, "futures", symbol))
            for symbol in config.symbols
        ]

        try:
            await stop.wait()
        except KeyboardInterrupt:
            pass  # Windows: loop.add_signal_handler is unsupported, Ctrl+C raises here instead
        log.info("phase0_stopping")
        for task in stream_tasks:
            task.cancel()
        await asyncio.gather(*stream_tasks, return_exceptions=True)
    finally:
        await spot_client.close_connection()
        await futures_client.close_connection()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
