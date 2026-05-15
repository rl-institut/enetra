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
    this.isEditing = null;

    this._initMap();
    this._initLayers();
    this._initDrawControl();


    this._bindEvents();
    const debounced_draw = debounce(this.drawElements.bind(this), 10);
    document.addEventListener('alpine:initialized', () => this.drawElements());
    document.addEventListener('map-redraw', () => debounced_draw());
    document.addEventListener('geom-changed', () => debounced_draw());
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

    // Do we need a Toolbar?
    // NOTE: selectedPathOptions dont work since we style each layer separately
    // var drawControl = new L.Control.Draw({
    //   edit: {
    //     featureGroup: this.featureGroups[editLayerName],
    //     remove: false,
    //     poly: { allowIntersection: false },
    //   },
    //   draw: {
    //     marker: false,
    //     circle: false,
    //     polyline: false,
    //     rectangle: false,
    //     polygon: {
    //       allowIntersection: true,
    //       showArea: true,
    //     },
    //   },
    // })
    // this.map.addControl(drawControl);


    // Do we want custom edit handlers?
    // changing the default css could also be an option
    // .leaflet-editing-icon {
    //     border-radius: 50%;
    // }
    const customIcon = L.divIcon(
      getPolyVerticesEditOption()
    );

    L.Edit.PolyVerticesEdit.prototype.options = {
      ...L.Edit.PolyVerticesEdit.prototype.options,
      icon: customIcon,
      touchIcon: customIcon,
    };

    // // Make the polygon path dotted while editing
    // L.Edit.Poly.prototype.options = {
    //   ...L.Edit.Poly.prototype.options,
    //   selectedPathOptions: {
    //     dashArray: '8, 8',
    //     fill: true,
    //     fillColor: '#fe57a1',
    //     fillOpacity: 0.1,
    //     maintainColor: false,
    //   },
    // };

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
    var layers = {}
    this.isEditing = false
    geometries.forEach(({ geojson, layer: layer_name, id, style }) => {
      console.assert(geojson.type === 'Polygon')

      const coords = geojson.coordinates[0].map(([lng, lat]) => [lat, lng]);
      const layer = L.polygon(coords, {
        ...style,
        interactive: true,
        bubblingMouseEvents: true,
      });
      layer.on('click', (e) => {
        console.log('id: ' + id + ' top clicked. Layer: ' + e.layer);
      });

      if (layer_name === editLayerName) {
        layer.editing.enable();
        this.isEditing = true;
      }

      layers[id] = layer;
      layer.id = id;
      layer.geojson = geojson;
      this.featureGroups[layer_name].addLayer(layer);

    });

    // turn on markers and hovers, but only if no layer is in editable mode
    // since editing is not easy with hovers/icons etc
    if (!this.isEditing) {
      geometries.forEach(({ geojson, layer: layer_name, id, style }) => {
        var layer = layers[id];
        const popupContent = popups[id];
        if (popupContent) {
          layer.on('click', function(e) {
            const evt = e.originalEvent;

            // Return early if any modifier is pressed
            if (evt.shiftKey || evt.ctrlKey || evt.altKey || evt.metaKey) return;

            layer.bindPopup(popupContent).openPopup();
          })
        }
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
      });
    }


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

    const dispatchLayerChanges = (event) => {
      const layers = []; this.featureGroups[editLayerName].eachLayer((layer) => layers.push(layer));

      this.map._container.dispatchEvent(new CustomEvent('map-elements-edited', {
        detail: { layers, originalEvent: event },
      }));

    }

    document.addEventListener('finish-create-polygon', (event) => {

      dispatchLayerChanges(event)
    });

    [
      // lets patch the input only when editing finishes
      // 'draw:editvertex', 'draw:editresize', 'draw:editmove',
      L.Draw.Event.EDITED].forEach((eventName) => {
        this.map.addEventListener(eventName, (event) => {
          dispatchLayerChanges(event)
        });
      });

    this.map.on(L.Draw.Event.CREATED, (event) => {
      this.map._container.dispatchEvent(new CustomEvent('some-map-element-created', {
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

      setTimeout(() => document.dispatchEvent(new Event('map-redraw')), 10);
    });
  }
}
