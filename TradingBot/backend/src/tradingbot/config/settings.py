"""App configuration (YAML, non-secret) and secrets (.env, via pydantic-settings)."""

import enum
import pathlib

import yaml
from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Network(str, enum.Enum):
    TESTNET = "testnet"
    MAINNET = "mainnet"


class MarketConfig(BaseModel):
    network: Network


class AppConfig(BaseModel):
    spot: MarketConfig
    futures: MarketConfig
    symbols: list[str] = Field(min_length=1)
    log_dir: str = "logs"
    log_level: str = "INFO"

    @field_validator("symbols")
    @classmethod
    def _upper(cls, v: list[str]) -> list[str]:
        return [s.upper() for s in v]


class Secrets(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    binance_spot_testnet_api_key: str | None = None
    binance_spot_testnet_api_secret: str | None = None
    binance_futures_testnet_api_key: str | None = None
    binance_futures_testnet_api_secret: str | None = None
    binance_spot_mainnet_api_key: str | None = None
    binance_spot_mainnet_api_secret: str | None = None
    binance_futures_mainnet_api_key: str | None = None
    binance_futures_mainnet_api_secret: str | None = None

    def credentials_for(self, market: str, network: Network) -> tuple[str, str]:
        prefix = f"binance_{market}_{network.value}"
        key = getattr(self, f"{prefix}_api_key")
        secret = getattr(self, f"{prefix}_api_secret")
        if not key or not secret:
            raise ValueError(
                f"Missing API credentials for {market}/{network.value}: "
                f"set {prefix.upper()}_API_KEY and {prefix.upper()}_API_SECRET in .env"
            )
        return key, secret


def load_config(path: str | pathlib.Path) -> AppConfig:
    raw = yaml.safe_load(pathlib.Path(path).read_text(encoding="utf-8"))
    return AppConfig.model_validate(raw)
