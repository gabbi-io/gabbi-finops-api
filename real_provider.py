# real_provider.py
from __future__ import annotations

import os
from calendar import monthrange
from datetime import datetime, timedelta, timezone
from typing import Any
from functools import lru_cache

from db import fetch_all, fetch_one, execute, execute_returning, table_exists


ROI_HOURLY_RATE_BRL = float(os.getenv("ROI_HOURLY_RATE_BRL", "45"))
ROI_MINUTES_SAVED_PER_INTERACTION = float(os.getenv("ROI_MINUTES_SAVED_PER_INTERACTION", "6"))
BUDGET_MONTHLY_BRL = float(os.getenv("BUDGET_MONTHLY_BRL", "10000"))
BUDGET_ALERT_PERCENT = float(os.getenv("BUDGET_ALERT_PERCENT", "80"))
MODEL_MIGRATION_RATIO = float(os.getenv("MODEL_MIGRATION_RATIO", "0.30"))

CHEAPER_MODEL_MAP = {
    "gpt-5.1": "gpt-4o",
    "gpt-5": "gpt-4o",
    "gpt-5.0": "gpt-4o",
    "gpt-4.1": "gpt-4.1-mini",
    "gpt-4.1-turbo": "gpt-4o-mini",
    "gpt-4o": "gpt-4o-mini",
}


def _parse_days(days: int) -> tuple[datetime, datetime]:
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=max(1, int(days)))
    return start, end


def _month_bounds(now: datetime) -> tuple[datetime, datetime]:
    start = datetime(now.year, now.month, 1, tzinfo=timezone.utc)
    last_day = monthrange(now.year, now.month)[1]
    end = datetime(now.year, now.month, last_day, 23, 59, 59, 999999, tzinfo=timezone.utc) + timedelta(microseconds=1)
    return start, end


def _has_pricing() -> bool:
    return table_exists("finops", "model_pricing")


def _has_ledger() -> bool:
    return table_exists("finops", "cost_ledger")


def _has_accumulators() -> bool:
    return table_exists("finops", "billing_accumulator")




@lru_cache(maxsize=128)
def _column_exists(schema: str, table: str, column: str) -> bool:
    row = fetch_one(
        """
        select 1
        from information_schema.columns
        where table_schema=%(s)s and table_name=%(t)s and column_name=%(c)s
        limit 1
        """,
        {"s": schema, "t": table, "c": column},
    )
    return bool(row)


def _table_for_alias(alias: str) -> str:
    return {"u": "interaction_usage", "l": "cost_ledger"}.get(alias, alias)


def _business_area_expr(alias: str) -> str:
    """Returns the best available column for business-area filtering/grouping.

    Current production tables may not yet have business_area. Until the schema evolves,
    project_key is used as a compatible fallback so the real frontend can already filter by area.
    """
    table = _table_for_alias(alias)
    for col in ("business_area", "area_name", "area", "project_key"):
        if _column_exists("finops", table, col):
            return f"{alias}.{col}"
    return "NULL"

def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v or 0)
    except Exception:
        return default


def _build_filter_clauses(
    project_key: str | None,
    agent_name: str | None,
    alias: str,
    business_area: str | None = None,
    project_keys: list[str] | None = None,
) -> list[str]:
    clauses: list[str] = []

    # Filtro multiempresa correto:
    # clientKey -> Customer.id -> Project.id[] -> finops.<table>.project_key IN Project.id[]
    # Quando project_keys=[] significa cliente válido sem projetos ou cliente inválido: deve retornar vazio.
    if project_keys is not None:
        if len(project_keys) == 0:
            clauses.append("1=0")
        else:
            clauses.append(f"{alias}.project_key = ANY(%(project_keys)s)")
    elif project_key:
        clauses.append(f"{alias}.project_key = %(project_key)s")

    if agent_name:
        clauses.append(f"{alias}.agent_name = %(agent_name)s")
    if business_area:
        expr = _business_area_expr(alias)
        if expr != "NULL":
            clauses.append(f"{expr} = %(business_area)s")
    return clauses

def _compose_where(clauses: list[str]) -> str:
    active = [c for c in clauses if c]
    return " and ".join(active) if active else "1=1"


