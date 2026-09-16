// Minimal canvas chart helpers shared by dashboard pages. No dependencies.
// Supports: line (multi-series, shared axis) and bar (grouped or stacked).

(function (global) {
  "use strict";

  function cssVar(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  }

  function resizeForDPR(canvas) {
    const rect = canvas.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    canvas.width = Math.max(1, Math.round(rect.width * dpr));
    canvas.height = Math.max(1, Math.round(rect.height * dpr));
    const ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    return { w: rect.width, h: rect.height, ctx };
  }

  // series: [{ label, color, values: [num|null] }]
  // options: { categories: [str], yMin, yMax, yStep, unit, padding }
  function drawLineChart(canvas, series, options) {
    const { w, h, ctx } = resizeForDPR(canvas);
    ctx.clearRect(0, 0, w, h);
    const pad = Object.assign({ top: 14, right: 14, bottom: 22, left: 34 }, options.padding || {});
    const plotW = w - pad.left - pad.right;
    const plotH = h - pad.top - pad.bottom;
    const cats = options.categories;
    const n = cats.length;
    const stepX = n > 1 ? plotW / (n - 1) : plotW;

    let yMin = options.yMin, yMax = options.yMax;
    if (yMin === undefined || yMax === undefined) {
      const all = series.flatMap((s) => s.values.filter((v) => v !== null && v !== undefined));
      yMin = options.yMin ?? Math.min(0, ...all);
      yMax = options.yMax ?? Math.max(1, ...all) * 1.15;
    }

    const gridColor = cssVar("--gridline");
    const mutedColor = cssVar("--text-muted");
    const baselineColor = cssVar("--baseline");

    ctx.font = "11px " + getComputedStyle(document.body).fontFamily;
    ctx.fillStyle = mutedColor;
    ctx.strokeStyle = gridColor;
    ctx.lineWidth = 1;
    const yStep = options.yStep ?? (yMax - yMin) / 4;
    for (let t = yMin; t <= yMax + 1e-9; t += yStep) {
      const y = pad.top + plotH - ((t - yMin) / (yMax - yMin)) * plotH;
      ctx.beginPath();
      ctx.moveTo(pad.left, y);
      ctx.lineTo(pad.left + plotW, y);
      ctx.stroke();
      ctx.fillText((Math.round(t * 100) / 100) + (options.unit || ""), 2, y + 3);
    }

    ctx.strokeStyle = baselineColor;
    ctx.beginPath();
    ctx.moveTo(pad.left, pad.top + plotH);
    ctx.lineTo(pad.left + plotW, pad.top + plotH);
    ctx.stroke();

    // x labels
    ctx.fillStyle = mutedColor;
    ctx.textAlign = "center";
    cats.forEach((c, i) => {
      if (n > 10 && i % 2 !== 0) return;
      const x = pad.left + i * stepX;
      ctx.fillText(c, x, pad.top + plotH + 16);
    });
    ctx.textAlign = "left";

    if (options.refLine !== undefined) {
      const y = pad.top + plotH - ((options.refLine - yMin) / (yMax - yMin)) * plotH;
      ctx.save();
      ctx.strokeStyle = mutedColor;
      ctx.setLineDash([4, 4]);
      ctx.beginPath();
      ctx.moveTo(pad.left, y);
      ctx.lineTo(pad.left + plotW, y);
      ctx.stroke();
      ctx.restore();
    }

    series.forEach((s) => {
      const pts = [];
      s.values.forEach((v, i) => {
        if (v === null || v === undefined) return;
        const x = pad.left + i * stepX;
        const y = pad.top + plotH - ((v - yMin) / (yMax - yMin)) * plotH;
        pts.push([x, y]);
      });
      if (pts.length === 0) return;
      ctx.beginPath();
      ctx.lineWidth = 2;
      ctx.lineJoin = "round";
      ctx.lineCap = "round";
      ctx.strokeStyle = s.color;
      pts.forEach(([x, y], i) => (i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y)));
      ctx.stroke();
      pts.forEach(([x, y]) => {
        ctx.beginPath();
        ctx.fillStyle = s.color;
        ctx.arc(x, y, 3, 0, Math.PI * 2);
        ctx.fill();
      });
    });
  }

  // series: [{ label, color, values: [num|null] }]
  // options: { categories, stacked, yMax, unit }
  function drawBarChart(canvas, series, options) {
    const { w, h, ctx } = resizeForDPR(canvas);
    ctx.clearRect(0, 0, w, h);
    const pad = Object.assign({ top: 14, right: 14, bottom: 22, left: 34 }, options.padding || {});
    const plotW = w - pad.left - pad.right;
    const plotH = h - pad.top - pad.bottom;
    const cats = options.categories;
    const n = cats.length;

    let yMax = options.yMax;
    if (yMax === undefined) {
      if (options.stacked) {
        const sums = cats.map((_, i) => series.reduce((acc, s) => acc + (s.values[i] || 0), 0));
        yMax = Math.max(1, ...sums) * 1.15;
      } else {
        const all = series.flatMap((s) => s.values.filter((v) => v !== null && v !== undefined));
        yMax = Math.max(1, ...all) * 1.15;
      }
    }
    const yMin = 0;

    const gridColor = cssVar("--gridline");
    const mutedColor = cssVar("--text-muted");
    const baselineColor = cssVar("--baseline");

    ctx.font = "11px " + getComputedStyle(document.body).fontFamily;
    const yStep = options.yStep ?? (yMax - yMin) / 4;
    ctx.strokeStyle = gridColor;
    ctx.fillStyle = mutedColor;
    ctx.lineWidth = 1;
    for (let t = yMin; t <= yMax + 1e-9; t += yStep) {
      const y = pad.top + plotH - ((t - yMin) / (yMax - yMin)) * plotH;
      ctx.beginPath();
      ctx.moveTo(pad.left, y);
      ctx.lineTo(pad.left + plotW, y);
      ctx.stroke();
      ctx.fillText(Math.round(t) + (options.unit || ""), 2, y + 3);
    }

    if (options.refLine !== undefined) {
      const y = pad.top + plotH - ((options.refLine - yMin) / (yMax - yMin)) * plotH;
      ctx.save();
      ctx.strokeStyle = mutedColor;
      ctx.setLineDash([4, 4]);
      ctx.beginPath();
      ctx.moveTo(pad.left, y);
      ctx.lineTo(pad.left + plotW, y);
      ctx.stroke();
      ctx.restore();
    }

    const groupW = plotW / n;
    const barGap = 2;
    const barW = options.stacked ? groupW * 0.55 : (groupW * 0.7) / series.length;

    cats.forEach((cat, i) => {
      const groupX = pad.left + i * groupW + groupW / 2;
      if (options.stacked) {
        let base = 0;
        series.forEach((s) => {
          const v = s.values[i];
          if (!v) return;
          const barH = (v / (yMax - yMin)) * plotH;
          const x = groupX - barW / 2;
          const y = pad.top + plotH - ((base + v) / (yMax - yMin)) * plotH;
          ctx.fillStyle = s.color;
          ctx.beginPath();
          roundRectTop(ctx, x, y, barW, barH, base + v >= (options.stackTotal ? options.stackTotal(i) : Infinity) ? 3 : 0);
          ctx.fill();
          base += v;
        });
      } else {
        const total = series.length;
        series.forEach((s, si) => {
          const v = s.values[i];
          const x = groupX - (total * barW) / 2 + si * (barW + barGap);
          if (v === null || v === undefined) return;
          const barH = (v / (yMax - yMin)) * plotH;
          const y = pad.top + plotH - barH;
          ctx.fillStyle = s.color;
          ctx.beginPath();
          roundRectTop(ctx, x, y, barW, barH, 3);
          ctx.fill();
        });
      }
    });

    ctx.strokeStyle = baselineColor;
    ctx.beginPath();
    ctx.moveTo(pad.left, pad.top + plotH);
    ctx.lineTo(pad.left + plotW, pad.top + plotH);
    ctx.stroke();

    ctx.fillStyle = mutedColor;
    ctx.textAlign = "center";
    cats.forEach((c, i) => {
      const x = pad.left + i * groupW + groupW / 2;
      ctx.fillText(c, x, pad.top + plotH + 16);
    });
    ctx.textAlign = "left";
  }

  function roundRectTop(ctx, x, y, w, h, r) {
    r = Math.min(r, w / 2, Math.max(h, 0));
    if (h <= 0) { ctx.rect(x, y, w, 0); return; }
    ctx.moveTo(x, y + h);
    ctx.lineTo(x, y + r);
    ctx.arcTo(x, y, x + r, y, r);
    ctx.lineTo(x + w - r, y);
    ctx.arcTo(x + w, y, x + w, y + r, r);
    ctx.lineTo(x + w, y + h);
    ctx.closePath();
  }

  global.Charts = { drawLineChart, drawBarChart, cssVar, resizeForDPR };
})(window);
