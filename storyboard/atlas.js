"use strict";

const get = id => document.getElementById(id);
const readData = id => JSON.parse(get(id).textContent);
const {people, relationships, query, marriages = []} = readData("tree");
const events = readData("events");
const countries = readData("world");
const personByKey = new Map(people.map(person => [person.key, person]));
const startingPeople = people.filter(person => person.starting_profile);
if (query?.name || startingPeople.length) {
  get("query").hidden = false;
  get("query-label").textContent = query?.name ? "Search query" : "Starting profile";
  get("query-value").textContent = query?.name
    ? [query.name, query.birth_place].filter(Boolean).join(" · ")
    : startingPeople.map(person => person.name + " [" + person.key + "]").join(", ");
  if (query?.name && startingPeople.length) {
    get("query-note").hidden = false;
    get("query-note").textContent = "Selected result " + startingPeople.map(person => person.key).join(", ");
  } else if (!query) {
    get("query-note").hidden = false;
    get("query-note").textContent = "The original search was not saved in this export.";
  }
}
const records = new Map(people.map(person => [person.key, []]));
for (const event of events) records.get(event.profile)?.push(event);

const yearOf = value => /^[1-9]\d{3}-/.test(value ?? "") ? Number(value.slice(0, 4)) : null;
const aliveIn = (person, year) => {
  const birth = yearOf(person.birth), death = yearOf(person.death);
  return birth !== null && death !== null && birth <= year && year <= death;
};

function nameWithAge(person, year) {
  return aliveIn(person, year) ? person.name + " · " + (year - yearOf(person.birth)) : person.name;
}

function locationAt(key, year) {
  const places = (records.get(key) ?? []).filter(record => record.place && yearOf(record.date) <= year);
  return places.at(-1) ?? null;
}

function locationLabel(record, year) {
  if (!record) return "Location unknown";
  const date = yearOf(record.date);
  if (date === year) return record.place + " · recorded this year";
  return record.place + " · last recorded " + date;
}

const scrub = get("scrub");
const canvas = get("map"), ctx = canvas.getContext("2d");
const years = [...new Set(events.map(event => yearOf(event.date)).filter(year => year !== null))].sort((a,b) => a-b);
scrub.min = years[0] ?? 0;
scrub.max = years.at(-1) ?? 0;
scrub.value = scrub.min;
scrub.disabled = years.length < 2;
get("min-year").textContent = years[0] ?? "";
get("max-year").textContent = years.at(-1) ?? "";
let selected = null, galleryKey = null, mapView = "locations";
let manualMapFrame = null, currentMapFrame = null, currentMapPeople = [];
let mapDrag = null, mapDragged = false, mapDrawScheduled = false;
let hits = [];
let selectedGroup = new Set();
let treeButtons = new Map();
const worldBounds = [-180, -58, 180, 83];
const labels = [
  ["North Atlantic", -44, 42], ["Pacific Ocean", -137, 8], ["Indian Ocean", 79, -22],
  ["Canada", -108, 58], ["United States", -103, 43], ["South America", -58, -12],
  ["Europe", 16, 56], ["Africa", 22, 8], ["Asia", 94, 42], ["Australia", 134, -24],
];

const sparseMapPadding = {
  city: [.17, .12], township: [.35, .25], state: [2, 1.4], region: [3, 2], country: [5, 3.4],
};

function mapBounds(points, focusPerson = false, precision) {
  if (mapView === "world" || !points.length) return worldBounds;
  const lon = points.map(point => point[0]), lat = points.map(point => point[1]);
  const left = Math.min(...lon), right = Math.max(...lon);
  const bottom = Math.min(...lat), top = Math.max(...lat);
  let padding = focusPerson ? [.65, .45] : [8, 5];
  if (focusPerson && new Set(points.map(point => point.join(","))).size <= 2) {
    padding = sparseMapPadding[precision] ?? sparseMapPadding.region;
  }
  const xPad = Math.max(padding[0], (right - left) * .15);
  const yPad = Math.max(padding[1], (top - bottom) * .19);
  if (right - left > 220) return worldBounds;
  const x = (left + right) / 2, y = (bottom + top) / 2;
  const width = Math.max(right - left + xPad * 2, (top - bottom + yPad * 2) * canvas.width / canvas.height);
  const height = width * canvas.height / canvas.width;
  return [x - width / 2, y - height / 2, x + width / 2, y + height / 2];
}

const mapPalettes = {
  light: {water:"#e5eef8", grid:"#ccd9e7", land:"#fafbfd", border:"#9caec1", label:"#445972",
    selected:"#204faf", recorded:"#547490", unknown:"#fff", stroke:"#3c5877", selectedStroke:"#fff", digits:"#fff", hollowDigits:"#243a52"},
  dark: {water:"#172b42", grid:"#304963", land:"#26394e", border:"#637e9d", label:"#d0dff1",
    selected:"#a6c5ff", recorded:"#9cbedb", unknown:"#172b42", stroke:"#a7bbd5", selectedStroke:"#fff", digits:"#162235", hollowDigits:"#f0f5fd"},
};
let theme = "light";
try {
  theme = localStorage.getItem("family-atlas-theme") === "dark" ? "dark" : "light";
} catch (_) {
  // Some browsers disable localStorage for files opened directly.
}
function applyTheme() {
  document.documentElement.dataset.theme = theme;
  get("theme-toggle").checked = theme === "dark";
}
applyTheme();
get("theme-toggle").onchange = event => {
  theme = event.currentTarget.checked ? "dark" : "light";
  try { localStorage.setItem("family-atlas-theme", theme); } catch (_) {}
  applyTheme();
  render();
};

function drawPointYears(points, palette, width, height) {
  const placed = [];
  const collides = (a, b) => a.left < b.right && a.right > b.left && a.top < b.bottom && a.bottom > b.top;
  const markers = points.map(({x, y, radius}) => ({
    left: x - radius, right: x + radius, top: y - radius, bottom: y + radius,
  }));
  ctx.font = "600 16px system-ui";
  ctx.textAlign = "left";
  for (const point of points.sort((a, b) => b.priority - a.priority || a.x - b.x || a.y - b.y)) {
    const {x, y, radius, years, priority} = point;
    const dates = [...years].sort((a, b) => a - b);
    if (!dates.length) continue;
    const label = dates.slice(0, 2).join(", ") + (dates.length > 2 ? " +" + (dates.length - 2) : "");
    const w = Math.ceil(ctx.measureText(label).width) + 12, h = 22, inset = radius + 5;
    const options = [[x - w / 2, y - inset - h], [x + inset, y - h / 2],
      [x - w / 2, y + inset], [x - inset - w, y - h / 2]];
    for (const [left, top] of options) {
      const box = {left, top, right: left + w, bottom: top + h};
      if (left < 3 || top < 3 || box.right > width - 3 || box.bottom > height - 3 ||
          placed.some(other => collides(box, other)) || markers.some(marker => collides(box, marker))) continue;
      ctx.fillStyle = palette.unknown;
      ctx.fillRect(left, top, w, h);
      ctx.fillStyle = priority ? palette.selected : palette.label;
      ctx.fillText(label, left + 6, top + 16);
      placed.push(box);
      break;
    }
  }
}

