from __future__ import annotations

import os
from decimal import Decimal
from datetime import date, datetime

import psycopg2
from flask import Flask, jsonify, redirect, render_template, request, url_for, Response
from flask_cors import CORS
from flask.json.provider import DefaultJSONProvider
import bcrypt
from flask import session, redirect, url_for, render_template, request, flash
from functools import wraps
import simulator as sim
from real_provider import summarize_real, upsert_pricing, ingest_usage, get_finops_filter_options, get_cost_by_agent, get_hero_fold
from roi_provider import (
    simulate_roi, list_roi_configurations, create_roi_configuration,
    get_roi_configuration, update_roi_configuration, publish_roi_configuration,
    archive_roi_configuration, list_roi_tasks, create_roi_task, update_roi_task, archive_roi_task,
    save_task_baseline, approve_task_baseline, reject_task_baseline, archive_task_baseline,
    list_roi_evidences, create_roi_evidence,
    list_task_framework_links, create_task_framework_link, deactivate_task_framework_link,
    list_roi_mappings, create_roi_mapping, executive_dashboard, task_result,
    list_roi_methods, get_roi_method_parameters, create_task_process, list_task_processes,
    create_task_roi, get_task_roi, save_task_roi_values, create_process_automation,
    add_automation_participant, list_process_automations, calculate_task_roi,
)
from db import fetch_one, fetch_all


class GabbiJSONProvider(DefaultJSONProvider):
    def default(self, obj):
        if isinstance(obj, Decimal):
            return float(obj)
        if isinstance(obj, (datetime, date)):
            return obj.isoformat()
        return super().default(obj)

app = Flask(__name__)
app.json = GabbiJSONProvider(app)
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.secret_key = "gabbi-super-secret-key-2026"

# -------------------------------------------------------------------
# CORS
# -------------------------------------------------------------------
# Permite que o frontend React/Node em ambiente local consuma a API Python.
# Em produção, informe FINOPS_CORS_ORIGINS com a lista de origens permitidas, separadas por vírgula.
# Exemplo:
# FINOPS_CORS_ORIGINS=http://localhost:5173,http://192.168.230.107:5173,https://finops.gabbi.io
_allowed_origins = [
    origin.strip()
    for origin in os.getenv(
        "FINOPS_CORS_ORIGINS",
        ",".join([
            # Local
            "http://localhost:5173",
            "http://127.0.0.1:5173",

            # FinOps API server
            "http://192.168.230.107:5173",
            "http://192.168.230.107:8098",

            # Dev server
            "http://192.168.230.99",
            "http://192.168.230.99:5173",
            "http://192.168.230.99:8098",
            "https://192.168.230.99",
            "https://192.168.230.99:5173",
            "https://192.168.230.99:8098",

            # Prod server / banco atual
            "http://192.168.230.108",
            "http://192.168.230.108:5173",
            "http://192.168.230.108:8098",
            "https://192.168.230.108",
            "https://192.168.230.108:5173",
            "https://192.168.230.108:8098",

            # Domínios Gabbi
            "https://dev.gabbi.io",
            "https://gabbi.io",
            "https://www.gabbi.io"
        ])
    ).split(",")
    if origin.strip()
]

CORS(
    app,
    resources={
        r"/api/*": {"origins": _allowed_origins},
        r"/api/roi/*": {"origins": _allowed_origins},
        r"/api/finops/*": {"origins": _allowed_origins},
    },
    supports_credentials=True,
    allow_headers=[
        "Content-Type",
        "Authorization",
        "clientKey",
        "ClientKey",
        "X-Client-Key",
        "X-Tenant-Id",
        "X-Company-Id",
        "X-Empresa-Id",
    ],
    expose_headers=[
        "clientKey",
        "X-Client-Key",
        "X-Tenant-Id",
        "X-Company-Id",
    ],
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    max_age=86400,
)


@app.before_request
def handle_all_api_options():
    if request.method == "OPTIONS" and request.path.startswith("/api/"):
        return Response(status=204)
    return None

# -------------------------------------------------------------------
# JSON / CORS safety
# -------------------------------------------------------------------
def json_safe(obj):
    """Converte tipos vindos do PostgreSQL para JSON serializável.

    Resolve Decimal, datetime/date, tuplas/listas e dicionários aninhados.
    """
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, tuple):
        return [json_safe(item) for item in obj]
    if isinstance(obj, list):
        return [json_safe(item) for item in obj]
    if isinstance(obj, dict):
        return {key: json_safe(value) for key, value in obj.items()}
    return obj


def safe_jsonify(obj, status_code: int = 200):
    return jsonify(json_safe(obj)), status_code


def _origin_allowed(origin: str | None) -> bool:
    if not origin:
        return False
    if os.getenv("FINOPS_CORS_ALLOW_ALL", "false").lower() in ("1", "true", "yes", "sim"):
        return True
    return origin in _allowed_origins


@app.after_request
def add_cors_headers(response):
    """Garante CORS inclusive em respostas de erro 4xx/5xx.

    Flask-CORS normalmente já cobre, mas quando ocorre exceção em serialização ou erro interno,
    o browser pode mostrar como CORS. Este fallback reflete origens autorizadas.
    """
    origin = request.headers.get("Origin")
    if _origin_allowed(origin):
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Vary"] = "Origin"
        response.headers["Access-Control-Allow-Credentials"] = "true"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization, clientKey, ClientKey, X-Client-Key, X-Tenant-Id, X-Company-Id, X-Empresa-Id, X-User-Id, userId"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, PATCH, DELETE, OPTIONS"
        response.headers["Access-Control-Expose-Headers"] = "clientKey, X-Client-Key, X-Tenant-Id, X-Company-Id"
    return response


@app.errorhandler(Exception)
def handle_unexpected_error(exc):
    print(f"[API][ERROR] {type(exc).__name__}: {exc}")
    return safe_jsonify({"ok": False, "error": "internal_error", "message": str(exc)}, 500)



def _tenant_id_from_request() -> str | None:
    return (
        request.headers.get("clientKey")
        or request.headers.get("ClientKey")
        or request.headers.get("X-Client-Key")
        or request.headers.get("X-Tenant-Id")
        or request.headers.get("X-Company-Id")
        or request.headers.get("X-Empresa-Id")
        or request.args.get("clientKey")
        or request.args.get("client_key")
        or request.args.get("tenant_id")
        or request.args.get("company_id")
        or request.args.get("empresa_id")
        or None
    )

def _resolve_customer_projects_from_request() -> dict:
    """Resolve o escopo FinOps a partir do padrão Gabbi.

    Padrão do front:
      headers: { clientKey: customerId }

    Fluxo correto:
      1. Recebe clientKey contendo public."Customer".id
      2. Valida o Customer ativo
      3. Busca todos os projetos ativos do cliente em public."Project"
      4. Usa Project.id[] como filtro em finops.<tabela>.project_key

    Compatibilidade:
      - Se project_key vier explicitamente na query, filtra só esse projeto.
      - Se não vier clientKey nem project_key, mantém visão geral sem filtro.
      - Se clientKey vier inválido ou cliente não tiver projetos, retorna project_keys=[] para não vazar dados.
    """
    explicit_project_key = request.args.get("project_key")
    if explicit_project_key:
        return {
            "clientKey": _tenant_id_from_request(),
            "customer_id": None,
            "customer_key": None,
            "customer_name": None,
            "project_key": explicit_project_key,
            "project_keys": [explicit_project_key],
            "projects": [{"id": explicit_project_key, "name": None}],
            "scope_mode": "explicit_project_key",
        }

    client_key = _tenant_id_from_request()
    if not client_key:
        return {
            "clientKey": None,
            "customer_id": None,
            "customer_key": None,
            "customer_name": None,
            "project_key": None,
            "project_keys": None,
            "projects": [],
            "scope_mode": "all",
        }

    try:
        customer = fetch_one(
            """
            select id, name, key
            from public."Customer"
            where id = %(customer_id)s
              and deleted = false
            limit 1
            """,
            {"customer_id": client_key},
        )

        if not customer:
            return {
                "clientKey": client_key,
                "customer_id": None,
                "customer_key": None,
                "customer_name": None,
                "project_key": None,
                "project_keys": [],
                "projects": [],
                "scope_mode": "customer_not_found",
            }

        projects = fetch_all(
            """
            select id, name
            from public."Project"
            where "customerId" = %(customer_id)s
              and deleted = false
            order by name asc nulls last, id asc
            """,
            {"customer_id": customer["id"]},
        )

        project_ids = [str(p["id"]) for p in projects if p.get("id")]

        return {
            "clientKey": client_key,
            "customer_id": customer.get("id"),
            "customer_key": customer.get("key"),
            "customer_name": customer.get("name"),
            "project_key": None,
            "project_keys": project_ids,
            "projects": [{"id": p.get("id"), "name": p.get("name")} for p in projects],
            "scope_mode": "customer_projects",
        }

    except Exception as e:
        print(f"[FINOPS][WARN] erro ao resolver projetos do Customer pelo clientKey: {e}")
        return {
            "clientKey": client_key,
            "customer_id": None,
            "customer_key": None,
            "customer_name": None,
            "project_key": None,
            "project_keys": [],
            "projects": [],
            "scope_mode": "customer_resolution_error",
        }


