from __future__ import annotations

import os
from calendar import monthrange
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Any

from db import column_exists, execute, execute_returning, fetch_all, fetch_one, table_exists


ROI_HOURLY_RATE_BRL = float(os.getenv("ROI_HOURLY_RATE_BRL", "45"))
ROI_MINUTES_SAVED_PER_INTERACTION = float(os.getenv("ROI_MINUTES_SAVED_PER_INTERACTION", "6"))
BUDGET_MONTHLY_BRL = float(os.getenv("BUDGET_MONTHLY_BRL", "10000"))
BUDGET_ALERT_PERCENT = float(os.getenv("BUDGET_ALERT_PERCENT", "80"))
MODEL_MIGRATION_RATIO = float(os.getenv("MODEL_MIGRATION_RATIO", "0.30"))
DEFAULT_TENANT_ID = os.getenv("FINOPS_TENANT_ID", "spread")
DEFAULT_GRAFANA_EMBED_URL = os.getenv("GRAFANA_EMBED_URL", "")

CHEAPER_MODEL_MAP = {
    "gpt-5.1": "gpt-4o",
    "gpt-5": "gpt-4o",
    "gpt-5.0": "gpt-4o",
    "gpt-4.1": "gpt-4o-mini",
    "gpt-4.1-turbo": "gpt-4o-mini",
    "gpt-4o": "gpt-4o-mini",
}


@dataclass(frozen=True)
class SchemaCaps:
    usage_table: str
    usage_time_col: str
    usage_tokens_col: str
    usage_has_actor_type: bool
    usage_has_project_key: bool
    usage_has_agent_name: bool
    pricing_has_cost_per_1k: bool
    pricing_has_tenant_id: bool
    ledger_has_tenant_id: bool
    accumulators_has_tenant_id: bool


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value or 0)
    except Exception:
        return default


def _parse_days(days: int) -> tuple[datetime, datetime]:
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=max(1, int(days)))
    return start, end


def _month_bounds(now: datetime) -> tuple[datetime, datetime]:
    start = datetime(now.year, now.month, 1, tzinfo=timezone.utc)
    last_day = monthrange(now.year, now.month)[1]
    end = datetime(now.year, now.month, last_day, 23, 59, 59, 999999, tzinfo=timezone.utc) + timedelta(microseconds=1)
    return start, end


@lru_cache(maxsize=1)
def _schema_caps() -> SchemaCaps:
    usage_table = "interaction_usage" if table_exists("finops", "interaction_usage") else "interaction"
    usage_time_col = "created_at" if column_exists("finops", usage_table, "created_at") else "occurred_at"
    usage_tokens_col = "total_tokens" if column_exists("finops", usage_table, "total_tokens") else "tokens_total"
    return SchemaCaps(
        usage_table=usage_table,
        usage_time_col=usage_time_col,
        usage_tokens_col=usage_tokens_col,
        usage_has_actor_type=column_exists("finops", usage_table, "actor_type"),
        usage_has_project_key=column_exists("finops", usage_table, "project_key"),
        usage_has_agent_name=column_exists("finops", usage_table, "agent_name"),
        pricing_has_cost_per_1k=column_exists("finops", "model_pricing", "cost_per_1k_tokens_brl"),
        pricing_has_tenant_id=column_exists("finops", "model_pricing", "tenant_id"),
        ledger_has_tenant_id=column_exists("finops", "cost_ledger", "tenant_id"),
        accumulators_has_tenant_id=column_exists("finops", "billing_accumulator", "tenant_id"),
    )


def _pricing_cost_expr(alias: str = "p") -> str:
    caps = _schema_caps()
    if caps.pricing_has_cost_per_1k:
        return f"{alias}.cost_per_1k_tokens_brl"
    return f"(({alias}.min_cost_brl / nullif({alias}.min_tokens, 0)) * 1000.0)"


def _base_params(start: datetime | None = None, end: datetime | None = None) -> dict[str, Any]:
    params: dict[str, Any] = {"tenant_id": DEFAULT_TENANT_ID}
    if start is not None:
        params["start"] = start
    if end is not None:
        params["end"] = end
    return params


