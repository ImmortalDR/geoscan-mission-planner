/* Pure editor rules shared by the browser and regression tests. */
const SceneEditor = (() => {
  const prefixes = {survey_areas:'Job', landing_sites:'Base', allowed_airspace:'Airspace', no_fly_zones:'NoFly', temporal_airspace:'TimeZone', obstacles:'Obstacle'};
  function nextName(prefix, used) {
    const names = new Set(used);
    let number = 0;
    while (names.has(`${prefix}_${number}`)) number++;
    return `${prefix}_${number}`;
  }
  function names(items, prefix) {
    const pattern = new RegExp(`^${prefix}_[0-9]+$`);
    const used = items.map(x => x.name || (pattern.test(x.id) ? x.id : null)).filter(Boolean);
    return items.map(x => {
      const name = x.name || (pattern.test(x.id) ? x.id : nextName(prefix, used));
      used.push(name);
      return name;
    });
  }
  function eligible(sites, column) {
    const roles = column === 'start_site' ? ['both','start'] : column === 'landing_site' ? ['both','landing'] : ['both'];
    return sites.filter(s => !s.candidate && roles.includes(s.role)).map(s => s.id);
  }
  function refuels(uav, sites) {
    return eligible(sites, 'refuel_sites').filter(id => Object.hasOwn(uav, 'refuel_sites')
      ? uav.refuel_sites === null || uav.refuel_sites.includes(id)
      : (uav.start_site == null || uav.start_site === id) && (uav.landing_site == null || uav.landing_site === id));
  }
  function choose(uav, sites, column, id, checked) {
    if (id !== null && !eligible(sites, column).includes(id)) return false;
    // Resolve the old policy before changing endpoints; never replace it with unrestricted refuelling.
    if (!Object.hasOwn(uav, 'refuel_sites')) uav.refuel_sites = refuels(uav, sites);
    if (column !== 'refuel_sites') uav[column] = id;
    else if (id === null) uav.refuel_sites = checked ? null : [];
    else {
      const selected = new Set(refuels(uav, sites));
      if (checked) selected.add(id); else selected.delete(id);
      uav.refuel_sites = [...selected].sort();
    }
    return true;
  }
  function reconcile(uav, sites) {
    let changed = false;
    for (const column of ['start_site','landing_site']) {
      if (uav[column] != null && !eligible(sites, column).includes(uav[column])) { uav[column] = null; changed = true; }
    }
    if (Array.isArray(uav.refuel_sites)) {
      const valid = [...new Set(uav.refuel_sites)].filter(id => eligible(sites,'refuel_sites').includes(id));
      if (JSON.stringify(valid) !== JSON.stringify(uav.refuel_sites)) { uav.refuel_sites = valid; changed = true; }
    }
    return changed;
  }
  return {prefixes, nextName, names, eligible, refuels, choose, reconcile};
})();
if (typeof module !== 'undefined') module.exports = SceneEditor;