def _effective_project_key() -> str | None:
    """Mantido para compatibilidade com chamadas antigas."""
    return _resolve_customer_projects_from_request().get("project_key")


def _effective_project_keys() -> list[str] | None:
    return _resolve_customer_projects_from_request().get("project_keys")


def _customer_context_from_request() -> dict:
    """Retorna metadados do Customer e projetos para auditoria/depuração no retorno da API."""
    ctx = _resolve_customer_projects_from_request()
    return {
        "clientKey": ctx.get("clientKey"),
        "customer_id": ctx.get("customer_id"),
        "customer_key": ctx.get("customer_key"),
        "customer_name": ctx.get("customer_name"),
        "scope_mode": ctx.get("scope_mode"),
        "project_count": len(ctx.get("project_keys") or []),
        "project_keys": ctx.get("project_keys"),
        "projects": ctx.get("projects") or [],
    }


OPENAPI_DESCRIPTION = """
# Gabbi FinOps — API de custos, governança e retorno de IA

O **Gabbi FinOps** expõe uma camada de API para consolidar custos de uso de IA, automações, agentes, ROI, filtros executivos e indicadores de decisão para o dashboard real em Node/React.

A API foi organizada para responder perguntas de gestão como:

- Quanto a IA custou no período?
- Quanto desse custo veio de automações?
- Quais agentes concentram maior gasto?
- Qual área de negócio está consumindo mais?
- Qual o ROI estimado e a economia acumulada?
- O que deve aparecer na dobra principal do dashboard FinOps?

## Jornada recomendada de consumo pelo frontend

1. `GET /api/finops/filters` — carrega períodos, áreas, projetos e agentes disponíveis.
2. `GET /api/finops/dataset` — carrega visão consolidada completa do painel.
3. `GET /api/finops/agents/cost` — carrega ranking de custo por agente.
4. `GET /api/finops/hero-fold` — carrega os blocos da dobra principal.
5. `POST /api/finops/usage` — registra uso real vindo do backend Java/JPA, Node ou outro consumidor.
6. `POST /api/finops/pricing` — atualiza tabela de preço por modelo.

## Observação sobre área de negócio

O header `clientKey` é o padrão recomendado para informar a empresa/cliente que está consultando o FinOps. O valor esperado é o `id` da tabela `public."Customer"`. A API valida esse `id`, busca os projetos ativos vinculados em `public."Project"` e usa a lista de `Project.id` como filtro em `finops.<tabela>.project_key`. Também são aceitos, por compatibilidade, `X-Tenant-Id`, `X-Company-Id`, `X-Empresa-Id` e query params de apoio.

O filtro `business_area` é suportado pela API. Caso o banco ainda não possua uma coluna explícita de área, a API continua funcionando e utiliza os dados disponíveis, sem obrigar criação imediata de tabela nova.
"""

TAGS_METADATA = [
    {"name": "00. Portal", "description": "Rotas visuais e documentação customizada da solução FinOps."},
    {"name": "01. Observabilidade", "description": "Health check e validação básica da aplicação."},
    {"name": "02. FinOps Dashboard", "description": "Consulta consolidada para o dashboard executivo de custos, ROI e automações."},
    {"name": "03. Filtros", "description": "Opções disponíveis para filtros de período, área de negócio, projeto e agente."},
    {"name": "04. Agentes", "description": "Ranking de custo por agente com valor absoluto e percentual de participação."},
    {"name": "05. Dobra Principal", "description": "Dados resumidos para os cards principais da visão executiva FinOps."},
    {"name": "06. Ingestão", "description": "Registro de uso, tokens, modelos, automações e precificação."},
]



def _roi_calculation_methods() -> dict:
    """Catálogo estático dos métodos de cálculo de ROI para o frontend montar formulários dinâmicos.

    Não depende de banco. O valor de `key` deve ser enviado em `calculation_method` nos endpoints
    de simulação, criação e atualização de configuração.
    """
    common_fields = [
        {
            "key": "attribution_pct",
            "label": "Fator de atribuição ao GABBI (%)",
            "type": "number",
            "unit": "%",
            "required": True,
            "default": 80,
            "min": 0,
            "max": 100,
            "help": "Percentual do benefício atribuído ao GABBI.",
        },
        {
            "key": "agent_monthly_cost_brl",
            "label": "Custo mensal do agente",
            "type": "currency",
            "currency": "BRL",
            "required": True,
            "default": 0,
            "help": "Custo mensal do agente/solução usado no cálculo do ROI.",
        },
        {
            "key": "implementation_cost_brl",
            "label": "Custo de implantação (setup)",
            "type": "currency",
            "currency": "BRL",
            "required": False,
            "default": 0,
            "help": "Usado para calcular payback. Se não informado, o payback considera o custo mensal.",
        },
        {
            "key": "requires_evidence",
            "label": "Exigir evidência",
            "type": "boolean",
            "required": False,
            "default": True,
            "help": "Indica se a premissa precisa de evidência para publicação oficial.",
        },
        {
            "key": "human_review_pct",
            "label": "Revisão humana (%)",
            "type": "number",
            "unit": "%",
            "required": False,
            "default": 0,
            "min": 0,
            "max": 100,
            "help": "Percentual do benefício reduzido por necessidade de revisão humana.",
        },
        {
            "key": "responsible_area",
            "label": "Área responsável",
            "type": "text",
            "required": False,
            "help": "Área de negócio responsável pela configuração.",
        },
        {
            "key": "notes",
            "label": "Observações",
            "type": "textarea",
            "required": False,
            "maxLength": 500,
            "help": "Premissas, fontes dos dados e contexto da configuração.",
        },
    ]

    time_fields = [
        {
            "key": "avg_manual_time_min",
            "label": "Tempo economizado por tarefa",
            "type": "number",
            "unit": "min",
            "required": True,
            "default": 0,
            "help": "Tempo manual médio economizado por execução/tarefa.",
        },
        {
            "key": "cost_per_hour_brl",
            "label": "Custo horário",
            "type": "currency",
            "currency": "BRL",
            "required": True,
            "default": 0,
            "help": "Custo hora usado para converter tempo economizado em valor financeiro.",
        },
        {
            "key": "monthly_volume",
            "label": "Volume de tarefas/mês",
            "type": "integer",
            "required": True,
            "default": 0,
            "help": "Quantidade mensal de tarefas/processos impactados.",
        },
        {
            "key": "coverage_pct",
            "label": "Automação efetiva (%)",
            "type": "number",
            "unit": "%",
            "required": True,
            "default": 100,
            "min": 0,
            "max": 100,
            "help": "Percentual do volume realmente automatizado/assistido.",
        },
    ]

    business_fields = [
        {
            "key": "value_event_name",
            "label": "Evento de valor",
            "type": "select_or_text",
            "required": True,
            "examples": ["Contratação realizada", "Chamado resolvido", "Venda recuperada", "Documento aprovado"],
            "help": "Evento de negócio que gera valor financeiro.",
        },
        {
            "key": "event_unit_value_brl",
            "aliases": ["unit_value_brl"],
            "label": "Valor unitário do evento",
            "type": "currency",
            "currency": "BRL",
            "required": True,
            "default": 0,
            "help": "Valor financeiro atribuído a cada evento de negócio.",
        },
        {
            "key": "expected_events_month",
            "label": "Eventos esperados/mês",
            "type": "integer",
            "required": True,
            "default": 0,
            "help": "Quantidade mensal esperada de eventos de valor.",
        },
        {
            "key": "baseline_monthly_brl",
            "label": "Baseline mensal atual",
            "type": "currency",
            "currency": "BRL",
            "required": False,
            "default": 0,
            "help": "Valor de referência atual do processo antes do GABBI.",
        },
    ]

    return {
        "methods": [
            {
                "key": "time_saved",
                "aliases": ["TIME_SAVED", "time", "h_h"],
                "label": "H:H / Tempo economizado",
                "description": "Calcula o valor com base no tempo economizado multiplicado pelo custo horário.",
                "formula": "((avg_manual_time_min / 60) * monthly_volume * cost_per_hour_brl * coverage_pct) * attribution_pct",
                "required_fields": ["avg_manual_time_min", "cost_per_hour_brl", "monthly_volume", "coverage_pct", "attribution_pct", "agent_monthly_cost_brl"],
                "fields": time_fields + common_fields,
                "sample_payload": {
                    "calculation_method": "time_saved",
                    "avg_manual_time_min": 25,
                    "cost_per_hour_brl": 65,
                    "monthly_volume": 320,
                    "coverage_pct": 90,
                    "attribution_pct": 80,
                    "agent_monthly_cost_brl": 4750,
                    "implementation_cost_brl": 15000,
                    "human_review_pct": 0,
                    "responsible_area": "RH",
                },
            },
            {
                "key": "business_result",
                "aliases": ["BUSINESS_RESULT", "result", "event_value"],
                "label": "Resultado de negócio",
                "description": "Calcula o valor com base em eventos de negócio gerados pela tarefa.",
                "formula": "event_unit_value_brl * expected_events_month * attribution_pct",
                "required_fields": ["value_event_name", "event_unit_value_brl", "expected_events_month", "attribution_pct", "agent_monthly_cost_brl"],
                "fields": business_fields + common_fields,
                "sample_payload": {
                    "calculation_method": "business_result",
                    "value_event_name": "Contratação realizada",
                    "event_unit_value_brl": 8500,
                    "expected_events_month": 12,
                    "attribution_pct": 80,
                    "agent_monthly_cost_brl": 4750,
                    "implementation_cost_brl": 0,
                    "human_review_pct": 0,
                    "responsible_area": "RH",
                },
            },
            {
                "key": "hybrid",
                "aliases": ["HYBRID", "hibrido"],
                "label": "Híbrido",
                "description": "Combina tempo economizado e resultado de negócio para medir valor total.",
                "formula": "valor_tempo + valor_eventos, com revisão humana, custo e atribuição aplicados no resultado final.",
                "required_fields": ["avg_manual_time_min", "cost_per_hour_brl", "monthly_volume", "coverage_pct", "value_event_name", "event_unit_value_brl", "expected_events_month", "attribution_pct", "agent_monthly_cost_brl"],
                "fields": time_fields + business_fields + common_fields,
                "sample_payload": {
                    "calculation_method": "hybrid",
                    "avg_manual_time_min": 25,
                    "cost_per_hour_brl": 65,
                    "monthly_volume": 320,
                    "coverage_pct": 90,
                    "value_event_name": "Contratação realizada",
                    "event_unit_value_brl": 8500,
                    "expected_events_month": 12,
                    "attribution_pct": 80,
                    "agent_monthly_cost_brl": 4750,
                    "implementation_cost_brl": 15000,
                    "human_review_pct": 0,
                    "responsible_area": "RH",
                },
            },
        ],
        "usage": {
            "field_to_send": "calculation_method",
            "endpoints": [
                "POST /api/roi/configurations/simulate",
                "POST /api/roi/configurations",
                "PATCH /api/roi/configurations/{id}",
            ],
            "note": "Este catálogo é estático e não exige tabela nova. Serve para o front montar o formulário correto por método.",
        },
    }

