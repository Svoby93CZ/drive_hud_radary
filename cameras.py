"""Speed cameras and average speed sections from OpenStreetMap extracts in OPL (osmium's text format), as a small CSV.

    python3 cameras.py czech-republic.opl slovakia.opl ... > cameras.csv

The first line is "# <date> <sources>". Then "c,lat,lon" for a camera: a highway=speed_camera node, or the device of an
enforcement=maxspeed relation. And "s,lat,lon,lat,lon" for an average speed section (enforcement=average_speed) from its
"from" to its "to" member; a way stands in with its middle node. Coordinates have 5 decimals (about a metre).
"""
import datetime
import os
import sys


def parse(line):
    """An OPL line as (type, id, {field letter: value})."""
    parts = line.split()
    return parts[0][0], int(parts[0][1:]), {p[0]: p[1:] for p in parts[1:]}


def tags(text):
    return dict(kv.split("=", 1) for kv in text.split(",") if "=" in kv) if text else {}


def main(files):
    cameras, sections = set(), set()
    for name in files:
        nodes, ways, relations = {}, {}, []
        with open(name, encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                kind, ident, fields = parse(line)
                if kind == "n":
                    if "x" not in fields or "y" not in fields or not fields["x"]:
                        continue
                    spot = (round(float(fields["y"]), 5), round(float(fields["x"]), 5))
                    nodes[ident] = spot
                    if tags(fields.get("T")).get("highway") == "speed_camera":
                        cameras.add(spot)
                elif kind == "w":
                    ways[ident] = [int(n[1:]) for n in fields.get("N", "").split(",") if n]
                elif kind == "r":
                    relations.append((tags(fields.get("T")), fields.get("M", "")))

        def spot(member):
            ref, _, _ = member.partition("@")
            kind, ident = ref[0], int(ref[1:])
            if kind == "n":
                return nodes.get(ident)
            if kind == "w" and ways.get(ident):
                way = ways[ident]
                return nodes.get(way[len(way) // 2])
            return None

        for t, members in relations:
            if t.get("type") != "enforcement":
                continue
            roles = {}
            for m in members.split(","):
                if "@" in m:
                    roles.setdefault(m.partition("@")[2], []).append(m)
            if t.get("enforcement") == "maxspeed":
                cameras.update(s for s in map(spot, roles.get("device", [])) if s)
            elif t.get("enforcement") == "average_speed" and roles.get("from") and roles.get("to"):
                start, end = spot(roles["from"][0]), spot(roles["to"][0])
                if start and end:
                    sections.add(start + end)

    sources = " ".join(os.path.splitext(os.path.basename(f))[0] for f in files)
    print(f"# {datetime.date.today().isoformat()} {sources}")
    for lat, lon in sorted(cameras):
        print(f"c,{lat:.5f},{lon:.5f}")
    for s in sorted(sections):
        print("s," + ",".join(f"{v:.5f}" for v in s))


if __name__ == "__main__":
    main(sys.argv[1:])
