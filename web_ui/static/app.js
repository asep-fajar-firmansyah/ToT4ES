const form = document.getElementById("search-form");
const entityInput = document.getElementById("entity-input");
const limitInput = document.getElementById("limit-input");
const submitButton = document.getElementById("submit-button");
const statusEl = document.getElementById("status");

const entityCard = document.getElementById("entity-card");
const entityLabel = document.getElementById("entity-label");
const entityUri = document.getElementById("entity-uri");
const entityAbstract = document.getElementById("entity-abstract");

const candidatesSection = document.getElementById("candidates-section");
const candidatesList = document.getElementById("candidates-list");

const graphSection = document.getElementById("graph-section");
const graph = document.getElementById("knowledge-graph");
const graphReset = document.getElementById("graph-reset");
const summaryGraph = document.getElementById("summary-graph");
const summaryGraphBlock = document.getElementById("summary-graph-block");
const summaryGraphReset = document.getElementById("summary-graph-reset");
const triplesSection = document.getElementById("triples-section");
const triplesBody = document.getElementById("triples-body");
const triplesCount = document.getElementById("triples-count");
const summarizeButton = document.getElementById("summarize-button");
const providerSelect = document.getElementById("provider-select");
const summarySection = document.getElementById("summary-section");
const summaryList = document.getElementById("summary-list");
const summaryCount = document.getElementById("summary-count");

let currentEntity = null;
let currentTriples = [];

loadProviders();

async function loadProviders() {
  try {
    const response = await fetch("/api/providers");
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail || `Request failed (${response.status})`);
    providerSelect.replaceChildren();
    let fallback = null;
    (payload.providers || []).forEach((provider) => {
      const models = provider.models && provider.models.length ? provider.models : [provider.model];
      const group = document.createElement("optgroup");
      group.label = provider.available ? provider.label : `${provider.label} (unavailable)`;
      models.forEach((model) => {
        const option = document.createElement("option");
        option.value = `${provider.id}::${model}`;
        option.textContent = model;
        option.dataset.provider = provider.id;
        option.dataset.model = model;
        option.disabled = !provider.available;
        option.title = provider.detail || `${provider.label} \u2014 ${model}`;
        if (provider.available && !fallback) fallback = option.value;
        if (provider.default && provider.available && model === provider.model) {
          option.selected = true;
        }
        group.appendChild(option);
      });
      providerSelect.appendChild(group);
    });
    if (fallback && (!providerSelect.selectedOptions[0] || providerSelect.selectedOptions[0].disabled)) {
      providerSelect.value = fallback;
    }
  } catch (error) {
    statusEl.textContent = `Could not load LLM providers: ${error.message}`;
  }
}

form.addEventListener("submit", (event) => {
  event.preventDefault();
  const name = entityInput.value.trim();
  if (name) {
    load({ name });
  }
});

async function load(params) {
  const limit = limitInput.value || "30";
  const query = new URLSearchParams({ ...params, limit });

  setBusy(true, "Querying DBpedia\u2026");
  try {
    const response = await fetch(`/api/entity?${query.toString()}`);
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.detail || `Request failed (${response.status})`);
    }
    render(payload);
    setBusy(false, "");
  } catch (error) {
    reset();
    setBusy(false, error.message);
  }
}

function setBusy(busy, message) {
  submitButton.disabled = busy;
  statusEl.textContent = message;
}

function reset() {
  entityCard.classList.add("hidden");
  candidatesSection.classList.add("hidden");
  graphSection.classList.add("hidden");
  triplesSection.classList.add("hidden");
  summarySection.classList.add("hidden");
  summaryGraphBlock.classList.add("hidden");
  candidatesList.replaceChildren();
  graph.replaceChildren();
  summaryGraph.replaceChildren();
  graphControllers.clear();
  triplesBody.replaceChildren();
  summaryList.replaceChildren();
  currentEntity = null;
  currentTriples = [];
}