def _openapi_spec() -> dict:
    header_params = [
        {"name": "clientKey", "in": "header", "schema": {"type": "string"}, "description": "ID do cliente na tabela public.Customer. A API resolve os projetos do cliente e filtra por project_key IN (Project.id[])."},
        {"name": "X-Tenant-Id", "in": "header", "schema": {"type": "string"}, "description": "Compatibilidade legada para tenant/empresa."},
    ]
    period_params = header_params + [
        {"name": "days", "in": "query", "schema": {"type": "integer", "default": 30}, "description": "Janela de análise em dias."}
    ]
    finops_params = period_params + [
        {"name": "business_area", "in": "query", "schema": {"type": "string"}, "description": "Área de negócio. Também aceita alias area."},
        {"name": "project_key", "in": "query", "schema": {"type": "string"}, "description": "Filtro explícito por projeto. Se informado, tem prioridade sobre clientKey."},
        {"name": "agent_name", "in": "query", "schema": {"type": "string"}, "description": "Nome do agente."},
    ]

    def json_response(schema_ref: str, description: str = "Sucesso") -> dict:
        return {"200": {"description": description, "content": {"application/json": {"schema": {"$ref": schema_ref}}}}}

    return {
        "openapi": "3.0.3",
        "info": {
            "title": "Gabbi FinOps + ROI API",
            "version": "2.2.0",
            "description": OPENAPI_DESCRIPTION + "\n\n## Módulo ROI\nInclui configuração de ROI, simulação, tarefas, baseline, mapeamentos e dashboard executivo.",
            "contact": {"name": "Gabbi / Spread", "url": "https://www.spread.com.br"},
            "license": {"name": "Proprietary / Internal Use"},
        },
        "tags": TAGS_METADATA + [
            {"name": "07. ROI Configurações", "description": "CRUD, simulação, publicação e arquivamento de configurações de ROI."},
            {"name": "08. ROI Tarefas", "description": "Cadastro de tarefas, baseline e resultados por tarefa."},
            {"name": "09. ROI Mapeamentos", "description": "Associação tarefa x agente x workflow x DAG."},
            {"name": "10. ROI Dashboard", "description": "Visão executiva de ROI consolidada por cliente/projetos."},
        ],
        "servers": [
            {"url": "/", "description": "Servidor atual"},
            {"url": "http://localhost:5000", "description": "Flask local"},
            {"url": "http://192.168.230.107:8098", "description": "Servidor FinOps atual"},
        ],
        "paths": {
            "/health": {"get": {"tags": ["01. Observabilidade"], "summary": "Health Check", "description": "Verifica se a API está ativa.", "responses": {"200": {"description": "Aplicação disponível"}}}},

            "/api/finops/dataset": {"get": {"tags": ["02. FinOps Dashboard"], "summary": "Dataset consolidado do dashboard FinOps", "description": "Retorna KPIs, séries, tabelas, recomendações, showback e filtros aplicados.", "parameters": finops_params, "responses": json_response("#/components/schemas/FinOpsDataset", "Dataset consolidado")}},
            "/api/finops/filters": {"get": {"tags": ["03. Filtros"], "summary": "Opções de filtros para o frontend", "description": "Retorna períodos, áreas, projetos e agentes para montar filtros.", "parameters": period_params, "responses": json_response("#/components/schemas/FinOpsFilters", "Filtros disponíveis")}},
            "/api/finops/agents/cost": {"get": {"tags": ["04. Agentes"], "summary": "Custo por agente", "description": "Ranking de agentes por custo total e percentual de participação.", "parameters": finops_params + [{"name": "limit", "in": "query", "schema": {"type": "integer", "default": 20}}], "responses": json_response("#/components/schemas/AgentCostResponse", "Ranking de custo por agente")}},
            "/api/finops/hero-fold": {"get": {"tags": ["05. Dobra Principal"], "summary": "Dados da dobra principal do FinOps", "description": "Retorna os blocos executivos da visão FinOps.", "parameters": finops_params, "responses": json_response("#/components/schemas/HeroFoldResponse", "Dados da dobra principal")}},
            "/api/finops/usage": {"post": {"tags": ["06. Ingestão"], "summary": "Registrar uso de IA", "description": "Registra tokens, modelo, agente, projeto, tarefa e workflow para cálculo FinOps.", "requestBody": {"required": True, "content": {"application/json": {"schema": {"$ref": "#/components/schemas/UsageIngestRequest"}}}}, "responses": {"200": {"description": "Uso registrado"}, "400": {"description": "Payload inválido"}, "500": {"description": "Erro interno"}}}},
            "/api/finops/pricing": {"post": {"tags": ["06. Ingestão"], "summary": "Atualizar precificação de modelo", "description": "Atualiza o custo por 1k tokens do modelo.", "requestBody": {"required": True, "content": {"application/json": {"schema": {"$ref": "#/components/schemas/PricingRequest"}}}}, "responses": {"200": {"description": "Precificação salva"}, "400": {"description": "Payload inválido"}, "500": {"description": "Erro interno"}}}},

            "/api/roi/calculation-methods": {"get": {"tags": ["07. ROI Configurações"], "summary": "Catálogo de métodos de cálculo de ROI", "description": "Retorna os métodos disponíveis para o front montar formulários dinâmicos. Não depende de banco.", "parameters": header_params, "responses": json_response("#/components/schemas/RoiCalculationMethodsResponse", "Métodos retornados")}},
            "/api/roi/configurations/simulate": {"post": {"tags": ["07. ROI Configurações"], "summary": "Simular ROI sem salvar", "description": "Usado pela lateral de simulação instantânea da tela de configuração. Calcula benefício bruto, custo, economia líquida, ROI e payback. Este endpoint é stateless e não grava dados no banco.", "parameters": header_params, "requestBody": {"required": True, "content": {"application/json": {"schema": {"$ref": "#/components/schemas/RoiSimulationRequest"}}}}, "responses": json_response("#/components/schemas/RoiSimulationResponse", "Simulação calculada")}},
            "/api/roi/configurations": {
                "get": {"tags": ["07. ROI Configurações"], "summary": "Listar configurações de ROI", "description": "Lista configurações no escopo do cliente informado via clientKey.", "parameters": header_params + [{"name": "status", "in": "query", "schema": {"type": "string", "enum": ["DRAFT", "PUBLISHED", "ARCHIVED"]}}], "responses": json_response("#/components/schemas/RoiConfigurationList", "Configurações listadas")},
                "post": {"tags": ["07. ROI Configurações"], "summary": "Criar configuração de ROI", "description": "Cria uma configuração em rascunho e já calcula a simulação inicial.", "parameters": header_params, "requestBody": {"required": True, "content": {"application/json": {"schema": {"$ref": "#/components/schemas/RoiConfigurationRequest"}}}}, "responses": json_response("#/components/schemas/RoiMutationResponse", "Configuração criada")},
            },
            "/api/roi/configurations/{id}": {
                "get": {"tags": ["07. ROI Configurações"], "summary": "Detalhar configuração de ROI", "parameters": header_params + [{"name": "id", "in": "path", "required": True, "schema": {"type": "string"}}], "responses": json_response("#/components/schemas/RoiConfiguration", "Configuração retornada")},
                "patch": {"tags": ["07. ROI Configurações"], "summary": "Atualizar configuração em rascunho", "description": "Configurações publicadas são imutáveis; alterações devem gerar nova configuração/versão.", "parameters": header_params + [{"name": "id", "in": "path", "required": True, "schema": {"type": "string"}}], "requestBody": {"required": True, "content": {"application/json": {"schema": {"$ref": "#/components/schemas/RoiConfigurationRequest"}}}}, "responses": json_response("#/components/schemas/RoiMutationResponse", "Configuração atualizada")},
            },
            "/api/roi/configurations/{id}/simulate": {"post": {"tags": ["07. ROI Configurações"], "summary": "Simular configuração existente", "parameters": header_params + [{"name": "id", "in": "path", "required": True, "schema": {"type": "string"}}], "requestBody": {"required": False, "content": {"application/json": {"schema": {"type": "object"}}}}, "responses": json_response("#/components/schemas/RoiSimulationResponse", "Simulação calculada")}},
            "/api/roi/configurations/{id}/publish": {"post": {"tags": ["07. ROI Configurações"], "summary": "Publicar configuração de ROI", "description": "Gera versão/snapshot imutável para uso oficial.", "parameters": header_params + [{"name": "id", "in": "path", "required": True, "schema": {"type": "string"}}], "responses": json_response("#/components/schemas/RoiMutationResponse", "Configuração publicada")}},
            "/api/roi/configurations/{id}/archive": {"post": {"tags": ["07. ROI Configurações"], "summary": "Arquivar configuração de ROI", "parameters": header_params + [{"name": "id", "in": "path", "required": True, "schema": {"type": "string"}}], "responses": json_response("#/components/schemas/RoiMutationResponse", "Configuração arquivada")}},

            "/api/roi/tasks": {
                "get": {"tags": ["08. ROI Tarefas"], "summary": "Listar tarefas de ROI", "description": "Lista tarefas de negócio medidas por ROI no escopo do cliente.", "parameters": header_params, "responses": json_response("#/components/schemas/RoiTaskList", "Tarefas listadas")},
                "post": {"tags": ["08. ROI Tarefas"], "summary": "Criar tarefa de ROI", "description": "Cria tarefa de negócio com área, processo e owner.", "parameters": header_params, "requestBody": {"required": True, "content": {"application/json": {"schema": {"$ref": "#/components/schemas/RoiTaskRequest"}}}}, "responses": json_response("#/components/schemas/RoiMutationResponse", "Tarefa criada")},
            },
            "/api/roi/tasks/{id}": {"patch": {"tags": ["08. ROI Tarefas"], "summary": "Atualizar tarefa de ROI", "parameters": header_params + [{"name": "id", "in": "path", "required": True, "schema": {"type": "string"}}], "requestBody": {"required": True, "content": {"application/json": {"schema": {"$ref": "#/components/schemas/RoiTaskRequest"}}}}, "responses": json_response("#/components/schemas/RoiMutationResponse", "Tarefa atualizada")}},
            "/api/roi/tasks/{id}/archive": {"post": {"tags": ["08. ROI Tarefas"], "summary": "Arquivar tarefa de ROI", "parameters": header_params + [{"name": "id", "in": "path", "required": True, "schema": {"type": "string"}}], "responses": json_response("#/components/schemas/RoiMutationResponse", "Tarefa arquivada")}},
            "/api/roi/tasks/{id}/baseline": {"post": {"tags": ["08. ROI Tarefas"], "summary": "Salvar baseline da tarefa", "description": "Registra tempo manual, volume mensal, custo/hora, SLA, erro, confiança e status do baseline.", "parameters": header_params + [{"name": "id", "in": "path", "required": True, "schema": {"type": "string"}}], "requestBody": {"required": True, "content": {"application/json": {"schema": {"$ref": "#/components/schemas/RoiBaselineRequest"}}}}, "responses": json_response("#/components/schemas/RoiMutationResponse", "Baseline salvo")}},
            "/api/roi/tasks/{id}/approve-baseline": {"post": {"tags": ["08. ROI Tarefas"], "summary": "Aprovar baseline da tarefa", "description": "Marca o baseline mais recente como aprovado para cálculo oficial.", "parameters": header_params + [{"name": "id", "in": "path", "required": True, "schema": {"type": "string"}}], "responses": json_response("#/components/schemas/RoiMutationResponse", "Baseline aprovado")}},
            "/api/roi/tasks/{id}/reject-baseline": {"post": {"tags": ["08. ROI Tarefas"], "summary": "Rejeitar baseline da tarefa", "parameters": header_params + [{"name": "id", "in": "path", "required": True, "schema": {"type": "string"}}], "responses": json_response("#/components/schemas/RoiMutationResponse", "Baseline rejeitado")}},
            "/api/roi/tasks/{id}/archive-baseline": {"post": {"tags": ["08. ROI Tarefas"], "summary": "Arquivar baseline da tarefa", "parameters": header_params + [{"name": "id", "in": "path", "required": True, "schema": {"type": "string"}}], "responses": json_response("#/components/schemas/RoiMutationResponse", "Baseline arquivado")}},
            "/api/roi/tasks/{id}/frameworks": {"get": {"tags": ["08. ROI Tarefas"], "summary": "Listar frameworks vinculados à tarefa", "parameters": header_params + [{"name": "id", "in": "path", "required": True, "schema": {"type": "string"}}], "responses": json_response("#/components/schemas/RoiMappingList", "Vínculos listados")}, "post": {"tags": ["08. ROI Tarefas"], "summary": "Vincular tarefa a framework publicado", "parameters": header_params + [{"name": "id", "in": "path", "required": True, "schema": {"type": "string"}}], "requestBody": {"required": True, "content": {"application/json": {"schema": {"type": "object"}}}}, "responses": json_response("#/components/schemas/RoiMutationResponse", "Framework vinculado")}},
            "/api/roi/task-frameworks/{id}/deactivate": {"post": {"tags": ["08. ROI Tarefas"], "summary": "Encerrar vínculo tarefa-framework", "parameters": header_params + [{"name": "id", "in": "path", "required": True, "schema": {"type": "string"}}], "responses": json_response("#/components/schemas/RoiMutationResponse", "Vínculo encerrado")}},
            "/api/roi/evidences": {"get": {"tags": ["08. ROI Tarefas"], "summary": "Listar evidências de ROI", "parameters": header_params + [{"name": "entity_type", "in": "query", "schema": {"type": "string"}}, {"name": "entity_id", "in": "query", "schema": {"type": "string"}}], "responses": json_response("#/components/schemas/RoiMappingList", "Evidências listadas")}, "post": {"tags": ["08. ROI Tarefas"], "summary": "Cadastrar evidência de ROI", "parameters": header_params, "requestBody": {"required": True, "content": {"application/json": {"schema": {"type": "object"}}}}, "responses": json_response("#/components/schemas/RoiMutationResponse", "Evidência criada")}},
            "/api/roi/tasks/{id}/results": {"get": {"tags": ["08. ROI Tarefas"], "summary": "Resultado de ROI por tarefa", "description": "Retorna tarefa, baseline, mapeamentos e resultados históricos.", "parameters": header_params + [{"name": "id", "in": "path", "required": True, "schema": {"type": "string"}}], "responses": json_response("#/components/schemas/RoiTaskResult", "Resultado retornado")}},

            "/api/roi/mappings": {
                "get": {"tags": ["09. ROI Mapeamentos"], "summary": "Listar mapeamentos", "description": "Lista associações tarefa x agente x workflow x DAG.", "parameters": header_params, "responses": json_response("#/components/schemas/RoiMappingList", "Mapeamentos listados")},
                "post": {"tags": ["09. ROI Mapeamentos"], "summary": "Criar mapeamento", "description": "Associa tarefa a agente, workflow n8n e/ou DAG Airflow, com cobertura e revisão humana.", "parameters": header_params, "requestBody": {"required": True, "content": {"application/json": {"schema": {"$ref": "#/components/schemas/RoiMappingRequest"}}}}, "responses": json_response("#/components/schemas/RoiMutationResponse", "Mapeamento criado")},
            },
            "/api/roi/dashboard/executive": {"get": {"tags": ["10. ROI Dashboard"], "summary": "Dashboard executivo de ROI", "description": "Retorna KPIs consolidados de ROI no escopo do cliente/projetos.", "parameters": period_params, "responses": json_response("#/components/schemas/RoiExecutiveDashboard", "Dashboard retornado")}},
        },
        "components": {
            "securitySchemes": {
                "ClientKeyHeader": {"type": "apiKey", "in": "header", "name": "clientKey", "description": "ID do cliente na tabela public.Customer."},
                "TenantHeader": {"type": "apiKey", "in": "header", "name": "X-Tenant-Id", "description": "Compatibilidade legada."},
            },
            "schemas": {
                "FinOpsDataset": {"type": "object", "properties": {"kpis": {"$ref": "#/components/schemas/FinOpsKpis"}, "series": {"type": "object"}, "tables": {"type": "object"}, "showback": {"type": "object"}, "filters": {"type": "object"}}},
                "FinOpsKpis": {"type": "object", "properties": {"total_cost": {"type": "number", "example": 2009.60}, "manual_cost": {"type": "number"}, "automation_cost": {"type": "number"}, "interaction_rows": {"type": "integer"}, "estimated_savings_brl": {"type": "number"}, "roi_percent": {"type": "number"}}},
                "FinOpsFilters": {"type": "object", "properties": {"periods": {"type": "array", "items": {"type": "integer"}}, "business_areas": {"type": "array", "items": {"type": "string"}}, "project_keys": {"type": "array", "items": {"type": "string"}}, "agents": {"type": "array", "items": {"type": "string"}}}},
                "AgentCostResponse": {"type": "object", "properties": {"total_cost_brl": {"type": "number"}, "agents": {"type": "array", "items": {"$ref": "#/components/schemas/AgentCostItem"}}, "filters": {"type": "object"}}},
                "AgentCostItem": {"type": "object", "properties": {"agent_name": {"type": "string"}, "total_cost_brl": {"type": "number"}, "cost_percent": {"type": "number"}}},
                "HeroFoldResponse": {"type": "object", "properties": {"cards": {"type": "object"}, "agent_concentration": {"type": "array", "items": {"type": "object"}}, "filters": {"type": "object"}}},
                "UsageIngestRequest": {"type": "object", "required": ["interaction_id", "session_id", "project_key", "agent_name", "model", "input_tokens", "output_tokens", "total_tokens"], "properties": {"interaction_id": {"type": "string"}, "session_id": {"type": "string"}, "project_key": {"type": "string"}, "agent_name": {"type": "string"}, "model": {"type": "string"}, "input_tokens": {"type": "integer"}, "output_tokens": {"type": "integer"}, "total_tokens": {"type": "integer"}, "source_type": {"type": "string", "enum": ["MANUAL", "AUTOMATION"]}, "task_id": {"type": "string"}, "flow_id": {"type": "string"}}},
                "PricingRequest": {"type": "object", "required": ["model", "cost_per_1k_tokens_brl"], "properties": {"model": {"type": "string", "example": "gpt-4o"}, "cost_per_1k_tokens_brl": {"type": "number", "example": 0.85}, "min_tokens": {"type": "integer"}, "min_cost_brl": {"type": "number"}}},

                "RoiCalculationMethodsResponse": {"type": "object", "properties": {"methods": {"type": "array", "items": {"type": "object", "properties": {"key": {"type": "string", "example": "time_saved"}, "label": {"type": "string", "example": "H:H / Tempo economizado"}, "description": {"type": "string"}, "formula": {"type": "string"}, "required_fields": {"type": "array", "items": {"type": "string"}}, "fields": {"type": "array", "items": {"type": "object"}}, "sample_payload": {"type": "object"}}}}, "usage": {"type": "object"}}},
                "RoiSimulationRequest": {"type": "object", "properties": {"calculation_method": {"type": "string", "enum": ["business_result", "time_saved", "hybrid"], "example": "business_result"}, "value_event_name": {"type": "string", "example": "Contratação realizada"}, "event_unit_value_brl": {"type": "number", "example": 8500}, "expected_events_month": {"type": "integer", "example": 12}, "attribution_pct": {"type": "number", "example": 80}, "baseline_monthly_brl": {"type": "number", "example": 102000}, "agent_monthly_cost_brl": {"type": "number", "example": 4750}, "human_review_pct": {"type": "number", "example": 0}, "responsible_area": {"type": "string", "example": "RH"}}},
                "RoiSimulationResponse": {"type": "object", "properties": {"gross_savings_brl": {"type": "number", "example": 81600}, "ai_cost_brl": {"type": "number", "example": 4750}, "net_savings_brl": {"type": "number", "example": 76850}, "roi_pct": {"type": "number", "example": 1617.89}, "payback_months": {"type": "number", "example": 0.06}, "payback_days": {"type": "number", "example": 2}, "chart": {"type": "object"}}},
                "RoiConfigurationRequest": {"allOf": [{"$ref": "#/components/schemas/RoiSimulationRequest"}], "type": "object", "properties": {"project_id": {"type": "string", "description": "Project.id. Se omitido, usa o primeiro projeto do cliente."}, "task_id": {"type": "string"}, "agent_id": {"type": "string"}, "workflow_id": {"type": "string"}, "dag_id": {"type": "string"}, "name": {"type": "string", "example": "ROI Talent Finder"}, "description": {"type": "string"}, "require_evidence": {"type": "boolean", "example": True}, "human_review_required": {"type": "boolean", "example": False}, "assumptions_json": {"type": "object"}}},
                "RoiConfiguration": {"type": "object", "additionalProperties": True},
                "RoiConfigurationList": {"type": "object", "properties": {"items": {"type": "array", "items": {"$ref": "#/components/schemas/RoiConfiguration"}}, "total": {"type": "integer"}}},
                "RoiMutationResponse": {"type": "object", "properties": {"ok": {"type": "boolean", "example": True}, "item": {"type": "object"}, "error": {"type": "string"}, "version": {"type": "integer"}}},
                "RoiTaskRequest": {"type": "object", "required": ["name"], "properties": {"project_id": {"type": "string"}, "code": {"type": "string", "example": "TALENT-FINDER"}, "name": {"type": "string", "example": "Triagem de candidatos"}, "description": {"type": "string"}, "area_id": {"type": "string"}, "process_name": {"type": "string", "example": "Recrutamento"}, "owner_id": {"type": "string"}, "status": {"type": "string", "example": "DRAFT"}}},
                "RoiTaskList": {"type": "object", "properties": {"items": {"type": "array", "items": {"type": "object"}}, "total": {"type": "integer"}}},
                "RoiBaselineRequest": {"type": "object", "properties": {"avg_manual_time_min": {"type": "number", "example": 45}, "monthly_volume": {"type": "integer", "example": 120}, "cost_per_hour_brl": {"type": "number", "example": 85}, "manual_sla_hours": {"type": "number"}, "manual_error_rate": {"type": "number"}, "baseline_date": {"type": "string", "format": "date"}, "confidence_level": {"type": "string", "example": "MEDIUM"}}},
                "RoiTaskResult": {"type": "object", "properties": {"ok": {"type": "boolean"}, "task": {"type": "object"}, "baseline": {"type": "object"}, "mappings": {"type": "array", "items": {"type": "object"}}, "results": {"type": "array", "items": {"type": "object"}}}},
                "RoiMappingRequest": {"type": "object", "required": ["task_id"], "properties": {"task_id": {"type": "string"}, "agent_id": {"type": "string"}, "agent_name": {"type": "string"}, "workflow_id": {"type": "string"}, "dag_id": {"type": "string"}, "coverage_pct": {"type": "number", "example": 80}, "human_review_pct": {"type": "number", "example": 0}, "execution_mode": {"type": "string", "example": "AUTOMATED"}, "channel": {"type": "string", "example": "WEB"}}},
                "RoiMappingList": {"type": "object", "properties": {"items": {"type": "array", "items": {"type": "object"}}, "total": {"type": "integer"}}},
                "RoiExecutiveDashboard": {"type": "object", "properties": {"kpis": {"type": "object"}, "days": {"type": "integer"}}},
            },
        },
    }