def _compute_budget_metrics(
    pricing_on: bool,
    ledger_on: bool,
    project_key: str | None,
    agent_name: str | None,
    business_area: str | None = None,
    project_keys: list[str] | None = None,
) -> dict:
    now = datetime.now(timezone.utc)
    month_start, month_end = _month_bounds(now)

    params: dict[str, Any] = {"start": month_start, "end": month_end}
    if project_key:
        params["project_key"] = project_key
    if project_keys is not None:
        params["project_keys"] = project_keys
    if agent_name:
        params["agent_name"] = agent_name
    if business_area:
        params["business_area"] = business_area

    month_cost = 0.0

    if ledger_on:
        where_l = _compose_where(
            ["l.occurred_at >= %(start)s", "l.occurred_at < %(end)s"] + _build_filter_clauses(project_key, agent_name, "l", business_area, project_keys)
        )
        row = fetch_one(
            f"""
            select coalesce(sum(l.amount_brl), 0)::numeric(14,4) as total_cost
            from finops.cost_ledger l
            where {where_l}
            """,
            params,
        ) or {"total_cost": 0}
        month_cost = _safe_float(row["total_cost"])
    elif pricing_on:
        where_u = _compose_where(
            ["u.created_at >= %(start)s", "u.created_at < %(end)s"] + _build_filter_clauses(project_key, agent_name, "u", business_area, project_keys)
        )
        row = fetch_one(
            f"""
            select coalesce(sum((u.total_tokens/1000.0) * p.cost_per_1k_tokens_brl), 0)::numeric(14,4) as total_cost
            from finops.interaction_usage u
            join finops.model_pricing p
              on p.model = u.model
             and u.created_at >= p.valid_from
             and (p.valid_to is null or u.created_at < p.valid_to)
            where {where_u}
            """,
            params,
        ) or {"total_cost": 0}
        month_cost = _safe_float(row["total_cost"])

    elapsed_days = max(now.day, 1)
    total_days = monthrange(now.year, now.month)[1]
    forecast = (month_cost / elapsed_days) * total_days if elapsed_days else month_cost
    budget = BUDGET_MONTHLY_BRL
    used_percent = (month_cost / budget * 100.0) if budget > 0 else 0.0
    forecast_percent = (forecast / budget * 100.0) if budget > 0 else 0.0

    return {
        "budget_monthly_brl": round(budget, 2),
        "budget_used_brl": round(month_cost, 2),
        "budget_used_percent": round(used_percent, 2),
        "budget_forecast_brl": round(forecast, 2),
        "budget_forecast_percent": round(forecast_percent, 2),
        "budget_alert_threshold_percent": BUDGET_ALERT_PERCENT,
        "budget_alert_triggered": used_percent >= BUDGET_ALERT_PERCENT,
        "budget_forecast_alert_triggered": forecast_percent >= 100.0,
    }


def _current_pricing_map() -> dict[str, float]:
    rows = fetch_all(
        """
        select distinct on (model)
            model,
            cost_per_1k_tokens_brl
        from finops.model_pricing
        where valid_to is null
        order by model, valid_from desc
        """
    )
    return {r["model"]: _safe_float(r["cost_per_1k_tokens_brl"]) for r in rows}


def _compute_recommendations(
    pricing_on: bool,
    project_key: str | None,
    agent_name: str | None,
    start: datetime,
    end: datetime,
    business_area: str | None = None,
    project_keys: list[str] | None = None,
) -> list[dict]:
    if not pricing_on:
        return []

    params: dict[str, Any] = {"start": start, "end": end}
    if project_key:
        params["project_key"] = project_key
    if project_keys is not None:
        params["project_keys"] = project_keys
    if agent_name:
        params["agent_name"] = agent_name
    if business_area:
        params["business_area"] = business_area

    where_u = _compose_where(
        ["u.created_at >= %(start)s", "u.created_at < %(end)s"] + _build_filter_clauses(project_key, agent_name, "u", business_area, project_keys)
    )

    rows = fetch_all(
        f"""
        with current_pricing as (
            select distinct on (model)
                model,
                cost_per_1k_tokens_brl
            from finops.model_pricing
            where valid_to is null
            order by model, valid_from desc
        )
        select
            u.model,
            coalesce(sum(u.total_tokens),0)::numeric(14,2) as total_tokens,
            cp.cost_per_1k_tokens_brl
        from finops.interaction_usage u
        join current_pricing cp on cp.model = u.model
        where {where_u}
        group by u.model, cp.cost_per_1k_tokens_brl
        order by total_tokens desc
        """,
        params,
    )

    pricing_map = _current_pricing_map()
    recommendations: list[dict] = []

    for row in rows:
        model = row["model"]
        current_price = _safe_float(row["cost_per_1k_tokens_brl"])
        target = CHEAPER_MODEL_MAP.get(model)
        target_price = pricing_map.get(target) if target else None
        if not target or target_price is None or target_price >= current_price:
            continue

        total_tokens = _safe_float(row["total_tokens"])
        current_cost = (total_tokens / 1000.0) * current_price
        target_cost = (total_tokens / 1000.0) * target_price
        estimated_saving = max(current_cost - target_cost, 0.0) * MODEL_MIGRATION_RATIO
        if estimated_saving <= 0:
            continue

        recommendations.append({
            "current_model": model,
            "suggested_model": target,
            "coverage_percent": round(MODEL_MIGRATION_RATIO * 100.0, 1),
            "observed_tokens": round(total_tokens, 0),
            "current_cost_brl": round(current_cost, 2),
            "target_cost_brl": round(target_cost, 2),
            "estimated_savings_brl": round(estimated_saving, 2),
            "summary": f"Migrar ~{round(MODEL_MIGRATION_RATIO * 100)}% do uso de {model} para {target}",
        })

    recommendations.sort(key=lambda x: x["estimated_savings_brl"], reverse=True)
    return recommendations[:3]


