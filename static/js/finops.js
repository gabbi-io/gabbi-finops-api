(function () {
  const s = window.__FINOPS_STATE__;
  if (!s) return;

  const statusEl = document.getElementById("status");
  function setStatus(msg) {
    if (!statusEl) return;
    statusEl.textContent = msg || "";
  }

  // ---------- Helpers ----------
  function byId(id) { return document.getElementById(id); }

  function fmtBRL(v) {
    const n = Number(v || 0);
    try {
      return n.toLocaleString("pt-BR", { style: "currency", currency: "BRL" });
    } catch {
      return `R$ ${n.toFixed(2)}`;
    }
  }

  function safeNum(v, def = 0) {
    const n = Number(v);
    return Number.isFinite(n) ? n : def;
  }

  function groupSum(rows, keyFn, valFn) {
    const m = new Map();
    (rows || []).forEach(r => {
      const k = keyFn(r);
      const v = safeNum(valFn(r), 0);
      m.set(k, (m.get(k) || 0) + v);
    });
    return Array.from(m.entries()).map(([k, v]) => ({ key: k, value: v }));
  }

  function sortByValueDesc(arr) {
    return (arr || []).slice().sort((a, b) => (b.value || 0) - (a.value || 0));
  }

  /**
   * Normaliza séries para o formato esperado pelos charts:
   * - cost_by_day: [{ day, cost }]
   * - cost_by_source: [{ source, cost }]
   * - cost_by_model: [{ model, cost }]
   *
   * Aceita:
   * A) formato antigo (já pronto)
   * B) formato real: lista flat [{date,source,model,amount}]
   * C) formato real provider: series.cost_by_day [{time,value}]
   */
  function normalizeSeries(state) {
    const out = {
      cost_by_day: [],
      cost_by_source: [],
      cost_by_model: []
    };

    // 1) Caso antigo (fake)
    if (Array.isArray(state?.series?.cost_by_day) && state.series.cost_by_day.length && state.series.cost_by_day[0].day !== undefined) {
      out.cost_by_day = state.series.cost_by_day.map(x => ({ day: x.day, cost: safeNum(x.cost) }));
    }
    if (Array.isArray(state?.series?.cost_by_source) && state.series.cost_by_source.length && state.series.cost_by_source[0].source !== undefined) {
      out.cost_by_source = state.series.cost_by_source.map(x => ({ source: x.source, cost: safeNum(x.cost) }));
    }
    if (Array.isArray(state?.series?.cost_by_model) && state.series.cost_by_model.length && state.series.cost_by_model[0].model !== undefined) {
      out.cost_by_model = state.series.cost_by_model.map(x => ({ model: x.model, cost: safeNum(x.cost) }));
    }

    // Se já preencheu tudo pelo formato antigo, retorna
    if (out.cost_by_day.length || out.cost_by_source.length || out.cost_by_model.length) {
      return out;
    }

    // 2) Caso real "flat": state.rows | state.ledger | state.data etc.
    // Procura um array com objetos {date, source, model, amount}
    const candidates = [
      state?.rows,
      state?.data,
      state?.dataset,
      state?.series?.rows,
      state?.tables?.ledger,
      state?.ledger_rows,
      state?.series?.flat_cost
    ];
    const flat = candidates.find(a => Array.isArray(a) && a.length && (a[0].date !== undefined && a[0].amount !== undefined));

    if (flat) {
      // cost_by_day
      const byDay = sortByValueDesc(groupSum(flat, r => r.date, r => r.amount))
        .map(x => ({ day: x.key, cost: x.value }))
        .sort((a, b) => String(a.day).localeCompare(String(b.day)));

      // cost_by_source
      const bySource = sortByValueDesc(groupSum(flat, r => r.source, r => r.amount))
        .map(x => ({ source: x.key, cost: x.value }));

      // cost_by_model
      const byModel = sortByValueDesc(groupSum(flat, r => r.model, r => r.amount))
        .map(x => ({ model: x.key, cost: x.value }));

      out.cost_by_day = byDay;
      out.cost_by_source = bySource;
      out.cost_by_model = byModel;
      return out;
    }

    // 3) Caso real provider: series.cost_by_day com {time,value}
    // (ou series.cost_by_day vindo de view do banco)
    const costByDay = state?.series?.cost_by_day || state?.series?.cost_by_day_brl || state?.series?.costByDay;
    if (Array.isArray(costByDay) && costByDay.length && (costByDay[0].time !== undefined || costByDay[0].date !== undefined)) {
      out.cost_by_day = costByDay.map(x => ({
        day: (x.day || x.date || (x.time ? String(x.time).slice(0, 10) : "")),
        cost: safeNum(x.cost ?? x.amount ?? x.value)
      }));
    }

    const costBySource = state?.series?.cost_by_source || state?.series?.costBySource;
    if (Array.isArray(costBySource) && costBySource.length) {
      // tenta suportar {source,cost} ou {metric,value}
      out.cost_by_source = costBySource.map(x => ({
        source: x.source ?? x.metric ?? x.key ?? "N/A",
        cost: safeNum(x.cost ?? x.amount ?? x.value)
      }));
    }

    const costByModel = state?.series?.cost_by_model || state?.series?.costByModel;
    if (Array.isArray(costByModel) && costByModel.length) {
      out.cost_by_model = costByModel.map(x => ({
        model: x.model ?? x.metric ?? x.key ?? "N/A",
        cost: safeNum(x.cost ?? x.amount ?? x.value)
      }));
    }

    return out;
  }

  function normalizeKPIs(state) {
    const k = state?.kpis || {};

    const total =
      k.total_cost ??
      k.total_cost_brl ??
      k.totalCost ??
      k.totalCostBrl ??
      0;

    const manual =
      k.manual_cost ??
      k.manual_cost_brl ??
      0;

    const automation =
      k.automation_cost ??
      k.automation_cost_brl ??
      0;

    const interactionRows =
      k.interaction_rows ??
      k.interactions ??
      0;

    const ledgerRows =
      k.ledger_rows ??
      k.ledgerRows ??
      0;

    return {
      total,
      manual,
      automation,
      interactionRows,
      ledgerRows,
      estimatedSavings: k.estimated_savings_brl ?? 0,
      roiPercent: k.roi_percent ?? 0,
      netValue: k.net_value_brl ?? 0,
      budgetUsed: k.budget_used_brl ?? 0,
      budgetMonthly: k.budget_monthly_brl ?? 0,
      budgetUsedPercent: k.budget_used_percent ?? 0,
      budgetForecast: k.budget_forecast_brl ?? 0,
      budgetThreshold: k.budget_alert_threshold_percent ?? 0
    };
  }

  // ---------- Charts ----------
  let chartDay, chartSource, chartModel;

  function makeChart(ctx, type, labels, data, label) {
    return new Chart(ctx, {
      type,
      data: { labels, datasets: [{ label, data }] },
      options: { responsive: true, maintainAspectRatio: false }
    });
  }

  function buildCharts(state) {
    const series = normalizeSeries(state);

    const dayLabels = (series.cost_by_day || []).map(x => x.day);
    const dayValues = (series.cost_by_day || []).map(x => x.cost);

    const srcLabels = (series.cost_by_source || []).map(x => x.source);
    const srcValues = (series.cost_by_source || []).map(x => x.cost);

    const mdlLabels = (series.cost_by_model || []).map(x => x.model);
    const mdlValues = (series.cost_by_model || []).map(x => x.cost);

    const c1 = byId("chartCostByDay");
    const c2 = byId("chartBySource");
    const c3 = byId("chartByModel");

    if (c1) {
      c1.parentElement.style.height = "280px";
      chartDay = makeChart(c1, "line", dayLabels, dayValues, "Custo diário (R$)");
    }
    if (c2) {
      c2.parentElement.style.height = "280px";
      chartSource = makeChart(c2, "doughnut", srcLabels, srcValues, "Por origem");
    }
    if (c3) {
      c3.parentElement.style.height = "280px";
      chartModel = makeChart(c3, "bar", mdlLabels, mdlValues, "Por modelo");
    }
  }

  function updateCharts(state) {
    const series = normalizeSeries(state);

    if (chartDay) {
      chartDay.data.labels = (series.cost_by_day || []).map(x => x.day);
      chartDay.data.datasets[0].data = (series.cost_by_day || []).map(x => x.cost);
      chartDay.update();
    }
    if (chartSource) {
      chartSource.data.labels = (series.cost_by_source || []).map(x => x.source);
      chartSource.data.datasets[0].data = (series.cost_by_source || []).map(x => x.cost);
      chartSource.update();
    }
    if (chartModel) {
      chartModel.data.labels = (series.cost_by_model || []).map(x => x.model);
      chartModel.data.datasets[0].data = (series.cost_by_model || []).map(x => x.cost);
      chartModel.update();
    }
  }

  // ---------- UI Updates ----------
  function updateKPIs(state) {
    const total = byId("kpiTotal");
    const manual = byId("kpiManual");
    const auto = byId("kpiAutomation");
    const ints = byId("kpiInteractions");
    const ledgerRows = byId("kpiLedgerRows");

    const savings = byId("kpiEstimatedSavings");
    const roi = byId("kpiRoi");
    const netValue = byId("kpiNetValue");
    const budgetUsed = byId("kpiBudgetUsed");
    const budgetMonthly = byId("kpiBudgetMonthly");
    const budgetPercent = byId("kpiBudgetPercent");
    const budgetForecast = byId("kpiBudgetForecast");

    const k = normalizeKPIs(state);

    if (total) total.textContent = fmtBRL(k.total);
    if (manual) manual.textContent = fmtBRL(k.manual);
    if (auto) auto.textContent = fmtBRL(k.automation);
    if (ints) ints.textContent = `${safeNum(k.interactionRows, 0)}`;
    if (ledgerRows) ledgerRows.textContent = `${safeNum(k.ledgerRows, 0)}`;

    if (savings) savings.textContent = fmtBRL(k.estimatedSavings);
    if (roi) roi.textContent = `${safeNum(k.roiPercent, 0).toFixed(2)}%`;
    if (netValue) netValue.textContent = fmtBRL(k.netValue);
    if (budgetUsed) budgetUsed.textContent = fmtBRL(k.budgetUsed);
    if (budgetMonthly) budgetMonthly.textContent = fmtBRL(k.budgetMonthly);
    if (budgetPercent) budgetPercent.textContent = `${safeNum(k.budgetUsedPercent, 0).toFixed(2)}%`;
    if (budgetForecast) budgetForecast.textContent = fmtBRL(k.budgetForecast);

    updateBudgetPanel(state, k);
    renderOptimization(state);
    renderShowback(state);
  }


  function updateBudgetPanel(state, kpis) {
    const progress = byId("budgetProgressBar");
    const usedText = byId("budgetUsedText");
    const monthlyText = byId("budgetMonthlyText");
    const thresholdText = byId("budgetThresholdText");
    const forecastText = byId("budgetForecastText");
    const badge = byId("budgetAlertBadge");
    const box = byId("budgetAlertBox");

    if (usedText) usedText.textContent = fmtBRL(kpis.budgetUsed);
    if (monthlyText) monthlyText.textContent = fmtBRL(kpis.budgetMonthly);
    if (thresholdText) thresholdText.textContent = `Limiar de alerta: ${safeNum(kpis.budgetThreshold, 0).toFixed(0)}%`;
    if (forecastText) forecastText.textContent = `Forecast: ${fmtBRL(kpis.budgetForecast)}`;

    const pct = Math.max(0, Math.min(100, safeNum(kpis.budgetUsedPercent, 0)));
    if (progress) {
      progress.style.width = `${pct}%`;
      progress.style.background =
        pct >= 100 ? "#ef4444" :
        pct >= safeNum(kpis.budgetThreshold, 80) ? "#f59e0b" : "#7c5cff";
    }

    const alerts = state?.alerts || {};
    const hasAlert = !!(alerts.budget_alert || alerts.forecast_alert);
    if (badge) badge.textContent = hasAlert ? "ALERTA" : "OK";

    if (box) {
      const messages = Array.isArray(alerts.messages) ? alerts.messages : [];
      if (!messages.length) {
        box.innerHTML = "<div>Sem alertas ativos no momento.</div>";
      } else {
        box.innerHTML = messages.map(m => `<div>${m}</div>`).join("");
      }
    }
  }

  function renderOptimization(state) {
    const wrap = byId("optimizationRecommendation");
    if (!wrap) return;

    const rec = state?.optimization?.top_recommendation;
    if (!rec) {
      wrap.innerHTML = `
        <div class="placeholder">
          <div class="placeholder-title">Sem recomendação ativa</div>
          <div class="placeholder-sub">Cadastre modelos atual e substituto no pricing para habilitar a comparação.</div>
        </div>`;
      return;
    }

    wrap.innerHTML = `
      <div class="list-row">
        <span>${rec.summary || "Otimização identificada"}</span>
        <b>Economia: ${fmtBRL(rec.estimated_savings_brl || 0)}</b>
      </div>
      <div class="hint" style="margin-top:10px;">
        Modelo atual: <b>${rec.current_model || "N/A"}</b> →
        sugerido: <b>${rec.suggested_model || "N/A"}</b> •
        cobertura estimada: <b>${safeNum(rec.coverage_percent, 0).toFixed(1)}%</b>
      </div>`;
  }

  function renderSimpleCostList(containerId, rows, emptyTitle, emptySub) {
    const el = byId(containerId);
    if (!el) return;
    el.innerHTML = "";
    if (!Array.isArray(rows) || !rows.length) {
      el.innerHTML = `
        <div class="placeholder">
          <div class="placeholder-title">${emptyTitle}</div>
          <div class="placeholder-sub">${emptySub}</div>
        </div>`;
      return;
    }
    rows.forEach(r => {
      const div = document.createElement("div");
      div.className = "list-row";
      div.innerHTML = `<span>${r.label || "N/A"}</span><b>${fmtBRL(r.amount_brl || 0)}</b>`;
      el.appendChild(div);
    });
  }

  function renderShowback(state) {
    const sb = state?.showback || {};
    renderSimpleCostList("showbackProjects", sb.by_project, "Sem dados por projeto", "Os custos ainda não foram classificados por projeto.");
    renderSimpleCostList("showbackAgents", sb.by_agent, "Sem dados por agente", "Os custos ainda não foram classificados por agente.");
    renderSimpleCostList("showbackSources", sb.by_source, "Sem dados por origem", "Os custos ainda não foram classificados por origem.");
  }


  function renderTopLists(state) {
    // No real mode pode não existir top_tasks/top_flows.
    // Mantém: se existir, renderiza. Se não, limpa ou mostra placeholder.
    const topTasks = byId("topTasks");
    const topFlows = byId("topFlows");

    const topTasksArr = state?.series?.top_tasks || state?.breakdown?.top_tasks || [];
    const topFlowsArr = state?.series?.top_flows || state?.breakdown?.top_flows || [];

    if (topTasks) {
      topTasks.innerHTML = "";
      if (!topTasksArr.length) {
        const div = document.createElement("div");
        div.className = "placeholder";
        div.innerHTML = `<div class="placeholder-title">Sem dados de Top Tasks</div><div class="placeholder-sub">No modo real, isso depende de task_id/flow_id na coleta.</div>`;
        topTasks.appendChild(div);
      } else {
        topTasksArr.forEach(t => {
          const div = document.createElement("div");
          div.className = "list-row";
          div.innerHTML = `<span>${t.task_name || t.key || t.task_id || "N/A"}</span><b>${fmtBRL(t.cost || t.value || 0)}</b>`;
          topTasks.appendChild(div);
        });
      }
    }

    if (topFlows) {
      topFlows.innerHTML = "";
      if (!topFlowsArr.length) {
        const div = document.createElement("div");
        div.className = "placeholder";
        div.innerHTML = `<div class="placeholder-title">Sem dados de Top Flows</div><div class="placeholder-sub">No modo real, isso depende de task_id/flow_id na coleta.</div>`;
        topFlows.appendChild(div);
      } else {
        topFlowsArr.forEach(f => {
          const div = document.createElement("div");
          div.className = "list-row";
          div.innerHTML = `<span>${f.flow_name || f.key || f.flow_id || "N/A"}</span><b>${fmtBRL(f.cost || f.value || 0)}</b>`;
          topFlows.appendChild(div);
        });
      }
    }
  }

  async function postJSON(url, body) {
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {})
    });
    return res.json();
  }

  async function handleAction(action, steps) {
    try {
      // Se estiver em modo REAL, simulação pode estar desabilitada no backend
      if ((s?.source === "real" || s?.filters?.data_source === "real") && action !== "refresh") {
        setStatus("Modo REAL: simulação desabilitada.");
        return;
      }

      setStatus("Executando...");
      let data;
      if (action === "refresh") {
        const res = await fetch("/api/state");
        data = { state: await res.json(), ok: true, result: {} };
      } else if (action === "reset") {
        data = await postJSON("/api/simulate/reset");
      } else if (action === "spike") {
        data = await postJSON("/api/simulate/spike");
      } else {
        data = await postJSON("/api/simulate/step", { steps: Number(steps || 10) });
      }

      const st = data.state;
      if (!st) {
        setStatus("Resposta inválida.");
        return;
      }

      updateKPIs(st);
      renderTopLists(st);
      updateCharts(st);

      const createdI = data.result?.created_interactions ?? "-";
      const createdL = data.result?.created_ledgers ?? "-";
      setStatus(`OK • +${createdI} interações • +${createdL} ledger`);
    } catch (e) {
      console.error(e);
      setStatus("Erro ao simular.");
    }
  }

  function bindButtons() {
    document.querySelectorAll("[data-action]").forEach(btn => {
      btn.addEventListener("click", () => {
        const action = btn.getAttribute("data-action");
        const steps = btn.getAttribute("data-steps");
        handleAction(action, steps);
      });
    });
  }

  // Init
  buildCharts(s);
  updateKPIs(s);
  renderTopLists(s);
  bindButtons();
})();