def _custom_swagger_html() -> str:
    return """
<!DOCTYPE html>
<html lang="pt-BR">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Gabbi FinOps | API Docs</title>
  <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui.css" />
  <script src="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui-bundle.js"></script>
  <script src="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui-standalone-preset.js"></script>
  <script src="https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js"></script>
  <style>
    :root{--spread-purple:#5F259F;--spread-purple-2:#7A3CC2;--spread-orange:#FF7A00;--bg:#F6F4FB;--panel:#FFFFFF;--text:#2E2E38;--muted:#6E6390;--border:#E6E0F2;--soft:#FAF8FE;--dark:#1F1630;--shadow:0 18px 42px rgba(95,37,159,.14);--radius:22px}*{box-sizing:border-box}body{margin:0;font-family:Inter,Segoe UI,Roboto,Arial,sans-serif;background:radial-gradient(circle at top left,rgba(95,37,159,.14),transparent 28%),linear-gradient(180deg,#fff 0%,var(--bg) 42%,#F1ECFA 100%);color:var(--text)}.hero{background:linear-gradient(135deg,#241338 0%,#5F259F 58%,#FF7A00 140%);color:white;padding:34px 42px 40px;position:relative;overflow:hidden}.hero:after{content:"";position:absolute;right:-120px;top:-100px;width:420px;height:420px;border-radius:50%;background:rgba(255,122,0,.18)}.hero-inner{position:relative;z-index:1;max-width:1480px;margin:0 auto;display:grid;grid-template-columns:1fr auto;gap:24px;align-items:center}.brand{display:flex;align-items:center;gap:16px;margin-bottom:18px}.brand-mark{width:54px;height:54px;border-radius:18px;background:linear-gradient(135deg,#FFB547,#FF7A00);display:grid;place-items:center;font-weight:900;color:#241338;box-shadow:0 10px 30px rgba(255,122,0,.28)}.brand small{display:block;font-size:12px;letter-spacing:.18em;text-transform:uppercase;opacity:.78;margin-top:2px}.hero h1{margin:0;font-size:clamp(30px,4vw,52px);letter-spacing:-.04em;line-height:1.02}.hero p{max-width:900px;font-size:17px;line-height:1.72;opacity:.92;margin:18px 0 0}.hero-actions{display:flex;gap:12px;flex-wrap:wrap;justify-content:flex-end}.pill{border:1px solid rgba(255,255,255,.24);background:rgba(255,255,255,.1);color:#fff;padding:11px 15px;border-radius:999px;text-decoration:none;font-size:13px;font-weight:800;backdrop-filter:blur(8px)}.pill.primary{background:#fff;color:#5F259F}.wrap{max-width:1480px;margin:-24px auto 48px;padding:0 28px;position:relative;z-index:2}.grid{display:grid;grid-template-columns:1.05fr .95fr;gap:22px;margin-bottom:22px}.card{background:rgba(255,255,255,.92);border:1px solid var(--border);border-radius:var(--radius);box-shadow:var(--shadow);padding:24px}.card h2{margin:0 0 10px;font-size:22px;letter-spacing:-.02em}.card p,.card li{color:var(--muted);line-height:1.65}.metrics{display:grid;grid-template-columns:repeat(4,1fr);gap:14px}.metric{background:var(--soft);border:1px solid var(--border);border-radius:18px;padding:16px}.metric b{display:block;font-size:22px;color:var(--spread-purple);margin-bottom:5px}.metric span{font-size:12px;color:var(--muted);font-weight:800;text-transform:uppercase;letter-spacing:.06em}.mermaid{background:#fff;border:1px solid var(--border);border-radius:18px;padding:16px;overflow:auto}.swagger-shell{background:#fff;border:1px solid var(--border);border-radius:var(--radius);box-shadow:var(--shadow);overflow:hidden}.swagger-title{display:flex;align-items:center;justify-content:space-between;padding:20px 24px;background:linear-gradient(90deg,#FAF8FE,#FFF7F0);border-bottom:1px solid var(--border)}.swagger-title h2{margin:0;font-size:22px}.badge{background:#FFF1E6;color:#C75000;border:1px solid #FFD8BA;border-radius:999px;padding:8px 12px;font-weight:900;font-size:12px}.swagger-ui{padding:18px}.swagger-ui .topbar{display:none}.swagger-ui .info{display:none}.swagger-ui .opblock{border-radius:16px!important;overflow:hidden;border-color:#E9E0F5!important;box-shadow:0 8px 24px rgba(95,37,159,.06)!important}.swagger-ui .opblock-tag{font-size:18px!important;color:#251339!important;border-bottom:1px solid #E9E0F5!important}.swagger-ui .btn.execute{background:var(--spread-purple)!important;border-color:var(--spread-purple)!important}.swagger-ui .scheme-container{border-radius:16px;background:#FAF8FE!important;box-shadow:none!important;border:1px solid #E9E0F5!important}@media(max-width:980px){.hero-inner,.grid{grid-template-columns:1fr}.metrics{grid-template-columns:repeat(2,1fr)}.hero-actions{justify-content:flex-start}}
  </style>
</head>
<body>
  <section class="hero">
    <div class="hero-inner">
      <div>
        <div class="brand"><div class="brand-mark">G</div><div><strong>Gabbi FinOps</strong><small>Spread · API Docs</small></div></div>
        <h1>Documentação executiva e técnica da API FinOps</h1>
        <p>Custos, governança, automações, custo por agente, filtros por área de negócio e dados da dobra principal do dashboard em uma documentação no padrão visual Gabbi/Spread.</p>
      </div>
      <div class="hero-actions"><a class="pill primary" href="#swagger">Abrir endpoints</a><a class="pill" href="/openapi.json" target="_blank">OpenAPI JSON</a><a class="pill" href="/health" target="_blank">Health</a></div>
    </div>
  </section>
  <main class="wrap">
    <section class="grid">
      <div class="card"><h2>Visão da solução</h2><p>Esta API atende o frontend real em Node/React e também mantém compatibilidade com a PoC Flask. A leitura principal deve começar pelo endpoint de filtros e seguir para dataset, agentes e dobra principal.</p><div class="metrics"><div class="metric"><b>4</b><span>Endpoints principais</span></div><div class="metric"><b>ROI</b><span>Retorno estimado</span></div><div class="metric"><b>Área</b><span>Filtro executivo</span></div><div class="metric"><b>Agente</b><span>Showback</span></div></div></div>
      <div class="card"><h2>Fluxo recomendado</h2><div class="mermaid">flowchart TD
        A[Frontend Node/React] --> B[GET /api/finops/filters]
        B --> C[GET /api/finops/dataset]
        C --> D[GET /api/finops/agents/cost]
        C --> E[GET /api/finops/hero-fold]
        F[Backend Java/JPA] --> G[POST /api/finops/usage]
        H[Admin FinOps] --> I[POST /api/finops/pricing]
      </div></div>
    </section>
    <section id="swagger" class="swagger-shell"><div class="swagger-title"><h2>Swagger UI</h2><span class="badge">Try it out habilitado</span></div><div id="swagger-ui"></div></section>
  </main>
  <script>
    mermaid.initialize({startOnLoad:true,theme:'base',themeVariables:{primaryColor:'#F4ECFF',primaryTextColor:'#241338',primaryBorderColor:'#5F259F',lineColor:'#7A3CC2',secondaryColor:'#FFF1E6',tertiaryColor:'#FFFFFF'}});
    window.onload=function(){SwaggerUIBundle({url:'/openapi.json',dom_id:'#swagger-ui',deepLinking:true,displayRequestDuration:true,docExpansion:'none',filter:true,tryItOutEnabled:true,presets:[SwaggerUIBundle.presets.apis,SwaggerUIStandalonePreset],layout:'BaseLayout',syntaxHighlight:{theme:'obsidian'}})};
  </script>
</body>
</html>
"""