function render(payload) {
  reset();
  const resolved = payload.resolved;
  if (!resolved) {
    statusEl.textContent = "No entity found.";
    return;
  }

  entityLabel.textContent = resolved.label;
  entityUri.textContent = resolved.uri;
  entityUri.href = resolved.uri;
  entityAbstract.textContent = resolved.description || "No description available.";
  entityCard.classList.remove("hidden");

  const others = (payload.candidates || []).filter((item) => item.uri !== resolved.uri);
  if (others.length) {
    others.forEach((item) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "candidate";
      button.textContent = item.label;
      button.title = item.uri;
      button.addEventListener("click", () => load({ uri: item.uri }));

      const li = document.createElement("li");
      li.appendChild(button);
      candidatesList.appendChild(li);
    });
    candidatesSection.classList.remove("hidden");
  }

  const triples = payload.triples || [];
  currentEntity = resolved;
  currentTriples = triples;
  renderGraph(graph, resolved, triples);
  graphSection.classList.remove("hidden");
  triples.forEach((triple) => triplesBody.appendChild(buildRow(triple)));
  triplesCount.textContent = String(triples.length);
  triplesSection.classList.remove("hidden");
}

summarizeButton.addEventListener("click", async () => {
  if (!currentEntity || !currentTriples.length) return;
  const selected = providerSelect.selectedOptions[0];
  const provider = selected?.dataset.provider || null;
  const model = selected?.dataset.model || null;
  summarizeButton.disabled = true;
  statusEl.textContent = `Running ToT4ES selection through ${model || "the LLM API"}\u2026`;
  try {
    const response = await fetch("/api/summarize", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        entity_label: currentEntity.label,
        triples: currentTriples,
        summary_length: 5,
        provider,
        model,
      }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail || `Request failed (${response.status})`);
    renderSummary(payload.summary || []);
    statusEl.textContent = payload.model
      ? `ToT4ES summary generated with ${payload.model}.`
      : "ToT4ES summary generated.";
  } catch (error) {
    statusEl.textContent = error.message;
  } finally {
    summarizeButton.disabled = false;
  }
});

function renderSummary(summary) {
  summaryList.replaceChildren();
  summaryCount.textContent = `${summary.length} selected`;
  summary.forEach((triple) => {
    const item = document.createElement("li");
    const predicate = document.createElement("strong");
    predicate.textContent = triple.predicate_label;
    const separator = document.createTextNode(" ");
    const object = document.createElement("span");
    object.textContent = triple.object_label;
    item.append(predicate, separator, object);
    item.title = triple.triple_text;
    summaryList.appendChild(item);
  });
  summarySection.classList.remove("hidden");

  const selected = new Set(summary.map((triple) => triple.index));
  graphControllers.get(graph)?.highlight(selected);
  if (currentEntity && summary.length) {
    renderGraph(summaryGraph, currentEntity, summary);
    summaryGraphBlock.classList.remove("hidden");
  } else {
    summaryGraphBlock.classList.add("hidden");
  }
}

const SVG_NS = "http://www.w3.org/2000/svg";
const graphControllers = new Map();

graphReset.addEventListener("click", () => graphControllers.get(graph)?.reset());
summaryGraphReset.addEventListener("click", () => graphControllers.get(summaryGraph)?.reset());

function clientToSvg(svg, clientX, clientY) {
  const point = svg.createSVGPoint();
  point.x = clientX;
  point.y = clientY;
  return point.matrixTransform(svg.getScreenCTM().inverse());
}

