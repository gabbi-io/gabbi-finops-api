from __future__ import annotations

from datetime import datetime, timezone, date
from typing import Any

from db import fetch_all, fetch_one, execute, execute_returning, table_exists


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v or 0)
    except Exception:
        return default


def _safe_int(v: Any, default: int = 0) -> int:
    try:
        return int(v or 0)
    except Exception:
        return default


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _scope_filter_sql(alias: str, project_keys: list[str] | None) -> tuple[str, dict]:
    if project_keys is None:
        return "1=1", {}
    if len(project_keys) == 0:
        return "1=0", {"project_keys": []}
    return f"{alias}.project_id = any(%(project_keys)s)", {"project_keys": project_keys}


def simulate_roi(payload: dict) -> dict:
    """Pure ROI simulation used by the configuration screen preview."""
    method = payload.get("calculation_method") or payload.get("method") or "business_result"
    attribution_pct = _safe_float(payload.get("attribution_pct", payload.get("gabbi_attribution_pct", 100)), 100.0)
    attribution_factor = max(0.0, min(100.0, attribution_pct)) / 100.0

    agent_monthly_cost = _safe_float(payload.get("agent_monthly_cost_brl", payload.get("monthly_ai_cost_brl", 0)))
    implementation_cost = _safe_float(payload.get("implementation_cost_brl", 0))
    human_review_pct = _safe_float(payload.get("human_review_pct", 0))
    human_review_factor = max(0.0, min(100.0, human_review_pct)) / 100.0

    if method in ("time_saved", "time", "h_h"):
        avg_manual_time_min = _safe_float(payload.get("avg_manual_time_min"))
        monthly_volume = _safe_float(payload.get("monthly_volume", payload.get("expected_events_month", 0)))
        cost_per_hour = _safe_float(payload.get("cost_per_hour_brl", payload.get("cost_per_hour", 0)))
        coverage_pct = _safe_float(payload.get("coverage_pct", 100)) / 100.0
        gross_savings = (avg_manual_time_min / 60.0) * monthly_volume * cost_per_hour * coverage_pct * attribution_factor
        calculation_base = "avg_manual_time_min * monthly_volume * cost_per_hour * coverage_pct * attribution_pct"
    else:
        unit_value = _safe_float(payload.get("event_unit_value_brl", payload.get("unit_value_brl", 0)))
        events_month = _safe_float(payload.get("expected_events_month", payload.get("monthly_volume", 0)))
        gross_savings = unit_value * events_month * attribution_factor
        calculation_base = "event_unit_value_brl * expected_events_month * attribution_pct"

    review_penalty = gross_savings * human_review_factor
    gross_after_review = max(gross_savings - review_penalty, 0.0)
    total_monthly_cost = agent_monthly_cost
    net_savings = gross_after_review - total_monthly_cost
    roi_pct = (net_savings / total_monthly_cost * 100.0) if total_monthly_cost > 0 else 0.0
    payback_months = (implementation_cost / net_savings) if implementation_cost > 0 and net_savings > 0 else ((total_monthly_cost / net_savings) if net_savings > 0 else None)

    benefit_share_pct = (gross_after_review / (gross_after_review + total_monthly_cost) * 100.0) if (gross_after_review + total_monthly_cost) > 0 else 0.0
    cost_share_pct = 100.0 - benefit_share_pct if (gross_after_review + total_monthly_cost) > 0 else 0.0

    return {
        "calculated_at": _now_iso(),
        "method": method,
        "calculation_base": calculation_base,
        "gross_savings_brl": round(gross_savings, 2),
        "human_review_penalty_brl": round(review_penalty, 2),
        "gross_savings_after_review_brl": round(gross_after_review, 2),
        "ai_cost_brl": round(total_monthly_cost, 2),
        "net_savings_brl": round(net_savings, 2),
        "roi_pct": round(roi_pct, 2),
        "payback_months": round(payback_months, 4) if payback_months is not None else None,
        "payback_days": round(payback_months * 30, 1) if payback_months is not None else None,
        "chart": {
            "benefit_pct": round(benefit_share_pct, 2),
            "cost_pct": round(cost_share_pct, 2),
        },
        "inputs": payload,
    }


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
    return {"items": rows, "total": len(rows)}