@app.route("/openapi.json", methods=["GET"])
def openapi_json():
    return safe_jsonify(_openapi_spec())


@app.route("/docs", methods=["GET"])
@app.route("/apidocs", methods=["GET"])
@app.route("/apidocs/", methods=["GET"])
@app.route("/swagger", methods=["GET"])
def swagger_docs():
    return Response(_custom_swagger_html(), mimetype="text/html")

def get_connection():
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "192.168.230.108"),
        port=os.getenv("DB_PORT", "5432"),
        database=os.getenv("DB_NAME", "gabbi-io"),
        user=os.getenv("DB_USER", "gabbi_io"),
        password=os.getenv("DB_PASSWORD"),
    )

def _state():
    return sim.load_state()

def get_user_by_email(email):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        'select id, email, password, name, deleted from public."User" where lower(email) = lower(%s)',
        (email,)
    )
    user = cur.fetchone()
    cur.close()
    conn.close()
    return user

def _save(state):
    sim.save_state(state)

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = (request.form.get("email") or "").strip()
        password = request.form.get("password") or ""

        user = get_user_by_email(email)

        if not user:
            flash("Usuário não encontrado", "danger")
            return render_template("login.html")

        user_id, user_email, password_hash, name, active = user

        if not active:
            flash("Usuário inativo", "danger")
            return render_template("login.html")

        if not bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8")):
            flash("Senha inválida", "danger")
            return render_template("login.html")

        # salva sessão
        session["user_id"] = str(user_id)
        session["user_name"] = name
        session["user_email"] = user_email
        session["authenticated"] = True

        return redirect(url_for("dashboard"))

    return render_template("login.html")

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get("authenticated"):
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated_function

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.get("/health")
def health():
    return safe_jsonify({"status": "ok"})


