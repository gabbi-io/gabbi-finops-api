from __future__ import annotations

import os
from flask import Flask, jsonify, redirect, render_template, request, url_for

import simulator as sim

app = Flask(__name__)
app.config["TEMPLATES_AUTO_RELOAD"] = True


def _state():
    return sim.load_state()


def _save(state):
    sim.save_state(state)


@app.route("/")
def home():
    return redirect(url_for("dashboard"))


@app.route("/finops")
def dashboard():
    state = _state()

    # controls from query string
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

    dataset = sim.summarize(state)
    return render_template("finops_dashboard.html", dataset=dataset)


@app.route("/ledger")
def ledger():
    state = _state()
    dataset = sim.summarize(state)
    return render_template("finops_ledger.html", dataset=dataset)


@app.route("/interactions")
def interactions():
    state = _state()
    dataset = sim.summarize(state)
    return render_template("finops_interactions.html", dataset=dataset)


@app.route("/accumulators")
def accumulators():
    state = _state()
    dataset = sim.summarize(state)
    return render_template("finops_accumulators.html", dataset=dataset)


@app.route("/pricing", methods=["GET", "POST"])
def pricing():
    state = _state()

    if request.method == "POST":
        model = (request.form.get("model") or "").strip()
        min_tokens = int(request.form.get("min_tokens") or "0")
        min_cost = float(request.form.get("min_cost") or "0")
        if model and min_tokens > 0 and min_cost >= 0:
            sim.update_pricing(state, model=model, min_tokens=min_tokens, min_cost=min_cost)
            _save(state)
        return redirect(url_for("pricing"))

    dataset = sim.summarize(state)
    return render_template("finops_pricing.html", dataset=dataset)


@app.route("/settings", methods=["GET", "POST"])
def settings():
    state = _state()
    if request.method == "POST":
        url = (request.form.get("grafana_embed_url") or "").strip()
        sim.set_grafana_url(state, url)
        _save(state)
        return redirect(url_for("settings"))
    dataset = sim.summarize(state)
    return render_template("finops_settings.html", dataset=dataset)


# ---------------- API (para botões "live") ----------------

@app.route("/api/state")
def api_state():
    state = _state()
    return jsonify(sim.summarize(state))


@app.route("/api/simulate/reset", methods=["POST"])
def api_reset():
    state = _state()
    sim.reset_live(state)
    _save(state)
    return jsonify({"ok": True, "message": "Estado resetado (live).", "state": sim.summarize(state)})


@app.route("/api/simulate/step", methods=["POST"])
def api_step():
    state = _state()
    payload = request.get_json(silent=True) or {}
    steps = int(payload.get("steps") or 10)
    spike = bool(payload.get("spike") or False)
    out = sim.step_live(state, steps=steps, spike=spike)
    _save(state)
    return jsonify({"ok": True, "result": out, "state": sim.summarize(state)})


@app.route("/api/simulate/spike", methods=["POST"])
def api_spike():
    state = _state()
    out = sim.step_live(state, steps=30, spike=True)
    _save(state)
    return jsonify({"ok": True, "result": out, "state": sim.summarize(state)})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=True)