function renderGraph(svg, resolved, triples) {
  svg.replaceChildren();
  graphControllers.delete(svg);
  if (!triples.length) return;

  const subject = { key: resolved.uri, label: resolved.label, uri: resolved.uri, isUri: true };
  const objects = new Map();
  triples.forEach((triple) => {
    const key = triple.object_is_uri ? triple.object : `literal:${triple.object}`;
    if (!objects.has(key)) {
      objects.set(key, {
        key,
        label: triple.object_label,
        uri: triple.object_is_uri ? triple.object : null,
        isUri: triple.object_is_uri,
      });
    }
  });

  const nodes = [subject, ...objects.values()];
  const positions = new Map([[subject.key, { x: 500, y: 280 }]]);
  const ringRadius = objects.size > 18 ? 220 : 185;
  [...objects.values()].forEach((node, index) => {
    const angle = -Math.PI / 2 + (index * Math.PI * 2) / objects.size;
    positions.set(node.key, {
      x: 500 + Math.cos(angle) * ringRadius,
      y: 280 + Math.sin(angle) * Math.min(ringRadius * 0.9, 205),
    });
  });
  const initialPositions = new Map([...positions].map(([key, value]) => [key, { ...value }]));

  const markerId = `${svg.id}-arrow`;
  const defs = document.createElementNS(SVG_NS, "defs");
  const marker = document.createElementNS(SVG_NS, "marker");
  marker.setAttribute("id", markerId);
  marker.setAttribute("viewBox", "0 0 10 10");
  marker.setAttribute("refX", "9");
  marker.setAttribute("refY", "5");
  marker.setAttribute("markerWidth", "6");
  marker.setAttribute("markerHeight", "6");
  marker.setAttribute("orient", "auto-start-reverse");
  const arrow = document.createElementNS(SVG_NS, "path");
  arrow.setAttribute("d", "M 0 0 L 10 5 L 0 10 z");
  arrow.setAttribute("fill", "#718096");
  marker.appendChild(arrow);
  defs.appendChild(marker);
  svg.appendChild(defs);

  const viewport = document.createElementNS(SVG_NS, "g");
  viewport.setAttribute("class", "graph-viewport");
  svg.appendChild(viewport);

  const edgeLayer = document.createElementNS(SVG_NS, "g");
  edgeLayer.setAttribute("class", "graph-edges");
  viewport.appendChild(edgeLayer);

  const edges = [];
  triples.forEach((triple) => {
    const targetKey = triple.object_is_uri ? triple.object : `literal:${triple.object}`;
    if (!positions.has(targetKey)) return;

    const line = document.createElementNS(SVG_NS, "line");
    line.setAttribute("class", "graph-edge");
    line.setAttribute("marker-end", `url(#${markerId})`);
    edgeLayer.appendChild(line);

    const label = document.createElementNS(SVG_NS, "text");
    label.setAttribute("class", "graph-edge-label");
    label.textContent = shorten(triple.predicate_label, 22);
    edgeLayer.appendChild(label);

    edges.push({ line, label, targetKey, index: triple.index });
  });

  const nodeGroups = new Map();
  nodes.forEach((node) => {
    const group = document.createElementNS(SVG_NS, "g");
    const kind = node === subject ? "graph-subject" : node.isUri ? "graph-entity" : "graph-literal";
    group.setAttribute("class", `graph-node ${kind}`);

    const shape = document.createElementNS(SVG_NS, node.isUri ? "circle" : "rect");
    if (node.isUri) {
      shape.setAttribute("r", node === subject ? "36" : "27");
    } else {
      shape.setAttribute("x", "-48");
      shape.setAttribute("y", "-20");
      shape.setAttribute("width", "96");
      shape.setAttribute("height", "40");
      shape.setAttribute("rx", "8");
    }
    shape.setAttribute("class", "graph-node-shape");
    group.appendChild(shape);

    const text = document.createElementNS(SVG_NS, "text");
    text.setAttribute("class", "graph-node-label");
    text.textContent = shorten(node.label, node === subject ? 18 : 16);
    group.appendChild(text);

    const title = document.createElementNS(SVG_NS, "title");
    title.textContent = node.uri || node.label;
    group.appendChild(title);

    if (node.uri && node !== subject) {
      group.setAttribute("tabindex", "0");
      group.setAttribute("role", "link");
      group.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") window.open(node.uri, "_blank", "noopener,noreferrer");
      });
    }

    attachNodeDrag(group, node);
    viewport.appendChild(group);
    nodeGroups.set(node.key, group);
    placeNode(node.key);
  });

  const view = { x: 0, y: 0, k: 1 };
  applyView();

  function applyView() {
    viewport.setAttribute("transform", `translate(${view.x} ${view.y}) scale(${view.k})`);
  }

  function placeNode(key) {
    const position = positions.get(key);
    nodeGroups.get(key).setAttribute("transform", `translate(${position.x} ${position.y})`);
    const start = positions.get(subject.key);
    edges.forEach((edge) => {
      if (key !== subject.key && edge.targetKey !== key) return;
      const end = positions.get(edge.targetKey);
      edge.line.setAttribute("x1", start.x);
      edge.line.setAttribute("y1", start.y);
      edge.line.setAttribute("x2", end.x);
      edge.line.setAttribute("y2", end.y);
      edge.label.setAttribute("x", (start.x + end.x) / 2);
      edge.label.setAttribute("y", (start.y + end.y) / 2 - 5);
    });
  }

  function attachNodeDrag(group, node) {
    let drag = null;
    group.addEventListener("pointerdown", (event) => {
      event.stopPropagation();
      group.setPointerCapture(event.pointerId);
      drag = { id: event.pointerId, moved: false };
      group.classList.add("dragging");
    });
    group.addEventListener("pointermove", (event) => {
      if (!drag || event.pointerId !== drag.id) return;
      const point = clientToSvg(svg, event.clientX, event.clientY);
      positions.set(node.key, { x: (point.x - view.x) / view.k, y: (point.y - view.y) / view.k });
      placeNode(node.key);
      drag.moved = true;
    });
    const endDrag = (event) => {
      if (!drag || event.pointerId !== drag.id) return;
      const dragged = drag.moved;
      drag = null;
      group.classList.remove("dragging");
      if (!dragged && node.uri && node !== subject) {
        window.open(node.uri, "_blank", "noopener,noreferrer");
      }
    };
    group.addEventListener("pointerup", endDrag);
    group.addEventListener("pointercancel", endDrag);
  }

  svg.addEventListener("wheel", (event) => {
    event.preventDefault();
    const point = clientToSvg(svg, event.clientX, event.clientY);
    const target = Math.min(4, Math.max(0.3, view.k * (event.deltaY < 0 ? 1.15 : 1 / 1.15)));
    const factor = target / view.k;
    view.x = point.x - (point.x - view.x) * factor;
    view.y = point.y - (point.y - view.y) * factor;
    view.k = target;
    applyView();
  }, { passive: false });

  let pan = null;
  svg.addEventListener("pointerdown", (event) => {
    if (event.target.closest(".graph-node")) return;
    const point = clientToSvg(svg, event.clientX, event.clientY);
    pan = { id: event.pointerId, x: point.x, y: point.y };
    svg.setPointerCapture(event.pointerId);
    svg.classList.add("panning");
  });
  svg.addEventListener("pointermove", (event) => {
    if (!pan || event.pointerId !== pan.id) return;
    const point = clientToSvg(svg, event.clientX, event.clientY);
    view.x += point.x - pan.x;
    view.y += point.y - pan.y;
    pan.x = point.x;
    pan.y = point.y;
    applyView();
  });
  const endPan = () => {
    pan = null;
    svg.classList.remove("panning");
  };
  svg.addEventListener("pointerup", endPan);
  svg.addEventListener("pointercancel", endPan);

  graphControllers.set(svg, {
    reset() {
      initialPositions.forEach((position, key) => positions.set(key, { ...position }));
      nodeGroups.forEach((_, key) => placeNode(key));
      view.x = 0;
      view.y = 0;
      view.k = 1;
      applyView();
    },
    highlight(indices) {
      edges.forEach((edge) => {
        const on = indices.has(edge.index);
        edge.line.classList.toggle("selected", on);
        edge.label.classList.toggle("selected", on);
        if (on) nodeGroups.get(edge.targetKey)?.classList.add("selected");
      });
    },
  });
}

function shorten(value, maxLength) {
  return value.length > maxLength ? `${value.slice(0, maxLength - 1)}\u2026` : value;
}

function buildRow(triple) {
  const row = document.createElement("tr");

  const indexCell = document.createElement("td");
  indexCell.textContent = String(triple.index);

  const predicateCell = document.createElement("td");
  predicateCell.textContent = triple.predicate_label;
  predicateCell.title = triple.predicate;

  const objectCell = document.createElement("td");
  if (triple.object_is_uri) {
    const link = document.createElement("a");
    link.href = triple.object;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    link.textContent = triple.object_label;
    link.title = triple.object;
    objectCell.appendChild(link);
  } else {
    objectCell.textContent = triple.object_label;
  }

  row.append(indexCell, predicateCell, objectCell);
  return row;
}
