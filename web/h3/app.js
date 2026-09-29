"use strict";

const $ = (id) => document.getElementById(id);
const clone = (value) => structuredClone(value);
const collection = (features = []) => ({ type: "FeatureCollection", features });
const LAYERS = {
  survey_areas: { label: "Области съёмки", color: "#259779", icon: "scan" },
  allowed_airspace: { label: "Разрешённая область", color: "#558aca", icon: "pentagon" },
  no_fly_zones: { label: "Запретные зоны", color: "#cb565d", icon: "ban" },
  landing_sites: { label: "Площадки", color: "#bd8c27", icon: "map-pin" },
  obstacles: { label: "Препятствия", color: "#8f735b", icon: "building-2" },
  temporal_airspace: { label: "Временные зоны", color: "#9872b0", icon: "clock-4" },
};
const UAV_COLORS = ["#087c70", "#d17a24", "#427cbd", "#b64976", "#8464ac", "#76932a", "#c1524b", "#367e95", "#956843", "#575fba"];
const SENSOR_LABELS = { rgb: "RGB", multispectral: "Мультиспектральный", thermal: "Тепловизионный", lidar: "LiDAR", geophysics: "Геофизический", rgb_video: "RGB-видео" };
const STAGE_LABELS = { h1: "Галсы и задачи", h2: "Планирование маршрутов", h3: "Проверка безопасности" };
function userMessage(value) {
  return String(value || "")
    .replace("Canonical H1/H2 calculation and independent safety validation", "Построение маршрутов и проверка безопасности")
    .replace("Оптимизация OR-Tools Routing", "Поиск лучшего распределения задач и маршрутов")
    .replace("Расчёт рёбер для профиля БПЛА", "Оценка времени перелётов для аппаратов")
    .replace("Граф и ресурсные таблицы готовы", "Время перелётов и запас энергии рассчитаны")
    .replace(/H1 завершён/g, "Галсы построены").replace(/H3 завершён/g, "Проверка завершена")
    .replace(/\bH1\b/g, "Покрытие").replace(/\bH2\b/g, "Планирование").replace(/\bH3\b/g, "Проверка безопасности")
    .replace(/\bSAFE\b/g, "проверено").replace(/\bINFEASIBLE\b/g, "нет допустимого решения");
}
const KNOWN_MESSAGES = { "Preparation and data download leave no flight time in the mission/daylight window": "Подготовка и снятие данных занимают всё разрешённое окно. Увеличьте окно или уточните длительности наземных работ.", "Reserve sites are emergency-only; choose a regular final landing site": "Резервная площадка предназначена только для аварийной посадки. Выберите обычную конечную площадку.", "Template is read-only; use Save As": "Шаблон нельзя перезаписать. Используйте «Сохранить как».", "A saved scenario with this name already exists": "Такое имя уже есть в «Моих сценариях». Выберите другое.", "Invalid access code": "Неверный код доступа.", "Authentication required": "Время сеанса истекло. Введите код повторно.", "Too many attempts; wait 15 minutes": "Слишком много попыток. Повторите вход через 15 минут.", "Access code is not configured": "Администратор ещё не настроил код доступа.", "CSRF token missing or invalid": "Сеанс устарел. Выйдите и войдите повторно.", "Model validation is not a flight permit or proof of real-world operational safety.": "Проверка модели не является разрешением на реальный полёт.", "No reference scenario": "Для пользовательской сцены эталон отсутствует.", "Input differs from the reference or objective has no reference": "Вход отличается от эталона или для выбранного критерия эталон отсутствует.", "Wait for active calculation before deleting": "Удаление доступно после завершения активного расчёта." };
const TERMINAL = new Set(["SAFE", "UNSAFE", "INFEASIBLE", "ERROR", "FAILED", "CANCELLED", "UNVERIFIED", "UNSUPPORTED", "COMPLETED"]);
const state = { templateFolder: "simple", templateQuery: "", fleetOpen: new Set(), selectedUav: null, namingMode: "save-as", sceneLoading: false, editHistory: [], editIndex: -1, cleanEdit: "", restoringEdit: false, selectedH1Task: null, csrf: null, scene: null, draft: null, dirty: false, sidebar: "scene", scenarios: [], myScenarios: [], payloadDefaults: [], models: [], presets: [], plan: null, resultTab: "summary", selected: null, tool: "select", hiddenLayers: new Set(), poll: null, map: null, draw: null, mapReady: false, frame: null, playing: false, playbackValue: 0, playbackSpeed: 60, playbackPrevious: 0, jsonApply: null, busy: false };
const basemap = { preference: null, mode: "map", manifest: null, manifestStatus: "idle", active: null, pending: null, message: "", generation: 0, failed: new Set(), layers: new Set(), osmUnavailable: false };
try { const saved = localStorage.getItem("geoscan.h3.basemap"); if (["map", "imagery", "terrain"].includes(saved)) basemap.preference = saved; } catch { /* Browsing with storage disabled still permits switching layers. */ }

function element(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === null || value === undefined) continue;
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else if (key in node && !key.startsWith("aria-")) node[key] = value;
    else node.setAttribute(key, value);
  }
  for (const child of Array.isArray(children) ? children : [children]) {
    if (child === null || child === undefined) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}
function icon(name) { return element("i", { "data-lucide": name }); }
function icons() { if (window.lucide) lucide.createIcons({ attrs: { "aria-hidden": "true" } }); }
function button(text, iconName, handler, className = "quiet") {
  return element("button", { type: "button", class: className, onclick: handler }, [iconName ? icon(iconName) : null, text]);
}
function badge(text, kind = "neutral") { return element("span", { class: `badge ${kind}`, text }); }
function statusBadge(status) {
  const value = String(status || "").toUpperCase();
  return badge(({ SAFE:"ПРОВЕРЕНО", UNSAFE:"ЕСТЬ НАРУШЕНИЯ", INFEASIBLE:"НЕТ РЕШЕНИЯ", ERROR:"ОШИБКА", FAILED:"ОШИБКА", QUEUED:"В ОЧЕРЕДИ", RUNNING:"РАСЧЁТ", UNVERIFIED:"НЕ ПРОВЕРЕНО", UNSUPPORTED:"НЕ ПОДДЕРЖИВАЕТСЯ", COMPLETED: "ГОТОВО", CANCELLED: "ОСТАНОВЛЕНО" })[value] || value || "НЕТ РЕЗУЛЬТАТА", value === "SAFE" ? "good" : ["UNSAFE", "ERROR", "FAILED"].includes(value) ? "bad" : "warn");
}
function toast(message, error = false) {
  const item = element("div", { class: `toast${error ? " error" : ""}`, text: message, role: error ? "alert" : "status" });
  $("toast-container").append(item);
  setTimeout(() => item.remove(), error ? 12000 : 6500);
}
function errorMessage(error) { return error instanceof Error ? error.message : String(error); }
function detailsMessage(value) {
  if (typeof value === "string") return KNOWN_MESSAGES[value] || value;
  if (Array.isArray(value)) return value.map(detailsMessage).join("; ");
  if (value && typeof value === "object") return value.message || value.msg || value.detail && detailsMessage(value.detail) || JSON.stringify(value);
  return String(value ?? "Неизвестная ошибка");
}
async function api(path, options = {}) {
  const headers = new Headers(options.headers || {});
  if (options.body && !(options.body instanceof FormData)) { headers.set("Content-Type", "application/json"); options.body = JSON.stringify(options.body); }
  if (options.method && !["GET", "HEAD"].includes(options.method) && state.csrf) headers.set("X-CSRF-Token", state.csrf);
  let response;
  try { response = await fetch(`/api/v1${path}`, { ...options, headers, credentials: "same-origin" }); }
  catch { throw new Error("Сервер недоступен. Проверьте подключение и повторите действие."); }
  let data;
  try { data = await response.json(); } catch { data = null; }
  if (!response.ok) {
    if (response.status === 401) lock();
    throw new Error(detailsMessage(data?.detail || data?.message || `Ошибка сервера: ${response.status}`));
  }
  return data;
}
async function action(control, callback) {
  if (control?.disabled) return;
  if (control) control.disabled = true;
  try { return await callback(); } catch (error) { toast(errorMessage(error), true); }
  finally { if (control) control.disabled = false; updateControls(); }
}
function lock() {
  state.csrf = null;
  clearTimeout(state.poll);
  pausePlayback();
  $("app").hidden = true;
  $("login-screen").hidden = false;
  $("connection-status").textContent = "Введите общий код доступа";
}
async function unlock(auth) {
  state.csrf = auth.csrf_token;
  state.role = auth.role || "admin";
  state.offline = Boolean(auth.offline);
  $("logout").hidden = Boolean(auth.public_access);
  $("session-role").textContent = {viewer:"Наблюдатель", planner:"Планировщик", admin:"Администратор"}[state.role];
  $("access-code").value = "";
  $("login-screen").hidden = true;
  $("app").hidden = false;
  initializeMap();
  const [catalog, saved, models] = await Promise.all([api("/scenarios"), api("/scenes?saved_only=true"), api("/uav-models").catch(() => ({ models: [], defaults: [] }))]);
  state.scenarios = catalog.scenarios || [];
  state.myScenarios = saved.scenes || [];
  state.payloadDefaults = models.payload_defaults || [];
  state.models = models.models || [];
  state.presets = models.defaults || [];
  renderCatalog();
  renderProjectBar();
  renderSidebar();
  const lastId = localStorage.getItem("geoscan.h3.last-scene");
  if (lastId) {
    try { await openScene(lastId); } catch { localStorage.removeItem("geoscan.h3.last-scene"); }
  } else if (state.myScenarios.length) await openScene(state.myScenarios[0].id);
  icons();
  setTimeout(() => state.map?.resize(), 0);
}