@app.route("/")
def home():
    return redirect(url_for("dashboard"))


def _get_dataset(state: dict):
    data_source = state.get("data_source", "real")
    days = int(state.get("days", 30))

    if data_source == "real":
        project_key = state.get("project_key")
        agent_name = state.get("agent_name")
        business_area = state.get("business_area")
        return summarize_real(days=days, project_key=project_key, agent_name=agent_name, business_area=business_area)

    return sim.summarize(state)


@app.route("/finops")
def dashboard():
    state = _state()

    data_source = request.args.get("data_source")
    scenario = request.args.get("scenario")
    days = request.args.get("days")

    if data_source or scenario or days:
        sim.apply_controls(
            state,
            data_source=data_source or state.get("data_source", "fake_static"),
            scenario=scenario or state.get("scenario", "growth"),
            days=int(days or state.get("days", 30)),
        )
        _save(state)

    dataset = _get_dataset(state)
    return render_template("finops_dashboard.html", dataset=dataset)


@app.route("/ledger")
def ledger():
    state = _state()
    dataset = _get_dataset(state)
    return render_template("finops_ledger.html", dataset=dataset)


@app.route("/interactions")
def interactions():
    state = _state()
    dataset = _get_dataset(state)
    return render_template("finops_interactions.html", dataset=dataset)


@app.route("/accumulators")
def accumulators():
    state = _state()
    dataset = _get_dataset(state)
    return render_template("finops_accumulators.html", dataset=dataset)


@app.route("/pricing", methods=["GET", "POST"])
def pricing():
    state = _state()

    if request.method == "POST":
        model = (request.form.get("model") or "").strip()

        cost_per_1k = request.form.get("cost_per_1k_tokens_brl")  # opcional
        min_tokens = request.form.get("min_tokens")
        min_cost = request.form.get("min_cost")

        # valida mínimo
        if not model:
            return redirect(url_for("pricing"))

        # Se você não mandar cost_per_1k no form, usa um default (ou busca o último vigente)
        if not cost_per_1k:
            # default simples (você pode mudar)
            cost_per_1k = "0.85"

        try:
            upsert_pricing(
                model=model,
                cost_per_1k_tokens_brl=float(cost_per_1k),
                min_tokens=int(min_tokens) if min_tokens else None,
                min_cost_brl=float(min_cost) if min_cost else None,
            )
        except Exception as e:
            print(f"[PRICING][WARN] falha ao salvar pricing real: {e}")

        return redirect(url_for("pricing"))

    dataset = _get_dataset(state)
    return render_template("finops_pricing.html", dataset=dataset)


