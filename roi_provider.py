from __future__ import annotations

from datetime import datetime, timezone, date
from decimal import Decimal
from typing import Any
import json

from db import fetch_all, fetch_one, execute, execute_returning, table_exists


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------

def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        if v is None or v == "":
            return default
        if isinstance(v, str):
            # aceita formatos simples vindos de máscara pt-BR
            v = v.replace("R$", "").replace("%", "").strip()
            if "," in v and "." in v:
                v = v.replace(".", "").replace(",", ".")
            elif "," in v:
                v = v.replace(",", ".")
        return float(v)
    except Exception:
        return default


def _safe_int(v: Any, default: int = 0) -> int:
    try:
        if v is None or v == "":
            return default
        return int(float(v))
    except Exception:
        return default


def _json_safe(obj: Any):
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, tuple):
        return [_json_safe(x) for x in obj]
    if isinstance(obj, list):
        return [_json_safe(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    return obj


def _json_dumps(obj: Any) -> str:
    return json.dumps(_json_safe(obj or {}), ensure_ascii=False)


def _json_loads_maybe(value: Any) -> dict:
    if not value:
        return {}
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        try:
            data = json.loads(value)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}
    return {}


def _safe_percent(v: Any, default: float = 0.0) -> float:
    """Normaliza percentuais.

    Aceita 80, 80.0 e também 8000 vindo de máscara de percentual do front (80,00%).
    Sempre retorna 0..100.
    """
    n = _safe_float(v, default)
    if n > 100 and n <= 10000:
        n = n / 100.0
    return max(0.0, min(100.0, n))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _scope_filter_sql(alias: str, project_keys: list[str] | None) -> tuple[str, dict]:
    if project_keys is None:
        return "1=1", {}
    if len(project_keys) == 0:
        return "1=0", {"project_keys": []}
    return f"{alias}.project_id = any(%(project_keys)s)", {"project_keys": project_keys}


METHOD_ALIASES = {
    "time_saved": "time_saved",
    "time": "time_saved",
    "h_h": "time_saved",
    "h:h": "time_saved",
    "tempo": "time_saved",
    "tempo_economizado": "time_saved",
    "business_result": "business_result",
    "result": "business_result",
    "event_value": "business_result",
    "resultado_negocio": "business_result",
    "resultado_de_negocio": "business_result",
    "hybrid": "hybrid",
    "hibrido": "hybrid",
}


def _normalize_method(value: Any) -> str:
    raw = str(value or "business_result").strip().lower()
    return METHOD_ALIASES.get(raw, raw if raw in {"time_saved", "business_result", "hybrid"} else "business_result")


def _payload_value(payload: dict, *keys: str, default: Any = None) -> Any:
    for k in keys:
        if k in payload and payload.get(k) is not None:
            return payload.get(k)
    return default


def _normalize_roi_payload(payload: dict | None, base: dict | None = None) -> dict:
    """Normaliza aliases e preserva todos os parâmetros no assumptions_json.

    Esta função é o ponto central para garantir que os 3 métodos persistam os campos:
    - time_saved: campos de tempo
    - business_result: campos de evento
    - hybrid: campos de tempo + evento
    """
    merged = dict(base or {})
    merged.update(dict(payload or {}))

    # Se vier do banco, pode trazer assumptions_json com os campos reais do formulário.
    assumptions = _json_loads_maybe(merged.get("assumptions_json"))
    assumptions.update(dict(payload or {}))
    merged.update(assumptions)

    method = _normalize_method(_payload_value(merged, "calculation_method", "method", default="business_result"))

    normalized = dict(merged)
    normalized["calculation_method"] = method

    # Campos de tempo
    normalized["avg_manual_time_min"] = _safe_float(_payload_value(merged, "avg_manual_time_min", "saved_time_min", "manual_time_min", default=0))
    normalized["cost_per_hour_brl"] = _safe_float(_payload_value(merged, "cost_per_hour_brl", "hourly_cost_brl", "cost_per_hour", default=0))
    normalized["monthly_volume"] = _safe_float(_payload_value(merged, "monthly_volume", "volume_month", "task_volume_month", default=0))
    normalized["coverage_pct"] = _safe_percent(_payload_value(merged, "coverage_pct", "automation_pct", "automation_effective_pct", default=100), 100.0)

    # Campos de evento/resultado de negócio
    normalized["value_event_name"] = _payload_value(merged, "value_event_name", "event_name", "value_event", default=None)
    normalized["event_unit_value_brl"] = _safe_float(_payload_value(merged, "event_unit_value_brl", "unit_value_brl", "value_event_unit_brl", default=0))
    normalized["expected_events_month"] = _safe_float(_payload_value(merged, "expected_events_month", "events_expected_month", "events_month", default=0))
    normalized["baseline_monthly_brl"] = _safe_float(_payload_value(merged, "baseline_monthly_brl", "current_monthly_baseline_brl", "baseline_brl", default=0))

    # Campos comuns
    normalized["attribution_pct"] = _safe_percent(_payload_value(merged, "attribution_pct", "gabbi_attribution_pct", default=100), 100.0)
    normalized["agent_monthly_cost_brl"] = _safe_float(_payload_value(merged, "agent_monthly_cost_brl", "monthly_ai_cost_brl", "gabbi_monthly_cost_brl", default=0))
    normalized["implementation_cost_brl"] = _safe_float(_payload_value(merged, "implementation_cost_brl", "setup_cost_brl", "implantation_cost_brl", default=0))
    normalized["human_review_pct"] = _safe_percent(_payload_value(merged, "human_review_pct", default=0), 0.0)
    normalized["require_evidence"] = bool(_payload_value(merged, "require_evidence", "requires_evidence", default=False))
    normalized["requires_evidence"] = normalized["require_evidence"]
    normalized["human_review_required"] = bool(_payload_value(merged, "human_review_required", default=False))
    normalized["responsible_area"] = _payload_value(merged, "responsible_area", "business_area", "area", default=None)
    normalized["notes"] = _payload_value(merged, "notes", "observations", default=None)

    # Mantém aliases úteis para o front ao abrir a configuração.
    normalized["unit_value_brl"] = normalized["event_unit_value_brl"]
    normalized["saved_time_min"] = normalized["avg_manual_time_min"]
    normalized["hourly_cost_brl"] = normalized["cost_per_hour_brl"]
    normalized["automation_pct"] = normalized["coverage_pct"]
    normalized["gabbi_attribution_pct"] = normalized["attribution_pct"]
    normalized["setup_cost_brl"] = normalized["implementation_cost_brl"]

    return _json_safe(normalized)


def _assumptions_for_storage(payload: dict) -> dict:
    normalized = _normalize_roi_payload(payload)
    # Remove campos técnicos vindos da linha do banco para não poluir o formulário.
    for k in [
        "id", "customer_id", "project_id", "created_at", "updated_at", "created_by", "updated_by",
        "published_at", "published_by", "status", "last_simulation_json", "assumptions_json",
    ]:
        normalized.pop(k, None)
    return normalized


def _hydrate_configuration(row: dict | None) -> dict | None:
    if not row:
        return None
    out = _json_safe(dict(row))
    assumptions = _json_loads_maybe(out.get("assumptions_json"))
    # assumptions primeiro; colunas depois; normalização no final preserva campos específicos.
    hydrated = {**assumptions, **out}
    normalized = _normalize_roi_payload(hydrated)
    hydrated.update(normalized)
    hydrated["assumptions_json"] = assumptions or _assumptions_for_storage(hydrated)
    if out.get("last_simulation_json"):
        hydrated["last_simulation_json"] = _json_loads_maybe(out.get("last_simulation_json")) if isinstance(out.get("last_simulation_json"), str) else out.get("last_simulation_json")
    return _json_safe(hydrated)


# -----------------------------------------------------------------------------
# Calculation
# -----------------------------------------------------------------------------

def simulate_roi(payload: dict) -> dict:
    """Pure ROI simulation used by the configuration screen preview.

    Stateless: não grava no banco.
    """
    p = _normalize_roi_payload(payload)
    method = p["calculation_method"]

    attribution_factor = _safe_percent(p.get("attribution_pct"), 100.0) / 100.0
    agent_monthly_cost = _safe_float(p.get("agent_monthly_cost_brl"))
    implementation_cost = _safe_float(p.get("implementation_cost_brl"))
    human_review_factor = _safe_percent(p.get("human_review_pct"), 0.0) / 100.0

    avg_manual_time_min = _safe_float(p.get("avg_manual_time_min"))
    monthly_volume = _safe_float(p.get("monthly_volume"))
    cost_per_hour = _safe_float(p.get("cost_per_hour_brl"))
    coverage_factor = _safe_percent(p.get("coverage_pct"), 100.0) / 100.0

    unit_value = _safe_float(p.get("event_unit_value_brl"))
    events_month = _safe_float(p.get("expected_events_month"))

    time_savings_raw = (avg_manual_time_min / 60.0) * monthly_volume * cost_per_hour * coverage_factor
    business_savings_raw = unit_value * events_month

    if method == "time_saved":
        gross_before_attribution = time_savings_raw
        calculation_base = "(avg_manual_time_min / 60) * monthly_volume * cost_per_hour_brl * coverage_pct * attribution_pct"
    elif method == "hybrid":
        gross_before_attribution = time_savings_raw + business_savings_raw
        calculation_base = "(((avg_manual_time_min / 60) * monthly_volume * cost_per_hour_brl * coverage_pct) + (event_unit_value_brl * expected_events_month)) * attribution_pct"
    else:
        method = "business_result"
        gross_before_attribution = business_savings_raw
        calculation_base = "event_unit_value_brl * expected_events_month * attribution_pct"

    gross_savings = gross_before_attribution * attribution_factor
    review_penalty = gross_savings * human_review_factor
    gross_after_review = max(gross_savings - review_penalty, 0.0)
    net_savings = gross_after_review - agent_monthly_cost
    roi_pct = (net_savings / agent_monthly_cost * 100.0) if agent_monthly_cost > 0 else 0.0
    payback_months = (implementation_cost / net_savings) if implementation_cost > 0 and net_savings > 0 else ((agent_monthly_cost / net_savings) if net_savings > 0 else None)

    benefit_share_pct = (gross_after_review / (gross_after_review + agent_monthly_cost) * 100.0) if (gross_after_review + agent_monthly_cost) > 0 else 0.0
    cost_share_pct = 100.0 - benefit_share_pct if (gross_after_review + agent_monthly_cost) > 0 else 0.0

    return _json_safe({
        "calculated_at": _now_iso(),
        "method": method,
        "calculation_base": calculation_base,
        "time_savings_brl": round(time_savings_raw * attribution_factor, 2),
        "business_savings_brl": round(business_savings_raw * attribution_factor, 2),
        "gross_savings_brl": round(gross_savings, 2),
        "human_review_penalty_brl": round(review_penalty, 2),
        "gross_savings_after_review_brl": round(gross_after_review, 2),
        "ai_cost_brl": round(agent_monthly_cost, 2),
        "net_savings_brl": round(net_savings, 2),
        "roi_pct": round(roi_pct, 2),
        "payback_months": round(payback_months, 4) if payback_months is not None else None,
        "payback_days": round(payback_months * 30, 1) if payback_months is not None else None,
        "chart": {"benefit_pct": round(benefit_share_pct, 2), "cost_pct": round(cost_share_pct, 2)},
        "inputs": p,
    })


# -----------------------------------------------------------------------------
# Configurations
# -----------------------------------------------------------------------------

def list_roi_configurations(project_keys: list[str] | None = None, status: str | None = None) -> dict:
    where, params = _scope_filter_sql("c", project_keys)
    if status:
        where += " and c.status = %(status)s"
        params["status"] = status
    rows = fetch_all(
        f"""
        select c.*
        from roi.roi_configuration c
        where {where}
        order by c.updated_at desc nulls last, c.created_at desc
        """,
        params,
    ) if table_exists("roi", "roi_configuration") else []
    hydrated = [_hydrate_configuration(r) for r in rows]
    return {"items": hydrated, "total": len(hydrated)}


def create_roi_configuration(payload: dict, customer_ctx: dict, user_id: str | None = None) -> dict:
    p = _normalize_roi_payload(payload)
    project_id = p.get("project_id") or p.get("project_key")
    if not project_id:
        projects = customer_ctx.get("projects") or []
        project_id = projects[0].get("id") if projects else None
    if not project_id:
        return {"ok": False, "error": "missing_project_id"}

    assumptions = _assumptions_for_storage(p)
    simulation = simulate_roi(assumptions)
    rows = execute_returning(
        """
        insert into roi.roi_configuration (
            customer_id, project_id, task_id, agent_id, workflow_id, dag_id,
            name, description, calculation_method, value_event_name,
            event_unit_value_brl, expected_events_month, attribution_pct,
            baseline_monthly_brl, agent_monthly_cost_brl, human_review_pct,
            require_evidence, human_review_required, responsible_area,
            status, assumptions_json, last_simulation_json, created_by, updated_by
        ) values (
            %(customer_id)s, %(project_id)s, %(task_id)s, %(agent_id)s, %(workflow_id)s, %(dag_id)s,
            %(name)s, %(description)s, %(calculation_method)s, %(value_event_name)s,
            %(event_unit_value_brl)s, %(expected_events_month)s, %(attribution_pct)s,
            %(baseline_monthly_brl)s, %(agent_monthly_cost_brl)s, %(human_review_pct)s,
            %(require_evidence)s, %(human_review_required)s, %(responsible_area)s,
            'DRAFT', %(assumptions_json)s::jsonb, %(last_simulation_json)s::jsonb, %(user_id)s, %(user_id)s
        ) returning *
        """,
        {
            "customer_id": customer_ctx.get("customer_id"),
            "project_id": project_id,
            "task_id": p.get("task_id"),
            "agent_id": p.get("agent_id"),
            "workflow_id": p.get("workflow_id"),
            "dag_id": p.get("dag_id"),
            "name": p.get("name") or p.get("title") or "Configuração ROI",
            "description": p.get("description"),
            "calculation_method": p.get("calculation_method"),
            "value_event_name": p.get("value_event_name"),
            "event_unit_value_brl": _safe_float(p.get("event_unit_value_brl")),
            "expected_events_month": _safe_float(p.get("expected_events_month")),
            "attribution_pct": _safe_percent(p.get("attribution_pct"), 100.0),
            "baseline_monthly_brl": _safe_float(p.get("baseline_monthly_brl")),
            "agent_monthly_cost_brl": _safe_float(p.get("agent_monthly_cost_brl")),
            "human_review_pct": _safe_percent(p.get("human_review_pct"), 0.0),
            "require_evidence": bool(p.get("require_evidence")),
            "human_review_required": bool(p.get("human_review_required")),
            "responsible_area": p.get("responsible_area"),
            "assumptions_json": _json_dumps(assumptions),
            "last_simulation_json": _json_dumps(simulation),
            "user_id": user_id,
        },
    )
    row = _hydrate_configuration(rows[0]) if rows else None
    if row:
        _audit("roi_configuration", row["id"], "CREATE", None, row, user_id, customer_ctx.get("customer_id"))
    return {"ok": True, "item": row, "simulation": simulation}


def get_roi_configuration(config_id: str, project_keys: list[str] | None = None) -> dict | None:
    where, params = _scope_filter_sql("c", project_keys)
    params["id"] = config_id
    row = fetch_one(f"select c.* from roi.roi_configuration c where c.id=%(id)s and {where}", params)
    return _hydrate_configuration(row)


def update_roi_configuration(config_id: str, payload: dict, project_keys: list[str] | None, user_id: str | None = None) -> dict:
    current = get_roi_configuration(config_id, project_keys)
    if not current:
        return {"ok": False, "error": "not_found"}
    if current.get("status") == "PUBLISHED":
        return {"ok": False, "error": "published_configuration_is_immutable"}

    assumptions_current = _json_loads_maybe(current.get("assumptions_json"))
    merged_base = {**assumptions_current, **current}
    p = _normalize_roi_payload(payload, base=merged_base)
    assumptions = _assumptions_for_storage(p)
    simulation = simulate_roi(assumptions)

    rows = execute_returning(
        """
        update roi.roi_configuration set
            name = coalesce(%(name)s, name),
            description = %(description)s,
            calculation_method = coalesce(%(calculation_method)s, calculation_method),
            value_event_name = %(value_event_name)s,
            event_unit_value_brl = %(event_unit_value_brl)s,
            expected_events_month = %(expected_events_month)s,
            attribution_pct = %(attribution_pct)s,
            baseline_monthly_brl = %(baseline_monthly_brl)s,
            agent_monthly_cost_brl = %(agent_monthly_cost_brl)s,
            human_review_pct = %(human_review_pct)s,
            require_evidence = %(require_evidence)s,
            human_review_required = %(human_review_required)s,
            responsible_area = %(responsible_area)s,
            assumptions_json = %(assumptions_json)s::jsonb,
            last_simulation_json = %(last_simulation_json)s::jsonb,
            updated_by = %(user_id)s,
            updated_at = now()
        where id = %(id)s
        returning *
        """,
        {
            "id": config_id,
            "name": p.get("name"),
            "description": p.get("description"),
            "calculation_method": p.get("calculation_method"),
            "value_event_name": p.get("value_event_name"),
            "event_unit_value_brl": _safe_float(p.get("event_unit_value_brl")),
            "expected_events_month": _safe_float(p.get("expected_events_month")),
            "attribution_pct": _safe_percent(p.get("attribution_pct"), 100.0),
            "baseline_monthly_brl": _safe_float(p.get("baseline_monthly_brl")),
            "agent_monthly_cost_brl": _safe_float(p.get("agent_monthly_cost_brl")),
            "human_review_pct": _safe_percent(p.get("human_review_pct"), 0.0),
            "require_evidence": bool(p.get("require_evidence")),
            "human_review_required": bool(p.get("human_review_required")),
            "responsible_area": p.get("responsible_area"),
            "assumptions_json": _json_dumps(assumptions),
            "last_simulation_json": _json_dumps(simulation),
            "user_id": user_id,
        },
    )
    item = _hydrate_configuration(rows[0]) if rows else None
    _audit("roi_configuration", config_id, "UPDATE", current, item, user_id, current.get("customer_id"))
    return {"ok": True, "item": item, "simulation": simulation}


def publish_roi_configuration(config_id: str, project_keys: list[str] | None, user_id: str | None = None) -> dict:
    current = get_roi_configuration(config_id, project_keys)
    if not current:
        return {"ok": False, "error": "not_found"}
    if not current.get("last_simulation_json"):
        return {"ok": False, "error": "simulation_required"}
    version_row = fetch_one("select coalesce(max(version),0)+1 as version from roi.roi_configuration_version where configuration_id=%(id)s", {"id": config_id}) or {"version": 1}
    version = int(version_row["version"])
    snapshot = _json_dumps(current)
    execute(
        """
        insert into roi.roi_configuration_version(configuration_id, version, status, snapshot_json, published_by)
        values (%(id)s, %(version)s, 'PUBLISHED', %(snapshot)s::jsonb, %(user_id)s)
        """,
        {"id": config_id, "version": version, "snapshot": snapshot, "user_id": user_id},
    )
    rows = execute_returning(
        """
        update roi.roi_configuration
           set status='PUBLISHED', published_at=now(), published_by=%(user_id)s, updated_at=now()
         where id=%(id)s
         returning *
        """,
        {"id": config_id, "user_id": user_id},
    )
    item = _hydrate_configuration(rows[0]) if rows else None
    _audit("roi_configuration", config_id, "PUBLISH", current, item, user_id, current.get("customer_id"))
    return {"ok": True, "item": item, "version": version}


def archive_roi_configuration(config_id: str, project_keys: list[str] | None, user_id: str | None = None) -> dict:
    current = get_roi_configuration(config_id, project_keys)
    if not current:
        return {"ok": False, "error": "not_found"}
    rows = execute_returning("update roi.roi_configuration set status='ARCHIVED', updated_at=now(), updated_by=%(user_id)s where id=%(id)s returning *", {"id": config_id, "user_id": user_id})
    item = _hydrate_configuration(rows[0]) if rows else None
    _audit("roi_configuration", config_id, "ARCHIVE", current, item, user_id, current.get("customer_id"))
    return {"ok": True, "item": item}


# -----------------------------------------------------------------------------
# Tasks / baselines / mappings
# -----------------------------------------------------------------------------

def _task_exists(task_id: str, project_keys: list[str] | None = None) -> dict | None:
    where, params = _scope_filter_sql("t", project_keys)
    params["id"] = task_id
    return fetch_one(f"select * from roi.roi_task t where t.id=%(id)s and {where}", params)


def _baseline_status_sql_expr() -> str:
    """Mantém compatibilidade com schema antigo que possuía apenas approved."""
    return (
        "coalesce(b.baseline_status, case when b.approved then 'APPROVED' else 'DRAFT' end)"
        if _column_exists_roi("roi_task_baseline", "baseline_status")
        else "case when b.approved then 'APPROVED' else 'DRAFT' end"
    )


def _column_exists_roi(table: str, column: str) -> bool:
    row = fetch_one(
        """
        select 1
        from information_schema.columns
        where table_schema='roi' and table_name=%(table)s and column_name=%(column)s
        limit 1
        """,
        {"table": table, "column": column},
    )
    return bool(row)


def list_roi_tasks(
    project_keys: list[str] | None = None,
    area_id: str | None = None,
    owner_id: str | None = None,
    status: str | None = None,
    framework_id: str | None = None,
) -> dict:
    where, params = _scope_filter_sql("t", project_keys)
    if area_id:
        where += " and t.area_id = %(area_id)s"
        params["area_id"] = area_id
    if owner_id:
        where += " and t.owner_id = %(owner_id)s"
        params["owner_id"] = owner_id
    if status:
        where += " and t.status = %(status)s"
        params["status"] = status

    join_framework = ""
    if framework_id:
        join_framework = (
            " join roi.roi_task_framework tf on tf.task_id=t.id "
            "and tf.framework_id=%(framework_id)s and tf.active_to is null"
        )
        params["framework_id"] = framework_id

    rows = fetch_all(
        f"""
        select t.*,
               b.id as latest_baseline_id,
               b.avg_manual_time_min,
               b.monthly_volume,
               b.cost_per_hour_brl,
               b.confidence_level,
               {_baseline_status_sql_expr()} as baseline_status,
               b.approved as baseline_approved
        from roi.roi_task t
        {join_framework}
        left join lateral (
            select * from roi.roi_task_baseline b
            where b.task_id=t.id
            order by b.created_at desc
            limit 1
        ) b on true
        where {where}
        order by t.updated_at desc nulls last, t.created_at desc
        """,
        params,
    ) if table_exists("roi", "roi_task") else []
    return {"items": _json_safe(rows), "total": len(rows)}


def create_roi_task(payload: dict, customer_ctx: dict, user_id: str | None = None) -> dict:
    project_id = payload.get("project_id") or payload.get("project_key")
    if not project_id:
        projects = customer_ctx.get("projects") or []
        project_id = projects[0].get("id") if projects else None
    if not project_id:
        return {"ok": False, "error": "missing_project_id"}

    rows = execute_returning(
        """
        insert into roi.roi_task(
            customer_id, project_id, code, name, description, area_id,
            process_name, owner_id, status, created_by, updated_by
        ) values(
            %(customer_id)s, %(project_id)s, %(code)s, %(name)s, %(description)s,
            %(area_id)s, %(process_name)s, %(owner_id)s,
            coalesce(%(status)s,'DRAFT'), %(user_id)s, %(user_id)s
        )
        returning *
        """,
        {
            "customer_id": customer_ctx.get("customer_id"),
            "project_id": project_id,
            "code": payload.get("code"),
            "name": payload.get("name"),
            "description": payload.get("description"),
            "area_id": payload.get("area_id"),
            "process_name": payload.get("process_name"),
            "owner_id": payload.get("owner_id"),
            "status": payload.get("status"),
            "user_id": user_id,
        },
    )
    item = _json_safe(rows[0]) if rows else None
    if item:
        _audit("roi_task", item["id"], "CREATE", None, item, user_id, customer_ctx.get("customer_id"))
    return {"ok": True, "item": item}


def update_roi_task(task_id: str, payload: dict, project_keys: list[str] | None, user_id: str | None = None) -> dict:
    current = _task_exists(task_id, project_keys)
    if not current:
        return {"ok": False, "error": "task_not_found"}
    rows = execute_returning(
        """
        update roi.roi_task set
            code = coalesce(%(code)s, code),
            name = coalesce(%(name)s, name),
            description = %(description)s,
            area_id = %(area_id)s,
            process_name = %(process_name)s,
            owner_id = %(owner_id)s,
            status = coalesce(%(status)s, status),
            updated_by = %(user_id)s,
            updated_at = now()
        where id=%(id)s
        returning *
        """,
        {
            "id": task_id,
            "code": payload.get("code"),
            "name": payload.get("name"),
            "description": payload.get("description"),
            "area_id": payload.get("area_id"),
            "process_name": payload.get("process_name"),
            "owner_id": payload.get("owner_id"),
            "status": payload.get("status"),
            "user_id": user_id,
        },
    )
    item = _json_safe(rows[0]) if rows else None
    _audit("roi_task", task_id, "UPDATE", current, item, user_id, current.get("customer_id"))
    return {"ok": True, "item": item}


def archive_roi_task(task_id: str, project_keys: list[str] | None, user_id: str | None = None) -> dict:
    current = _task_exists(task_id, project_keys)
    if not current:
        return {"ok": False, "error": "task_not_found"}
    rows = execute_returning(
        """
        update roi.roi_task
           set status='ARCHIVED', updated_by=%(user_id)s, updated_at=now()
         where id=%(id)s
         returning *
        """,
        {"id": task_id, "user_id": user_id},
    )
    item = _json_safe(rows[0]) if rows else None
    _audit("roi_task", task_id, "ARCHIVE", current, item, user_id, current.get("customer_id"))
    return {"ok": True, "item": item}


def save_task_baseline(task_id: str, payload: dict, project_keys: list[str] | None, user_id: str | None = None) -> dict:
    task = _task_exists(task_id, project_keys)
    if not task:
        return {"ok": False, "error": "task_not_found"}
    status = str(payload.get("baseline_status") or payload.get("status") or "DRAFT").upper()
    if status not in {"DRAFT", "PENDING_APPROVAL", "APPROVED", "REJECTED", "ARCHIVED"}:
        status = "DRAFT"
    approved = status == "APPROVED" or bool(payload.get("approved") or False)
    rows = execute_returning(
        """
        insert into roi.roi_task_baseline(
            task_id, avg_manual_time_min, monthly_volume, cost_per_hour_brl,
            manual_sla_hours, manual_error_rate, baseline_date, confidence_level,
            evidence_required, approved, baseline_status, created_by
        ) values(
            %(task_id)s, %(avg_manual_time_min)s, %(monthly_volume)s, %(cost_per_hour_brl)s,
            %(manual_sla_hours)s, %(manual_error_rate)s,
            coalesce(%(baseline_date)s, current_date), %(confidence_level)s,
            %(evidence_required)s, %(approved)s, %(baseline_status)s, %(user_id)s
        )
        returning *
        """,
        {
            "task_id": task_id,
            "avg_manual_time_min": _safe_float(payload.get("avg_manual_time_min")),
            "monthly_volume": _safe_float(payload.get("monthly_volume")),
            "cost_per_hour_brl": _safe_float(payload.get("cost_per_hour_brl", payload.get("cost_per_hour", 0))),
            "manual_sla_hours": _safe_float(payload.get("manual_sla_hours")),
            "manual_error_rate": _safe_float(payload.get("manual_error_rate")),
            "baseline_date": payload.get("baseline_date"),
            "confidence_level": payload.get("confidence_level") or "MEDIUM",
            "evidence_required": bool(payload.get("evidence_required", True)),
            "approved": approved,
            "baseline_status": status,
            "user_id": user_id,
        },
    )
    item = _json_safe(rows[0]) if rows else None
    if item:
        _audit("roi_task_baseline", item["id"], "CREATE", None, item, user_id, task.get("customer_id"))
    return {"ok": True, "item": item}


def _latest_baseline(task_id: str) -> dict | None:
    return fetch_one(
        "select * from roi.roi_task_baseline where task_id=%(task_id)s order by created_at desc limit 1",
        {"task_id": task_id},
    )


def approve_task_baseline(task_id: str, project_keys: list[str] | None, user_id: str | None = None) -> dict:
    task = _task_exists(task_id, project_keys)
    if not task:
        return {"ok": False, "error": "task_not_found"}
    baseline = _latest_baseline(task_id)
    if not baseline:
        return {"ok": False, "error": "baseline_not_found"}
    if bool(baseline.get("evidence_required")):
        ev = fetch_one(
            "select 1 from roi.roi_evidence where entity_type='ROI_TASK_BASELINE' and entity_id=%(id)s limit 1",
            {"id": baseline["id"]},
        )
        if not ev:
            return {"ok": False, "error": "evidence_required"}
    rows = execute_returning(
        """
        update roi.roi_task_baseline
           set approved=true, baseline_status='APPROVED', approved_by=%(user_id)s, approved_at=now()
         where id=%(baseline_id)s
         returning *
        """,
        {"baseline_id": baseline["id"], "user_id": user_id},
    )
    item = _json_safe(rows[0]) if rows else None
    _audit("roi_task_baseline", baseline["id"], "APPROVE", baseline, item, user_id, task.get("customer_id"))
    return {"ok": bool(rows), "item": item}


def reject_task_baseline(task_id: str, payload: dict, project_keys: list[str] | None, user_id: str | None = None) -> dict:
    task = _task_exists(task_id, project_keys)
    if not task:
        return {"ok": False, "error": "task_not_found"}
    baseline = _latest_baseline(task_id)
    if not baseline:
        return {"ok": False, "error": "baseline_not_found"}
    rows = execute_returning(
        """
        update roi.roi_task_baseline
           set approved=false, baseline_status='REJECTED', rejected_by=%(user_id)s,
               rejected_at=now(), rejection_reason=%(reason)s
         where id=%(baseline_id)s
         returning *
        """,
        {
            "baseline_id": baseline["id"],
            "user_id": user_id,
            "reason": payload.get("reason") or payload.get("rejection_reason"),
        },
    )
    item = _json_safe(rows[0]) if rows else None
    _audit("roi_task_baseline", baseline["id"], "REJECT", baseline, item, user_id, task.get("customer_id"))
    return {"ok": bool(rows), "item": item}


def archive_task_baseline(task_id: str, project_keys: list[str] | None, user_id: str | None = None) -> dict:
    task = _task_exists(task_id, project_keys)
    if not task:
        return {"ok": False, "error": "task_not_found"}
    baseline = _latest_baseline(task_id)
    if not baseline:
        return {"ok": False, "error": "baseline_not_found"}
    rows = execute_returning(
        """
        update roi.roi_task_baseline
           set approved=false, baseline_status='ARCHIVED'
         where id=%(baseline_id)s
         returning *
        """,
        {"baseline_id": baseline["id"]},
    )
    item = _json_safe(rows[0]) if rows else None
    _audit("roi_task_baseline", baseline["id"], "ARCHIVE", baseline, item, user_id, task.get("customer_id"))
    return {"ok": bool(rows), "item": item}


def list_task_framework_links(task_id: str, project_keys: list[str] | None = None) -> dict:
    task = _task_exists(task_id, project_keys)
    if not task:
        return {"ok": False, "error": "task_not_found", "items": [], "total": 0}
    rows = fetch_all(
        """
        select tf.*, c.name as framework_name, c.status as framework_status
          from roi.roi_task_framework tf
          left join roi.roi_configuration c on c.id=tf.framework_id
         where tf.task_id=%(task_id)s
         order by tf.active_from desc, tf.created_at desc
        """,
        {"task_id": task_id},
    ) if table_exists("roi", "roi_task_framework") else []
    return {"ok": True, "items": _json_safe(rows), "total": len(rows)}


def create_task_framework_link(task_id: str, payload: dict, project_keys: list[str] | None, user_id: str | None = None) -> dict:
    task = _task_exists(task_id, project_keys)
    if not task:
        return {"ok": False, "error": "task_not_found"}
    framework_id = payload.get("framework_id") or payload.get("configuration_id")
    if not framework_id:
        return {"ok": False, "error": "missing_framework_id"}
    framework = fetch_one(
        "select * from roi.roi_configuration where id=%(id)s and status='PUBLISHED'",
        {"id": framework_id},
    )
    if not framework:
        return {"ok": False, "error": "published_framework_required"}
    version = payload.get("framework_version")
    if version is None:
        vr = fetch_one(
            "select max(version) as version from roi.roi_configuration_version where configuration_id=%(id)s",
            {"id": framework_id},
        ) or {"version": None}
        version = vr.get("version")
    if version is None:
        return {"ok": False, "error": "published_framework_version_required"}
    rows = execute_returning(
        """
        insert into roi.roi_task_framework(
            task_id, framework_id, framework_version, active_from, active_to, created_by
        ) values(
            %(task_id)s, %(framework_id)s, %(framework_version)s,
            coalesce(%(active_from)s::date, current_date), %(active_to)s::date, %(user_id)s
        )
        returning *
        """,
        {
            "task_id": task_id,
            "framework_id": framework_id,
            "framework_version": int(version),
            "active_from": payload.get("active_from"),
            "active_to": payload.get("active_to"),
            "user_id": user_id,
        },
    )
    item = _json_safe(rows[0]) if rows else None
    if item:
        _audit("roi_task_framework", item["id"], "CREATE", None, item, user_id, task.get("customer_id"))
    return {"ok": True, "item": item}


def deactivate_task_framework_link(link_id: str, payload: dict, project_keys: list[str] | None, user_id: str | None = None) -> dict:
    link = fetch_one(
        """
        select tf.*, t.customer_id, t.project_id
          from roi.roi_task_framework tf
          join roi.roi_task t on t.id=tf.task_id
         where tf.id=%(id)s
        """,
        {"id": link_id},
    )
    if not link:
        return {"ok": False, "error": "task_framework_not_found"}
    if project_keys is not None and link.get("project_id") not in project_keys:
        return {"ok": False, "error": "task_framework_not_found"}
    rows = execute_returning(
        """
        update roi.roi_task_framework
           set active_to=coalesce(%(active_to)s::date,current_date),
               deactivated_by=%(user_id)s, deactivated_at=now()
         where id=%(id)s
         returning *
        """,
        {"id": link_id, "active_to": payload.get("active_to"), "user_id": user_id},
    )
    item = _json_safe(rows[0]) if rows else None
    _audit("roi_task_framework", link_id, "DEACTIVATE", link, item, user_id, link.get("customer_id"))
    return {"ok": True, "item": item}


def list_roi_evidences(project_keys: list[str] | None = None, entity_type: str | None = None, entity_id: str | None = None) -> dict:
    if not table_exists("roi", "roi_evidence"):
        return {"items": [], "total": 0}
    params: dict[str, Any] = {}
    clauses = ["1=1"]
    if entity_type:
        clauses.append("e.entity_type=%(entity_type)s")
        params["entity_type"] = entity_type
    if entity_id:
        clauses.append("e.entity_id=%(entity_id)s")
        params["entity_id"] = entity_id

    join_sql = ""
    if project_keys is not None:
        params["project_keys"] = project_keys
        if len(project_keys) == 0:
            clauses.append("1=0")
        else:
            join_sql = """
            left join roi.roi_task t on (
                (e.entity_type='ROI_TASK' and e.entity_id=t.id)
                or (
                    e.entity_type='ROI_TASK_BASELINE'
                    and exists (
                        select 1 from roi.roi_task_baseline b
                        where b.id=e.entity_id and b.task_id=t.id
                    )
                )
            )
            """
            clauses.append("(t.project_id = any(%(project_keys)s) or e.entity_type not in ('ROI_TASK','ROI_TASK_BASELINE'))")

    rows = fetch_all(
        f"""
        select e.*
          from roi.roi_evidence e
          {join_sql}
         where {' and '.join(clauses)}
         order by e.created_at desc
        """,
        params,
    )
    return {"items": _json_safe(rows), "total": len(rows)}


def create_roi_evidence(payload: dict, customer_ctx: dict, user_id: str | None = None) -> dict:
    entity_type = payload.get("entity_type")
    entity_id = payload.get("entity_id")
    if not entity_type or not entity_id:
        return {"ok": False, "error": "missing_entity_type_or_entity_id"}
    rows = execute_returning(
        """
        insert into roi.roi_evidence(
            customer_id, entity_type, entity_id, file_url, source_url,
            source_type, description, pii_masked, uploaded_by
        ) values(
            %(customer_id)s, %(entity_type)s, %(entity_id)s, %(file_url)s,
            %(source_url)s, %(source_type)s, %(description)s,
            coalesce(%(pii_masked)s,true), %(user_id)s
        )
        returning *
        """,
        {
            "customer_id": customer_ctx.get("customer_id"),
            "entity_type": entity_type,
            "entity_id": entity_id,
            "file_url": payload.get("file_url"),
            "source_url": payload.get("source_url"),
            "source_type": payload.get("source_type"),
            "description": payload.get("description"),
            "pii_masked": payload.get("pii_masked"),
            "user_id": user_id,
        },
    )
    item = _json_safe(rows[0]) if rows else None
    if item:
        _audit("roi_evidence", item["id"], "CREATE", None, item, user_id, customer_ctx.get("customer_id"))
    return {"ok": True, "item": item}


def list_roi_mappings(project_keys: list[str] | None = None) -> dict:
    where, params = _scope_filter_sql("t", project_keys)
    rows = fetch_all(
        f"""
        select m.*, t.name as task_name, t.project_id
        from roi.roi_task_mapping m
        join roi.roi_task t on t.id = m.task_id
        where {where}
        order by m.updated_at desc nulls last, m.created_at desc
        """,
        params,
    ) if table_exists("roi", "roi_task_mapping") else []
    return {"items": _json_safe(rows), "total": len(rows)}


def create_roi_mapping(payload: dict, project_keys: list[str] | None, user_id: str | None = None) -> dict:
    task_id = payload.get("task_id")
    if not task_id:
        return {"ok": False, "error": "missing_task_id"}
    where, params = _scope_filter_sql("t", project_keys)
    params["id"] = task_id
    task = fetch_one(f"select * from roi.roi_task t where t.id=%(id)s and {where}", params)
    if not task:
        return {"ok": False, "error": "task_not_found"}
    rows = execute_returning(
        """
        insert into roi.roi_task_mapping(task_id, agent_id, agent_name, workflow_id, dag_id, coverage_pct, human_review_pct, execution_mode, channel, status, active_from, created_by, updated_by)
        values(%(task_id)s, %(agent_id)s, %(agent_name)s, %(workflow_id)s, %(dag_id)s, %(coverage_pct)s, %(human_review_pct)s, %(execution_mode)s, %(channel)s, coalesce(%(status)s,'ACTIVE'), coalesce(%(active_from)s::date, current_date), %(user_id)s, %(user_id)s)
        returning *
        """,
        {"task_id": task_id, "agent_id": payload.get("agent_id"), "agent_name": payload.get("agent_name"), "workflow_id": payload.get("workflow_id"), "dag_id": payload.get("dag_id"), "coverage_pct": _safe_percent(payload.get("coverage_pct", 100), 100.0), "human_review_pct": _safe_percent(payload.get("human_review_pct", 0), 0.0), "execution_mode": payload.get("execution_mode"), "channel": payload.get("channel"), "status": payload.get("status"), "active_from": payload.get("active_from"), "user_id": user_id},
    )
    return {"ok": True, "item": _json_safe(rows[0]) if rows else None}


# -----------------------------------------------------------------------------
# Dashboard / results
# -----------------------------------------------------------------------------

def executive_dashboard(project_keys: list[str] | None = None, days: int = 30) -> dict:
    where, params = _scope_filter_sql("r", project_keys)
    rows = fetch_all(
        f"""
        select coalesce(sum(r.gross_savings_brl),0)::numeric(14,2) gross_savings_brl,
               coalesce(sum(r.net_savings_brl),0)::numeric(14,2) net_savings_brl,
               coalesce(sum(r.ai_cost_brl),0)::numeric(14,2) ai_cost_brl,
               coalesce(avg(r.roi_pct),0)::numeric(14,2) avg_roi_pct,
               coalesce(avg(r.payback_months),0)::numeric(14,4) avg_payback_months,
               count(distinct r.task_id)::int tasks_with_roi
        from roi.roi_calculation_result r
        where {where} and r.period_start >= current_date - (%(days)s || ' days')::interval
        """,
        {**params, "days": int(days)},
    ) if table_exists("roi", "roi_calculation_result") else []
    k = rows[0] if rows else {}
    return _json_safe({"kpis": k, "days": days})


def task_result(task_id: str, project_keys: list[str] | None = None) -> dict:
    where, params = _scope_filter_sql("t", project_keys)
    params["task_id"] = task_id
    task = fetch_one(f"select * from roi.roi_task t where t.id=%(task_id)s and {where}", params)
    if not task:
        return {"ok": False, "error": "task_not_found"}
    baseline = fetch_one("select * from roi.roi_task_baseline where task_id=%(task_id)s order by created_at desc limit 1", {"task_id": task_id})
    mappings = fetch_all("select * from roi.roi_task_mapping where task_id=%(task_id)s order by created_at desc", {"task_id": task_id})
    results = fetch_all("select * from roi.roi_calculation_result where task_id=%(task_id)s order by period_start desc limit 24", {"task_id": task_id})
    return _json_safe({"ok": True, "task": task, "baseline": baseline, "mappings": mappings, "results": results})


# -----------------------------------------------------------------------------
# Audit
# -----------------------------------------------------------------------------

def _audit(entity_type: str, entity_id: str, event_type: str, before: Any, after: Any, user_id: str | None, customer_id: str | None) -> None:
    if not table_exists("roi", "roi_audit_event"):
        return
    try:
        execute(
            """
            insert into roi.roi_audit_event(event_type, entity_type, entity_id, user_id, customer_id, before_json, after_json)
            values(%(event_type)s, %(entity_type)s, %(entity_id)s, %(user_id)s, %(customer_id)s, %(before_json)s::jsonb, %(after_json)s::jsonb)
            """,
            {"event_type": event_type, "entity_type": entity_type, "entity_id": entity_id, "user_id": user_id, "customer_id": customer_id, "before_json": _json_dumps(before) if before is not None else None, "after_json": _json_dumps(after) if after is not None else None},
        )
    except Exception as e:
        print(f"[ROI][WARN] audit failed: {e}")

# =============================================================================
# ROI TO-BE compatibility layer (v3)
# =============================================================================

def _user_context(user_id: str | None) -> dict:
    """Best-effort user lookup. Does not assume a specific role/area schema."""
    if not user_id:
        return {"id": None, "is_admin": False, "area_id": None, "active": False}
    user = fetch_one('select * from public."User" where id=%(id)s limit 1', {"id": user_id}) or {}
    # Compatibility with common columns. Header-based admin is handled in app.py.
    role = str(user.get("role") or user.get("profile") or user.get("type") or "").upper()
    is_admin = role in {"ADMIN", "ADMINISTRATOR", "SUPERADMIN"} or bool(user.get("is_admin") or False)
    area_id = user.get("area_id") or user.get("areaId") or user.get("business_area_id")
    active = not bool(user.get("deleted") or False)
    return {"id": user_id, "is_admin": is_admin, "area_id": area_id, "active": active, "raw": _json_safe(user)}


def list_roi_methods(project_keys: list[str] | None = None, published_only: bool = True) -> dict:
    if not table_exists("roi", "roi_method"):
        # Fallback to the current configuration model while migration is not applied.
        status = "PUBLISHED" if published_only else None
        legacy = list_roi_configurations(project_keys=project_keys, status=status)
        items = []
        for c in legacy.get("items", []):
            items.append({
                "id": c.get("id"), "code": str(c.get("calculation_method") or "business_result").upper(),
                "name": c.get("name"), "description": c.get("description"),
                "status": str(c.get("status") or "DRAFT").lower(), "legacy": True,
            })
        return {"items": items, "total": len(items), "compatibility_mode": True}
    clauses = ["1=1"]
    params: dict[str, Any] = {}
    if published_only:
        clauses.append("m.status='published'")
    rows = fetch_all(f"""
        select m.*, v.id as current_version_id, v.version_number, v.formula_key,
               v.status as version_status, v.published_at
          from roi.roi_method m
          left join lateral (
              select * from roi.roi_method_version v
               where v.roi_method_id=m.id
                 and (%(published_only)s=false or v.status='published')
               order by v.version_number desc limit 1
          ) v on true
         where {' and '.join(clauses)}
         order by m.name
    """, {"published_only": published_only, **params})
    return {"items": _json_safe(rows), "total": len(rows), "compatibility_mode": False}


def get_roi_method_parameters(version_id: str, editable_by: str | None = None) -> dict:
    if not table_exists("roi", "roi_method_parameter"):
        return {"items": [], "total": 0, "compatibility_mode": True}
    params: dict[str, Any] = {"version_id": version_id}
    where = "p.roi_method_version_id=%(version_id)s"
    if editable_by:
        where += " and p.editable_by in (%(editable_by)s,'BOTH','SYSTEM')"
        params["editable_by"] = editable_by.upper()
    rows = fetch_all(f"""
        select p.* from roi.roi_method_parameter p
         where {where}
         order by p.scope, p.display_order, p.label
    """, params)
    return {"items": _json_safe(rows), "total": len(rows)}


def create_task_process(task_id: str, payload: dict, project_keys: list[str] | None, user_id: str | None = None) -> dict:
    task = _task_exists(task_id, project_keys)
    if not task:
        return {"ok": False, "error": "task_not_found"}
    code = str(payload.get("code") or "").strip().upper()
    if not code:
        n = fetch_one("select count(*)::int+1 as n from roi.roi_task_process where task_id=%(task_id)s", {"task_id": task_id}) or {"n": 1}
        code = f"P{int(n['n']):03d}"
    rows = execute_returning("""
        insert into roi.roi_task_process(task_id, code, name, description, order_index, status, created_by, updated_by)
        values(%(task_id)s,%(code)s,%(name)s,%(description)s,%(order_index)s,coalesce(%(status)s,'active'),%(user_id)s,%(user_id)s)
        returning *
    """, {"task_id": task_id, "code": code, "name": payload.get("name") or "Principal", "description": payload.get("description"),
          "order_index": _safe_int(payload.get("order_index"), 0), "status": payload.get("status"), "user_id": user_id})
    item = _json_safe(rows[0]) if rows else None
    if item: _audit("roi_task_process", item["id"], "CREATE", None, item, user_id, task.get("customer_id"))
    return {"ok": bool(item), "item": item}


def list_task_processes(task_id: str, project_keys: list[str] | None = None) -> dict:
    task = _task_exists(task_id, project_keys)
    if not task:
        return {"ok": False, "error": "task_not_found", "items": [], "total": 0}
    rows = fetch_all("""
        select p.*,
               (select count(*) from roi.task_process_automation a where a.task_process_id=p.id and a.status='active')::int as automation_count
          from roi.roi_task_process p
         where p.task_id=%(task_id)s
         order by p.order_index, p.created_at
    """, {"task_id": task_id})
    return {"ok": True, "items": _json_safe(rows), "total": len(rows)}


def create_task_roi(task_id: str, payload: dict, project_keys: list[str] | None, user_id: str | None = None) -> dict:
    task = _task_exists(task_id, project_keys)
    if not task:
        return {"ok": False, "error": "task_not_found"}
    version_id = payload.get("roi_method_version_id") or payload.get("method_version_id")
    if not version_id:
        return {"ok": False, "error": "missing_roi_method_version_id"}
    active = fetch_one("select id from roi.task_roi where task_id=%(task_id)s and active_to is null and status not in ('inactive','archived') limit 1", {"task_id": task_id})
    if active:
        return {"ok": False, "error": "active_task_roi_already_exists", "task_roi_id": active["id"]}
    rows = execute_returning("""
        insert into roi.task_roi(task_id, roi_method_version_id, status, active_from, created_by)
        values(%(task_id)s,%(version_id)s,coalesce(%(status)s,'draft'),coalesce(%(active_from)s::date,current_date),%(user_id)s)
        returning *
    """, {"task_id": task_id, "version_id": version_id, "status": payload.get("status"), "active_from": payload.get("active_from"), "user_id": user_id})
    item = _json_safe(rows[0]) if rows else None
    # Always guarantee a default process for compatibility.
    if item:
        existing = fetch_one("select id from roi.roi_task_process where task_id=%(task_id)s limit 1", {"task_id": task_id})
        if not existing:
            create_task_process(task_id, {"code": "MAIN", "name": "Principal", "order_index": 0}, project_keys, user_id)
        _audit("task_roi", item["id"], "CREATE", None, item, user_id, task.get("customer_id"))
    return {"ok": bool(item), "item": item}


def get_task_roi(task_id: str, project_keys: list[str] | None = None) -> dict:
    task = _task_exists(task_id, project_keys)
    if not task:
        return {"ok": False, "error": "task_not_found"}
    row = fetch_one("""
        select tr.*, m.code as method_code, m.name as method_name, v.version_number, v.formula_key
          from roi.task_roi tr
          join roi.roi_method_version v on v.id=tr.roi_method_version_id
          join roi.roi_method m on m.id=v.roi_method_id
         where tr.task_id=%(task_id)s and tr.active_to is null
         order by tr.created_at desc limit 1
    """, {"task_id": task_id})
    if not row:
        return {"ok": True, "item": None}
    vals = fetch_all("select * from roi.task_roi_parameter_value where task_roi_id=%(id)s order by created_at", {"id": row["id"]})
    return {"ok": True, "item": _json_safe(row), "parameter_values": _json_safe(vals)}


def save_task_roi_values(payload: dict, user_id: str | None = None, is_admin: bool = False) -> dict:
    task_roi_id = payload.get("task_roi_id")
    values = payload.get("values") or []
    if not task_roi_id or not isinstance(values, list):
        return {"ok": False, "error": "missing_task_roi_id_or_values"}
    tr = fetch_one("select tr.*, t.customer_id from roi.task_roi tr join roi.roi_task t on t.id=tr.task_id where tr.id=%(id)s", {"id": task_roi_id})
    if not tr:
        return {"ok": False, "error": "task_roi_not_found"}
    saved = []
    for entry in values:
        param_id = entry.get("roi_method_parameter_id") or entry.get("parameter_id")
        param = fetch_one("select * from roi.roi_method_parameter where id=%(id)s", {"id": param_id}) if param_id else None
        if not param:
            return {"ok": False, "error": "parameter_not_found", "parameter_id": param_id}
        editable = str(param.get("editable_by") or "BOTH").upper()
        if editable == "ADMIN" and not is_admin:
            return {"ok": False, "error": "admin_required_for_parameter", "parameter_key": param.get("parameter_key")}
        value = entry.get("value")
        data_type = str(param.get("data_type") or "text").lower()
        cols = {"value_numeric": None, "value_text": None, "value_boolean": None, "value_json": None}
        if data_type in {"numeric", "number", "currency", "integer"}: cols["value_numeric"] = _safe_float(value)
        elif data_type == "boolean": cols["value_boolean"] = bool(value)
        elif data_type in {"json", "object", "array"}: cols["value_json"] = _json_dumps(value)
        else: cols["value_text"] = None if value is None else str(value)
        rows = execute_returning("""
            insert into roi.task_roi_parameter_value(
                task_roi_id, task_process_id, automation_id, participant_id,
                roi_method_parameter_id, parameter_key, value_numeric, value_text, value_boolean, value_json, filled_by
            ) values(
                %(task_roi_id)s,%(task_process_id)s,%(automation_id)s,%(participant_id)s,
                %(param_id)s,%(parameter_key)s,%(value_numeric)s,%(value_text)s,%(value_boolean)s,%(value_json)s::jsonb,%(user_id)s
            )
            on conflict (task_roi_id, scope_key, roi_method_parameter_id)
            do update set value_numeric=excluded.value_numeric, value_text=excluded.value_text,
                          value_boolean=excluded.value_boolean, value_json=excluded.value_json,
                          filled_by=excluded.filled_by, updated_at=now()
            returning *
        """, {"task_roi_id": task_roi_id, "task_process_id": entry.get("task_process_id"), "automation_id": entry.get("automation_id"),
              "participant_id": entry.get("participant_id"), "param_id": param_id, "parameter_key": param.get("parameter_key"),
              **cols, "user_id": user_id})
        if rows: saved.append(_json_safe(rows[0]))
    return {"ok": True, "items": saved, "total": len(saved)}


def create_process_automation(payload: dict, user_id: str | None = None, is_admin: bool = False) -> dict:
    if not is_admin:
        return {"ok": False, "error": "admin_required"}
    process_id = payload.get("task_process_id")
    if not process_id:
        return {"ok": False, "error": "missing_task_process_id"}
    rows = execute_returning("""
        insert into roi.task_process_automation(task_process_id,name,execution_mode,channel,status,active_from,active_to,created_by,updated_by)
        values(%(task_process_id)s,%(name)s,%(execution_mode)s,%(channel)s,coalesce(%(status)s,'active'),coalesce(%(active_from)s::date,current_date),%(active_to)s::date,%(user_id)s,%(user_id)s)
        returning *
    """, {"task_process_id": process_id, "name": payload.get("name") or "Automação", "execution_mode": payload.get("execution_mode"),
          "channel": payload.get("channel"), "status": payload.get("status"), "active_from": payload.get("active_from"),
          "active_to": payload.get("active_to"), "user_id": user_id})
    item = _json_safe(rows[0]) if rows else None
    return {"ok": bool(item), "item": item}


def add_automation_participant(automation_id: str, payload: dict, user_id: str | None = None, is_admin: bool = False) -> dict:
    if not is_admin:
        return {"ok": False, "error": "admin_required"}
    rows = execute_returning("""
        insert into roi.task_process_automation_participant(
            automation_id,agent_id,workflow_id,dag_id,agent_name_snapshot,workflow_name_snapshot,dag_name_snapshot,role,order_index,status,created_by
        ) values(%(automation_id)s,%(agent_id)s,%(workflow_id)s,%(dag_id)s,%(agent_name)s,%(workflow_name)s,%(dag_name)s,%(role)s,%(order_index)s,coalesce(%(status)s,'active'),%(user_id)s)
        returning *
    """, {"automation_id": automation_id, "agent_id": payload.get("agent_id"), "workflow_id": payload.get("workflow_id"), "dag_id": payload.get("dag_id"),
          "agent_name": payload.get("agent_name") or payload.get("agent_name_snapshot"), "workflow_name": payload.get("workflow_name") or payload.get("workflow_name_snapshot"),
          "dag_name": payload.get("dag_name") or payload.get("dag_name_snapshot"), "role": payload.get("role"), "order_index": _safe_int(payload.get("order_index"), 0),
          "status": payload.get("status"), "user_id": user_id})
    item = _json_safe(rows[0]) if rows else None
    return {"ok": bool(item), "item": item}


def list_process_automations(task_id: str, project_keys: list[str] | None = None) -> dict:
    task = _task_exists(task_id, project_keys)
    if not task:
        return {"ok": False, "error": "task_not_found", "items": [], "total": 0}
    rows = fetch_all("""
        select a.*, p.name as process_name,
               coalesce(jsonb_agg(to_jsonb(ap) order by ap.order_index) filter (where ap.id is not null),'[]'::jsonb) participants
          from roi.task_process_automation a
          join roi.roi_task_process p on p.id=a.task_process_id
          left join roi.task_process_automation_participant ap on ap.automation_id=a.id
         where p.task_id=%(task_id)s
         group by a.id,p.name
         order by p.order_index,a.created_at
    """, {"task_id": task_id})
    return {"ok": True, "items": _json_safe(rows), "total": len(rows)}


def calculate_task_roi(task_roi_id: str, payload: dict, user_id: str | None = None) -> dict:
    tr = fetch_one("""
        select tr.*, t.customer_id,t.project_id,t.owner_id,t.owner_area_id,m.code as method_code,v.version_number,v.formula_key
          from roi.task_roi tr join roi.roi_task t on t.id=tr.task_id
          join roi.roi_method_version v on v.id=tr.roi_method_version_id
          join roi.roi_method m on m.id=v.roi_method_id
         where tr.id=%(id)s
    """, {"id": task_roi_id})
    if not tr:
        return {"ok": False, "error": "task_roi_not_found"}
    params = fetch_all("select * from roi.task_roi_parameter_value where task_roi_id=%(id)s", {"id": task_roi_id})
    flat: dict[str, Any] = {}
    for v in params:
        val = v.get("value_numeric")
        if val is None: val = v.get("value_boolean")
        if val is None: val = v.get("value_text")
        if val is None: val = v.get("value_json")
        # Consolidated calculation uses TASK_ROI/Principal values; detailed items preserve scope.
        flat[v.get("parameter_key")] = val
    flat.update(payload.get("overrides") or {})
    flat["calculation_method"] = str(tr.get("method_code") or tr.get("formula_key") or "business_result").lower()
    simulation = simulate_roi(flat)
    snapshot = {"task_roi": tr, "parameters": params, "inputs": flat, "simulation": simulation}
    period_start = payload.get("period_start") or date.today().replace(day=1).isoformat()
    period_end = payload.get("period_end") or date.today().isoformat()
    rows = execute_returning("""
        insert into roi.task_roi_calculation_result(
          task_roi_id,period_start,period_end,gross_benefit_brl,automation_cost_brl,net_savings_brl,roi_pct,payback_months,calculation_snapshot_json,calculated_by
        ) values(%(task_roi_id)s,%(period_start)s::date,%(period_end)s::date,%(gross)s,%(cost)s,%(net)s,%(roi)s,%(payback)s,%(snapshot)s::jsonb,%(user_id)s)
        returning *
    """, {"task_roi_id": task_roi_id, "period_start": period_start, "period_end": period_end,
          "gross": simulation.get("gross_savings_after_review_brl", simulation.get("gross_savings_brl", 0)), "cost": simulation.get("ai_cost_brl", 0),
          "net": simulation.get("net_savings_brl", 0), "roi": simulation.get("roi_pct", 0), "payback": simulation.get("payback_months"),
          "snapshot": _json_dumps(snapshot), "user_id": user_id})
    item = _json_safe(rows[0]) if rows else None
    # Legacy shadow write keeps current dashboard/front alive.
    if item:
        execute("""
            insert into roi.roi_calculation_result(customer_id,project_id,task_id,entity_type,entity_id,period_start,period_end,
                gross_savings_brl,ai_cost_brl,net_savings_brl,roi_pct,payback_months,calculation_snapshot_json,calculated_by)
            values(%(customer_id)s,%(project_id)s,%(task_id)s,'TASK_ROI',%(task_roi_id)s,%(period_start)s::date,%(period_end)s::date,
                %(gross)s,%(cost)s,%(net)s,%(roi)s,%(payback)s,%(snapshot)s::jsonb,%(user_id)s)
        """, {"customer_id": tr.get("customer_id"), "project_id": tr.get("project_id"), "task_id": tr.get("task_id"), "task_roi_id": task_roi_id,
              "period_start": period_start, "period_end": period_end, "gross": simulation.get("gross_savings_after_review_brl", simulation.get("gross_savings_brl", 0)),
              "cost": simulation.get("ai_cost_brl", 0), "net": simulation.get("net_savings_brl", 0), "roi": simulation.get("roi_pct", 0),
              "payback": simulation.get("payback_months"), "snapshot": _json_dumps(snapshot), "user_id": user_id})
    return {"ok": bool(item), "item": item, "simulation": simulation, "snapshot": _json_safe(snapshot)}