function draw(year, roster) {
  currentMapPeople = roster;
  const palette = mapPalettes[theme];
  const locations = new Map();
  for (const person of roster) {
    const record = locationAt(person.key, year);
    if (record?.coords) locations.set(person.key, record);
  }
  const activePerson = personByKey.get(selected);
  const highlight = activePerson && aliveIn(activePerson, year) ? locationAt(selected, year) : null;
  if (highlight?.coords) locations.set(selected, highlight);
  const route = [];
  for (const record of records.get(selected) ?? []) {
    if (!record.coords) continue;
    const last = route.at(-1);
    if (!last || record.coords[0] !== last.coords[0] || record.coords[1] !== last.coords[1]) route.push(record);
  }
  const routeShown = selectedGroup.size < 2 && route.length > 1;
  const points = [...locations.values(), ...(routeShown ? route : [])].map(record => record.coords);
  const personAvailable = selectedGroup.size < 2 && route.length > 0;
  const fitPerson = mapView === "person" && personAvailable;
  const personButton = get("fit-person");
  personButton.disabled = !personAvailable;
  personButton.setAttribute("aria-pressed", String(fitPerson && !manualMapFrame));
  get("fit").setAttribute("aria-pressed", String(!manualMapFrame && (mapView === "locations" || (mapView === "person" && !personAvailable))));
  get("world-view").setAttribute("aria-pressed", String(mapView === "world" && !manualMapFrame));
  const framingPoints = fitPerson ? route.map(record => record.coords) : points;
  const precisionOrder = ["country", "region", "state", "township", "city"];
  const leastPrecise = precisionOrder.find(level => route.some(record => record.precision === level));
  currentMapFrame = manualMapFrame ?? mapBounds(framingPoints, fitPerson, leastPrecise);
  const [left, bottom, right, top] = currentMapFrame;
  const width = canvas.width, height = canvas.height;
  const project = ([lon, lat]) => [(lon-left)/(right-left)*width, (top-lat)/(top-bottom)*height];

  ctx.fillStyle = palette.water;
  ctx.fillRect(0, 0, width, height);
  ctx.fillStyle = palette.land;
  ctx.strokeStyle = palette.border;
  for (const [, rings] of countries) {
    for (const ring of rings) {
      ctx.beginPath();
      ring.forEach((point, i) => ctx[i ? "lineTo" : "moveTo"](...project(point)));
      ctx.closePath(); ctx.fill(); ctx.stroke();
    }
  }
  ctx.lineWidth = 1;
  ctx.strokeStyle = palette.grid;
  const span = right - left;
  const grid = [0.05, .1, .25, .5, 1, 2, 5, 10, 20, 40].find(step => span / step <= 12) ?? 40;
  ctx.beginPath();
  for (let lon = Math.ceil(left/grid)*grid; lon <= right; lon += grid) {
    ctx.moveTo(...project([lon, bottom])); ctx.lineTo(...project([lon, top]));
  }
  for (let lat = Math.ceil(bottom/grid)*grid; lat <= top; lat += grid) {
    ctx.moveTo(...project([left, lat])); ctx.lineTo(...project([right, lat]));
  }
  ctx.stroke();
  ctx.font = "15px system-ui";
  ctx.fillStyle = palette.label;
  ctx.textAlign = "center";
  for (const [name, lon, lat] of labels) ctx.fillText(name, ...project([lon, lat]));

  const yearLabels = new Map();
  function addYearLabel(record, radius, priority) {
    const key = record.coords.join(",");
    if (!yearLabels.has(key)) {
      const [x, y] = project(record.coords);
      yearLabels.set(key, {x, y, radius, priority, years: new Set()});
    }
    const label = yearLabels.get(key), recordYear = yearOf(record.date);
    label.radius = Math.max(label.radius, radius);
    label.priority = Math.max(label.priority, priority);
    if (recordYear !== null) label.years.add(recordYear);
  }
  if (routeShown) {
    for (const record of records.get(selected) ?? []) {
      if (record.coords) addYearLabel(record, 3, 1);
    }
    ctx.strokeStyle = palette.selected;
    ctx.lineWidth = 2.5;
    ctx.lineCap = "round";
    ctx.setLineDash([1, 7]);
    ctx.beginPath();
    route.forEach((record, index) => {
      const last = route[index - 1];
      const crossesDateLine = last && Math.abs(last.coords[0] - record.coords[0]) > 180;
      ctx[index && !crossesDateLine ? "lineTo" : "moveTo"](...project(record.coords));
    });
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.lineCap = "butt";
    for (const record of route) {
      ctx.beginPath();
      ctx.arc(...project(record.coords), 3, 0, Math.PI * 2);
      ctx.fillStyle = palette.selected;
      ctx.fill();
    }
  }

  const pointsByPlace = new Map();
  for (const [key, record] of locations) {
    const place = record.coords.join(",");
    if (!pointsByPlace.has(place)) pointsByPlace.set(place, []);
    pointsByPlace.get(place).push({key, record});
  }
  hits = [];
  for (const group of pointsByPlace.values()) {
    const [x,y] = project(group[0].record.coords);
    const active = group.some(item => item.key === selected);
    const recorded = group.some(item => yearOf(item.record.date) === year);
    const radius = active ? 11 : group.length > 1 ? 10 : 7;
    ctx.beginPath();
    ctx.arc(x, y, radius, 0, Math.PI * 2);
    ctx.fillStyle = active ? palette.selected : recorded ? palette.recorded : palette.unknown;
    ctx.fill();
    ctx.strokeStyle = active ? palette.selectedStroke : palette.stroke;
    ctx.lineWidth = 2;
    ctx.stroke();
    if (group.length > 1) {
      ctx.fillStyle = active || recorded ? palette.digits : palette.hollowDigits;
      ctx.font = "bold 12px system-ui";
      ctx.fillText(group.length, x, y + 4);
    }
    if (active && selectedGroup.size < 2) {
      const selection = group.find(item => item.key === selected);
      addYearLabel(selection.record, radius, 2);
    }
    hits.push({x, y, radius, keys: group.map(item => item.key)});
  }
  drawPointYears([...yearLabels.values()], palette, width, height);
  const precisionLabels = {
    city: "city or town", township: "township approximation", state: "state approximation", region: "regional approximation", country: "country approximation",
  };
  const precision = highlight?.coords && precisionLabels[highlight.precision];
  const pinNote = precision ? " Selected pin: " + precision + "." : "";
  const groupNote = selectedGroup.size > 1 ? " Select a person to see their life-record trail." : "";
  get("map-note").textContent = points.length
    ? "Filled markers use this year’s records. Hollow markers use earlier records."
      + (yearLabels.size ? " Labels show the selected person’s record years." : "")
      + pinNote + groupNote + (routeShown ? " Dots connect places from their life records. The route is unknown." : "")
    : "No mapped locations for this selection. Any recorded place names still appear in the roster and evidence.";
}