def _usage_where(alias: str, start: datetime, end: datetime, project_key: str | None, agent_name: str | None) -> tuple[str, dict[str, Any]]:
    caps = _schema_caps()
    params = _base_params(start, end)
    clauses = [
        f"{alias}.{caps.usage_time_col} >= %(start)s",
        f"{alias}.{caps.usage_time_col} < %(end)s",
    ]
    if DEFAULT_TENANT_ID and column_exists("finops", caps.usage_table, "tenant_id"):
        clauses.append(f"{alias}.tenant_id = %(tenant_id)s")
    if project_key and caps.usage_has_project_key:
        clauses.append(f"{alias}.project_key = %(project_key)s")
        params["project_key"] = project_key
    if agent_name and caps.usage_has_agent_name:
        clauses.append(f"{alias}.agent_name = %(agent_name)s")
        params["agent_name"] = agent_name
    return " and ".join(clauses), params


def _ledger_where(alias: str, start: datetime, end: datetime) -> tuple[str, dict[str, Any]]:
    params = _base_params(start, end)
    clauses = [f"{alias}.occurred_at >= %(start)s", f"{alias}.occurred_at < %(end)s"]
    if DEFAULT_TENANT_ID and _schema_caps().ledger_has_tenant_id:
        clauses.append(f"{alias}.tenant_id = %(tenant_id)s")
    return " and ".join(clauses), params


def _pricing_where(alias: str = "p") -> tuple[str, dict[str, Any]]:
    params = _base_params()
    clauses = ["1=1"]
    if DEFAULT_TENANT_ID and _schema_caps().pricing_has_tenant_id:
        clauses.append(f"{alias}.tenant_id = %(tenant_id)s")
    return " and ".join(clauses), params


def _accumulator_where(alias: str = "a") -> tuple[str, dict[str, Any]]:
    params = _base_params()
    clauses = ["1=1"]
    if DEFAULT_TENANT_ID and _schema_caps().accumulators_has_tenant_id:
        clauses.append(f"{alias}.tenant_id = %(tenant_id)s")
    return " and ".join(clauses), params


def _empty_dataset(days: int, error_message: str | None = None) -> dict[str, Any]:
    messages = [error_message] if error_message else []
    return {
        "tenant_id": DEFAULT_TENANT_ID,
        "data_source": "real",
        "scenario": "real",
        "days": int(days),
        "kpis": {
            "total_cost": 0.0,
            "manual_cost": 0.0,
            "automation_cost": 0.0,
            "interaction_rows": 0,
            "ledger_rows": 0,
            "estimated_savings_brl": 0.0,
            "net_value_brl": 0.0,
            "roi_percent": 0.0,
            "avg_latency_ms": 0.0,
            "cache_hit_rate": 0.0,
            "budget_monthly_brl": round(BUDGET_MONTHLY_BRL, 2),
            "budget_used_brl": 0.0,
            "budget_used_percent": 0.0,
            "budget_forecast_brl": 0.0,
            "budget_forecast_percent": 0.0,
            "budget_alert_threshold_percent": BUDGET_ALERT_PERCENT,
        },
        "series": {
            "cost_by_day": [],
            "cost_by_source": [],
            "cost_by_model": [],
            "top_tasks": [],
            "top_flows": [],
        },
        "tables": {
            "interaction_rows": [],
            "ledger_rows": [],
            "accumulators": [],
            "pricing": [],
        },
        "recommendations": [],
        "showback": {
            "by_project": [],
            "by_agent": [],
            "by_source": [],
        },
        "source": "real",
        "filters": {
            "days": int(days),
            "project_key": None,
            "agent_name": None,
            "pricing_enabled": False,
            "ledger_enabled": False,
            "accumulators_enabled": False,
            "data_source": "real",
        },
        "alerts": {
            "budget_alert": False,
            "forecast_alert": False,
            "messages": messages,
        },
        "optimization": {
            "top_recommendation": None,
        },
        "grafana_embed_url": DEFAULT_GRAFANA_EMBED_URL,
    }


