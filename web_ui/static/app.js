const form = document.getElementById("search-form");
const entityInput = document.getElementById("entity-input");
const limitInput = document.getElementById("limit-input");
const submitButton = document.getElementById("submit-button");
const uploadForm = document.getElementById("upload-form");
const ntFileInput = document.getElementById("nt-file");
const uploadButton = document.getElementById("upload-button");
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
const entityRuntime = document.getElementById("entity-runtime");
const entityGraphTitle = document.getElementById("entity-graph-title");
const summaryGraph = document.getElementById("summary-graph");
const summaryGraphBlock = document.getElementById("summary-graph-block");
const summaryGraphReset = document.getElementById("summary-graph-reset");
const summaryRuntime = document.getElementById("summary-runtime");
const triplesSection = document.getElementById("triples-section");
const triplesBody = document.getElementById("triples-body");
const triplesCount = document.getElementById("triples-count");
const summarizeButton = document.getElementById("summarize-button");
const providerSelect = document.getElementById("provider-select");
const summarySection = document.getElementById("summary-section");
const summaryList = document.getElementById("summary-list");
const summaryCount = document.getElementById("summary-count");
const generationModal = document.getElementById("generation-modal");
const searchTree = document.getElementById("search-tree");
const generationRuntime = document.getElementById("generation-runtime");
const generationStatus = document.getElementById("generation-status");
const generationStep = document.getElementById("generation-step");

let currentEntity = null;
let currentTriples = [];
let generationTimer = null;
let generationStartedAt = 0;

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

uploadForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const file = ntFileInput.files[0];
  if (!file) return;
  setBusy(true, "Loading N-Triples file...");
  uploadButton.disabled = true;
    const startedAt = performance.now();
  try {
    const body = new FormData();
    body.append("file", file);
    const response = await fetch(`/api/entity/upload?limit=${encodeURIComponent(limitInput.value || "30")}`, {
      method: "POST",
      body,
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail || `Request failed (${response.status})`);
    render(payload);
    entityRuntime.textContent = `Loaded in ${formatRuntime(performance.now() - startedAt)}`;
    setBusy(false, `Loaded ${file.name}.`);
  } catch (error) {
    reset();
    setBusy(false, error.message);
  } finally {
    uploadButton.disabled = false;
  }
});

async function load(params) {
  const limit = limitInput.value || "30";
  const startedAt = performance.now();
  const query = new URLSearchParams({ ...params, limit });

  setBusy(true, "Querying DBpedia\u2026");
  try {
    const response = await fetch(`/api/entity?${query.toString()}`);
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.detail || `Request failed (${response.status})`);
    }
    render(payload);
    entityRuntime.textContent = `Loaded in ${formatRuntime(performance.now() - startedAt)}`;
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
  entityRuntime.textContent = "";
  summaryRuntime.textContent = "";
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
  entityGraphTitle.textContent = `${resolved.label} entity description`;
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
  const startedAt = performance.now();
  openGenerationModal(currentEntity.label);
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
    summaryRuntime.textContent = `Runtime ${formatRuntime(performance.now() - startedAt)}`;
    finishGenerationModal(true, payload.summary || []);
    statusEl.textContent = payload.model
      ? `ToT4ES summary generated with ${payload.model}.`
      : "ToT4ES summary generated.";
  } catch (error) {
    finishGenerationModal(false);
    statusEl.textContent = error.message;
  } finally {
    summarizeButton.disabled = false;
  }
});

function formatRuntime(milliseconds) {
  if (milliseconds < 1000) return `${Math.round(milliseconds)} ms`;
  return `${(milliseconds / 1000).toFixed(2)} s`;
}

function openGenerationModal(entityLabel) {
  generationModal.classList.remove("hidden");
  generationModal.setAttribute("aria-busy", "true");
  generationStartedAt = performance.now();
  generationStatus.textContent = `Exploring candidate triples for ${entityLabel}...`;
  generationStep.textContent = "Step 0 of 5";
  drawSearchTree(0, []);
  clearInterval(generationTimer);
  generationTimer = setInterval(() => {
    const elapsed = performance.now() - generationStartedAt;
    const stage = Math.min(5, Math.floor(elapsed / 850));
    generationRuntime.textContent = formatRuntime(elapsed);
    generationStep.textContent = `Step ${stage} of 5`;
    generationStatus.textContent = [
      "Preparing the current state...",
      "Generating relatedness candidates...",
      "Scoring informativeness...",
      "Checking diversity...",
      "Evaluating candidate summaries...",
      "Selecting the best path...",
    ][stage];
    drawSearchTree(stage, []);
  }, 120);
}

function finishGenerationModal(success, summary) {
  clearInterval(generationTimer);
  generationTimer = null;
  const elapsed = performance.now() - generationStartedAt;
  generationRuntime.textContent = formatRuntime(elapsed);
  generationStep.textContent = success ? "Complete" : "Stopped";
  generationStatus.textContent = success
    ? "Best summary path selected."
    : "Summary generation failed.";
  drawSearchTree(success ? 5 : 0, summary.map((triple) => triple.index));
  generationModal.setAttribute("aria-busy", "false");
  window.setTimeout(() => generationModal.classList.add("hidden"), success ? 700 : 250);
}

