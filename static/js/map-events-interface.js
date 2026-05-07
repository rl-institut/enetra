// This takes care of handling events send by the map and passing it to the appropriate html elements
// Its a seperate file from the map-interface-enetra, so it can be deferet until after the map is loaded
// map-interface-enetra hydrates the map with data, and therefor needs to be loaded before the leaflet/draw map
//


var drawHandler = null;
var createPolygonKey = null;
document.addEventListener('create-polygon', (event) => {
  if (drawHandler instanceof L.Draw.Polygon) {
    drawHandler.disable()
  }
  createPolygonKey = event.detail.key || null;
  if (createPolygonKey == null) console.log("WARNING:polygon is created without key.")
  drawHandler = new L.Draw.Polygon(myMap.map, {
    allowIntersection: true,
    showArea: true,
    icon: new L.DivIcon({
      iconSize: new L.Point(8, 8),
      className: 'rounded leaflet-div-icon leaflet-editing-icon'
    }),
    touchIcon: new L.DivIcon({
      iconSize: new L.Point(20, 20),
      className: 'leaflet-div-icon leaflet-editing-icon'
    }),
    shapeOptions: {
      color: '#000000',
      fillColor: '#7F77DD',
      fillOpacity: 0.9,
      weight: 2,
      opacity: 1,
    }
  });
  drawHandler.enable()
}
)

document.addEventListener('stop-create-polygon', (event) => {
  if (drawHandler instanceof L.Draw.Polygon) {
    drawHandler.disable()
  }
})

document.addEventListener('finish-create-polygon', (event) => {
  if (drawHandler instanceof L.Draw.Polygon) {
    drawHandler.completeShape();
    drawHandler = null;
  }
})

document.addEventListener('keyup', (event) => {
  if (event.key === 'Esc' && drawHandler != null) {
    document.dispatchEvent(new CustomEvent('stop-create-polygon'));
  }
});

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
  console.log(event.detail.layer.toGeoJSON().geometry);
  if (createPolygonKey != null) {
    event.detail.key = createPolygonKey
    console.log('searching for ' + createPolygonKey)
    var target = document.getElementById(createPolygonKey)
    if (!target) {
      console.log('not found')
      return
    }
    target.value = JSON.stringify(event.detail.layer.toGeoJSON().geometry);
    target.dispatchEvent(new Event('change', { bubbles: true }));
    document.dispatchEvent(new CustomEvent('map-element-created', { detail: { key: createPolygonKey }, bubbles: true }))
  }
  event.stopPropagation()
});
