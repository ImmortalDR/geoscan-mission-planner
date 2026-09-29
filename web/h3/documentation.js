"use strict";
const byId = id => document.getElementById(id);
function element(tag, text, className) { const node = document.createElement(tag); if(text !== undefined) node.textContent = text; if(className) node.className = className; return node; }
function download(label, path) { const a = element("a", label); a.href = `/api/v1/documentation/file?path=${encodeURIComponent(path)}`; return a; }
function sourceLink(label, path) { const a = element("a", label); a.href = `https://github.com/ImmortalDR/geoscan/blob/main/${path.split("/").map(encodeURIComponent).join("/")}`; a.target = "_blank"; a.rel = "noopener"; return a; }
function select(view) { for(const id of ["overview","algorithms","testing"]) byId(id).hidden = id !== view; document.querySelectorAll("[data-view]").forEach(b => b.classList.toggle("active", b.dataset.view===view)); }
document.querySelectorAll("[data-view]").forEach(button => button.addEventListener("click", () => select(button.dataset.view)));
fetch("/api/v1/documentation", {credentials:"same-origin"}).then(async response => {
  if(response.status===401) { location.href="/"; return null; }
  if(!response.ok) throw new Error("Материалы временно недоступны");
  return response.json();
}).then(data => {
  if(!data) return;
  byId("status").hidden=true;
  for(const doc of data.documents) byId("documents").append(download(doc.title, doc.path));
  for(const algorithm of data.catalog?.algorithms || []) {
    const row=element("tr"), title=element("td",algorithm.title), basis=element("td",algorithm.basis), tests=element("td"), links=element("td");
    title.append(element("code",algorithm.id));
    const report=data.tests?.algorithms.find(item=>item.id===algorithm.id);
    tests.textContent=report ? `${report.passed} пройдено / ${report.failed} ошибок / ${report.skipped} пропущено` : "Нет опубликованного прогона";
    tests.className=report?.failed ? "fail" : "pass";
    links.append(sourceLink("Код",algorithm.source), sourceLink("Пример",algorithm.example));
    if(report) links.append(download("CSV",`docs/evidence/algorithms/${algorithm.id}.csv`));
    row.append(title,basis,tests,links); byId("algorithm-rows").append(row);
  }
  const summary=byId("test-summary");
  summary.append(element("p","Проверка на независимых сценариях и тестах ограничений. Статистическая k-fold кросс-валидация не применяется: обучаемой модели нет."));
  if(data.tests) summary.append(download("Все измерения JSON","docs/evidence/algorithms/summary.json"), document.createTextNode(" · "), download("Метрики сценариев CSV","docs/evidence/algorithms/cross_scenario.csv"));
  if(data.system) summary.append(element("p",`Системная регрессия: ${data.system.passed} пройдено, ${data.system.failed} ошибок, ${data.system.skipped} пропущено.`));
  const load=byId("load-results");
  if(data.load) {
    load.append(element("p",`Испытательный сервер: ${data.load.hardware.cpus} CPU, ${data.load.hardware.memory_mb} МБ RAM.`));
    for(const run of data.load.runs) load.append(element("p",`${run.users} пользователей: ${run.requests} запросов, p95 ${run.p95_ms} мс, ошибок 5xx: ${run.server_errors}, ответов 429: ${run.rate_limited}.`));
    load.append(download("Отчёт JSON","docs/evidence/load/summary.json"),document.createTextNode(" · "),download("Запросы CSV","docs/evidence/load/requests.csv"));
  } else load.append(element("p","Измерения ещё не опубликованы."));
  select("overview"); window.lucide?.createIcons();
}).catch(error=> { byId("status").textContent=error.message; });
