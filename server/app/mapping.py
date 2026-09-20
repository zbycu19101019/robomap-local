"""Observed contact points and conservative wall hypotheses, never a laser scan."""
import json
import math
import time
from pathlib import Path


class MappingSurvey:
    def __init__(self, root):
        self.path = Path(root) / 'mapping-survey.json'
        self.active = False
        self.points = []
        self.unlocated = 0
        try:
            data = json.loads(self.path.read_text(encoding='utf-8'))
            self.points = data.get('points', [])[-1000:]
            self.unlocated = int(data.get('unlocated', 0))
        except (OSError, ValueError, TypeError):
            pass

    def save(self):
        tmp = self.path.with_suffix('.tmp')
        tmp.write_text(json.dumps({'points': self.points, 'unlocated': self.unlocated}), encoding='utf-8')
        tmp.replace(self.path)

    def contact(self, loc, source='bumper-error'):
        if not self.active:
            return False
        if (not loc.get('calibrated') or loc.get('confidence', 0) < .35
                or loc.get('source') == 'autonomous-last-known'):
            self.unlocated += 1
            self.save()
            return False
        p = loc['pose']
        # Contact lies approximately one robot radius ahead of its centre.
        x = p['x'] + .17 * math.sin(p['heading'])
        y = p['y'] - .17 * math.cos(p['heading'])
        if not all(math.isfinite(v) for v in (x, y)):
            return False
        if any(math.hypot(q['x']-x, q['y']-y) < .08 for q in self.points):
            return False
        self.points.append({'x': round(x, 4), 'y': round(y, 4), 'source': source,
                            'confidence': loc['confidence'], 'time': time.time()})
        self.points = self.points[-1000:]
        self.save()
        return True

    def walls(self):
        # Fit separate local clusters; never close rooms or bridge unseen gaps.
        remaining = list(self.points)
        result = []
        while remaining:
            group = [remaining.pop(0)]
            for p in group:
                near = [q for q in remaining if math.hypot(p['x']-q['x'], p['y']-q['y']) <= .65]
                group.extend(near)
                for q in near:
                    remaining.remove(q)
            if len(group) < 3:
                continue
            cx = sum(p['x'] for p in group)/len(group)
            cy = sum(p['y'] for p in group)/len(group)
            xx = sum((p['x']-cx)**2 for p in group)
            yy = sum((p['y']-cy)**2 for p in group)
            xy = sum((p['x']-cx)*(p['y']-cy) for p in group)
            angle = .5*math.atan2(2*xy, xx-yy)
            ux, uy = math.cos(angle), math.sin(angle)
            if max(abs(-(p['x']-cx)*uy+(p['y']-cy)*ux) for p in group) > .10:
                continue
            ts = [(p['x']-cx)*ux+(p['y']-cy)*uy for p in group]
            a, b = min(ts), max(ts)
            if b-a < .4:
                continue
            start = {'x': round(cx+a*ux, 3), 'y': round(cy+a*uy, 3)}
            end = {'x': round(cx+b*ux, 3), 'y': round(cy+b*uy, 3)}
            import hashlib
            ident = hashlib.sha256(json.dumps([start, end], sort_keys=True).encode()).hexdigest()[:12]
            result.append({'id': 'survey-'+ident, 'start': start, 'end': end, 'samples': len(group)})
        return result

    def view(self):
        return {'active': self.active, 'points': self.points, 'walls': self.walls(),
                'unlocated': self.unlocated, 'approximate': True}
