"""Speed cameras and average speed sections from OpenStreetMap extracts in OPL (osmium's text format), as small CSVs.

    python3 cameras.py --areas czech-republic         # czech-republic-areas.geojson: where its roads are needed
    python3 cameras.py czech-republic slovakia ...     # cameras.csv and cameras2.csv

Each country is <name>.opl (speed cameras and enforcement relations with their members) and, when its areas file
had anything in it, <name>-roads.opl (the roads in those areas: around the average speed sections and around the
cameras that stand on a road and watch one way along it).

cameras.csv, read by Drive HUD up to 1.6: "# <date> <sources>", then "c,lat,lon" for a camera (a highway=speed_camera
node, or the device of an enforcement=maxspeed relation) and "s,lat,lon,lat,lon" for an average speed section
(enforcement=average_speed) from its "from" to its "to" member; a way stands in with its middle node.

cameras2.csv, read from Drive HUD 1.7: the same first line, then "c,lat,lon,direction" with the direction of travel the
camera watches in whole degrees (empty when it isn't known or it watches both), "s,limit,lat,lon,lat,lon,..." for a
section with its limit in km/h (0 when not known) and its course along the roads from its start to its end, and
"e,limit,lat,lon,lat,lon" for one whose ends no road joins in the data, with just those. Coordinates have 5 decimals
(about a metre).
"""
import datetime
import heapq
import json
import math
import os
import sys

# Roads a car drives an average speed section on; service roads, tracks and paths aren't routed over.
ROADS = {
    "motorway", "motorway_link", "trunk", "trunk_link", "primary", "primary_link", "secondary", "secondary_link",
    "tertiary", "tertiary_link", "unclassified", "residential", "living_street",
}
# Margin around a section's ends for its roads, metres: at least this, or a share of the section's length.
SECTION_MARGIN = 1500
SECTION_MARGIN_SHARE = 0.3
CAMERA_MARGIN = 60  # around a camera standing on a road, for the road's direction there
SNAP = 60  # how far from its road a section's end may be mapped, metres
METRES_PER_DEGREE = 111_195.0


def parse(line):
    """An OPL line as (type, id, {field letter: value})."""
    parts = line.split()
    return parts[0][0], int(parts[0][1:]), {p[0]: p[1:] for p in parts[1:]}


def unescape(text):
    """OPL writes spaces, commas and a few more characters as %hex%."""
    out, i = [], 0
    while i < len(text):
        end = text.find("%", i + 1) if text[i] == "%" else -1
        if end > 0:
            out.append(chr(int(text[i + 1:end], 16)))
            i = end + 1
        else:
            out.append(text[i])
            i += 1
    return "".join(out)


def tags(text):
    if not text:
        return {}
    pairs = (kv.split("=", 1) for kv in text.split(",") if "=" in kv)
    return {unescape(k): unescape(v) for k, v in pairs}


def metres(a, b):
    """Great-circle distance between two (lat, lon) points."""
    la1, lo1, la2, lo2 = map(math.radians, (*a, *b))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * 6_371_000 * math.asin(math.sqrt(h))


def bearing(a, b):
    """Compass bearing in degrees from a to b; flat, close enough over one stretch of road."""
    dx = (b[1] - a[1]) * math.cos(math.radians((a[0] + b[0]) / 2))
    return math.degrees(math.atan2(dx, b[0] - a[0])) % 360


def off_segment(p, a, b):
    """How far p is from the stretch a-b in metres, and where along it (0 at a, 1 at b) the nearest point lies."""
    k = math.cos(math.radians(p[0])) * METRES_PER_DEGREE
    ax, ay = (a[1] - p[1]) * k, (a[0] - p[0]) * METRES_PER_DEGREE
    dx, dy = (b[1] - p[1]) * k - ax, (b[0] - p[0]) * METRES_PER_DEGREE - ay
    length = dx * dx + dy * dy
    t = 0.0 if length == 0 else min(1.0, max(0.0, -(ax * dx + ay * dy) / length))
    return math.hypot(ax + t * dx, ay + t * dy), t


def between(a, b, t):
    return round(a[0] + (b[0] - a[0]) * t, 5), round(a[1] + (b[1] - a[1]) * t, 5)


