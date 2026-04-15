from __future__ import annotations

import json
import math
import os
import random
from calendar import monthrange
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

STATE_PATH = os.environ.get(
    "FINOPS_POC_STATE_PATH",
    os.path.join(os.path.dirname(__file__), "data", "state.json"),
)

DEFAULT_TENANT = "spread-tecnologia"

DEFAULT_PRICING = [
    {"model": "gpt-4o", "min_tokens": 800, "min_cost": 0.80},
    {"model": "gpt-4.1", "min_tokens": 1000, "min_cost": 1.00},
    {"model": "gpt-4o-mini", "min_tokens": 1200, "min_cost": 0.60},
]

DEFAULT_TASKS = [
    {"task_id": "T-ANALISE-CONTRATO", "task_name": "Analisar risco de contrato"},
    {"task_id": "T-RESUMO-PDF", "task_name": "Resumir documento PDF"},
    {"task_id": "T-CLASSIFICAR-EMAIL", "task_name": "Classificar e-mail"},
    {"task_id": "T-TRIAGEM-INCIDENTE", "task_name": "Triagem de incidente"},
]

DEFAULT_FLOWS = [
    {"flow_id": "F-N8N-RISCO", "flow_name": "n8n: risco-contrato"},
    {"flow_id": "F-AIRFLOW-REL", "flow_name": "airflow: relatorio-finops"},
    {"flow_id": "F-N8N-TRIAGEM", "flow_name": "n8n: triagem-itsm"},
]

SCENARIOS = ("normal", "growth", "explosion")
DATA_SOURCES = ("fake_static", "fake_live", "real")
ROI_HOURLY_RATE_BRL = 45.0
ROI_MINUTES_SAVED_PER_INTERACTION = 6.0
BUDGET_MONTHLY_BRL = 10000.0
BUDGET_ALERT_PERCENT = 80.0


def _now() -> datetime:
    return datetime.now()


def _money(x: float) -> float:
    return float(f"{x:.2f}")


def _ensure_dir(path: str):
    os.makedirs(os.path.dirname(path), exist_ok=True)