function initializeMap() {
  if (state.map) { state.map.resize(); return; }
  if (!window.maplibregl || !window.MapboxDraw) {
    $("map-failure").hidden = false;
    $("map-failure").textContent = "Компоненты карты не загрузились. Обновите страницу; параметры и история остаются доступны.";
    return;
  }
  try {
    Object.assign(MapboxDraw.constants.classes, { CANVAS: "maplibregl-canvas", CONTROL_BASE: "maplibregl-ctrl", CONTROL_PREFIX: "maplibregl-ctrl-", CONTROL_GROUP: "maplibregl-ctrl-group", ATTRIBUTION: "maplibregl-ctrl-attrib" });
    const map = new maplibregl.Map({ container: "map", center: [37.53056, 55.70306], zoom: 11.5, attributionControl: true,
      style: { version: 8, sources: { osm: { type: "raster", tiles: state.offline ? [] : ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"], tileSize: 256, attribution: '&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a>' } }, layers: [{ id: "background", type: "background", paint: { "background-color": "#edf0eb" } }, { id: "osm", source: "osm", type: "raster", layout: { visibility: "none" }, paint: { "raster-saturation": -0.35, "raster-opacity": 0.87 } }] } });
    state.map = map;
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
    map.addControl(new maplibregl.ScaleControl({ maxWidth: 100, unit: "metric" }), "bottom-right");
    const color = ["match", ["get", "user_layer_key"], ...Object.entries(LAYERS).flatMap(([key, layer]) => [key, layer.color]), "#087c70"];
    const styles = [
      { id: "h3-polygon-fill", type: "fill", filter: ["==", "$type", "Polygon"], paint: { "fill-color": color, "fill-opacity": ["case", ["==", ["get", "user_layer_key"], "allowed_airspace"], 0.045, 0.17] } },
      { id: "h3-polygon-halo", type: "line", filter: ["==", "$type", "Polygon"], paint: { "line-color": "#fff", "line-width": 4.5, "line-opacity": 0 } },
      { id: "h3-polygon-line", type: "line", filter: ["==", "$type", "Polygon"], paint: { "line-color": ["case", ["==", ["get", "active"], "true"], "#e39729", color], "line-width": ["case", ["==", ["get", "active"], "true"], 3, 1.6] } },
      { id: "h3-point", type: "circle", filter: ["all", ["==", "$type", "Point"], ["==", "meta", "feature"]], paint: { "circle-color": color, "circle-radius": ["case", ["==", ["get", "active"], "true"], 8, 6], "circle-stroke-width": 2, "circle-stroke-color": "#fff" } },
      { id: "h3-vertex", type: "circle", filter: ["all", ["==", "$type", "Point"], ["==", "meta", "vertex"]], paint: { "circle-color": "#e39729", "circle-radius": 5, "circle-stroke-color": "#fff", "circle-stroke-width": 2 } },
      { id: "h3-midpoint", type: "circle", filter: ["all", ["==", "$type", "Point"], ["==", "meta", "midpoint"]], paint: { "circle-color": "#e39729", "circle-radius": 3 } },
      { id: "h3-drawing-line", type: "line", filter: ["==", "$type", "LineString"], paint: { "line-color": "#e39729", "line-width": 2 } },
    ];
    state.draw = new MapboxDraw({ displayControlsDefault: false, userProperties: true, styles, touchEnabled: true });
    map.addControl(state.draw);
    map.on("load", () => {
      state.mapReady = true;
      // Remote tiles must not block initialization of the local editor and imagery.
      if (!state.offline) map.setLayoutProperty("osm", "visibility", "visible");
      map.addSource("routes", { type: "geojson", data: collection() });
      map.addLayer({ id: "routes-halo", type: "line", source: "routes", paint: { "line-color": "#fff", "line-width": 5.2, "line-opacity": 0 } });
      map.addLayer({ id: "routes", type: "line", source: "routes", paint: { "line-color": ["get", "color"], "line-width": 2.4, "line-opacity": 0.86 } });
      map.addSource("playback", { type: "geojson", data: collection() });
      map.addLayer({ id: "playback", type: "circle", source: "playback", paint: { "circle-color": ["get", "color"], "circle-radius": 7, "circle-stroke-width": 3, "circle-stroke-color": "#fff" } });
      map.addSource("h1-output", { type: "geojson", data: collection() });
      map.addLayer({ id: "h1-output-fill", type: "fill", source: "h1-output", filter: ["==", ["get", "kind"], "task_envelope"], layout: { visibility: "none" }, paint: { "fill-color": "#d9e5f2", "fill-opacity": 0.07 } });
      map.addLayer({ id: "h1-output-boundaries", type: "line", source: "h1-output", filter: ["==", ["get", "kind"], "task_envelope"], layout: { visibility: "none" }, paint: { "line-color": "#d9e5f2", "line-width": 1.5, "line-dasharray": [2, 1.3], "line-opacity": 0.85 } });
      map.addLayer({ id: "h1-output-transects", type: "line", source: "h1-output", filter: ["==", ["get", "kind"], "transect"], layout: { visibility: "none" }, paint: { "line-color": ["get", "color"], "line-width": 2.3, "line-opacity": 0.88 } });
      map.addLayer({ id: "h1-output-labels", type: "symbol", source: "h1-output", filter: ["==", ["get", "kind"], "task_label"], layout: { visibility: "none", "text-field": ["get", "label"], "text-size": 11, "text-offset": [0, 0.6], "text-allow-overlap": false }, paint: { "text-color": "#24332e", "text-halo-color": "#fff", "text-halo-width": 1.5 } });
      map.addLayer({ id: "h1-selected-fill", type: "fill", source: "h1-output", filter: ["==", ["get", "task_id"], ""], layout: { visibility: "none" }, paint: { "fill-color": "#ffb000", "fill-opacity": .35 } });
      map.addLayer({ id: "h1-selected-line", type: "line", source: "h1-output", filter: ["==", ["get", "task_id"], ""], layout: { visibility: "none" }, paint: { "line-color": "#e07800", "line-width": 4 } });
      installRoutingGraphLayers(map);
      for (const layer of ["routes", "playback"]) map.on("click", layer, event => {
        const uid = event.features?.[0]?.properties?.uav_id;
        if (!uid) return;
        state.selectedUav = uid; state.fleetOpen.add(uid); state.sidebar = "fleet";
        renderSidebar(); highlightUav();
        [...document.querySelectorAll(".fleet-item")].find(node => node.dataset.uavId === uid)?.scrollIntoView({block:"nearest"});
      });
      paintScene();
      paintPlan();
      renderSidebar();
      updateControls();
      loadImageryManifest();
    });
    map.on("error", (event) => {
      if (event.sourceId === "osm" || /tile.openstreetmap/.test(event.error?.message || "")) { basemap.osmUnavailable = true; renderBasemap(); }
      if (event.sourceId?.startsWith("imagery-") && [basemap.pending, basemap.active?.id].some((id) => id && event.sourceId === `imagery-${id}`)) {
        const id = event.sourceId.slice("imagery-".length);
        basemap.failed.add(id);
        showStreetMap("Не удалось загрузить снимок. Показана карта.");
      }
    });
    map.on("sourcedata", (event) => {
      if (event.sourceId === "osm" && event.tile?.state === "loaded") { basemap.osmUnavailable = false; renderBasemap(); }
    });
    map.on("mousemove", (event) => { $("coordinates").textContent = `${event.lngLat.lat.toFixed(5)}, ${event.lngLat.lng.toFixed(5)}`; });
    map.on("draw.create", (event) => {
      if (!state.draft) { state.draw.deleteAll(); return; }
      for (const feature of event.features) {
        const layer = state.tool === "select" ? "survey_areas" : state.tool;
        state.draw.setFeatureProperty(feature.id, "layer_key", layer);
        const properties = defaultFeatureProperties(layer);
        for (const [key, value] of Object.entries(properties)) state.draw.setFeatureProperty(feature.id, key, value);
        state.selected = feature.id;
      }
      state.tool = "select";
      syncLayers();
      markDirty();
      renderSidebar();
      updateTools();
    });
    map.on("draw.update", () => { syncLayers(); markDirty(); renderProperties(); });
    map.on("draw.delete", () => { state.selected = null; syncLayers(); repairSiteChoices(); markDirty(); renderSidebar(); });
    map.on("draw.selectionchange", (event) => {
      state.selected = event.features[0]?.id || null;
      if (state.selected) { state.sidebar = "scene"; renderSidebar(); }
      updateObjectSelection(true); renderProperties();
      $("delete-feature").disabled = !state.selected;
    });
  } catch (error) {
    $("map-failure").hidden = false;
    $("map-failure").textContent = `Карта недоступна: ${errorMessage(error)}. Нужен браузер с поддержкой WebGL.`;
  }
}
function defaultFeatureProperties(layer) {
  const id = nextObjectName(layer);
  if (layer === "survey_areas") return { id, survey_type: "rgb", payload_profile: profiles()[0]?.id || "rgb_default" };
  if (layer === "landing_sites") return { id, role: "both", candidate: false };
  if (layer === "obstacles") return { id, height_m: 30, horizontal_buffer_m: 20, vertical_buffer_m: 20 };
  if (layer === "temporal_airspace") return { id, restriction_type: "prohibited", active_from: state.draft.mission.mission_window?.start || "2026-06-15T09:00:00+03:00", active_to: state.draft.mission.mission_window?.end || "2026-06-15T10:00:00+03:00", min_alt_m: 0, max_alt_m: 500 };
  return { id };
}
function flattenLayers() {
  return collection(Object.entries(state.draft?.layers || {}).flatMap(([layer, data]) => (data?.features || []).map((feature, index) => ({ ...clone(feature), id: `${layer}:${index}:${feature.properties?.id || index}`, properties: { ...clone(feature.properties || {}), layer_key: layer } }))));
}
function syncLayers() {
  if (!state.draw || !state.mapReady || !state.draft) return;
  const layers = Object.fromEntries(Object.keys(LAYERS).map((key) => [key, collection()]));
  for (const feature of state.draw.getAll().features) {
    const data = clone(feature);
    const layer = data.properties.layer_key;
    if (!layers[layer]) continue;
    delete data.id;
    delete data.properties.layer_key;
    layers[layer].features.push(data);
  }
  state.draft.layers = layers;
}
function paintScene() {
  if (!state.mapReady) return;
  state.draw.set(state.draft ? flattenLayers() : collection());
  applyLayerVisibility();
  fitScene();
  updateBasemap();
}

function safeSourceLink(value) {
  try { const parsed = new URL(value); return parsed.protocol === "https:" ? parsed.href : null; } catch { return null; }
}
function validImageryItem(item) {
  if (!item || !/^[a-z0-9_-]+$/.test(item.id) || !/^\/static\/imagery\/[a-zA-Z0-9_.-]+\.(jpg|jpeg|png|webp)$/.test(item.url)) return false;
  if (!Array.isArray(item.bounds) || item.bounds.length !== 4 || !item.bounds.every(Number.isFinite)) return false;
  const [west, south, east, north] = item.bounds;
  if (!(west < east && south < north && west >= -180 && east <= 180 && south > -85.051129 && north < 85.051129)) return false;
  const expected = [[west, north], [east, north], [east, south], [west, south]];
  if (!Array.isArray(item.coordinates) || item.coordinates.length !== 4 || !item.coordinates.every((point, index) => Array.isArray(point) && point.length === 2 && point.every((number, axis) => Number.isFinite(number) && Math.abs(number - expected[index][axis]) < 0.000001))) return false;
  return Number.isFinite(item.resolution_m) && item.resolution_m > 0 && Number.isFinite(Date.parse(item.acquired_at));
}
async function loadImageryManifest() {
  if (basemap.manifestStatus === "loading") return;
  basemap.manifestStatus = "loading";
  updateBasemap();
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 12000);
  try {
    const response = await fetch("/static/imagery/manifest.json", { signal: controller.signal, credentials: "same-origin" });
    if (!response.ok) throw new Error("Manifest unavailable");
    const data = await response.json();
    if (data.schema !== "geoscan.h3.imagery.v1" || !Array.isArray(data.images) || data.images.length > 100 || !data.images.every(validImageryItem)) throw new Error("Invalid imagery manifest");
    if (new Set(data.images.map((item) => item.id)).size !== data.images.length) throw new Error("Duplicate imagery IDs");
    basemap.manifest = data;
    basemap.manifestStatus = "ready";
  } catch { basemap.manifest = null; basemap.manifestStatus = "error"; }
  finally { clearTimeout(timeout); updateBasemap(); }
}
function sceneBounds() {
  const points = Object.values(state.draft?.layers || {}).flatMap((layer) => (layer.features || []).flatMap((feature) => coordinatePoints(feature.geometry)));
  if (!points.length) return null;
  const bounds = [Infinity, Infinity, -Infinity, -Infinity];
  for (const [lon, lat] of points) { bounds[0] = Math.min(bounds[0], lon); bounds[1] = Math.min(bounds[1], lat); bounds[2] = Math.max(bounds[2], lon); bounds[3] = Math.max(bounds[3], lat); }
  return bounds;
}
function matchingImagery() {
  const bounds = sceneBounds();
  if (!bounds) return null;
  return (basemap.manifest?.images || []).filter((item) => item.bounds[0] <= bounds[0] && item.bounds[1] <= bounds[1] && item.bounds[2] >= bounds[2] && item.bounds[3] >= bounds[3]).sort((left, right) => (left.bounds[2] - left.bounds[0]) * (left.bounds[3] - left.bounds[1]) - (right.bounds[2] - right.bounds[0]) * (right.bounds[3] - right.bounds[1]))[0] || null;
}
function hideImageryLayers() {
  if (!state.mapReady) return;
  for (const id of basemap.layers) if (state.map.getLayer(id)) state.map.setLayoutProperty(id, "visibility", "none");
}
function showStreetMap(message = "") {
  basemap.generation += 1;
  basemap.pending = null;
  basemap.active = null;
  basemap.mode = "map";
  basemap.message = message;
  hideImageryLayers();
  setOverlayContrast(false);
  renderBasemap();
}
function setOverlayContrast(imagery) {
  if (!state.mapReady) return;
  if (basemap.contrast === imagery) return;
  basemap.contrast = imagery;
  for (const layer of state.map.getStyle().layers) {
    if (layer.id.startsWith("h3-polygon-fill")) state.map.setPaintProperty(layer.id, "fill-opacity", ["case", ["==", ["get", "user_layer_key"], "allowed_airspace"], imagery ? 0.015 : 0.045, imagery ? 0.055 : 0.17]);
    if (layer.id.startsWith("h3-polygon-halo")) state.map.setPaintProperty(layer.id, "line-opacity", imagery ? 0.8 : 0);
  }
  if (state.map.getLayer("routes-halo")) state.map.setPaintProperty("routes-halo", "line-opacity", imagery ? 0.85 : 0);
  if (state.map.getLayer("routes")) state.map.setPaintProperty("routes", "line-opacity", imagery ? 1 : 0.86);
}
function decodeLocalImage(url) {
  return new Promise((resolve, reject) => {
    const image = new Image();
    const timeout = setTimeout(() => { image.onload = null; image.onerror = null; image.src = ""; reject(new Error("Image timed out")); }, 20000);
    image.onload = () => { clearTimeout(timeout); if (image.naturalWidth && image.naturalHeight) resolve(); else reject(new Error("Empty image")); };
    image.onerror = () => { clearTimeout(timeout); reject(new Error("Image unavailable")); };
    image.src = url;
  });
}
async function updateBasemap() {
  if (!state.mapReady) return;
  if (basemap.preference === "terrain") { await showTerrainMap(); return; }
  if (basemap.preference === "map") { showStreetMap(); return; }
  if (!state.draft) { showStreetMap("Сцена не выбрана"); return; }
  if (["idle", "loading"].includes(basemap.manifestStatus)) { showStreetMap("Проверка локальных снимков..."); return; }
  if (basemap.manifestStatus === "error") { showStreetMap("Каталог снимков недоступен. Показана карта."); return; }
  const item = matchingImagery();
  if (!item) { showStreetMap("Снимок не покрывает всю сцену. Показана карта."); return; }
  if (basemap.failed.has(item.id)) { showStreetMap("Не удалось загрузить снимок. Показана карта."); return; }
  if (basemap.active?.id === item.id || basemap.pending === item.id) { renderBasemap(); return; }
  showStreetMap("Снимок загружается. Пока показана карта.");
  const generation = basemap.generation;
  basemap.pending = item.id;
  renderBasemap();
  try {
    await decodeLocalImage(item.url);
    if (generation !== basemap.generation || basemap.preference === "map") return;
    const id = `imagery-${item.id}`;
    if (!state.map.getSource(id)) state.map.addSource(id, { type: "image", url: item.url, coordinates: item.coordinates });
    if (!state.map.getLayer(id)) {
      const before = state.map.getStyle().layers.find((layer) => !["background", "osm"].includes(layer.id))?.id;
      state.map.addLayer({ id, type: "raster", source: id, paint: { "raster-opacity": 1, "raster-fade-duration": 0, "raster-resampling": "linear" } }, before);
      basemap.layers.add(id);
    }
    state.map.setLayoutProperty(id, "visibility", "visible");
    basemap.pending = null;
    basemap.active = item;
    basemap.mode = "imagery";
    basemap.message = "";
    setOverlayContrast(true);
    renderBasemap();
  } catch {
    if (generation !== basemap.generation) return;
    basemap.failed.add(item.id);
    showStreetMap("Не удалось загрузить снимок. Показана карта.");
  }
}
async function showTerrainMap() {
  const scene = state.scene;
  if (!scene?.terrain?.available) { showStreetMap("Нет файла высот. Загрузите GeoTIFF или загрузите рельеф."); return; }
  const key = `terrain-${scene.id}`;
  if (basemap.pending === key || basemap.mode === "terrain" && basemap.active?.id === key) return;
  showStreetMap("Загрузка карты высот…");
  const generation = basemap.generation;
  basemap.pending = key;
  renderBasemap();
  try {
    const preview = await api(`/scenes/${encodeURIComponent(scene.id)}/terrain-preview`);
    await decodeLocalImage(preview.image_url);
    if (generation !== basemap.generation || state.scene?.id !== scene.id || basemap.preference !== "terrain") return;
    const id = "terrain-preview";
    if (state.map.getLayer(id)) state.map.removeLayer(id);
    if (state.map.getSource(id)) state.map.removeSource(id);
    state.map.addSource(id, { type: "image", url: preview.image_url, coordinates: preview.coordinates });
    const before = state.map.getStyle().layers.find((layer) => !["background", "osm"].includes(layer.id))?.id;
    state.map.addLayer({ id, type: "raster", source: id, paint: { "raster-opacity": 1, "raster-fade-duration": 0, "raster-resampling": "nearest" } }, before);
    basemap.layers.add(id);
    basemap.active = { id: key, ...preview, source_label: scene.terrain.label };
    basemap.pending = null;
    basemap.mode = "terrain";
    basemap.message = "";
    setOverlayContrast(true);
    renderBasemap();
  } catch (error) {
    if (generation === basemap.generation) showStreetMap(`Карта высот недоступна: ${errorMessage(error)}`);
  }
}
function renderTerrainLegend() {
  const target = $("terrain-info");
  target.hidden = basemap.mode !== "terrain" || !basemap.active;
  if (target.hidden) return;
  const info = basemap.active;
  const bar = element("div", { class: "terrain-scale", "aria-hidden": "true" });
  bar.style.background = info.flat ? info.colors[2] : `linear-gradient(to right, ${info.colors.join(",")})`;
  target.replaceChildren(element("strong", { text: info.source_label }), bar,
    element("div", { class: "terrain-range" }, [element("span", { text: `${valueNumber(info.min_m, 1)} м` }), element("span", { text: `${valueNumber(info.max_m, 1)} м` })]),
    element("span", { class: "small-text", text: info.flat ? "Плоская поверхность: высота одинакова во всём файле." : "Высота поверхности в системе исходного файла. Цветовая шкала — для текущей сцены." }));
}
function showTerrainNotice(scene) {
  if (state.scene?.id !== scene.id || scene.terrain?.available) return;
  $("terrain-dialog-title").textContent = "Сценарий без рельефа";
  $("terrain-dialog-text").textContent = "В сценарии нет файла карты высот. Загрузить реальную модель поверхности Copernicus GLO-30 по координатам ваших точек с copernicus-dem-30m.s3.amazonaws.com? Можно также позже приложить свой GeoTIFF или рассчитать на плоской поверхности без учёта реальных перепадов высот.";
  const hasGeometry = Object.values(scene.layers || {}).some((layer) => layer.features?.length);
  $("terrain-download").hidden = false;
  $("terrain-download").disabled = !hasGeometry;
  $("terrain-flat").hidden = false;
  $("terrain-flat").disabled = !hasGeometry;
  $("terrain-dialog-hint").textContent = hasGeometry ? "Загрузка выполняется по вашему подтверждению. Нужен интернет или ранее загруженный кеш. Модель высот имеет шаг около 30 м и включает поверхность зданий и растительности. В плоском режиме вся поверхность принимается за 0 м; скачивание не требуется." : "Сначала добавьте области или точки на карту, чтобы определить территорию загрузки.";
  $("terrain-show-map").hidden = true;
  $("terrain-dialog-close").textContent = "Позже";
  if (!$("terrain-dialog").open) $("terrain-dialog").showModal();
}
function templateCode(item) { return item.display_code || item.id.split("_")[0]; }
function templateTitle(item) { return item.display_name || item.name; }
function templateGroups() {
  return [
    { id: "simple", label: "Простые тесты", items: state.scenarios.filter(item => !item.real_elevation) },
    { id: "complex", label: "Сложные примеры", items: state.scenarios.filter(item => item.real_elevation) }
  ].map(group => ({ ...group, items: group.items.sort((a,b) => a.id.localeCompare(b.id)) }));
}
function countLabel(count, forms) {
  const n = Math.abs(count), mod = n % 100;
  return `${count} ${forms[mod >= 11 && mod <= 14 ? 2 : n % 10 === 1 ? 0 : n % 10 >= 2 && n % 10 <= 4 ? 1 : 2]}`;
}

function renderBasemap() {
  const loading = Boolean(basemap.pending) || basemap.manifestStatus === "loading" && !["map", "terrain"].includes(basemap.preference);
  $("basemap-switch").setAttribute("aria-busy", String(loading));
  document.querySelectorAll("[data-basemap]").forEach((control) => { const active = control.dataset.basemap === basemap.mode; control.classList.toggle("active", active); control.setAttribute("aria-pressed", String(active)); });
  $("basemap-message").textContent = basemap.message;
  $("basemap-message").classList.toggle("loading", loading);
  $("map-network-warning").hidden = !basemap.osmUnavailable || basemap.mode !== "map";
  renderTerrainLegend();
  const info = $("imagery-info");
  info.hidden = basemap.mode !== "imagery" || !basemap.active;
  if (info.hidden) return;
  const item = basemap.active;
  const infoKey = `${item.id}|${item.acquired_at}|${basemap.manifest.attribution}`;
  if (info.dataset.imageKey === infoKey) return;
  info.dataset.imageKey = infoKey;
  const date = new Date(item.acquired_at).toLocaleDateString("ru-RU", { timeZone: "UTC" });
  const link = safeSourceLink(item.source_url) || safeSourceLink(basemap.manifest.source_url);
  const title = element("div", { class: "imagery-title" }, [element(link ? "a" : "strong", { href: link, target: link ? "_blank" : null, rel: link ? "noopener noreferrer" : null, text: `Sentinel-2 · ${date} · ${valueNumber(item.resolution_m, 0)} м`, title: item.name })]);
  if (/^\/static\/imagery\/[a-zA-Z0-9_.-]+\.tiff?$/.test(item.geotiff_url || "")) title.append(element("a", { class: "imagery-download", href: item.geotiff_url, download: "", title: "Скачать спутниковый снимок GeoTIFF", "aria-label": "Скачать спутниковый снимок GeoTIFF" }, icon("download")));
  info.replaceChildren(title, element("span", { class: "imagery-attribution", text: basemap.manifest.attribution || "Copernicus Sentinel data" }), element("span", { class: "imagery-scope", text: "Снимок не используется для проверки препятствий." }));
  icons();
}
function applyLayerVisibility() {
  if (!state.mapReady) return;
  for (const layer of state.map.getStyle().layers) {
    if (!layer.id.startsWith("h3-")) continue;
    if (!layer.metadata?.originalFilter) {
      state.map.getLayer(layer.id).__h3Filter ||= clone(layer.filter);
    }
    const original = state.map.getLayer(layer.id).__h3Filter;
    state.map.setFilter(layer.id, state.hiddenLayers.size ? ["all", original, ["!in", "user_layer_key", ...state.hiddenLayers]] : original);
  }
}
function coordinatePoints(geometry) {
  const output = [];
  function visit(item) { if (Array.isArray(item) && typeof item[0] === "number") output.push(item); else if (Array.isArray(item)) item.forEach(visit); }
  visit(geometry?.coordinates);
  return output;
}
function fitScene() {
  if (!state.mapReady || !state.draft) return;
  const features = state.draw.getAll().features;
  const preferred = features.filter((feature) => feature.properties.layer_key === "survey_areas");
  const points = (preferred.length ? preferred : features).flatMap((feature) => coordinatePoints(feature.geometry));
  for (const sortie of sorties()) for (const point of sortie.waypoints || []) if (Number.isFinite(point.lon) && Number.isFinite(point.lat)) points.push([point.lon, point.lat]);
  if (state.resultTab === "h1") for (const feature of h1Output()?.geojson?.features || []) for (const point of coordinatePoints(feature.geometry)) points.push(point);
  if (!points.length) return;
  const bounds = new maplibregl.LngLatBounds(points[0], points[0]);
  points.forEach((point) => bounds.extend(point));
  state.map.fitBounds(bounds, { padding: { top: 100, bottom: 55, left: 55, right: 55 }, maxZoom: 15, duration: 500 });
}
function setTool(tool) {
  if (!state.draft || !state.mapReady) return;
  state.tool = tool;
  state.draw.changeMode(tool === "select" ? "simple_select" : tool === "landing_sites" ? "draw_point" : "draw_polygon");
  updateTools();
  if (innerWidth < 761) $("sidebar").classList.remove("open");
}
function updateTools() { document.querySelectorAll("[data-tool]").forEach((control) => control.classList.toggle("active", control.dataset.tool === state.tool)); }
function objectName(feature) {
  const layer = feature.properties.layer_key;
  const features = state.draw?.getAll().features.filter(f => f.properties.layer_key === layer) || [];
  const index = features.findIndex(f => f.id === feature.id);
  return SceneEditor.names(features.map(f => f.properties), SceneEditor.prefixes[layer] || "Object")[index] || feature.properties.id;
}
function nextObjectName(layer) {
  const features = state.draw?.getAll().features.filter(f => f.properties.layer_key === layer && f.properties.id) || [];
  return SceneEditor.nextName(SceneEditor.prefixes[layer], [...features.map(f => f.properties.id), ...SceneEditor.names(features.map(f => f.properties), SceneEditor.prefixes[layer])]);
}
function uavName(uav) {
  const fleet = state.draft?.fleet?.uavs || [];
  return SceneEditor.names(fleet, "UAV")[fleet.indexOf(uav)] || uav.id;
}
function updateObjectSelection(scroll = false) {
  document.querySelectorAll("[data-feature-id]").forEach(node => {
    const selected = node.dataset.featureId === state.selected;
    node.classList.toggle("active", selected);
    if (node.matches("button")) node.setAttribute("aria-pressed", String(selected));
  });
  if (scroll && state.selected) {
    const row = [...document.querySelectorAll(".object-entry")].find(node => node.dataset.featureId === state.selected);
    row?.scrollIntoView({block:"nearest", behavior:"smooth"});
  }
}
function selectFeature(id) {
  if (!state.mapReady) return;
  const feature = state.draw.get(id);
  if (!feature) return;
  state.selected = id;
  state.hiddenLayers.delete(feature.properties.layer_key);
  applyLayerVisibility();
  state.sidebar = "scene";
  state.draw.changeMode("simple_select", {featureIds:[id]});
  renderSidebar(); updateObjectSelection();
  const points = coordinatePoints(feature.geometry);
  if (points.length && !points.some(point => state.map.getBounds().contains(point))) {
    const bounds = new maplibregl.LngLatBounds(points[0], points[0]);
    points.forEach(point => bounds.extend(point));
    state.map.fitBounds(bounds, {padding:70, maxZoom:16, duration:350});
  }
  $("delete-feature").disabled = false;
}
function objectRow(feature, sensor = false) {
  const label = objectName(feature);
  const control = button(label, LAYERS[feature.properties.layer_key].icon, () => selectFeature(feature.id), "object-select");
  control.dataset.featureId = feature.id;
  const remove = button("", "x", event => {
    event.stopPropagation(); state.selected = feature.id; deleteSelectedFeature();
  }, "icon-button danger object-remove");
  remove.setAttribute("aria-label", `Удалить ${label}`); remove.title = `Удалить ${label}`;
  const row = element("div", {class:"object-entry", "data-feature-id":feature.id}, [element("div", {class:"object-heading"}, [control, remove])]);
  if (sensor) row.append(areaSensorField(feature, `Датчик: ${label}`));
  return row;
}
function siteProperties() { return (state.draft?.layers.landing_sites?.features || []).map(f => f.properties); }
function repairSiteChoices() {
  const sites = siteProperties();
  let changed = false;
  for (const uav of state.draft.fleet.uavs) changed = SceneEditor.reconcile(uav, sites) || changed;
  if (changed) toast("Выбор баз БВС обновлён: недоступные базы исключены, старт и финиш при необходимости переключены на подходящую площадку.");
}
function baseTable(uav, index) {
  const sites = siteProperties();
  const selectedRefuels = SceneEditor.refuels(uav, sites);
  const columns = [["start_site", "Старт"], ["refuel_sites", "Дозарядка"], ["landing_site", "Финиш"]];
  const table = element("table", {class:"base-table", "aria-label":`Базы ${uavName(uav)}`});
  table.append(element("thead", {}, element("tr", {}, [element("th", {scope:"col", text:"База"}), ...columns.map(([,label]) => element("th", {scope:"col", text:label}))])));
  const body = element("tbody");
  for (const site of [null, ...sites]) {
    const feature = site && state.mapReady && state.draw.getAll().features.find(f => f.properties.layer_key === "landing_sites" && f.properties.id === site.id);
    const label = site ? (feature ? objectName(feature) : SceneEditor.names(sites, "Base")[sites.indexOf(site)]) : "Любая";
    const heading = site ? button(label, "map-pin", () => { if (feature) selectFeature(feature.id); }, "base-location") : element("span", {text:label});
    if (site) heading.title = `${label} · ${site.role === "both" ? "старт и посадка" : site.role === "start" ? "только старт" : site.role === "reserve" ? "резервная" : "только посадка"}${site.candidate ? " · предлагаемая" : ""}`;
    const row = element("tr", {}, element("th", {scope:"row"}, heading));
    for (const [column, title] of columns) {
      const allowed = SceneEditor.eligible(sites, column);
      const isRefuel = column === "refuel_sites";
      const enabled = site ? allowed.includes(site.id) : allowed.length > 0;
      const checked = isRefuel ? site ? selectedRefuels.includes(site.id) : allowed.length > 0 && selectedRefuels.length === allowed.length : (uav[column] ?? null) === (site?.id ?? null);
      const input = element("input", {type:isRefuel ? "checkbox" : "radio", name:`base-${index}-${column}`, checked, disabled:!enabled, "data-base-column":column, "data-site-id":site?.id || "", "aria-label":`${title}: ${label}`, onchange:() => {
        if (SceneEditor.choose(uav, sites, column, site?.id ?? null, input.checked)) { markDirty(); table.closest(".base-settings").replaceWith(baseTable(uav, index)); icons(); }
      }});
      if (!site && isRefuel) input.indeterminate = selectedRefuels.length > 0 && selectedRefuels.length < allowed.length;
      const cell = element("td", {title:enabled ? `${title}: ${label}` : "Площадка не поддерживает это действие"}, input);
      row.append(cell);
    }
    body.append(row);
  }
  table.append(body);
  return element("section", {class:"base-settings"}, [element("h4", {text:"Базы и дозарядка"}), table,
    element("p", {class:"small-text", text:"Старт и финиш: одна база или любая подходящая. Дозарядка: отметьте разрешённые базы; без отметок — без промежуточной зарядки."})]);
}

function renderProjectBar() {
  const template = state.scene?.template_readonly && state.scenarios.find(item => item.id === state.scene.scenario_id);
  const name = template ? `${templateCode(template)} · ${templateTitle(template)}` : state.draft?.name || state.scene?.name || "Сценарий не открыт";
  $("scenario-title").textContent = name + (hasUnsavedChanges() ? " *" : "");
  $("scenario-title").title = name;
}
function hasUnsavedChanges() {
  return Boolean(state.scene && (state.dirty || (!state.scene.saved && !state.scene.ready_to_run)));
}
function showOpenTab(tab) {
  document.querySelectorAll("[data-open-tab]").forEach(node => {
    node.setAttribute("aria-selected", String(node.dataset.openTab === tab));
    node.tabIndex = node.dataset.openTab === tab ? 0 : -1;
  });
  document.querySelectorAll("[data-open-panel]").forEach(node => { node.hidden = node.dataset.openPanel !== tab; });
  if (tab === "files") {
    $("import-base-label").hidden = !state.scene;
    $("input-template").hidden = !state.scene;
    if (state.scene) $("input-template").href = `/api/v1/scenes/${encodeURIComponent(state.scene.id)}/download`;
    renderImportSelection();
  }
}
function openWorkspace() {
  $("import-base").checked = false;
  showOpenTab(state.myScenarios.length ? "saved" : "templates");
  $("catalog-dialog").showModal();
}
async function refreshScenarios() {
  const data = await api("/scenes?saved_only=true");
  state.myScenarios = data.scenes || [];
  renderProjectBar(); renderCatalog();
}
function setScenarioLoading(loading) {
  state.sceneLoading = loading;
  $("map-loading").hidden = !loading;
  $("map-area").setAttribute("aria-busy", String(loading));
  updateControls();
}
async function withScenarioLoading(callback) {
  if (state.sceneLoading) return;
  setScenarioLoading(true);
  try { return await callback(); }
  finally { setScenarioLoading(false); }
}
async function openTemplate(id) {
  if (hasUnsavedChanges() && !confirm("Несохранённые изменения будут потеряны. Открыть шаблон?")) { renderProjectBar(); return; }
  return withScenarioLoading(async () => {
  const request = (state.sceneRequest || 0) + 1;
  state.sceneRequest = request;
  const scene = await api(`/scenes?scenario_id=${encodeURIComponent(id)}`, { method: "POST" });
  if (state.sceneRequest !== request) return;
  setScene(scene);
  $("catalog-dialog").close();
  toast("Шаблон открыт. Можно запустить расчёт. Изменения сохраняются через «Сохранить как».");
  showTerrainNotice(scene);
  });
}
function openSaveAs() { openScenarioName("save-as"); }
function openScenarioName(mode) {
  if (!state.draft || state.busy || calculating() || (mode === "rename" && state.scene.template_readonly)) return;
  state.namingMode = mode;
  $("scenario-name-title").textContent = mode === "rename" ? "Переименовать сценарий" : "Сохранить сценарий как";
  $("scenario-name-hint").textContent = mode === "rename" ? "Изменится только имя сценария. Текущий результат и несохранённые правки останутся в открытом окне." : "Будет создан отдельный сценарий в «Моих сценариях». Исходный останется без изменений.";
  $("save-as-form").querySelector("button[type=submit]").textContent = mode === "rename" ? "Переименовать" : "Сохранить";
  $("save-as-name").value = mode === "rename" ? state.draft.name : `${state.draft.name || "Новый сценарий"} — копия`;
  $("save-as-error").textContent = "";
  $("save-as-dialog").showModal();
  $("save-as-name").focus();
  $("save-as-name").select();
}
function scenarioListLabel(scene) {
  return scene.name;
}
function renderRunTab() {
  const target = $("run-info");
  target.replaceChildren();
  if (!state.scene) { target.append(element("p", { class: "small-text", text: "Сначала откройте или создайте сценарий." })); return; }
  const run = state.plan;
  target.append(element("h3", { text: "Расчёт сценария" }));
  if (!run) { target.append(element("p", { text: "Расчёт в этом окне ещё не запускался. Результат можно скачать после завершения." })); return; }
  const status = { COMPLETED: "Расчёт завершён", CANCELLED: "Расчёт остановлен", ERROR: "Расчёт завершился с ошибкой", queued: "Расчёт в очереди", running: "Расчёт выполняется" }[run.status] || run.status;
  target.append(element("p", { id: "run-tab-status", text: status }));
  const code = run.run_code || run.id;
  const codeInput = element("input", { id: "run-code-input", value: code, readOnly: true, "aria-label": "Код запуска", onclick: (event) => event.currentTarget.select() });
  const copy = button("Копировать код", "copy", async () => {
    try { await navigator.clipboard.writeText(code); toast("Код запуска скопирован."); }
    catch {
      codeInput.focus(); codeInput.select();
      toast(document.execCommand("copy") ? "Код запуска скопирован." : "Код выделен. Нажмите Ctrl+C.");
    }
  });
  target.append(element("label", { class: "field" }, [element("span", { text: "Код запуска" }), codeInput]), copy);
  const changed = state.dirty || run.matches_input === false || (run.input_sha256 && run.input_sha256 !== state.scene.input_sha256);
  if (changed) target.append(element("p", { class: "small-text warning", text: "Этот расчёт относится к прежним входным данным. Для текущих изменений запустите новый расчёт." }));
  else target.append(button("Показать результат", "panel-bottom", () => action(null, () => openPlan(run.id))));
}
async function openScene(id) {
  if (hasUnsavedChanges() && !confirm("Несохранённые изменения будут потеряны. Открыть другой сценарий?")) { return; }
  return withScenarioLoading(async () => {
  const request = (state.sceneRequest || 0) + 1;
  state.sceneRequest = request;
  const scene = await api(`/scenes/${encodeURIComponent(id)}`);
  if (state.sceneRequest !== request) return;
  setScene(scene);
  if (state.scene?.id === id) showTerrainNotice(scene);
  });
}
function setScene(scene) {
  clearTimeout(state.poll);
  pausePlayback();
  state.selectedH1Task = null;
  state.scene = scene;
  state.selectedUav = null;
  state.fleetOpen = new Set();
  state.draft = clone(scene);
  state.draft.layers ||= {};
  for (const key of Object.keys(LAYERS)) state.draft.layers[key] ||= collection();
  state.draft.fleet = Array.isArray(state.draft.fleet) ? { uavs: state.draft.fleet } : state.draft.fleet || { uavs: [] };
  state.draft.mission ||= {};
  state.draft.payload_catalog ||= { payload_profiles: [] };
  state.dirty = false;
  state.selected = null;
  state.plan = null;
  state.sidebar = "scene";
  state.tool = "select";
  $("result-dock").hidden = true;
  $("plan-overlay").hidden = true;
  $("objective").value = scene.mission?.objectives?.[0] || "makespan";
  localStorage.setItem("geoscan.h3.last-scene", scene.id);
  renderProjectBar();
  paintScene();
  renderSidebar();
  paintPlan();
  resetEditHistory();
  updateControls();
  updateTools();

  $("terrain-label").textContent = scene.terrain?.label || "Рельеф не приложен";
  updateBasemap();
  if (state.map) setTimeout(() => state.map.resize(), 0);
}
function editableSnapshot() {
  return clone(Object.fromEntries(["name", "layers", "mission", "fleet", "payload_catalog"].map((key) => [key, state.draft[key]])));
}
function resetEditHistory() {
  state.editHistory = [editableSnapshot()];
  state.editIndex = 0;
  state.cleanEdit = JSON.stringify(state.editHistory[0]);
}
function checkpointEdit() {
  if (state.restoringEdit) return;
  const current = editableSnapshot();
  const encoded = JSON.stringify(current);
  if (encoded !== JSON.stringify(state.editHistory[state.editIndex])) {
    state.editHistory.splice(state.editIndex + 1);
    state.editHistory.push(current);
    if (state.editHistory.length > 100) state.editHistory.shift();
    state.editIndex = state.editHistory.length - 1;
  }
  state.dirty = encoded !== state.cleanEdit;
}
function undoEdit(direction) {
  if (!state.draft || state.busy || calculating()) return;
  const next = state.editIndex + direction;
  if (next < 0 || next >= state.editHistory.length) return;
  state.editIndex = next;
  state.restoringEdit = true;
  try {
    Object.assign(state.draft, clone(state.editHistory[next]));
    state.selected = null;
    state.tool = "select";
    if (state.mapReady) {
      state.draw.changeMode("simple_select");
      state.draw.set(flattenLayers());
      applyLayerVisibility();
    }
    markDirty();
    state.dirty = JSON.stringify(editableSnapshot()) !== state.cleanEdit;
    renderSidebar(); updateControls(); updateTools();
  } finally { state.restoringEdit = false; }
}
function markDirty() {
  checkpointEdit();
  updateBasemap();
  if (state.plan) {
    state.plan = null;
    clearTimeout(state.poll);
    pausePlayback();
    $("result-dock").hidden = true;
    $("plan-overlay").hidden = true;
    paintPlan();
    toast("Входные данные изменены. Для новых данных потребуется новый расчёт.");
  }
  updateControls();
}
function calculating() { return Boolean(state.plan && !TERMINAL.has(String(state.plan.status).toUpperCase())); }
const SAVE_BEFORE_RUN = "Сначала сохраните изменения. Для шаблона используйте «Сохранить как…».";
function needsSaveBeforeRun() { return Boolean(state.scene && (state.dirty || !state.scene.ready_to_run)); }
function updateControls() {
  const locked = state.busy || state.sceneLoading || calculating();
  renderProjectBar();
  $("objective").disabled = locked || state.role === "viewer";
  for (const option of $("objective").options) option.disabled = !state.draft?.mission?.objectives?.includes(option.value);
  $("sidebar-content").inert = locked;
  $("map").inert = locked;
  for (const id of ["catalog-open", "import-open", "refresh-scenarios"]) $(id).disabled = locked;
  $("dirty-badge").hidden = !hasUnsavedChanges();
  $("save-scene").disabled = !state.draft || state.scene?.template_readonly || (!state.dirty && state.scene?.saved) || locked;
  $("save-scene").title = state.scene?.template_readonly ? "Шаблон нельзя перезаписать — используйте «Сохранить как»" : "Сохранить изменения (Ctrl+S)";
  $("save-as-scene").disabled = !state.draft || locked;
  $("rename-scene").disabled = !state.draft || state.scene?.template_readonly || locked;
  $("delete-scene").disabled = !state.scene || state.scene.template_readonly || locked;
  $("undo-edit").disabled = !state.draft || locked || state.editIndex <= 0;
  $("redo-edit").disabled = !state.draft || locked || state.editIndex >= state.editHistory.length - 1;
  $("stop-plan").disabled = !calculating();
  $("run-live").disabled = locked || !state.scene || needsSaveBeforeRun() || state.scene.validation?.valid === false;
  $("run-live-label").textContent = "Запустить расчёт";
  $("run-save-hint").hidden = !needsSaveBeforeRun();
  $("run-save-hint").textContent = state.scene?.template_readonly ? "Сохраните изменённый шаблон кнопкой «Сохранить как…»." : "Сохраните изменения сценария перед расчётом.";
  $("version-badge").textContent = !state.scene ? "Нет сценария" : state.scene.template_readonly ? (hasUnsavedChanges() ? "Правки шаблона" : "Шаблон · сохранён") : state.scene.saved && !state.dirty ? "Сохранён" : state.scene.ready_to_run && !state.dirty ? "Из файла" : "Не сохранён";
  $("run-fixture").disabled = true;
  $("delete-feature").disabled = locked || !state.selected;
  document.querySelectorAll("[data-tool]").forEach((control) => { control.disabled = !state.draft || !state.mapReady || locked; });
  if (state.role !== "admin") $("delete-scene").disabled = true;
  if (state.role === "viewer") {
    for (const id of ["import-open","new-blank","save-scene","save-as-scene","rename-scene","delete-scene","undo-edit","redo-edit","stop-plan","run-live","run-fixture","delete-feature"]) $(id).disabled = true;
    document.querySelectorAll("[data-tool]").forEach(control => {control.disabled=true;});
    $("sidebar-content").inert = true;
  }
}
async function saveScene(publish = true, saveAsName = null) {
  if (!state.draft) return;
  if (publish && !saveAsName && state.scene.template_readonly) { openSaveAs(); return; }
  syncLayers();
  const editHistory = state.editHistory, editIndex = state.editIndex;
  const selectedObjective = $("objective").value;
  state.busy = true;
  updateControls();
  try {
    let scene = state.scene;
    const previousPlan = state.plan;
    if (state.dirty) {
      toast("Сохранение сценария...");
      const draft = state.draft;
      scene = await api(`/scenes/${encodeURIComponent(scene.id)}/versions`, { method: "POST", body: { name: draft.name, layers: draft.layers, mission: draft.mission, fleet: draft.fleet, payload_catalog: draft.payload_catalog, auto_dem: false } });
    }
    if (saveAsName) scene = await api(`/scenes/${encodeURIComponent(scene.id)}/save-as`, { method: "POST", body: { name: saveAsName } });
    else if (publish) scene = await api(`/scenes/${encodeURIComponent(scene.id)}/save`, { method: "POST" });
    setScene(scene);
    if (scene.mission?.objectives?.includes(selectedObjective)) $("objective").value = selectedObjective;
    if (!saveAsName) { state.editHistory = editHistory; state.editIndex = editIndex; }
    if (previousPlan?.scene_id === scene.id) { state.plan = previousPlan; renderResult(); }
    await refreshScenarios();
    if (publish) toast(scene.validation?.valid === false ? "Сценарий сохранён в «Мои сценарии». Перед расчётом исправьте ошибки входа." : "Сценарий сохранён в «Мои сценарии».");
    if (publish && !scene.terrain?.covers_scene) showTerrainNotice(scene);
    return scene;
  } finally { state.busy = false; updateControls(); }
}
async function useFlatTerrain() {
  if (!state.scene || state.busy || calculating()) return;
  if (state.dirty) { toast("Сначала сохраните координаты сцены.", true); return; }
  const objective = $("objective").value;
  state.busy = true;
  updateControls();
  try {
    const scene = await api(`/scenes/${encodeURIComponent(state.scene.id)}/terrain/flat`, { method: "POST", body: {} });
    setScene(scene);
    $("objective").value = objective;
    await refreshScenarios();
    toast("Выбрана плоская поверхность. Сохраните сцену; расчёт запускается обычной кнопкой.");
  } finally { state.busy = false; updateControls(); }
}

async function updateTerrain(control) {
  if (state.dirty) { toast("Сначала сохраните изменения геометрии.", true); return; }
  await action(control, async () => {
    state.busy = true;
    updateControls();
    try {
      toast("Загрузка рельефа. Большая область может потребовать нескольких минут.");
      const scene = await api(`/scenes/${encodeURIComponent(state.scene.id)}/terrain`, { method: "POST", body: {} });
      setScene(scene);
      await refreshScenarios();
      toast(scene.terrain?.covers_scene ? "Модель поверхности готова. Сохраните сценарий." : scene.terrain_error || "Модель поверхности недоступна.", !scene.terrain?.covers_scene);
      showTerrainNotice(scene);
    } finally { state.busy = false; updateControls(); }
  });
}

function section(title, children = [], extra = null) {
  return element("section", { class: "panel-section" }, [element("div", { class: "section-title" }, [element("h3", { text: title }), extra]), ...children]);
}
function field(label, value, onChange, type = "text", attributes = {}) {
  const input = element("input", { type, value: value ?? "", ...attributes });
  input.addEventListener("change", () => onChange(type === "number" ? input.valueAsNumber : input.value));
  return element("label", { class: "field" }, [element("span", { text: label }), input]);
}
function selectField(label, value, choices, onChange) {
  const input = element("select", { "aria-label": label, onchange: () => onChange(input.value) }, choices.map((choice) => element("option", { value: Array.isArray(choice) ? choice[0] : choice, text: Array.isArray(choice) ? choice[1] : choice })));
  input.value = value || "";
  return element("label", { class: "field" }, [element("span", { text: label }), input]);
}
function dateInput(value) {
  if (!value || Number.isNaN(Date.parse(value))) return "";
  const parts = new Intl.DateTimeFormat("sv-SE", { timeZone: "Europe/Moscow", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).format(new Date(value));
  return parts.replace(" ", "T");
}
function isoDate(value) { return value ? `${value}:00+03:00` : null; }
function displayDate(value) { return value ? new Date(value).toLocaleString("ru-RU", { timeZone: "Europe/Moscow", day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }) : "Нет данных"; }
function displayTime(value) { return value ? new Date(value).toLocaleTimeString("ru-RU", { timeZone: "Europe/Moscow", hour: "2-digit", minute: "2-digit", second: "2-digit" }) : "--:--:--"; }
function profiles() { return state.draft?.payload_catalog?.payload_profiles || []; }
function renderSidebar() {
  document.querySelectorAll("[data-template-folder]").forEach(control => control.addEventListener("click", () => {
  state.templateFolder=control.dataset.templateFolder; state.templateQuery=""; $("template-search").value=""; renderTemplateCatalog();
}));
$("template-search").addEventListener("input", event => { state.templateQuery=event.target.value; renderTemplateCatalog(); });
document.querySelectorAll("[data-sidebar]").forEach((tab) => tab.classList.toggle("active", tab.dataset.sidebar === state.sidebar));
  const target = $("sidebar-content");
  target.hidden = state.sidebar === "run";
  $("run-pane").hidden = state.sidebar !== "run";
  target.replaceChildren();
  if (state.sidebar === "run") { renderRunTab(); updateControls(); icons(); return; }
  if (!state.draft) {
    target.append(element("div", { class: "empty-state" }, [icon("map-pinned"), element("h3", { text: "Новая миссия" }), element("p", { text: "Нажмите «Открыть…» в строке сценария сверху: выберите шаблон, свои файлы или создайте пустой сценарий." }), element("p", { class: "small-text", text: "Сохранённые сценарии появятся во вкладке «Мои сценарии»." })]));
    icons();
    return;
  }
  if (state.sidebar === "scene") renderSceneSidebar(target);
  if (state.sidebar === "fleet") renderFleetSidebar(target);
  icons();
}
function renderSceneSidebar(target) {
  const draft = state.draft;
  const validation = state.scene.validation || {};
  const surveySection = section("Области съёмки и датчики", [element("p", { class: "small-text", text: "Выберите датчик для каждой области. Его наличие на БВС проверяется позднее и не ограничивает этот список." })]);
  if (state.mapReady) for (const feature of state.draw.getAll().features.filter(item => item.properties.layer_key === "survey_areas")) surveySection.append(objectRow(feature, true));
  if (!draft.layers.survey_areas.features.length) surveySection.append(element("p", { class: "small-text", text: "Добавьте область кнопкой «Новая область съёмки» на карте." }));
  target.append(surveySection);
  const layerRows = [];
  for (const [key, config] of Object.entries(LAYERS)) {
    const checkbox = element("input", { type: "checkbox", checked: !state.hiddenLayers.has(key), onchange: () => { if (checkbox.checked) state.hiddenLayers.delete(key); else state.hiddenLayers.add(key); applyLayerVisibility(); } });
    layerRows.push(element("div", { class: "layer-row" }, [element("label", {}, [checkbox, element("span", { class: "layer-color", style: `background:${config.color}` }), config.label]), element("span", { class: "layer-count", text: draft.layers[key]?.features?.length || 0 })]));
  }
  const objectList = element("ul", { class: "object-list" });
  if (state.mapReady) for (const feature of state.draw.getAll().features) {
    const config = LAYERS[feature.properties.layer_key];
    if (!config) continue;
    if (feature.properties.layer_key === "survey_areas") continue;
    objectList.append(element("li", {}, objectRow(feature)));
  }
  target.append(section("Слои", [...layerRows, objectList]));
  target.append(element("div", { id: "feature-properties" }));
  renderProperties(); updateObjectSelection();
  const wind = draft.mission.wind ||= {};
  const window = draft.mission.mission_window ||= {};
  target.append(section("Условия миссии", [
    element("div", { class: "field-grid" }, [field("Ветер, м/с", wind.speed_ms || 0, (value) => { wind.speed_ms = value; markDirty(); }, "number", { min: 0, max: 100, step: 0.5 }), field("Откуда, градусы", wind.direction_deg_from || 0, (value) => { wind.direction_deg_from = value; markDirty(); }, "number", { min: 0, max: 359, step: 1 })]),
    field("Начало окна, МСК", dateInput(window.start), (value) => { window.start = isoDate(value); markDirty(); }, "datetime-local"),
    field("Конец окна, МСК", dateInput(window.end), (value) => { window.end = isoDate(value); markDirty(); }, "datetime-local"),
    ...(draft.mission.daylight_window ? [
      field("Начало светового окна, МСК", dateInput(draft.mission.daylight_window.start), (value) => { draft.mission.daylight_window.start = isoDate(value); markDirty(); }, "datetime-local"),
      field("Конец светового окна, МСК", dateInput(draft.mission.daylight_window.end), (value) => { draft.mission.daylight_window.end = isoDate(value); markDirty(); }, "datetime-local"),
      element("p", { class: "small-text", text: "Полёты разрешены только в пересечении окна миссии и светового окна. Временные запретные зоны дополнительно исключают полёты внутри них в указанные часы." }),
    ] : []),
    field("Подготовка перед первым вылетом, мин", (draft.mission.preparation_time_s ?? 0) / 60, value => { draft.mission.preparation_time_s = value * 60; markDirty(); }, "number", {min:0,max:1440,step:1}),
    field("Снятие данных после последней посадки, мин", (draft.mission.data_download_time_s ?? 0) / 60, value => { draft.mission.data_download_time_s = value * 60; markDirty(); }, "number", {min:0,max:1440,step:1}),
    element("p", {class:"small-text", text:"Подготовка и снятие данных входят во время миссии и должны помещаться в её разрешённое окно. Это общие длительности для группы; подготовка аппаратов считается параллельной. В старых сценариях они равны 0, пока вы их не зададите."}),
    field("Обслуживание между вылетами, мин", draft.mission.service_time_min ?? 7, (value) => { draft.mission.service_time_min = value; markDirty(); }, "number", { min: 0, max: 1440, step: 1 }),
    button("Все ограничения миссии", "settings-2", () => jsonEditor("Ограничения миссии", draft.mission, (value) => { draft.mission = value; markDirty(); renderSidebar(); })),
  ]));
  const terrainButton = button("Обновить рельеф", "download-cloud", (event) => updateTerrain(event.currentTarget));
  const metadata = draft.metadata || {};
  const terrainText = validation.terrain_available ? "Высотная модель доступна" : "Высотная модель отсутствует или не покрывает сцену";
  target.append(section("Модель поверхности", [element("p", { class: "small-text", text: terrainText }), terrainButton, button("Источник и допущения", "info", () => jsonEditor("Источник рельефа и допущения", metadata, null))]));
  if (validation.errors?.length || validation.warnings?.length) target.append(section("Проверка входа", [renderIssues(validation.errors || [], false), renderIssues(validation.warnings || [], true)]));
  target.append(element("div", { class: "project-note", text: state.scene.template_readonly ? "Шаблон защищён от перезаписи. Можно рассчитать сразу; правки сохраните как свой сценарий." : state.scene.saved ? "Сценарий сохранён. Результат расчёта скачивается отдельно." : state.scene.ready_to_run ? "Сценарий открыт из файлов. Можно рассчитать сразу или сохранить в «Мои сценарии»." : "Сохраните сценарий, чтобы добавить его в «Мои сценарии» и запустить расчёт." }));
}
function renderIssues(items, warning = false) {
  return element("ul", { class: `validation-list${warning ? " warnings" : ""}` }, items.map((issue) => element("li", { text: detailsMessage(issue) })));
}
function areaSensorField(feature, label) {
  const types = [...new Set([...state.payloadDefaults, ...profiles()].map((profile) => profile.type))];
  return selectField(label, feature.properties.survey_type, types.map((type) => [type, SENSOR_LABELS[type] || type]), (value) => {
    let profile = profiles().find((item) => item.type === value);
    if (!profile) {
      profile = clone(state.payloadDefaults.find((item) => item.type === value));
      const ids = new Set(profiles().map((item) => item.id));
      const base = profile.id;
      for (let i = 1; ids.has(profile.id); i++) profile.id = `${base}_${i}`;
      state.draft.payload_catalog.payload_profiles.push(profile);
    }
    state.draw.setFeatureProperty(feature.id, "survey_type", value);
    state.draw.setFeatureProperty(feature.id, "payload_profile", profile.id);
    syncLayers(); markDirty(); renderSidebar();
  });
}
function renderProperties() {
  const target = $("feature-properties");
  if (!target) return;
  const feature = state.selected && state.draw?.get(state.selected);
  if (!feature) { target.replaceChildren(); return; }
  const properties = feature.properties;
  const layer = properties.layer_key;
  const update = (key, value) => { state.draw.setFeatureProperty(feature.id, key, value); syncLayers(); if (layer === "landing_sites") repairSiteChoices(); markDirty(); };
  const fields = [field("Название", objectName(feature), (value) => { update("name", value.trim() || properties.id); renderSidebar(); }, "text", { required: true })];
  if (layer === "survey_areas") {
    fields.push(areaSensorField(feature, "Датчик этой области"));
    fields.push(selectField("Профиль оборудования", properties.payload_profile, profiles().filter((profile) => profile.type === properties.survey_type).map((profile) => [profile.id, profile.id]), (value) => update("payload_profile", value)));
    fields.push(element("p", { class: "small-text", text: "Для нескольких датчиков на одной территории создайте копию области и выберите другой датчик." }), button("Дублировать область", "copy", () => {
      const duplicate = clone(state.draw.get(feature.id));
      delete duplicate.id;
      duplicate.properties.id = nextObjectName("survey_areas");
      delete duplicate.properties.name;
      state.selected = state.draw.add(duplicate)[0];
      syncLayers(); markDirty(); renderSidebar();
    }));
  }
  if (layer === "landing_sites") {
    fields.push(selectField("Назначение площадки", properties.role || "both", [["both", "Старт и посадка"], ["start", "Только старт"], ["landing", "Только посадка"], ["reserve", "Резервная"]], (value) => update("role", value)));
    const candidate = element("input", { type: "checkbox", checked: properties.candidate === true, onchange: () => update("candidate", candidate.checked) });
    fields.push(element("label", { class: "layer-row" }, [candidate, "Предлагаемая площадка"]));
  }
  if (layer === "obstacles") for (const [key, label] of [["height_m", "Высота препятствия, м"], ["horizontal_buffer_m", "Горизонтальный запас, м"], ["vertical_buffer_m", "Вертикальный запас, м"]]) fields.push(field(label, properties[key] || 0, (value) => update(key, value), "number", { min: 0, max: 10000, step: 1 }));
  if (layer === "temporal_airspace") {
    for (const [key, label] of [["active_from", "Начало запрета, МСК"], ["active_to", "Конец запрета, МСК"]]) fields.push(field(label, dateInput(properties[key]), (value) => update(key, isoDate(value)), "datetime-local"));
    for (const [key, label] of [["min_alt_m", "Нижняя граница AMSL, м"], ["max_alt_m", "Верхняя граница AMSL, м"]]) fields.push(field(label, properties[key] ?? (key.startsWith("max") ? 500 : 0), (value) => update(key, value), "number", { min: -500, max: 20000 }));
  }
  if (feature.geometry.type === "Point") fields.push(element("div", { class: "field-grid" }, [field("Широта", feature.geometry.coordinates[1], (value) => movePoint(feature.id, 1, value), "number", { min: -85, max: 85, step: 0.00001 }), field("Долгота", feature.geometry.coordinates[0], (value) => movePoint(feature.id, 0, value), "number", { min: -180, max: 180, step: 0.00001 })]));
  fields.push(button("Свойства GeoJSON", "braces", () => jsonEditor("Свойства объекта", Object.fromEntries(Object.entries(properties).filter(([key]) => key !== "layer_key")), (value) => {
    const updated = state.draw.get(feature.id);
    updated.properties = { ...value, layer_key: layer };
    state.draw.add(updated);
    syncLayers(); markDirty(); renderSidebar();
  })));
  target.replaceChildren(section(LAYERS[layer]?.label || "Объект", fields));
  icons();
}
function movePoint(id, index, value) {
  const feature = state.draw.get(id);
  feature.geometry.coordinates[index] = value;
  state.draw.add(feature);
  syncLayers();
  markDirty();
}

function renderFleetSidebar(target) {
  const fleet = state.draft.fleet.uavs;
  const create = button("Добавить", "plus", () => {
    if (fleet.length >= 10) { toast("Для этой версии сервиса максимум 10 БВС.", true); return; }
    const preset = state.presets[0];
    if (!preset) { toast("Каталог моделей недоступен. Проверьте подключение к серверу.", true); return; }
    const site = state.draft.layers.landing_sites.features.find((feature) => ["both", "start"].includes(feature.properties?.role))?.properties?.id || "";
    fleet.push({ ...clone(preset), id: SceneEditor.nextName("UAV", [...fleet.map(u => u.id), ...SceneEditor.names(fleet, "UAV")]), start_site: site || null, landing_site: null, refuel_sites: null });
    markDirty(); renderSidebar();
  });
  target.append(section("Парк БВС", [element("p", { class: "small-text", text: `${fleet.length} из 10 · операционные параметры` })], create));
  fleet.forEach((uav, index) => {
    const modify = (key, value) => { uav[key] = value; uav.operator_override = true; markDirty(); };
    const fields = element("div", { class: "fleet-fields" }, element("h4", {class:"fleet-properties-title", text:`Свойства ${uavName(uav)}`}));
    const modelChoices = ["geoscan_201", "geoscan_401", "geoscan_701", "geoscan_801", "gemini"].map((model) => [model, model === "gemini" ? "Geoscan Gemini" : `Geoscan ${model.split("_")[1]}`]);
    fields.append(field("Название БВС", uavName(uav), (value) => { modify("name", value.trim() || uav.id); renderSidebar(); }, "text", { required: true }), selectField("Модель", uav.model, modelChoices, (value) => {
      const preset = state.presets.find((item) => item.model === value);
      if (!preset) { toast("Параметры выбранной модели отсутствуют в каталоге.", true); return; }
      const id = uav.id, start = uav.start_site, end = uav.landing_site;
      Object.assign(uav, clone(preset), { id, start_site: start, landing_site: end, operator_override: false }); markDirty(); renderSidebar();
    }));
    fields.append(element("div", { class: "field-grid" }, [field("Скорость, км/ч", uav.ground_speed_kmh, (value) => modify("ground_speed_kmh", value), "number", { min: 1, max: 500, step: 1 }), field("Ресурс, мин", uav.operational_endurance_min, (value) => modify("operational_endurance_min", value), "number", { min: 1, max: 1440, step: 1 }), field("Резерв, %", Math.round((uav.energy_reserve_fraction ?? 0.2) * 100), (value) => modify("energy_reserve_fraction", value / 100), "number", { min: 10, max: 50, step: 1 }), field("Макс. ветер, м/с", uav.max_wind_ms, (value) => modify("max_wind_ms", value), "number", { min: 0, max: 50, step: 0.5 })]));
    fields.append(baseTable(uav, index));
    const payloads = element("div", { class: "field" }, element("label", { text: "Совместимое оборудование" }));
    for (const type of ["rgb", "multispectral", "thermal", "lidar", "geophysics"]) {
      const check = element("input", { type: "checkbox", checked: (uav.payload_classes || []).includes(type), onchange: () => { const values = new Set(uav.payload_classes || []); if (check.checked) values.add(type); else values.delete(type); modify("payload_classes", [...values]); } });
      payloads.append(element("label", { class: "layer-row" }, [check, type]));
    }
    fields.append(payloads, element("p", { class: "override-label", text: "Изменённые значения считаются пользовательскими допущениями." }), button("Все параметры БВС", "settings-2", () => jsonEditor("Параметры БВС", uav, (value) => { fleet[index] = value; markDirty(); renderSidebar(); })));
    const copyUav = button("", "copy", (event) => {
      event.preventDefault(); event.stopPropagation();
      if (fleet.length >= 10) { toast("Для этой версии сервиса максимум 10 БВС.", true); return; }
      const duplicate = {...clone(uav), id:SceneEditor.nextName("UAV", [...fleet.map(u => u.id), ...SceneEditor.names(fleet, "UAV")])};
      delete duplicate.name;
      fleet.splice(index + 1, 0, duplicate);
      markDirty(); renderSidebar();
    }, "icon-button");
    copyUav.title = `Копировать БВС ${uav.id}`;
    copyUav.setAttribute("aria-label", copyUav.title);
    const deleteUav = button("", "trash-2", (event) => {
      event.preventDefault(); event.stopPropagation();
      fleet.splice(index, 1); markDirty(); renderSidebar();
    }, "icon-button danger");
    deleteUav.title = `Удалить БВС ${uav.id}`;
    deleteUav.setAttribute("aria-label", deleteUav.title);
    const details = element("details", {class:"fleet-item", "data-uav-id":uav.id, open:state.fleetOpen.has(uav.id)}, [element("summary", {}, [
      icon("plane"), element("span", {class:"fleet-identity"}, [element("strong", {text:uavName(uav)}), badge(uav.model)]),
      element("span", {class:"fleet-actions"}, [copyUav, deleteUav]),
      element("span", {class:"fleet-expand", "aria-hidden":"true"}, [element("span", {class:"when-closed"}, icon("chevron-down")), element("span", {class:"when-open"}, icon("x"))]),
    ]), fields]);
    details.querySelector("summary").setAttribute("aria-label", `Свойства ${uavName(uav)}`);
    details.addEventListener("toggle", () => {
      if (!details.isConnected) return;
      details.classList.toggle("animate-opening", details.open && !state.fleetOpen.has(uav.id));
      if (details.open) state.fleetOpen.add(uav.id); else state.fleetOpen.delete(uav.id);
      state.selectedUav = details.open ? uav.id : null;
      highlightUav();
    });
    target.append(details);
  });
  const payloadSection = section("Оборудование и съёмка");
  profiles().forEach((profile) => {
    const update = (key, value) => { profile[key] = value; markDirty(); };
    payloadSection.append(element("h4", { text: `${profile.id} · ${profile.type}` }), element("div", { class: "field-grid" }, [field("GSD, см/пикс.", profile.gsd_cm, (value) => update("gsd_cm", value), "number", { min: 0.1, max: 100, step: 0.1 }), field("Высота AGL, м", profile.nominal_agl_m, (value) => update("nominal_agl_m", value), "number", { min: 10, max: 5000, step: 1 }), field("Продольное, %", (profile.front_overlap ?? 0.7) * 100, (value) => update("front_overlap", value / 100), "number", { min: 0, max: 99, step: 1 }), field("Поперечное, %", (profile.side_overlap ?? 0.6) * 100, (value) => update("side_overlap", value / 100), "number", { min: 0, max: 99, step: 1 })]));
    if (["lidar", "geophysics"].includes(profile.type)) payloadSection.append(field("Шаг галсов, м", profile.line_spacing_m, (value) => update("line_spacing_m", value), "number", { min: 1, max: 1000, step: 1 }));
  });
  payloadSection.append(button("Каталог оборудования", "braces", () => jsonEditor("Каталог оборудования", state.draft.payload_catalog, (value) => { state.draft.payload_catalog = value; markDirty(); renderSidebar(); })));
  target.append(payloadSection);
}
function jsonEditor(title, value, apply) {
  state.jsonApply = apply;
  $("json-title").textContent = title;
  $("json-editor").value = JSON.stringify(value, null, 2);
  $("json-editor").readOnly = !apply;
  $("json-form").querySelector("button[type=submit]").hidden = !apply;
  $("json-error").textContent = "";
  $("json-dialog").showModal();
}

function renderTemplateCatalog() {
  const groups = templateGroups();
  const group = groups.find(item => item.id === state.templateFolder) || groups[0];
  for (const folder of groups) {
    $(`folder-count-${folder.id}`).textContent = folder.items.length;
    document.querySelector(`[data-template-folder="${folder.id}"]`).setAttribute("aria-pressed", String(folder.id === group.id));
  }
  $("template-folder-label").textContent = group.label;
  const query = state.templateQuery.trim().toLocaleLowerCase("ru").replace(/ё/g, "е");
  const items = group.items.filter(item => [templateCode(item), item.id.replace(/^S\d+_/, ""), item.name, templateTitle(item), item.summary, ...(item.sensor_types || []).map(sensor => `${sensor} ${SENSOR_LABELS[sensor] || ""}`)].join(" ").toLocaleLowerCase("ru").replace(/ё/g, "е").includes(query));
  $("catalog-results").textContent = query ? `Найдено ${items.length} из ${group.items.length}` : `${countLabel(items.length, ["сценарий", "сценария", "сценариев"])} · выберите карточку, чтобы открыть`;
  $("catalog-list").replaceChildren(...items.map(scenario => {
    const real = Boolean(scenario.real_elevation);
    const facts = [`${scenario.fleet_count} БВС`];
    if (Number.isFinite(scenario.zone_count)) facts.push(countLabel(scenario.zone_count, ["зона", "зоны", "зон"]));
    if (Number.isFinite(scenario.site_count)) facts.push(countLabel(scenario.site_count, ["площадка", "площадки", "площадок"]));
    const sensors = (scenario.sensor_types || []).map(type => SENSOR_LABELS[type] || type).join(" · ");
    const wind = Number.isFinite(scenario.wind_ms) ? scenario.wind_ms === 0 ? "Без ветра" : `Ветер ${valueNumber(scenario.wind_ms)} м/с` : "";
    const control = element("button", { class: "scenario-item", type: "button" }, [
      element("div", { class: "scenario-top" }, [element("span", { class: "scenario-code", text: templateCode(scenario) }), icon(real ? "mountain" : "scan")]),
      element("strong", { class: "scenario-name", text: templateTitle(scenario) }),
      element("p", { class: "scenario-description", text: scenario.summary || scenario.purpose_ru || scenario.description }),
      element("div", { class: "scenario-facts", text: facts.join(" · ") }),
      sensors ? element("p", { class: "scenario-sensors", text: sensors }) : null,
      element("div", { class: "scenario-badges" }, [badge(real ? "Реальный рельеф" : "Плоская поверхность"), wind ? badge(wind) : null, scenario.expected_status === "INFEASIBLE" ? badge("Ожидаемый отказ", "warn") : null]),
      element("span", { class: "scenario-open" }, ["Открыть сценарий", icon("arrow-up-right")]),
    ]);
    control.dataset.templateId = scenario.id;
    control.disabled = state.role === "viewer";
    control.addEventListener("click", () => action(control, () => openTemplate(scenario.id)));
    return control;
  }));
  if (!items.length) $("catalog-list").append(element("div", {class:"catalog-empty"}, [icon("search"), element("h3", {text:query ? "Ничего не найдено" : "В папке пока нет сценариев"}), element("p", {text:query ? "Попробуйте другой запрос или выберите другую папку." : "Обновите список сценариев."}), query ? button("Сбросить поиск", "x", () => {state.templateQuery=""; $("template-search").value=""; renderTemplateCatalog(); $("template-search").focus();}) : null]));
  icons();
}
function renderCatalog() {
  renderTemplateCatalog();
  $("my-scenarios-list").replaceChildren(...state.myScenarios.map(item => {
    const control = element("button", {class:"scenario-item saved-scenario",type:"button"}, [icon("file-text"),element("strong",{text:scenarioListLabel(item)}),element("p",{text:"Сохранённый сценарий"}),element("span",{class:"scenario-open",text:"Открыть сценарий"})]);
    control.addEventListener("click", () => action(control, async () => { await openScene(item.id); $("catalog-dialog").close(); }));
    control.dataset.sceneId=item.id; return control;
  }));
  if (!state.myScenarios.length) $("my-scenarios-list").append(element("div", {class:"catalog-empty"}, [icon("folder-open"),element("h3",{text:"Здесь будут ваши сценарии"}),element("p",{text:"Откройте шаблон, внесите изменения и выберите «Сохранить как». Можно также загрузить файлы или создать пустой сценарий."})]));
  icons();
}

async function createBlank() { await action(null, async () => { if (hasUnsavedChanges() && !confirm("Несохранённые изменения будут потеряны. Создать сценарий?")) return; const scene = await api("/scenes", { method: "POST", body: { name: "Новый сценарий" } }); setScene(scene); await refreshScenarios(); $("catalog-dialog").close(); showTerrainNotice(scene); }); }

async function deleteScene() {
  if (!state.scene || state.scene.template_readonly || calculating()) return;
  if (!confirm("Удалить этот сценарий и его результаты для всей команды?")) return;
  await api(`/scenes/${encodeURIComponent(state.scene.id)}`, { method: "DELETE" });
  clearTimeout(state.poll);
  pausePlayback();
  state.scene = null;
  state.draft = null;
  state.plan = null;
  state.dirty = false;
  $("result-dock").hidden = true;
  $("plan-overlay").hidden = true;
  paintScene();
  paintPlan();
  await refreshScenarios();
  renderSidebar();
  updateControls();
  $("version-badge").textContent = "Нет сценария";
  toast("Сценарий удалён.");
}

async function startPlan(mode) {
  if (!state.scene || state.busy || calculating()) return;
  if (needsSaveBeforeRun()) { toast(SAVE_BEFORE_RUN, true); return; }
  if (!state.scene || state.scene.validation?.valid === false) {
    toast("Расчёт невозможен: сначала исправьте ошибки входных данных.", true);
    return;
  }
  if (!$("run-form").reportValidity()) return;
  const record = await api("/plans", { method: "POST", body: { scene_id: state.scene.id, objective: $("objective").value, seed: 20260918, mode } });
  state.selectedH1Task = null;
  state.resultTab = "summary";
  state.sidebar = "run";
  state.plan = { ...record, mode, objective: $("objective").value, progress: [] };
  renderSidebar();
  renderResult();
  await pollPlan(record.id);
}
async function openPlan(id) {
  pausePlayback();
  clearTimeout(state.poll);
  state.selectedH1Task = null;
  const sceneId = state.scene?.id;
  const record = await api(`/plans/${encodeURIComponent(id)}`);
  if (state.scene?.id !== sceneId || record.scene_id !== sceneId) return;
  state.plan = record;
  if (state.plan.mode === "h1" && h1Output()) state.resultTab = "h1";
  state.playbackValue = 0;
  renderResult();
  renderSidebar();
  paintPlan();
  if (!TERMINAL.has(String(state.plan.status).toUpperCase())) await pollPlan(id);
}
async function pollPlan(id) {
  clearTimeout(state.poll);
  try {
    const record = await api(`/plans/${encodeURIComponent(id)}`);
    if (state.plan?.id !== id) return;
    const hadH1 = Boolean(h1Output());
    state.plan = record;
    if (state.sidebar === "run") { renderRunTab(); icons(); }
    if (record.mode === "h1" && !hadH1 && h1Output()) state.resultTab = "h1";
    renderResult();
    const status = String(record.status).toUpperCase();
    if (TERMINAL.has(status)) { paintPlan(); updateControls(); return; }
    state.poll = setTimeout(() => pollPlan(id), 1500);
  } catch (error) {
    toast(errorMessage(error), true);
    if (state.csrf && state.plan?.id === id) state.poll = setTimeout(() => pollPlan(id), 5000);
  }
}
function planResult() { return state.plan?.plan || {}; }
function h1Output() { return planResult().h1_output || [...(state.plan?.progress || [])].reverse().find((entry) => entry.h1_output)?.h1_output || null; }
function h2Output() { return planResult().h2_output || [...(state.plan?.progress || [])].reverse().find((entry) => entry.h2_output)?.h2_output || null; }
function routingGraphOutput() { return h2Output()?.routing_graph || [...(state.plan?.progress || [])].reverse().find((entry) => entry.search_progress?.routing_graph)?.search_progress.routing_graph || null; }
function paintRoutingGraph() {
  const output = routingGraphOutput();
  const profiles = output?.summary?.profiles || [];
  if (!profiles.some((profile) => profile.id === state.graphProfile)) state.graphProfile = profiles[0]?.id;
  const features = (output?.geojson?.features || []).filter((feature) => !feature.properties.profile_id || feature.properties.profile_id === state.graphProfile);
  state.map?.getSource("routing-graph")?.setData(collection(features));
}
function installRoutingGraphLayers(map) {
  map.addSource("routing-graph", { type: "geojson", data: collection() });
  const color = ["match", ["get", "kind"], "survey", "#156fca", "fence", "#159c7d", "along_transect", "#92a7b9", "base_link", "#d38c15", "long_link", "#d45670", "dummy_link", "#8751b4", "#6c899a"];
  map.addLayer({ id: "routing-graph-edges", type: "line", source: "routing-graph", filter: ["==", ["geometry-type"], "LineString"], layout: { visibility: "none" }, paint: { "line-color": color, "line-width": ["case", ["==", ["get", "kind"], "survey"], 2.5, 1.5], "line-opacity": .8, "line-offset": ["case", ["==", ["get", "kind"], "survey"], 3, 0] } });
  const canvas = document.createElement("canvas"); canvas.width = canvas.height = 32;
  const context = canvas.getContext("2d"); context.fillStyle = "#fff"; context.beginPath(); context.moveTo(25, 16); context.lineTo(8, 5); context.lineTo(8, 27); context.closePath(); context.fill();
  map.addImage("routing-arrow", context.getImageData(0, 0, 32, 32), { sdf: true });
  map.addLayer({ id: "routing-graph-arrows", type: "symbol", source: "routing-graph", filter: ["all", ["==", ["geometry-type"], "LineString"], ["==", ["get", "directed"], true]], layout: { visibility: "none", "symbol-placement": "line", "symbol-spacing": 85, "icon-image": "routing-arrow", "icon-size": .44, "icon-rotation-alignment": "map", "icon-keep-upright": false, "icon-allow-overlap": true, "icon-offset": [0, 6] }, paint: { "icon-color": color } });
  map.addLayer({ id: "routing-graph-nodes", type: "circle", source: "routing-graph", filter: ["==", ["geometry-type"], "Point"], layout: { visibility: "none" }, paint: { "circle-color": ["match", ["get", "kind"], "base", "#d38c15", "dummy", "#8751b4", "#27627b"], "circle-radius": ["case", ["==", ["get", "kind"], "base"], 7, ["==", ["get", "kind"], "dummy"], 6, ["==", ["get", "portal"], true], 5, 3], "circle-stroke-color": "#fff", "circle-stroke-width": 1 } });
  for (const layer of ["routing-graph-edges", "routing-graph-nodes"]) map.on("click", layer, (event) => {
    if (state.resultTab !== "graph") return;
    const feature = event.features?.[0]; if (!feature) return;
    const props = feature.properties || {};
    const labels = { endpoint: "Конец галса", base: "База", dummy: "Выбор начальной / конечной базы", survey: "Полный направленный проход галса", fence: "Связь соседних галсов", between_groups: "Ближняя связь зон", long_link: "Дальняя связь зон", base_link: "Перелёт к базе", dummy_link: "Выбор базы, 0 секунд", along_transect: "Транзит вдоль галса", bridge: "Связь компонент" };
    const content = element("div", { class: "graph-popup" }, [element("strong", { text: props.portal === true || props.portal === "true" ? "Точка входа / выхода зоны" : labels[props.kind] || props.kind }), null]);
    let costs = props.profile_times; if (typeof costs === "string") { try { costs = JSON.parse(costs); } catch { costs = null; } }
    if (costs) for (const [profile, cost] of Object.entries(costs)) {
      const info = routingGraphOutput()?.summary?.profiles?.find(item => item.id === profile);
      const name = info ? `${info.model.replace("geoscan_", "Геоскан ")} · ${info.uav_ids.join(", ")}` : "Аппараты";
      content.append(element("div", { text: `${name}: → ${valueNumber(cost.forward_s)} с; ← ${valueNumber(cost.backward_s)} с` }));
    }
    new maplibregl.Popup().setLngLat(event.lngLat).setDOMContent(content).addTo(map);
  });
}
function renderRoutingGraph(target) {
  const output = routingGraphOutput();
  if (!output) { renderStagePlaceholder(target, "H2", "Граф появится после оценки возможных перелётов. Если он не сохранён в этом результате, запустите расчёт заново."); return; }
  const info = output.summary || {};
  target.append(element("div", { class: "metric-grid" }, [[info.transects, "Обязательных галсов"], [info.vertices, "Точек графа"], [info.orientations, "Направлений съёмки"], [info.profiles?.length, "Групп аппаратов"]].map(([value, label]) => element("div", { class: "metric" }, [element("strong", { class: "metric-value", text: valueNumber(value, 0) }), element("label", { text: label })]))));
  paintRoutingGraph();
  const profileSelect = element("select", { id: "graph-profile", "aria-label": "Аппараты на графе", onchange: (event) => { state.graphProfile = event.target.value; paintRoutingGraph(); } }, (info.profiles || []).map((profile) => element("option", { value: profile.id, text: `${profile.model} · ${profile.uav_ids.join(", ")}`, selected: profile.id === state.graphProfile })));
  target.append(element("label", { class: "field" }, ["Аппараты на графе", profileSelect]));
  target.append(element("p", { class: "small-text", text: "Синие стрелки — два альтернативных полных прохода каждого галса. Зелёные связи соединяют соседние галсы, серые — ближние группы, розовые — дальние, золотые — базы. Фиолетовые стрелки выбирают старт и финиш, их положение на карте условное. Нажмите ребро, чтобы увидеть время по профилям БПЛА." }));

  target.append(button("Показать весь граф", "expand", () => {
    const bounds = new maplibregl.LngLatBounds();
    for (const feature of output.geojson.features) if (feature.geometry.type === "Point") bounds.extend(feature.geometry.coordinates);
    if (!bounds.isEmpty()) state.map.fitBounds(bounds, { padding: 70, maxZoom: 15 });
  }));
}
function stageEntry(stage) { return [...(state.plan?.progress || [])].reverse().find((entry) => entry.stage === stage) || null; }
function stageDone(stage) { const entry = stageEntry(stage); return entry?.status === "completed" || (stage === "h1" && Boolean(h1Output())) || (stage === "h2" && Boolean(h2Output())) || (stage === "h3" && Boolean(state.plan?.validation)); }
function planMetrics() { return { ...planResult().metrics, ...h2Output()?.metrics, ...state.plan?.metrics, ...state.plan?.validation?.metrics }; }
function sorties() { return h2Output()?.sorties || planResult().sorties || []; }
function valueNumber(value, decimals = 1) { return Number.isFinite(Number(value)) && value !== null && value !== undefined ? Number(value).toLocaleString("ru-RU", { maximumFractionDigits: decimals }) : "Нет данных"; }
function uavColor(id) { const ids = [...new Set(sorties().map((sortie) => sortie.uav_id))]; return UAV_COLORS[Math.max(0, ids.indexOf(id)) % UAV_COLORS.length]; }
function highlightUav() {
  document.querySelectorAll(".fleet-item").forEach(node => node.classList.toggle("active", node.dataset.uavId === state.selectedUav));
  if (!state.mapReady || !state.map.getLayer("routes")) return;
  const selected = ["==", ["get", "uav_id"], state.selectedUav || ""];
  state.map.setPaintProperty("routes", "line-width", state.selectedUav ? ["case", selected, 5, 1.5] : 2.4);
  state.map.setPaintProperty("routes", "line-opacity", state.selectedUav ? ["case", selected, 1, .3] : .86);
}
function paintPlan() {
  if (!state.mapReady) return;
  const features = sorties().filter((sortie) => (sortie.waypoints || []).length >= 2).map((sortie) => ({ type: "Feature", properties: { color: uavColor(sortie.uav_id), uav_id: sortie.uav_id }, geometry: { type: "LineString", coordinates: sortie.waypoints.map((point) => [point.lon, point.lat]) } }));
  state.map.getSource("routes")?.setData(collection(features));
  highlightUav();
  state.map.getSource("playback")?.setData(collection());
  const h1 = h1Output()?.geojson;
  state.map.getSource("h1-output")?.setData(h1 || collection());
  paintRoutingGraph();
  highlightH1Task();
  setResultLayers();
  paintOverlay();
}
function paintH1Output() {
  if (!state.mapReady) return;
  state.map.getSource("h1-output")?.setData(h1Output()?.geojson || collection());
}
function setResultLayers() {
  if (!state.mapReady) return;
  const h1Visible = state.resultTab === "h1" && Boolean(h1Output()?.geojson);
  const graphVisible = state.resultTab === "graph" && Boolean(routingGraphOutput());
  for (const id of ["routing-graph-edges", "routing-graph-arrows", "routing-graph-nodes"]) if (state.map.getLayer(id)) state.map.setLayoutProperty(id, "visibility", graphVisible ? "visible" : "none");
  for (const id of ["h1-output-fill", "h1-output-boundaries", "h1-output-transects", "h1-output-labels", "h1-selected-fill", "h1-selected-line"]) if (state.map.getLayer(id)) state.map.setLayoutProperty(id, "visibility", h1Visible ? "visible" : "none");
  for (const id of ["routes", "routes-halo", "playback"]) if (state.map.getLayer(id)) state.map.setLayoutProperty(id, "visibility", (h1Visible || graphVisible) ? "none" : "visible");
}
function objectiveMetricCard(compact = false) {
  const metrics = planMetrics();
  const objective = state.plan?.objective || planResult().objective || "makespan";
  const raw = metrics[`${objective}_s`] ?? (metrics[`${objective}_min`] !== undefined ? metrics[`${objective}_min`] * 60 : null);
  if (raw === null || !Number.isFinite(Number(raw)) || !sorties().length) return null;
  const seconds = Math.max(0, Math.ceil(Number(raw)));
  const hours = Math.floor(seconds / 3600), minutes = Math.floor(seconds % 3600 / 60), remainder = seconds % 60;
  const text = `${hours ? `${hours} ч ` : ""}${minutes} мин ${remainder} с`;
  const missing = h2Output()?.unassigned?.length || planResult().diagnosis?.unassigned?.length || 0;
  const label = objective === "total_flight" ? "Суммарный налёт" : "Время завершения миссии";
  return element("section", { class: `objective-highlight${compact ? " compact" : ""}`, "data-objective": objective }, [
    element("span", { class: "objective-label", text: `${label}${missing ? " · неполный план" : ""}` }),
    element("strong", { class: "objective-value", text }),
    element("span", { class: "objective-detail", text: `${valueNumber(Number(raw) / 60, 2)} мин${missing ? ` · не назначено ${missing} задач` : " · выбранный критерий"}` })
  ]);
}
function paintOverlay() {
  const target = $("plan-overlay");
  target.classList.toggle("graph-view", state.resultTab === "graph");
  if (!state.plan || state.plan.mode === "h1" || !TERMINAL.has(String(state.plan.status).toUpperCase())) { target.hidden = true; return; }
  target.hidden = false;
  const metrics = planMetrics();
  const summary = (value, label) => element("div", {}, [element("strong", { text: value }), element("span", { text: label })]);
  const children = [element("div", { class: "section-title" }, [statusBadge(state.plan.status), button("Результат", "panel-bottom", () => { $("result-dock").hidden = false; state.map.resize(); fitScene(); })]), objectiveMetricCard(true), element("div", { class: "overlay-metrics" }, [summary(`${valueNumber(metrics.coverage_percent)} %`, "Покрытие"), summary(valueNumber(metrics.makespan_s !== undefined ? metrics.makespan_s / 60 : metrics.makespan_min), "Завершение работ, мин")]), state.plan.mode === "fixture" ? element("div", { class: "small-text warning", text: "Готовый эталон, не новый расчёт" }) : null];
  target.replaceChildren(...children.filter(Boolean));
  icons();
}
function renderResult() {
  if (!state.plan) return;
  const wasHidden = $("result-dock").hidden;
  $("result-dock").hidden = false;
  $("result-heading").replaceChildren(...[element("strong", { text: "Результат" }), state.plan.mode === "fixture" ? badge("ЭТАЛОН", "fixture") : null].filter(Boolean));
  updateControls();
  document.querySelectorAll("[data-result]").forEach((tab) => {
    tab.hidden = false;
    tab.classList.toggle("active", tab.dataset.result === state.resultTab);
  });
  const target = $("result-content");
  target.replaceChildren();
  if (state.plan.mode === "h1" && ["graph", "timeline", "h3"].includes(state.resultTab)) {
    target.append(element("div", { class: "stage-placeholder" }, [icon("pause-circle"), element("h3", { text: state.resultTab === "graph" ? "Граф перелётов" : state.resultTab === "timeline" ? "Маршруты" : "Проверки безопасности" }), element("p", { text: "В этом результате построены только галсы. Запустите полный расчёт, чтобы получить маршруты и проверить безопасность." })]));
  }
  else if (state.resultTab === "summary") renderOverview(target);
  else if (state.resultTab === "h1") stageDone("h1") ? renderH1(target) : renderStagePlaceholder(target, "H1", "Галсы и задачи появятся после завершения построения покрытия.");
  else if (state.resultTab === "graph") renderRoutingGraph(target);
  else if (state.resultTab === "timeline") stageDone("h2") ? renderTimeline(target) : renderStagePlaceholder(target, "H2", "Расписание появится после завершения распределения задач.");
  else if (state.resultTab === "h3") stageDone("h3") ? renderH3(target) : renderStagePlaceholder(target, "H3", "Протокол проверки появится после завершения независимой проверки.");
  else if (state.resultTab === "export") stageDone("h3") ? renderExports(target) : renderStagePlaceholder(target, "H3", "Экспорт появится после завершения независимой проверки.");
  icons();
  // H2 emits its schedule before H3 finishes; paint those final UAV routes immediately.
  paintPlan();
  if (state.map) requestAnimationFrame(() => { state.map.resize(); if (wasHidden || state.resultTab === "h1") fitScene(); });
}
function renderStagePlaceholder(target, stage, message) {
  const entry = stageEntry(stage.toLowerCase());
  target.append(element("div", { class: "stage-placeholder" }, [element("div", { class: "progress-spinner" }), element("h3", { text: "Прогресс" }), element("p", { text: userMessage(entry?.message || message) }), statusBadge(state.plan.status)]));
}
function renderOverview(target) {
  const status = String(state.plan.status || "").toUpperCase();
  if (TERMINAL.has(status) && state.plan.mode !== "h1") { const hero = objectiveMetricCard(); if (hero) target.append(hero); }
  target.append(element("div", { class: "section-title" }, [element("h3", { text: status === "QUEUED" ? "Расчёт в очереди" : (state.plan.mode === "h1" ? "Построение галсов и задач" : "Ход расчёта") }), statusBadge(status)]));
  if (!TERMINAL.has(status)) target.append(element("div", { class: "progress-bar", role: "progressbar", "aria-label": "Выполняется расчёт" }));
  const stages = (state.plan.mode === "h1" ? ["h1"] : ["h1", "h2", "h3"]).map((stage) => {
    const entry = stageEntry(stage);
    const running = entry?.status === "running";
    const done = stageDone(stage);
    const seconds = Number(entry?.duration_s);
    const timer = Number.isFinite(seconds) ? `${valueNumber(seconds, 1)} с` : running && entry?.started_at ? `${valueNumber(Math.max(0, (Date.now() / 1000) - entry.started_at), 1)} с · идёт` : done ? "завершено" : "ожидает";
    return element("div", { class: `stage-card${done ? " stage-done" : running ? " stage-running" : ""}` }, [element("strong", { text: STAGE_LABELS[stage] || "Расчёт" }), element("span", { text: userMessage(entry?.message) || (stage === "h1" ? "Построение галсов и задач" : stage === "h2" ? "Распределение и расписание" : "Независимая проверка") }), element("small", { text: timer })]);
  });
  target.append(element("div", { class: "stage-cards" }, stages));
  const journal = element("ul", { class: "progress-list" }, (state.plan.progress || []).map(formatProgress));
  target.append(TERMINAL.has(status) ? element("details", { class: "details-json" }, [element("summary", { text: "Ход расчёта" }), journal]) : journal);
  if (TERMINAL.has(status) && state.plan.mode !== "h1") renderSummaryMetrics(target);
}

function formatProgress(entry) {
  if (typeof entry === "string") return element("li", { text: userMessage(entry) });
  const labels = { queued: "Очередь", ...STAGE_LABELS, validation: "Проверка", recommendations: "Рекомендации", complete: "Готово", error: "Ошибка", running: "Расчёт" };
  const duration = Number.isFinite(Number(entry.duration_s)) ? ` · ${valueNumber(entry.duration_s, 1)} с` : "";
  return element("li", { text: `${labels[entry.stage] || entry.stage || "Этап"}: ${userMessage(entry.message || ({completed:"Завершено",running:"Выполняется"})[entry.status] || "")}${duration}` });
}
function renderH1(target) {
  const output = h1Output();
  if (!output?.geojson) { target.append(element("h3", { text: "Галсы не сохранены" }), element("p", { class: "small-text", text: "В этом результате нет карты галсов. Запустите расчёт заново." })); return; }
  const features = output.geojson.features || [];
  const tasks = new Set(features.filter((feature) => feature.properties?.kind === "transect").map((feature) => feature.properties?.task_id));
  const sensorTypes = new Set([...tasks].map((id) => features.find((feature) => feature.properties?.task_id === id)?.properties?.payload_class).filter(Boolean));
  target.append(element("div", { class: "metric-grid h1-metrics" }, [[String(output.task_count || tasks.size), "Задач"], [String(features.filter((feature) => feature.properties?.kind === "transect").length), "Галсов"], [String(sensorTypes.size), "Типов датчиков"]].map(([value, label]) => element("div", { class: "metric" }, [element("strong", { class: "metric-value", text: value }), element("label", { text: label })]))));
  target.append(element("p", { class: "small-text h1-note", text: "На карте показаны линии съёмки и границы задач. Выберите строку, чтобы выделить задачу на карте." }));
  const envelopes = features.filter((feature) => feature.properties?.kind === "task_envelope");
  const rows = envelopes.map((feature) => {
    const props = feature.properties || {};
    return [props.task_id, props.transect_count || 0, valueNumber((props.survey_length_m || 0) / 1000, 2), SENSOR_LABELS[props.payload_class] || props.payload_class || ""];
  });
  if (rows.length) {
    const taskTable = table(["Задача", "Галсов", "Длина, км", "Тип датчика"], rows);
    taskTable.querySelectorAll("tbody tr").forEach((row, index) => {
      const taskId = rows[index][0];
      row.dataset.taskId = taskId;
      row.classList.add("h1-task-row");
      row.tabIndex = 0;
      row.setAttribute("aria-label", `Выделить задачу ${taskId} на карте`);
      const select = () => { state.selectedH1Task = taskId; highlightH1Task(); };
      row.addEventListener("click", select);
      row.addEventListener("keydown", (event) => { if (["Enter", " "].includes(event.key)) { event.preventDefault(); select(); } });
    });
    target.append(element("details", { class: "details-json", open: true }, [element("summary", { text: "Задачи · нажмите на строку, чтобы выделить на карте" }), taskTable]));
  }
}
function highlightH1Task() {
  document.querySelectorAll(".h1-task-row").forEach((row) => {
    const selected = row.dataset.taskId === state.selectedH1Task;
    row.classList.toggle("selected", selected);
    row.setAttribute("aria-selected", String(selected));
  });
  if (!state.mapReady) return;
  const filter = ["all", ["==", ["get", "kind"], "task_envelope"], ["==", ["get", "task_id"], state.selectedH1Task || ""]];
  for (const id of ["h1-selected-fill", "h1-selected-line"]) if (state.map.getLayer(id)) state.map.setFilter(id, filter);
}
function renderH3(target) {
  const validation = state.plan.validation || {};
  const certificate = state.plan.certificate;
  const checks = certificate?.checks || validation.checks || validation.check_list || [];
  const labels = {input_schema:"Корректность исходных данных",original_required_coverage:"Покрытие областей съёмки",full_segment_airspace:"Соблюдение границ и запретных зон",raster_cell_surface_clearance:"Расстояние до поверхности и препятствий",continuous_temporal_airspace:"Временные запреты на полёты",continuous_pairwise_separation:"Безопасное расстояние между аппаратами",resource_and_landing_suffix:"Запас энергии и возможность посадки",payload_gsd:"Совместимость датчиков и параметры съёмки",site_roles:"Разрешённые стартовые и посадочные площадки",service_and_mission_windows:"Время обслуживания и разрешённые окна",wind_limits:"Допустимая скорость ветра",declared_survey_turn_buffers:"Место для разворотов",metric_claims:"Корректность итоговых времени и расстояний"};
  target.append(element("div", {class:"section-title"}, [element("h3", {text:"Проверка безопасности"}),statusBadge(state.plan.status)]));
  target.append(element("p", {class:"small-text",text:certificate ? "Результаты независимой проверки маршрута. Сертификат можно скачать во вкладке «Экспортировать результат»." : "Сертификат не выдан. Ниже приведены доступные результаты проверки."}));
  if (Array.isArray(checks) && checks.length) target.append(element("ul",{class:"safety-checks"},checks.map(check => {
    const key=typeof check === "string" ? check : check.name || check.code || check.id;
    return element("li",{},[icon(certificate?.status === "SAFE" ? "check-circle-2" : "circle"),element("span",{text:labels[key] || "Дополнительная проверка маршрута"})]);
  })));
  const violations=validation.violations || [];
  if (violations.length) target.append(element("ul",{class:"validation-list"},violations.map(item => element("li",{text:userMessage(item.message || detailsMessage(item))}))));
  if (!checks.length && !violations.length) target.append(element("p",{class:"small-text",text:"Для этого результата подробный протокол недоступен."}));
  const scopeLabels = { discrete_photo_front_overlap: "перекрытие отдельных кадров по ходу полёта", side_overlap_quality: "качество поперечного перекрытия", aircraft_dynamics: "полная динамика БВС", off_track_sensor_terrain_occlusion: "затенение рельефом вне линии полёта", real_operational_permissions: "разрешения на реальный полёт", global_optimality: "глобальная оптимальность" };
  const notChecked = certificate?.not_checked || validation.not_checked || [];
  if (notChecked.length) target.append(element("p", {class:"small-text", text:`За пределами проверки: ${notChecked.map(key => scopeLabels[key] || "дополнительные ограничения, указанные в протоколе").join("; ")}.`}));
  target.append(element("p",{class:"small-text",text:"Проверка выполнена в рамках модели. Глобальная оптимальность и разрешение на реальный полёт не подтверждаются."}));
}

function renderSummaryMetrics(target) {
  const metrics = planMetrics();
  const total = metrics.total_flight_s !== undefined ? metrics.total_flight_s / 60 : metrics.total_flight_min;
  const duration = metrics.makespan_s !== undefined ? metrics.makespan_s / 60 : metrics.makespan_min;
  const values = [[`${valueNumber(metrics.coverage_percent)} %`, "Покрытие области"], [valueNumber(duration), "Завершение работ, мин"], [valueNumber(total), "Суммарный налёт, мин"], [String(sorties().length), "Вылетов"], [String(new Set(sorties().map((sortie) => sortie.uav_id)).size), "БВС задействовано"]];
  target.append(element("div", { class: "metric-grid" }, values.map(([value, label]) => element("div", { class: "metric" }, [element("strong", { class: "metric-value", text: value }), element("label", { text: label })]))));
  target.append(element("p", {class:"small-text", text:`Подготовка: ${valueNumber((metrics.preparation_time_s ?? 0)/60)} мин; снятие данных: ${valueNumber((metrics.data_download_time_s ?? 0)/60)} мин.${metrics.ground_operations_explicit ? "" : " В исходных данных эти затраты заданы не полностью; отсутствующие значения приняты равными 0."}`}));
  target.append(element("div", { class: "result-notes" }, [statusBadge(state.plan.status), element("span", { class: "result-note" }, [icon("shield-check"), "Безопасность: только в рамках модели"]), element("span", { class: "result-note" }, [icon("circle-dashed"), planResult().optimality?.status === "proven" ? "Оптимальность доказана" : "Глобальная оптимальность не доказана"]), state.plan.mode === "fixture" ? element("span", { class: "result-note" }, [icon("file-check-2"), "Готовый эталон датасета"]) : null]));
  const error = state.plan.error || state.plan.message || planResult().diagnosis?.message || state.plan.diagnosis?.message;
  if (error) target.append(element("p", { class: "error-text", text: detailsMessage(error) }));

}
function timelineBounds() {
  const entries = sorties();
  if (!entries.length) return null;
  const starts = entries.map((sortie) => Date.parse(sortie.t_start)).filter(Number.isFinite);
  const ends = entries.map((sortie) => Date.parse(sortie.t_end)).filter(Number.isFinite);
  if (!starts.length || !ends.length) return null;
  return { start: Math.min(...starts), end: Math.max(...ends) };
}
function renderTimeline(target) {
  const missing = h2Output()?.unassigned || planResult().diagnosis?.unassigned || [];
  if (missing.length) {
    const total = h2Output()?.task_count ?? h1Output()?.task_count;
    target.append(element("p", { class: "error-text", text: `ПЛАН НЕПОЛНЫЙ: не назначено ${missing.length}${total ? ` из ${total}` : ""} задач. Анимация показывает только построенные вылеты, а не выполнение всей миссии.` }));
    const reasonText = (item) => {
      if (item.message) return item.message;
      const attempts = (item.construction_attempts || []).flatMap((x) => x.route_failures || []);
      const single = attempts.filter((x) => x.kind === "new_sortie");
      if (single.some((x) => x.reason === "mission_window")) return "Следующий вылет не помещается в окно миссии";
      const energy = single.filter((x) => Number.isFinite(x.flight_time_s));
      if (energy.length) {
        const best = energy.reduce((a, b) => a.flight_time_s < b.flight_time_s ? a : b);
        return `Проверенный отдельный вылет: ${valueNumber(best.flight_time_s / 60)} мин; ресурс: ${valueNumber(best.usable_time_s / 60)} мин`;
      }
      return ({time_budget_exhausted: "Исчерпан бюджет вычисления (старый расчёт)", no_eligible_uav: "Нет совместимого БВС", no_resource_site_window_route_found: "Не найден вылет с допустимыми ресурсом, площадками и временем"})[item.reason] || item.reason;
    };
    target.append(element("details", { class: "details-json" }, [element("summary", { text: "Невыполненные задачи и причины" }), table(["Задача", "Причина"], missing.map((item) => [item.task_id, reasonText(item)]))]));
  }
  const bounds = timelineBounds();
  if (!bounds) { target.append(element("p", { class: "muted", text: "Расписание отсутствует: план вылетов не сформирован." })); return; }
  const slider = element("input", { id: "playback-slider", type: "range", min: 0, max: 1000, step: 1, value: state.playbackValue, "aria-label": "Время воспроизведения", oninput: () => { state.playbackValue = Number(slider.value); updatePlayback(); } });
  const play = button("", state.playing ? "pause" : "play", togglePlayback, "icon-button");
  play.id = "playback-play";
  play.title = "Воспроизведение маршрутов";
  play.setAttribute("aria-label", "Воспроизведение маршрутов");
  const speed = element("select", { class: "timeline-speed", "aria-label": "Скорость воспроизведения", onchange: (event) => { state.playbackSpeed = Number(event.target.value); } }, [10, 60, 300, 1000].map((value) => element("option", { value, text: `×${value}` })));
  speed.value = state.playbackSpeed;
  target.append(element("div", { class: "timeline-controls" }, [play, element("span", { class: "timeline-clock", id: "playback-clock", text: displayTime(bounds.start) }), slider, element("span", { class: "timeline-clock", text: displayTime(bounds.end) }), speed]));
  const gantt = element("div", { class: "gantt" });
  const duration = Math.max(1, bounds.end - bounds.start);
  sorties().forEach((sortie) => {
    const left = (Date.parse(sortie.t_start) - bounds.start) / duration * 100;
    const width = (Date.parse(sortie.t_end) - Date.parse(sortie.t_start)) / duration * 100;
    const bar = element("div", { class: "gantt-bar", title: `${sortie.id}: ${displayTime(sortie.t_start)} - ${displayTime(sortie.t_end)} МСК`, style: `left:${left}%;width:${width}%;background:${uavColor(sortie.uav_id)}` });
    gantt.append(element("div", { class: "gantt-row" }, [element("span", { class: "gantt-label", text: `${sortie.uav_id} / ${sortie.index ?? 1}` }), element("div", { class: "gantt-track" }, bar)]));
  });
  target.append(element("div", { class: "table-wrap" }, gantt));
  const rows = sorties().map((sortie) => [sortie.uav_id, sortie.id, `${sortie.start_site} → ${sortie.landing_site}`, displayTime(sortie.t_start), displayTime(sortie.t_end), valueNumber((sortie.flight_time_s || 0) / 60)]);
  target.append(element("details", { class: "details-json" }, [element("summary", { text: "Таблица вылетов · время МСК" }), table(["БВС", "Вылет", "Площадки", "Старт", "Посадка", "Минут"], rows)]));
  updatePlayback();
}
function table(headers, rows) { return element("div", { class: "table-wrap" }, element("table", {}, [element("thead", {}, element("tr", {}, headers.map((label) => element("th", { text: label })))), element("tbody", {}, rows.map((row) => element("tr", {}, row.map((value) => element("td", { text: typeof value === "object" ? JSON.stringify(value) : value ?? "" })))))])); }
function pausePlayback() { state.playing = false; if (state.frame) cancelAnimationFrame(state.frame); state.frame = null; state.playbackPrevious = 0; }
function togglePlayback() {
  if (state.playing) pausePlayback();
  else { if (state.playbackValue >= 1000) state.playbackValue = 0; state.playing = true; state.frame = requestAnimationFrame(playbackFrame); }
  if (state.resultTab === "timeline") renderResult();
}
function playbackFrame(time) {
  if (!state.playing) return;
  const bounds = timelineBounds();
  if (!bounds) { pausePlayback(); return; }
  if (state.playbackPrevious) state.playbackValue += (time - state.playbackPrevious) * state.playbackSpeed / Math.max(1, bounds.end - bounds.start) * 1000;
  state.playbackPrevious = time;
  state.playbackValue = Math.min(1000, state.playbackValue);
  updatePlayback();
  if (state.playbackValue >= 1000) { pausePlayback(); renderResult(); return; }
  state.frame = requestAnimationFrame(playbackFrame);
}
function updatePlayback() {
  const bounds = timelineBounds();
  if (!bounds) return;
  const timestamp = bounds.start + (bounds.end - bounds.start) * state.playbackValue / 1000;
  if ($("playback-slider")) $("playback-slider").value = state.playbackValue;
  if ($("playback-clock")) $("playback-clock").textContent = displayTime(timestamp);
  const features = [];
  for (const sortie of sorties()) {
    if (timestamp < Date.parse(sortie.t_start) || timestamp > Date.parse(sortie.t_end)) continue;
    const points = sortie.waypoints || [];
    let low = 0, high = points.length - 1;
    while (low < high) { const mid = Math.floor((low + high) / 2); if (Date.parse(points[mid].t) < timestamp) low = mid + 1; else high = mid; }
    const next = points[low], previous = points[Math.max(0, low - 1)];
    if (!next || !previous) continue;
    const start = Date.parse(previous.t), end = Date.parse(next.t);
    const fraction = end === start ? 0 : Math.max(0, Math.min(1, (timestamp - start) / (end - start)));
    features.push({ type: "Feature", properties: { color: uavColor(sortie.uav_id), uav_id: sortie.uav_id }, geometry: { type: "Point", coordinates: [previous.lon + (next.lon - previous.lon) * fraction, previous.lat + (next.lat - previous.lat) * fraction] } });
  }
  state.map?.getSource("playback")?.setData(collection(features));
}
function renderSafety(target) {
  const validation = state.plan.validation || {};
  const certificate = state.plan.certificate;
  target.append(element("div", { class: "certificate-summary" }, [icon(certificate && state.plan.status === "SAFE" ? "shield-check" : "shield-alert"), element("div", {}, [element("h3", { text: certificate ? "Safety Certificate" : "Сертификат безопасности не выдан" }), element("p", { text: "Проверка модели не является разрешением на реальный полёт." }), certificate ? element("p", { text: `Вход: ${certificate.input_sha256 || state.scene.input_sha256 || "не указан"}` }) : null]), statusBadge(state.plan.status)]));
  const violations = validation.violations || validation.issues || [];
  if (violations.length) target.append(table(["Проверка", "Описание", "Объект"], violations.map((item) => [item.code || item.kind || "Нарушение", item.message || detailsMessage(item), item.uav_id || item.sortie_id || item.job_id || ""])));
  else target.append(element("p", { class: "small-text", text: validation.passed === true ? "Независимый валидатор: проверка пройдена." : "Отсутствие списка нарушений само по себе не подтверждает безопасность." }));
  const assumptions = certificate?.model_assumptions || certificate?.assumptions || validation.model_assumptions || planResult().model_assumptions;
  if (assumptions) target.append(element("details", { class: "details-json", open: true }, [element("summary", { text: "Допущения и границы проверки" }), element("pre", { text: typeof assumptions === "string" ? assumptions : JSON.stringify(assumptions, null, 2) })]));
  const notChecked = certificate?.not_checked || validation.not_checked || [];
  const scopeLabels = { discrete_photo_front_overlap: "перекрытие отдельных кадров по ходу полёта", side_overlap_quality: "качество поперечного перекрытия", aircraft_dynamics: "полная динамика БВС", off_track_sensor_terrain_occlusion: "затенение рельефом вне линии полёта", real_operational_permissions: "реальные разрешения на полёт", global_optimality: "глобальная оптимальность" };
  if (notChecked.length) target.append(element("p", { class: "small-text warning", text: `Не подтверждены: ${notChecked.map((key) => scopeLabels[key] || key).join("; ")}.` }));
  target.append(element("details", { class: "details-json" }, [element("summary", { text: "Полный протокол проверки" }), element("pre", { text: JSON.stringify({ validation, certificate }, null, 2) })]));
}
function renderRecommendations(target) {
  const recommendations = state.plan.recommendations || planResult().recommendations || [];
  if (!recommendations.length) { target.append(element("h3", { text: state.plan.status === "SAFE" ? "Изменения не требуются" : "Проверенных рекомендаций пока нет" }), element("p", { class: "small-text", text: "Непроверенное изменение условий не может считаться безопасным решением." })); return; }
  recommendations.forEach((recommendation, index) => {
    const verified = recommendation.verified === true && (recommendation.verification?.status === "SAFE" || recommendation.status === "SAFE" || recommendation.validation?.passed === true || recommendation.verification?.passed === true);
    const apply = button("Применить", "git-branch", (event) => action(event.currentTarget, async () => {
      const scene = await api(`/plans/${encodeURIComponent(state.plan.id)}/recommendations/${encodeURIComponent(recommendation.id || recommendation.recommendation_id || index)}/apply`, { method: "POST", body: {} });
      setScene(scene); await refreshScenarios(); toast("Рекомендация применена. Создан изменённый сценарий.");
    }), "primary");
    apply.disabled = !verified;
    target.append(element("article", { class: "recommendation" }, [element("div", {}, [element("div", { class: "section-title" }, [element("h3", { text: recommendation.title || `Вариант ${index + 1}` }), badge(verified ? "Проверено" : "Не подтверждено", verified ? "good" : "warn")]), element("p", { text: recommendation.description || "" }), button("Изменения и проверка", "list-checks", () => jsonEditor("Рекомендация", recommendation, null))]), apply]));
  });
}
function renderExports(target) {
  const safe = String(state.plan.status).toUpperCase() === "SAFE";
  const uids = [...new Set(sorties().map(s => s.uav_id))];
  if (!uids.includes(state.exportUav)) state.exportUav = "";
  target.append(selectField("БВС для KML / GeoJSON", state.exportUav, [["", "Все БВС"], ...uids.map(uid => [uid, uid])], value => {
    state.exportUav = value; target.replaceChildren(); renderExports(target);
  }));
  const aircraftQuery = state.exportUav ? `&uav_id=${encodeURIComponent(state.exportUav)}` : "";
  const formats = [["kml", "KML", "Маршруты и все точки полёта", "route", true], ["geojson", "GeoJSON", "Маршруты и все точки полёта", "map", true], ["mission", "JSON", "Полный результат", "braces", false], ["pdf", "PDF", "Отчёт", "file-text", false], ["docx", "DOCX", "Редактируемый отчёт", "file-pen-line", false], ["certificate", "Сертификат", "Протокол JSON", "shield-check", false]];
  target.append(element("div", { class: "export-grid" }, formats.map(([format, name, description, iconName, flight]) => {
    const disabled = flight && !safe;
    return element("a", { class: `export-link${disabled ? " disabled" : ""}`, href: disabled ? null : `/api/v1/plans/${encodeURIComponent(state.plan.id)}/export?format=${format}${flight ? aircraftQuery : ""}`, download: "", "aria-disabled": disabled ? "true" : null, tabindex: disabled ? -1 : 0 }, [icon(iconName), element("div", {}, [element("strong", { text: name }), element("small", { text: description })])]);
  })));
  target.append(element("p", { class: "auth-note", text: safe ? "Маршрутные файлы описывают результат модели. Перед полётом требуется проверка оператором и необходимые разрешения." : "Экспорт маршрутных файлов закрыт: результат не имеет статуса SAFE. Отчёт и JSON доступны для разбора." }));
}

$("login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const control = event.currentTarget.querySelector("button[type=submit]");
  control.disabled = true;
  $("login-error").textContent = "";
  try { const username = $("username").value.trim(); const body = state.namedUsers && username ? {username,password:$("access-code").value} : {code:$("access-code").value}; const auth = await api("/auth/login", { method: "POST", body }); await unlock(auth); }
  catch (error) { $("login-error").textContent = errorMessage(error); }
  finally { control.disabled = false; }
});
$("logout").addEventListener("click", () => action(null, async () => { await api("/auth/logout", { method: "POST", body: {} }); lock(); }));
$("catalog-open").addEventListener("click", openWorkspace);
document.querySelectorAll("[data-open-tab]").forEach(control => {
  control.addEventListener("click", () => showOpenTab(control.dataset.openTab));
  control.addEventListener("keydown", event => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const tabs = [...document.querySelectorAll("[data-open-tab]")].filter(tab => !tab.disabled);
    const index = event.key === "Home" ? 0 : event.key === "End" ? tabs.length-1 : (tabs.indexOf(control)+(event.key === "ArrowRight" ? 1 : -1)+tabs.length)%tabs.length;
    showOpenTab(tabs[index].dataset.openTab); tabs[index].focus();
  });
});
$("new-blank").addEventListener("click", createBlank);
document.querySelectorAll("[data-close]").forEach((control) => control.addEventListener("click", () => $(control.dataset.close).close()));
$("mobile-menu").addEventListener("click", () => $("sidebar").classList.toggle("open"));
$("refresh-scenarios").addEventListener("click", (event) => action(event.currentTarget, refreshScenarios));

$("save-scene").addEventListener("click", () => action(null, saveScene));
$("save-as-scene").addEventListener("click", openSaveAs);
$("rename-scene").addEventListener("click", () => openScenarioName("rename"));
$("delete-scene").addEventListener("click", (event) => action(event.currentTarget, deleteScene));
$("save-as-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const control = event.currentTarget.querySelector("button[type=submit]");
  const name = $("save-as-name").value.trim();
  if (!name) { $("save-as-error").textContent = "Введите название."; return; }
  control.disabled = true;
  try {
    if (state.namingMode === "rename") {
      state.busy = true; updateControls();
      try {
        const scene = await api(`/scenes/${encodeURIComponent(state.scene.id)}/rename`, { method: "POST", body: { name } });
        state.scene.name = scene.name;
        state.draft.name = scene.name;
        for (const entry of state.editHistory) entry.name = scene.name;
        const clean = JSON.parse(state.cleanEdit); clean.name = scene.name; state.cleanEdit = JSON.stringify(clean);
        state.dirty = JSON.stringify(editableSnapshot()) !== state.cleanEdit;
        await refreshScenarios(); renderSidebar();
        toast("Сценарий переименован.");
      } finally { state.busy = false; updateControls(); }
    } else await saveScene(true, name);
    $("save-as-dialog").close();
  }
  catch (error) { $("save-as-error").textContent = errorMessage(error); }
  finally { control.disabled = false; }
});
$("undo-edit").addEventListener("click", () => undoEdit(-1));
$("redo-edit").addEventListener("click", () => undoEdit(1));
addEventListener("keydown", (event) => {
  if (!(event.ctrlKey || event.metaKey) || event.altKey || document.querySelector("dialog[open]")) return;
  const key = event.code;
  if (!["KeyZ", "KeyY", "KeyS"].includes(key)) return;
  if (event.target.closest("textarea,[contenteditable=true]")) return;
  event.preventDefault(); event.stopImmediatePropagation();
  if (state.busy || calculating()) return;
  // Commit a focused form field before restoring a complete editor snapshot.
  if (event.target.matches("input,select")) event.target.blur();
  if (key === "KeyS") event.shiftKey || state.scene?.template_readonly ? openSaveAs() : action(null, saveScene);
  else undoEdit(key === "KeyY" || event.shiftKey ? 1 : -1);
}, true);
function deleteSelectedFeature() {
  if (!state.selected || state.busy || state.sceneLoading || calculating()) return;
  state.draw.delete(state.selected);
  state.selected = null;
  syncLayers();
  repairSiteChoices();
  markDirty();
  renderSidebar();
  $("delete-feature").disabled = true;
}
addEventListener("keydown", (event) => {
  if (event.key !== "Delete" || event.ctrlKey || event.metaKey || event.altKey || document.querySelector("dialog[open]")) return;
  if (event.target.closest("input,textarea,select,[contenteditable=true]")) return;
  if (!state.selected) return;
  event.preventDefault(); event.stopImmediatePropagation();
  deleteSelectedFeature();
}, true);
document.querySelectorAll("[data-sidebar]").forEach((control) => control.addEventListener("click", () => { state.sidebar = control.dataset.sidebar; renderSidebar(); }));
document.querySelectorAll("[data-tool]").forEach((control) => control.addEventListener("click", () => setTool(control.dataset.tool)));
$("delete-feature").addEventListener("click", deleteSelectedFeature);
$("fit-scene").addEventListener("click", fitScene);
document.querySelectorAll("[data-basemap]").forEach((control) => control.addEventListener("click", () => {
  basemap.preference = control.dataset.basemap;
  try { localStorage.setItem("geoscan.h3.basemap", basemap.preference); } catch { /* The current tab can work without persistent storage. */ }
  if (basemap.preference === "imagery") {
    for (const failed of basemap.failed) {
      const id = `imagery-${failed}`;
      if (state.map?.getLayer(id)) state.map.removeLayer(id);
      if (state.map?.getSource(id)) state.map.removeSource(id);
      basemap.layers.delete(id);
    }
    basemap.failed.clear();
    if (basemap.manifestStatus === "error") { loadImageryManifest(); return; }
  }
  updateBasemap();
}));
$("terrain-flat").addEventListener("click", () => { $("terrain-dialog").close(); action(null, useFlatTerrain); });
$("terrain-download").addEventListener("click", () => { $("terrain-dialog").close(); updateTerrain(null); });
$("terrain-show-map").addEventListener("click", () => {
  $("terrain-dialog").close(); basemap.preference = "terrain";
  try { localStorage.setItem("geoscan.h3.basemap", "terrain"); } catch {}
  updateBasemap();
});
$("run-form").addEventListener("submit", (event) => { event.preventDefault(); action($("run-live"), () => startPlan("live")); });
$("stop-plan").addEventListener("click", (event) => action(event.currentTarget, async () => {
  const id = state.plan?.id;
  if (!id) return;
  await api(`/plans/${encodeURIComponent(id)}/cancel`, { method: "POST" });
  if (state.plan?.id === id) await pollPlan(id);
}));
$("run-fixture").addEventListener("click", (event) => action(event.currentTarget, () => startPlan("fixture")));
document.querySelectorAll("[data-result]").forEach((control) => control.addEventListener("click", () => { state.resultTab = control.dataset.result; renderResult(); }));
$("dock-close").addEventListener("click", () => { $("result-dock").hidden = true; state.map?.resize(); });
function singleImport() {
  const files = [...$("import-files").files];
  if (files.length !== 1) return null;
  const extension = files[0].name.split(".").pop().toLowerCase();
  if (["geojson", "kml"].includes(extension)) return { type: "layer", extension, filename: `${$("import-layer").value}.${extension}` };
  if (["tif", "tiff"].includes(extension)) return { type: "terrain", extension, filename: "dem.tif" };
  return null;
}
function renderImportSelection() {
  const single = singleImport();
  $("import-layer-field").hidden = single?.type !== "layer";
  $("import-selection").textContent = [...$("import-files").files].map((file) => file.name).join(", ") + (single ? ` → ${single.filename}` : "");
  $("import-error").textContent = "";
}
$("import-files").addEventListener("change", () => {
  const files = [...$("import-files").files];
  if (files.length === 1) {
    const stem = files[0].name.replace(/\.[^.]+$/, "").toLowerCase();
    if (Object.hasOwn(LAYERS, stem)) $("import-layer").value = stem;
  }
  $("import-base").checked = Boolean(singleImport() && state.scene);
  renderImportSelection();
});
$("import-layer").addEventListener("change", renderImportSelection);
$("import-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const control = event.currentTarget.querySelector("button[type=submit]");
  if (hasUnsavedChanges() && !confirm("Несохранённые изменения будут потеряны. Загрузить новую сцену?")) return;
  control.disabled = true;
  $("import-error").textContent = "";
  try {
    const single = singleImport();
    if (single && (!state.scene || !$("import-base").checked)) throw new Error("Одиночный слой или файл рельефа требует исходного сценария. Выберите сценарий для обновления либо загрузите полный архив сцены.");
    state.busy = true;
    updateControls();
    const data = new FormData();
    for (const file of $("import-files").files) data.append("files", file, single ? single.filename : file.name);
    const suffix = $("import-base").checked && state.scene ? `?base_scene_id=${encodeURIComponent(state.scene.id)}` : "";
    const scene = await api(`/scenes/upload${suffix}`, { method: "POST", body: data });
    setScene(scene); await refreshScenarios(); $("catalog-dialog").close(); $("import-form").reset(); $("import-selection").textContent = ""; toast("Сцена загружена. Результаты проверки доступны в параметрах."); showTerrainNotice(scene);
  } catch (error) { $("import-error").textContent = errorMessage(error); }
  finally { control.disabled = false; state.busy = false; updateControls(); }
});
$("json-form").addEventListener("submit", (event) => {
  event.preventDefault();
  try { const value = JSON.parse($("json-editor").value); if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("Ожидается JSON-объект."); state.jsonApply?.(value); $("json-dialog").close(); }
  catch (error) { $("json-error").textContent = errorMessage(error); }
});
addEventListener("beforeunload", (event) => { if (hasUnsavedChanges()) { event.preventDefault(); event.returnValue = ""; } });
let resizeTimer;
addEventListener("resize", () => { state.map?.resize(); clearTimeout(resizeTimer); resizeTimer = setTimeout(fitScene, 150); });
icons();
api("/auth/status").then((auth) => {
  state.namedUsers=Boolean(auth.named_users);
  $("username-field").hidden=!state.namedUsers;
  $("password-label").textContent=state.namedUsers ? "Пароль" : "Код доступа";
  if(state.namedUsers) $("access-code").removeAttribute("inputmode");
  if (auth.authenticated) return unlock(auth);
  $("connection-status").textContent = auth.configured === false ? "Доступ ещё не настроен администратором" : "Введите общий код доступа";
}).catch((error) => { $("connection-status").textContent = errorMessage(error); }).finally(() => { $("login-form").querySelector("button[type=submit]").disabled = false; });