def _compute_budget_metrics(pricing_on: bool, ledger_on: bool, project_key: str | None, agent_name: str | None) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    month_start, month_end = _month_bounds(now)
    month_cost = 0.0

    if ledger_on:
        where_l, params = _ledger_where("l", month_start, month_end)
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
        caps = _schema_caps()
        where_u, params = _usage_where("u", month_start, month_end, project_key, agent_name)
        row = fetch_one(
            f"""
            select coalesce(sum((u.{caps.usage_tokens_col}/1000.0) * {_pricing_cost_expr('p')}), 0)::numeric(14,4) as total_cost
              from finops.{caps.usage_table} u
              join finops.model_pricing p
                on p.model = u.model
               and u.{caps.usage_time_col} >= p.valid_from
               and (p.valid_to is null or u.{caps.usage_time_col} < p.valid_to)
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


def _current_pricing_rows() -> list[dict[str, Any]]:
    where_p, params = _pricing_where("p")
    return fetch_all(
        f"""
        select distinct on (p.model)
               p.model,
               p.min_tokens,
               p.min_cost_brl,
               {_pricing_cost_expr('p')}::numeric(14,4) as cost_per_1k_tokens_brl
          from finops.model_pricing p
         where {where_p}
         order by p.model, p.valid_from desc
        """,
        params,
    )


def _current_pricing_map() -> dict[str, float]:
    return {row["model"]: _safe_float(row["cost_per_1k_tokens_brl"]) for row in _current_pricing_rows()}


def _compute_recommendations(pricing_on: bool, project_key: str | None, agent_name: str | None, start: datetime, end: datetime) -> list[dict[str, Any]]:
    if not pricing_on:
        return []

    caps = _schema_caps()
    where_u, usage_params = _usage_where("u", start, end, project_key, agent_name)
    where_p, pricing_params = _pricing_where("p")
    rows = fetch_all(
        f"""
        with current_pricing as (
          select distinct on (p.model)
                 p.model,
                 {_pricing_cost_expr('p')}::numeric(14,4) as cost_per_1k_tokens_brl
            from finops.model_pricing p
           where {where_p}
           order by p.model, p.valid_from desc
        )
        select
          u.model,
          coalesce(sum(u.{caps.usage_tokens_col}), 0)::numeric(14,2) as total_tokens,
          cp.cost_per_1k_tokens_brl
          from finops.{caps.usage_table} u
          join current_pricing cp on cp.model = u.model
         where {where_u}
         group by u.model, cp.cost_per_1k_tokens_brl
         order by total_tokens desc
        """,
        {**usage_params, **pricing_params},
    )

    pricing_map = _current_pricing_map()
    recommendations: list[dict[str, Any]] = []

    for row in rows:
        model = row["model"]
        target = CHEAPER_MODEL_MAP.get(model)
        current_price = _safe_float(row["cost_per_1k_tokens_brl"])
        target_price = pricing_map.get(target) if target else None
        if not target or target_price is None or target_price >= current_price:
            continue

        total_tokens = _safe_float(row["total_tokens"])
        current_cost = (total_tokens / 1000.0) * current_price
        target_cost = (total_tokens / 1000.0) * target_price
        estimated_saving = max(current_cost - target_cost, 0.0) * MODEL_MIGRATION_RATIO
        if estimated_saving <= 0:
            continue

        recommendations.append(
            {
                "current_model": model,
                "suggested_model": target,
                "coverage_percent": round(MODEL_MIGRATION_RATIO * 100.0, 1),
                "observed_tokens": round(total_tokens, 0),
                "current_cost_brl": round(current_cost, 2),
                "target_cost_brl": round(target_cost, 2),
                "estimated_savings_brl": round(estimated_saving, 2),
                "summary": f"Migrar ~{round(MODEL_MIGRATION_RATIO * 100)}% do uso de {model} para {target}",
            }
        )

    recommendations.sort(key=lambda item: item["estimated_savings_brl"], reverse=True)
    return recommendations[:3]


def _compute_showback(start: datetime, end: datetime) -> dict[str, Any]:
    where_l, params = _ledger_where("l", start, end)
    by_source = fetch_all(
        f"""
        select coalesce(l.source_type, 'N/A') as label,
               coalesce(sum(l.amount_brl), 0)::numeric(14,4) as amount_brl
          from finops.cost_ledger l
         where {where_l}
         group by 1
         order by amount_brl desc
        """,
        params,
    )

    by_agent: list[dict[str, Any]] = []
    if column_exists("finops", "cost_ledger", "collaborator_id"):
        by_agent = fetch_all(
            f"""
            select coalesce(l.collaborator_id, 'N/A') as label,
                   coalesce(sum(l.amount_brl), 0)::numeric(14,4) as amount_brl
              from finops.cost_ledger l
             where {where_l}
             group by 1
             order by amount_brl desc
             limit 10
            """,
            params,
        )

    return {
        "by_project": [],
        "by_agent": [{"label": row["label"], "amount_brl": round(_safe_float(row["amount_brl"]), 2)} for row in by_agent],
        "by_source": [{"label": row["label"], "amount_brl": round(_safe_float(row["amount_brl"]), 2)} for row in by_source],
    }


def summarize_real(days: int = 30, project_key: str | None = None, agent_name: str | None = None) -> dict[str, Any]:
    try:
        caps = _schema_caps()
        pricing_on = table_exists("finops", "model_pricing")
        ledger_on = table_exists("finops", "cost_ledger")
        accumulators_on = table_exists("finops", "billing_accumulator")
        usage_on = table_exists("finops", caps.usage_table)
    except Exception as exc:
        return _empty_dataset(days, f"Modo real indisponível: {exc}")

    if not usage_on and not ledger_on:
        return _empty_dataset(days, "Modo real sem tabelas carregadas no Postgres.")

    try:
        start, end = _parse_days(days)
        where_u, usage_params = _usage_where("u", start, end, project_key, agent_name)
        latency_expr = "u.latency_ms" if column_exists("finops", caps.usage_table, "latency_ms") else "null::numeric"
        cache_expr = "case when u.cached then 1 else 0 end" if column_exists("finops", caps.usage_table, "cached") else "0::numeric"
        kpi_usage = fetch_one(
            f"""
            select
              count(*)::int as interaction_rows,
              coalesce(sum(u.{caps.usage_tokens_col}), 0)::bigint as total_tokens,
              coalesce(avg({latency_expr}), 0)::numeric(12,2) as avg_latency_ms,
              coalesce(avg({cache_expr}), 0)::numeric(12,4) as cache_hit_rate
              from finops.{caps.usage_table} u
             where {where_u}
            """,
            usage_params,
        ) or {"interaction_rows": 0, "total_tokens": 0, "avg_latency_ms": 0, "cache_hit_rate": 0}

        total_cost_brl = 0.0
        manual_cost_brl = 0.0
        automation_cost_brl = 0.0
        ledger_rows_count = 0

        if ledger_on:
            where_l, ledger_params = _ledger_where("l", start, end)
            kpi_ledger = fetch_one(
                f"""
                select
                  count(*)::int as ledger_rows,
                  coalesce(sum(l.amount_brl), 0)::numeric(14,4) as total_cost,
                  coalesce(sum(case when l.source_type='MANUAL' then l.amount_brl else 0 end), 0)::numeric(14,4) as manual_cost,
                  coalesce(sum(case when l.source_type='AUTOMATION' then l.amount_brl else 0 end), 0)::numeric(14,4) as automation_cost
                  from finops.cost_ledger l
                 where {where_l}
                """,
                ledger_params,
            ) or {"ledger_rows": 0, "total_cost": 0, "manual_cost": 0, "automation_cost": 0}
            ledger_rows_count = int(kpi_ledger["ledger_rows"] or 0)
            total_cost_brl = _safe_float(kpi_ledger["total_cost"])
            manual_cost_brl = _safe_float(kpi_ledger["manual_cost"])
            automation_cost_brl = _safe_float(kpi_ledger["automation_cost"])
        elif pricing_on:
            cost_row = fetch_one(
                f"""
                select coalesce(sum((u.{caps.usage_tokens_col}/1000.0) * {_pricing_cost_expr('p')}), 0)::numeric(14,4) as total_cost,
                       coalesce(sum(case when u.source_type='MANUAL' then (u.{caps.usage_tokens_col}/1000.0) * {_pricing_cost_expr('p')} else 0 end), 0)::numeric(14,4) as manual_cost,
                       coalesce(sum(case when u.source_type='AUTOMATION' then (u.{caps.usage_tokens_col}/1000.0) * {_pricing_cost_expr('p')} else 0 end), 0)::numeric(14,4) as automation_cost
                  from finops.{caps.usage_table} u
                  join finops.model_pricing p
                    on p.model = u.model
                   and u.{caps.usage_time_col} >= p.valid_from
                   and (p.valid_to is null or u.{caps.usage_time_col} < p.valid_to)
                 where {where_u}
                """,
                usage_params,
            ) or {"total_cost": 0, "manual_cost": 0, "automation_cost": 0}
            total_cost_brl = _safe_float(cost_row["total_cost"])
            manual_cost_brl = _safe_float(cost_row["manual_cost"])
            automation_cost_brl = _safe_float(cost_row["automation_cost"])

        estimated_savings_brl = (
            int(kpi_usage["interaction_rows"] or 0)
            * (ROI_MINUTES_SAVED_PER_INTERACTION / 60.0)
            * ROI_HOURLY_RATE_BRL
        )
        net_value_brl = estimated_savings_brl - total_cost_brl
        roi_percent = ((net_value_brl / total_cost_brl) * 100.0) if total_cost_brl > 0 else 0.0

        budget_metrics = _compute_budget_metrics(pricing_on, ledger_on, project_key, agent_name)
        recommendations = _compute_recommendations(pricing_on, project_key, agent_name, start, end)
        top_recommendation = recommendations[0] if recommendations else None
        showback = _compute_showback(start, end) if ledger_on else {"by_project": [], "by_agent": [], "by_source": []}

        series_cost_by_day: list[dict[str, Any]] = []
        series_cost_by_source: list[dict[str, Any]] = []
        series_cost_by_model: list[dict[str, Any]] = []
        top_tasks: list[dict[str, Any]] = []
        top_flows: list[dict[str, Any]] = []

        if ledger_on:
            where_l, ledger_params = _ledger_where("l", start, end)
            series_cost_by_day = fetch_all(
                f"""
                select date_trunc('day', l.occurred_at) as time,
                       coalesce(sum(l.amount_brl), 0)::numeric(14,4) as value
                  from finops.cost_ledger l
                 where {where_l}
                 group by 1
                 order by 1
                """,
                ledger_params,
            )
            series_cost_by_source = fetch_all(
                f"""
                select l.source_type as source,
                       coalesce(sum(l.amount_brl), 0)::numeric(14,4) as cost
                  from finops.cost_ledger l
                 where {where_l}
                 group by 1
                 order by cost desc
                """,
                ledger_params,
            )
            series_cost_by_model = fetch_all(
                f"""
                select l.model as model,
                       coalesce(sum(l.amount_brl), 0)::numeric(14,4) as cost
                  from finops.cost_ledger l
                 where {where_l}
                 group by 1
                 order by cost desc
                """,
                ledger_params,
            )
            top_tasks = fetch_all(
                f"""
                select coalesce(l.task_id, '-') as task_name,
                       coalesce(sum(l.amount_brl), 0)::numeric(14,4) as cost
                  from finops.cost_ledger l
                 where {where_l} and l.source_type = 'AUTOMATION'
                 group by 1
                 order by cost desc
                 limit 12
                """,
                ledger_params,
            )
            top_flows = fetch_all(
                f"""
                select coalesce(l.flow_id, '-') as flow_name,
                       coalesce(sum(l.amount_brl), 0)::numeric(14,4) as cost
                  from finops.cost_ledger l
                 where {where_l} and l.source_type = 'AUTOMATION'
                 group by 1
                 order by cost desc
                 limit 12
                """,
                ledger_params,
            )
        elif pricing_on:
            series_cost_by_day = fetch_all(
                f"""
                select date_trunc('day', u.{caps.usage_time_col}) as time,
                       coalesce(sum((u.{caps.usage_tokens_col}/1000.0) * {_pricing_cost_expr('p')}), 0)::numeric(14,4) as value
                  from finops.{caps.usage_table} u
                  join finops.model_pricing p
                    on p.model = u.model
                   and u.{caps.usage_time_col} >= p.valid_from
                   and (p.valid_to is null or u.{caps.usage_time_col} < p.valid_to)
                 where {where_u}
                 group by 1
                 order by 1
                """,
                usage_params,
            )
            series_cost_by_source = fetch_all(
                f"""
                select u.source_type as source,
                       coalesce(sum((u.{caps.usage_tokens_col}/1000.0) * {_pricing_cost_expr('p')}), 0)::numeric(14,4) as cost
                  from finops.{caps.usage_table} u
                  join finops.model_pricing p
                    on p.model = u.model
                   and u.{caps.usage_time_col} >= p.valid_from
                   and (p.valid_to is null or u.{caps.usage_time_col} < p.valid_to)
                 where {where_u}
                 group by 1
                 order by cost desc
                """,
                usage_params,
            )
            series_cost_by_model = fetch_all(
                f"""
                select u.model as model,
                       coalesce(sum((u.{caps.usage_tokens_col}/1000.0) * {_pricing_cost_expr('p')}), 0)::numeric(14,4) as cost
                  from finops.{caps.usage_table} u
                  join finops.model_pricing p
                    on p.model = u.model
                   and u.{caps.usage_time_col} >= p.valid_from
                   and (p.valid_to is null or u.{caps.usage_time_col} < p.valid_to)
                 where {where_u}
                 group by 1
                 order by cost desc
                """,
                usage_params,
            )

        actor_expr = (
            "coalesce(u.actor_type, case when u.source_type='MANUAL' then 'COLLABORATOR' else 'AGENT' end)"
            if caps.usage_has_actor_type
            else "case when u.source_type='MANUAL' then 'COLLABORATOR' else 'AGENT' end"
        )
        interaction_rows = fetch_all(
            f"""
            select
              u.{caps.usage_time_col} as occurred_at,
              coalesce(u.source_type, 'MANUAL') as source_type,
              u.model,
              u.{caps.usage_tokens_col} as tokens_total,
              {actor_expr} as actor_type,
              u.conversation_id,
              u.task_id,
              u.flow_id
              from finops.{caps.usage_table} u
             where {where_u}
             order by u.{caps.usage_time_col} desc
             limit 500
            """,
            usage_params,
        )

        ledger_rows: list[dict[str, Any]] = []
        if ledger_on:
            where_l, ledger_params = _ledger_where("l", start, end)
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
                ledger_params,
            )
        elif pricing_on:
            ledger_rows = fetch_all(
                f"""
                select
                  u.{caps.usage_time_col} as occurred_at,
                  u.source_type,
                  u.model,
                  u.{caps.usage_tokens_col} as tokens_billed,
                  ((u.{caps.usage_tokens_col}/1000.0) * {_pricing_cost_expr('p')})::numeric(14,4) as amount,
                  u.task_id,
                  u.flow_id,
                  concat('USAGE|', coalesce(u.conversation_id, 'na'), '|', u.{caps.usage_time_col}) as idempotency_key
                  from finops.{caps.usage_table} u
                  join finops.model_pricing p
                    on p.model = u.model
                   and u.{caps.usage_time_col} >= p.valid_from
                   and (p.valid_to is null or u.{caps.usage_time_col} < p.valid_to)
                 where {where_u}
                 order by u.{caps.usage_time_col} desc
                 limit 500
                """,
                usage_params,
            )

        acc_rows: list[dict[str, Any]] = []
        if accumulators_on:
            where_a, acc_params = _accumulator_where("a")
            acc_rows = fetch_all(
                f"""
                select a.bucket_type, a.model, a.bucket_key, a.pending_tokens, a.close_count, a.updated_at
                  from finops.billing_accumulator a
                 where {where_a}
                 order by a.updated_at desc
                 limit 200
                """,
                acc_params,
            )

        pricing_rows: list[dict[str, Any]] = []
        if pricing_on:
            where_p, pricing_params = _pricing_where("p")
            pricing_rows = fetch_all(
                f"""
                select p.model, p.min_tokens, p.min_cost_brl as min_cost, p.valid_from, p.valid_to
                  from finops.model_pricing p
                 where {where_p}
                 order by p.model, p.valid_from desc
                """,
                pricing_params,
            )

        return {
            "tenant_id": DEFAULT_TENANT_ID,
            "data_source": "real",
            "scenario": "real",
            "days": int(days),
            "kpis": {
                "total_cost": round(total_cost_brl, 2),
                "manual_cost": round(manual_cost_brl, 2),
                "automation_cost": round(automation_cost_brl, 2),
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
                "cost_by_day": [{"time": row["time"], "value": row["value"]} for row in series_cost_by_day],
                "cost_by_source": [{"source": row.get("source"), "cost": row.get("cost")} for row in series_cost_by_source],
                "cost_by_model": [{"model": row.get("model"), "cost": row.get("cost")} for row in series_cost_by_model],
                "top_tasks": [{"task_name": row.get("task_name"), "cost": row.get("cost")} for row in top_tasks],
                "top_flows": [{"flow_name": row.get("flow_name"), "cost": row.get("cost")} for row in top_flows],
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
                "agent_name": agent_name,
                "pricing_enabled": bool(pricing_on),
                "ledger_enabled": bool(ledger_on),
                "accumulators_enabled": bool(accumulators_on),
                "data_source": "real",
            },
            "alerts": {
                "budget_alert": bool(budget_metrics["budget_alert_triggered"]),
                "forecast_alert": bool(budget_metrics["budget_forecast_alert_triggered"]),
                "messages": [
                    message
                    for message in [
                        f"Budget mensal acima de {int(BUDGET_ALERT_PERCENT)}%." if budget_metrics["budget_alert_triggered"] else None,
                        "Forecast mensal acima do orçamento." if budget_metrics["budget_forecast_alert_triggered"] else None,
                    ]
                    if message
                ],
            },
            "optimization": {
                "top_recommendation": top_recommendation,
            },
            "grafana_embed_url": DEFAULT_GRAFANA_EMBED_URL,
        }
    except Exception as exc:
        return _empty_dataset(days, f"Falha ao consultar Postgres no modo real: {exc}")


def ingest_usage(payload: dict[str, Any]) -> dict[str, Any]:
    caps = _schema_caps()
    if not table_exists("finops", caps.usage_table):
        return {"ok": False, "error": "usage_table_missing"}

    required = ["model", "total_tokens"]
    if caps.usage_table == "interaction_usage":
        required.extend(["interaction_id", "session_id", "project_key", "agent_name"])

    for field in required:
        if payload.get(field) in (None, ""):
            return {"ok": False, "error": f"missing_field:{field}"}

    if caps.usage_table == "interaction_usage":
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
              coalesce(%(source_type)s, 'MANUAL'),
              coalesce(%(actor_type)s, 'USER'),
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
                "input_tokens": int(payload.get("input_tokens") or 0),
                "output_tokens": int(payload.get("output_tokens") or 0),
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
    else:
        rows = execute_returning(
            """
            insert into finops.interaction (
              tenant_id, occurred_at, source_type, model, tokens_total,
              collaborator_id, conversation_id, task_id, flow_id
            ) values (
              %(tenant_id)s, coalesce(%(occurred_at)s, now()), coalesce(%(source_type)s, 'MANUAL'),
              %(model)s, %(tokens_total)s, %(collaborator_id)s, %(conversation_id)s, %(task_id)s, %(flow_id)s
            )
            returning id
            """,
            {
                "tenant_id": str(payload.get("tenant_id") or DEFAULT_TENANT_ID),
                "occurred_at": payload.get("created_at") or payload.get("occurred_at"),
                "source_type": payload.get("source_type"),
                "model": str(payload["model"]),
                "tokens_total": int(payload["total_tokens"]),
                "collaborator_id": payload.get("collaborator_id"),
                "conversation_id": payload.get("conversation_id"),
                "task_id": payload.get("task_id"),
                "flow_id": payload.get("flow_id"),
            },
        )

    new_id = rows[0]["id"] if rows else None
    return {"ok": True, "id": new_id}


def upsert_pricing(
    model: str,
    cost_per_1k_tokens_brl: float | None = None,
    min_tokens: int | None = None,
    min_cost_brl: float | None = None,
) -> None:
    min_tokens = int(min_tokens or 1000)
    if min_cost_brl is None:
        if cost_per_1k_tokens_brl is None:
            raise ValueError("Informe min_cost_brl ou cost_per_1k_tokens_brl.")
        min_cost_brl = float(cost_per_1k_tokens_brl) * (min_tokens / 1000.0)

    now = datetime.now(timezone.utc)
    where_p, params = _pricing_where("p")

    execute(
        f"""
        update finops.model_pricing p
           set valid_to = %(now)s
         where p.model = %(model)s
           and p.valid_to is null
           and {where_p}
        """,
        {**params, "now": now, "model": model},
    )

    execute(
        """
        insert into finops.model_pricing (
          tenant_id, model, min_tokens, min_cost_brl, valid_from, valid_to
        ) values (
          %(tenant_id)s, %(model)s, %(min_tokens)s, %(min_cost_brl)s, %(now)s, null
        )
        """,
        {
            "tenant_id": DEFAULT_TENANT_ID,
            "model": model,
            "min_tokens": min_tokens,
            "min_cost_brl": float(min_cost_brl),
            "now": now,
        },
    )