def speed_limit(value):
    """km/h from a maxspeed value: "80", "CZ:urban" and the like; 0 when it gives none."""
    v = (value or "").split(";")[0].strip().lower()
    if v.isdigit():
        return int(v)
    if v.endswith("mph") and v[:-3].strip().isdigit():
        return round(int(v[:-3].strip()) * 1.609)
    kind = v.partition(":")[2]
    defaults = {"living_street": 20, "urban": 50, "rural": 90, "trunk": 110, "motorway": 130}
    if kind in defaults:
        return defaults[kind]
    return int(kind) if kind.isdigit() else 0


def oneway(t):
    """+1 one way along the drawing, -1 against it, 0 both ways."""
    value = t.get("oneway", "")
    if value in ("yes", "true", "1"):
        return 1
    if value in ("-1", "reverse"):
        return -1
    if value == "no":
        return 0
    implied = t.get("highway") in ("motorway", "motorway_link") or t.get("junction") in ("roundabout", "circular")
    return 1 if implied else 0


class Country:
    """One country's cameras, enforcement relations and roads."""

    def __init__(self, name):
        self.nodes = {}  # id: (lat, lon)
        self.camera_tags = {}  # id: tags of a highway=speed_camera node
        self.camera_ways = {}  # camera node id: [road ways through it]
        self.ways = {}  # id: [node ids]
        self.way_tags = {}  # id: tags, of the roads only
        self.relations = []  # (tags, {role: [member refs like "n123"]})
        self.read(f"{name}.opl")
        if os.path.exists(f"{name}-roads.opl"):
            self.read(f"{name}-roads.opl")

    def read(self, path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                kind, ident, fields = parse(line)
                if kind == "n":
                    if not fields.get("x") or not fields.get("y"):
                        continue
                    self.nodes[ident] = (round(float(fields["y"]), 5), round(float(fields["x"]), 5))
                    t = tags(fields.get("T"))
                    if t.get("highway") == "speed_camera":
                        self.camera_tags[ident] = t
                elif kind == "w":
                    refs = [int(n[1:]) for n in fields.get("N", "").split(",") if n]
                    self.ways[ident] = refs
                    t = tags(fields.get("T"))
                    if t.get("highway") in ROADS:
                        self.way_tags[ident] = t
                        for n in refs:
                            if n in self.camera_tags:
                                self.camera_ways.setdefault(n, []).append(ident)
                elif kind == "r":
                    roles = {}
                    for m in fields.get("M", "").split(","):
                        if "@" in m:
                            ref, _, role = m.partition("@")
                            roles.setdefault(unescape(role), []).append(ref)
                    self.relations.append((tags(fields.get("T")), roles))

    def spot(self, ref):
        """A member's place: a node's, or a way's middle node's."""
        kind, ident = ref[0], int(ref[1:])
        if kind == "n":
            return self.nodes.get(ident)
        way = self.ways.get(ident) if kind == "w" else None
        return self.nodes.get(way[len(way) // 2]) if way else None

    def enforcement(self, kind):
        return [(t, roles) for t, roles in self.relations if t.get("type") == "enforcement" and t.get("enforcement") == kind]

    # The old file, as it always was.

    def old_cameras(self):
        found = {self.nodes[n] for n in self.camera_tags}
        for _, roles in self.enforcement("maxspeed"):
            found.update(s for s in map(self.spot, roles.get("device", [])) if s)
        return found

    def old_sections(self):
        found = set()
        for _, roles in self.enforcement("average_speed"):
            if roles.get("from") and roles.get("to"):
                start, end = self.spot(roles["from"][0]), self.spot(roles["to"][0])
                if start and end:
                    found.add(start + end)
        return found

    # Where the roads are needed.

    def areas(self):
        """Boxes (south, west, north, east) around each section and each camera watching one way along a road."""
        boxes = []
        for _, roles in self.enforcement("average_speed"):
            ends = [s for s in (self.spot(r) for role in ("from", "to", "section") for r in roles.get(role, [])) if s]
            if ends:
                lats, lons = [s[0] for s in ends], [s[1] for s in ends]
                span = metres((min(lats), min(lons)), (max(lats), max(lons)))
                boxes.append(box(ends, max(SECTION_MARGIN, SECTION_MARGIN_SHARE * span)))
        for ident, t in self.camera_tags.items():
            if t.get("direction") in ("forward", "backward"):
                boxes.append(box([self.nodes[ident]], CAMERA_MARGIN))
        return merge(boxes)

    # The new file.

    def directions(self):
        """{place: {direction in degrees, None for unknown or both}} for every camera."""
        found = {}
        for ident, t in self.camera_tags.items():
            found.setdefault(self.nodes[ident], set()).add(self.camera_direction(ident, t.get("direction")))
        for _, roles in self.enforcement("maxspeed"):
            start = self.spot(roles["from"][0]) if roles.get("from") else None
            end = self.spot(roles["to"][0]) if roles.get("to") else None
            # "from" lies before "to" on the road the device watches: that's the direction of travel.
            way = round(bearing(start, end)) % 360 if start and end and start != end else None
            for device in roles.get("device", []):
                spot = self.spot(device)
                if not spot:
                    continue
                if way is None and device[0] == "n":
                    own = self.camera_direction(int(device[1:]), self.camera_tags.get(int(device[1:]), {}).get("direction"))
                else:
                    own = way
                found.setdefault(spot, set()).add(own)
        return found

    def camera_direction(self, node, direction):
        """The direction of travel a camera standing on a road watches: direction=forward or backward along the road.
        Degrees as the value say where the lens points, which mappers use both ways, so they count as unknown."""
        if direction not in ("forward", "backward"):
            return None
        for way in self.camera_ways.get(node, []):
            refs = [n for n in self.ways[way] if n in self.nodes]
            if node not in refs or len(refs) < 2:
                continue
            i = refs.index(node)
            a, b = (refs[i - 1], refs[i]) if i > 0 else (refs[i], refs[i + 1])
            ahead = bearing(self.nodes[a], self.nodes[b])
            return round(ahead if direction == "forward" else ahead + 180) % 360
        return None

    def sections(self):
        """{(start, end): (limit, [points from start to end], whether they follow the roads)} for every average speed
        section."""
        graph = Graph(self) if self.way_tags else None
        found = {}
        for t, roles in self.enforcement("average_speed"):
            if not roles.get("from") or not roles.get("to"):
                continue
            start, end = self.spot(roles["from"][0]), self.spot(roles["to"][0])
            if not start or not end or (start, end) in found:
                continue
            prefer = {int(r[1:]) for r in roles.get("section", []) if r[0] == "w"}
            route, ways = graph.route(start, end, prefer) if graph else (None, [])
            limit = speed_limit(t.get("maxspeed")) or (graph.common_limit(ways) if graph else 0)
            found[(start, end)] = (limit, simplify(route) if route else [start, end], route is not None)
        return found


def watched(found):
    """The one direction of travel a camera watches, from what its node and relations say (None: they don't say);
    None when nothing says or they name different ones, as a camera watching both ways does."""
    known = sorted(d for d in found if d is not None)
    if not known or any(abs((d - known[0] + 180) % 360 - 180) > 45 for d in known[1:]):
        return None
    return known[0]


def box(points, margin):
    lats, lons = [p[0] for p in points], [p[1] for p in points]
    dlat = margin / METRES_PER_DEGREE
    dlon = margin / (METRES_PER_DEGREE * math.cos(math.radians(sum(lats) / len(lats))))
    return min(lats) - dlat, min(lons) - dlon, max(lats) + dlat, max(lons) + dlon


def merge(boxes):
    """Overlapping boxes joined into one covering both, until none overlap, as the parts of a multipolygon mustn't.
    Each round sweeps them from south to north; a joined box may reach another, so rounds go on until none joins."""
    boxes = sorted(boxes)
    while True:
        out = []
        for b in boxes:
            for i in range(len(out) - 1, -1, -1):
                a = out[i]
                if a[0] <= b[2] and b[0] <= a[2] and a[1] <= b[3] and b[1] <= a[3]:
                    out[i] = min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3])
                    break
            else:
                out.append(b)
        if len(out) == len(boxes):
            return out
        boxes = sorted(out)


class Graph:
    """The roads as stretches between nodes, with a grid to find the stretches near a place."""

    CELL = 0.002  # degrees; a cell is about 220 m by 140 m

    def __init__(self, country):
        self.nodes = country.nodes
        self.way_tags = country.way_tags
        self.edges = {}  # node: [(next node, metres, way, allowed: not against a one-way street)]
        self.grid = {}  # (row, column): [(a, b, way)], stretches as drawn
        for way, t in country.way_tags.items():
            refs = [n for n in country.ways[way] if n in self.nodes]
            direction = oneway(t)
            for a, b in zip(refs, refs[1:]):
                length = metres(self.nodes[a], self.nodes[b])
                self.edges.setdefault(a, []).append((b, length, way, direction >= 0))
                self.edges.setdefault(b, []).append((a, length, way, direction <= 0))
                for cell in self.cells(self.nodes[a], self.nodes[b]):
                    self.grid.setdefault(cell, []).append((a, b, way))

    def cells(self, p, q):
        rows = range(math.floor(min(p[0], q[0]) / self.CELL), math.floor(max(p[0], q[0]) / self.CELL) + 1)
        cols = range(math.floor(min(p[1], q[1]) / self.CELL), math.floor(max(p[1], q[1]) / self.CELL) + 1)
        return [(r, c) for r in rows for c in cols]

    def near(self, p):
        """Stretches within SNAP metres of p: (metres off, share along, a, b, way)."""
        pad = 2 * SNAP / METRES_PER_DEGREE
        seen, out = set(), []
        for cell in self.cells((p[0] - pad, p[1] - pad), (p[0] + pad, p[1] + pad)):
            for a, b, way in self.grid.get(cell, []):
                if (a, b, way) not in seen:
                    seen.add((a, b, way))
                    off, t = off_segment(p, self.nodes[a], self.nodes[b])
                    if off <= SNAP:
                        out.append((off, t, a, b, way))
        return out

    def allowed(self, a, b, way):
        return any(n == b and w == way and ok for n, _, w, ok in self.edges.get(a, []))

    def route(self, start, end, prefer, strict=True):
        """([points], [(way, metres)]) along the roads from start to end, or (None, []) when they don't join well.
        The section's own ways, when the relation lists them, cost less, so the route keeps to them. Without strict,
        one-way streets may be driven either way: a last try for a section whose ends are mapped on the wrong side."""
        straight = metres(start, end)
        heap, best, came, exits = [], {}, {}, {}
        finish = None  # (cost, last node or None, the straight way along one stretch)
        starts, ends = self.near(start), self.near(end)
        def cost_along(way, metres_):
            return metres_ * (0.7 if way in prefer else 1)

        # Into the road network from where the start lies beside a stretch...
        for off, t, a, b, way in starts:
            length = metres(self.nodes[a], self.nodes[b])
            for frm, to, share in ((a, b, 1 - t), (b, a, t)):
                if not strict or self.allowed(frm, to, way):
                    cost = cost_along(way, share * length) + 2 * off
                    if cost < best.get(to, math.inf):
                        best[to] = cost
                        came[to] = ("start", between(self.nodes[a], self.nodes[b], t), way, share * length)
                        heapq.heappush(heap, (cost, to))
        # ...and out of it to where the end does.
        for off, t, a, b, way in ends:
            length = metres(self.nodes[a], self.nodes[b])
            for frm, to, share in ((a, b, t), (b, a, 1 - t)):
                if not strict or self.allowed(frm, to, way):
                    extra = cost_along(way, share * length) + 2 * off
                    if extra < exits.get(frm, (math.inf,))[0]:
                        exits[frm] = (extra, between(self.nodes[a], self.nodes[b], t), way, share * length)
        # Both ends beside the same stretch, the end further on: straight along it.
        for off1, t1, a, b, way in starts:
            for off2, t2, c, d, other in ends:
                if (a, b, way) == (c, d, other) and ((t2 >= t1 and (not strict or self.allowed(a, b, way))) or
                                                     (t2 < t1 and (not strict or self.allowed(b, a, way)))):
                    length = abs(t2 - t1) * metres(self.nodes[a], self.nodes[b])
                    cost = cost_along(way, length) + 2 * (off1 + off2)
                    if not finish or cost < finish[0]:
                        p, q = between(self.nodes[a], self.nodes[b], t1), between(self.nodes[a], self.nodes[b], t2)
                        finish = (cost, None, ([p, q], [(way, length)]))
        while heap:
            cost, node = heapq.heappop(heap)
            if (finish and cost >= finish[0]) or cost > 3 * straight + 3000:
                break
            if cost > best.get(node, math.inf):
                continue  # reached cheaper since
            if node in exits and (not finish or cost + exits[node][0] < finish[0]):
                finish = (cost + exits[node][0], node, None)
            for nxt, length, way, ok in self.edges.get(node, []):
                if strict and not ok:
                    continue
                total = cost + cost_along(way, length)
                if total < best.get(nxt, math.inf):
                    best[nxt] = total
                    came[nxt] = (node, None, way, length)
                    heapq.heappush(heap, (total, nxt))
        if not finish:
            return self.route(start, end, prefer, strict=False) if strict else (None, [])
        if finish[1] is None:
            points, ways = finish[2]
        else:
            node = finish[1]
            _, exit_point, exit_way, exit_length = exits[node]
            points, ways = [exit_point], [(exit_way, exit_length)]
            while True:
                before, point, way, length = came[node]
                points.append(self.nodes[node])
                ways.append((way, length))
                if before == "start":
                    points.append(point)
                    break
                node = before
            points.reverse()
        if sum(metres(p, q) for p, q in zip(points, points[1:])) > 2 * straight + 1500:
            # A long way round: the ends are on the wrong carriageway or the roads between them are missing.
            return self.route(start, end, prefer, strict=False) if strict else (None, [])
        return points, ways

    def common_limit(self, ways):
        """The limit on most of a route by length, if that's well over half of it; else 0."""
        total, by_limit = 0.0, {}
        for way, length in ways:
            total += length
            limit = speed_limit(self.way_tags.get(way, {}).get("maxspeed"))
            if limit:
                by_limit[limit] = by_limit.get(limit, 0) + length
        if not by_limit or total == 0:
            return 0
        limit, length = max(by_limit.items(), key=lambda kv: kv[1])
        return limit if length >= 0.6 * total else 0


def simplify(points, tolerance=5.0):
    """Douglas-Peucker: the points a line within tolerance metres of the route needs."""
    keep = {0, len(points) - 1}
    stack = [(0, len(points) - 1)]
    while stack:
        i, j = stack.pop()
        far, at = 0.0, None
        for k in range(i + 1, j):
            off, _ = off_segment(points[k], points[i], points[j])
            if off > far:
                far, at = off, k
        if at is not None and far > tolerance:
            keep.add(at)
            stack += [(i, at), (at, j)]
    return [points[k] for k in sorted(keep)]


def write_areas(name):
    """<name>-areas.geojson with the boxes to cut its roads from, or no file when there are none."""
    boxes = Country(name).areas()
    path = f"{name}-areas.geojson"
    if not boxes:
        if os.path.exists(path):
            os.remove(path)
        return
    polygons = [[[[w, s], [e, s], [e, n], [w, n], [w, s]]] for s, w, n, e in boxes]
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"type": "Feature", "properties": {}, "geometry": {"type": "MultiPolygon", "coordinates": polygons}}, f)
    print(f"{name}: {len(boxes)} areas for roads", file=sys.stderr)


