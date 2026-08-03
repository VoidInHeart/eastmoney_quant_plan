(() => {
  const data = window.QUANT_DASHBOARD_DATA || {};
  const storageKey = "eastmoney-quant-initial-build";
  const totalAssets = 200000;
  const boardLot = 100;
  const minimumCommission = 5;
  const commissionRate = 0.0003;
  const openPremium = 0.003;
  const maxGap = 0.03;
  const $ = (id) => document.getElementById(id);
  const candidates = (data.prescreen || []).map((row) => ({
    ...row,
    code: String(row.code || "").padStart(6, "0"),
    rank: Number(row.rank),
    previousClose: Number(row.previous_close ?? row.reference_close ?? row.close),
    targetHoldings: Number(row.target_holdings || 5),
  })).sort((left, right) => left.rank - right.rank);
  const liveOpens = Object.fromEntries((data.live_open_prices || []).map((row) => [
    String(row.code || "").padStart(6, "0"), Number(row.open_price),
  ]));
  const liveLastPrices = Object.fromEntries((data.live_open_prices || []).map((row) => [
    String(row.code || "").padStart(6, "0"), Number(row.last_price),
  ]));
  const dashboardOrders = (data.buy_recommendations || []).map((row) => ({
    signalDate: row.signal_date,
    executionDate: row.execution_date,
    rank: Number(row.rank),
    code: String(row.code || "").padStart(6, "0"),
    name: row.name,
    previousClose: Number(row.previous_close),
    openPrice: Number(row.open_price),
    openingGap: Number(row.opening_gap),
    limitPrice: Number(row.buy_limit_price),
    quantity: Number(row.quantity),
    targetBudget: Number(row.target_budget),
    estimatedNotional: Number(row.estimated_notional),
    estimatedCommission: Number(row.estimated_commission),
    totalCost: Number(row.estimated_total_cost),
  }));

  const escapeHtml = (value) => String(value ?? "").replace(/[&<>'"]/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", "\"": "&quot;",
  }[character]));
  const num = (value, digits = 2) => Number.isFinite(Number(value)) ? Number(value).toFixed(digits) : "--";
  const money = (value) => Number.isFinite(Number(value)) ? `¥${Number(value).toFixed(2)}` : "--";
  const pct = (value) => Number.isFinite(Number(value)) ? `${(Number(value) * 100).toFixed(2)}%` : "--";
  const floorTick = (value) => Math.floor((value + 1e-10) * 100) / 100;
  const clamp = (value, lower, upper) => Math.max(lower, Math.min(upper, value));

  function loadState() {
    try {
      return JSON.parse(localStorage.getItem(storageKey)) || { openPrices: {}, currentPrices: {}, orders: dashboardOrders };
    } catch (_) {
      return { openPrices: {}, currentPrices: {}, orders: dashboardOrders };
    }
  }

  function saveState(nextState) {
    localStorage.setItem(storageKey, JSON.stringify(nextState));
  }

  function downloadCsv(rows, filename) {
    if (!rows.length) return;
    const columns = Object.keys(rows[0]);
    const quote = (value) => `"${String(value ?? "").replaceAll("\"", "\"\"")}"`;
    const text = [columns.join(","), ...rows.map((row) => columns.map((column) => quote(row[column])).join(","))].join("\n");
    const anchor = document.createElement("a");
    anchor.href = URL.createObjectURL(new Blob(["\uFEFF", text], { type: "text/csv;charset=utf-8" }));
    anchor.download = filename;
    anchor.click();
    URL.revokeObjectURL(anchor.href);
  }

  function prescreenEmptyRow(columnCount) {
    return `<tr><td colspan="${columnCount}">请先在收盘后运行更新和预测，生成 daily_prescreen.csv。</td></tr>`;
  }

  function renderPrescreen() {
    $("workflow-generated-at").textContent = data.generated_at || "--";
    if (!candidates.length) {
      $("prescreen-body").innerHTML = prescreenEmptyRow(6);
      $("prescreen-meta").textContent = "暂无数据";
      return;
    }
    const first = candidates[0];
    $("prescreen-meta").textContent = `${candidates.length} 只候选 · ${first.market_regime || "--"} · 目标 ${first.targetHoldings} 只`;
    $("prescreen-body").innerHTML = candidates.map((row) =>
      `<tr><td>${row.rank}</td><td>${escapeHtml(row.code)}</td><td>${escapeHtml(row.name)}</td><td>${num(row.score, 3)}</td><td>${num(row.previousClose)}</td><td>${pct(row.trend_120)}</td></tr>`
    ).join("");
  }

  function renderOpeningInputs() {
    if (!candidates.length) {
      $("opening-input-body").innerHTML = prescreenEmptyRow(5);
      return;
    }
    const state = loadState();
    $("opening-input-body").innerHTML = candidates.map((row) => {
      const saved = state.openPrices[row.code] ?? liveOpens[row.code];
      return `<tr><td>${row.rank}</td><td>${escapeHtml(row.code)}</td><td>${escapeHtml(row.name)}</td><td>${num(row.previousClose)}</td><td><input class="open-price-input" data-code="${escapeHtml(row.code)}" type="number" min="0.01" step="0.01" value="${saved ?? ""}" aria-label="${escapeHtml(row.code)} 开盘价" /></td></tr>`;
    }).join("");
    document.querySelectorAll(".open-price-input").forEach((input) => input.addEventListener("input", () => {
      const nextState = loadState();
      nextState.openPrices[input.dataset.code] = input.value;
      saveState(nextState);
    }));
  }

  function readOpeningPrices() {
    const values = {};
    document.querySelectorAll(".open-price-input").forEach((input) => { values[input.dataset.code] = Number(input.value); });
    const missing = candidates.filter((row) => !Number.isFinite(values[row.code]) || values[row.code] <= 0).map((row) => row.code);
    return { values, missing };
  }

  function renderOrders(orders) {
    $("order-result-body").innerHTML = orders.length ? orders.map((row) =>
      `<tr><td>${escapeHtml(row.code)}</td><td>${escapeHtml(row.name)}</td><td>${num(row.openPrice)}</td><td>${num(row.limitPrice)}</td><td>${num(row.quantity, 0)}</td><td>${money(row.totalCost)}</td></tr>`
    ).join("") : `<tr><td colspan="6">录入开盘价后生成订单。</td></tr>`;
  }

  function showOrderStatus(message, kind = "") {
    const element = $("order-status");
    element.textContent = message;
    element.className = `route-status ${kind}`;
  }

  function generateOrders() {
    if (!candidates.length) {
      showOrderStatus("没有预筛选数据。", "error");
      return;
    }
    const { values, missing } = readOpeningPrices();
    if (missing.length) {
      showOrderStatus(`请补齐全部 ${candidates.length} 只候选的开盘价；缺少：${missing.join("、")}`, "error");
      return;
    }
    const target = clamp(candidates[0].targetHoldings || 5, 5, 10);
    const safe = candidates
      .map((row) => ({ ...row, openPrice: values[row.code], openingGap: values[row.code] / row.previousClose - 1 }))
      .filter((row) => row.openingGap <= maxGap)
      .slice(0, target);
    if (safe.length < 5) {
      showOrderStatus(`仅 ${safe.length} 只股票开盘涨幅不超过 3%，为避免追高，未生成订单。`, "error");
      renderOrders([]);
      return;
    }
    const budget = totalAssets / safe.length;
    const orders = safe.map((row) => {
      const limitPrice = Math.min(floorTick(row.openPrice * (1 + openPremium)), floorTick(row.previousClose * (1 + maxGap)));
      let quantity = Math.floor((budget - minimumCommission) / (limitPrice * boardLot)) * boardLot;
      while (quantity > 0) {
        const notional = quantity * limitPrice;
        const commission = Math.max(notional * commissionRate, minimumCommission);
        if (notional + commission <= budget + 1e-8) break;
        quantity -= boardLot;
      }
      const notional = quantity * limitPrice;
      const commission = Math.max(notional * commissionRate, minimumCommission);
      return {
        signalDate: row.signal_date,
        executionDate: row.execution_date,
        rank: row.rank,
        code: row.code,
        name: row.name,
        previousClose: floorTick(row.previousClose),
        openPrice: floorTick(row.openPrice),
        openingGap: row.openingGap,
        limitPrice,
        quantity,
        targetBudget: budget,
        estimatedNotional: notional,
        estimatedCommission: commission,
        totalCost: notional + commission,
      };
    }).filter((row) => row.quantity > 0);
    if (orders.length < 5) {
      showOrderStatus("资金不足以满足至少 5 只股票的整手建仓，未生成订单。", "error");
      renderOrders([]);
      return;
    }
    const state = loadState();
    state.openPrices = Object.fromEntries(Object.entries(values).map(([code, price]) => [code, String(price)]));
    state.orders = orders;
    state.generatedAt = new Date().toLocaleString("zh-CN");
    saveState(state);
    renderOrders(orders);
    $("download-orders").disabled = false;
    showOrderStatus(`已选出 ${orders.length} 只股票。每只按 ${money(budget)} 上限、100 股整手计算；${candidates.length - safe.length} 只因高开超过 3% 被跳过。`, "success");
    renderHoldings();
  }

  function renderHoldings() {
    const state = loadState();
    const orders = state.orders || [];
    if (!orders.length) {
      $("holdings-body").innerHTML = `<tr><td colspan="7">请先在“填开盘价”页生成订单。</td></tr>`;
      $("pnl-summary").innerHTML = "";
      return;
    }
    $("holdings-body").innerHTML = orders.map((row) => {
      const current = state.currentPrices?.[row.code] ?? liveLastPrices[row.code] ?? "";
      const marketValue = Number(current) > 0 ? row.quantity * Number(current) : null;
      const pnl = marketValue == null ? null : marketValue - row.totalCost;
      return `<tr><td>${escapeHtml(row.code)}</td><td>${escapeHtml(row.name)}</td><td>${num(row.quantity, 0)}</td><td>${money(row.totalCost)}</td><td><input class="current-price-input" data-code="${escapeHtml(row.code)}" type="number" min="0.01" step="0.01" value="${current}" aria-label="${escapeHtml(row.code)} 当前价" /></td><td>${money(marketValue)}</td><td class="${Number(pnl) >= 0 ? "positive" : "negative"}">${money(pnl)}</td></tr>`;
    }).join("");
    document.querySelectorAll(".current-price-input").forEach((input) => input.addEventListener("input", () => {
      const nextState = loadState();
      nextState.currentPrices = nextState.currentPrices || {};
      nextState.currentPrices[input.dataset.code] = input.value;
      saveState(nextState);
    }));
    updatePnlSummary();
  }

  function updatePnlSummary() {
    const state = loadState();
    const orders = state.orders || [];
    const prices = state.currentPrices || {};
    const priceFor = (row) => prices[row.code] ?? liveLastPrices[row.code];
    const priced = orders.filter((row) => Number(priceFor(row)) > 0);
    if (!priced.length) {
      $("pnl-summary").innerHTML = `已生成 ${orders.length} 笔订单；请填入当前价。`;
      return;
    }
    const marketValue = priced.reduce((sum, row) => sum + row.quantity * Number(priceFor(row)), 0);
    const cost = priced.reduce((sum, row) => sum + row.totalCost, 0);
    const pnl = marketValue - cost;
    $("pnl-summary").innerHTML = `已更新 ${priced.length}/${orders.length} 只 · 成本<strong>${money(cost)}</strong> · 市值<strong>${money(marketValue)}</strong> · 浮盈亏<strong class="${pnl >= 0 ? "positive" : "negative"}">${money(pnl)}</strong>`;
  }

  function updatePnl() {
    const state = loadState();
    state.currentPrices = state.currentPrices || {};
    document.querySelectorAll(".current-price-input").forEach((input) => { state.currentPrices[input.dataset.code] = input.value; });
    saveState(state);
    renderHoldings();
  }

  function route() {
    const current = ["prescreen", "opening-prices", "holdings"].includes(location.hash.slice(1)) ? location.hash.slice(1) : "prescreen";
    document.querySelectorAll("[data-route-page]").forEach((page) => { page.hidden = page.dataset.routePage !== current; });
    document.querySelectorAll("[data-route-link]").forEach((link) => link.classList.toggle("active", link.dataset.routeLink === current));
    if (current === "opening-prices") renderOpeningInputs();
    if (current === "holdings") renderHoldings();
  }

  $("generate-orders").addEventListener("click", generateOrders);
  $("download-open-prices").addEventListener("click", () => {
    const { values } = readOpeningPrices();
    downloadCsv(candidates.map((row) => ({ code: row.code, open_price: values[row.code] || "" })), "opening_prices.csv");
  });
  $("download-orders").addEventListener("click", () => downloadCsv(loadState().orders || [], "buy_recommendations.csv"));
  $("update-pnl").addEventListener("click", updatePnl);
  window.addEventListener("hashchange", route);
  renderPrescreen();
  renderOrders(loadState().orders || []);
  if (loadState().orders?.length) $("download-orders").disabled = false;
  route();
})();