const imageDetails = get("images");
function prepareGallery(person) {
  if (galleryKey === person.key) return;
  galleryKey = person.key;
  imageDetails.open = false;
  imageDetails.hidden = !person.photos.length;
  get("image-count").textContent = person.photos.length ? "(" + person.photos.length + ")" : "";
  get("gallery").replaceChildren();
}

imageDetails.ontoggle = () => {
  const person = personByKey.get(galleryKey), gallery = get("gallery");
  if (!imageDetails.open || !person || gallery.childElementCount) return;
  for (const photo of person.photos) {
    const figure = document.createElement("figure");
    const caption = document.createElement("figcaption");
    const source = document.createElement("a");
    source.href = photo.source;
    source.target = "_blank";
    source.rel = "noopener";
    source.textContent = photo.title;
    if (photo.image || photo.preview) {
      const image = document.createElement("img");
      image.alt = photo.title;
      image.loading = "lazy";
      image.referrerPolicy = "no-referrer";
      image.onerror = () => {
        if (photo.preview && image.src !== photo.preview) image.src = photo.preview;
        else image.remove();
      };
      image.src = photo.image || photo.preview;
      figure.append(image);
    }
    caption.append(source);
    figure.append(caption);
    gallery.append(figure);
  }
}

function renderBiography(person) {
  const biography = get("biography");
  biography.replaceChildren();
  get("biography-section").hidden = !person.biography.length;
  if (!person.biography.length) return;
  const source = person.biography_source;
  get("biography-source").href = source.profile_url;
  const retrieved = source.retrieved_at?.slice(0,10);
  get("biography-date").textContent = retrieved ? " · Retrieved " + retrieved : "";
  for (const text of person.biography) {
    const paragraph = document.createElement("p");
    paragraph.textContent = text;
    biography.append(paragraph);
  }
}

function renderCitations(person) {
  const bibliography = get("bibliography");
  bibliography.replaceChildren();
  get("research-notes").hidden = !person.bibliography.length;
  for (const source of person.bibliography) {
    const item = document.createElement("li"), link = document.createElement("a");
    link.href = source.url;
    link.target = "_blank";
    link.rel = "noopener";
    link.textContent = source.label;
    item.append(link);
    bibliography.append(item);
  }
}

const treeViewport = get("roster");
const treeAliveOnly = get("tree-alive-only");
const treeSpace = get("tree-space"), treeLayer = get("tree-layer");
let treeZoom = 1, treeWidth = 0, treeHeight = 0;
let treeInsetX = 0, treeInsetY = 0, mapCollapsed = false;
let treePositioned = false, treeDrag = null, cancelTreeClick = false;
const svgNamespace = "http://www.w3.org/2000/svg";

const treePreview = get("tree-preview");

function dismissTreePreview() {
  treePreview.hidden = true;
}

function showTreePreview(button, person, year, lifespan) {
  if (treeZoom >= .85 || treeDrag?.moved) return;
  const card = button.getBoundingClientRect();
  const area = treeViewport.getBoundingClientRect();
  if (card.left + card.width <= area.left || card.left >= area.left + area.width ||
      card.top + card.height <= area.top || card.top >= area.top + area.height) return;
  get("tree-preview-name").textContent = nameWithAge(person, year);
  get("tree-preview-dates").textContent = lifespan;
  treePreview.hidden = false;
  const screenWidth = document.documentElement.clientWidth, screenHeight = document.documentElement.clientHeight;
  const {width, height} = treePreview.getBoundingClientRect();
  const gap = 8;
  const right = card.left + card.width + gap;
  const preferredX = right + width <= screenWidth - gap ? right : card.left - width - gap;
  treePreview.style.left = Math.max(gap, Math.min(preferredX, screenWidth - width - gap)) + "px";
  treePreview.style.top = Math.max(gap, Math.min(card.top, screenHeight - height - gap)) + "px";
}

document.addEventListener("scroll", dismissTreePreview, true);
document.addEventListener("keydown", event => {
  if (event.key === "Escape") dismissTreePreview();
});

function scaleTree(zoom, preserveCenter = true) {
  dismissTreePreview();
  const width = treeViewport.clientWidth, height = treeViewport.clientHeight;
  const ratio = zoom / treeZoom;
  const x = (treeViewport.scrollLeft + width / 2 - treeInsetX) * ratio;
  const y = (treeViewport.scrollTop + height / 2 - treeInsetY) * ratio;
  treeZoom = zoom;
  treeInsetX = mapCollapsed ? Math.max(0, (width - treeWidth * zoom) / 2) : 0;
  treeInsetY = mapCollapsed ? Math.max(0, (height - treeHeight * zoom) / 2) : 0;
  treeLayer.style.transform = "scale(" + zoom + ")";
  treeSpace.style.width = treeWidth * zoom + "px";
  treeSpace.style.height = treeHeight * zoom + "px";
  treeSpace.style.marginLeft = treeInsetX + "px";
  treeSpace.style.marginTop = treeInsetY + "px";
  if (preserveCenter) {
    treeViewport.scrollLeft = Math.max(0, x + treeInsetX - width / 2);
    treeViewport.scrollTop = Math.max(0, y + treeInsetY - height / 2);
  }
  updateProfilePlacement();
}

function focusTreeNode(key) {
  const button = treeButtons.get(key);
  if (!button) return;
  const left = button.offsetLeft * treeZoom + treeInsetX, top = button.offsetTop * treeZoom + treeInsetY;
  treeViewport.scrollLeft = Math.max(0, left - (treeViewport.clientWidth - button.offsetWidth * treeZoom) / 2);
  treeViewport.scrollTop = Math.max(0, top - (treeViewport.clientHeight - button.offsetHeight * treeZoom) / 2);
}