def _compute_showback(
    pricing_on: bool,
    ledger_on: bool,
    project_key: str | None,
    agent_name: str | None,
    start: datetime,
    end: datetime,
    business_area: str | None = None,
    project_keys: list[str] | None = None,
) -> dict:
    params: dict[str, Any] = {"start": start, "end": end}
    if project_key:
        params["project_key"] = project_key
    if project_keys is not None:
        params["project_keys"] = project_keys
    if agent_name:
        params["agent_name"] = agent_name
    if business_area:
        params["business_area"] = business_area

    project_rows = []
    agent_rows = []
    source_rows = []

    if ledger_on:
        where_l = _compose_where(
            ["l.occurred_at >= %(start)s", "l.occurred_at < %(end)s"] + _build_filter_clauses(project_key, agent_name, "l", business_area, project_keys)
        )
        project_rows = fetch_all(
            f"""
            select coalesce(l.project_key, 'N/A') as label,
                   coalesce(sum(l.amount_brl),0)::numeric(14,4) as amount_brl
            from finops.cost_ledger l
            where {where_l}
            group by 1
            order by amount_brl desc
            limit 10
            """,
            params,
        )
        agent_rows = fetch_all(
            f"""
            select coalesce(l.agent_name, 'N/A') as label,
                   coalesce(sum(l.amount_brl),0)::numeric(14,4) as amount_brl
            from finops.cost_ledger l
            where {where_l}
            group by 1
            order by amount_brl desc
            limit 10
            """,
            params,
        )
        source_rows = fetch_all(
            f"""
            select coalesce(l.source_type, 'N/A') as label,
                   coalesce(sum(l.amount_brl),0)::numeric(14,4) as amount_brl
            from finops.cost_ledger l
            where {where_l}
            group by 1
            order by amount_brl desc
            """,
            params,
        )
    elif pricing_on:
        where_u = _compose_where(
            ["u.created_at >= %(start)s", "u.created_at < %(end)s"] + _build_filter_clauses(project_key, agent_name, "u", business_area, project_keys)
        )
        project_rows = fetch_all(
            f"""
            select coalesce(u.project_key, 'N/A') as label,
                   coalesce(sum((u.total_tokens/1000.0) * p.cost_per_1k_tokens_brl),0)::numeric(14,4) as amount_brl
            from finops.interaction_usage u
            join finops.model_pricing p
              on p.model = u.model
             and u.created_at >= p.valid_from
             and (p.valid_to is null or u.created_at < p.valid_to)
            where {where_u}
            group by 1
            order by amount_brl desc
            limit 10
            """,
            params,
        )
        agent_rows = fetch_all(
            f"""
            select coalesce(u.agent_name, 'N/A') as label,
                   coalesce(sum((u.total_tokens/1000.0) * p.cost_per_1k_tokens_brl),0)::numeric(14,4) as amount_brl
            from finops.interaction_usage u
            join finops.model_pricing p
              on p.model = u.model
             and u.created_at >= p.valid_from
             and (p.valid_to is null or u.created_at < p.valid_to)
            where {where_u}
            group by 1
            order by amount_brl desc
            limit 10
            """,
            params,
        )
        try:
            source_rows = fetch_all(
                f"""
                select coalesce(u.source_type, 'N/A') as label,
                       coalesce(sum((u.total_tokens/1000.0) * p.cost_per_1k_tokens_brl),0)::numeric(14,4) as amount_brl
                from finops.interaction_usage u
                join finops.model_pricing p
                  on p.model = u.model
                 and u.created_at >= p.valid_from
                 and (p.valid_to is null or u.created_at < p.valid_to)
                where {where_u}
                group by 1
                order by amount_brl desc
                """,
                params,
            )
        except Exception:
            source_rows = []

    return {
        "by_project": [{"label": r["label"], "amount_brl": round(_safe_float(r["amount_brl"]), 2)} for r in project_rows],
        "by_agent": [{"label": r["label"], "amount_brl": round(_safe_float(r["amount_brl"]), 2)} for r in agent_rows],
        "by_source": [{"label": r["label"], "amount_brl": round(_safe_float(r["amount_brl"]), 2)} for r in source_rows],
    }


