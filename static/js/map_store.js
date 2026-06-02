// alpine storage for map state data
// listens to events and adjusts state accordingly.
// selected site elements read this storage to infer their current state
//
const dispatchMapRedraw = () => document.dispatchEvent(new CustomEvent('map-redraw'));
document.addEventListener('alpine:init', () => {
  console.log('init')
  Alpine.store('map', {
    editingElements: new Set(),
    highlightElements: new Set(),
  });
  const mapStore = Alpine.store('map');

  function stopEdit(){
      mapStore.editingElements = new Set();
      dispatchMapRedraw();
  }
  // User presses escapse or enter
  window.addEventListener('keyup', (event) => {
    if (event.key === 'Enter' || event.key === "Escape") {
      stopEdit();
    }
  });
  // User presses escapse or enter
  window.addEventListener('map-stop-edit', (event) => {
      stopEdit();
  });

  window.addEventListener('map-item-stop-highlight', (event) => {
    const layerId = event.detail.value;
    mapStore.highlightElements.delete(layerId);
    dispatchMapRedraw();
  });

  window.addEventListener('map-item-highlight', (event) => {
    const layerId = event.detail.value;
    mapStore.highlightElements.add(layerId);
    dispatchMapRedraw();
  });

  window.addEventListener('map-item-stop-edit', (event) => {
    console.log('stop')
    const layerId = event.detail.value;
    mapStore.editingElements.delete(layerId);
    dispatchMapRedraw();
  });

  function addEditElement(event) {
    // Allow only single instance edit for now
    const layerId = event.detail.value;
    mapStore.editingElements.clear();
    mapStore.editingElements.add(layerId);
    dispatchMapRedraw();
  }

  window.addEventListener('map-item-edit', (event) => {
    addEditElement(event);
  });

  // Toggle edit state of layer on shift click
  window.addEventListener('map-layer-shift-clicked', (event) => {
    addEditElement(event);
  });

  // Toggle highlight state of layer on click
  window.addEventListener('map-layer-clicked', (event) => {
    const layerId = event.detail.value;
    toggleSet(mapStore.highlightElements, layerId)
    dispatchMapRedraw();
  });
})

function toggleSet(set, element) {
  set.has(element) ? set.delete(element) : set.add(element);
}