get("tree-plus").onclick = () => scaleTree(Math.min(2, treeZoom * 1.2));
get("tree-minus").onclick = () => scaleTree(Math.max(.05, treeZoom / 1.2));
function fitTree() {
  const width = treeViewport.clientWidth, height = treeViewport.clientHeight;
  if (!treeWidth || !treeHeight || width < 25 || height < 25) return;
  const padding = mapCollapsed ? 24 : 0;
  const fit = Math.min(1, (width - padding) / treeWidth, (height - padding) / treeHeight);
  scaleTree(fit, false);
  treeViewport.scrollLeft = 0;
  treeViewport.scrollTop = 0;
}
get("tree-fit").onclick = fitTree;
treeViewport.onpointerdown = event => {
  if (event.pointerType === "mouse" && event.button === 0) {
    cancelTreeClick = false;
    treeDrag = {id:event.pointerId, x:event.clientX, y:event.clientY, left:treeViewport.scrollLeft, top:treeViewport.scrollTop};
  }
};
treeViewport.onpointermove = event => {
  if (treeDrag?.id !== event.pointerId) return;
  const x = event.clientX - treeDrag.x, y = event.clientY - treeDrag.y;
  if (!cancelTreeClick && Math.hypot(x, y) < 5) return;
  if (!cancelTreeClick) treeViewport.setPointerCapture(event.pointerId);
  dismissTreePreview();
  treeDrag.moved = true;
  cancelTreeClick = true;
  treeViewport.classList.add("is-dragging");
  treeViewport.scrollLeft = treeDrag.left - x;
  treeViewport.scrollTop = treeDrag.top - y;
};
function endTreeDrag() {
  treeDrag = null;
  treeViewport.classList.remove("is-dragging");
}
treeViewport.onpointerup = endTreeDrag;
treeViewport.onpointercancel = endTreeDrag;
treeViewport.onlostpointercapture = endTreeDrag;

function addCrossingGaps(edges, routes, width, height) {
  const horizontal = [], vertical = [];
  for (const route of routes) {
    const [, initialX, initialY, ...steps] = route.geometry.split(" ");
    let x = Number(initialX), y = Number(initialY);
    for (let index = 0; index < steps.length; index += 2) {
      const end = Number(steps[index + 1]);
      if (steps[index] === "H") {
        if (end !== x) horizontal.push({route, y, left: Math.min(x, end), right: Math.max(x, end)});
        x = end;
      } else {
        if (end !== y) vertical.push({route, x, top: Math.min(y, end), bottom: Math.max(y, end)});
        y = end;
      }
    }
  }
  const gaps = new Map();
  for (const across of horizontal) {
    for (const down of vertical) {
      if (across.route.family === down.route.family || down.x <= across.left || down.x >= across.right ||
          across.y <= down.top || across.y >= down.bottom) continue;
      const under = across.route.related && !down.route.related ? down.route : across.route;
      if (!gaps.has(under)) gaps.set(under, new Map());
      gaps.get(under).set(down.x + "," + across.y, [down.x, across.y]);
    }
  }
  if (!gaps.size) return;
  const definitions = document.createElementNS(svgNamespace, "defs");
  for (const [route, points] of gaps) {
    const id = "tree-crossover-" + route.index;
    const mask = document.createElementNS(svgNamespace, "mask");
    for (const [key, value] of Object.entries({id, x: 0, y: 0, width, height, maskUnits: "userSpaceOnUse", maskContentUnits: "userSpaceOnUse"})) {
      mask.setAttribute(key, value);
    }
    const field = document.createElementNS(svgNamespace, "rect");
    field.setAttribute("width", width);
    field.setAttribute("height", height);
    field.style.fill = "white";
    field.style.stroke = "none";
    mask.append(field);
    for (const [x, y] of points.values()) {
      const cut = document.createElementNS(svgNamespace, "circle");
      cut.setAttribute("cx", x);
      cut.setAttribute("cy", y);
      cut.setAttribute("r", 4.5);
      cut.style.fill = "black";
      cut.style.stroke = "none";
      mask.append(cut);
    }
    definitions.append(mask);
    route.path.setAttribute("mask", "url(#" + id + ")");
  }
  edges.append(definitions);
}

