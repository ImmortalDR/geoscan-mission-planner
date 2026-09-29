"""Explain omissions without mistaking a sparse-graph bound for a proof."""
import math


def explain_task(model, tid):
    g=model.g;task=g.tids.index(tid);allowed=g.bundle['feasibility']['eligible_uav_ids_by_task'][tid]
    result=dict(task_id=tid,parent_task_id=g.tasks[tid]['parent_task_id'])
    if not allowed:
        reasons={uid:g.bundle['feasibility'].get('ineligible_reasons',{}).get(f'{tid}:{uid}','not_eligible') for uid in g.fleet}
        result.update(reason='no_eligible_uav',message='Нет совместимого БПЛА: проверьте датчик, ветер и ограничения модели.',aircraft_reasons=reasons)
        return result
    options=[];invalid=[]
    for uid in allowed:
        p=g.by_uav[uid];u=g.fleet[uid];policy=g.policy[uid]
        starts=sorted(set(policy['start']+policy['refuel']));ends=sorted(set(policy['finish']+policy['refuel']))
        usable=u['operational_endurance_min']*60*(1-u['energy_reserve_fraction']);best=(math.inf,None,None)
        for row in p['services'][2*task:2*task+2]:
            if not row['valid']:continue
            for a in starts:
                for z in ends:
                    if policy['same_base'] and a!=z:continue
                    aa,zz=g.bases[a],g.bases[z]
                    outbound=p['takeoff']+p['dist'][aa,row['entry']]
                    need=max(p['takeoff']+p['q'][aa,row['entry']],outbound+row['required_s'],
                             outbound+row['time_s']+p['q'][row['exit'],zz])
                    if need<best[0]:best=(need,a,z)
        if math.isfinite(best[0]):
            options.append(dict(uav_id=uid,required_at_least_s=best[0],usable_s=usable,start_site=best[1],landing_site=best[2]))
        else:invalid.append(uid)
    result['resource_options']=options
    if options and all(x['required_at_least_s']>x['usable_s']+1e-6 for x in options):
        best=min(options,key=lambda x:x['required_at_least_s']/x['usable_s'])
        result.update(reason='graph_resource_limit',message=(f"В рассчитанном графе даже отдельному галсу нужно не менее {best['required_at_least_s']/60:.1f} мин ресурса "
            f"у {best['uav_id']}, доступно {best['usable_s']/60:.1f} мин. Проверьте размещение и разрешения баз, ресурс БПЛА. "
            "Оценка содержит запас возврата; это не доказательство невозможности вне данного графа."))
    elif not options:
        result.update(reason='no_verified_graph_path',message='Нет проверенного пути между разрешёнными базами и галсом. Проверьте геометрию запретов, рельеф и разрешения площадок.',affected_uavs=invalid)
    else:
        result.update(reason='routing_search_unresolved',message='Галс отдельно доступен, но общий план в пределах поиска не найден. Попробуйте большую глубину; проверьте окно миссии, дозарядку и загрузку бортов.')
    return result
