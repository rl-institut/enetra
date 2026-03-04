// This takes care of handling events send by the map and passing it to the appropriate html elements
// Its a seperate file from the map-interface-enetra, so it can be deferet until after the map is loaded
// map-interface-enetra hydrates the map with data, and therefor needs to be loaded before the leaflet/draw map
//
//
//
mapdiv = document.getElementById('mapElement');
mapdiv.addEventListener('map-elements-edited', (event) => {
  console.log('map-elements-edited')
  targets = [];
  event.detail.layers.forEach((layer) => {
    target = document.getElementById(layer.id);
    target.value = JSON.stringify(layer.toGeoJSON().geometry);
    targets.push(target);
  });
  // Targets may trigger map redraw. this can effect unstored saves of the editiable layer
  // Therefor we trigger the change events only after all inputs have been transfered to the inputs
  targets.forEach((target) => target.dispatchEvent(new Event('change')));
})

mapdiv.addEventListener('map-element-created', (event) => {
  console.log(event.detail.layer.id);
  target_form = document.getElementById(document.getElementById('focusedForm').value)
  target = target_form.querySelector('[name*="geom"]');
  target.value = JSON.stringify(event.detail.layer.toGeoJSON().geometry);
  target.dispatchEvent(new Event('change'));
});