function renderTree(scope, year) {
  dismissTreePreview();
  const focusedKey = treeViewport.contains(document.activeElement) ? document.activeElement.dataset.key : null;
  const visible = new Set(scope.map(person => person.key));
  const parents = new Map(people.map(person => [person.key, []]));
  const childCounts = new Map(people.map(person => [person.key, 0]));
  const familyLinks = relationships.filter(([parent, child]) => parents.has(parent) && parents.has(child));
  for (const [parent, child] of familyLinks) {
    parents.get(child).push(parent);
    childCounts.set(parent, childCounts.get(parent) + 1);
  }

  const compare = (a, b) => {
    const first = personByKey.get(a), second = personByKey.get(b);
    return (yearOf(first.birth) ?? Infinity) - (yearOf(second.birth) ?? Infinity) || first.name.localeCompare(second.name);
  };
  // Assign generations from the youngest relatives upward before applying the year filter.
  const generations = [], placed = new Set();
  let pending = people.map(person => person.key).filter(key => childCounts.get(key) === 0);
  while (pending.length) {
    generations.push(pending.sort(compare));
    const next = [];
    for (const key of pending) {
      placed.add(key);
      for (const parent of parents.get(key)) {
        childCounts.set(parent, childCounts.get(parent) - 1);
        if (!childCounts.get(parent)) next.push(parent);
      }
    }
    pending = next;
  }
  const leftovers = people.map(person => person.key).filter(key => !placed.has(key));
  if (leftovers.length) generations.push(leftovers.sort(compare));
  const groups = new Map();
  const family = keys => {
    const members = [...new Set(keys)].sort();
    const id = members.join("|");
    if (!groups.has(id)) groups.set(id, {id, members, children: [], marriage: null});
    return groups.get(id);
  };
  for (const [child, entries] of parents) {
    const distinct = [...new Set(entries)];
    if (distinct.length && distinct.length <= 2) family(distinct).children.push(child);
    else for (const parent of distinct) family([parent]).children.push(child);
  }
  for (const marriage of marriages) {
    const group = groups.get([marriage.first, marriage.second].sort().join("|"));
    if (group) group.marriage = marriage;
  }
  const orderedGroups = [...groups.values()].sort((a, b) => a.id.localeCompare(b.id));
  const partnership = new Map();
  for (const group of orderedGroups) {
    if (group.members.length !== 2) continue;
    const [first, second] = group.members;
    if (!partnership.has(first)) partnership.set(first, second);
    if (!partnership.has(second)) partnership.set(second, first);
  }
  const childRanks = new Map();
  const descendantsByParent = new Map(people.map(person => [person.key, []]));
  for (const [parent, child] of familyLinks) descendantsByParent.get(parent).push(child);
  const orderedGenerations = generations.map(row => {
    const available = new Set(row), blocks = [];
    for (const key of row) {
      if (!available.delete(key)) continue;
      const block = [key], partner = partnership.get(key);
      if (available.delete(partner)) block.push(partner);
      const childPositions = block.flatMap(person => descendantsByParent.get(person))
        .map(child => childRanks.get(child)).filter(position => position !== undefined);
      const target = childPositions.length
        ? childPositions.reduce((sum, position) => sum + position, 0) / childPositions.length
        : (blocks.length + .5) / row.length;
      blocks.push({block, target});
    }
    blocks.sort((a, b) => a.target - b.target || compare(a.block[0], b.block[0]));
    const ordered = blocks.flatMap(group => group.block);
    ordered.forEach((key, index) => childRanks.set(key, (index + .5) / ordered.length));
    return ordered;
  });
  const rows = orderedGenerations.reverse().map(row => row.filter(key => visible.has(key))).filter(row => row.length);
  const ranks = new Map(rows.flatMap((row, rank) => row.map(key => [key, rank])));
  const lanesByRank = rows.map(() => []);
  for (const group of orderedGroups) {
    const shown = group.members.filter(key => ranks.has(key));
    if (!shown.length) continue;
    const rank = Math.max(...shown.map(key => ranks.get(key)));
    const children = group.children.filter(key => ranks.has(key) && ranks.get(key) > rank);
    if (children.length) {
      lanesByRank[rank].push({...group, shown, children, rank});
    }
  }

  const edges = document.createElementNS(svgNamespace, "svg");
  edges.setAttribute("class", visible.has(selected) ? "tree-edges has-selection" : "tree-edges");
  edges.setAttribute("aria-hidden", "true");
  treeButtons = new Map();
  treeLayer.replaceChildren(edges);
  for (const key of rows.flat()) {
    const person = personByKey.get(key), isAlive = aliveIn(person, year);
    const button = document.createElement("button");
    button.type = "button";
    button.dataset.key = key;
    button.dataset.alive = String(isAlive);
    button.setAttribute("aria-pressed", String(key === selected || selectedGroup.has(key)));
    if (key === selected) button.setAttribute("aria-current", "true");
    const name = document.createElement("strong"), subtitle = document.createElement("small");
    name.textContent = person.name;
    if (isAlive) {
      const age = document.createElement("span");
      age.className = "tree-age";
      age.textContent = "· " + (year - yearOf(person.birth));
      name.append(" ", age);
    }
    subtitle.textContent = (yearOf(person.birth) ?? "?") + "–" + (yearOf(person.death) ?? "?");
    button.title = isAlive ? locationLabel(locationAt(key, year), year) : person.name;
    button.append(name, subtitle);
    const preview = () => showTreePreview(button, person, year, subtitle.textContent);
    button.onpointerenter = event => {
      if (event.pointerType !== "touch") preview();
    };
    button.onpointerleave = dismissTreePreview;
    button.onfocus = preview;
    button.onblur = dismissTreePreview;
    button.onclick = event => {
      if (cancelTreeClick && event.detail) return;
      selectPerson(key);
    };
    treeLayer.append(button);
    treeButtons.set(key, button);
  }
  const nodeWidth = 158, stepX = 181, inset = 18;
  const nodeHeight = Math.max(64, ...[...treeButtons.values()].map(button => button.offsetHeight || 0));
  const nodeAreaWidth = Math.max(1, ...rows.map(row => row.length)) * stepX + inset;
  const positions = new Map();
  let top = inset;
  rows.forEach((row, rank) => {
    row.forEach((key, column) => {
      positions.set(key, [(nodeAreaWidth - row.length * stepX) / 2 + column * stepX + 10, top]);
    });
    top += nodeHeight + Math.max(35, 18 + lanesByRank[rank].length * 22);
  });
  for (const [key, [x, y]] of positions) {
    const button = treeButtons.get(key);
    button.style.minHeight = nodeHeight + "px";
    button.style.left = x + "px";
    button.style.top = y + "px";
  }

  const center = key => positions.get(key)[0] + nodeWidth / 2;
  let outside = nodeAreaWidth;
  const exteriorLane = () => { outside += 10; return outside; };
  const families = lanesByRank.flat();
  const ports = new Map();
  for (const group of families) {
    for (const key of group.shown) {
      if (!ports.has(key)) ports.set(key, []);
      ports.get(key).push(group.id);
    }
  }
  const routes = [];
  function addPath(groupNode, route, family, related) {
    const path = document.createElementNS(svgNamespace, "path");
    path.setAttribute("d", route);
    routes.push({path, geometry: route, family, related, index: routes.length});
    groupNode.append(path);
  }
  for (const [rank, groupsAtRank] of lanesByRank.entries()) {
    groupsAtRank.forEach((group, lane) => {
      const junctionX = group.shown.reduce((sum, key) => sum + center(key), 0) / group.shown.length;
      const junctionY = positions.get(rows[rank][0])[1] + nodeHeight + 13 + lane * 22;
      const groupNode = document.createElementNS(svgNamespace, "g");
      groupNode.setAttribute("data-family", group.id);
      groupNode.setAttribute("data-parents", group.shown.join("|"));
      groupNode.setAttribute("data-children", group.children.join("|"));
      const recordedMarriage = group.marriage && (group.marriage.date || group.marriage.place);
      const marriageShown = group.shown.length === 2 && recordedMarriage;
      const status = marriageShown ? "Recorded marriage" : group.shown.length === 2
        ? (group.marriage ? "WikiTree spouse link" : "Co-parents") : "One parent shown";
      groupNode.setAttribute("data-kind", status);
      const related = [...group.shown, ...group.children].includes(selected);
      if (related) groupNode.setAttribute("class", "related");
      const connect = route => addPath(groupNode, route, group.id, related);
      const title = document.createElementNS(svgNamespace, "title");
      title.textContent = status + (marriageShown && group.marriage.date ? " · " + eventDateLabel(group.marriage.date) : "")
        + " · " + group.shown.map(key => personByKey.get(key).name).join(" and ");
      groupNode.append(title);
      for (const key of group.shown) {
        const slots = ports.get(key);
        const from = center(key) + (slots.indexOf(group.id) - (slots.length - 1) / 2) * Math.min(12, 90 / slots.length);
        const start = positions.get(key)[1] + nodeHeight;
        if (ranks.get(key) === rank) {
          connect(`M ${from} ${start} V ${junctionY} H ${junctionX}`);
        } else {
          const laneX = exteriorLane();
          connect(`M ${from} ${start} V ${start + 8} H ${laneX} V ${junctionY} H ${junctionX}`);
        }
      }
      for (const key of group.children) {
        const to = center(key), end = positions.get(key)[1];
        const branchY = junctionY + 8;
        if (ranks.get(key) === rank + 1) {
          connect(`M ${junctionX} ${junctionY} V ${branchY} H ${to} V ${end}`);
        } else {
          const laneX = exteriorLane();
          connect(`M ${junctionX} ${junctionY} V ${branchY} H ${laneX} V ${end - 8} H ${to} V ${end}`);
        }
      }
      const marker = document.createElementNS(svgNamespace, marriageShown ? "rect" : "circle");
      if (marriageShown) {
        marker.setAttribute("x", junctionX - 4);
        marker.setAttribute("y", junctionY - 4);
        marker.setAttribute("width", 8);
        marker.setAttribute("height", 8);
        marker.setAttribute("class", "marriage");
      } else {
        marker.setAttribute("cx", junctionX);
        marker.setAttribute("cy", junctionY);
        marker.setAttribute("r", 3);
      }
      groupNode.append(marker);
      edges.append(groupNode);
    });
  }
  treeWidth = outside + 12;
  treeHeight = top;
  edges.setAttribute("width", treeWidth);
  edges.setAttribute("height", treeHeight);
  addCrossingGaps(edges, routes, treeWidth, treeHeight);
  scaleTree(treeZoom, false);
  if (!treePositioned) {
    treePositioned = true;
    focusTreeNode(selected);
  }
  treeButtons.get(focusedKey)?.focus({preventScroll: true});
}

