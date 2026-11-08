"""Estimated cost of a model call at list price (NFR-08), from config/model_prices.yaml."""

import logging
from decimal import Decimal
from functools import cache
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)
DEFAULT_PRICES = Path(__file__).resolve().parents[3] / "config" / "model_prices.yaml"
_MILLION = Decimal(1_000_000)


@cache
def load_prices(path: Path = DEFAULT_PRICES) -> dict[str, tuple[Decimal, Decimal]]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {
        str(model): (Decimal(str(rates["input"])), Decimal(str(rates["output"])))
        for model, rates in raw.items()
    }


_unpriced: set[str] = set()


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> Decimal:
    """USD at list price; 0 (logged once per model) when the model has no price."""
    rates = load_prices().get(model)
    if rates is None and model.startswith("fake/"):  # test doubles
        return Decimal(0)
    if rates is None:
        if model not in _unpriced:
            _unpriced.add(model)
            logger.warning("No list price for %s; its calls are costed at $0", model)
        return Decimal(0)
    per_input, per_output = rates
    return (per_input * input_tokens + per_output * output_tokens) / _MILLION
