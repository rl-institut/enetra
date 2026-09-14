// Extension to allow javascript/client side caching of htmx server responses
// On dom elements witch trigger an htmx-request with `htmx-js-cache` as attribute
// It injects a previous server response and its headers when a get request is made repeatidly
function parseHeaders(rawHeaders) {
  const headers = new Headers()
  // iterate over rawstring splitting at new lines. this consumes the eol \r\n or just \n
  for (const line of rawHeaders.trim().split(/\r?\n/)) {
    // get the first index of ':'. Later ':' might exist in the value
    const idx = line.indexOf(':')
    if (idx === -1) continue
    // create header with key [0,idx[  and value ]idx,end]
    headers.append(line.slice(0, idx).trim(), line.slice(idx + 1).trim())
  }
  return headers
}
{
  let htmxApi;
  let cache;
  htmx.defineExtension('htmx-js-cache', {
    init(api) {
      htmxApi = api;
      cache = {};
    },
    onEvent(name, evt) {
      if (!evt.detail.elt.hasAttribute('htmx-js-cache')) return;
      switch (name) {
        // On configRequest, stop the normal process and manually continue without sending the request
        case 'htmx:configRequest':
          if (evt.detail.verb === "get") {
            // create a lookup key from path/url + params
            let path = evt.detail.path;
            let lookup = path + JSON.stringify(Object.fromEntries(evt.detail.formData.entries()));
            if (cache[lookup]) {
              evt.detail.path = '/static_val/'
              // The htmx request will not add extra query params. If params exists,
              // they will be put in the request.body with no effect on a static endpoint
              evt.detail.useUrlParams = false;
            }
            evt.detail.cache_lookup = lookup;
          }
          return true
        case 'htmx:beforeOnLoad':
          // On beforeOnLoad, before htmx processing of the response, swap the header and body
          // with cached values.
          // Do not cache non 200 responses
          if (evt.detail.xhr.status !== 200) {
            console.log('not caching non 200 responses')
            return true
          }
          var lookup = evt.detail.requestConfig.cache_lookup
          if (!lookup) return;
          if (!cache[lookup]) {
            let headers = parseHeaders(evt.detail.xhr.getAllResponseHeaders())
            cache[lookup] = [evt.detail.xhr.response, evt.detail.xhr.getAllResponseHeaders(), headers];
          } else {
            var [body, allHeaders, headers] = cache[lookup];
            evt.detail.serverResponse = body;
            evt.detail.xhr.getAllResponseHeaders = () => allHeaders;
            evt.detail.xhr.getResponseHeader = (name)=> headers.get(name);
          }
          break
      }
    }
  })
}
