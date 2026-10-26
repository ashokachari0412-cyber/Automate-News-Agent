"""What each image / video cost, per paid service, and what's left in each account.

Every paid call records itself (service, what was made, USD) into the ledger of the job that's running;
at the end the job asks HeyGen and WaveSpeed for their live balances. The app shows it as a receipt
right under the finished image or video.

Prices are checked against real balance drops (Oct 2026): Wan 2.6 Flash 720p = $0.025/s, Seedream 5.0 Flash
= $0.027/image, HeyGen Avatar III = ~$1/min. Gemini (Nano Banana) and Flux are free-tier fallbacks.
"""

import contextvars
import logging
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

HEYGEN_PER_MIN = {"avatar_iii": 1.0, "avatar_iv": 4.83, "avatar_v": 7.20}
WAN_PER_SEC = {"720p": 0.025, "1080p": 0.0375}
OMNI_PER_SEC = {"360p": 0.03, "720p": 0.10, "1080p": 0.15}
SEEDREAM_PER_IMAGE = 0.027

_ledger: contextvars.ContextVar[Optional[List[Dict[str, Any]]]] = contextvars.ContextVar("cost_ledger", default=None)


def start() -> List[Dict[str, Any]]:
    """New ledger for the current job; asyncio tasks and to_thread calls started from here share it."""
    items: List[Dict[str, Any]] = []
    _ledger.set(items)
    return items


def record(service: str, item: str, usd: float, note: str = "") -> None:
    items = _ledger.get()
    if items is not None:
        items.append({"service": service, "item": item, "usd": round(usd, 4), "note": note})


def _env() -> Dict[str, str]:
    from pathlib import Path
    try:
        from dotenv import dotenv_values
        env_file = Path(__file__).resolve().parent.parent / ".env"
        return {k: (v or "").strip().strip('"').strip("'") for k, v in dotenv_values(env_file).items()}
    except Exception:
        return {}


async def balances() -> Dict[str, Optional[float]]:
    """Live remaining USD per service (None = the provider has no balance API, or the check failed)."""
    env, out = _env(), {"HeyGen": None, "WaveSpeed": None}
    async with httpx.AsyncClient(timeout=20) as client:
        if env.get("HEYGEN_API_KEY"):
            try:
                user = (await client.get("https://api.heygen.com/v3/users/me",
                                         headers={"x-api-key": env["HEYGEN_API_KEY"]})).json().get("data") or {}
                wallet = user.get("wallet") or {}
                bal = wallet.get("remaining_balance", wallet.get("balance"))
                out["HeyGen"] = round(float(bal), 2) if bal is not None else None
            except Exception as e:
                logger.warning(f"HeyGen balance check failed: {e}")
        if env.get("WAVESPEED_API_KEY"):
            try:
                data = (await client.get("https://api.wavespeed.ai/api/v3/balance",
                                         headers={"Authorization": f"Bearer {env['WAVESPEED_API_KEY']}"})).json()
                out["WaveSpeed"] = round(float((data.get("data") or {})["balance"]), 2)
            except Exception as e:
                logger.warning(f"WaveSpeed balance check failed: {e}")
    return out


async def summary(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    """The receipt: every paid item, the total per service, the grand total and the live balances."""
    per_service: Dict[str, float] = {}
    for it in items:
        per_service[it["service"]] = round(per_service.get(it["service"], 0) + it["usd"], 4)
    return {"items": items, "per_service": per_service, "total_usd": round(sum(per_service.values()), 4),
            "balances": await balances()}