def summarize_real(
    days: int = 30,
    project_key: str | None = None,
    agent_name: str | None = None,
    business_area: str | None = None,
    project_keys: list[str] | None = None,
) -> dict:
    start, end = _parse_days(days)

    params: dict[str, Any] = {"start": start, "end": end}
    if project_key:
        params["project_key"] = project_key
    if project_keys is not None:
        params["project_keys"] = project_keys
    if agent_name:
        params["agent_name"] = agent_name
    if business_area:
        params["business_area"] = business_area

    pricing_on = _has_pricing()
    ledger_on = _has_ledger()
    acc_on = _has_accumulators()

    where_u = _compose_where(
        ["u.created_at >= %(start)s", "u.created_at < %(end)s"] + _build_filter_clauses(project_key, agent_name, "u", business_area, project_keys)
    )

    kpi_usage = fetch_one(
        f"""
        select
          count(*)::int as interaction_rows,
          coalesce(sum(u.total_tokens),0)::bigint as total_tokens,
          coalesce(avg(u.latency_ms),0)::numeric(12,2) as avg_latency_ms,
          coalesce(avg(case when u.cached then 1 else 0 end),0)::numeric(12,4) as cache_hit_rate
        from finops.interaction_usage u
        where {where_u}
        """,
        params,
    ) or {"interaction_rows": 0, "total_tokens": 0, "avg_latency_ms": 0, "cache_hit_rate": 0}

    total_cost_brl = 0.0
    manual_cost_brl = 0.0
    automation_cost_brl = 0.0
    ledger_rows_count = 0

    if ledger_on:
        where_l = _compose_where(
            ["l.occurred_at >= %(start)s", "l.occurred_at < %(end)s"] + _build_filter_clauses(project_key, agent_name, "l", business_area, project_keys)
        )
        kpi_ledger = fetch_one(
            f"""
            select
              count(*)::int as ledger_rows,
              coalesce(sum(l.amount_brl),0)::numeric(14,4) as total_cost,
              coalesce(sum(case when l.source_type='MANUAL' then l.amount_brl else 0 end),0)::numeric(14,4) as manual_cost,
              coalesce(sum(case when l.source_type='AUTOMATION' then l.amount_brl else 0 end),0)::numeric(14,4) as automation_cost
            from finops.cost_ledger l
            where {where_l}
            """,
            params,
        ) or {"ledger_rows": 0, "total_cost": 0, "manual_cost": 0, "automation_cost": 0}

        ledger_rows_count = int(kpi_ledger["ledger_rows"] or 0)
        total_cost_brl = float(kpi_ledger["total_cost"] or 0)
        manual_cost_brl = float(kpi_ledger["manual_cost"] or 0)
        automation_cost_brl = float(kpi_ledger["automation_cost"] or 0)
    elif pricing_on:
        cost_row = fetch_one(
            f"""
            select
              coalesce(sum((u.total_tokens/1000.0) * p.cost_per_1k_tokens_brl),0)::numeric(14,4) as total_cost
            from finops.interaction_usage u
            join finops.model_pricing p
              on p.model = u.model
             and u.created_at >= p.valid_from
             and (p.valid_to is null or u.created_at < p.valid_to)
            where {where_u}
            """,
            params,
        ) or {"total_cost": 0}
        total_cost_brl = float(cost_row["total_cost"] or 0)

        try:
            by_source = fetch_all(
                f"""
                select
                  coalesce(u.source_type,'MANUAL') as source_type,
                  coalesce(sum((u.total_tokens/1000.0) * p.cost_per_1k_tokens_brl),0)::numeric(14,4) as cost
                from finops.interaction_usage u
                join finops.model_pricing p
                  on p.model = u.model
                 and u.created_at >= p.valid_from
                 and (p.valid_to is null or u.created_at < p.valid_to)
                where {where_u}
                group by 1
                """,
                params,
            )
            for r in by_source:
                if r["source_type"] == "MANUAL":
                    manual_cost_brl = float(r["cost"] or 0)
                elif r["source_type"] == "AUTOMATION":
                    automation_cost_brl = float(r["cost"] or 0)
        except Exception:
            pass

    estimated_savings_brl = (
        int(kpi_usage["interaction_rows"] or 0)
        * (ROI_MINUTES_SAVED_PER_INTERACTION / 60.0)
        * ROI_HOURLY_RATE_BRL
    )
    net_value_brl = estimated_savings_brl - total_cost_brl
    roi_percent = ((net_value_brl / total_cost_brl) * 100.0) if total_cost_brl > 0 else 0.0

    budget_metrics = _compute_budget_metrics(pricing_on, ledger_on, project_key, agent_name, business_area, project_keys)
    recommendations = _compute_recommendations(pricing_on, project_key, agent_name, start, end, business_area, project_keys)
    top_recommendation = recommendations[0] if recommendations else None
    showback = _compute_showback(pricing_on, ledger_on, project_key, agent_name, start, end, business_area, project_keys)

    series_cost_by_day = []
    series_cost_by_source = []
    series_cost_by_model = []

    if ledger_on:
        where_l = _compose_where(
            ["l.occurred_at >= %(start)s", "l.occurred_at < %(end)s"] + _build_filter_clauses(project_key, agent_name, "l", business_area, project_keys)
        )
        series_cost_by_day = fetch_all(
            f"""
            select date_trunc('day', l.occurred_at) as time,
                   coalesce(sum(l.amount_brl),0)::numeric(14,4) as value
            from finops.cost_ledger l
            where {where_l}
            group by 1
            order by 1
            """,
            params,
        )
        series_cost_by_source = fetch_all(
            f"""
            select l.source_type as source,
                   coalesce(sum(l.amount_brl),0)::numeric(14,4) as cost
            from finops.cost_ledger l
            where {where_l}
            group by 1
            order by cost desc
            """,
            params,
        )
        series_cost_by_model = fetch_all(
            f"""
            select l.model as model,
                   coalesce(sum(l.amount_brl),0)::numeric(14,4) as cost
            from finops.cost_ledger l
            where {where_l}
            group by 1
            order by cost desc
            """,
            params,
        )
    elif pricing_on:
        series_cost_by_day = fetch_all(
            f"""
            select date_trunc('day', u.created_at) as time,
                   coalesce(sum((u.total_tokens/1000.0) * p.cost_per_1k_tokens_brl),0)::numeric(14,4) as value
            from finops.interaction_usage u
            join finops.model_pricing p
              on p.model = u.model
             and u.created_at >= p.valid_from
             and (p.valid_to is null or u.created_at < p.valid_to)
            where {where_u}
            group by 1
            order by 1
            """,
            params,
        )
        series_cost_by_model = fetch_all(
            f"""
            select u.model as model,
                   coalesce(sum((u.total_tokens/1000.0) * p.cost_per_1k_tokens_brl),0)::numeric(14,4) as cost
            from finops.interaction_usage u
            join finops.model_pricing p
              on p.model = u.model
             and u.created_at >= p.valid_from
             and (p.valid_to is null or u.created_at < p.valid_to)
            where {where_u}
            group by 1
            order by cost desc
            """,
            params,
        )
        try:
            series_cost_by_source = fetch_all(
                f"""
                select coalesce(u.source_type,'MANUAL') as source,
                       coalesce(sum((u.total_tokens/1000.0) * p.cost_per_1k_tokens_brl),0)::numeric(14,4) as cost
                from finops.interaction_usage u
                join finops.model_pricing p
                  on p.model = u.model
                 and u.created_at >= p.valid_from
                 and (p.valid_to is null or u.created_at < p.valid_to)
                where {where_u}
                group by 1
                order by cost desc
                """,
                params,
            )
        except Exception:
            series_cost_by_source = []

    top_tasks = []
    top_flows = []
    if ledger_on:
        where_l_auto = _compose_where(
            ["l.occurred_at >= %(start)s", "l.occurred_at < %(end)s", "l.source_type='AUTOMATION'"] + _build_filter_clauses(project_key, agent_name, "l", business_area, project_keys)
        )
        top_tasks = fetch_all(
            f"""
            select coalesce(l.task_id,'-') as task_name,
                   coalesce(sum(l.amount_brl),0)::numeric(14,4) as cost
            from finops.cost_ledger l
            where {where_l_auto}
            group by 1
            order by cost desc
            limit 12
            """,
            params,
        )
        top_flows = fetch_all(
            f"""
            select coalesce(l.flow_id,'-') as flow_name,
                   coalesce(sum(l.amount_brl),0)::numeric(14,4) as cost
            from finops.cost_ledger l
            where {where_l_auto}
            group by 1
            order by cost desc
            limit 12
            """,
            params,
        )

    interaction_rows = fetch_all(
        f"""
        select
          u.created_at as occurred_at,
          coalesce(u.source_type,'MANUAL') as source_type,
          u.model,
          u.total_tokens as tokens_total,
          coalesce(u.actor_type,'USER') as actor_type,
          u.conversation_id,
          u.task_id,
          u.flow_id
        from finops.interaction_usage u
        where {where_u}
        order by u.created_at desc
        limit 500
        """,
        params,
    )

    ledger_rows = []
    if ledger_on:
        where_l = _compose_where(
            ["l.occurred_at >= %(start)s", "l.occurred_at < %(end)s"] + _build_filter_clauses(project_key, agent_name, "l", business_area, project_keys)
        )
        ledger_rows = fetch_all(
            f"""
            select
              l.occurred_at,
              l.source_type,
              l.model,
              l.tokens_billed,
              l.amount_brl as amount,
              l.task_id,
              l.flow_id,
              l.idempotency_key
            from finops.cost_ledger l
            where {where_l}
            order by l.occurred_at desc
            limit 500
            """,
            params,
        )
    elif pricing_on:
        ledger_rows = fetch_all(
            f"""
            select
              u.created_at as occurred_at,
              coalesce(u.source_type,'MANUAL') as source_type,
              u.model,
              u.total_tokens as tokens_billed,
              ((u.total_tokens/1000.0) * p.cost_per_1k_tokens_brl)::numeric(14,4) as amount,
              u.task_id,
              u.flow_id,
              concat('USAGE|',u.interaction_id,'|',u.created_at) as idempotency_key
            from finops.interaction_usage u
            join finops.model_pricing p
              on p.model = u.model
             and u.created_at >= p.valid_from
             and (p.valid_to is null or u.created_at < p.valid_to)
            where {where_u}
            order by u.created_at desc
            limit 500
            """,
            params,
        )

    acc_rows = []
    if acc_on:
        acc_rows = fetch_all(
            """
            select bucket_type, model, bucket_key, pending_tokens, close_count, updated_at
            from finops.billing_accumulator
            order by updated_at desc
            limit 200
            """
        )

    pricing_rows = []
    if pricing_on:
        pricing_rows = fetch_all(
            """
            select model, cost_per_1k_tokens_brl, min_tokens, min_cost_brl, valid_from, valid_to
            from finops.model_pricing
            order by model, valid_from desc
            """
        )

    dataset = {
        "data_source": "real",
        "scenario": "real",
        "days": int(days),
        "kpis": {
            "total_cost": round(float(total_cost_brl), 2),
            "manual_cost": round(float(manual_cost_brl), 2),
            "automation_cost": round(float(automation_cost_brl), 2),
            "interaction_rows": int(kpi_usage["interaction_rows"] or 0),
            "ledger_rows": int(ledger_rows_count or len(ledger_rows)),
            "estimated_savings_brl": round(estimated_savings_brl, 2),
            "net_value_brl": round(net_value_brl, 2),
            "roi_percent": round(roi_percent, 2),
            "avg_latency_ms": round(_safe_float(kpi_usage["avg_latency_ms"]), 2),
            "cache_hit_rate": round(_safe_float(kpi_usage["cache_hit_rate"]) * 100.0, 2),
            **budget_metrics,
        },
        "series": {
            "cost_by_day": [{"time": r["time"], "value": r["value"]} for r in (series_cost_by_day or [])],
            "cost_by_source": [{"source": r.get("source"), "cost": r.get("cost")} for r in (series_cost_by_source or [])],
            "cost_by_model": [{"model": r.get("model"), "cost": r.get("cost")} for r in (series_cost_by_model or [])],
            "top_tasks": top_tasks,
            "top_flows": top_flows,
        },
        "tables": {
            "interaction_rows": interaction_rows,
            "ledger_rows": ledger_rows,
            "accumulators": acc_rows,
            "pricing": pricing_rows,
        },
        "recommendations": recommendations,
        "showback": showback,
        "source": "real",
        "filters": {
            "days": int(days),
            "project_key": project_key,
            "project_keys": project_keys,
            "agent_name": agent_name,
            "business_area": business_area,
            "pricing_enabled": bool(pricing_on),
            "ledger_enabled": bool(ledger_on),
            "accumulators_enabled": bool(acc_on),
            "data_source": "real",
        },
        "alerts": {
            "budget_alert": bool(budget_metrics["budget_alert_triggered"]),
            "forecast_alert": bool(budget_metrics["budget_forecast_alert_triggered"]),
            "messages": [
                m
                for m in [
                    f"Budget mensal acima de {int(BUDGET_ALERT_PERCENT)}%."
                    if budget_metrics["budget_alert_triggered"] else None,
                    "Forecast mensal acima do orçamento."
                    if budget_metrics["budget_forecast_alert_triggered"] else None,
                ]
                if m
            ],
        },
        "optimization": {
            "top_recommendation": top_recommendation,
        },
        "grafana_embed_url": None,
    }
    return dataset