let peopleView = "list";
const listPanel = get("people-list");
const treePanel = get("family-tree"), peoplePanel = get("people-panel");
let peopleCollapsed = false;

function updateProfilePlacement() {
  const width = treeViewport.clientWidth, height = treeViewport.clientHeight;
  const needsFullWidth = peopleView === "tree" && !peopleCollapsed && !mapCollapsed && width > 0 && height > 0 &&
    (treeWidth * treeZoom > width + 2 || treeHeight * treeZoom > height + 2);
  stage.classList.toggle("tree-detail-below", needsFullWidth);
}

function setPeopleView(view) {
  dismissTreePreview();
  peopleView = view;
  listPanel.hidden = view !== "list";
  treePanel.hidden = view !== "tree";
  get("list-view").setAttribute("aria-pressed", String(view === "list"));
  get("tree-view").setAttribute("aria-pressed", String(view === "tree"));
  updateProfilePlacement();
  if (view === "tree") {
    render();
    if (mapCollapsed) fitTree();
    else focusTreeNode(selected);
  }
}
get("list-view").onclick = () => setPeopleView("list");
get("tree-view").onclick = () => setPeopleView("tree");

const stage = get("stage"), splitter = get("splitter");
let mapShare = 60, previousMapShare = 60;
let splitDrag = null;
const narrowLayout = globalThis.matchMedia?.("(max-width: 760px)");
function describeSplitter() {
  splitter.setAttribute("aria-orientation", narrowLayout?.matches ? "horizontal" : "vertical");
  splitter.setAttribute("aria-valuenow", String(mapCollapsed ? 0 : mapShare));
  splitter.setAttribute("aria-valuetext", mapCollapsed ? "Map closed" : mapShare + "% map width");
  splitter.title = mapCollapsed ? "Drag or press Enter to reopen the map" : "Drag to resize or close the map";
}
function resizePanels(share) {
  mapShare = Math.round(Math.max(7, Math.min(75, share)));
  stage.style.setProperty("--map-share", mapShare + "%");
  describeSplitter();
  updateProfilePlacement();
}
function moveDivider(share) {
  if (share < 7) {
    setMapCollapsed(true);
  } else {
    if (mapCollapsed) {
      resizePanels(share);
      setMapCollapsed(false);
    } else {
      resizePanels(share);
    }
  }
}
function toggleMap() {
  if (mapCollapsed) resizePanels(previousMapShare);
  else if (mapShare >= 15) previousMapShare = mapShare;
  setMapCollapsed(!mapCollapsed);
}
splitter.onpointerdown = event => {
  if (event.button !== 0) return;
  splitDrag = {id: event.pointerId, x: event.clientX, y: event.clientY, moved: false};
  if (!mapCollapsed && mapShare >= 15) previousMapShare = mapShare;
  splitter.setPointerCapture(event.pointerId);
  splitter.classList.add("is-resizing");
};
splitter.onpointermove = event => {
  if (event.pointerId !== splitDrag?.id) return;
  const delta = narrowLayout?.matches ? event.clientY - splitDrag.y : event.clientX - splitDrag.x;
  if (Math.abs(delta) < 5 && !splitDrag.moved) return;
  splitDrag.moved = true;
  if (narrowLayout?.matches) {
    if (delta < -35) setMapCollapsed(true);
    if (delta > 35 && mapCollapsed) toggleMap();
    return;
  }
  const bounds = stage.getBoundingClientRect();
  if (bounds.width) moveDivider(100 * (event.clientX - bounds.left) / bounds.width);
};
splitter.onpointerup = event => {
  if (event.pointerId === splitDrag?.id && !splitDrag.moved) toggleMap();
};
splitter.onlostpointercapture = () => {
  splitDrag = null;
  splitter.classList.remove("is-resizing");
};
splitter.onkeydown = event => {
  if (event.key === "Enter" || event.key === " ") {
    event.preventDefault();
    toggleMap();
    return;
  }
  const decrease = narrowLayout?.matches ? "ArrowUp" : "ArrowLeft";
  const increase = narrowLayout?.matches ? "ArrowDown" : "ArrowRight";
  if (![decrease, increase, "Home", "End"].includes(event.key)) return;
  event.preventDefault();
  if (event.key === "Home") {
    if (!mapCollapsed) toggleMap();
  } else if (mapCollapsed && event.key !== decrease) {
    toggleMap();
    if (event.key === "End") resizePanels(75);
  } else if (!mapCollapsed) {
    moveDivider(event.key === "End" ? 75 : mapShare + (event.key === increase ? 2 : -2));
  }
};
describeSplitter();

function selectPerson(key) {
  if (selected !== key && manualMapFrame) {
    const [left, bottom, right, top] = manualMapFrame;
    if ((records.get(key) ?? []).some(record => record.coords &&
        (record.coords[0] < left || record.coords[0] > right || record.coords[1] < bottom || record.coords[1] > top))) {
      manualMapFrame = null;
      mapView = "person";
    }
  }
  selected = key;
  selectedGroup.clear();
  render();
}

function renderPeopleList(people, year) {
  const focusedKey = listPanel.contains(document.activeElement) ? document.activeElement.dataset.key : null;
  listPanel.replaceChildren();
  for (const person of people) {
    const button = document.createElement("button");
    const name = document.createElement("strong"), location = document.createElement("small");
    button.type = "button";
    button.dataset.key = person.key;
    button.setAttribute("aria-pressed", String(person.key === selected || selectedGroup.has(person.key)));
    if (person.key === selected) button.setAttribute("aria-current", "true");
    name.textContent = nameWithAge(person, year);
    location.textContent = locationLabel(locationAt(person.key, year), year);
    button.append(name, location);
    button.onclick = () => selectPerson(person.key);
    listPanel.append(button);
    if (person.key === focusedKey) button.focus({preventScroll: true});
  }
}

function eventDateLabel(iso) {
  const [year, month, day] = iso.split("-");
  if (month === "00") return year;
  const date = new Date(year + "-" + month + "-" + (day === "00" ? "01" : day) + "T00:00:00Z");
  if (Number.isNaN(date.valueOf())) return iso.replace(/-00/g, "");
  return new Intl.DateTimeFormat(undefined, {
    year: "numeric", month: "short", ...(day === "00" ? {} : {day: "numeric"}), timeZone: "UTC",
  }).format(date);
}

