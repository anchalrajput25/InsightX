# backend/evaluation/variants.py
#
# Pipeline variants for ablation studies: run the same golden set with
# one component changed and measure what it is worth.
#
# Spec format (CLI --variant, repeatable):
#
#   baseline                          the current configuration
#   single:candidates=1               one SQL candidate instead of N
#   nofix:correction=0                no SQL correction loop
#   lean:candidates=1,correction=0    several changes at once
#   big:sql_model=groq/openai/gpt-oss-120b   pin one model for SQL work
#
# Settings that can be changed:
#   candidates      number of SQL candidates (self-consistency)
#   correction      maximum SQL correction attempts
#   query_variants  rewritten-question variants used for retrieval
#   top_k           columns kept by schema retrieval
#   adaptive        1: simple questions get one SQL candidate; 0: always all
#   light_model     model for light jobs (rewrite, decompose, linking, answer)
#   sql_model       model for SQL generation
#   heavy_model     model for heavy jobs (SQL repair, investigations)
#   small_model     old name for light_model
#
# A model setting pins that tier to exactly one route (no failover), so
# the benchmark measures that model alone. Comparing variants such as
#   --variant base --variant lite:sql_model=gemini/gemini-3.5-flash-lite
# shows which free model to put first in each tier.
#
# Variants patch process-wide settings while active, so they run one
# after another (never two variants at the same time).

import dataclasses
import sys
from contextlib import contextmanager
from typing import Any, Dict, Iterator, List

from backend import config
from backend.ai_gateway.gateway import get_gateway
from backend.ai_gateway.routes import DEFAULT_LIMITS, parse_route

SETTING_FIELDS = {
    "candidates": ("sql_candidates", int),
    "query_variants": ("query_variants", int),
    "top_k": ("retrieval_top_k", int),
    "adaptive": ("adaptive_candidates", int),
}
MODEL_KEYS = {"light_model": "light", "small_model": "light", "sql_model": "sql", "heavy_model": "heavy"}
KNOWN_KEYS = set(SETTING_FIELDS) | set(MODEL_KEYS) | {"correction"}


class VariantError(ValueError):
    pass


@dataclasses.dataclass
class Variant:
    name: str
    changes: Dict[str, Any] = dataclasses.field(default_factory=dict)

    def label(self) -> str:
        if not self.changes:
            return self.name
        return f"{self.name} ({', '.join(f'{k}={v}' for k, v in self.changes.items())})"


def parse_variant(spec: str) -> Variant:
    spec = (spec or "").strip()
    if not spec:
        raise VariantError("Empty variant.")
    name, _, rest = spec.partition(":")
    name = name.strip()
    if not name.replace("_", "").replace("-", "").isalnum():
        raise VariantError(f"Variant name '{name}' may only contain letters, digits, - and _.")
    changes: Dict[str, Any] = {}
    for part in filter(None, (item.strip() for item in rest.split(","))):
        key, sep, value = part.partition("=")
        key, value = key.strip(), value.strip()
        if not sep or not value:
            raise VariantError(f"'{part}' must look like key=value.")
        if key not in KNOWN_KEYS:
            raise VariantError(f"Unknown setting '{key}'. Known: {', '.join(sorted(KNOWN_KEYS))}.")
        if key in MODEL_KEYS:
            try:
                parse_route(value, DEFAULT_LIMITS)
            except ValueError as exc:
                raise VariantError(str(exc)) from exc
            changes[key] = value
        else:
            try:
                number = int(value)
            except ValueError as exc:
                raise VariantError(f"'{key}' needs a whole number, got '{value}'.") from exc
            if number < (0 if key in ("correction", "adaptive") else 1):
                raise VariantError(f"'{key}' is out of range: {number}.")
            changes[key] = number
    return Variant(name=name, changes=changes)


def parse_variants(specs: List[str]) -> List[Variant]:
    variants = [parse_variant(spec) for spec in specs] or [Variant("baseline")]
    names = [variant.name for variant in variants]
    duplicates = {name for name in names if names.count(name) > 1}
    if duplicates:
        raise VariantError(f"Duplicate variant names: {', '.join(sorted(duplicates))}.")
    return variants


def _modules_holding(value: Any, attribute: str) -> List[Any]:
    return [module for name, module in list(sys.modules.items())
            if name.startswith("backend") and module is not None and getattr(module, attribute, None) is value]


@contextmanager
def applied(variant: Variant) -> Iterator[Dict[str, Any]]:
    """Activate a variant; yields the effective settings for the report."""
    from backend.text_to_sql import agent

    original_settings = config.query_settings
    original_correction = agent.MAX_CORRECTION_ATTEMPTS
    gateway = get_gateway()
    original_routes = {tier: list(routes) for tier, routes in gateway.routes.items()}
    holders = _modules_holding(original_settings, "query_settings")

    replacements = {SETTING_FIELDS[key][0]: value for key, value in variant.changes.items() if key in SETTING_FIELDS}
    patched_settings = dataclasses.replace(original_settings, **replacements) if replacements else original_settings
    try:
        for module in holders:
            module.query_settings = patched_settings
        if "correction" in variant.changes:
            agent.MAX_CORRECTION_ATTEMPTS = variant.changes["correction"]
        for key, tier in MODEL_KEYS.items():
            if key in variant.changes:
                gateway.routes[tier] = [parse_route(variant.changes[key], DEFAULT_LIMITS)]
                gateway.pinned.add(tier)
        yield effective_settings()
    finally:
        for module in holders:
            module.query_settings = original_settings
        agent.MAX_CORRECTION_ATTEMPTS = original_correction
        gateway.routes.clear()
        gateway.routes.update(original_routes)
        gateway.pinned.clear()


def effective_settings() -> Dict[str, Any]:
    from backend.text_to_sql import agent

    settings = agent.query_settings
    return {
        "candidates": settings.sql_candidates,
        "adaptive": settings.adaptive_candidates,
        "correction": agent.MAX_CORRECTION_ATTEMPTS,
        "query_variants": settings.query_variants,
        "top_k": settings.retrieval_top_k,
        # first-choice route per tier (later ones are failover)
        "light_model": _first_route("light"),
        "sql_model": _first_route("sql"),
        "heavy_model": _first_route("heavy"),
    }


def _first_route(tier: str) -> str:
    routes = get_gateway().routes.get(tier) or []
    return routes[0].id if routes else ""