def load_state() -> Dict[str, Any]:
    if not os.path.exists(STATE_PATH):
        _ensure_dir(STATE_PATH)
        state = seed_state()
        save_state(state)
        return state
    with open(STATE_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_state(state: Dict[str, Any]) -> None:
    _ensure_dir(STATE_PATH)
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def seed_state() -> Dict[str, Any]:
    random.seed(42)
    state = {
        "tenant_id": DEFAULT_TENANT,
        "data_source": "fake_static",
        "scenario": "growth",
        "days": 30,
        "grafana_embed_url": "",
        "pricing": DEFAULT_PRICING,
        "tasks": DEFAULT_TASKS,
        "flows": DEFAULT_FLOWS,
        "interactions": [],
        "accumulators": {},
        "ledger": [],
        "seq": 0,
    }
    _generate_static_dataset(state, days=30, scenario="growth")
    return state


def _scenario_multiplier(day_index: int, days: int, scenario: str) -> float:
    if scenario == "normal":
        return max(0.6, 1.0 + 0.08 * math.sin(day_index / 2.2))
    t = day_index / max(days - 1, 1)
    base = 1.0 + (math.exp(1.7 * t) - 1.0)
    base *= (0.92 + 0.12 * math.sin(day_index / 1.7))
    if scenario == "explosion":
        spike_day = int(days * 0.65)
        if day_index == spike_day:
            base *= 5.0
    return max(base, 0.4)


def _price_for(pricing: List[Dict[str, Any]], model: str) -> Tuple[int, float]:
    p = next((x for x in pricing if x["model"] == model), None)
    if not p:
        return 1000, 1.0
    return int(p["min_tokens"]), float(p["min_cost"])


def _pick(lst: List[Dict[str, Any]]) -> Dict[str, Any]:
    return random.choice(lst)


def _bucket_key(
    source_type: str,
    model: str,
    collaborator_id: Optional[str],
    task_id: Optional[str],
) -> str:
    if source_type == "MANUAL":
        return f"MANUAL|{model}|{collaborator_id or 'collab-001'}"
    return f"AUTOMATION|{model}|{task_id or 'T-UNKNOWN'}"


def _append_interaction(state: Dict[str, Any], row: Dict[str, Any]) -> None:
    state["interactions"].append(row)
    if len(state["interactions"]) > 5000:
        state["interactions"] = state["interactions"][-5000:]


def _append_ledger(state: Dict[str, Any], row: Dict[str, Any]) -> None:
    state["ledger"].append(row)
    if len(state["ledger"]) > 5000:
        state["ledger"] = state["ledger"][-5000:]


def _acc_get(state: Dict[str, Any], key: str) -> Dict[str, Any]:
    return state["accumulators"].setdefault(
        key, {"pending_tokens": 0, "close_count": 0, "updated_at": ""}
    )


def _idempotency_key(state: Dict[str, Any], key: str) -> str:
    state["seq"] += 1
    return f"{state['tenant_id']}|{key}|{state['seq']}"


def simulate_event(
    state: Dict[str, Any],
    occurred_at: datetime,
    source_type: str,
    model: str,
    tokens_total: int,
    collaborator_id: Optional[str] = None,
    task_id: Optional[str] = None,
    flow_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Replica o comportamento REAL da Fase 1:
    - Interaction sempre gravada (audit)
    - Acumula tokens no bucket
    - Se pending >= min_tokens => cria 1..N CostLedger e reduz pending
    """
    min_tokens, min_cost = _price_for(state["pricing"], model)
    actor_type = "COLLABORATOR" if source_type == "MANUAL" else "AGENT"
    conversation_id = f"conv-{random.randint(1000, 9999)}"

    interaction = {
        "occurred_at": occurred_at.strftime("%Y-%m-%d %H:%M:%S"),
        "tenant_id": state["tenant_id"],
        "source_type": source_type,
        "model": model,
        "actor_type": actor_type,
        "tokens_total": int(tokens_total),
        "collaborator_id": collaborator_id if source_type == "MANUAL" else None,
        "task_id": task_id if source_type == "AUTOMATION" else None,
        "flow_id": flow_id if source_type == "AUTOMATION" else None,
        "conversation_id": conversation_id,
    }
    _append_interaction(state, interaction)

    bkey = _bucket_key(source_type, model, collaborator_id, task_id)
    acc = _acc_get(state, bkey)
    acc["pending_tokens"] += int(tokens_total)

    closes = 0
    ledgers_created = 0

    while acc["pending_tokens"] >= min_tokens:
        acc["pending_tokens"] -= min_tokens
        acc["close_count"] += 1
        closes += 1

        ledger = {
            "occurred_at": occurred_at.strftime("%Y-%m-%d %H:%M:%S"),
            "tenant_id": state["tenant_id"],
            "source_type": source_type,
            "model": model,
            "tokens_billed": int(min_tokens),
            "amount": _money(min_cost),
            "task_id": task_id if source_type == "AUTOMATION" else None,
            "flow_id": flow_id if source_type == "AUTOMATION" else None,
            "idempotency_key": _idempotency_key(state, bkey),
        }
        _append_ledger(state, ledger)
        ledgers_created += 1

    acc["updated_at"] = _now().strftime("%Y-%m-%d %H:%M:%S")

    return {
        "interaction": interaction,
        "bucket_key": bkey,
        "min_tokens": min_tokens,
        "min_cost": min_cost,
        "closes": closes,
        "ledgers_created": ledgers_created,
        "pending_tokens": acc["pending_tokens"],
        "close_count": acc["close_count"],
    }


def _generate_static_dataset(state: Dict[str, Any], days: int, scenario: str) -> None:
    state["interactions"] = []
    state["ledger"] = []
    state["accumulators"] = {}
    state["seq"] = 0

    end_date = _now().date()
    start_date = end_date - timedelta(days=days - 1)
    models = [p["model"] for p in state["pricing"]]

    for i in range(days):
        d = start_date + timedelta(days=i)
        mult = _scenario_multiplier(i, days, scenario)
        events = min(220, int(28 * mult) + random.randint(0, 10))

        for _ in range(events):
            source_type = "AUTOMATION" if random.random() < 0.62 else "MANUAL"
            model = random.choice(models)

            min_tokens, _ = _price_for(state["pricing"], model)
            tokens = random.randint(int(min_tokens * 0.3), int(min_tokens * 1.6))
            occurred_at = datetime(
                d.year, d.month, d.day,
                random.randint(8, 19),
                random.randint(0, 59),
                random.randint(0, 59),
            )

            if source_type == "MANUAL":
                simulate_event(state, occurred_at, source_type, model, tokens, collaborator_id="collab-001")
            else:
                task = _pick(state["tasks"])
                flow = _pick(state["flows"])
                simulate_event(
                    state,
                    occurred_at,
                    source_type,
                    model,
                    tokens,
                    task_id=task["task_id"],
                    flow_id=flow["flow_id"],
                )


def apply_controls(state: Dict[str, Any], data_source: str, scenario: str, days: int) -> None:
    if data_source in DATA_SOURCES:
        state["data_source"] = data_source
    if scenario in SCENARIOS:
        state["scenario"] = scenario
    state["days"] = int(days)

    if state["data_source"] == "fake_static":
        _generate_static_dataset(state, days=state["days"], scenario=state["scenario"])


def reset_live(state: Dict[str, Any]) -> None:
    keep = {k: state[k] for k in ["tenant_id", "data_source", "scenario", "days", "grafana_embed_url", "pricing", "tasks", "flows"]}
    new_state = seed_state()
    for k, v in keep.items():
        new_state[k] = v

    new_state["data_source"] = "fake_live"
    new_state["interactions"] = []
    new_state["ledger"] = []
    new_state["accumulators"] = {}
    new_state["seq"] = 0

    state.clear()
    state.update(new_state)


def step_live(state: Dict[str, Any], steps: int = 10, spike: bool = False) -> Dict[str, Any]:
    if state["data_source"] != "fake_live":
        state["data_source"] = "fake_live"

    models = [p["model"] for p in state["pricing"]]
    created_ledgers = 0
    created_interactions = 0

    base_mult = 1.0
    if state["scenario"] == "growth":
        base_mult = 1.6
    if state["scenario"] == "explosion":
        base_mult = 2.0
    if spike:
        base_mult *= 6.0

    for _ in range(int(steps)):
        source_type = "AUTOMATION" if random.random() < 0.65 else "MANUAL"
        model = random.choice(models)
        min_tokens, _ = _price_for(state["pricing"], model)
        tokens = random.randint(int(min_tokens * 0.2), int(min_tokens * (1.4 * base_mult)))
        ts = _now() - timedelta(seconds=random.randint(0, 180))

        if source_type == "MANUAL":
            out = simulate_event(state, ts, source_type, model, tokens, collaborator_id="collab-001")
        else:
            task = _pick(state["tasks"])
            flow = _pick(state["flows"])
            out = simulate_event(
                state, ts, source_type, model, tokens,
                task_id=task["task_id"], flow_id=flow["flow_id"]
            )

        created_interactions += 1
        created_ledgers += out["ledgers_created"]

    return {"created_interactions": created_interactions, "created_ledgers": created_ledgers}


def update_pricing(state: Dict[str, Any], model: str, min_tokens: int, min_cost: float) -> bool:
    found = False
    for p in state["pricing"]:
        if p["model"] == model:
            p["min_tokens"] = int(min_tokens)
            p["min_cost"] = float(min_cost)
            found = True
            break
    if not found:
        state["pricing"].append({"model": model, "min_tokens": int(min_tokens), "min_cost": float(min_cost)})

    if state.get("data_source") == "fake_static":
        _generate_static_dataset(state, days=int(state.get("days", 30)), scenario=str(state.get("scenario", "growth")))
    return True


def set_grafana_url(state: Dict[str, Any], url: str) -> None:
    state["grafana_embed_url"] = (url or "").strip()


def summarize(state: Dict[str, Any]) -> Dict[str, Any]:
    days = int(state.get("days", 30))
    cutoff = _now() - timedelta(days=days)

    ledger = [
        r for r in state["ledger"]
        if datetime.strptime(r["occurred_at"], "%Y-%m-%d %H:%M:%S") >= cutoff
    ]
    interactions = [
        r for r in state["interactions"]
        if datetime.strptime(r["occurred_at"], "%Y-%m-%d %H:%M:%S") >= cutoff
    ]

    total_cost = sum(float(r["amount"]) for r in ledger)
    interaction_rows_count = len(interactions)
    estimated_savings_brl = interaction_rows_count * (ROI_MINUTES_SAVED_PER_INTERACTION / 60.0) * ROI_HOURLY_RATE_BRL
    net_value_brl = estimated_savings_brl - total_cost
    roi_percent = ((net_value_brl / total_cost) * 100.0) if total_cost > 0 else 0.0

    today = _now()
    month_days = max(1, monthrange(today.year, today.month)[1])
    budget_used_brl = total_cost
    budget_used_percent = (budget_used_brl / BUDGET_MONTHLY_BRL * 100.0) if BUDGET_MONTHLY_BRL > 0 else 0.0
    budget_forecast_brl = (budget_used_brl / max(1, days)) * month_days
    budget_forecast_percent = (budget_forecast_brl / BUDGET_MONTHLY_BRL * 100.0) if BUDGET_MONTHLY_BRL > 0 else 0.0
    budget_alert = budget_used_percent >= BUDGET_ALERT_PERCENT
    forecast_alert = budget_forecast_percent >= 100.0

    by_source: Dict[str, float] = {}
    by_model: Dict[str, float] = {}
    by_day: Dict[str, float] = {}
    by_task: Dict[str, float] = {}
    by_flow: Dict[str, float] = {}

    for r in ledger:
        by_source[r["source_type"]] = by_source.get(r["source_type"], 0.0) + float(r["amount"])
        by_model[r["model"]] = by_model.get(r["model"], 0.0) + float(r["amount"])
        day = r["occurred_at"][:10]
        by_day[day] = by_day.get(day, 0.0) + float(r["amount"])
        if r.get("task_id"):
            by_task[r["task_id"]] = by_task.get(r["task_id"], 0.0) + float(r["amount"])
        if r.get("flow_id"):
            by_flow[r["flow_id"]] = by_flow.get(r["flow_id"], 0.0) + float(r["amount"])

    task_names = {t["task_id"]: t["task_name"] for t in state["tasks"]}
    flow_names = {f["flow_id"]: f["flow_name"] for f in state["flows"]}

    top_tasks = sorted(by_task.items(), key=lambda x: x[1], reverse=True)[:10]
    top_flows = sorted(by_flow.items(), key=lambda x: x[1], reverse=True)[:10]

    end_day = _now().date()
    start_day = end_day - timedelta(days=days - 1)
    series_days = []
    for i in range(days):
        d = start_day + timedelta(days=i)
        ds = d.strftime("%Y-%m-%d")
        series_days.append({"day": ds, "cost": _money(by_day.get(ds, 0.0))})

    alert_messages = []
    if budget_alert:
        alert_messages.append(f"Budget mensal acima de {int(BUDGET_ALERT_PERCENT)}%.")
    if forecast_alert:
        alert_messages.append("Forecast mensal acima do orçamento.")

    return {
        "tenant_id": state["tenant_id"],
        "data_source": state.get("data_source", "fake_static"),
        "scenario": state.get("scenario", "growth"),
        "days": days,
        "grafana_embed_url": state.get("grafana_embed_url", ""),
        "kpis": {
            "total_cost": _money(total_cost),
            "manual_cost": _money(by_source.get("MANUAL", 0.0)),
            "automation_cost": _money(by_source.get("AUTOMATION", 0.0)),
            "ledger_rows": len(ledger),
            "interaction_rows": interaction_rows_count,
            "estimated_savings_brl": _money(estimated_savings_brl),
            "net_value_brl": _money(net_value_brl),
            "roi_percent": _money(roi_percent),
            "budget_monthly_brl": _money(BUDGET_MONTHLY_BRL),
            "budget_used_brl": _money(budget_used_brl),
            "budget_used_percent": _money(budget_used_percent),
            "budget_forecast_brl": _money(budget_forecast_brl),
            "budget_forecast_percent": _money(budget_forecast_percent),
            "budget_alert_threshold_percent": _money(BUDGET_ALERT_PERCENT),
        },
        "series": {
            "cost_by_day": series_days,
            "cost_by_model": [{"model": m, "cost": _money(c)} for m, c in sorted(by_model.items(), key=lambda x: x[1], reverse=True)],
            "cost_by_source": [{"source": s, "cost": _money(c)} for s, c in sorted(by_source.items(), key=lambda x: x[1], reverse=True)],
            "top_tasks": [{"task_id": tid, "task_name": task_names.get(tid, tid), "cost": _money(c)} for tid, c in top_tasks],
            "top_flows": [{"flow_id": fid, "flow_name": flow_names.get(fid, fid), "cost": _money(c)} for fid, c in top_flows],
        },
        "tables": {
            "ledger_rows": sorted(ledger, key=lambda x: x["occurred_at"], reverse=True)[:200],
            "interaction_rows": sorted(interactions, key=lambda x: x["occurred_at"], reverse=True)[:200],
            "accumulators": _accumulator_table(state),
            "pricing": state["pricing"],
        },
        "alerts": {
            "budget_alert": budget_alert,
            "forecast_alert": forecast_alert,
            "messages": alert_messages,
        },
        "optimization": {
            "top_recommendation": None,
        },
        "showback": {
            "by_project": [],
            "by_agent": [],
            "by_source": [
                {"label": s, "amount_brl": _money(c)}
                for s, c in sorted(by_source.items(), key=lambda x: x[1], reverse=True)
            ],
        },
        "recommendations": [],
        "source": "fake",
        "filters": {
            "days": days,
            "project_key": None,
            "agent_name": None,
            "pricing_enabled": True,
            "ledger_enabled": True,
            "accumulators_enabled": True,
            "data_source": state.get("data_source", "fake_static"),
        },
        "notes": [
            "PoC 100% fake: funcionalidades identicas ao real da Fase 1 (Interaction -> Accumulator -> CostLedger).",
            "Cenario 'explosion' injeta spike para storytelling.",
            "Grafana real pode ser embutido via iframe quando estiver pronto (Settings).",
        ],
    }


def _accumulator_table(state: Dict[str, Any]) -> List[Dict[str, Any]]:
    out = []
    for key, v in state.get("accumulators", {}).items():
        parts = key.split("|")
        bucket_type, model, bucket_key = parts[0], parts[1], "|".join(parts[2:])
        out.append({
            "bucket_type": bucket_type,
            "model": model,
            "bucket_key": bucket_key,
            "pending_tokens": int(v.get("pending_tokens", 0)),
            "close_count": int(v.get("close_count", 0)),
            "updated_at": v.get("updated_at", ""),
        })
    out.sort(key=lambda x: (x["bucket_type"], x["model"], x["bucket_key"]))
    return out[:200]
