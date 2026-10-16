from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone

from shared.models import Customer, UsageCounter, utc_now
from shared.storage import JsonStore


PLAN_LIMITS = {
    "FREE": {
        "search_requests_per_month": 100,
        "ingestion_rows_per_month": 1000,
        "active_api_keys": 1,
    },
    "PRO": {
        "search_requests_per_month": 10_000,
        "ingestion_rows_per_month": 100_000,
        "active_api_keys": 5,
    },
    "ENTERPRISE": {
        "search_requests_per_month": 1_000_000,
        "ingestion_rows_per_month": 10_000_000,
        "active_api_keys": 25,
    },
}


class PlanLimitError(ValueError):
    def __init__(self, message: str, plan: str, limit_name: str, limit: int) -> None:
        super().__init__(message)
        self.plan = plan
        self.limit_name = limit_name
        self.limit = limit


class UsageService:
    def __init__(self, store: JsonStore) -> None:
        self.store = store

    def reserve_search(self, customer: Customer, amount: int = 1) -> UsageCounter:
        return self._reserve(customer, "search_requests", "search_requests_per_month", amount)

    def reserve_ingestion_rows(self, customer: Customer, amount: int) -> UsageCounter:
        return self._reserve(customer, "ingestion_rows", "ingestion_rows_per_month", amount)

    def _reserve(self, customer: Customer, field: str, limit_name: str, amount: int) -> UsageCounter:
        plan = customer.plan.upper()
        limits = PLAN_LIMITS.get(plan, PLAN_LIMITS["FREE"])
        limit = limits[limit_name]
        if amount < 1:
            raise ValueError("Usage reservation amount must be positive.")
        if amount > limit:
            raise PlanLimitError(
                f"{limit_name} exceeded for {plan} plan. Please upgrade your plan.",
                plan=plan,
                limit_name=limit_name,
                limit=limit,
            )
        reserve = getattr(self.store, "reserve_usage", None)
        if reserve:
            counter = reserve(customer.customer_id, current_period(), field, amount, limit)
            if counter is not None:
                return counter
            raise PlanLimitError(
                f"{limit_name} exceeded for {plan} plan. Please upgrade your plan.",
                plan=plan,
                limit_name=limit_name,
                limit=limit,
            )

        counter = self.store.get_usage_counter(customer.customer_id, current_period())
        current = getattr(counter, field)
        if current + amount > limit:
            raise PlanLimitError(
                f"{limit_name} exceeded for {plan} plan. Please upgrade your plan.",
                plan=plan,
                limit_name=limit_name,
                limit=limit,
            )
        setattr(counter, field, current + amount)
        counter.updated_at = utc_now()
        self.store.upsert_usage_counter(counter)
        return counter


def plan_limits(plan: str) -> dict[str, int]:
    return PLAN_LIMITS.get(plan.upper(), PLAN_LIMITS["FREE"])


def current_period() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def public_usage(counter: UsageCounter) -> dict:
    return asdict(counter)