def create_roi_configuration(payload: dict, customer_ctx: dict, user_id: str | None = None) -> dict:
    project_id = payload.get("project_id") or payload.get("project_key")
    if not project_id:
        projects = customer_ctx.get("projects") or []
        project_id = projects[0].get("id") if projects else None
    if not project_id:
        return {"ok": False, "error": "missing_project_id"}

    simulation = simulate_roi(payload)
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
            "task_id": payload.get("task_id"),
            "agent_id": payload.get("agent_id"),
            "workflow_id": payload.get("workflow_id"),
            "dag_id": payload.get("dag_id"),
            "name": payload.get("name") or payload.get("title") or "Configuração ROI",
            "description": payload.get("description"),
            "calculation_method": payload.get("calculation_method") or payload.get("method") or "business_result",
            "value_event_name": payload.get("value_event_name") or payload.get("event_name"),
            "event_unit_value_brl": _safe_float(payload.get("event_unit_value_brl", payload.get("unit_value_brl", 0))),
            "expected_events_month": _safe_float(payload.get("expected_events_month", 0)),
            "attribution_pct": _safe_float(payload.get("attribution_pct", payload.get("gabbi_attribution_pct", 100))),
            "baseline_monthly_brl": _safe_float(payload.get("baseline_monthly_brl", 0)),
            "agent_monthly_cost_brl": _safe_float(payload.get("agent_monthly_cost_brl", 0)),
            "human_review_pct": _safe_float(payload.get("human_review_pct", 0)),
            "require_evidence": bool(payload.get("require_evidence", False)),
            "human_review_required": bool(payload.get("human_review_required", False)),
            "responsible_area": payload.get("responsible_area") or payload.get("area"),
            "assumptions_json": __import__("json").dumps(payload, ensure_ascii=False),
            "last_simulation_json": __import__("json").dumps(simulation, ensure_ascii=False),
            "user_id": user_id,
        },
    )
    row = rows[0] if rows else None
    if row:
        _audit("roi_configuration", row["id"], "CREATE", None, row, user_id, customer_ctx.get("customer_id"))
    return {"ok": True, "item": row, "simulation": simulation}


def get_roi_configuration(config_id: str, project_keys: list[str] | None = None) -> dict | None:
    where, params = _scope_filter_sql("c", project_keys)
    params["id"] = config_id
    return fetch_one(f"select c.* from roi.roi_configuration c where c.id=%(id)s and {where}", params)


