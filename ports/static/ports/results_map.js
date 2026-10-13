function centroid(coords) {
    const pts = coords.filter((c, i) =>
        !(i > 0 && c[0] === coords[0][0] && c[1] === coords[0][1])
    );
    return [
        pts.reduce((s, c) => s + c[0], 0) / pts.length,
        pts.reduce((s, c) => s + c[1], 0) / pts.length,
    ];
}

function makeLabelIcon(name, opacity) {
    const shadow = '-1px -1px 0 #fff,1px -1px 0 #fff,-1px 1px 0 #fff,1px 1px 0 #fff';
    return L.divIcon({
        html: "<span style=\"display:inline-block;font-family:'Inter',sans-serif;font-weight:500;font-size:14px;color:#000;text-shadow:" + shadow + ";white-space:nowrap;transform:translate(-50%,-50%);opacity:" + opacity + "\">" + name + "</span>",
        className: 'map-label-icon',
        iconSize: [0, 0],
        iconAnchor: [0, 0],
    });
}

function showResultsMap(map_id, carto_api_token) {
    const map = L.map(map_id, { zoomControl: true });
    L.tileLayer(
        'https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png?key=' + carto_api_token, {
            attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors © <a href="https://carto.com/attributions">CARTO</a>',
            subdomains: 'abcd',
            maxZoom: 20,
        }
    ).addTo(map);

    const areas = JSON.parse(document.getElementById('areas').textContent);
    const layers = L.featureGroup();
    for (let area of areas) {
        // coordinates: latlng -> lnglat
        const coords = area.geom.coordinates[0].map(c => [c[1], c[0]]);
        let desc = '<div>';
        for (let [category, components] of Object.entries(area.components)) {
            desc += '<b>' + category + '</b><ul>';
            for (let component of components) {
                desc += '<li>' + component.name;
            }
            desc += '</ul>';
        }
        desc += '</div>';
        L.polygon(coords).bindPopup(desc).addTo(layers);
        L.marker(centroid(coords), {
            icon: makeLabelIcon(area.name, 1),
            interactive: false,
            zIndexOffset: 500,
        }).addTo(map);
    }
    layers.addTo(map);
    map.fitBounds(layers.getBounds());
}
