/** @param {Element} parent @param {string} selector @returns {NodeListOf<Element>} */
function getNodes(parent, selector) {
  return parent.querySelectorAll(selector);
}

/** @param {NodeList} nodelist @param {(el: Element) => boolean} filter @returns {Element[]} */
function filteredNodes(nodelist, filter) {
  return filtered = [...nodelist].filter(filter);
}

/** @param {Element[]} nodearray @param {string} key @param {(a: string, b: string) => number} comparefunction @returns {Element[]} */
function sortNodes(nodearray, key, comparefunction) {
  if (comparefunction) return nodearray.sort((a, b) => comparefunction(a.dataset[key], b.dataset[key]));
  return nodearray.sort(
    (a, b) =>
      a.dataset[key].localeCompare(b.dataset[key])
  );
}

/** @param {Element[]} nodes  */
function findSiblingNodes(nodes, parent) {
  // given an array of nodes and parents, find the sibling layer nodes
  var siblings = [];
  nodes.forEach((n) => {
    while (n.parentNode != parent) {
      n = n.parentNode;
    }
    siblings.push(n);
  })
  return siblings;
}

function searchName(search, parent) {
  var nodes = (getNodes(parent, '[data-name]'));
  const filterFunc = (n) => {
    return n.dataset['name'].toLowerCase().search(search.toLowerCase()) >= 0;
  };
  const all_siblings = parent.children;
  const filtered = filteredNodes(nodes, filterFunc);
  // in case the elements with the data-name are not direct children of the parent
  const filtered_siblings = findSiblingNodes(filtered, parent);
  [...all_siblings].forEach((n) => { n.style.display = 'none' });
  filtered_siblings.forEach((n) => { n.style.display = '' });
};


function sortContainer(parent, key) {
  const all_siblings = parent.children;
  var nodes = (getNodes(parent, `[data-${key}]`));
  console.assert(all_siblings.length == nodes.length, `Not all nodes have data-${key} set`)
  const sorted = sortNodes([...all_siblings], key,)
  // const sorted_siblings = findSiblingNodes(sorted, parent);
  // const sorted_siblings = findSiblingNodes(sorted, parent);
  if (sorted.length != all_siblings.length) {
    console.assert(false, 'Values got lost. Aborting sort for ' + key);
    return
  }
  parent.replaceChildren(...sorted)
};
