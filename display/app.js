(() => {
  const data = window.QUANT_DASHBOARD_DATA || {};
  const perf = data.performance || {};
  const $ = (id) => document.getElementById(id);
  const pct = (value, digits = 2) => Number.isFinite(Number(value)) ? `${(Number(value) * 100).toFixed(digits)}%` : "--";
  const num = (value, digits = 2) => Number.isFinite(Number(value)) ? Number(value).toFixed(digits) : "--";
  const tone = (value) => Number(value) >= 0 ? "positive" : "negative";

  $("generated-at").textContent = data.generated_at || "请先运行回测";
  const metrics = [
    ["累计收益", perf.cumulative_return, pct],
    ["年化收益", perf.annual_return, pct],
    ["相对基准", perf.relative_return, pct],
    ["最大回撤", perf.max_drawdown, pct],
    ["夏普比率", perf.sharpe, num],
    ["年化换手", perf.avg_turnover == null ? null : perf.avg_turnover * (perf.annualization_periods || 252), pct],
  ];
  $("metrics").innerHTML = metrics.map(([label, value, formatter]) =>
    `<article class="metric"><span>${label}</span><strong class="${tone(value)}">${formatter(value)}</strong></article>`
  ).join("");

  const portfolio = data.portfolio || [];
  $("portfolio-count").textContent = `${portfolio.length} 只`;
  $("portfolio-body").innerHTML = portfolio.length ? portfolio.map((row) =>
    `<tr><td>${row.rank ?? "--"}</td><td>${row.code ?? "--"}</td><td>${row.name ?? "--"}</td><td>${num(row.score, 3)}</td><td>${pct(row.weight)}</td><td>${num(row.close)}</td></tr>`
  ).join("") : `<tr><td colspan="6">暂无最新持仓；运行预测后会自动显示。</td></tr>`;

  const trades = (data.trades || []).slice(-10).reverse();
  $("trades-body").innerHTML = trades.length ? trades.map((row) =>
    `<tr><td>${String(row.execution_date || "--").slice(0, 10)}</td><td>${row.holdings ?? "--"}</td><td>${pct(row.turnover)}</td><td>${pct(row.cost, 3)}</td></tr>`
  ).join("") : `<tr><td colspan="4">暂无调仓记录。</td></tr>`;

  const empty = $("empty-state");
  if (!(data.nav || []).length) {
    empty.hidden = false;
    empty.textContent = "尚未生成日频回测结果。运行主流程或增量更新后，本页面会自动刷新。";
  }

  function drawChart() {
    const canvas = $("nav-chart");
    const ctx = canvas.getContext("2d");
    const width = canvas.clientWidth;
    const height = canvas.clientHeight;
    const ratio = window.devicePixelRatio || 1;
    canvas.width = width * ratio;
    canvas.height = height * ratio;
    ctx.scale(ratio, ratio);
    ctx.clearRect(0, 0, width, height);
    const nav = data.nav || [];
    const benchmark = data.benchmark || [];
    if (!nav.length) return;
    const strategy = nav.map((row) => Number(row.nav));
    const bench = benchmark.map((row) => Number(row.benchmark_nav));
    const values = strategy.concat(bench).filter(Number.isFinite);
    const min = Math.min(...values);
    const max = Math.max(...values);
    const pad = { left: 46, right: 16, top: 16, bottom: 28 };
    const plotW = width - pad.left - pad.right;
    const plotH = height - pad.top - pad.bottom;
    const y = (value) => pad.top + (max === min ? .5 : (max - value) / (max - min)) * plotH;
    const x = (index, count) => pad.left + (count <= 1 ? 0 : index / (count - 1)) * plotW;
    ctx.font = "12px Microsoft YaHei";
    ctx.fillStyle = "#9ba8c7";
    ctx.strokeStyle = "rgba(255,255,255,.08)";
    for (let i = 0; i < 4; i += 1) {
      const yy = pad.top + i * plotH / 3;
      const label = (max - (max - min) * i / 3).toFixed(2);
      ctx.beginPath(); ctx.moveTo(pad.left, yy); ctx.lineTo(width - pad.right, yy); ctx.stroke();
      ctx.fillText(label, 2, yy + 4);
    }
    const line = (series, color) => {
      if (!series.length) return;
      ctx.beginPath();
      series.forEach((value, index) => index ? ctx.lineTo(x(index, series.length), y(value)) : ctx.moveTo(x(index, series.length), y(value)));
      ctx.lineWidth = 2; ctx.strokeStyle = color; ctx.stroke();
    };
    line(bench, "#7aa7ff");
    line(strategy, "#62d8b4");
  }
  drawChart();
  window.addEventListener("resize", drawChart);
})();
