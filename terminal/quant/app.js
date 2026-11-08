/* Case NFL Quant terminal — renders backend outputs only. */
(function () {
  "use strict";
  const $ = s => document.querySelector(s);
  const main = $("#main");
  const C = Charts.C, F = Charts.fmt;
  const state = { ws: "predict", season: null, week: null, game: null, sim: null, remove: new Set(), team: "KC", tseason: null,
                  window: "season", context: "all", cat: "Overall offensive efficiency", tq: "", player: null, pseason: null, pwindow: "season" };
  const cache = {};

  function esc(v) { return String(v ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }
  async function api(path) {
    if (cache[path]) return cache[path];
    const r = await fetch(path);
    if (!r.ok) { let m = r.statusText; try { m = (await r.json()).detail || m; } catch (e) {} throw new Error(m); }
    const j = await r.json();
    if (!path.includes("simulate")) cache[path] = j;
    return j;
  }
  const pct = (v, d = 1) => v == null ? "—" : (v * 100).toFixed(d) + "%";
  const num = (v, d = 2) => v == null ? "—" : Number(v).toFixed(d);
  const sgn = (v, d = 2) => v == null ? "—" : (v > 0 ? "+" : "") + Number(v).toFixed(d);
  const cls = (v, dir = 1) => v == null || dir === 0 ? "neu" : (v * dir > 0 ? "pos" : v * dir < 0 ? "neg" : "neu");
  function panel(title, body, meta = "", extra = "") { return `<section class="panel ${extra}"><div class="ph"><h3>${esc(title)}</h3><span class="meta">${meta}</span></div><div class="pb">${body}</div></section>`; }
  function pctbar(p) {
    if (p == null) return `<span class="tag na">n/a</span>`;
    const col = p >= 67 ? C.grn : p <= 33 ? C.red : C.blue;
    return `<span class="pct"><i style="width:${p.toFixed(0)}%;background:${col}"></i></span> <span class="mono">${p.toFixed(0)}</span>`;
  }
  function mount(node, sel) { const t = main.querySelector(sel); if (t) { t.innerHTML = ""; t.appendChild(node); } }
  function fmtVal(v, unit) {
    if (v == null) return "—";
    if (unit === "rate") return pct(v);
    if (["count", "games", "yards", "TD", "per game", "touches", "plays", "players", "snaps", "days", "miles"].includes(unit)) return Math.abs(v) >= 100 ? v.toFixed(0) : v.toFixed(1);
    return Math.abs(v) >= 100 ? v.toFixed(1) : v.toFixed(3);
  }
  function sortable(tableSel) {
    const t = main.querySelector(tableSel); if (!t) return;
    t.querySelectorAll("th").forEach((th, i) => th.addEventListener("click", () => {
      const rows = [...t.tBodies[0].rows], asc = th.dataset.asc !== "1"; th.dataset.asc = asc ? "1" : "0";
      rows.sort((a, b) => { const x = a.cells[i].dataset.v ?? a.cells[i].textContent, y = b.cells[i].dataset.v ?? b.cells[i].textContent;
        const nx = parseFloat(x), ny = parseFloat(y); const r = isNaN(nx) || isNaN(ny) ? String(x).localeCompare(String(y)) : nx - ny; return asc ? r : -r; });
      rows.forEach(r => t.tBodies[0].appendChild(r));
    }));
  }

  // ------------------------------------------------------------- navigation
  $("#wsnav").addEventListener("click", e => {
    const b = e.target.closest("button[data-ws]"); if (!b) return;
    document.querySelectorAll("#wsnav button").forEach(x => x.classList.toggle("on", x === b));
    state.ws = b.dataset.ws; render();
  });
  function render() {
    main.innerHTML = `<div class="loading">Loading…</div>`;
    ({ predict: wsPredict, matchup: wsMatchup, team: wsTeam, player: wsPlayer, audit: wsAudit, data: wsData })[state.ws]().catch(err => {
      main.innerHTML = `<div class="err">${esc(err.message)}</div>`;
    });
  }

  // ------------------------------------------------------------- Prediction Engine
  async function wsPredict() {
    const q = state.season ? `?season=${state.season}&week=${state.week}` : "";
    const sch = await api(`/api/q/schedule${q}`);
    state.season = sch.season; state.week = sch.week;
    if (!state.game || !sch.games.find(g => g.game_id === state.game)) state.game = sch.games[0]?.game_id;
    main.innerHTML = `
      <div class="bar"><label>Season</label><select id="pSeason">${sch.seasons.slice().reverse().map(s => `<option ${s === sch.season ? "selected" : ""}>${s}</option>`).join("")}</select>
      <label>Week</label><select id="pWeek">${sch.weeks.map(w => `<option ${w === sch.week ? "selected" : ""}>${w}</option>`).join("")}</select>
      <span class="small">Completed games show walk-forward pregame predictions (trained only on prior seasons, as-of ratings). Unplayed games use the production fit.</span></div>
      <div class="grid g-side"><section class="panel"><div class="ph"><h3>Slate · Week ${sch.week}</h3><span class="meta">${sch.games.length} games</span></div>
        <div class="pb tight" id="slate">${sch.games.map(gameCard).join("")}</div></section>
        <div id="gameDetail"><div class="loading">Select a game</div></div></div>`;
    $("#pSeason").onchange = e => { state.season = +e.target.value; state.week = 1; state.game = null; render(); };
    $("#pWeek").onchange = e => { state.week = +e.target.value; state.game = null; render(); };
    $("#slate").addEventListener("click", e => { const c = e.target.closest(".gamecard"); if (!c) return; state.game = c.dataset.id; state.sim = null; state.remove.clear();
      document.querySelectorAll(".gamecard").forEach(x => x.classList.toggle("sel", x === c)); gameDetail(); });
    if (state.game) gameDetail();
  }
  function gameCard(g) {
    const p = g.p_home;
    const res = g.completed ? `<span class="mono">${g.away_score}-${g.home_score} F</span>` : `<span class="gc-sub">${esc((g.kickoff || "").slice(0, 16).replace("T", " "))}</span>`;
    return `<div class="gamecard ${g.game_id === state.game ? "sel" : ""}" data-id="${esc(g.game_id)}">
      <div><div class="gc-teams">${esc(g.away)} @ ${esc(g.home)}</div><div class="gc-sub">Proj ${num(g.away_points, 1)}–${num(g.home_points, 1)} · ${g.prediction_source ? (g.prediction_source === "production_pregame" ? "production" : "walk-forward") : "no prediction"}</div></div>
      <div style="text-align:right">${res}<div class="gc-sub">${esc(g.home)} ${p == null ? "—" : pct(p, 0)}</div></div>
      <div class="wpbar"><i style="width:${p == null ? 0 : (100 * (1 - p)).toFixed(1)}%;background:${C.amb}"></i></div></div>`;
  }
  async function gameDetail() {
    const box = $("#gameDetail"); box.innerHTML = `<div class="loading">Loading prediction…</div>`;
    const g = await api(`/api/q/game/prediction?game_id=${encodeURIComponent(state.game)}`);
    const m = g.model;
    const act = g.actual ? `<div class="note">Actual final: ${esc(g.away)} ${g.actual.away} – ${esc(g.home)} ${g.actual.home} (margin ${sgn(g.actual.home - g.actual.away, 0)}; model ${sgn(m.margin_ensemble, 1)}).</div>` : "";
    const players = g.player_projections.filter(p => ["QB", "RB", "WR", "TE"].includes(p.group));
    box.innerHTML = `
      <div class="grid g-main">
        <div class="grid">
          ${panel("Game model · " + (g.source === "production_pregame" ? "production pregame" : "walk-forward pregame"), `
            <div class="score"><div class="tm"><div class="ab">${esc(g.away)}</div><div class="pts">${num(m.away_points, 1)}</div><div class="wp">${pct(1 - m.p_home)}</div></div>
            <div class="vs">EXPECTED<br>POINTS</div><div class="tm"><div class="ab">${esc(g.home)}</div><div class="pts">${num(m.home_points, 1)}</div><div class="wp">${pct(m.p_home)}</div></div></div>
            <div class="kpis">
              <div class="kpi"><div class="k">Home margin</div><div class="v">${sgn(m.margin_ensemble, 1)}</div><div class="s">80% PI ${num(m.margin_p10, 1)} / ${num(m.margin_p90, 1)}</div></div>
              <div class="kpi"><div class="k">Total</div><div class="v">${num(m.total_ensemble, 1)}</div><div class="s">80% PI ${num(m.total_p10, 1)} / ${num(m.total_p90, 1)}</div></div>
              <div class="kpi"><div class="k">σ margin / total</div><div class="v">${num(m.sigma_margin, 1)}</div><div class="s">${num(m.sigma_total, 1)} (prior OOS)</div></div>
              <div class="kpi"><div class="k">Ridge · XGB · Elo</div><div class="v" style="font-size:14px">${sgn(m.margin_ridge, 1)} · ${sgn(m.margin_xgb, 1)} · ${sgn(m.margin_elo, 1)}</div><div class="s">model ladder (margin)</div></div>
              <div class="kpi"><div class="k">Market benchmark</div><div class="v" style="font-size:14px">${g.market_benchmark.spread_line == null ? "—" : sgn(g.market_benchmark.spread_line, 1)} / ${num(g.market_benchmark.total_line, 1)}</div><div class="s">not a model input</div></div>
            </div>${act}<p class="note">${esc(g.information_cutoff)}. Win probability = P(margin&gt;0) under N(μ,σ), Platt-recalibrated on prior out-of-sample seasons.</p>`,
            `<b class="t-pre">PREDICTED</b>`)}
          <section class="panel"><div class="ph"><h3>Monte Carlo simulation</h3><span class="meta" id="simMeta">seeded · play-level</span></div><div class="pb">
            <div class="bar"><label>Sims</label><select id="simN"><option>1000</option><option selected>10000</option><option>50000</option></select>
            <label>Seed</label><input type="number" id="simSeed" value="2026" style="width:80px"><button class="btn" id="simGo">Run simulation</button>
            <span class="small">Click players below to rule them out (injury scenario).</span></div>
            <div class="chips" id="outChips">${players.filter(p => (p.stats.attempts?.mean || 0) > 5 || (p.stats.targets?.mean || 0) > 2.5 || (p.stats.carries?.mean || 0) > 4).map(p => `<span class="chip ${state.remove.has(p.player_id) ? "on" : ""}" data-id="${esc(p.player_id)}">${esc(p.team)} ${esc(p.name)}</span>`).join("")}</div>
            <div id="simOut"><p class="note">Simulation is anchored to the game model's expected points; player boxes, score distribution and win probability come from the same simulated games.</p></div></div></section>
        </div>
        <div class="grid">
          ${panel("Projected starting QBs", ["away", "home"].map(s => { const q = g.projected_qbs[s]; return q ? `<div class="li"><span>${esc(g[s])} · ${esc(q.name || q.id)}</span><span class="r">${esc(q.source)} · prior EPA/db ${num(q.epa_db_prior, 3)}</span></div>` : ""; }).join(""), "pregame rule")}
          ${panel("Why — model contributions", g.explanations ? `<div id="shap"></div><p class="note">TreeSHAP (XGB margin) + standardized ridge terms, blended by ensemble weights; positive favours ${esc(g.home)}.</p>` : `<p class="note">Feature attributions are computed for production (unplayed) games. Key pregame inputs:</p>${featTable(g.features)}`)}
          ${panel("Official injury report (game week)", g.injury_report.length ? `<div class="scroll s300"><table><thead><tr><th>Team</th><th>Player</th><th>Pos</th><th>Status</th><th>Practice</th></tr></thead><tbody>${g.injury_report.map(r => `<tr><td>${esc(r.team)}</td><td>${esc(r.player)}</td><td>${esc(r.position)}</td><td>${esc(r.status)}</td><td class="small">${esc(r.practice || "")}</td></tr>`).join("")}</tbody></table></div>` : `<p class="note">No report statuses published for this week in the source feed.</p>`, "nflverse injuries", "")}
        </div>
      </div>
      ${panel("Player projections · ML models (pregame)", projTable(g), "mean · 10th–90th pct · P(≥1)")}`;
    if (g.explanations) mount(Charts.hbars(g.explanations.map(e => ({ label: e.feature, value: e.xgb_shap * 0.5 + e.ridge_contribution * 0.5 })), { w: 460, labelW: 150 }), "#shap");
    sortable("#projT");
    $("#outChips").addEventListener("click", e => { const c = e.target.closest(".chip"); if (!c) return; const id = c.dataset.id;
      state.remove.has(id) ? state.remove.delete(id) : state.remove.add(id); c.classList.toggle("on"); });
    $("#simGo").onclick = runSim;
  }
  function featTable(f) {
    if (!f) return "";
    return `<table><tbody>${Object.entries(f).map(([k, v]) => `<tr><td>${esc(k)}</td><td class="n">${num(v, 3)}</td></tr>`).join("")}</tbody></table>`;
  }
  function projTable(g) {
    const cols = [["attempts", "Att"], ["passing_yards", "PassYd"], ["passing_tds", "PassTD"], ["interceptions", "INT"], ["carries", "Car"], ["rushing_yards", "RushYd"],
                  ["targets", "Tgt"], ["receptions", "Rec"], ["receiving_yards", "RecYd"], ["receiving_tds", "RecTD"], ["rushing_tds", "RushTD"]];
    const rows = g.player_projections.filter(p => ["QB", "RB", "WR", "TE"].includes(p.group))
      .sort((a, b) => ((b.stats.passing_yards?.mean || 0) + (b.stats.receiving_yards?.mean || 0) + (b.stats.rushing_yards?.mean || 0)) - ((a.stats.passing_yards?.mean || 0) + (a.stats.receiving_yards?.mean || 0) + (a.stats.rushing_yards?.mean || 0)));
    return `<div class="scroll s420"><table id="projT"><thead><tr><th>Player</th><th>Tm</th><th>Pos</th>${cols.map(c => `<th class="n">${c[1]}</th>`).join("")}</tr></thead><tbody>
      ${rows.map(p => `<tr><td>${esc(p.name)}</td><td>${esc(p.team)}</td><td>${esc(p.group)}</td>${cols.map(([k]) => { const s = p.stats[k]; if (!s) return `<td class="n">·</td>`;
        const tip = `p10 ${num(s.q10, 1)} · p90 ${num(s.q90, 1)}${s.p_ge1 != null ? " · P≥1 " + pct(s.p_ge1, 0) : ""}${s.y != null ? " · actual " + s.y : ""}`;
        return `<td class="n" data-v="${s.mean}" title="${esc(tip)}">${num(s.mean, k.includes("yards") ? 1 : 2)}${s.y != null ? `<br><span class="small">${s.y}</span>` : ""}</td>`; }).join("")}</tr>`).join("")}
      </tbody></table></div><p class="note">Hover a cell for the 10th/90th percentile and P(≥1). For completed games the actual value is shown under the projection. Out/Doubtful players are excluded pregame.</p>`;
  }
  async function runSim() {
    const out = $("#simOut"), n = $("#simN").value, seed = $("#simSeed").value;
    $("#simGo").disabled = true; out.innerHTML = `<div class="loading">Simulating ${esc(n)} games…</div>`;
    try {
      const r = await api(`/api/q/game/simulate?game_id=${encodeURIComponent(state.game)}&n=${n}&seed=${seed}&remove=${[...state.remove].join(",")}`);
      state.sim = r; renderSim(r);
    } catch (e) { out.innerHTML = `<div class="err">${esc(e.message)}</div>`; }
    $("#simGo").disabled = false;
  }
  function renderSim(r) {
    const out = $("#simOut"), H = r.home, A = r.away, th = r.team[H], ta = r.team[A];
    $("#simMeta").textContent = `${r.n_sims} sims · seed ${r.seed} · ${r.elapsed_s}s · OT rules: ${r.ot_rules}`;
    const ok = Object.values(r.consistency).every(v => v !== false);
    const rows = [["points", "Points"], ["plays", "Plays"], ["dropbacks", "Dropbacks"], ["pass_yds", "Pass yds"], ["rush_yds", "Rush yds"], ["total_yds", "Total yds"], ["drives", "Drives"],
                  ["turnovers", "Turnovers"], ["sacks", "Sacks taken"], ["td", "TDs"], ["fg_att", "FG att"], ["punts", "Punts"], ["first_downs", "First downs"], ["penalties", "Penalties"]];
    out.innerHTML = `<div class="kpis">
        <div class="kpi"><div class="k">${esc(H)} win</div><div class="v">${pct(r.win_prob_home)}</div><div class="s">${esc(A)} ${pct(r.win_prob_away)} · tie ${pct(r.tie_prob, 2)}</div></div>
        <div class="kpi"><div class="k">Overtime</div><div class="v">${pct(r.ot_prob)}</div><div class="s">${esc(r.ot_rules)} rules</div></div>
        <div class="kpi"><div class="k">Score (median)</div><div class="v">${r.away_points.median.toFixed(0)}–${r.home_points.median.toFixed(0)}</div><div class="s">mean ${num(r.away_points.mean, 1)}–${num(r.home_points.mean, 1)}</div></div>
        <div class="kpi"><div class="k">Margin σ</div><div class="v">${num(r.margin.sd, 1)}</div><div class="s">total σ ${num(r.total.sd, 1)}</div></div>
        <div class="kpi"><div class="k">Accounting checks</div><div class="v ${ok ? "pos" : "neg"}">${ok ? "PASS" : "FAIL"}</div><div class="s">Σplayers = team totals</div></div></div>
      <div class="grid g2" style="margin-top:8px"><div><div class="small">Home margin distribution</div><div id="hMargin"></div></div><div><div class="small">Total points distribution</div><div id="hTotal"></div></div></div>
      <div class="grid g2" style="margin-top:8px"><div><table><thead><tr><th>Team box (mean)</th><th class="n">${esc(A)}</th><th class="n">${esc(H)}</th></tr></thead><tbody>
        ${rows.map(([k, l]) => `<tr><td>${l}</td><td class="n">${num(ta[k].mean, 1)}</td><td class="n">${num(th[k].mean, 1)}</td></tr>`).join("")}
        <tr><td>Points / drive</td><td class="n">${num(ta.points_per_drive, 2)}</td><td class="n">${num(th.points_per_drive, 2)}</td></tr>
        <tr><td>Dropback ratio</td><td class="n">${pct(ta.pass_ratio)}</td><td class="n">${pct(th.pass_ratio)}</td></tr>
        <tr><td>Efficiency scalar (anchoring)</td><td class="n">${num(ta.eff_scalar, 3)}</td><td class="n">${num(th.eff_scalar, 3)}</td></tr></tbody></table></div>
        <div><table><thead><tr><th>Most likely scores</th><th class="n">Prob</th></tr></thead><tbody>${r.top_scores.map(s => `<tr><td class="mono">${esc(A)} ${s.away} – ${esc(H)} ${s.home}</td><td class="n">${pct(s.prob, 2)}</td></tr>`).join("")}</tbody></table></div></div>
      <h4 class="small" style="margin:10px 0 4px">Simulated player box scores (click a row for the full distribution)</h4>
      <div class="scroll s420"><table id="simP"><thead><tr><th>Player</th><th>Tm</th><th class="n">PassYd</th><th class="n">PassTD</th><th class="n">INT</th><th class="n">Car</th><th class="n">RushYd</th><th class="n">Tgt</th><th class="n">Rec</th><th class="n">RecYd</th><th class="n">TD P≥1</th><th class="n">Tkl</th><th class="n">Sk</th><th class="n">FGM</th></tr></thead><tbody>
      ${r.players.map((p, i) => { const s = p.stats, v = k => s[k] ? s[k].mean : null, td = Math.max(s.rec_td?.p_ge1 || 0, s.rush_td?.p_ge1 || 0) || null;
        return `<tr data-i="${i}"><td>${esc(p.name)}</td><td>${esc(p.team)}</td><td class="n">${num(v("pass_yds"), 1)}</td><td class="n">${num(v("pass_td"), 2)}</td><td class="n">${num(v("pass_int"), 2)}</td><td class="n">${num(v("rush_att"), 1)}</td><td class="n">${num(v("rush_yds"), 1)}</td><td class="n">${num(v("targets"), 1)}</td><td class="n">${num(v("rec"), 1)}</td><td class="n">${num(v("rec_yds"), 1)}</td><td class="n">${td == null ? "—" : pct(td, 0)}</td><td class="n">${num(v("tackles"), 1)}</td><td class="n">${num(v("def_sacks"), 2)}</td><td class="n">${num(v("fg_made"), 2)}</td></tr>`; }).join("")}
      </tbody></table></div><div id="simDist"></div>
      ${r.actual ? `<p class="note">Actual final: ${esc(A)} ${r.actual.away} – ${esc(H)} ${r.actual.home}. Simulated P(actual margin or more extreme) can be read off the histogram.</p>` : ""}
      ${r.scenario.removed.length ? `<p class="note">Scenario: ruled out ${r.scenario.removed.length} player(s); anchor targets ${num(r.scenario.anchor_targets.away, 1)}–${num(r.scenario.anchor_targets.home, 1)}. ${esc(r.scenario.note)}</p>` : ""}`;
    mount(Charts.histogram(r.margin_hist, { split: 0, leftColor: C.amb, rightColor: C.blue, marks: r.actual ? [{ x: r.actual.home - r.actual.away, label: "actual" }] : [] }), "#hMargin");
    mount(Charts.histogram(r.total_hist, { color: C.vio, marks: r.actual ? [{ x: r.actual.home + r.actual.away, label: "actual" }] : [] }), "#hTotal");
    sortable("#simP");
    $("#simP").addEventListener("click", e => { const tr = e.target.closest("tr[data-i]"); if (!tr) return; showDist(r.players[+tr.dataset.i]); });
  }
  function showDist(p) {
    const box = $("#simDist");
    const keys = Object.keys(p.stats).filter(k => p.stats[k].mean > 0.05);
    box.innerHTML = `<h4 class="small" style="margin:10px 0 4px">${esc(p.name)} — simulated distributions</h4><div class="grid g3" id="distGrid"></div>`;
    const grid = $("#distGrid");
    keys.slice(0, 9).forEach(k => {
      const s = p.stats[k], d = document.createElement("div"); d.className = "panel";
      d.innerHTML = `<div class="ph"><h3>${esc(k)}</h3><span class="meta">μ ${num(s.mean, 1)} · med ${num(s.median, 0)} · σ ${num(s.sd, 1)} · p10–p90 ${num(s.p10, 0)}–${num(s.p90, 0)}</span></div><div class="pb"></div>`;
      d.querySelector(".pb").appendChild(s.hist ? Charts.histogram(s.hist, { w: 300, h: 120 }) : Charts.pmf(s.pmf, { w: 300 }));
      grid.appendChild(d);
    });
  }

  // ------------------------------------------------------------- Matchup Lab
  async function wsMatchup() {
    const q = state.season ? `?season=${state.season}&week=${state.week}` : "";
    const sch = await api(`/api/q/schedule${q}`);
    state.season = sch.season; state.week = sch.week;
    if (!state.game || !sch.games.find(g => g.game_id === state.game)) state.game = sch.games[0]?.game_id;
    main.innerHTML = `<div class="bar"><label>Season</label><select id="mSeason">${sch.seasons.slice().reverse().filter(s => s >= 2016).map(s => `<option ${s === sch.season ? "selected" : ""}>${s}</option>`).join("")}</select>
      <label>Week</label><select id="mWeek">${sch.weeks.map(w => `<option ${w === sch.week ? "selected" : ""}>${w}</option>`).join("")}</select>
      <label>Game</label><select id="mGame">${sch.games.map(g => `<option value="${esc(g.game_id)}" ${g.game_id === state.game ? "selected" : ""}>${esc(g.away)} @ ${esc(g.home)}</option>`).join("")}</select></div><div id="mBody"><div class="loading">Scoring matchup models…</div></div>`;
    $("#mSeason").onchange = e => { state.season = +e.target.value; state.week = 1; state.game = null; render(); };
    $("#mWeek").onchange = e => { state.week = +e.target.value; state.game = null; render(); };
    $("#mGame").onchange = e => { state.game = e.target.value; wsMatchupBody(); };
    wsMatchupBody();
  }
  async function wsMatchupBody() {
    const b = $("#mBody"); b.innerHTML = `<div class="loading">Scoring matchup models…</div>`;
    const m = await api(`/api/q/game/matchup?game_id=${encodeURIComponent(state.game)}`);
    const labels = { epa: "EPA/play", success: "Success rate", pressure: "Pressure rate", sack: "Sack rate", explosive_pass: "Explosive pass (20+)", interception: "INT rate", stuff: "Stuff rate", explosive_run: "Explosive run (10+)" };
    const good = { epa: 1, success: 1, pressure: -1, sack: -1, explosive_pass: 1, interception: -1, stuff: -1, explosive_run: 1 };
    const sideHtml = s => `
      ${panel(`${s.offense} offense vs ${s.defense} defense`, `
        <table><thead><tr><th>Outcome (per play)</th><th class="n">Predicted</th><th class="n">vs avg D</th><th class="n">Matchup impact</th></tr></thead><tbody>
        ${["pass", "run"].map(u => Object.entries(s[u]).map(([k, v]) => `<tr><td>${u === "pass" ? "Dropback" : "Run"} · ${labels[k] || k}</td><td class="n">${k === "epa" ? num(v.predicted, 3) : pct(v.predicted)}</td><td class="n">${k === "epa" ? num(v.vs_league_avg_defense, 3) : pct(v.vs_league_avg_defense)}</td><td class="n ${cls(v.impact, good[k])}">${k === "epa" ? sgn(v.impact, 3) : sgn(v.impact * 100, 1) + " pp"}</td></tr>`).join("")).join("")}
        </tbody></table>
        <div class="grid g2" style="margin-top:8px"><div><div class="small">Coverage matrix — predicted dropback EPA</div><table class="hm"><thead><tr><th>Shell</th><th class="n">EPA/db</th><th class="n">Usage</th></tr></thead><tbody>
          <tr><td>Man</td><td class="n">${num(s.coverage.man, 3)}</td><td class="n">${pct(s.coverage.opponent_man_rate)}</td></tr>
          <tr><td>Zone</td><td class="n">${num(s.coverage.zone, 3)}</td><td class="n">${pct(1 - s.coverage.opponent_man_rate)}</td></tr>
          <tr><td><b>Usage-weighted</b></td><td class="n"><b>${num(s.coverage.weighted, 3)}</b></td><td></td></tr></tbody></table></div>
          <div><div class="small">Box structure — predicted run EPA</div><table class="hm"><tbody>
          <tr><td>Light (≤6)</td><td class="n">${num(s.box.light_box, 3)}</td></tr><tr><td>Normal (7)</td><td class="n">${num(s.box.normal_box, 3)}</td></tr>
          <tr><td>Loaded (≥8)</td><td class="n">${num(s.box.loaded_box, 3)}</td></tr><tr><td>Opp loaded-box rate</td><td class="n">${pct(s.box.opponent_loaded_rate)}</td></tr></tbody></table></div></div>`,
        `<b class="t-mod">MODELED</b>`)}`;
    const keys = Object.keys(m.profiles[m.sides[0].offense] || {});
    const [t1, t2] = m.sides.map(s => s.offense);
    b.innerHTML = `<div class="grid g2">${m.sides.map(sideHtml).join("")}</div>
      ${panel("Side-by-side unit profiles (through week " + m.through_week + ")", `<div class="scroll"><table id="mProf"><thead><tr><th>Field</th><th class="n">${esc(t1)}</th><th>Pctl</th><th class="n">${esc(t2)}</th><th>Pctl</th><th class="n">n</th><th>Source</th></tr></thead><tbody>
        ${keys.map(k => { const a = m.profiles[t1][k], c = m.profiles[t2][k]; if (!a) return ""; return `<tr><td>${esc(a.label)}</td><td class="n">${fmtVal(a.value, a.unit)}</td><td>${pctbar(a.percentile)}</td><td class="n">${fmtVal(c?.value, a.unit)}</td><td>${pctbar(c?.percentile)}</td><td class="n">${a.sample?.toFixed(0)} / ${c?.sample?.toFixed(0)}</td><td><span class="tag">${esc(a.source)}</span></td></tr>`; }).join("")}
      </tbody></table></div>`, "percentile = direction-aware rank among 32 teams")}
      <div class="grid g2">${panel("Model validation (held-out 2024–2025 plays)", `<table><thead><tr><th>Model</th><th class="n">n test</th><th class="n">Log loss</th><th class="n">Base rate</th><th class="n">R²</th></tr></thead><tbody>
        ${["pass", "run"].map(u => Object.entries(m.report[u]).map(([k, v]) => `<tr><td>${u} · ${esc(k)}</td><td class="n">${v.n_test}</td><td class="n">${v.log_loss != null ? num(v.log_loss, 4) : "—"}</td><td class="n">${v.log_loss_base_rate != null ? num(v.log_loss_base_rate, 4) : "—"}</td><td class="n">${v.r2 != null ? num(v.r2, 4) : "—"}</td></tr>`).join("")).join("")}</tbody></table>
        <p class="note">${esc(m.method)}. Play-level EPA is dominated by noise, so small R² is expected; the matchup impact is a conditional mean shift.</p>`)}
        ${panel("Unavailable matchup analyses", `<ul class="small">${m.unavailable.map(u => `<li>${esc(u)}</li>`).join("")}</ul>`)}</div>`;
    sortable("#mProf");
  }

  // ------------------------------------------------------------- Team Intelligence
  async function wsTeam() {
    const meta = await api("/api/q/teams");
    state.tseason = state.tseason || meta.seasons[meta.seasons.length - 1];
    const p = await api(`/api/q/team/profile?team=${state.team}&season=${state.tseason}&window=${state.window}&context=${state.context}`);
    const by = Object.fromEntries(p.fields.map(f => [f.id, f]));
    const kpi = (id, label, d = 3) => { const f = by[id]; return `<div class="kpi"><div class="k">${label}</div><div class="v ${cls(f?.percentile == null ? null : f.percentile - 50, 1)}">${f?.value == null ? "—" : (f.unit === "rate" ? pct(f.value) : num(f.value, d))}</div><div class="s">rank ${f?.rank ?? "—"} · n ${f?.sample?.toFixed(0) ?? "—"}</div></div>`; };
    const fields = p.fields.filter(f => f.category === state.cat && (!state.tq || f.label.toLowerCase().includes(state.tq.toLowerCase())));
    const ctxCats = ["Roster strength, health and continuity", "Venue, weather, rest and schedule", "Performance trends and regression", "Opponent strength and matchups"];
    main.innerHTML = `<div class="bar"><label>Team</label><select id="tTeam">${meta.teams.map(t => `<option ${t === state.team ? "selected" : ""}>${t}</option>`).join("")}</select>
      <label>Season</label><select id="tSeason">${meta.seasons.slice().reverse().map(s => `<option ${s === state.tseason ? "selected" : ""}>${s}</option>`).join("")}</select>
      <label>Window</label><select id="tWin">${meta.windows.map(w => `<option ${w === state.window ? "selected" : ""}>${w}</option>`).join("")}</select>
      <label>Context</label><select id="tCtx">${Object.entries(meta.contexts).map(([k, v]) => `<option value="${k}" ${k === state.context ? "selected" : ""}>${esc(v)}</option>`).join("")}</select>
      <input type="search" id="tQ" placeholder="Search ${meta.field_count} fields…" value="${esc(state.tq)}"><span class="small">${p.games} games · cohort ${p.teams_in_cohort} teams</span></div>
      <section class="panel"><div class="kpis">${kpi("off.epa_play.all", "Off EPA/play")}${kpi("def.epa_play.all", "Def EPA/play allowed")}${kpi("off.points_per_drive.all", "Points / drive", 2)}${kpi("def.points_per_drive.all", "Pts/drive allowed", 2)}${kpi("off.success.all", "Off success")}${kpi("off.pass_epa.all", "EPA/dropback")}${kpi("off.rush_epa.all", "EPA/run")}${kpi("off.win_pct.all", "Win %")}</div></section>
      <div class="tabs" id="tCats" style="margin-top:8px">${meta.categories.map(c => `<button class="${c === state.cat ? "on" : ""}" data-c="${esc(c)}">${esc(c.replace(" performance", "").replace(" and ", " & "))}</button>`).join("")}</div>
      <div class="grid g-main">
        ${ctxCats.includes(state.cat) ? panel(state.cat, ctxTable(p.context_fields.filter(f => f.category === state.cat)), "game-level fields") :
          panel(state.cat, `<div class="scroll"><table id="tF"><thead><tr><th>Field</th><th class="n">Value</th><th class="n">EB shrunk</th><th class="n">League</th><th class="n">Rank</th><th>Percentile</th><th class="n">Sample</th><th>Source</th></tr></thead><tbody>
          ${fields.map(f => `<tr data-id="${esc(f.id)}"><td title="${esc(f.formula)}">${esc(f.label)} <span class="tag">${f.side}</span></td><td class="n" data-v="${f.value ?? ""}">${f.status === "observed" ? fmtVal(f.value, f.unit) : `<span class="tag na">${f.status === "not_covered" ? "not covered " + esc(f.coverage) : "no sample"}</span>`}</td><td class="n">${fmtVal(f.shrunk, f.unit)}</td><td class="n">${fmtVal(f.league, f.unit)}</td><td class="n">${f.rank ?? "—"}</td><td data-v="${f.percentile ?? ""}">${pctbar(f.percentile)}</td><td class="n" data-v="${f.sample}">${f.sample?.toFixed(0)}</td><td><span class="tag">${esc(f.source)}</span></td></tr>`).join("") || `<tr><td colspan="8" class="empty">No fields match.</td></tr>`}
          </tbody></table></div>`, `${fields.length} fields · click a row for its game trend`)}
        <div class="grid">${panel("Field trend", `<div id="tTrend"><p class="note">Select a field to plot per-game values with rolling 3/5 and season-to-date (exposure weighted).</p></div>`)}
          ${panel("Situational context (categories 16–19)", ctxTable(p.context_fields.slice(0, 14)), "derived & modeled")}</div></div>`;
    $("#tTeam").onchange = e => { state.team = e.target.value; render(); };
    $("#tSeason").onchange = e => { state.tseason = +e.target.value; render(); };
    $("#tWin").onchange = e => { state.window = e.target.value; render(); };
    $("#tCtx").onchange = e => { state.context = e.target.value; render(); };
    $("#tQ").onchange = e => { state.tq = e.target.value; render(); };
    $("#tCats").onclick = e => { const b = e.target.closest("button"); if (b) { state.cat = b.dataset.c; render(); } };
    sortable("#tF");
    const tf = $("#tF"); if (tf) tf.addEventListener("click", async e => { const tr = e.target.closest("tr[data-id]"); if (!tr) return;
      tf.querySelectorAll("tr").forEach(x => x.classList.toggle("sel", x === tr));
      const tr2 = await api(`/api/q/team/trend?team=${state.team}&season=${state.tseason}&field=${encodeURIComponent(tr.dataset.id)}`);
      const box = $("#tTrend"); box.innerHTML = `<div class="small">${esc(tr.cells[0].textContent)}</div><div class="legend"><span><i style="background:${C.mut}"></i>game</span><span><i style="background:${C.blue}"></i>roll 3</span><span><i style="background:${C.amb}"></i>roll 5</span><span><i style="background:${C.grn}"></i>season-to-date</span></div><div id="tChart"></div>
        <table><thead><tr><th>Wk</th><th>Opp</th><th class="n">Value</th><th class="n">n</th><th class="n">Score</th></tr></thead><tbody>${tr2.map(r => `<tr><td>${r.week}</td><td>${esc(r.opponent)}</td><td class="n">${num(r.value, 3)}</td><td class="n">${r.sample.toFixed(0)}</td><td class="n">${r.points_for}-${r.points_against}</td></tr>`).join("")}</tbody></table>`;
      mount(Charts.line([{ points: tr2.map(r => ({ x: r.week, y: r.value })), color: C.mut, dotsOnly: true },
                         { points: tr2.map(r => ({ x: r.week, y: r.roll3 })), color: C.blue }, { points: tr2.map(r => ({ x: r.week, y: r.roll5 })), color: C.amb },
                         { points: tr2.map(r => ({ x: r.week, y: r.season_to_date })), color: C.grn, dash: "4 3" }], { dec: 3 }), "#tChart");
    });
  }
  function ctxTable(rows) {
    if (!rows.length) return `<p class="note">No observations.</p>`;
    return `<table><thead><tr><th>Field</th><th class="n">Value</th><th class="n">Rank</th><th class="n">n</th></tr></thead><tbody>${rows.map(r => `<tr><td title="${esc(r.formula)}">${esc(r.label)} <span class="tag">${esc(r.tier)}</span></td><td class="n">${fmtVal(r.value, r.unit)}</td><td class="n">${r.rank ?? "—"}</td><td class="n">${r.sample ?? "—"}</td></tr>`).join("")}</tbody></table>`;
  }

  // ------------------------------------------------------------- Player Intelligence
  async function wsPlayer() {
    const meta = await api("/api/q/teams");
    state.pseason = state.pseason || meta.seasons[meta.seasons.length - 1];
    main.innerHTML = `<div class="grid g-side"><section class="panel"><div class="ph"><h3>Player universe</h3><span class="meta" id="pCount"></span></div>
      <div class="pb"><input type="search" id="pQ" placeholder="Search player…" style="width:100%"><div class="bar" style="margin-top:6px">
      <select id="pPos"><option value="">All roles</option>${["QB", "RB", "WR", "TE", "OL", "EDGE", "DT", "LB", "CB", "S", "K", "P"].map(p => `<option>${p}</option>`).join("")}</select>
      <select id="pTeam"><option value="">All teams</option>${meta.teams.map(t => `<option>${t}</option>`).join("")}</select>
      <select id="pSeason">${meta.seasons.slice().reverse().map(s => `<option ${s === state.pseason ? "selected" : ""}>${s}</option>`).join("")}</select></div></div>
      <div class="list" id="pList"></div></section><div id="pBody"><div class="loading">Select a player</div></div></div>`;
    const load = async () => {
      const r = await api(`/api/q/players?q=${encodeURIComponent($("#pQ").value)}&position=${$("#pPos").value}&team=${$("#pTeam").value}&season=${state.pseason}&limit=150`);
      $("#pCount").textContent = `${r.length} shown`;
      $("#pList").innerHTML = r.map(p => `<div class="li ${p.player_id === state.player ? "sel" : ""}" data-id="${esc(p.player_id)}"><span>${esc(p.name)}</span><span class="r">${esc(p.role)} · ${esc(p.team)} · ${p.games}g</span></div>`).join("") || `<div class="empty">No players</div>`;
      if (!state.player && r[0]) { state.player = r[0].player_id; playerBody(); }
    };
    let t; $("#pQ").oninput = () => { clearTimeout(t); t = setTimeout(load, 250); };
    $("#pPos").onchange = load; $("#pTeam").onchange = load;
    $("#pSeason").onchange = e => { state.pseason = +e.target.value; load(); if (state.player) playerBody(); };
    $("#pList").onclick = e => { const li = e.target.closest(".li"); if (!li) return; state.player = li.dataset.id; document.querySelectorAll(".li").forEach(x => x.classList.toggle("sel", x === li)); playerBody(); };
    await load(); if (state.player) playerBody();
  }
  async function playerBody() {
    const b = $("#pBody"); b.innerHTML = `<div class="loading">Loading profile…</div>`;
    let p;
    try { p = await api(`/api/q/player/profile?player=${state.player}&season=${state.pseason}&window=${state.pwindow}`); }
    catch (e) { b.innerHTML = `<div class="err">${esc(e.message)}</div>`; return; }
    const id = p.identity;
    const groups = [...new Set(p.metrics.map(m => m.group))].sort();
    const proj = p.projection;
    b.innerHTML = `<section class="panel"><div class="hero">${id.headshot ? `<img src="${esc(id.headshot)}" alt="">` : ""}<div><h2>${esc(p.name)}</h2>
      <div class="sub">${esc(p.position)} (${esc(p.role)}) · ${esc(p.team)} · ${id.height_in ? Math.floor(id.height_in / 12) + "'" + (id.height_in % 12) + '"' : "—"} ${id.weight_lb ?? "—"} lb · age ${num(id.age, 1)} · ${esc(id.college || "")} · draft ${id.draft_year ? id.draft_year + " R" + id.draft_round + " #" + id.draft_pick : "undrafted/—"} · ${id.career_seasons} seasons / ${id.career_games} games</div>
      <div class="bar" style="margin:6px 0 0"><label>Window</label><select id="pWin">${["season", "last3", "last5", "last10", "career"].map(w => `<option ${w === state.pwindow ? "selected" : ""}>${w}</option>`).join("")}</select>
      <span class="small">Reliability: <b>${esc(p.data_reliability.grade)}</b> · ${p.data_reliability.metrics_observed}/${p.data_reliability.metrics_applicable} metrics observed · ${p.games} games in window</span></div></div></div></section>
      <div class="grid g-main" style="margin-top:10px"><div class="grid">
        ${panel("Position-specific metric explorer", `<div class="tabs" id="pG"><button class="on" data-g="">All</button>${groups.map(g => `<button data-g="${g}">${g} · ${esc(p.metrics.find(m => m.group === g).group_label)}</button>`).join("")}</div>
          <div class="scroll"><table id="pM"><thead><tr><th>Metric</th><th class="n">Value</th><th>Pctl (${esc(p.role)})</th><th class="n">Sample</th><th>Source</th></tr></thead><tbody>
          ${p.metrics.map(m => `<tr data-g="${m.group}"><td title="${esc(m.formula)}">${esc(m.label)}</td><td class="n" data-v="${m.value ?? ""}">${m.status === "observed" ? fmtVal(m.value, m.unit) : `<span class="tag na">${m.status === "not_covered" ? "not covered " + esc(m.coverage) : "no sample"}</span>`}</td><td data-v="${m.percentile ?? ""}">${pctbar(m.percentile)}</td><td class="n" data-v="${m.sample ?? 0}">${m.sample == null ? "—" : Number(m.sample).toFixed(0)}</td><td><span class="tag">${esc(m.source)}</span></td></tr>`).join("")}
          </tbody></table></div>`, "hover a metric for its formula · percentile vs same-role peers this season")}
        ${panel("Performance history", `<div class="grid g3" id="pTrends"></div>`, "per-game values")}
        ${panel("Game log", gameLog(p.box), "box statistics · nflverse")}
      </div><div class="grid">
        ${panel("Next-game projection", proj ? projBox(proj) : `<p class="note">No unplayed game with a pregame projection for this player (not in the team's recent usage pool, ruled out, or season complete).</p>`, `<b class="t-pre">PREDICTED</b>`)}
        ${panel("Universal framework (A–J)", `<div class="scroll s420"><table><tbody>${p.universal.map(u => `<tr><td title="${esc(u.formula)}"><span class="tag">${u.group}</span> ${esc(u.label)}</td><td class="n">${fmtVal(u.value, u.unit)}</td></tr>`).join("")}</tbody></table></div>`)}
        ${Object.keys(p.ngs).length ? panel("NGS season aggregates", Object.entries(p.ngs).map(([k, v]) => `<div class="small" style="margin-top:4px">${esc(k)}</div><table><tbody>${Object.entries(v).filter(([kk]) => !["season", "week", "season_type", "player_jersey_number"].includes(kk)).slice(0, 18).map(([kk, vv]) => `<tr><td>${esc(kk)}</td><td class="n">${typeof vv === "number" ? num(vv, 2) : esc(vv)}</td></tr>`).join("")}</tbody></table>`).join(""), "OBSERVED") : ""}
        ${panel("Unavailable for this role", p.unavailable.length ? `<ul class="small">${p.unavailable.map(u => `<li><b>${esc(u.label)}</b> — ${esc(u.reason)}</li>`).join("")}</ul>` : `<p class="note">All specified metrics for this role are either implemented or listed in the catalog.</p>`, "schema preserved")}
      </div></div>`;
    $("#pWin").onchange = e => { state.pwindow = e.target.value; playerBody(); };
    $("#pG").onclick = e => { const bt = e.target.closest("button"); if (!bt) return; $("#pG").querySelectorAll("button").forEach(x => x.classList.toggle("on", x === bt));
      $("#pM").querySelectorAll("tbody tr").forEach(tr => tr.hidden = bt.dataset.g && tr.dataset.g !== bt.dataset.g); };
    sortable("#pM");
    const tg = $("#pTrends");
    Object.values(p.trend).forEach(t => { const d = document.createElement("div"); d.innerHTML = `<div class="small">${esc(t.label)}</div>`;
      d.appendChild(Charts.line([{ points: t.points.map(x => ({ x: x.week, y: x.value })), color: C.blue, dots: true }], { w: 300, h: 130, zero: true, dec: 2 })); tg.appendChild(d); });
  }
  function gameLog(box) {
    if (!box.games.length) return `<p class="note">No games in this window.</p>`;
    const cols = Object.keys(box.totals).filter(k => box.totals[k]);
    return `<div class="scroll s300"><table><thead><tr><th>Wk</th><th>Opp</th>${cols.map(c => `<th class="n">${esc(c.replace("passing_", "pass_").replace("receiving_", "rec_").replace("rushing_", "rush_").replace("def_", ""))}</th>`).join("")}</tr></thead><tbody>
      ${box.games.map(g => `<tr><td>${g.week}</td><td>${esc(g.opponent)}</td>${cols.map(c => `<td class="n">${g[c] == null ? "" : num(g[c], Number.isInteger(g[c]) ? 0 : 1)}</td>`).join("")}</tr>`).join("")}
      <tr><td colspan="2"><b>Total</b></td>${cols.map(c => `<td class="n"><b>${num(box.totals[c], 0)}</b></td>`).join("")}</tr></tbody></table></div>`;
  }
  function projBox(pr) {
    return `<div class="small">${esc(pr.matchup)} · ${esc(pr.kickoff || "")}</div><table><thead><tr><th>Stat</th><th class="n">Mean</th><th class="n">p10</th><th class="n">p50</th><th class="n">p90</th><th class="n">P≥1</th></tr></thead><tbody>
      ${Object.entries(pr.stats).filter(([, s]) => s.mean > 0.05).map(([k, s]) => `<tr><td>${esc(k)}</td><td class="n">${num(s.mean, 2)}</td><td class="n">${num(s.q10, 1)}</td><td class="n">${num(s.q50, 1)}</td><td class="n">${num(s.q90, 1)}</td><td class="n">${s.p_ge1 == null ? "—" : pct(s.p_ge1, 0)}</td></tr>`).join("")}</tbody></table>`;
  }

  // ------------------------------------------------------------- Model Audit
  async function wsAudit() {
    const a = await api("/api/q/audit");
    const g = a.game, pl = a.player, mk = g?.market_benchmark;
    const ladder = g ? Object.entries(g.models) : [];
    main.innerHTML = `
      ${g ? `<section class="panel"><div class="ph"><h3>Game model · chronological walk-forward ${g.seasons[0]}–${g.seasons[1]}</h3><span class="meta">${g.n_games} pregame predictions</span></div>
      <div class="kpis"><div class="kpi"><div class="k">Win log loss</div><div class="v">${num(g.models.ensemble.log_loss, 4)}</div><div class="s">Elo ${num(g.models.elo.log_loss, 4)} · HFA ${num(g.models.hfa.log_loss, 4)}</div></div>
      <div class="kpi"><div class="k">Brier</div><div class="v">${num(g.models.ensemble.brier, 4)}</div><div class="s">ECE ${pct(g.models.ensemble.ece, 2)}</div></div>
      <div class="kpi"><div class="k">Margin MAE</div><div class="v">${num(g.models.ensemble.margin_mae, 2)}</div><div class="s">RMSE ${num(g.models.ensemble.margin_rmse, 2)}</div></div>
      <div class="kpi"><div class="k">Score MAE (home/away)</div><div class="v">${num(g.models.ensemble.home_points_mae, 2)}</div><div class="s">${num(g.models.ensemble.away_points_mae, 2)}</div></div>
      <div class="kpi"><div class="k">80% PI coverage</div><div class="v">${pct(g.models.ensemble.margin_80pi_coverage)}</div><div class="s">total ${pct(g.models.ensemble.total_80pi_coverage)} · 50%: ${pct(g.models.ensemble.margin_50pi_coverage)}</div></div>
      <div class="kpi"><div class="k">Straight-up accuracy</div><div class="v">${pct(g.models.ensemble.accuracy)}</div><div class="s">p&gt;0.5</div></div></div></section>
      <div class="grid g3" style="margin-top:10px">
        ${panel("Model ladder (simpler models are benchmarks)", `<table><thead><tr><th>Model</th><th class="n">Margin MAE</th><th class="n">RMSE</th><th class="n">Total MAE</th><th class="n">Log loss</th></tr></thead><tbody>${ladder.map(([k, v]) => `<tr><td>${esc(k)}</td><td class="n">${num(v.margin_mae, 3)}</td><td class="n">${num(v.margin_rmse, 3)}</td><td class="n">${num(v.total_mae, 3)}</td><td class="n">${v.log_loss != null ? num(v.log_loss, 4) : "—"}</td></tr>`).join("")}</tbody></table>
          <p class="note">Raw (uncalibrated) ensemble log loss ${num(g.models.ensemble.log_loss_raw, 4)} vs Platt ${num(g.models.ensemble.log_loss, 4)}.</p>`)}
        ${panel("Calibration — predicted vs observed home win", `<div id="rel"></div>`, "10 bins")}
        ${mk ? panel("Closing-market benchmark (not an input)", `<table><thead><tr><th>Metric</th><th class="n">Model</th><th class="n">Market</th></tr></thead><tbody>
          <tr><td>Margin MAE</td><td class="n">${num(mk.margin_mae_model, 3)}</td><td class="n">${num(mk.margin_mae_market, 3)}</td></tr>
          <tr><td>Total MAE</td><td class="n">${num(mk.total_mae_model, 3)}</td><td class="n">${num(mk.total_mae_market, 3)}</td></tr>
          <tr><td>Win log loss</td><td class="n">${num(mk.log_loss_model, 4)}</td><td class="n">${num(mk.log_loss_market, 4)}</td></tr>
          <tr><td>Brier</td><td class="n">${num(mk.brier_model, 4)}</td><td class="n">${num(mk.brier_market, 4)}</td></tr></tbody></table><p class="note">${esc(mk.note)} The market is better on every row; the model is an independent, market-free forecast.</p>`) : ""}
      </div>
      ${panel("Performance by segment", `<div class="scroll s420"><table id="segT"><thead><tr><th>Segment</th><th class="n">n</th><th class="n">Log loss</th><th class="n">Brier</th><th class="n">Margin MAE</th><th class="n">Total MAE</th></tr></thead><tbody>${g.segments.map(s => `<tr><td>${esc(s.segment)}</td><td class="n">${s.n}</td><td class="n">${num(s.log_loss, 4)}</td><td class="n">${num(s.brier, 4)}</td><td class="n">${num(s.margin_mae, 2)}</td><td class="n">${num(s.total_mae, 2)}</td></tr>`).join("")}</tbody></table></div>`)}` : `<div class="err">Game validation not built.</div>`}
      ${pl ? panel("Player stat models · walk-forward " + Object.values(pl.targets)[0].seasons.join("–"), `<div class="scroll"><table id="plT"><thead><tr><th>Target</th><th class="n">n</th><th class="n">MAE</th><th class="n">Baseline MAE</th><th class="n">Skill</th><th class="n">Bias</th><th class="n">80% cov</th><th class="n">50% cov</th><th class="n">P≥1 Brier</th><th class="n">P≥1 ECE</th><th class="n">Base rate</th></tr></thead><tbody>
        ${Object.entries(pl.targets).map(([k, v]) => `<tr data-k="${esc(k)}"><td>${esc(k)}</td><td class="n">${v.n}</td><td class="n">${num(v.mae, 3)}</td><td class="n">${num(v.baseline_mae, 3)}</td><td class="n ${cls(v.skill_vs_baseline)}">${pct(v.skill_vs_baseline)}</td><td class="n">${sgn(v.bias, 3)}</td><td class="n">${pct(v.coverage_80)}</td><td class="n">${pct(v.coverage_50)}</td><td class="n">${v.p_ge1_brier != null ? num(v.p_ge1_brier, 4) : "—"}</td><td class="n">${v.p_ge1_ece != null ? pct(v.p_ge1_ece, 2) : "—"}</td><td class="n">${v.p_ge1_base_rate != null ? pct(v.p_ge1_base_rate) : "—"}</td></tr>`).join("")}
        </tbody></table></div><p class="note">Baseline = player's EWMA (half-life 4 games). Skill = 1 − MAE/baseline MAE. Projected players who did not play are scored as 0. Click a count target to see its P(≥1) reliability.</p><div id="plRel"></div>`) : ""}
      ${a.sim ? panel("Simulation backtest", simAudit(a.sim)) : panel("Simulation backtest", `<p class="note">Not yet run.</p>`)}
      ${panel("Model versions", `<div class="scroll s300"><table><thead><tr><th>Version</th><th>Model</th><th>Created</th><th>Training</th><th>Notes</th></tr></thead><tbody>${a.versions.map(v => `<tr><td class="mono">${esc(v.model_version)}</td><td>${esc(v.model_id)}</td><td>${esc(v.created_at)}</td><td>${esc(v.train_seasons)}</td><td class="small">${esc(v.notes)}</td></tr>`).join("")}</tbody></table></div>`)}`;
    if (g) mount(Charts.line([{ points: g.reliability.map(r => ({ x: r.mean_pred, y: r.observed, r: 2 + Math.sqrt(r.n) / 5 })), color: C.blue, dots: true }], { w: 320, h: 220, ymin: 0, ymax: 1, diag: true, xdec: 1, dec: 1 }), "#rel");
    sortable("#segT"); sortable("#plT");
    const plT = $("#plT"); if (plT) plT.addEventListener("click", e => { const tr = e.target.closest("tr[data-k]"); if (!tr) return; const v = pl.targets[tr.dataset.k];
      if (!v.p_ge1_reliability) return; const box = $("#plRel"); box.innerHTML = `<div class="small">${esc(tr.dataset.k)}: P(≥1) predicted vs observed</div>`;
      box.appendChild(Charts.line([{ points: v.p_ge1_reliability.map(r => ({ x: r.mean_pred, y: r.observed })), color: C.grn, dots: true }], { w: 360, h: 200, ymin: 0, ymax: 1, diag: true, xdec: 1, dec: 1 })); });
  }
  function simAudit(s) {
    return `<div class="kpis"><div class="kpi"><div class="k">Games</div><div class="v">${s.n_games}</div><div class="s">${esc(s.scope)}</div></div>
      <div class="kpi"><div class="k">Sim win log loss</div><div class="v">${num(s.log_loss_sim, 4)}</div><div class="s">game model ${num(s.log_loss_model, 4)}</div></div>
      <div class="kpi"><div class="k">Sim margin σ</div><div class="v">${num(s.mean_sim_margin_sd, 2)}</div><div class="s">empirical residual σ ${num(s.empirical_margin_resid_sd, 2)}</div></div>
      <div class="kpi"><div class="k">Margin 80% PI coverage</div><div class="v">${pct(s.margin_cov80)}</div><div class="s">total ${pct(s.total_cov80)}</div></div>
      <div class="kpi"><div class="k">Accounting identities</div><div class="v ${s.all_identities_hold ? "pos" : "neg"}">${s.all_identities_hold ? "PASS" : "FAIL"}</div><div class="s">every simulation</div></div></div>
      <table style="margin-top:8px"><thead><tr><th>Player stat (sim)</th><th class="n">n</th><th class="n">MAE sim</th><th class="n">MAE ML</th><th class="n">80% cov</th></tr></thead><tbody>${Object.entries(s.player || {}).map(([k, v]) => `<tr><td>${esc(k)}</td><td class="n">${v.n}</td><td class="n">${num(v.mae_sim, 3)}</td><td class="n">${num(v.mae_ml, 3)}</td><td class="n">${pct(v.cov80)}</td></tr>`).join("")}</tbody></table>
      <p class="note">${esc(s.note || "")}</p>`;
  }

  // ------------------------------------------------------------- Data & Sources
  async function wsData() {
    const s = await api("/api/q/status");
    const lake = s.lake.plays || {};
    main.innerHTML = `<div class="grid g2">
      ${panel("Source releases", `<table><thead><tr><th>Release</th><th class="n">Assets</th><th class="n">Missing</th><th>Seasons</th><th class="n">MB</th><th>License</th><th>Retrieved</th></tr></thead><tbody>${Object.entries(s.sources).map(([k, v]) => `<tr><td>${esc(k)}</td><td class="n">${v.assets}</td><td class="n">${v.missing}</td><td>${v.seasons ? v.seasons.join("–") : "—"}</td><td class="n">${(v.bytes / 1e6).toFixed(1)}</td><td class="small">${esc(v.license)}</td><td class="small">${esc(v.retrieved_at)}</td></tr>`).join("")}</tbody></table>
        <p class="note">Missing upstream: ${s.missing_assets.map(esc).join(", ") || "none"}. Every asset is SHA-256 hashed with HTTP validators in data/raw/manifest.json; revisions are kept.</p>`, "OBSERVED")}
      ${panel("Play-level coverage by season", `<div class="scroll s420"><table><thead><tr><th>Season</th><th class="n">Plays</th><th class="n">Games</th><th class="n">Participation</th><th class="n">FTN</th></tr></thead><tbody>${Object.entries(lake).reverse().map(([k, v]) => `<tr><td>${k}</td><td class="n">${v.rows}</td><td class="n">${v.games}</td><td class="n">${v.participation_rows ? pct(v.participation_rows / v.rows, 0) : `<span class="tag na">none</span>`}</td><td class="n">${v.ftn_rows ? pct(v.ftn_rows / v.rows, 0) : `<span class="tag na">none</span>`}</td></tr>`).join("")}</tbody></table></div>`)}
      ${panel("Warehouse entities", `<table><thead><tr><th>Entity</th><th>Status</th><th>Note</th></tr></thead><tbody>${s.entities.map(e => `<tr><td>${esc(e.entity)}</td><td><span class="tag ${e.status === "reserved" ? "na" : ""}">${esc(e.status)}</span></td><td class="small">${esc(e.note)}</td></tr>`).join("")}</tbody></table>`)}
      ${panel("Production artifacts", s.production ? `<table><tbody>${Object.entries(s.production).filter(([k]) => typeof s.production[k] !== "object").map(([k, v]) => `<tr><td>${esc(k)}</td><td class="mono small">${esc(v)}</td></tr>`).join("")}</tbody></table>` : `<p class="note">Not built.</p>`)}
      ${panel("Data tiers", `<table><tbody>${Object.entries(s.tiers).map(([k, v]) => `<tr><td><b class="t-${k.slice(0, 3).toLowerCase()}">${k}</b></td><td>${esc(v)}</td></tr>`).join("")}</tbody></table><p class="note">${esc(s.runtime)}.</p>`)}
    </div>`;
  }

  api("/api/q/status").then(s => { $("#cutoff").textContent = s.production ? "DATA THROUGH " + String(s.production.trained_through).slice(0, 10) : "ARTIFACTS PENDING"; }).catch(() => {});
  render();
})();