@app.route("/settings", methods=["GET", "POST"])
def settings():
    state = _state()
    if request.method == "POST":
        url = (request.form.get("grafana_embed_url") or "").strip()
        sim.set_grafana_url(state, url)
        _save(state)
        return redirect(url_for("settings"))
    dataset = _get_dataset(state)
    return render_template("finops_settings.html", dataset=dataset)


# ---------------- API (front / demo) ----------------

@app.route("/api/state")
def api_state():
    state = _state()
    return safe_jsonify(_get_dataset(state))


@app.route("/api/simulate/reset", methods=["POST"])
def api_reset():
    state = _state()
    sim.reset_live(state)
    _save(state)
    return safe_jsonify({"ok": True, "message": "Estado resetado (live).", "state": _get_dataset(state)})


@app.route("/api/simulate/step", methods=["POST"])
def api_step():
    state = _state()
    payload = request.get_json(silent=True) or {}
    steps = int(payload.get("steps") or 10)
    spike = bool(payload.get("spike") or False)

    if state.get("data_source") == "real":
        return safe_jsonify({"ok": False, "message": "Modo REAL: simulação desabilitada.", "state": _get_dataset(state)}, 400)

    out = sim.step_live(state, steps=steps, spike=spike)
    _save(state)
    return safe_jsonify({"ok": True, "result": out, "state": _get_dataset(state)})


@app.route("/api/simulate/spike", methods=["POST"])
def api_spike():
    state = _state()

    if state.get("data_source") == "real":
        return safe_jsonify({"ok": False, "message": "Modo REAL: spike desabilitado.", "state": _get_dataset(state)}, 400)

    out = sim.step_live(state, steps=30, spike=True)
    _save(state)
    return safe_jsonify({"ok": True, "result": out, "state": _get_dataset(state)})


# ---------------- API (para consumo do backend Java/JPA) ----------------

@app.route("/api/finops/usage", methods=["POST"])
def api_finops_usage():
    payload = request.get_json(silent=True) or {}
    try:
        out = ingest_usage(payload)
        return safe_jsonify(out, 200 if out.get("ok") else 400)
    except Exception as e:
        return safe_jsonify({"ok": False, "error": "exception", "message": str(e)}, 500)


@app.route("/api/finops/pricing", methods=["POST"])
def api_finops_pricing():
    payload = request.get_json(silent=True) or {}
    model = (payload.get("model") or "").strip()
    cost = payload.get("cost_per_1k_tokens_brl")
    if not model or cost is None:
        return safe_jsonify({"ok": False, "error": "missing_field:model|cost_per_1k_tokens_brl"}, 400)

    try:
        upsert_pricing(
            model=model,
            cost_per_1k_tokens_brl=float(cost),
            min_tokens=int(payload["min_tokens"]) if payload.get("min_tokens") is not None else None,
            min_cost_brl=float(payload["min_cost_brl"]) if payload.get("min_cost_brl") is not None else None,
        )
        return safe_jsonify({"ok": True})
    except Exception as e:
        return safe_jsonify({"ok": False, "error": "exception", "message": str(e)}, 500)


@app.route("/api/finops/dataset", methods=["GET"])
def api_finops_dataset():
    days = int(request.args.get("days", "30"))
    scope = _resolve_customer_projects_from_request()
    project_key = scope.get("project_key")
    project_keys = scope.get("project_keys")
    tenant_id = _tenant_id_from_request()
    agent_name = request.args.get("agent_name") or None
    business_area = request.args.get("business_area") or request.args.get("area") or None
    data = summarize_real(days=days, project_key=project_key, project_keys=project_keys, agent_name=agent_name, business_area=business_area)
    data.setdefault("filters", {})["tenant_id"] = tenant_id
    data.setdefault("filters", {})["company_id"] = tenant_id
    data.setdefault("filters", {})["customer"] = _customer_context_from_request()
    return safe_jsonify(data)


@app.route("/api/finops/filters", methods=["GET"])
def api_finops_filters():
    days = int(request.args.get("days", "30"))
    tenant_id = _tenant_id_from_request()
    scope = _resolve_customer_projects_from_request()
    project_keys = scope.get("project_keys")
    data = get_finops_filter_options(days=days, project_keys=project_keys)
    data["tenant_id"] = tenant_id
    data["company_id"] = tenant_id
    data["customer"] = _customer_context_from_request()
    return safe_jsonify(data)


@app.route("/api/finops/agents/cost", methods=["GET"])
def api_finops_agents_cost():
    days = int(request.args.get("days", "30"))
    limit = int(request.args.get("limit", "20"))
    scope = _resolve_customer_projects_from_request()
    project_key = scope.get("project_key")
    project_keys = scope.get("project_keys")
    tenant_id = _tenant_id_from_request()
    agent_name = request.args.get("agent_name") or None
    business_area = request.args.get("business_area") or request.args.get("area") or None
    data = get_cost_by_agent(days=days, project_key=project_key, project_keys=project_keys, agent_name=agent_name, business_area=business_area, limit=limit)
    data.setdefault("filters", {})["tenant_id"] = tenant_id
    data.setdefault("filters", {})["company_id"] = tenant_id
    data.setdefault("filters", {})["customer"] = _customer_context_from_request()
    return safe_jsonify(data)


@app.route("/api/finops/hero-fold", methods=["GET"])
def api_finops_hero_fold():
    days = int(request.args.get("days", "30"))
    scope = _resolve_customer_projects_from_request()
    project_key = scope.get("project_key")
    project_keys = scope.get("project_keys")
    tenant_id = _tenant_id_from_request()
    agent_name = request.args.get("agent_name") or None
    business_area = request.args.get("business_area") or request.args.get("area") or None
    data = get_hero_fold(days=days, project_key=project_key, project_keys=project_keys, agent_name=agent_name, business_area=business_area)
    data.setdefault("filters", {})["tenant_id"] = tenant_id
    data.setdefault("filters", {})["company_id"] = tenant_id
    data.setdefault("filters", {})["customer"] = _customer_context_from_request()
    return safe_jsonify(data)


# ---------------- API ROI MVP ----------------

def _current_user_id() -> str | None:
    return session.get("user_id") or request.headers.get("X-User-Id") or request.headers.get("userId")


def _current_user_is_admin() -> bool:
    raw = (request.headers.get("X-User-Role") or request.headers.get("X-Profile") or "").upper()
    if raw in {"ADMIN", "ADMINISTRATOR", "SUPERADMIN"}:
        return True
    return bool(session.get("is_admin") or False)


@app.route("/api/roi/calculation-methods", methods=["GET"])
def api_roi_calculation_methods():
    return safe_jsonify(_roi_calculation_methods())


@app.route("/api/roi/configurations", methods=["GET"])
def api_roi_configurations_list():
    status = request.args.get("status") or None
    return safe_jsonify(list_roi_configurations(project_keys=_effective_project_keys(), status=status))


@app.route("/api/roi/configurations", methods=["POST"])
def api_roi_configurations_create():
    payload = request.get_json(silent=True) or {}
    out = create_roi_configuration(payload, _customer_context_from_request(), user_id=_current_user_id())
    return safe_jsonify(out, 200 if out.get("ok") else 400)


@app.route("/api/roi/configurations/<config_id>", methods=["GET"])
def api_roi_configuration_get(config_id: str):
    item = get_roi_configuration(config_id, project_keys=_effective_project_keys())
    if not item:
        return safe_jsonify({"ok": False, "error": "not_found"}, 404)
    return safe_jsonify({"ok": True, "item": item})


@app.route("/api/roi/configurations/<config_id>", methods=["PATCH"])
def api_roi_configuration_patch(config_id: str):
    payload = request.get_json(silent=True) or {}
    out = update_roi_configuration(config_id, payload, project_keys=_effective_project_keys(), user_id=_current_user_id())
    return safe_jsonify(out, 200 if out.get("ok") else 400)


@app.route("/api/roi/configurations/simulate", methods=["POST"])
def api_roi_configuration_simulate_new():
    payload = request.get_json(silent=True) or {}
    return safe_jsonify({"ok": True, "persisted": False, "simulation": simulate_roi(payload)})


@app.route("/api/roi/configurations/<config_id>/simulate", methods=["POST"])
def api_roi_configuration_simulate_existing(config_id: str):
    current = get_roi_configuration(config_id, project_keys=_effective_project_keys())
    if not current:
        return safe_jsonify({"ok": False, "error": "not_found"}, 404)
    payload = {**current, **(request.get_json(silent=True) or {})}
    return safe_jsonify({"ok": True, "persisted": False, "simulation": simulate_roi(payload)})


@app.route("/api/roi/configurations/<config_id>/publish", methods=["POST"])
def api_roi_configuration_publish(config_id: str):
    out = publish_roi_configuration(config_id, project_keys=_effective_project_keys(), user_id=_current_user_id())
    return safe_jsonify(out, 200 if out.get("ok") else 400)


