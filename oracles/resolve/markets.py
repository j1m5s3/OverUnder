"""Market list for the resolvers: the operator view, so paused registered markets still resolve.

The public GET /api/v1/markets hides paused markets, but operations pause registered
markets rather than archive them and those may hold positions. Both resolvers read
GET /api/v1/markets?includePaused=1 with the operator bearer JWT instead. When the
API rejects that request with a 4xx (an older API without the parameter, or the
JWT is refused) or the job has no JWT config, they fall back to the public list and
say so in the stage summary (`marketsView`).
"""

from __future__ import annotations

from typing import Any, Callable

import httpx

from redact import log_error
from scores.publish import mint_operator_jwt

OPERATOR_PATH = "/api/v1/markets?includePaused=1"
PUBLIC_PATH = "/api/v1/markets"


def operator_get_json(url: str) -> Any:
    """GET with the operator bearer JWT. Raises httpx.HTTPStatusError on 4xx/5xx."""
    token = mint_operator_jwt()
    with httpx.Client(timeout=30) as client:
        response = client.get(url, headers={"Authorization": f"Bearer {token}"})
        response.raise_for_status()
        return response.json()


def _client_error(exc: Exception) -> int | None:
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    return status if isinstance(status, int) and 400 <= status < 500 else None


def market_cards(
    base: str,
    getter: Callable[[str], Any],
    operator_get: Callable[[str], Any] | None,
) -> tuple[list, str]:
    """(cards, "operator" | "public"). Without `operator_get` only the public list is read."""
    if operator_get is not None:
        try:
            cards = operator_get(f"{base}{OPERATOR_PATH}")
        except httpx.HTTPStatusError as exc:
            status = _client_error(exc)
            if status is None:
                raise
            log_error(f"operator market list rejected ({status}); using the public list (paused markets hidden)")
        except RuntimeError as exc:
            # mint_operator_jwt: JWT_SECRET / OPERATOR_PRIVATE_KEY missing.
            log_error(f"operator market list unavailable ({exc}); using the public list (paused markets hidden)")
        else:
            if not isinstance(cards, list):
                raise RuntimeError("markets list must be an array")
            return cards, "operator"
    cards = getter(f"{base}{PUBLIC_PATH}")
    if not isinstance(cards, list):
        raise RuntimeError("markets list must be an array")
    return cards, "public"
