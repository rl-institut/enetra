// This takes care of handling events send by the map and passing it to the appropriate html elements
// Its a seperate file from the map-interface-enetra, so it can be deferet until after the map is loaded
// map-interface-enetra hydrates the map with data, and therefor needs to be loaded before the leaflet/draw map
//


var createPolygonKey = null;

document.addEventListener('create-polygon', (event) => {
  myMap.map.pm.disableDraw();
  createPolygonKey = event.detail.key || null;
  if (createPolygonKey == null) console.log("WARNING: polygon is created without key.");
  myMap.map.pm.enableDraw('Polygon', {
    allowSelfIntersection: true,
    pathOptions: {
      color: '#000000',
      fillColor: '#7F77DD',
      fillOpacity: 0.9,
      weight: 2,
      opacity: 1,
    },
  });
});


// document.addEventListener('map-item-stop-edit', (event) => {
//   console.log('foo')
//   if (myMap.map.pm.globalDrawModeEnabled()) {
//     console.log('bar')
//     document.dispatchEvent(new CustomEvent('stop-create-polygon'));
//   }
// });

// document.addEventListener('keyup', (event) => {
//   if (event.key === 'Escape' && myMap.map.pm.globalDrawModeEnabled()) {
//     console.log('bar')
//     document.dispatchEvent(new CustomEvent('stop-create-polygon'));
//   }
// });

document.addEventListener('keyup', (event) => {
  if (event.key === 'Enter') {
    document.dispatchEvent(new CustomEvent('finish-create-polygon'));
    document.dispatchEvent(new CustomEvent('map-redraw'));
  }
});


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
  targets.forEach((target) => target.dispatchEvent(new Event('change', { bubbles: true })));

})

mapdiv.addEventListener('some-map-element-created', (event) => {
  if (createPolygonKey != null) {
    event.detail.key = createPolygonKey
    var target = document.getElementById(createPolygonKey)
    if (!target) {
      console.log(target + ' not found')
      return
    }
    target.value = JSON.stringify(event.detail.layer.toGeoJSON().geometry);
    target.dispatchEvent(new Event('change', { bubbles: true }));
    document.dispatchEvent(new CustomEvent('map-element-created', { detail: { key: createPolygonKey }, bubbles: true }))
  }
  event.stopPropagation()
});