function renderTreeSelection() {
  const panel = get("tree-selection"), person = personByKey.get(selected);
  panel.replaceChildren();
  panel.hidden = !person || !treeButtons.has(person.key);
  if (panel.hidden) return;
  const heading = document.createElement("strong");
  heading.textContent = person.name;
  panel.append(heading);
  const related = [
    ["Parents", relationships.filter(([, child]) => child === selected).map(([parent]) => parent)],
    ["Recorded spouses", marriages.filter(link => (link.date || link.place) &&
      (link.first === selected || link.second === selected)).map(link => link.first === selected ? link.second : link.first)],
    ["Children", relationships.filter(([parent]) => parent === selected).map(([, child]) => child)],
  ];
  for (const [label, keys] of related) {
    const names = [...new Set(keys)].map(key => personByKey.get(key)?.name).filter(Boolean);
    if (!names.length) continue;
    const line = document.createElement("p"), caption = document.createElement("span");
    caption.textContent = label + " ";
    line.append(caption, names.join(", "));
    panel.append(line);
  }
}

const mapPanel = get("map-panel");
let splitTreeView = null;
function setMapCollapsed(collapsed) {
  if (mapCollapsed === collapsed) return;
  if (collapsed) {
    splitTreeView = {zoom: treeZoom, left: treeViewport.scrollLeft, top: treeViewport.scrollTop};
  }
  mapCollapsed = collapsed;
  mapPanel.hidden = collapsed;
  stage.classList.toggle("map-collapsed", collapsed);
  describeSplitter();
  render();
  if (collapsed && peopleView === "tree") {
    fitTree();
  } else if (!collapsed && splitTreeView) {
    scaleTree(splitTreeView.zoom, false);
    treeViewport.scrollLeft = splitTreeView.left;
    treeViewport.scrollTop = splitTreeView.top;
    splitTreeView = null;
  }
}
globalThis.addEventListener?.("resize", () => {
  describeSplitter();
  if (mapCollapsed) scaleTree(treeZoom, false);
  else updateProfilePlacement();
});

const peopleToggle = get("people-toggle");
peopleToggle.onclick = () => {
  if (!peopleCollapsed && mapCollapsed) {
    resizePanels(previousMapShare);
    setMapCollapsed(false);
  }
  peopleCollapsed = !peopleCollapsed;
  peoplePanel.hidden = peopleCollapsed;
  splitter.hidden = peopleCollapsed;
  peopleToggle.setAttribute("aria-expanded", String(!peopleCollapsed));
  stage.classList.toggle("people-collapsed", peopleCollapsed);
  render();
};

function render() {
  const year = Number(scrub.value);
  const rosterPeople = people.filter(person => aliveIn(person, year))
    .sort((a,b) => a.name.localeCompare(b.name) || a.key.localeCompare(b.key));
  get("year").value = years.length ? year : "—";
  scrub.setAttribute("aria-valuetext", years.length ? String(year) : "No dated records");
  const groupSize = rosterPeople.filter(person => selectedGroup.has(person.key)).length;
  get("roster-note").textContent = rosterPeople.length + " people alive in " + year + "."
    + (groupSize > 1 ? " " + groupSize + " highlighted at the selected location." : "");
  renderPeopleList(rosterPeople, year);
  const treePeople = treeAliveOnly.checked ? rosterPeople : people;
  get("tree-empty").hidden = treePeople.length > 0;
  treeViewport.hidden = treePeople.length === 0;
  renderTree(treePeople, year);
  renderTreeSelection();
  if (!mapCollapsed) draw(year, rosterPeople);
  const person = personByKey.get(selected);
  get("detail").hidden = !person;
  if (!person) return;
  get("name").textContent = person.name;
  get("ancestry-limit").hidden = !person.has_more_parents;
  get("source").href = person.source;
  get("source").hidden = Boolean(person.biography.length);
  const recordList = get("records");
  recordList.replaceChildren();
  const displayed = new Set();
  for (const event of records.get(person.key) ?? []) {
    const description = JSON.stringify([event.activity, event.date, event.place]);
    if (displayed.has(description)) continue;
    displayed.add(description);
    const li = document.createElement("li"), heading = document.createElement("div");
    heading.className = "event-heading";
    const label = document.createElement("strong"), date = document.createElement("time");
    label.textContent = event.activity;
    date.dateTime = event.date.replace(/-00/g, "");
    date.textContent = eventDateLabel(event.date);
    heading.append(label, date);
    if (event.source !== person.source) {
      const source = document.createElement("a");
      source.href = event.source;
      source.target = "_blank";
      source.rel = "noopener";
      source.textContent = "Source";
      source.setAttribute("aria-label", "Source for " + event.activity);
      heading.append(source);
    }
    li.append(heading);
    if (event.place) {
      const place = document.createElement("p");
      place.className = "event-place";
      place.textContent = event.place;
      li.append(place);
    }
    recordList.append(li);
  }
  get("record-section").hidden = !recordList.childElementCount;
  renderBiography(person);
  renderCitations(person);
  prepareGallery(person);
}

function changeYear() {
  selectedGroup.clear();
  const person = personByKey.get(selected);
  if (person && !aliveIn(person, Number(scrub.value))) selected = null;
  render();
}
function resetMapView(view) {
  mapView = view;
  manualMapFrame = null;
  draw(Number(scrub.value), currentMapPeople);
}

function constrainMapFrame([left, bottom, right, top]) {
  const width = right - left, height = top - bottom;
  left = Math.min(180 - width, Math.max(-180, left));
  bottom = Math.min(85 - height, Math.max(-85, bottom));
  return [left, bottom, left + width, bottom + height];
}

function scheduleMapDraw() {
  if (mapDrawScheduled) return;
  mapDrawScheduled = true;
  const paint = () => {
    mapDrawScheduled = false;
    if (!mapCollapsed) draw(Number(scrub.value), currentMapPeople);
  };
  if (globalThis.requestAnimationFrame) globalThis.requestAnimationFrame(paint);
  else paint();
}

function zoomMap(factor, anchorX = .5, anchorY = .5) {
  const frame = manualMapFrame ?? currentMapFrame;
  if (!frame) return;
  const [left, bottom, right, top] = frame;
  const width = right - left, height = top - bottom;
  const scale = Math.max(.03 / width, Math.min(factor, 360 / width, 170 / height));
  const lon = left + width * anchorX, lat = top - height * anchorY;
  manualMapFrame = constrainMapFrame([
    lon - width * anchorX * scale, lat - height * (1 - anchorY) * scale,
    lon + width * (1 - anchorX) * scale, lat + height * anchorY * scale,
  ]);
  scheduleMapDraw();
}

