// Why functions? Instead of constants? This allows lazy evaluation if needed
function getCenter() {
  return { 'lat': 51.505, 'lng': -0.04 };
}
function getBounds() {
  return { 'lat_min': 51, 'lng_min': -1, 'lat_max': 52, 'lng_max': 1 };
}
function getMaxZoom() {
  return 18;

}
function getMinZoom() {
  return 10
}

function getZoom() {
  return 13
}

function getLayerNames() {
  return ['map-layer-editable', 'map-layer-selected', 'map-layer-area']
}
function getEditLayerName() {
  return 'map-layer-editable'
}

function getStyle(name, id) {
  // https://leafletjs.com/reference.html#path-option
  if (name.includes('edit')) {
    return { 'fillColor': 'red', 'fillOpacity': 0.8 }
  }
  if (name.includes('selected')) {
    return { 'fillColor': 'green', 'fillOpacity': 0.8 }
  }
  return { 'fillColor': 'blue', 'fillOpacity': 0.8 }
}

function getPopUps() {
  const mapDrawElements = document.querySelectorAll('.map-draw-element');
  var popups = {};
  mapDrawElements.forEach((el) => {
    const popup = el.querySelector('.map-popup-content');
    if (!popup) return
    const input = el.querySelector('textarea,input');
    // For now we want to draw at max on popup per element
    const id = input.id;
    popups[id] = popup.innerHTML;
  });
  return popups;
}

function getMarkers() {
  const mapDrawElements = document.querySelectorAll('.map-draw-element');
  var markers = {};
  mapDrawElements.forEach((el) => {
    const found_markers = el.querySelectorAll('.map-marker');
    if (found_markers.length < 1) return
    const input = el.querySelector('textarea,input');
    // For now we want to draw at max on popup per element
    const id = input.id;
    markers[id] = [...found_markers].map((x) => x.innerHTML);
  });
  return markers;
}

function getGeoJsons() {
  geojsons = []
  getLayerNames().forEach((name) => {
    // Get the values as GeoJSON from the inputs
    // input mus be inside a container with a class of draw-map-element layername as class.
    // Can be anything with a value of geosjon
    const inputs = document.querySelectorAll('.map-draw-element.' + name + ' textarea, .map-draw-element.' + name + '  input');
    // const inputs = document.querySelectorAll('.map-draw-element.' + name + '> textarea, .map-draw-element.' + name + ' > input');
    if (inputs.length == 0) {
      console.log(`No inputs found for layer ${name}`)
    }

    // Add a style to each geometry/Feature, the layer name, and a unique identifier
    inputs.forEach((input) => {
      console.assert(input.name.includes('geom'), 'input should be a "geom"');
      id = input.id
      try {
        if (input.value == '') {
          return
        }
        geojson = JSON.parse(input.value);

      } catch (err) {
        console.error('JSON parsing failed:', err.message);
        return
      }

      coords = geojson.coordinates[0];
      cleaned_coords = [coords[0]]
      for (let i = 1; i < coords.length; i++) {
        if (coords[i][0] != coords[i - 1][0] || coords[i][1] != coords[i - 1][1]) {
          cleaned_coords.push(coords[i]);

        }
      }
      geojson.coordinates = [cleaned_coords];

      geojsons.push(
        {
          'geojson': geojson,
          'layer': name,
          'style': getStyle(name, id),
          'id': id
        }
      )
    });
  });
  return geojsons;
};
