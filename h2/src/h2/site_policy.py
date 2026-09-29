"""Explicit refuel_sites opts into mission-level endpoints; absent means legacy."""


def sites_for(bundle, u):
    sites = {s['id']:s for s in bundle['sites'] if not s.get('candidate', False)}
    starts = [sid for sid,s in sites.items() if s['role'] in ('both','start')
              and (u['start_site'] is None or sid == u['start_site'])]
    ends = [sid for sid,s in sites.items() if s['role'] in ('both','landing')
            and (u['landing_site'] is None or sid == u['landing_site'])]
    if 'refuel_sites' in u:
        refuels = [sid for sid,s in sites.items() if s['role']=='both'
                   and (u['refuel_sites'] is None or sid in u['refuel_sites'])]
    else:
        refuels = [sid for sid,s in sites.items() if s['role']=='both'
                   and (u['start_site'] is None or sid == u['start_site'])
                   and (u['landing_site'] is None or sid == u['landing_site'])]
    # Emergency return may use any explicit non-candidate landing-capable site.
    # Refuelling/relaunch always remains restricted to refuels above.
    returns = [sid for sid,s in sites.items() if s['role'] in ('both','landing','reserve')]
    return dict(start=sorted(starts), finish=sorted(ends), refuel=sorted(refuels),
                emergency=sorted(returns), legacy='refuel_sites' not in u,
                same_base='refuel_sites' not in u and not bundle['mission']['allow_different_start_end'])