def update_roi_configuration(config_id: str, payload: dict, project_keys: list[str] | None, user_id: str | None = None) -> dict:
    current = get_roi_configuration(config_id, project_keys)
    if not current:
        return {"ok": False, "error": "not_found"}
    if current.get("status") == "PUBLISHED":
        return {"ok": False, "error": "published_configuration_is_immutable"}
    merged = {**current, **payload}
    simulation = simulate_roi(merged)
    rows = execute_returning(
        """
        update roi.roi_configuration set
            name = coalesce(%(name)s, name), description = %(description)s,
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
            "name": merged.get("name"),
            "description": merged.get("description"),
            "calculation_method": merged.get("calculation_method"),
            "value_event_name": merged.get("value_event_name"),
            "event_unit_value_brl": _safe_float(merged.get("event_unit_value_brl")),
            "expected_events_month": _safe_float(merged.get("expected_events_month")),
            "attribution_pct": _safe_float(merged.get("attribution_pct")),
            "baseline_monthly_brl": _safe_float(merged.get("baseline_monthly_brl")),
            "agent_monthly_cost_brl": _safe_float(merged.get("agent_monthly_cost_brl")),
            "human_review_pct": _safe_float(merged.get("human_review_pct")),
            "require_evidence": bool(merged.get("require_evidence")),
            "human_review_required": bool(merged.get("human_review_required")),
            "responsible_area": merged.get("responsible_area"),
            "assumptions_json": __import__("json").dumps(merged, default=str, ensure_ascii=False),
            "last_simulation_json": __import__("json").dumps(simulation, ensure_ascii=False),
            "user_id": user_id,
        },
    )
    item = rows[0] if rows else None
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
    snapshot = __import__("json").dumps(current, default=str, ensure_ascii=False)
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
    item = rows[0] if rows else None
    _audit("roi_configuration", config_id, "PUBLISH", current, item, user_id, current.get("customer_id"))
    return {"ok": True, "item": item, "version": version}


def archive_roi_configuration(config_id: str, project_keys: list[str] | None, user_id: str | None = None) -> dict:
    current = get_roi_configuration(config_id, project_keys)
    if not current:
        return {"ok": False, "error": "not_found"}
    rows = execute_returning("update roi.roi_configuration set status='ARCHIVED', updated_at=now(), updated_by=%(user_id)s where id=%(id)s returning *", {"id": config_id, "user_id": user_id})
    item = rows[0] if rows else None
    _audit("roi_configuration", config_id, "ARCHIVE", current, item, user_id, current.get("customer_id"))
    return {"ok": True, "item": item}


def list_roi_tasks(project_keys: list[str] | None = None) -> dict:
    where, params = _scope_filter_sql("t", project_keys)
    rows = fetch_all(f"select t.* from roi.roi_task t where {where} order by t.updated_at desc nulls last, t.created_at desc", params) if table_exists("roi", "roi_task") else []
    return {"items": rows, "total": len(rows)}


def create_roi_task(payload: dict, customer_ctx: dict, user_id: str | None = None) -> dict:
    project_id = payload.get("project_id") or payload.get("project_key")
    if not project_id:
        projects = customer_ctx.get("projects") or []
        project_id = projects[0].get("id") if projects else None
    if not project_id:
        return {"ok": False, "error": "missing_project_id"}
    rows = execute_returning(
        """
        insert into roi.roi_task(customer_id, project_id, code, name, description, area_id, process_name, owner_id, status, created_by, updated_by)
        values(%(customer_id)s, %(project_id)s, %(code)s, %(name)s, %(description)s, %(area_id)s, %(process_name)s, %(owner_id)s, coalesce(%(status)s,'DRAFT'), %(user_id)s, %(user_id)s)
        returning *
        """,
        {"customer_id": customer_ctx.get("customer_id"), "project_id": project_id, "code": payload.get("code"), "name": payload.get("name"), "description": payload.get("description"), "area_id": payload.get("area_id"), "process_name": payload.get("process_name"), "owner_id": payload.get("owner_id"), "status": payload.get("status"), "user_id": user_id},
    )
    item = rows[0] if rows else None
    _audit("roi_task", item["id"], "CREATE", None, item, user_id, customer_ctx.get("customer_id")) if item else None
    return {"ok": True, "item": item}


def save_task_baseline(task_id: str, payload: dict, project_keys: list[str] | None, user_id: str | None = None) -> dict:
    where, params = _scope_filter_sql("t", project_keys)
    params["id"] = task_id
    task = fetch_one(f"select * from roi.roi_task t where t.id=%(id)s and {where}", params)
    if not task:
        return {"ok": False, "error": "task_not_found"}
    rows = execute_returning(
        """
        insert into roi.roi_task_baseline(task_id, avg_manual_time_min, monthly_volume, cost_per_hour_brl, manual_sla_hours, manual_error_rate, baseline_date, confidence_level, evidence_required, approved, created_by)
        values(%(task_id)s, %(avg_manual_time_min)s, %(monthly_volume)s, %(cost_per_hour_brl)s, %(manual_sla_hours)s, %(manual_error_rate)s, coalesce(%(baseline_date)s, current_date), %(confidence_level)s, %(evidence_required)s, false, %(user_id)s)
        returning *
        """,
        {"task_id": task_id, "avg_manual_time_min": _safe_float(payload.get("avg_manual_time_min")), "monthly_volume": _safe_float(payload.get("monthly_volume")), "cost_per_hour_brl": _safe_float(payload.get("cost_per_hour_brl", payload.get("cost_per_hour", 0))), "manual_sla_hours": _safe_float(payload.get("manual_sla_hours")), "manual_error_rate": _safe_float(payload.get("manual_error_rate")), "baseline_date": payload.get("baseline_date"), "confidence_level": payload.get("confidence_level") or "MEDIUM", "evidence_required": bool(payload.get("evidence_required", True)), "user_id": user_id},
    )
    return {"ok": True, "item": rows[0] if rows else None}


def approve_task_baseline(task_id: str, project_keys: list[str] | None, user_id: str | None = None) -> dict:
    where, params = _scope_filter_sql("t", project_keys)
    params["id"] = task_id
    task = fetch_one(f"select * from roi.roi_task t where t.id=%(id)s and {where}", params)
    if not task:
        return {"ok": False, "error": "task_not_found"}
    rows = execute_returning(
        """
        update roi.roi_task_baseline set approved=true, approved_by=%(user_id)s, approved_at=now()
        where id = (select id from roi.roi_task_baseline where task_id=%(task_id)s order by created_at desc limit 1)
        returning *
        """,
        {"task_id": task_id, "user_id": user_id},
    )
    return {"ok": bool(rows), "item": rows[0] if rows else None}


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
    return {"items": rows, "total": len(rows)}


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
        {"task_id": task_id, "agent_id": payload.get("agent_id"), "agent_name": payload.get("agent_name"), "workflow_id": payload.get("workflow_id"), "dag_id": payload.get("dag_id"), "coverage_pct": _safe_float(payload.get("coverage_pct", 100)), "human_review_pct": _safe_float(payload.get("human_review_pct", 0)), "execution_mode": payload.get("execution_mode"), "channel": payload.get("channel"), "status": payload.get("status"), "active_from": payload.get("active_from"), "user_id": user_id},
    )
    return {"ok": True, "item": rows[0] if rows else None}


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
    return {"kpis": k, "days": days}


def task_result(task_id: str, project_keys: list[str] | None = None) -> dict:
    where, params = _scope_filter_sql("t", project_keys)
    params["task_id"] = task_id
    task = fetch_one(f"select * from roi.roi_task t where t.id=%(task_id)s and {where}", params)
    if not task:
        return {"ok": False, "error": "task_not_found"}
    baseline = fetch_one("select * from roi.roi_task_baseline where task_id=%(task_id)s order by created_at desc limit 1", {"task_id": task_id})
    mappings = fetch_all("select * from roi.roi_task_mapping where task_id=%(task_id)s order by created_at desc", {"task_id": task_id})
    results = fetch_all("select * from roi.roi_calculation_result where task_id=%(task_id)s order by period_start desc limit 24", {"task_id": task_id})
    return {"ok": True, "task": task, "baseline": baseline, "mappings": mappings, "results": results}


def _audit(entity_type: str, entity_id: str, event_type: str, before: Any, after: Any, user_id: str | None, customer_id: str | None) -> None:
    if not table_exists("roi", "roi_audit_event"):
        return
    import json
    try:
        execute(
            """
            insert into roi.roi_audit_event(event_type, entity_type, entity_id, user_id, customer_id, before_json, after_json)
            values(%(event_type)s, %(entity_type)s, %(entity_id)s, %(user_id)s, %(customer_id)s, %(before_json)s::jsonb, %(after_json)s::jsonb)
            """,
            {"event_type": event_type, "entity_type": entity_type, "entity_id": entity_id, "user_id": user_id, "customer_id": customer_id, "before_json": json.dumps(before, default=str, ensure_ascii=False) if before is not None else None, "after_json": json.dumps(after, default=str, ensure_ascii=False) if after is not None else None},
        )
    except Exception as e:
        print(f"[ROI][WARN] audit failed: {e}")
