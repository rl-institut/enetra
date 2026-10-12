function createGridChart(grids, time, div) {
    const gridChart = echarts.init(gridDiv);
    const series = [];
    for (let grid of grids) {
        if (grid.is_root) {
            series.push({
                name: grid.name,
                type: 'line',
                data: grid.gc_ts,
                smooth: false,
                symbol: 'none',
            });
        }
    }
    gridChart.setOption({
        grid: { containLabel: true },
        legend: {},
        xAxis: {data: time},
        yAxis: {axisLabel: { formatter: v => v + ' kW'}},
        series: series,
    });
}
