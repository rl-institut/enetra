// alpine storage for map state data
// listens to events and adjusts state accordingly.
// selected site elements read this storage to infer their current state
//
document.addEventListener('alpine:init', () => {
  Alpine.store('map', {
    editingElements: new Set(),
    highlightElements: new Set(),
  });

  const mapStore = Alpine.store('map');

  // User presses escapse or enter
  window.addEventListener('keyup', (event) => {
    if (event.key === 'Enter' || event.key === "Escape") {
      mapStore.editingElements = new Set();
      window.dispatchEvent(new CustomEvent('map-redraw'));
    }
  });

  window.addEventListener('map-item-stop-edit', (event) => {
    const layerId = event.detail.value;
    mapStore.editingElements.delete(layerId);
    window.dispatchEvent(new CustomEvent('map-redraw'));
  });

  window.addEventListener('map-item-edit', (event) => {
    const layerId = event.detail.value;
    mapStore.editingElements.add(layerId);
    window.dispatchEvent(new CustomEvent('map-redraw'));
  });

  // Toggle edit state of layer on shift click
  window.addEventListener('map-layer-shift-clicked', (event) => {
    const layerId = event.detail.value;
    toggleSet(mapStore.editingElements, layerId)
    window.dispatchEvent(new CustomEvent('map-redraw'));
  });

  // Toggle highlight state of layer on click
  window.addEventListener('map-layer-shift-clicked', (event) => {
    const layerId = event.detail.value;
    toggleSet(mapStore.highlightElements, layerId)
    window.dispatchEvent(new CustomEvent('map-redraw'));
  });
})

function toggleSet(set, element) {
  set.has(element) ? set.add(element) : set.delete(element);
}
