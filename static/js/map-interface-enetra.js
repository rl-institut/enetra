// These are things the map needs
// Why functions? Instead of constants? This allows lazy evaluation if needed
function getCenter() {
  return { 'lat': 51.505, 'lng': -0.04 };
}
function getBounds() {
  return {};
  // return { 'lat_min': 51, 'lng_min': -1, 'lat_max': 52, 'lng_max': 1 };
}
function getMaxZoom() {
  return 18;

}
function getMinZoom() {
  return 5
}

function getZoom() {
  return 13
}

function getLayerNames() {
  return ['map-layer-editable', 'map-layer-selected', 'map-layer-open-area', 'map-layer-building-area']
}
function getEditLayerName() {
  return 'map-layer-editable'
}

function getPolyVerticesEditOption() {
  return {
    className: '',  // clear Leaflet's default divIcon styles
    html: '<div class="size-4 rounded-full bg-white border-2 border-slate-800 shadow-[0_2px_6px_rgba(0,0,0,0.35)] cursor-grab active:cursor-grabbing active:scale-120 transition-transform"></div>',
    iconSize: [16, 16],
    iconAnchor: [8, 8], // half of iconSize, so its centered
  }
}

function getStyle(name, id) {
  // https://leafletjs.com/reference.html#path-option
  const defaults = {
    stroke: true,
    color: '#3388ff',
    weight: 3,
    opacity: 1.0,
    lineCap: 'round',    // 'butt' | 'round' | 'square'
    lineJoin: 'round',    // 'miter' | 'round' | 'bevel'
    dashArray: null,       // e.g. '5,10' or '20,20'
    dashOffset: null,       // e.g. '5'
    fill: true,
    fillColor: '#3388ff',  // any CSS color; inherits `color` when null
    fillOpacity: 0.2,
    fillRule: 'evenodd',  // 'evenodd' | 'nonzero'
    interactive: true,
    bubblingMouseEvents: true,
    renderer: null,       // null → map default (SVG or Canvas)
    className: null,       // extra CSS class on the SVG/Canvas element
    pane: 'overlayPane', // any registered map pane name
    attribution: null,
  }
  console.log(name)
  let overrides = {}
  if (name.includes('edit')) overrides = { color: '#ea580c', fillColor: '#fdba74', dashArray: '8,5', fillOpacity: 0.45, weight: 2 } // orange — active editing
  else if (name.includes('selected')) overrides = { color: '#0284c7', fillColor: '#7dd3fc', fillOpacity: 0.45, weight: 2 } // sky blue — selected
  else if (name.includes('open')) overrides = { color: '#059669', fillColor: '#6ee7b7', fillOpacity: 0.40, weight: 2 } // emerald — open area
  else if (name.includes('building')) overrides = { color: '#4f46e5', fillColor: '#a5b4fc', fillOpacity: 0.45, weight: 2 } // indigo — building
  else overrides = { color: '#4f46e5', fillColor: '#a5b4fc', fillOpacity: 0.45, weight: 2 }
  return { ...defaults, ...overrides }
}

function getPopUps() {
  const mapDrawElements = document.querySelectorAll('.map-draw-element');
  var popups = {};
  mapDrawElements.forEach((el) => {
    // look inside a possible template first, query map element if no template exits
    const templateContent = el.querySelector('template.map-content-only').content || el;
    const popup = templateContent.querySelector('.map-popup-content');
    if (!popup) return
    const input = el.querySelector('textarea[name=geom],input[name=geom]');
    // For now we want to draw at max one popup per element
    const id = input.id;
    popups[id] = popup.innerHTML;
  });
  return popups;
}

function getMarkers() {
  const mapDrawElements = document.querySelectorAll('.map-draw-element');
  var markers = {};
  mapDrawElements.forEach((el) => {
    // look inside a possible template first, query map element if no template exits
    const templateContent = el.querySelector('template.map-content-only').content || el;
    const found_markers = templateContent.querySelectorAll('.map-marker');
    if (found_markers.length < 1) return
    const input = el.querySelector('textarea[name=geom],input[name=geom]');
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
    const inputs = document.querySelectorAll('.map-draw-element.' + name + ' textarea[name=geom], .map-draw-element.' + name + '  input[name=geom]');
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
