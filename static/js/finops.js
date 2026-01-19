(function () {
  const s = window.__FINOPS_STATE__;
  if (!s) return;

  const statusEl = document.getElementById("status");

  function setStatus(msg) {
    if (!statusEl) return;
    statusEl.textContent = msg || "";
  }

  // ---------- Charts ----------
  let chartDay, chartSource, chartModel;

  function byId(id) { return document.getElementById(id); }

  function makeChart(ctx, type, labels, data, label) {
    return new Chart(ctx, {
      type,
      data: {
        labels,
        datasets: [{
          label,
          data
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false
      }
    });
  }

  function buildCharts(state) {
    const day = state.series.cost_by_day || [];
    const src = state.series.cost_by_source || [];
    const mdl = state.series.cost_by_model || [];

    const dayLabels = day.map(x => x.day);
    const dayValues = day.map(x => x.cost);

    const srcLabels = src.map(x => x.source);
    const srcValues = src.map(x => x.cost);

    const mdlLabels = mdl.map(x => x.model);
    const mdlValues = mdl.map(x => x.cost);

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
    if (chartDay) {
      chartDay.data.labels = state.series.cost_by_day.map(x => x.day);
      chartDay.data.datasets[0].data = state.series.cost_by_day.map(x => x.cost);
      chartDay.update();
    }
    if (chartSource) {
      chartSource.data.labels = state.series.cost_by_source.map(x => x.source);
      chartSource.data.datasets[0].data = state.series.cost_by_source.map(x => x.cost);
      chartSource.update();
    }
    if (chartModel) {
      chartModel.data.labels = state.series.cost_by_model.map(x => x.model);
      chartModel.data.datasets[0].data = state.series.cost_by_model.map(x => x.cost);
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

    if (total) total.textContent = `R$ ${state.kpis.total_cost}`;
    if (manual) manual.textContent = `R$ ${state.kpis.manual_cost}`;
    if (auto) auto.textContent = `R$ ${state.kpis.automation_cost}`;
    if (ints) ints.textContent = `${state.kpis.interaction_rows}`;
    if (ledgerRows) ledgerRows.textContent = `${state.kpis.ledger_rows}`;
  }

  function renderTopLists(state) {
    const topTasks = byId("topTasks");
    const topFlows = byId("topFlows");
    if (topTasks) {
      topTasks.innerHTML = "";
      (state.series.top_tasks || []).forEach(t => {
        const div = document.createElement("div");
        div.className = "list-row";
        div.innerHTML = `<span>${t.task_name}</span><b>R$ ${t.cost}</b>`;
        topTasks.appendChild(div);
      });
    }
    if (topFlows) {
      topFlows.innerHTML = "";
      (state.series.top_flows || []).forEach(f => {
        const div = document.createElement("div");
        div.className = "list-row";
        div.innerHTML = `<span>${f.flow_name}</span><b>R$ ${f.cost}</b>`;
        topFlows.appendChild(div);
      });
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
      setStatus("Executando...");
      let data;
      if (action === "reset") {
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
  bindButtons();
})();
