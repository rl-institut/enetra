function debounce(func, timeout = 300) {
  let timer;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => { func.apply(this, args); }, timeout);
  };
}

class MyMap {
  constructor(selector, settings = {}) {
    this.selector = selector;
    this.settings = settings;
    this.map = null;
    this.osm = null;
    this.drawnItems = null;
    this.featureGroups = {};

    this._initMap();
    this._initLayers();
    this._initDrawControl();


    this._bindEvents();
    const debounced_draw = debounce(this.drawElements.bind(this), 10);
    document.addEventListener('alpine:initialized', () => this.drawElements());
    window.addEventListener('map-redraw', () => debounced_draw());
    window.addEventListener('geom-changed', () => debounced_draw());
  }

  _initMap() {
    console.log('_initMap()')
    const osmUrl = 'https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png';
    const osmAttrib = '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/attributions">CARTO</a>';

    // Add CartoDB light tile layer
    this.osm = L.tileLayer(osmUrl, {
      attribution: osmAttrib,
      subdomains: 'abcd',
      maxZoom: this.settings.getMaxZoom?.() ?? getMaxZoom(),
      minZoom: this.settings.getMinZoom?.() ?? getMinZoom(),
    })

    const b = this.settings.getBounds?.() ?? getBounds();
    const bounds = L.latLngBounds(
      L.latLng(b.lat_min, b.lng_min),
      L.latLng(b.lat_max, b.lng_max)
    );

    const center = this.settings.getCenter?.() ?? getCenter();

    this.map = new L.Map(this.selector.replace('#', ''), {
      maxBounds: bounds,
      maxBoundsViscosity: 1.0,
      center: new L.LatLng(center.lat, center.lng),
      zoom: this.settings.getZoom?.() ?? getZoom(),
    });

    this.osm.addTo(this.map);
  }

  _initLayers() {

    this.drawnItems = L.featureGroup().addTo(this.map);

    const layerNames = this.settings.getLayerNames?.() ?? getLayerNames();
    layerNames.forEach((lname) => {
      this.featureGroups[lname] = L.featureGroup().addTo(this.map);
      this.featureGroups[lname].on('click', (e) => console.log(lname + ' clicked. Layer: ' + e.layer));
    });
  }

  _initDrawControl() {

    const editLayerName = this._getEditLayerName();

    this.map.addControl(new L.Control.Draw({
      edit: {
        featureGroup: this.featureGroups[editLayerName],
        poly: { allowIntersection: false },
      },
      draw: {
        polygon: {
          allowIntersection: false,
          showArea: true,
        },
      },
    }));
  }

  _getEditLayerName() {
    return this.settings.getEditLayerName?.() ?? getEditLayerName();
  }