function panMap(fractionX, fractionY, frame = manualMapFrame ?? currentMapFrame) {
  if (!frame) return;
  const [left, bottom, right, top] = frame;
  const dx = (right - left) * fractionX, dy = (top - bottom) * fractionY;
  manualMapFrame = constrainMapFrame([left - dx, bottom + dy, right - dx, top + dy]);
  scheduleMapDraw();
}

get("fit").onclick = () => resetMapView("locations");
get("fit-person").onclick = () => resetMapView("person");
get("world-view").onclick = () => resetMapView("world");
get("map-zoom-in").onclick = () => zoomMap(.72);
get("map-zoom-out").onclick = () => zoomMap(1 / .72);
canvas.onwheel = event => {
  const bounds = canvas.getBoundingClientRect();
  if (!event.deltaY || !bounds.width || !bounds.height) return;
  event.preventDefault();
  const delta = event.deltaY * (event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? bounds.height : 1);
  const factor = Math.exp(Math.max(-.6, Math.min(.6, delta * .002)));
  zoomMap(factor, (event.clientX - bounds.left) / bounds.width, (event.clientY - bounds.top) / bounds.height);
};
canvas.onpointerdown = event => {
  if (event.button !== 0 || mapDrag) return;
  mapDragged = false;
  const bounds = canvas.getBoundingClientRect();
  if (!bounds.width || !bounds.height) return;
  mapDrag = {id: event.pointerId, x: event.clientX, y: event.clientY, bounds, frame: manualMapFrame ?? currentMapFrame};
  canvas.setPointerCapture(event.pointerId);
};
canvas.onpointermove = event => {
  if (event.pointerId !== mapDrag?.id) return;
  const dx = event.clientX - mapDrag.x, dy = event.clientY - mapDrag.y;
  if (!mapDragged && Math.hypot(dx, dy) < 4) return;
  mapDragged = true;
  canvas.classList.add("is-panning");
  panMap(dx / mapDrag.bounds.width, dy / mapDrag.bounds.height, mapDrag.frame);
};
function endMapDrag(event) {
  if (event.pointerId !== mapDrag?.id) return;
  mapDrag = null;
  canvas.classList.remove("is-panning");
}
canvas.onpointerup = endMapDrag;
canvas.onpointercancel = endMapDrag;
canvas.onlostpointercapture = endMapDrag;
canvas.onkeydown = event => {
  const offsets = {ArrowLeft: [.12, 0], ArrowRight: [-.12, 0], ArrowUp: [0, .12], ArrowDown: [0, -.12]};
  if (Object.hasOwn(offsets, event.key)) panMap(...offsets[event.key]);
  else if (event.key === "+" || event.key === "=") zoomMap(.72);
  else if (event.key === "-") zoomMap(1 / .72);
  else return;
  event.preventDefault();
};
scrub.oninput = changeYear;
treeAliveOnly.onchange = () => {
  render();
  focusTreeNode(selected);
};
canvas.onclick = event => {
  if (mapDragged) {
    mapDragged = false;
    return;
  }
  const rect = canvas.getBoundingClientRect();
  const x = (event.clientX - rect.left) * canvas.width / rect.width;
  const y = (event.clientY - rect.top) * canvas.height / rect.height;
  const hit = hits.find(point => Math.hypot(point.x - x, point.y - y) <= point.radius + 4);
  if (!hit) return;
  selectedGroup = new Set(hit.keys);
  selected = hit.keys[(hit.keys.indexOf(selected) + 1) % hit.keys.length];
  render();
  if (peopleView === "tree") focusTreeNode(selected);
  else [...listPanel.children].find(button => button.dataset.key === selected)
    ?.scrollIntoView({block: "nearest", inline: "nearest"});
};
const startingPerson = people.find(person => person.starting_profile && yearOf(person.birth));
if (startingPerson) {
  selected = startingPerson.key;
  scrub.value = yearOf(startingPerson.birth);
}
render();

const searchForm = get("atlas-search"), searchName = get("search-name");
const searchButton = get("search-button"), searchStatus = get("search-status"), searchResults = get("search-results");
const searchDialog = get("search-dialog"), searchTrigger = get("open-search");
searchTrigger.onclick = () => {
  searchDialog.showModal();
  searchName.focus();
};
get("close-search").onclick = () => searchDialog.close();
searchDialog.onclose = () => searchTrigger.focus();
searchDialog.onclick = event => {
  if (event.target !== searchDialog) return;
  const bounds = searchDialog.getBoundingClientRect();
  if (event.clientX < bounds.left || event.clientX > bounds.right ||
      event.clientY < bounds.top || event.clientY > bounds.bottom) searchDialog.close();
};
searchName.value = query?.name ?? "";
const localSearch = location.protocol === "http:" && location.hostname === "127.0.0.1";
get("download-data").hidden = !localSearch;
if (!localSearch) {
  searchButton.disabled = true;
  searchStatus.textContent = "Open this atlas from the genealogy CLI to search WikiTree.";
}
async function searchRequest(path, body) {
  const response = await fetch(path, body
    ? {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(body)}
    : {});
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || "The local search is unavailable.");
  return result;
}
async function buildSearchMatch(match) {
  searchButton.disabled = true;
  for (const button of searchResults.children) button.disabled = true;
  searchStatus.textContent = "Loading the tree for " + match.name + "…";
  try {
    await searchRequest("/api/build", {key:match.key});
    while (true) {
      const job = await searchRequest("/api/progress");
      if (job.state === "ready") {
        location.assign(job.url);
        return;
      }
      if (job.state === "failed") throw new Error(job.message);
      if (job.message) searchStatus.textContent = job.message;
      await new Promise(resolve => setTimeout(resolve, 700));
    }
  } catch (error) {
    searchStatus.textContent = error.message;
    searchButton.disabled = false;
    for (const button of searchResults.children) button.disabled = false;
  }
}
searchForm.onsubmit = async event => {
  event.preventDefault();
  if (!localSearch || searchButton.disabled || !searchName.value.trim()) return;
  searchButton.disabled = true;
  searchResults.replaceChildren();
  searchStatus.textContent = "Looking for WikiTree matches…";
  try {
    const result = await searchRequest("/api/search", {name: searchName.value});
    searchStatus.textContent = result.matches.length
      ? "Choose a match." + (result.truncated ? " Refine the name if the match is missing." : "")
      : "No eligible public, deceased matches.";
    for (const match of result.matches) {
      const button = document.createElement("button");
      button.type = "button";
      const years = (yearOf(match.birth) ?? "?") + "–" + (yearOf(match.death) ?? "?");
      button.textContent = [match.name, years, match.place, "[" + match.key + "]"].filter(Boolean).join(" · ");
      button.onclick = () => buildSearchMatch(match);
      searchResults.append(button);
    }
  } catch (error) {
    searchStatus.textContent = error.message;
  } finally {
    searchButton.disabled = false;
  }
};