function drawSearchTree(stage, selectedIndices) {
  const nodes = [
    { id: 0, parent: null, x: 450, y: 35, label: "Current state", level: 0 },
    { id: 1, parent: 0, x: 180, y: 105, label: "Triple: [8]", level: 1 },
    { id: 2, parent: 0, x: 450, y: 105, label: "Triple: [18]", level: 1 },
    { id: 3, parent: 0, x: 720, y: 105, label: "Triple: [15]", level: 1 },
    { id: 4, parent: 1, x: 105, y: 175, label: "[8, 4]", level: 2 },
    { id: 5, parent: 1, x: 255, y: 175, label: "[8, 20]", level: 2 },
    { id: 6, parent: 2, x: 375, y: 175, label: "[18, 15]", level: 2 },
    { id: 7, parent: 2, x: 525, y: 175, label: "[18, 6]", level: 2 },
    { id: 8, parent: 3, x: 645, y: 175, label: "[15, 4]", level: 2 },
    { id: 9, parent: 3, x: 795, y: 175, label: "[15, 20]", level: 2 },
    { id: 10, parent: 4, x: 65, y: 250, label: "[8, 4, 1]", level: 3 },
    { id: 11, parent: 5, x: 205, y: 250, label: "[8, 20, 15]", level: 3 },
    { id: 12, parent: 6, x: 345, y: 250, label: "[18, 15, 1]", level: 3 },
    { id: 13, parent: 7, x: 485, y: 250, label: "[18, 6, 20]", level: 3 },
    { id: 14, parent: 8, x: 625, y: 250, label: "[15, 4, 8]", level: 3 },
    { id: 15, parent: 9, x: 765, y: 250, label: "[15, 20, 4]", level: 3 },
    { id: 16, parent: 11, x: 205, y: 330, label: "Evaluate", level: 4 },
    { id: 17, parent: 12, x: 345, y: 330, label: "Evaluate", level: 4 },
    { id: 18, parent: 15, x: 765, y: 330, label: "Evaluate", level: 4 },
    { id: 19, parent: 16, x: 205, y: 400, label: "Best", level: 5 },
    { id: 20, parent: 17, x: 345, y: 400, label: "Discard", level: 5 },
    { id: 21, parent: 18, x: 765, y: 400, label: "Discard", level: 5 },
  ];
  const byId = new Map(nodes.map((node) => [node.id, node]));
  const selectedPath = new Set([0, 2, 6, 12, 17, 20]);
  const selectedSet = new Set(selectedIndices || []);
  searchTree.replaceChildren();
  const defs = document.createElementNS(SVG_NS, "defs");
  const marker = document.createElementNS(SVG_NS, "marker");
  marker.setAttribute("id", "tree-arrow");
  marker.setAttribute("viewBox", "0 0 10 10");
  marker.setAttribute("refX", "9");
  marker.setAttribute("refY", "5");
  marker.setAttribute("markerWidth", "5");
  marker.setAttribute("markerHeight", "5");
  marker.setAttribute("orient", "auto");
  const arrow = document.createElementNS(SVG_NS, "path");
  arrow.setAttribute("d", "M 0 0 L 10 5 L 0 10 z");
  arrow.setAttribute("fill", "#718096");
  marker.appendChild(arrow);
  defs.appendChild(marker);
  searchTree.appendChild(defs);

  const edges = document.createElementNS(SVG_NS, "g");
  nodes.filter((node) => node.parent !== null).forEach((node) => {
    const parent = byId.get(node.parent);
    const edge = document.createElementNS(SVG_NS, "line");
    edge.setAttribute("x1", parent.x);
    edge.setAttribute("y1", parent.y);
    edge.setAttribute("x2", node.x);
    edge.setAttribute("y2", node.y);
    edge.setAttribute("class", `tree-edge ${node.level <= stage ? "active" : ""} ${stage === 5 && selectedPath.has(node.id) ? "selected" : ""}`);
    edge.setAttribute("marker-end", "url(#tree-arrow)");
    edges.appendChild(edge);
  });
  searchTree.appendChild(edges);

  const nodeLayer = document.createElementNS(SVG_NS, "g");
  nodes.forEach((node) => {
    const group = document.createElementNS(SVG_NS, "g");
    const resolved = stage === 5 && selectedSet.size ? selectedPath.has(node.id) : selectedPath.has(node.id);
    const failed = node.level > 1 && !resolved;
    group.setAttribute("class", `tree-node ${node.level <= stage ? "active" : "dimmed"} ${stage === 5 && resolved ? "selected" : ""} ${stage === 5 && failed ? "failed" : ""}`);
    const circle = document.createElementNS(SVG_NS, "circle");
    circle.setAttribute("cx", node.x);
    circle.setAttribute("cy", node.y);
    circle.setAttribute("r", node.level === 0 ? "17" : "12");
    circle.setAttribute("fill", node.level === 0 ? "#f4f5f7" : ["#d5e5ff", "#ffe0bb", "#c7d8e4", "#dfc8e8", "#ffe6a5"][node.level - 1] || "#d5e5ff");
    group.appendChild(circle);
    const text = document.createElementNS(SVG_NS, "text");
    text.setAttribute("x", node.x);
    text.setAttribute("y", node.y - 20);
    text.textContent = node.label;
    group.appendChild(text);
    if (stage === 5 && failed) {
      const mark = document.createElementNS(SVG_NS, "text");
      mark.setAttribute("x", node.x + 15);
      mark.setAttribute("y", node.y + 5);
      mark.setAttribute("class", "tree-cross");
      mark.textContent = "×";
      group.appendChild(mark);
    }
    if (stage === 5 && resolved) {
      const mark = document.createElementNS(SVG_NS, "text");
      mark.setAttribute("x", node.x + 15);
      mark.setAttribute("y", node.y + 5);
      mark.setAttribute("class", "tree-check");
      mark.textContent = "✓";
      group.appendChild(mark);
    }
    nodeLayer.appendChild(group);
  });
  searchTree.appendChild(nodeLayer);
}

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