def get_finops_filter_options(days: int = 30, project_keys: list[str] | None = None) -> dict:
    """Options for the real React/Node frontend filters."""
    start, end = _parse_days(days)
    params = {"start": start, "end": end}
    if project_keys is not None:
        params["project_keys"] = project_keys
    project_filter_sql = ""
    if project_keys is not None:
        project_filter_sql = " and u.project_key = ANY(%(project_keys)s)" if project_keys else " and 1=0"

    options = {"periods": [7, 15, 30, 60, 90], "business_areas": [], "project_keys": [], "agents": []}

    if table_exists("finops", "interaction_usage"):
        area_expr_u = _business_area_expr("u")
        if area_expr_u != "NULL":
            options["business_areas"] = fetch_all(
                f"""
                select distinct {area_expr_u} as value
                from finops.interaction_usage u
                where u.created_at >= %(start)s and u.created_at < %(end)s
                  {project_filter_sql}
                  and {area_expr_u} is not null and trim({area_expr_u}::text) <> ''
                order by 1
                """,
                params,
            )
        if _column_exists("finops", "interaction_usage", "project_key"):
            options["project_keys"] = fetch_all(
                f"""
                select distinct u.project_key as value
                from finops.interaction_usage u
                where u.created_at >= %(start)s and u.created_at < %(end)s
                  {project_filter_sql}
                  and u.project_key is not null and trim(u.project_key::text) <> ''
                order by 1
                """,
                params,
            )
        if _column_exists("finops", "interaction_usage", "agent_name"):
            options["agents"] = fetch_all(
                f"""
                select distinct u.agent_name as value
                from finops.interaction_usage u
                where u.created_at >= %(start)s and u.created_at < %(end)s
                  {project_filter_sql}
                  and u.agent_name is not null and trim(u.agent_name::text) <> ''
                order by 1
                """,
                params,
            )

    # Normalize rows to plain strings for the frontend.
    for key in ("business_areas", "project_keys", "agents"):
        options[key] = [str(r.get("value")) for r in options[key] if r.get("value") is not None]
    return options


