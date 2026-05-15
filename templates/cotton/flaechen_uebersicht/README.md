# Flächenübersicht

Renders a full-width card with a Leaflet map on the left and a side panel on the right. Polygon colors reflect scenario-specific values using per-scenario color palettes. Optionally reacts to a custom DOM event to switch the active scenario.

### Static map (ergebnisse.html)

No scenario switching — always shows Wasserstoff-Integration colors.

```html
<c-flaechen-uebersicht>
  <div class="w-[351px] shrink-0 border-l border-slate-200 p-6 flex flex-col gap-6 rounded-br-lg"
    x-data="{ details: true }">

    <div class="flex flex-col gap-2">
      <span class="text-sm font-semibold text-slate-800">Gesamtstromerzeugung</span>
      <div class="flex items-baseline gap-1.5">
        <span class="w-2 h-2 rounded-full bg-cyan-500 shrink-0 self-center"></span>
        <span class="text-[20px] font-semibold text-slate-800 leading-normal">1.983</span>
        <span class="text-sm font-normal text-slate-500">MWh</span>
      </div>
    </div>

    <!-- location breakdown rows … -->

  </div>
</c-flaechen-uebersicht>
```

### Scenario-aware map (szenarienvergleich.html)

The side panel dispatches `flaechen-scenario-change` when a radio button is selected; the component listens and recolors the polygons.

```html
<c-flaechen-uebersicht scenario_event="flaechen-scenario-change">
  <div class="w-[351px] shrink-0 border-l border-slate-200 p-6 flex flex-col gap-4 rounded-br-lg"
    x-data="{
      active: 2,
      scenarios: [
        { id: 1, name: 'Ziel 2035',              value: '2.050', color: '#E9C823', pct: '100%'  },
        { id: 2, name: 'Wasserstoff-Integration', value: '1.983', color: '#06B6D4', pct: '95.5%' },
        { id: 3, name: 'PV-Integration',          value: '1.744', color: '#3CC681', pct: '83.8%' },
        { id: 4, name: 'Ziel 2030',               value: '1.298', color: '#E17527', pct: '59.9%' },
      ],
      init() {
        this.$watch('active', id =>
          document.dispatchEvent(new CustomEvent('flaechen-scenario-change', { detail: id }))
        );
      },
    }">

    <span class="text-sm font-semibold text-slate-800">Gesamtstromerzeugung</span>
    <!-- radio button rows … -->

  </div>
</c-flaechen-uebersicht>
```

## Notes

- Polygon coordinates are hardcoded (Westhafen site). In a real integration these would be passed from the Django view context.