def main(names):
    countries = [Country(n) for n in names]
    header = f"# {datetime.date.today().isoformat()} {' '.join(names)}\n"
    old_cameras, old_sections, directions, by_ends = set(), set(), {}, {}
    for c in countries:
        old_cameras |= c.old_cameras()
        old_sections |= c.old_sections()
        for spot, found in c.directions().items():
            directions.setdefault(spot, set()).update(found)
        # A section by a border is in both countries' extracts; the first one's course will do.
        for ends, section in c.sections().items():
            by_ends.setdefault(ends, section)
    sections = list(by_ends.values())
    with open("cameras.csv", "w", encoding="utf-8") as f:
        f.write(header)
        for lat, lon in sorted(old_cameras):
            f.write(f"c,{lat:.5f},{lon:.5f}\n")
        for s in sorted(old_sections):
            f.write("s," + ",".join(f"{v:.5f}" for v in s) + "\n")
    with open("cameras2.csv", "w", encoding="utf-8") as f:
        f.write(header)
        for spot in sorted(directions):
            way = watched(directions[spot])
            f.write(f"c,{spot[0]:.5f},{spot[1]:.5f},{'' if way is None else way}\n")
        for limit, points, routed in sorted(sections, key=lambda s: s[1]):
            f.write(f"{'s' if routed else 'e'},{limit}," + ",".join(f"{v:.5f}" for p in points for v in p) + "\n")
    known = sum(1 for found in directions.values() if watched(found) is not None)
    print(
        f"cameras: {len(directions)} ({known} with a direction), sections: {len(sections)} "
        f"({sum(1 for s in sections if s[2])} along the roads, {sum(1 for s in sections if s[0])} with a limit)",
        file=sys.stderr,
    )


if __name__ == "__main__":
    if sys.argv[1:2] == ["--areas"]:
        for country in sys.argv[2:]:
            write_areas(country)
    else:
        main(sys.argv[1:])
