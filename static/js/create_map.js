function debounce(func, timeout = 300) {
  let timer;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => { func.apply(this, args); }, timeout);
  };
}

class MyMap {
  static Mode = Object.freeze({
    EDIT: "edit",
    ROTATE: 'rotate',
    MOVE: "move"
  });
  constructor(selector, settings = {}) {
    this.selector = selector;
    this.settings = settings;
    this.map = null;
    this.osm = null;
    this.drawnItems = null;
    this.featureGroups = {};
    this.isEditing = null;
    this.editingMode = MyMap.Mode.EDIT
    this._vertexHistory = [];
    this._lastLayerState = new Map();


    this._initMap();
    this._initLayers();
    this._initDrawControl();


    this._bindEvents();
    const debounced_draw = debounce(this.drawElements.bind(this), 10);
    document.addEventListener('alpine:initialized', () => this.drawElements());
    document.addEventListener('map-redraw', () => debounced_draw());
    document.addEventListener('map-redraw-sync', () => this.drawElements());
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

  setMode(mode) {
    console.assert(
      Object.values(MyMap.Mode).includes(mode),
      "Invalid status"
    );
    this.editingMode = mode;
    this.drawElements();

  }
  _initLayers() {

    this.drawnItems = L.featureGroup().addTo(this.map);

    const layerNames = this.settings.getLayerNames?.() ?? getLayerNames();
    layerNames.forEach((lname) => {
      this.featureGroups[lname] = L.featureGroup().addTo(this.map);
      // this.featureGroups[lname].on('click', (e) => console.log(lname + ' clicked. Layer: ' + e.layer));
    });
  }

  disableDraw() {
    this.map.pm.disableDraw();
    this.drawElements()

  }

  // When changes are excepted call this function to pass changed map geometries to inputs
  // via event/event listener

  dispatchLayerChanges(event) {
    const editLayerName = this._getEditLayerName();
    const layers = this.featureGroups[editLayerName].getLayers();

    this.map._container.dispatchEvent(new CustomEvent('map-elements-edited', {
      detail: { layers, originalEvent: event },
    }));
  }
  finishDraw() {
    if (this.map.pm.Draw.Polygon.enabled()) {
      this.map.pm.Draw.Polygon._finishShape();
    }

    this.dispatchLayerChanges(event)
  }
  _initDrawControl() {
    const editLayerName = this._getEditLayerName();
    this.map.pm.addControls({
      position: "topright",
      drawMarker: false,
      drawPolyline: false,
      drawRectangle: false,
      drawPolygon: false,
      drawCircle: false,
      drawText: false,
      drawCircleMarker: false,
      editMode: false,
      dragMode: false,
      cutPolygon: false,
      removalMode: false,
      rotateMode: false
    });

    // editMode and rotateMode as name would collide with default buttons
    this.map.pm.Toolbar.createCustomControl({
      name: "moveModeCustom",
      block: "custom",
      title: "Move Polygon",
      className: "leaflet-pm-icon-drag",
      toggle: false,
      onClick: () => {
        this.setMode(MyMap.Mode.MOVE);
      },
    });

    this.map.pm.Toolbar.createCustomControl({
      name: "rotatelModeCustom",
      block: "custom",
      title: "Rotate Polygon",
      className: "leaflet-pm-icon-rotate",
      toggle: false,
      onClick: () => {
        this.setMode(MyMap.Mode.ROTATE);
      },
    });
    this.map.pm.Toolbar.createCustomControl({
      name: "editModeCustom",
      block: "custom",
      title: "Edit Polygon Verticies",
      className: "leaflet-pm-icon-edit",
      toggle: false,
      onClick: () => {
        this.setMode(MyMap.Mode.EDIT);
      },
    });
    this.map.pm.Toolbar.createCustomControl({
      name: "cancelEditModeCustom",
      block: "custom",
      title: "Änderung rückgängig machen",
      className: "leaflet-pm-icon-delete",
      toggle: false,
      onClick: () => {
        this.disableDraw();
      },
    });
    this.map.pm.Toolbar.createCustomControl({
      name: "saveCurrentChanges",
      block: "custom",
      title: "Änderung Speichern",
      className: "leaflet-pm-icon-polygon",
      toggle: false,
      onClick: () => {
        this.finishDraw();
      },
    });
    // Do we want custom edit handlers?
    // changing the default css could also be an option
    // .leaflet-editing-icon {
    //     border-radius: 50%;
    // }

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
    this._vertexHistory = [];
    this._lastLayerState = new Map();
    const geometries = this.settings.getGeoJsons?.() ?? getGeoJsons();
    const popups = this.settings.getPopUps();
    const markers = this.settings.getMarkers();

    const editLayerName = this._getEditLayerName();


    this.map.pm.removeControls();
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
    geometries.forEach(({ geojson, layer: layer_name, id, style, key }) => {
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


      layers[id] = layer;
      layer.id = id;
      layer.key = key;
      layer.geojson = geojson;
      this.featureGroups[layer_name].addLayer(layer);

      if (layer_name === editLayerName) {
        this.isEditing = true;
        if (this.editingMode === "rotate") {
          layer.pm.enableRotate();
        } else if (this.editingMode === "move") {
          layer.pm.enableLayerDrag();
        } else {
          layer.pm.enable({ allowEditing: true });
          this._lastLayerState.set(layer, this._cloneLatLngs(layer.getLatLngs()));
          layer.on('pm:vertexadded', this._onVertexChange, this);
          layer.on('pm:vertexremoved', this._onVertexChange, this);
          layer.on('pm:markerdragend', this._onVertexChange, this);
        }

      }

    });


    // turn on markers and hovers, but only if no layer is in editable mode
    // since editing is not easy with hovers/icons etc
    if (!this.isEditing) {
      geometries.forEach(({ geojson, layer: layer_name, id, style, key }) => {
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

      if (this.map.pm.controlsVisible()) this.map.pm.toggleControls();
    } else {
      // # enable draw controls
      if (!this.map.pm.controlsVisible()) this.map.pm.toggleControls();
    }
  }



  fitToElements() {
    const bounds = L.latLngBounds();
    for (const featureGroup of Object.values(this.featureGroups)) {
      if (featureGroup.getLayers().length > 0) {
        bounds.extend(featureGroup.getBounds());
      }
    }
    if (bounds.isValid()) {
      this.map.fitBounds(bounds);
    }
  }

  toggleEditable() {
    this.featureGroups[this._getEditLayerName()].eachLayer((layer) => {
      if (layer.pm.enabled()) {
        layer.pm.disable();
      } else {
        layer.pm.enable(
          {
            allowDragging: true,
            allowEditing: false
          }
        );
      }
    });
  }

  _applyToEditableVertices(layer, transformFn) {
    const handler = layer.editing._verticesHandlers?.[0];
    if (handler?._markers) {
      handler._markers.forEach((marker) => {
        if (!marker._origLatLng) return;
        const [newLat, newLng] = transformFn(marker._origLatLng.lat, marker._origLatLng.lng);
        L.extend(marker._origLatLng, { lat: newLat, lng: newLng });
        marker.setLatLng([newLat, newLng]);
      });
      layer.redraw();
      handler.updateMarkers();
    } else {
      layer.setLatLngs([layer.getLatLngs()[0].map(ll => transformFn(ll.lat, ll.lng))]);
    }
  }

  shiftEditablePolygonLatLngs(d_lat, d_lng) {
    this.featureGroups[this._getEditLayerName()].eachLayer((layer) => {
      this._applyToEditableVertices(layer, (lat, lng) => [lat + d_lat, lng + d_lng]);
    });
  }

  rotateEditablePolygonLatLngs(angle) {
    this.featureGroups[this._getEditLayerName()].eachLayer((layer) => {
      const center = layer.getCenter();
      const rad = (angle * Math.PI) / 180;
      const cos = Math.cos(rad);
      const sin = Math.sin(rad);
      this._applyToEditableVertices(layer, (lat, lng) => {
        const dlat = lat - center.lat;
        const dlng = lng - center.lng;
        return [center.lat + dlat * cos - dlng * sin, center.lng + dlat * sin + dlng * cos];
      });
    });
  }



  _cloneLatLngs(latlngs) {
    // this handles ring like polygons (during editing)
    return latlngs.map(ring => ring.map(ll => L.latLng(ll.lat, ll.lng)))
  }

  _onVertexChange(e) {
    const layer = e.layer;
    const before = this._lastLayerState.get(layer);
    if (before) this._vertexHistory.push({ layer, latlngs: before });
    this._lastLayerState.set(layer, this._cloneLatLngs(layer.getLatLngs()));
  }



  undoLastNode() {
    if (this.map.pm.Draw.Polygon.enabled()) {
      // Private method which is usually exposed via button
      this.map.pm.Draw.Polygon._removeLastVertex();
      return;
    }
    if (this._vertexHistory.length === 0) return;
    const { layer, latlngs } = this._vertexHistory.pop();
    layer.setLatLngs(latlngs);
    this._lastLayerState.set(layer, this._cloneLatLngs(latlngs));
    layer.pm.disable();
    layer.pm.enable({ allowEditing: true });
  }

  _bindEvents() {
    let mouseStart = null;

    // this.map.on('mousedown', (e) => {
    //   if (!this.isEditing) return;
    //   mouseStart = e.latlng;
    // });
    //
    // this.map.on('mousemove', (e) => {
    //   if (!mouseStart) return;
    //   const dlat = mouseStart.lat - e.latlng.lat;
    //   const dlng = mouseStart.lng - e.latlng.lng;
    //   this.shiftEditablePolygonLatLngs(-dlat, -dlng);
    //   mouseStart = e.latlng;
    // });
    //
    // this.map.on('mouseup', () => {
    //   if (!mouseStart) return;
    //   mouseStart = null;
    // });

    document.addEventListener('finish-create-polygon', (event) => {
      this.finishDraw()
    });


    document.addEventListener('stop-create-polygon', (event) => {
      this.disableDraw();
    });
    this.map.on("pm:create", (event) => {
      this.map._container.dispatchEvent(new CustomEvent('some-map-element-created', {
        detail: { layer: event.layer, originalEvent: event },
      }));
      event.layer.remove();
      document.dispatchEvent(new CustomEvent('map-redraw'));
    });

    // Query all layers on Shift click event
    this.map.on('click', (e) => {

      const point = turf.point([e.latlng.lng, e.latlng.lat]); // note: turf uses [lng, lat]
      // we can iterate over all map layers directly but map.eachLayer also gives the base layers
      // which we want to avoid
      const layerNames = this.settings.getLayerNames?.() ?? getLayerNames();
      layerNames.forEach((lname) => {
        const featureGroup = this.featureGroups[lname];
        featureGroup.eachLayer((layer) => {
          const inside = turf.booleanPointInPolygon(point, layer.geojson);
          if (inside) {
            var event;
            if (e.originalEvent.shiftKey) {
              event = new CustomEvent('map-layer-shift-clicked', { detail: { value: layer.key }, bubbles: true });
            } else {
              // fire the select-instance-id event. the list item can listen for it and toggle its state accordingly
              event = new CustomEvent('select-instance-' + layer.key, { detail: { value: layer.key }, bubbles: true });
              // maybe fire different event later on or introduce middlestep
              // event = new CustomEvent('map-layer-clicked', { detail: { value: layer.key }, bubbles: true });


            }
            document.dispatchEvent(event);
          }
        });
      });

    });
  }
}