def _agent_rows_from_showback(dataset: dict, limit: int | None = None) -> list[dict]:
    total = _safe_float(dataset.get("kpis", {}).get("total_cost"))
    rows = []
    for i, row in enumerate(dataset.get("showback", {}).get("by_agent", []) or [], start=1):
        amount = _safe_float(row.get("amount_brl"))
        rows.append({
            "rank": i,
            "agent_name": row.get("label") or "N/A",
            "total_cost_brl": round(amount, 2),
            "cost_percent": round((amount / total * 100.0) if total > 0 else 0.0, 2),
        })
    return rows[:limit] if limit else rows


def get_cost_by_agent(days: int = 30, project_key: str | None = None, agent_name: str | None = None, business_area: str | None = None, limit: int = 20, project_keys: list[str] | None = None) -> dict:
    dataset = summarize_real(days=days, project_key=project_key, agent_name=agent_name, business_area=business_area, project_keys=project_keys)
    return {
        "filters": dataset.get("filters", {}),
        "total_cost_brl": dataset.get("kpis", {}).get("total_cost", 0),
        "agents": _agent_rows_from_showback(dataset, limit=limit),
    }


def get_hero_fold(days: int = 30, project_key: str | None = None, agent_name: str | None = None, business_area: str | None = None, project_keys: list[str] | None = None) -> dict:
    dataset = summarize_real(days=days, project_key=project_key, agent_name=agent_name, business_area=business_area, project_keys=project_keys)
    kpis = dataset.get("kpis", {})
    agents = _agent_rows_from_showback(dataset, limit=999)
    top3_cost = sum(_safe_float(a.get("total_cost_brl")) for a in agents[:3])
    total_cost = _safe_float(kpis.get("total_cost"))
    top3_percent = (top3_cost / total_cost * 100.0) if total_cost > 0 else 0.0

    # Lacunas críticas para fechar o ciclo FinOps executivo.
    gaps = []
    if not dataset.get("series", {}).get("top_tasks"):
        gaps.append("cost_per_task")
    if not dataset.get("series", {}).get("top_flows"):
        gaps.append("cost_per_process")
    if not kpis.get("budget_forecast_brl"):
        gaps.append("forecast")
    if not dataset.get("alerts", {}).get("messages"):
        gaps.append("deviation_alerts")

    return {
        "filters": dataset.get("filters", {}),
        "cards": {
            "current_cost": {
                "label": "Concentração no Top 3",
                "value_percent": round(top3_percent, 2),
                "value_brl": round(top3_cost, 2),
                "description": "Percentual do custo concentrado nos três agentes de maior gasto.",
            },
            "delivered_value": {
                "label": "ROI precisa de contexto",
                "value_percent": round(_safe_float(kpis.get("roi_percent")), 2),
                "description": "ROI calculado pela economia estimada menos custo total, dividido pelo custo total.",
            },
            "forecast": {
                "label": "Lacunas críticas do painel",
                "value": len(gaps),
                "items": gaps,
                "description": "Dimensões ainda ausentes ou incompletas para tomada de decisão executiva.",
            },
        },
        "agent_concentration": agents[:3],
    }


