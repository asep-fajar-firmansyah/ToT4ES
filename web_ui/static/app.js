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
    (payload.providers || []).forEach((provider) => {
      const option = document.createElement("option");
      option.value = provider.id;
      option.textContent = provider.available
        ? `${provider.label} \u2014 ${provider.model}`
        : `${provider.label} (unavailable)`;
      option.disabled = !provider.available;
      option.title = provider.detail || provider.model || "";
      if (provider.default && provider.available) option.selected = true;
      providerSelect.appendChild(option);
    });
    const usable = (payload.providers || []).find((provider) => provider.available);
    if (usable && providerSelect.selectedOptions[0]?.disabled) {
      providerSelect.value = usable.id;
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
  candidatesList.replaceChildren();
  graph.replaceChildren();
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
  renderGraph(resolved, triples);
  triples.forEach((triple) => triplesBody.appendChild(buildRow(triple)));
  triplesCount.textContent = String(triples.length);
  triplesSection.classList.remove("hidden");
}

summarizeButton.addEventListener("click", async () => {
  if (!currentEntity || !currentTriples.length) return;
  const provider = providerSelect.value || null;
  summarizeButton.disabled = true;
  statusEl.textContent = `Running ToT4ES selection through ${provider || "the LLM API"}\u2026`;
  try {
    const response = await fetch("/api/summarize", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        entity_label: currentEntity.label,
        triples: currentTriples,
        summary_length: 5,
        provider,
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
}

function renderGraph(resolved, triples) {
  if (!triples.length) return;

  const svgNamespace = "http://www.w3.org/2000/svg";
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

  const defs = document.createElementNS(svgNamespace, "defs");
  const marker = document.createElementNS(svgNamespace, "marker");
  marker.setAttribute("id", "graph-arrow");
  marker.setAttribute("viewBox", "0 0 10 10");
  marker.setAttribute("refX", "9");
  marker.setAttribute("refY", "5");
  marker.setAttribute("markerWidth", "6");
  marker.setAttribute("markerHeight", "6");
  marker.setAttribute("orient", "auto-start-reverse");
  const arrow = document.createElementNS(svgNamespace, "path");
  arrow.setAttribute("d", "M 0 0 L 10 5 L 0 10 z");
  arrow.setAttribute("fill", "#718096");
  marker.appendChild(arrow);
  defs.appendChild(marker);
  graph.appendChild(defs);

  const edgeLayer = document.createElementNS(svgNamespace, "g");
  edgeLayer.setAttribute("class", "graph-edges");
  triples.forEach((triple) => {
    const targetKey = triple.object_is_uri ? triple.object : `literal:${triple.object}`;
    const start = positions.get(subject.key);
    const end = positions.get(targetKey);
    if (!end) return;

    const line = document.createElementNS(svgNamespace, "line");
    line.setAttribute("x1", start.x);
    line.setAttribute("y1", start.y);
    line.setAttribute("x2", end.x);
    line.setAttribute("y2", end.y);
    line.setAttribute("class", "graph-edge");
    line.setAttribute("marker-end", "url(#graph-arrow)");
    edgeLayer.appendChild(line);

    const label = document.createElementNS(svgNamespace, "text");
    label.setAttribute("x", (start.x + end.x) / 2);
    label.setAttribute("y", (start.y + end.y) / 2 - 5);
    label.setAttribute("class", "graph-edge-label");
    label.textContent = shorten(triple.predicate_label, 22);
    edgeLayer.appendChild(label);
  });
  graph.appendChild(edgeLayer);

  nodes.forEach((node) => {
    const position = positions.get(node.key);
    const group = document.createElementNS(svgNamespace, "g");
    group.setAttribute("class", `graph-node ${node === subject ? "graph-subject" : node.isUri ? "graph-entity" : "graph-literal"}`);
    group.setAttribute("transform", `translate(${position.x} ${position.y})`);

    const shape = document.createElementNS(svgNamespace, node.isUri ? "circle" : "rect");
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

    const text = document.createElementNS(svgNamespace, "text");
    text.setAttribute("class", "graph-node-label");
    text.textContent = shorten(node.label, node === subject ? 18 : 16);
    group.appendChild(text);

    const title = document.createElementNS(svgNamespace, "title");
    title.textContent = node.uri || node.label;
    group.appendChild(title);

    if (node.uri && node !== subject) {
      group.setAttribute("tabindex", "0");
      group.setAttribute("role", "link");
      group.addEventListener("click", () => window.open(node.uri, "_blank", "noopener,noreferrer"));
      group.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") window.open(node.uri, "_blank", "noopener,noreferrer");
      });
    }
    graph.appendChild(group);
  });
  graphSection.classList.remove("hidden");
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
