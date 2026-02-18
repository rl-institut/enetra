{
	let htmxApi

	htmx.defineExtension('data', {
		init(api) { htmxApi = api },

		onEvent(name, evt) {
			let path = evt.detail.path ?? evt.detail.requestConfig?.path

			if (path?.startsWith('data:')) switch (name) {
				// On beforeRequest, stop the normal process and manually continue without sending the request
				case 'htmx:beforeRequest':
					htmxApi.triggerEvent(evt.detail.elt, 'htmx:beforeSend', evt.detail.responseInfo)
					evt.detail.xhr.onload()
					return false

				// On beforeSwap, replace the "request body" with the targeted template's innerHTML
				case 'htmx:beforeSwap':
					let call = path.slice(5)
					let data = eval(call)
					if (!data) console.error(`Requested data could not be generated using '${call}'`)
					else {
						evt.detail.shouldSwap = !!data
						evt.detail.serverResponse = JSON.stringify(data);
					}
					return true
			}
		}
	})
}