  drawElements() {
    console.log('drawing elements');
    const geometries = this.settings.getGeoJsons?.() ?? getGeoJsons();
    const popups = this.settings.getPopUps();
    const markers = this.settings.getMarkers();


    console.log(geometries);
    const editLayerName = this._getEditLayerName();

    // #Remove all markers
    this.map.eachLayer((layer) => {
      if (layer instanceof L.Marker) {
        this.map.removeLayer(layer);
      }
    });

    for (const featureGroup of Object.values(this.featureGroups)) {
      featureGroup.clearLayers();
    }
    var layers = []
    geometries.forEach(({ geojson, layer: layer_name, id, style }) => {
      if (geojson.type !== 'Polygon') return;

      const coords = geojson.coordinates[0].map(([lng, lat]) => [lat, lng]);
      const layer = L.polygon(coords, {
        ...style,
        interactive: true,
        bubblingMouseEvents: true,
      });

      const popupContent = popups[id];
      if (popupContent) {
        layer.on('click', function(e) {
          const evt = e.originalEvent;

          // Return early if any modifier is pressed
          if (evt.shiftKey || evt.ctrlKey || evt.altKey || evt.metaKey) return;

          layer.bindPopup(popupContent).openPopup();
        })
      }


      layers.push(layer);
      layer.id = id;
      layer.geojson = geojson;
      this.featureGroups[layer_name].addLayer(layer);



      const this_markers = markers[id];
      if (this_markers && this_markers.length >= 1) {
        const latlng_center = layer.getCenter();
        const bounds = layer.getBounds();
        const lng_min = bounds.getWest();
        const lng_max = bounds.getEast();
        const span = (lng_max - lng_min) * 0.8;
        const delta = span / this_markers.length;
        var start_lng = latlng_center.lng - span / 2 + delta / 2;
        this_markers.forEach((label) => {
          const labelIcon = L.divIcon({
            className: "",
            html: label,
            iconSize: [0, 0],
            iconAnchor: [0, 0],
          });
          // interactive false to ignore clicks
          const marker = L.marker([latlng_center.lat, start_lng],
            { icon: labelIcon, interactive: false }
          ).addTo(this.map);
          start_lng = start_lng + delta;
          // marker.bindTooltip(label, {
          //   permanent: true,
          //   direction: "top",
          //   offset: [0, -10]
          // });
        });
      }

      layer.on('click', (e) => {
        console.log('id: ' + id + ' top clicked. Layer: ' + e.layer);
      });

      if (layer_name === editLayerName) {
        layer.editing.enable();
      }
    });


  }

  toggleEditable() {
    this.featureGroups[this._getEditLayerName()].eachLayer((layer) => {
      if (layer.editing.enabled()) {
        layer.editing.disable();
      } else {
        layer.editing.enable();
      }
    });
  }

  _bindEvents() {
    const editLayerName = this._getEditLayerName();

    ['draw:editvertex', 'draw:editresize', 'draw:editmove', L.Draw.Event.EDITED].forEach((eventName) => {
      this.map.addEventListener(eventName, (event) => {
        const layers = [];
        this.featureGroups[editLayerName].eachLayer((layer) => layers.push(layer));

        this.map._container.dispatchEvent(new CustomEvent('map-elements-edited', {
          detail: { layers, originalEvent: event },
        }));
      });
    });

    this.map.on(L.Draw.Event.CREATED, (event) => {

      this.map._container.dispatchEvent(new CustomEvent('map-element-created', {
        detail: { layer: event.layer, originalEvent: event },
      }));
    });

    // Query all layers on Shift click event
    this.map.on('click', (e) => {
      if (!e.originalEvent.shiftKey) return;
      console.log('map shift-clicked at ' + e.latlng);
      const point = turf.point([e.latlng.lng, e.latlng.lat]); // note: turf uses [lng, lat]
      // we can iterate over all map layers directly but map.eachLayer also gives the base layers
      // which we want to avoid
      const layerNames = this.settings.getLayerNames?.() ?? getLayerNames();
      layerNames.forEach((lname) => {
        console.log('searching ' + lname);
        const featureGroup = this.featureGroups[lname];
        featureGroup.eachLayer((layer) => {
          const inside = turf.booleanPointInPolygon(point, layer.geojson);
          if (inside) {
            console.log('inside ' + layer.id);
            const event = new CustomEvent('map-layer-shift-clicked', { bubbles: true });
            document.getElementById(layer.id).dispatchEvent(event);
          } else {
            console.log(layer.id + ' not inside');

          };


        });
      });

      setTimeout(() => window.dispatchEvent(new Event('map-redraw')), 10);
    });
  }
}

// // turf takes care of raycasting/finding of inside/outside
// // a layer can listen to a click event directly. this only works for the top most layer though.
// // if layers overlap the one below can not be selected. this takes care of it
// const inside = turf.booleanPointInPolygon(point, layer.geojson);
// if (inside) {
//   console.log('Layer with id: ' + layer.id + ' clicked');
//   console.log(layer);
//   const event = new CustomEvent('map-layer-clicked', {bubbles:true});
//   document.getElementById(layer.id).dispatchEvent(event);
// }
