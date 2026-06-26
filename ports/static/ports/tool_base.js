// General javascript functionality contained to tool_base.html

// HX-inserted elements can animate on entrance. This is useful for showing changes/creation
// of elements. When changing the view via tab can retrigger the animation. This is unwanted behaviour
// This forces the animation to not retrigger
document.addEventListener("animationend", (event) => {
    if (!event.target.classList.contains('animate-once')) return;
    event.target.classList.add("!animate-none")

})

// Stop insertion of elements which should be unique
document.addEventListener("htmx:oobBeforeSwap", (event) => {
    // Check if the event has a uniqueid identifier
    const uniqueid = event.detail.fragment?.dataset?.uniqueid;
    if (!uniqueid) return;
    // If the unique id already exists in the dom prevent the swap, else return early
    if (document.getElementById(uniqueid)) {
        console.log("prevented swap for " + uniqueid);
        event.detail.shouldSwap = false;
    }
})

// oob-swaps which include inputs might want to preserve their frontend state.
// E.g. a checked input with label foo is replaced oob with a checkbox with an adjusted label foobar
// If the checkbox state was not part of the request, the server can not serve the input in the ckecked / unchecked state it was in. An standard htmx function hx-preserve exists but does not allow changing anything about the element, e.g. styling or disabled status. This is needed though to
// properly change the input based on permission state (disable input when user is not authorized)
// A more minimal approach then hx-preserve is to restore inputs value/checked status only.
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
}