def ingest_usage(payload: dict) -> dict:
    required = [
        "interaction_id",
        "session_id",
        "project_key",
        "agent_name",
        "model",
        "input_tokens",
        "output_tokens",
        "total_tokens",
    ]
    for k in required:
        if payload.get(k) is None or str(payload.get(k)) == "":
            return {"ok": False, "error": f"missing_field:{k}"}

    rows = execute_returning(
        """
        insert into finops.interaction_usage (
          interaction_id, session_id, project_key, agent_name, model,
          input_tokens, output_tokens, total_tokens,
          latency_ms, cached, created_at,
          source_type, actor_type, conversation_id, task_id, flow_id
        ) values (
          %(interaction_id)s, %(session_id)s, %(project_key)s, %(agent_name)s, %(model)s,
          %(input_tokens)s, %(output_tokens)s, %(total_tokens)s,
          %(latency_ms)s, %(cached)s, coalesce(%(created_at)s, now()),
          coalesce(%(source_type)s,'MANUAL'),
          coalesce(%(actor_type)s,'USER'),
          %(conversation_id)s, %(task_id)s, %(flow_id)s
        )
        returning id
        """,
        {
            "interaction_id": str(payload["interaction_id"]),
            "session_id": str(payload["session_id"]),
            "project_key": str(payload["project_key"]),
            "agent_name": str(payload["agent_name"]),
            "model": str(payload["model"]),
            "input_tokens": int(payload["input_tokens"]),
            "output_tokens": int(payload["output_tokens"]),
            "total_tokens": int(payload["total_tokens"]),
            "latency_ms": int(payload["latency_ms"]) if payload.get("latency_ms") is not None else None,
            "cached": bool(payload.get("cached") or False),
            "created_at": payload.get("created_at"),
            "source_type": payload.get("source_type"),
            "actor_type": payload.get("actor_type"),
            "conversation_id": payload.get("conversation_id"),
            "task_id": payload.get("task_id"),
            "flow_id": payload.get("flow_id"),
        },
    )
    new_id = rows[0]["id"] if rows else None
    return {"ok": True, "id": new_id}


def upsert_pricing(
    model: str,
    cost_per_1k_tokens_brl: float,
    min_tokens: int | None = None,
    min_cost_brl: float | None = None,
) -> None:
    now = datetime.now(timezone.utc)

    execute(
        """
        update finops.model_pricing
           set valid_to = %(now)s
         where model = %(model)s
           and valid_to is null
        """,
        {"now": now, "model": model},
    )

    execute(
        """
        insert into finops.model_pricing (model, cost_per_1k_tokens_brl, min_tokens, min_cost_brl, valid_from, valid_to)
        values (%(model)s, %(cost)s, %(min_tokens)s, %(min_cost)s, %(now)s, null)
        """,
        {
            "model": model,
            "cost": float(cost_per_1k_tokens_brl),
            "min_tokens": int(min_tokens) if min_tokens is not None else None,
            "min_cost": float(min_cost_brl) if min_cost_brl is not None else None,
            "now": now,
        },
    )
