"""A small FastAPI checkout service that charges a card through a payment provider.

The resilience behaviour under test lives here, in application code:
three attempts with exponential backoff on 429/503/timeouts, one idempotency
key reused across attempts, and a clean 502 when the provider stays down.
"""

from __future__ import annotations

import asyncio
import uuid

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

PROVIDER_URL = "https://payments.example/v1/charges"
RETRYABLE_STATUS = {429, 503}
MAX_ATTEMPTS = 3
BASE_DELAY = 0.05

app = FastAPI()
sleep = asyncio.sleep  # module-level so tests can observe backoff without waiting


class Order(BaseModel):
    order_id: str
    amount_cents: int


async def charge(order: Order) -> dict[str, object]:
    key = f"{order.order_id}-{uuid.uuid4()}"
    async with httpx.AsyncClient(timeout=2.0) as client:
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                response = await client.post(
                    PROVIDER_URL,
                    json={"amount": order.amount_cents, "reference": order.order_id},
                    headers={"Idempotency-Key": key},
                )
            except (httpx.TimeoutException, httpx.TransportError):
                if attempt == MAX_ATTEMPTS:
                    raise
            else:
                if response.status_code not in RETRYABLE_STATUS:
                    response.raise_for_status()
                    try:
                        return response.json()  # type: ignore[no-any-return]
                    except ValueError as exc:
                        raise RuntimeError("provider returned an unreadable body") from exc
                if attempt == MAX_ATTEMPTS:
                    response.raise_for_status()
            await sleep(BASE_DELAY * 2 ** (attempt - 1))
    raise AssertionError("unreachable")


@app.post("/checkout")
async def checkout(order: Order) -> dict[str, object]:
    try:
        charge_result = await charge(order)
    except (httpx.HTTPError, RuntimeError) as exc:
        raise HTTPException(status_code=502, detail="payment provider unavailable") from exc
    return {"order_id": order.order_id, "charge_id": charge_result["id"], "status": "paid"}
