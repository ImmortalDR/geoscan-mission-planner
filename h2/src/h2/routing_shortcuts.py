"""Deterministic, physical shortcuts between mandatory survey/base visits."""
import math
import time

from .routing_graph import angle, heading, transit_motion
from .transit import TransitContext, NoPath


class TransitShortcuts:
    def __init__(self, graph):
        self.g = graph
        self.context = TransitContext(graph.scene, graph.bundle['crs']['metric_epsg'])
        self.cache = {}
        self.stats = dict(transfers_checked=0, direct_evaluations=0, blocked=0,
                          resource_rejections=0, accepted=0, removed_edges=0,
                          saved_flight_s=0., calculation_s=0.)

    def _direct(self, uid, a, b):
        g = self.g; p = g.by_uav[uid]
        key = (id(p), a, b)
        if key not in self.cache:
            self.stats['direct_evaluations'] += 1
            try:
                points = transit_motion(self.context, g.settings, p['elevation'],
                    g.fleet[uid], g.vertices[a], g.vertices[b], 'shortcut',
                    p['turn_seconds']/g.transit_factor, direct_only=True)
                points[:, 3] *= g.transit_factor
                self.cache[key] = (a, b, points, heading(points), heading(points, True))
            except (NoPath, ValueError):
                self.cache[key] = None
                self.stats['blocked'] += 1
        return self.cache[key]

    def shorten(self, model, vehicle, source_id, destination_id, edges, available_s):
        """Return an improving transfer, or None to retain the exact old path.

        A chain is bounded by survey service or a real base visit. No survey,
        landing, recharge or takeoff can be removed by this operation.
        """
        if len(edges) < 2:
            return None
        began = time.perf_counter()
        try:
            return self._shorten(model, vehicle, source_id, destination_id, edges, available_s)
        finally:
            self.stats['calculation_s'] += time.perf_counter() - began

    def _shorten(self, model, vehicle, source_id, destination_id, edges, available_s):
        g = self.g; uid = model.uids[vehicle]; p = g.by_uav[uid]
        self.stats['transfers_checked'] += 1
        def visit_heading(node_id, end):
            node = model.nodes[node_id]
            return heading(p['services'][node.service]['points'], end) if node.kind == 'survey' else None
        start_yaw = visit_heading(source_id, True)
        end_yaw = visit_heading(destination_id, False)
        sections = [(a, b, p['records'][a, b], *p['edge_headings'][a, b]) for a, b in edges]

        def evaluate(chain):
            elapsed = need = 0.; previous = start_yaw; extras = []
            for i, (a, b, points, first, last) in enumerate(chain):
                extra = 0. if previous is None else p['turn_seconds']*angle(previous, first)
                final = i == len(chain)-1
                if final and end_yaw is not None:
                    extra += p['turn_seconds']*angle(last, end_yaw)
                elapsed += float(points[-1, 3]) + extra
                extras.append(float(extra))
                vertex = g.vertices[b]
                turn = 0.
                if vertex['kind'] != 'base':
                    task = g.tasks[vertex['task_id']]
                    escape_yaw = (heading(task['geom_coords'], True) if vertex['id'].endswith(':1')
                                  else heading(task['geom_coords'])+math.pi)
                    turn = p['turn_seconds']*angle(end_yaw if final and end_yaw is not None else last, escape_yaw)
                # From every interior point, finish this physical edge, then
                # rotate into the verified return path from its retained end.
                # Prefix+remaining-edge is constant along the edge, so this
                # bound covers interiors, not only the displayed vertices.
                need = max(need, elapsed + turn + float(p['returns'][b]) + g.return_margin)
                previous = last
            return float(elapsed), float(need), extras

        original_s, _, _ = evaluate(sections)
        value = original_s; accepted = 0
        def try_replace(lo, hi):
            nonlocal sections, value, accepted
            direct = self._direct(uid, sections[lo][0], sections[hi-1][1])
            if direct is None:
                return False
            proposed = sections[:lo] + [direct] + sections[hi:]
            cost, need, _ = evaluate(proposed)
            if cost >= value - 1e-7:
                return False
            if need > available_s + 1e-6:
                self.stats['resource_rejections'] += 1
                return False
            sections = proposed; value = cost; accepted += 1
            return True

        # Try the complete transit first. If the chord is blocked/too costly,
        # remove intermediate vertices locally and revisit the new neighbours.
        try_replace(0, len(sections))
        i = 0
        while i+1 < len(sections):
            if try_replace(i, i+2):
                i = max(0, i-1)
            else:
                i += 1
        if not accepted:
            return None
        duration, need, extras = evaluate(sections)
        trajectories = []
        for section, extra in zip(sections, extras):
            points = section[2].copy(); seconds = float(points[-1, 3])
            if seconds > 0:
                points[:, 3] *= (seconds + extra)/seconds
            elif extra > 0:
                points[-1, 3] = extra
            trajectories.append(points)
        self.stats['accepted'] += accepted
        self.stats['removed_edges'] += len(edges)-len(sections)
        self.stats['saved_flight_s'] += original_s-duration
        return dict(trajectories=trajectories, required_s=need)
