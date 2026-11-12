"""One pricing function for every model call Arno makes.

The model decides what to compute; this module decides what an inference cost. It returns the
provider's reported value when the selected billing mode has one, then the installed
``genai-prices`` snapshot, then an honest unknown. Paise stay in the ledger: these amounts are USD
as exact ``Decimal`` values, and the native spend counters compare them at their own precision.

Nothing here reserves money, stores state, or reads a clock. Callers pass the response and the
configured provider/billing mode; the result is one immutable observation for the journal, the
native spend callback, and the trace.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from importlib.metadata import PackageNotFoundError, version
from typing import Any
from urllib.parse import urlsplit

from genai_prices.data_snapshot import get_snapshot
from pydantic_ai.messages import ModelResponse
from pydantic_ai.usage import RequestUsage

try:
    GENAI_PRICES_VERSION = version("genai-prices")
except PackageNotFoundError:  # pragma: no cover - the dependency is pinned
    GENAI_PRICES_VERSION = "unknown"

LOCAL_ZERO_SOURCE = "local_zero"
REGISTRY_SOURCE = "genai-prices"
PROVIDER_SOURCE = "provider_reported"
OPENCODE_SOURCE = "opencode_reported"
OPENCODE_ESTIMATE_SOURCE = "opencode_estimated"
UNKNOWN_SOURCE = "unknown"


def validate_pricing(
    *, provider: str, model: str, provider_url: str | None, billing_mode: str
) -> None:
    """Check paid pricing before dispatch; reported billing requires an explicit contract."""
    if billing_mode not in {"metered", "local", "local_zero", "reported"}:
        raise ValueError("BILLING_MODE must be metered, local, local_zero, or reported.")
    if billing_mode in LOCAL_BILLING_MODES or billing_mode == "reported":
        return
    host = (urlsplit(provider_url or "").hostname or "").lower()
    if (
        provider in {"opencode", "opencode-go", "opencode-zen", "openrouter"}
        or host == "opencode.ai"
    ):
        return
    probe = ModelResponse(
        parts=[],
        model_name=model,
        provider_name=provider,
        provider_url=provider_url,
        usage=RequestUsage(input_tokens=1, output_tokens=1),
    )
    if observe_cost(probe, provider=provider, billing_mode=billing_mode).usd is None:
        raise ValueError(f"No verified inference price for provider {provider!r}, model {model!r}.")


LOCAL_BILLING_MODES = frozenset({"local", "local_zero"})
LOCAL_PRICE_REVISION = "local-zero/v1"
REGISTRY_PRICE_REVISION = f"genai-prices/{GENAI_PRICES_VERSION}"
OPENCODE_PRICE_REVISION = "opencode-reported/v1"

# Without an endpoint, only these explicit identities may use the catalog. With an endpoint,
# the catalog must recognize its URL; a reseller cannot inherit another vendor's price merely
# by reusing its model ID or the generic OpenAI transport.
_VERIFIED_REGISTRY_PROVIDERS = frozenset(
    {"openai", "anthropic", "google", "google-cloud", "openrouter", "openai-chatgpt"}
)
_OPENCODE_HOST_MARKER = "opencode.ai"
_CHATGPT_PROVIDERS = frozenset({"openai-chatgpt", "chatgpt"})
_GO_BILLING_SOURCES = frozenset({"go", "go-plus"})

_USAGE_FIELDS = (
    "input_tokens",
    "cache_write_tokens",
    "cache_write_1h_tokens",
    "cache_read_tokens",
    "output_tokens",
    "output_reasoning_tokens",
    "input_audio_tokens",
    "cache_audio_read_tokens",
    "output_audio_tokens",
    "unmeasured_requests",
)


@dataclass(frozen=True, slots=True)
class CostObservation:
    """What one inference cost, how it was priced, and how much of it is known."""

    usd: Decimal | None
    source: str
    complete: bool
    price_revision: str
    provider_charge: Decimal | None = None
    allowance: Decimal | None = None
    billing_source: str | None = None
    usage: dict[str, int | float] | None = None
    rates: dict[str, str] | None = None

    def __post_init__(self) -> None:
        for name in ("usd", "provider_charge", "allowance"):
            value = getattr(self, name)
            if value is None:
                continue
            amount = as_amount(value)
            if amount is None:
                raise ValueError(f"{name} must be finite and nonnegative, got {value!r}")
            object.__setattr__(self, name, amount)

    @property
    def status(self) -> str:
        """``known``, ``partial``, or ``unknown``, in the terms the trace and journal use."""
        if self.usd is None:
            return "unknown"
        return "known" if self.complete else "partial"


def observe_cost(
    response: ModelResponse, *, provider: str | None = None, billing_mode: str = "metered"
) -> CostObservation:
    """Price one response once, without mutating it or touching a store."""
    usage = _usage_dict(response.usage)
    mode = (billing_mode or "metered").strip().lower()

    if mode in LOCAL_BILLING_MODES:
        # An explicit local profile is a measured zero even when the provider omitted usage.
        return CostObservation(
            usd=Decimal(0),
            source=LOCAL_ZERO_SOURCE,
            complete=True,
            price_revision=LOCAL_PRICE_REVISION,
            billing_source="local",
            usage=usage,
        )

    if _is_opencode(response, provider):
        reported = _opencode_observation(response, usage)
        if reported is not None:
            return reported

    unmeasured = response.usage.unmeasured_requests > 0
    reported_cost = _captured_cost_observation(response, usage, complete=not unmeasured)
    if reported_cost is not None:
        return reported_cost

    # The existing optional fallback is absent from the pinned catalog. Cohere publishes a
    # zero tariff for this exact model for trial and production keys, until rate limits. This
    # is a verified model/endpoint rate, never a free classification for an unknown model.
    if response.model_name == "command-a-plus-05-2026" and (
        urlsplit(response.provider_url or "").hostname or ""
    ).lower() in {"api.cohere.ai", "api.cohere.com"}:
        try:
            get_snapshot().find_provider_model(
                response.model_name,
                provider=None,
                provider_id=None,
                provider_api_url=response.provider_url,
            )
        except LookupError:
            return CostObservation(
                usd=Decimal(0),
                source="published_rate",
                complete=not unmeasured,
                price_revision="cohere-command-a-plus/2026-11-12",
                billing_source="free_tariff",
                usage=usage,
                rates={"input_mtok": "0", "output_mtok": "0"},
            )

    native_cost = (
        _native_cost_observation(response, usage, complete=not unmeasured, provider=provider)
        if _registry_allowed(response, provider)
        else None
    )
    if native_cost is not None:
        return native_cost
    if unmeasured:
        # A missing terminal usage event is never a successful free call. Keep the count so the
        # native incomplete path records it and raises.
        return CostObservation(
            usd=None,
            source=UNKNOWN_SOURCE,
            complete=False,
            price_revision=REGISTRY_PRICE_REVISION,
            usage=usage,
        )

    registry = _registry_observation(response, usage, provider=provider)
    if registry is not None:
        return registry

    return CostObservation(
        usd=None,
        source=UNKNOWN_SOURCE,
        complete=False,
        price_revision=REGISTRY_PRICE_REVISION,
        usage=usage,
    )


def price_response(response: ModelResponse) -> Decimal | None:
    """The ``SpendLimits(price=...)`` callback: the full amount or ``None`` for the native gap.

    ``None`` keeps the native incomplete-usage path: the response's own ``usage.cost`` subtotal is
    recorded and the configured ``on_unpriced`` policy applies. A complete observation returns its
    amount, so the catalog never re-prices a provider-reported value. An unknown price on an
    endpoint without a verified catalog identity raises instead of letting the registry price it
    as a different vendor.
    """
    provider, billing_mode = _response_profile(response)
    observation = observe_cost(response, provider=provider, billing_mode=billing_mode)
    if observation.usd is None:
        if response.usage.unmeasured_requests:
            return None
        if not _registry_allowed(response, provider):
            raise ValueError(
                "No verified price for "
                f"{provider or response.provider_name or 'unknown provider'} at "
                f"{urlsplit(response.provider_url or '').hostname or 'an unknown endpoint'}; "
                "report the provider cost or "
                "configure a verified provider profile."
            )
        return None
    if not observation.complete:
        if response.usage.cost is None:
            # Preserve a known subtotal for the native incomplete path.
            response.usage.cost = observation.usd
        return None
    return observation.usd


def cost_attributes(observation: CostObservation) -> dict[str, str | int | float | bool]:
    """The trace attributes Phoenix reads, with the amount omitted when it is unknown."""
    attributes: dict[str, str | int | float | bool] = {
        "arno.cost.source": observation.source,
        "arno.cost.completeness": observation.status,
        "arno.cost.price_revision": observation.price_revision,
    }
    if observation.usd is not None:
        attributes["llm.cost.total"] = float(observation.usd)
    if observation.provider_charge is not None:
        attributes["arno.cost.provider_charge"] = float(observation.provider_charge)
    if observation.allowance is not None:
        attributes["arno.cost.allowance"] = float(observation.allowance)
    if observation.billing_source is not None:
        attributes["arno.cost.billing_source"] = observation.billing_source
    usage = observation.usage or {}
    names = {
        "input_tokens": "llm.token_count.prompt",
        "output_tokens": "llm.token_count.completion",
        "cache_read_tokens": "llm.token_count.prompt_details.cache_read",
        "cache_write_tokens": "llm.token_count.prompt_details.cache_write",
        "output_reasoning_tokens": "llm.token_count.completion_details.reasoning",
        "audio_seconds": "arno.usage.audio_seconds",
    }
    for name, attribute in names.items():
        if name in usage:
            attributes[attribute] = usage[name]
    if "input_tokens" in usage or "output_tokens" in usage:
        attributes["llm.token_count.total"] = usage.get("input_tokens", 0) + usage.get(
            "output_tokens", 0
        )
    if observation.rates:
        attributes["arno.cost.rates"] = json.dumps(observation.rates, sort_keys=True)
    return attributes


def _response_profile(response: ModelResponse) -> tuple[str | None, str]:
    """The configured profile the model wrapper stamped on the response, if any."""
    details = response.provider_details or {}
    marker = details.get("arno_cost")
    if not isinstance(marker, dict):
        return None, "metered"
    provider = marker.get("provider")
    billing_mode = marker.get("billing_mode")
    return (
        provider if isinstance(provider, str) else None,
        billing_mode if isinstance(billing_mode, str) else "metered",
    )


def as_amount(value: object) -> Decimal | None:
    """A finite, nonnegative USD amount, or ``None`` for anything else."""
    if isinstance(value, bool) or not isinstance(value, (Decimal, int, float, str)):
        return None
    try:
        amount = value if isinstance(value, Decimal) else Decimal(str(value))
    except InvalidOperation, ValueError:
        return None
    return amount if amount.is_finite() and amount >= 0 else None


def _usage_dict(usage: RequestUsage) -> dict[str, int | float] | None:
    values: dict[str, int | float] = {}
    for name in _USAGE_FIELDS:
        value = getattr(usage, name, 0)
        if isinstance(value, int) and not isinstance(value, bool) and value:
            values[name] = value
    if usage.audio_seconds:
        values["audio_seconds"] = float(usage.audio_seconds)
    return values or None


def _is_opencode(response: ModelResponse, provider: str | None) -> bool:
    name = (provider or "").strip().lower()
    if name in {"opencode", "opencode-go", "opencode-zen"}:
        return True
    return _OPENCODE_HOST_MARKER in (response.provider_url or "")


def _opencode_observation(
    response: ModelResponse, usage: dict[str, int | float] | None
) -> CostObservation | None:
    """The narrow OpenCode adapter: charge, list value, allowance, and estimate stay distinct."""
    details = response.provider_details or {}
    raw = details.get("opencode")
    if not isinstance(raw, dict):
        return None

    billing_source = raw.get("billing_source")
    if not isinstance(billing_source, str):
        billing_source = None
    list_cost = as_amount(raw.get("list_cost"))
    charge = as_amount(raw.get("cost"))
    if charge is None:
        captured = details.get("provider_usage")
        if isinstance(captured, dict):
            charge = as_amount(captured.get("cost"))
    allowance = as_amount(raw.get("allowance"))
    estimated = raw.get("estimated") is True

    if list_cost is not None:
        usd = list_cost
    elif billing_source in _GO_BILLING_SOURCES:
        return CostObservation(
            usd=None,
            source=UNKNOWN_SOURCE,
            complete=False,
            price_revision=OPENCODE_PRICE_REVISION,
            provider_charge=charge,
            allowance=allowance,
            billing_source=billing_source,
            usage=usage,
        )
    elif charge is not None:
        usd = charge
    else:
        return CostObservation(
            usd=None,
            source=UNKNOWN_SOURCE,
            complete=False,
            price_revision=OPENCODE_PRICE_REVISION,
            provider_charge=charge,
            allowance=allowance,
            billing_source=billing_source,
            usage=usage,
        )

    return CostObservation(
        usd=usd,
        source=OPENCODE_ESTIMATE_SOURCE if estimated else OPENCODE_SOURCE,
        complete=response.usage.unmeasured_requests == 0,
        price_revision=OPENCODE_PRICE_REVISION,
        provider_charge=charge,
        allowance=allowance,
        billing_source=billing_source,
        usage=usage,
    )


def _captured_cost_observation(
    response: ModelResponse, usage: dict[str, int | float] | None, *, complete: bool
) -> CostObservation | None:
    """A cost captured from the provider's own wire body, zero included.

    Only the response hook and a native adapter that maps the wire field provide provenance for
    this. A zero that a generic SDK drops stays present under ``provider_usage``.
    """
    details = response.provider_details or {}
    amount: Decimal | None = None
    captured = details.get("provider_usage")
    if isinstance(captured, dict) and "cost" in captured:
        amount = as_amount(captured["cost"])
    if amount is None and "cost" in details:
        amount = as_amount(details["cost"])
    if amount is None:
        return None
    return CostObservation(
        usd=amount,
        source=PROVIDER_SOURCE,
        complete=complete,
        price_revision=REGISTRY_PRICE_REVISION,
        usage=usage,
    )


def _native_cost_observation(
    response: ModelResponse,
    usage: dict[str, int | float] | None,
    *,
    complete: bool,
    provider: str | None,
) -> CostObservation | None:
    """A ``usage.cost`` core already filled.

    Pydantic AI fills this field from ``genai-prices`` when the provider omits a cost, so without
    captured wire provenance it is an estimate, not a provider-reported charge.
    """
    amount = as_amount(response.usage.cost)
    if amount is None:
        return None
    rates = None
    if complete:
        try:
            calculated = response.cost()
        except LookupError, ValueError:
            pass
        else:
            if calculated.total_price == amount:
                rates = _rates(calculated.model_price)
    name = (provider or response.provider_name or "").strip().lower()
    return CostObservation(
        usd=amount,
        source=REGISTRY_SOURCE,
        complete=complete,
        price_revision=REGISTRY_PRICE_REVISION,
        billing_source="chatgpt_plan" if name in _CHATGPT_PROVIDERS else None,
        usage=usage,
        rates=rates,
    )


def _registry_allowed(response: ModelResponse, provider: str | None) -> bool:
    name = (provider or response.provider_name or "").strip().lower()
    name = {
        "openai-responses": "openai",
        "openai_responses": "openai",
        "responses": "openai",
        "chatgpt": "openai-chatgpt",
        "gemini": "google",
    }.get(name, name)
    if _OPENCODE_HOST_MARKER in (response.provider_url or ""):
        return False
    url = response.provider_url
    if not url:
        return name in _VERIFIED_REGISTRY_PROVIDERS
    try:
        # Match the actual endpoint, without falling back to the generic OpenAI transport's
        # provider name. This reuses the installed catalog for known vendors and rejects an
        # unknown reseller even if it reuses a familiar model ID.
        get_snapshot().find_provider(model_ref=None, provider_id=None, provider_api_url=url)
    except LookupError:
        return False
    return True


def _registry_observation(
    response: ModelResponse, usage: dict[str, int | float] | None, *, provider: str | None
) -> CostObservation | None:
    if not response.model_name or not _registry_allowed(response, provider):
        return None
    try:
        price = response.cost()
    except LookupError, ValueError:
        return None
    amount = as_amount(price.total_price)
    if amount is None:
        return None
    name = (provider or response.provider_name or "").strip().lower()
    return CostObservation(
        usd=amount,
        source=REGISTRY_SOURCE,
        complete=True,
        price_revision=REGISTRY_PRICE_REVISION,
        billing_source="chatgpt_plan" if name in _CHATGPT_PROVIDERS else None,
        usage=usage,
        rates=_rates(price.model_price),
    )


def _rates(model_price: Any) -> dict[str, str] | None:
    values = {
        str(key): str(value)
        for key, value in vars(model_price).items()
        if not str(key).startswith("_") and value is not None
    }
    return values or None


__all__ = [
    "GENAI_PRICES_VERSION",
    "LOCAL_BILLING_MODES",
    "LOCAL_PRICE_REVISION",
    "LOCAL_ZERO_SOURCE",
    "OPENCODE_ESTIMATE_SOURCE",
    "OPENCODE_PRICE_REVISION",
    "OPENCODE_SOURCE",
    "PROVIDER_SOURCE",
    "REGISTRY_PRICE_REVISION",
    "REGISTRY_SOURCE",
    "UNKNOWN_SOURCE",
    "CostObservation",
    "as_amount",
    "cost_attributes",
    "observe_cost",
    "price_response",
]
