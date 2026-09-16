// SQD Live Dashboard — client-side simulated real-time data.
// Swap `nextTick()` for a fetch() against a real API or JSON file to track real data.

(() => {
  "use strict";

  const MAX_POINTS = 60;

  const css = getComputedStyle(document.documentElement);
  const color = (name) => css.getPropertyValue(name).trim();

  // Metric definitions: random-walk generators with soft mean reversion.
  const metrics = {
    cpu: {
      label: "CPU Load", unit: "%", decimals: 1, min: 0, max: 100,
      value: 42, target: 42, volatility: 6, colorVar: "--series-cpu",
      history: [], onChart: true,
    },
    memory: {
      label: "Memory Usage", unit: "%", decimals: 1, min: 0, max: 100,
      value: 58, target: 58, volatility: 4, colorVar: "--series-memory",
      history: [], onChart: true,
    },
    network: {
      label: "Network Throughput", unit: "Mb/s", decimals: 0, min: 0, max: 900,
      value: 220, target: 220, volatility: 60, colorVar: "--series-network",
      history: [], onChart: false,
    },
    requests: {
      label: "Requests / sec", unit: "req/s", decimals: 0, min: 0, max: 2000,
      value: 640, target: 640, volatility: 140, colorVar: "--series-requests",
      history: [], onChart: false,
    },
    users: {
      label: "Active Users", unit: "", decimals: 0, min: 0, max: 5000,
      value: 1200, target: 1200, volatility: 90, colorVar: "--series-users",
      history: [], onChart: false,
    },
  };

  // Error rate is a status metric (good/warning/serious/critical), not identity-colored.
  const errorRate = { value: 0.4, target: 0.4, volatility: 0.35, min: 0, max: 20, history: [] };

  const timeLabels = [];
  let tickCount = 0;
  let running = true;
  let timer = null;
  let intervalMs = 2000;

  function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }

  function step(state) {
    // mean-reverting random walk with an occasional target drift
    if (Math.random() < 0.05) {
      state.target = clamp(state.target + (Math.random() - 0.5) * state.volatility * 3, state.min, state.max);
    }
    const pull = (state.target - state.value) * 0.12;
    const noise = (Math.random() - 0.5) * state.volatility;
    state.value = clamp(state.value + pull + noise, state.min, state.max);
    return state.value;
  }

  function fmt(v, decimals) {
    return v.toLocaleString(undefined, { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
  }

  function pushHistory(arr, v) {
    arr.push(v);
    if (arr.length > MAX_POINTS) arr.shift();
  }

  // ---------- DOM: stat cards ----------
  const statsEl = document.getElementById("stats");
  const statOrder = ["cpu", "memory", "network", "requests", "users"];

  function buildStatCards() {
    statOrder.forEach((key) => {
      const m = metrics[key];
      const card = document.createElement("div");
      card.className = "stat-card";
      card.id = `stat-${key}`;
      card.innerHTML = `
        <div class="stat-label"><span class="stat-swatch" style="background:${color(m.colorVar)}"></span>${m.label}</div>
        <div class="stat-value-row">
          <span><span class="stat-value" data-value>${fmt(m.value, m.decimals)}</span><span class="stat-unit">${m.unit}</span></span>
          <span class="stat-delta flat" data-delta>—</span>
        </div>
        <canvas data-spark width="240" height="28"></canvas>
      `;
      statsEl.appendChild(card);
    });

    const card = document.createElement("div");
    card.className = "stat-card status-good";
    card.id = "stat-error";
    card.innerHTML = `
      <div class="stat-label"><span class="stat-swatch" style="background:${color("--status-good")}"></span>Error Rate</div>
      <div class="stat-value-row">
        <span><span class="stat-value" data-value>${fmt(errorRate.value, 2)}</span><span class="stat-unit">%</span></span>
        <span class="stat-delta flat" data-delta>—</span>
      </div>
      <canvas data-spark width="240" height="28"></canvas>
    `;
    statsEl.appendChild(card);
  }

  function statusForErrorRate(v) {
    if (v < 1) return { cls: "good", color: color("--status-good") };
    if (v < 3) return { cls: "warning", color: color("--status-warning") };
    if (v < 6) return { cls: "serious", color: color("--status-serious") };
    return { cls: "critical", color: color("--status-critical") };
  }

  function drawSparkline(canvas, history, lineColor) {
    const ctx = canvas.getContext("2d");
    const w = canvas.width, h = canvas.height;
    ctx.clearRect(0, 0, w, h);
    if (history.length < 2) return;
    const min = Math.min(...history), max = Math.max(...history);
    const range = max - min || 1;
    const stepX = w / (MAX_POINTS - 1);
    const offset = MAX_POINTS - history.length;
    ctx.beginPath();
    ctx.lineWidth = 2;
    ctx.strokeStyle = lineColor;
    ctx.lineJoin = "round";
    ctx.lineCap = "round";
    history.forEach((v, i) => {
      const x = (offset + i) * stepX;
      const y = h - 4 - ((v - min) / range) * (h - 8);
      if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    });
    ctx.stroke();
  }

  function updateStatCards() {
    statOrder.forEach((key) => {
      const m = metrics[key];
      const prev = m.history[m.history.length - 1];
      const card = document.getElementById(`stat-${key}`);
      card.querySelector("[data-value]").textContent = fmt(m.value, m.decimals);
      const deltaEl = card.querySelector("[data-delta]");
      if (prev !== undefined) {
        const diff = m.value - prev;
        if (Math.abs(diff) < (m.max - m.min) * 0.002) {
          deltaEl.textContent = "—"; deltaEl.className = "stat-delta flat";
        } else {
          deltaEl.textContent = (diff > 0 ? "▲ " : "▼ ") + fmt(Math.abs(diff), m.decimals);
          deltaEl.className = "stat-delta " + (diff > 0 ? "up" : "down");
        }
      }
      drawSparkline(card.querySelector("[data-spark]"), m.history, color(m.colorVar));
    });

    const card = document.getElementById("stat-error");
    const prev = errorRate.history[errorRate.history.length - 1];
    card.querySelector("[data-value]").textContent = fmt(errorRate.value, 2);
    const status = statusForErrorRate(errorRate.value);
    card.className = "stat-card status-" + status.cls;
    const deltaEl = card.querySelector("[data-delta]");
    if (prev !== undefined) {
      const diff = errorRate.value - prev;
      if (Math.abs(diff) < 0.02) {
        deltaEl.textContent = "—"; deltaEl.className = "stat-delta flat";
      } else {
        // for error rate, down is good
        deltaEl.textContent = (diff > 0 ? "▲ " : "▼ ") + fmt(Math.abs(diff), 2);
        deltaEl.className = "stat-delta " + (diff > 0 ? "down" : "up");
      }
    }
    drawSparkline(card.querySelector("[data-spark]"), errorRate.history, status.color);
  }

  // ---------- Main chart (CPU vs Memory, shared 0-100 axis) ----------
  const chartCanvas = document.getElementById("main-chart");
  const chartCtx = chartCanvas.getContext("2d");
  const tooltip = document.getElementById("chart-tooltip");
  const legendEl = document.getElementById("chart-legend");
  const tableBody = document.querySelector("#chart-table tbody");
  const tableWrap = document.getElementById("table-wrap");
  const tableToggleBtn = document.getElementById("table-toggle-btn");

  const chartSeries = [
    { key: "cpu", label: "CPU Load", unit: "%" },
    { key: "memory", label: "Memory Usage", unit: "%" },
  ];
  const visibility = { cpu: true, memory: true };
  let hoverIndex = null;
  let padding = { top: 16, right: 16, bottom: 24, left: 36 };

  function buildLegend() {
    legendEl.innerHTML = "";
    chartSeries.forEach((s) => {
      const li = document.createElement("li");
      li.style.cursor = "pointer";
      li.dataset.key = s.key;
      li.innerHTML = `<span class="swatch" style="background:${color(metrics[s.key].colorVar)}"></span>${s.label}`;
      li.addEventListener("click", () => {
        visibility[s.key] = !visibility[s.key];
        li.style.opacity = visibility[s.key] ? "1" : "0.4";
        renderChart();
      });
      legendEl.appendChild(li);
    });
  }

  function resizeCanvas() {
    const rect = chartCanvas.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    chartCanvas.width = Math.max(1, rect.width * dpr);
    chartCanvas.height = Math.max(1, rect.height * dpr);
    chartCtx.setTransform(dpr, 0, 0, dpr, 0, 0);
    renderChart();
  }

  function chartMetrics() {
    const rect = chartCanvas.getBoundingClientRect();
    return { w: rect.width, h: rect.height };
  }

  function renderChart() {
    const { w, h } = chartMetrics();
    if (w === 0 || h === 0) return;
    chartCtx.clearRect(0, 0, w, h);

    const plotW = w - padding.left - padding.right;
    const plotH = h - padding.top - padding.bottom;
    const n = MAX_POINTS;
    const stepX = plotW / (n - 1);
    const yMin = 0, yMax = 100;

    const gridColor = color("--gridline");
    const mutedColor = color("--text-muted");
    const baselineColor = color("--baseline");

    // gridlines + y labels
    chartCtx.font = "11px " + getComputedStyle(document.body).fontFamily;
    chartCtx.fillStyle = mutedColor;
    chartCtx.strokeStyle = gridColor;
    chartCtx.lineWidth = 1;
    const ticks = [0, 25, 50, 75, 100];
    ticks.forEach((t) => {
      const y = padding.top + plotH - (t / 100) * plotH;
      chartCtx.beginPath();
      chartCtx.moveTo(padding.left, y);
      chartCtx.lineTo(padding.left + plotW, y);
      chartCtx.stroke();
      chartCtx.fillText(t + "%", 2, y + 3);
    });

    // baseline
    chartCtx.strokeStyle = baselineColor;
    chartCtx.beginPath();
    chartCtx.moveTo(padding.left, padding.top + plotH);
    chartCtx.lineTo(padding.left + plotW, padding.top + plotH);
    chartCtx.stroke();

    function xyFor(history, i) {
      const offset = n - history.length;
      const x = padding.left + (offset + i) * stepX;
      const y = padding.top + plotH - ((history[i] - yMin) / (yMax - yMin)) * plotH;
      return [x, y];
    }

    chartSeries.forEach((s) => {
      if (!visibility[s.key]) return;
      const history = metrics[s.key].history;
      if (history.length < 2) return;
      chartCtx.beginPath();
      chartCtx.lineWidth = 2;
      chartCtx.lineJoin = "round";
      chartCtx.strokeStyle = color(metrics[s.key].colorVar);
      history.forEach((v, i) => {
        const [x, y] = xyFor(history, i);
        if (i === 0) chartCtx.moveTo(x, y); else chartCtx.lineTo(x, y);
      });
      chartCtx.stroke();

      // rounded data-end dot + direct label at the latest point
      const [lx, ly] = xyFor(history, history.length - 1);
      chartCtx.beginPath();
      chartCtx.fillStyle = color(metrics[s.key].colorVar);
      chartCtx.arc(lx, ly, 3, 0, Math.PI * 2);
      chartCtx.fill();
    });

    // hover crosshair
    if (hoverIndex !== null) {
      const history = metrics.cpu.history;
      const offset = n - history.length;
      const idx = hoverIndex - offset;
      if (idx >= 0 && idx < history.length) {
        const x = padding.left + hoverIndex * stepX;
        chartCtx.strokeStyle = mutedColor;
        chartCtx.setLineDash([3, 3]);
        chartCtx.lineWidth = 1;
        chartCtx.beginPath();
        chartCtx.moveTo(x, padding.top);
        chartCtx.lineTo(x, padding.top + plotH);
        chartCtx.stroke();
        chartCtx.setLineDash([]);
      }
    }
  }

  function updateTable() {
    const history = metrics.cpu.history;
    tableBody.innerHTML = "";
    const rows = [];
    for (let i = history.length - 1; i >= Math.max(0, history.length - 15); i--) {
      rows.push(`<tr><td>${timeLabels[timeLabels.length - (history.length - i)] ?? ""}</td><td>${fmt(metrics.cpu.history[i], 1)}</td><td>${fmt(metrics.memory.history[i], 1)}</td></tr>`);
    }
    tableBody.innerHTML = rows.join("");
  }

  chartCanvas.addEventListener("mousemove", (evt) => {
    const rect = chartCanvas.getBoundingClientRect();
    const x = evt.clientX - rect.left;
    const plotW = rect.width - padding.left - padding.right;
    const n = MAX_POINTS;
    const stepX = plotW / (n - 1);
    let idx = Math.round((x - padding.left) / stepX);
    idx = clamp(idx, 0, n - 1);
    hoverIndex = idx;

    const history = metrics.cpu.history;
    const offset = n - history.length;
    const dataIdx = idx - offset;
    if (dataIdx < 0 || dataIdx >= history.length) {
      tooltip.hidden = true;
      renderChart();
      return;
    }

    const timeIdx = timeLabels.length - (history.length - dataIdx);
    const label = timeLabels[timeIdx] ?? "";
    tooltip.hidden = false;
    tooltip.innerHTML = `
      <div class="t-time">${label}</div>
      <div class="t-row"><span class="t-dot" style="background:${color("--series-cpu")}"></span>CPU<span class="t-val">${fmt(metrics.cpu.history[dataIdx], 1)}%</span></div>
      <div class="t-row"><span class="t-dot" style="background:${color("--series-memory")}"></span>Memory<span class="t-val">${fmt(metrics.memory.history[dataIdx], 1)}%</span></div>
    `;
    const left = clamp(x + 12, 0, rect.width - 150);
    tooltip.style.left = left + "px";
    tooltip.style.top = "8px";
    renderChart();
  });
  chartCanvas.addEventListener("mouseleave", () => {
    hoverIndex = null;
    tooltip.hidden = true;
    renderChart();
  });

  tableToggleBtn.addEventListener("click", () => {
    const willShow = tableWrap.hidden;
    tableWrap.hidden = !willShow;
    tableToggleBtn.textContent = willShow ? "Hide data as table" : "Show data as table";
    if (willShow) updateTable();
  });

  // ---------- Feed ----------
  const feedEl = document.getElementById("feed");
  const feedEvents = [
    { tpl: (m) => `CPU load ${m.cpu.value > 80 ? "spiked" : "steady"} at ${fmt(m.cpu.value, 1)}%`, tag: (m) => m.cpu.value > 80 ? "warning" : "good" },
    { tpl: (m) => `Memory usage at ${fmt(m.memory.value, 1)}%`, tag: (m) => m.memory.value > 85 ? "warning" : "good" },
    { tpl: (m) => `${Math.round(m.requests.value)} requests/sec handled`, tag: () => "good" },
    { tpl: (m) => `${Math.round(m.users.value)} active users online`, tag: () => "good" },
    { tpl: () => `Error rate ${fmt(errorRate.value, 2)}%`, tag: () => statusForErrorRate(errorRate.value).cls === "good" ? "good" : (statusForErrorRate(errorRate.value).cls === "critical" ? "critical" : "warning") },
  ];

  function pushFeedEntry() {
    const ev = feedEvents[Math.floor(Math.random() * feedEvents.length)];
    const text = ev.tpl(metrics);
    const tag = ev.tag(metrics);
    const li = document.createElement("li");
    const now = new Date();
    li.innerHTML = `<time>${now.toLocaleTimeString()}</time><span class="feed-tag ${tag}">${tag.toUpperCase()}</span><span>${text}</span>`;
    feedEl.prepend(li);
    while (feedEl.children.length > 40) feedEl.removeChild(feedEl.lastChild);
  }

  // ---------- Clock ----------
  const clockEl = document.getElementById("clock");
  function tickClock() { clockEl.textContent = new Date().toLocaleTimeString(); }

  // ---------- Main loop ----------
  function tick() {
    tickCount++;
    timeLabels.push(new Date().toLocaleTimeString());
    if (timeLabels.length > MAX_POINTS) timeLabels.shift();

    Object.values(metrics).forEach((m) => {
      step(m);
      pushHistory(m.history, m.value);
    });
    step(errorRate);
    pushHistory(errorRate.history, errorRate.value);

    updateStatCards();
    renderChart();
    if (!tableWrap.hidden) updateTable();
    if (tickCount % 2 === 0) pushFeedEntry();
  }

  function startTimer() {
    stopTimer();
    timer = setInterval(tick, intervalMs);
  }
  function stopTimer() {
    if (timer) clearInterval(timer);
    timer = null;
  }

  // ---------- Controls ----------
  const pauseBtn = document.getElementById("pause-btn");
  const resetBtn = document.getElementById("reset-btn");
  const speedSelect = document.getElementById("speed-select");
  const statusBadge = document.getElementById("status-badge");
  const statusText = document.getElementById("status-text");

  pauseBtn.addEventListener("click", () => {
    running = !running;
    if (running) {
      startTimer();
      pauseBtn.textContent = "Pause";
      statusBadge.classList.remove("paused");
      statusText.textContent = "Live";
    } else {
      stopTimer();
      pauseBtn.textContent = "Resume";
      statusBadge.classList.add("paused");
      statusText.textContent = "Paused";
    }
  });

  resetBtn.addEventListener("click", () => {
    Object.values(metrics).forEach((m) => { m.history = []; });
    errorRate.history = [];
    timeLabels.length = 0;
    tickCount = 0;
    feedEl.innerHTML = "";
    renderChart();
    updateStatCards();
  });

  speedSelect.addEventListener("change", () => {
    intervalMs = Number(speedSelect.value);
    if (running) startTimer();
  });

  // ---------- Theme ----------
  const themeToggle = document.getElementById("theme-toggle");
  function applyStoredTheme() {
    let stored = null;
    try { stored = localStorage.getItem("sqd-theme"); } catch (e) { /* ignore */ }
    if (stored) document.documentElement.setAttribute("data-theme", stored);
  }
  themeToggle.addEventListener("click", () => {
    const current = document.documentElement.getAttribute("data-theme");
    const next = current === "dark" ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", next);
    try { localStorage.setItem("sqd-theme", next); } catch (e) { /* ignore */ }
    buildLegend();
    renderChart();
    updateStatCards();
  });

  // ---------- Init ----------
  applyStoredTheme();
  buildStatCards();
  buildLegend();
  window.addEventListener("resize", resizeCanvas);
  tickClock();
  setInterval(tickClock, 1000);

  // seed some history so the chart isn't empty on load
  for (let i = 0; i < 20; i++) {
    timeLabels.push(new Date(Date.now() - (20 - i) * intervalMs).toLocaleTimeString());
    Object.values(metrics).forEach((m) => { step(m); pushHistory(m.history, m.value); });
    step(errorRate); pushHistory(errorRate.history, errorRate.value);
  }
  updateStatCards();
  pushFeedEntry();
  resizeCanvas();
  startTimer();
})();