@app.route("/api/roi/configurations/<config_id>/archive", methods=["POST"])
def api_roi_configuration_archive(config_id: str):
    out = archive_roi_configuration(config_id, project_keys=_effective_project_keys(), user_id=_current_user_id())
    return safe_jsonify(out, 200 if out.get("ok") else 400)


@app.route("/api/roi/tasks", methods=["GET"])
def api_roi_tasks_list():
    return safe_jsonify(list_roi_tasks(
        project_keys=_effective_project_keys(),
        area_id=request.args.get("area_id") or None,
        owner_id=request.args.get("owner_id") or None,
        status=request.args.get("status") or None,
        framework_id=request.args.get("framework_id") or None,
    ))


@app.route("/api/roi/tasks", methods=["POST"])
def api_roi_tasks_create():
    payload = request.get_json(silent=True) or {}
    out = create_roi_task(payload, _customer_context_from_request(), user_id=_current_user_id())
    return safe_jsonify(out, 200 if out.get("ok") else 400)


@app.route("/api/roi/tasks/<task_id>", methods=["PATCH"])
def api_roi_tasks_update(task_id: str):
    payload = request.get_json(silent=True) or {}
    out = update_roi_task(task_id, payload, project_keys=_effective_project_keys(), user_id=_current_user_id())
    return safe_jsonify(out, 200 if out.get("ok") else 400)


@app.route("/api/roi/tasks/<task_id>/archive", methods=["POST"])
def api_roi_tasks_archive(task_id: str):
    out = archive_roi_task(task_id, project_keys=_effective_project_keys(), user_id=_current_user_id())
    return safe_jsonify(out, 200 if out.get("ok") else 400)


@app.route("/api/roi/tasks/<task_id>/baseline", methods=["POST"])
def api_roi_task_baseline(task_id: str):
    payload = request.get_json(silent=True) or {}
    out = save_task_baseline(task_id, payload, project_keys=_effective_project_keys(), user_id=_current_user_id())
    return safe_jsonify(out, 200 if out.get("ok") else 400)


@app.route("/api/roi/tasks/<task_id>/approve-baseline", methods=["POST"])
def api_roi_task_approve_baseline(task_id: str):
    out = approve_task_baseline(task_id, project_keys=_effective_project_keys(), user_id=_current_user_id())
    return safe_jsonify(out, 200 if out.get("ok") else 400)


@app.route("/api/roi/tasks/<task_id>/reject-baseline", methods=["POST"])
def api_roi_task_reject_baseline(task_id: str):
    payload = request.get_json(silent=True) or {}
    out = reject_task_baseline(task_id, payload, project_keys=_effective_project_keys(), user_id=_current_user_id())
    return safe_jsonify(out, 200 if out.get("ok") else 400)


@app.route("/api/roi/tasks/<task_id>/archive-baseline", methods=["POST"])
def api_roi_task_archive_baseline(task_id: str):
    out = archive_task_baseline(task_id, project_keys=_effective_project_keys(), user_id=_current_user_id())
    return safe_jsonify(out, 200 if out.get("ok") else 400)


@app.route("/api/roi/tasks/<task_id>/frameworks", methods=["GET"])
def api_roi_task_frameworks_list(task_id: str):
    return safe_jsonify(list_task_framework_links(task_id, project_keys=_effective_project_keys()))


@app.route("/api/roi/tasks/<task_id>/frameworks", methods=["POST"])
def api_roi_task_frameworks_create(task_id: str):
    payload = request.get_json(silent=True) or {}
    out = create_task_framework_link(task_id, payload, project_keys=_effective_project_keys(), user_id=_current_user_id())
    return safe_jsonify(out, 200 if out.get("ok") else 400)


@app.route("/api/roi/task-frameworks/<link_id>/deactivate", methods=["POST"])
def api_roi_task_frameworks_deactivate(link_id: str):
    payload = request.get_json(silent=True) or {}
    out = deactivate_task_framework_link(link_id, payload, project_keys=_effective_project_keys(), user_id=_current_user_id())
    return safe_jsonify(out, 200 if out.get("ok") else 400)


@app.route("/api/roi/evidences", methods=["GET"])
def api_roi_evidences_list():
    return safe_jsonify(list_roi_evidences(
        project_keys=_effective_project_keys(),
        entity_type=request.args.get("entity_type") or None,
        entity_id=request.args.get("entity_id") or None,
    ))


@app.route("/api/roi/evidences", methods=["POST"])
def api_roi_evidences_create():
    payload = request.get_json(silent=True) or {}
    out = create_roi_evidence(payload, _customer_context_from_request(), user_id=_current_user_id())
    return safe_jsonify(out, 200 if out.get("ok") else 400)


@app.route("/api/roi/tasks/<task_id>/results", methods=["GET"])
def api_roi_task_results(task_id: str):
    out = task_result(task_id, project_keys=_effective_project_keys())
    return safe_jsonify(out, 200 if out.get("ok") else 404)


# ---------------- ROI TO-BE v3 (additive / backward compatible) ----------------

@app.route("/api/roi/methods", methods=["GET"])
def api_roi_methods_list():
    published_only = request.args.get("published_only", "true").lower() not in ("0", "false", "no")
    return safe_jsonify(list_roi_methods(project_keys=_effective_project_keys(), published_only=published_only))


@app.route("/api/roi/method-versions/<version_id>/parameters", methods=["GET"])
def api_roi_method_parameters(version_id: str):
    editable_by = request.args.get("editable_by") or None
    return safe_jsonify(get_roi_method_parameters(version_id, editable_by=editable_by))


@app.route("/api/roi/tasks/<task_id>/processes", methods=["GET"])
def api_roi_task_processes_list(task_id: str):
    return safe_jsonify(list_task_processes(task_id, project_keys=_effective_project_keys()))


@app.route("/api/roi/tasks/<task_id>/processes", methods=["POST"])
def api_roi_task_processes_create(task_id: str):
    out = create_task_process(task_id, request.get_json(silent=True) or {}, _effective_project_keys(), _current_user_id())
    return safe_jsonify(out, 200 if out.get("ok") else 400)


@app.route("/api/roi/tasks/<task_id>/roi", methods=["GET"])
def api_roi_task_roi_get(task_id: str):
    out = get_task_roi(task_id, project_keys=_effective_project_keys())
    return safe_jsonify(out, 200 if out.get("ok") else 404)


@app.route("/api/roi/tasks/<task_id>/roi", methods=["POST"])
def api_roi_task_roi_create(task_id: str):
    out = create_task_roi(task_id, request.get_json(silent=True) or {}, _effective_project_keys(), _current_user_id())
    return safe_jsonify(out, 200 if out.get("ok") else 400)


@app.route("/api/roi/task-roi-values", methods=["POST"])
def api_roi_task_values_save():
    out = save_task_roi_values(request.get_json(silent=True) or {}, _current_user_id(), _current_user_is_admin())
    return safe_jsonify(out, 200 if out.get("ok") else 403 if out.get("error", "").startswith("admin_required") else 400)


@app.route("/api/roi/admin/task-process-automations", methods=["POST"])
def api_roi_process_automation_create():
    out = create_process_automation(request.get_json(silent=True) or {}, _current_user_id(), _current_user_is_admin())
    return safe_jsonify(out, 200 if out.get("ok") else 403 if out.get("error") == "admin_required" else 400)


@app.route("/api/roi/admin/task-process-automations/<automation_id>/participants", methods=["POST"])
def api_roi_process_automation_participant_create(automation_id: str):
    out = add_automation_participant(automation_id, request.get_json(silent=True) or {}, _current_user_id(), _current_user_is_admin())
    return safe_jsonify(out, 200 if out.get("ok") else 403 if out.get("error") == "admin_required" else 400)


@app.route("/api/roi/tasks/<task_id>/automations", methods=["GET"])
def api_roi_task_automations_list(task_id: str):
    out = list_process_automations(task_id, project_keys=_effective_project_keys())
    return safe_jsonify(out, 200 if out.get("ok") else 404)


@app.route("/api/roi/task-roi/<task_roi_id>/calculate", methods=["POST"])
def api_roi_task_calculate(task_roi_id: str):
    out = calculate_task_roi(task_roi_id, request.get_json(silent=True) or {}, _current_user_id())
    return safe_jsonify(out, 200 if out.get("ok") else 400)


@app.route("/api/roi/mappings", methods=["GET"])
def api_roi_mappings_list():
    return safe_jsonify(list_roi_mappings(project_keys=_effective_project_keys()))


@app.route("/api/roi/mappings", methods=["POST"])
def api_roi_mappings_create():
    payload = request.get_json(silent=True) or {}
    out = create_roi_mapping(payload, project_keys=_effective_project_keys(), user_id=_current_user_id())
    return safe_jsonify(out, 200 if out.get("ok") else 400)


@app.route("/api/roi/dashboard/executive", methods=["GET"])
def api_roi_dashboard_executive():
    days = int(request.args.get("days", "30"))
    data = executive_dashboard(project_keys=_effective_project_keys(), days=days)
    data["customer"] = _customer_context_from_request()
    return safe_jsonify(data)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=True)
