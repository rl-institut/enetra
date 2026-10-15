// General javascript functionality contained to tool_base.html

// HX-inserted elements can animate on entrance. This is useful for showing changes/creation
// of elements. When changing the view via tab can retrigger the animation. This is unwanted behaviour
// This forces the animation to not retrigger
document.addEventListener("animationend", (event) => {
    if (!event.target.classList.contains('animate-once')) return;
    event.target.classList.add("!animate-none")

})


function rerouteServerEventsToElement(url) {
    // create a sse connection with the server. The server responds with events and messages,
    // which trigger events the dom can listen too
    const event_source = htmx.createEventSource(url);
    event_source.addEventListener("scenario_changed", e => {
        document.body.dispatchEvent(
            new CustomEvent("scenario_changed", {
                detail: e.data
            })
        );
    });
}

// Stop insertion of elements which should be unique
document.addEventListener("htmx:oobBeforeSwap", (event) => {
    // Check if the event has a uniqueid identifier
    const uniqueid = event.detail.fragment?.dataset?.uniqueid;
    if (!uniqueid) return;
    // If the unique id already exists in the dom prevent the swap, else return early
    if (document.getElementById(uniqueid)) {
        event.detail.shouldSwap = false;
    }
})

// oob-swaps which include inputs might want to preserve their frontend state.
// E.g. a checked input with label foo is replaced oob with a checkbox with an adjusted label foobar
// If the checkbox state was not part of the request, the server can not serve the input in the ckecked / unchecked state it was in. An standard htmx function hx-preserve exists but does not allow changing anything about the element, e.g. styling or disabled status. This is needed though to
// properly change the input based on permission state (disable input when user is not authorized)
// A more minimal approach than hx-preserve is to restore inputs value/checked status only.
// changing other attributes like disabled is still possible
document.addEventListener("htmx:oobBeforeSwap", (event) => {
    const fragment = event.detail.fragment;
    fragment.querySelectorAll("[preserve-val]").forEach(newEl => {
        if (!newEl.id) return;
        const oldEl = document.getElementById(newEl.id);
        if (oldEl) {
            newEl.value = oldEl.value;
            newEl.checked = oldEl.checked;
        }
    });
})

// Cotton list items used to be individual Alpine components (x-data + x-effect per row) just to
// dispatch these window events when (de)selected.Now the (c-list.index) delegates all input
// handling to a single listener and calls this helper directly instead.
function dispatchItemSelection(value, isSelected) {
    if (!value) return;
    window.dispatchEvent(new CustomEvent((isSelected ? 'item-selected-' : 'item-unselected-') + value, { detail: { value } }));
}

// The list indicates what is shown in the detailSidebar. Therefore the should only be a single
// selection active at a time. this function handles deselecting other Lists
function deselectOtherLists(element) {
    const all_lists = document.querySelectorAll('.cotton-list');
    const this_list = element.closest('.cotton-list')
    all_lists.forEach((list) => {
        if (list == this_list) return;
        list.dispatchEvent(new CustomEvent("deselect-all"))
    }
    )
}

function hideOtherDropDown(selector) {
    const this_element = document.querySelector(selector);
    const all_lists = document.querySelectorAll('.drop-down');
    all_lists.forEach((list) => {
        if (list == this_element) return;
        list.dispatchEvent(new CustomEvent("hide"))
    }
    )
};


// make child of div selects of class select2 select2 elements
document.addEventListener("htmx:afterSettle", (event) => {
    // fires on the swapped in elements. Search these elements for select2
    const root = event.target;
    const els = [...root.querySelectorAll('.make-select2')];
    if (root.matches?.('.make-select2')) els.unshift(root);   // root itself may match
    els.forEach(el => {
        // Search only the direct children, so there are not unintended select2
        const select_el = el.querySelector(':scope > select');
        $(select_el)?.select2({ width: "100%" })
    });
}
);


// give some functionality to c-list.items. since there might be hundreds, beeing efficient here improves render times
// this makes list items disptach the appropriate getdetails event as well as listen to select events
document.addEventListener("DOMContentLoaded", () => {
    const list_items = document.querySelectorAll(".cotton-list-item ")
    list_items.forEach((el) => addListItemFunctionality(el))
}
)

document.addEventListener("htmx:afterSwap", (event) => {
    const el = event.target;
    var list_items;
    if (el.classList.contains("cotton-list-item")) {
        list_items = [el]
    }
    else {
        list_items = el.querySelectorAll(".cotton-list-item ")
    }
    list_items.forEach((el) => addListItemFunctionality(el))
})

document.addEventListener("htmx:oobBeforeSwap", (event) => {
    console.log( 'added func')
    const el = event.target;
    var list_items;
    if (el.classList.contains("cotton-list-item")) {
        list_items = [el]
    }
    else {
        list_items = el.querySelectorAll(".cotton-list-item ")
    }
    list_items.forEach((el) => addListItemFunctionality(el))
})

function addListItemFunctionality(el) {
    if (el._added_list_item_func) return;

    function onclick() {
        if (event.target instanceof HTMLInputElement) el.dispatchEvent(new CustomEvent('getdetails', { bubbles: true }));
    }
    el.onclick = onclick;
    const internal_id = el.querySelector('input').value;
    console.assert(internal_id);
    window.addEventListener("select-instance-" + internal_id, () => { el.click(); })
    el._added_list_item_func = true
}
