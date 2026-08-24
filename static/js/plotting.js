async function loadPlot(url, el) {
  if (!url) {
    el._chart?.dispose();
    el._chart = null;
    el.innerHTML = "";
    return;
  }
  try {
    const r = await fetch(url);
    if (!r.ok) throw Object.assign(new Error(`HTTP ${r.status}`), { status_code: r.status });
    const data = await r.json();
    const ts = data.timeseries ?? data;
    const totalMinutes = ts.values.length * ts.timestep_minutes;
    var unitLabel, unitMinutes;
    const MAX_X_STEPS = 100;
    if (totalMinutes <= MAX_X_STEPS) {
      unitLabel = 'min';
      unitMinutes = 1;
    } else if (totalMinutes <= 60 * MAX_X_STEPS) {
      unitLabel = 'h';
      unitMinutes = 60;
    } else if (totalMinutes <= 24 * 60 * MAX_X_STEPS) {
      unitLabel = 'd';
      unitMinutes = 24 * 60;
    } else {
      unitLabel = 'Woche';
      unitMinutes = 24 * 60 * 7;
    }
    const points = ts.values.map((v, i) => [(i * ts.timestep_minutes) / unitMinutes, v]);

    if (!el._chart) {
      el.innerHTML = "";
      el._chart = echarts.init(el);
      new ResizeObserver(() => el._chart?.resize()).observe(el);
    }
    el._chart.setOption({
      grid: { left: 8, right: 16, top: 16, bottom: 8, containLabel: true },
      tooltip: {
        trigger: "axis",
        axisPointer: { type: "line" },
        valueFormatter: (v) => v.toLocaleString(),
      },
      xAxis: {
        type: "value",
        min: 0,
        max: "dataMax",
        axisLabel: { color: "#898781", formatter: `{value} ${unitLabel}` },
        axisLine: { lineStyle: { color: "#c3c2b7" } },
        splitLine: { show: false },
      },
      yAxis: {
        type: "value",
        axisLabel: { color: "#898781" },
        splitLine: { lineStyle: { color: "#e1e0d9" } },
      },
      series: [
        {
          type: "line",
          data: points,
          showSymbol: false,
          lineStyle: { width: 2, color: "#2a78d6" },
          itemStyle: { color: "#2a78d6" },
        },
      ],
    });
  } catch (err) {
    el._chart?.dispose();
    el._chart = null;
    if (err.status_code == 403) {
      el.innerHTML = `<p class="text-sm text-gray-500">Dir fehlt die Berechtigung diesen Plot zu sehen. Daten von anderen Beteiligten werden dir nicht angezeigt.</p>`;
    } else {
      el.innerHTML = `<p class="text-sm text-gray-500">Plot konnte nicht geladen werden (${err.message})</p>`;
    }
  }
}
