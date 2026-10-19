import pathlib

import pytest
import yaml
from pydantic import ValidationError

from tradingbot.config.settings import Network, Secrets, load_config


def _write_yaml(tmp_path: pathlib.Path, data: dict) -> pathlib.Path:
    path = tmp_path / "settings.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


def test_load_config_valid(tmp_path):
    path = _write_yaml(
        tmp_path,
        {
            "spot": {"network": "testnet"},
            "futures": {"network": "testnet"},
            "symbols": ["btcusdt"],
        },
    )
    config = load_config(path)
    assert config.spot.network == Network.TESTNET
    assert config.futures.network == Network.TESTNET
    assert config.symbols == ["BTCUSDT"]  # normalized to uppercase
    assert config.log_level == "INFO"  # default


def test_load_config_rejects_bad_network(tmp_path):
    path = _write_yaml(
        tmp_path,
        {
            "spot": {"network": "mainnnet"},  # typo, invalid enum value
            "futures": {"network": "testnet"},
            "symbols": ["BTCUSDT"],
        },
    )
    with pytest.raises(ValidationError):
        load_config(path)


def test_load_config_requires_at_least_one_symbol(tmp_path):
    path = _write_yaml(
        tmp_path,
        {
            "spot": {"network": "testnet"},
            "futures": {"network": "testnet"},
            "symbols": [],
        },
    )
    with pytest.raises(ValidationError):
        load_config(path)


def test_secrets_credentials_for_raises_when_missing(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)  # no .env here, and clear any inherited env vars
    for var in (
        "BINANCE_SPOT_TESTNET_API_KEY",
        "BINANCE_SPOT_TESTNET_API_SECRET",
    ):
        monkeypatch.delenv(var, raising=False)

    secrets = Secrets(_env_file=None)
    with pytest.raises(ValueError, match="Missing API credentials"):
        secrets.credentials_for("spot", Network.TESTNET)


def test_secrets_credentials_for_returns_pair(monkeypatch):
    monkeypatch.setenv("BINANCE_FUTURES_TESTNET_API_KEY", "abc")
    monkeypatch.setenv("BINANCE_FUTURES_TESTNET_API_SECRET", "def")
    secrets = Secrets(_env_file=None)
    key, secret = secrets.credentials_for("futures", Network.TESTNET)
    assert (key, secret) == ("abc", "def")
