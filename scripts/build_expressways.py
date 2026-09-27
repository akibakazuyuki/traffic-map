#!/usr/bin/env python3
"""全国の高速道路・自動車専用道路の線と名前、IC・JCT・SA・PAの位置を
OpenStreetMap（Overpass API）から集めて data/expressways.json に書き出す。
地図 © OpenStreetMap contributors（ODbL）。"""
import json, math, sys, time, urllib.parse, urllib.request

MIRRORS = ["https://overpass-api.de/api/interpreter",
           "https://overpass.kumi.systems/api/interpreter",
           "https://overpass.private.coffee/api/interpreter"]
# (南, 西, 北, 東) 地方ごとに分けて取得する（1回の取得が重くなりすぎないように）
REGIONS = [(41.3, 139.3, 45.6, 146.0), (36.8, 139.0, 41.6, 142.2), (34.8, 138.3, 37.2, 141.0),
           (34.5, 135.9, 38.6, 139.9), (33.4, 134.2, 36.0, 136.9), (32.7, 130.8, 36.0, 134.9),
           (30.9, 129.4, 34.3, 132.1), (24.0, 122.9, 27.2, 131.4)]

def overpass(q):
    last = None
    for attempt in range(6):
        url = MIRRORS[attempt % len(MIRRORS)]
        try:
            req = urllib.request.Request(url, data=urllib.parse.urlencode({"data": q}).encode(),
                                         headers={"User-Agent": "traffic-map-builder/1.0"})
            with urllib.request.urlopen(req, timeout=600) as r:
                return json.load(r)
        except Exception as e:  # 混雑時は別のサーバーで再試行
            last = e
            print("retry", url, e, file=sys.stderr)
            time.sleep(20)
    raise last

def dist_m(a, b):
    k = math.cos(math.radians((a[1] + b[1]) / 2))
    return math.hypot((a[0] - b[0]) * k, a[1] - b[1]) * 111320

def seg_dist(p, a, b):
    k = math.cos(math.radians(p[1]))
    ax, ay, bx, by = (a[0] - p[0]) * k, a[1] - p[1], (b[0] - p[0]) * k, b[1] - p[1]
    dx, dy = bx - ax, by - ay
    L = dx * dx + dy * dy
    t = 0 if L == 0 else max(0, min(1, -(ax * dx + ay * dy) / L))
    return math.hypot(ax + dx * t, ay + dy * t) * 111320

def simplify(pts, tol):
    if len(pts) < 3:
        return pts
    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        i, j = stack.pop()
        best, bi = 0, -1
        for k in range(i + 1, j):
            d = seg_dist(pts[k], pts[i], pts[j])
            if d > best:
                best, bi = d, k
        if best > tol:
            keep[bi] = True
            stack += [(i, bi), (bi, j)]
    return [p for p, f in zip(pts, keep) if f]

def main():
    ways, nodes = {}, {}
    for s, w, n, e in REGIONS:
        bb = f"{s},{w},{n},{e}"
        q = (f'[out:json][timeout:500][maxsize:1073741824];'
             f'(way["highway"="motorway"]({bb});way["highway"="trunk"]["motorroad"="yes"]({bb}););out tags geom;')
        for el in overpass(q).get("elements", []):
            ways[el["id"]] = el
        q = (f'[out:json][timeout:300];(node["highway"="motorway_junction"]({bb});'
             f'nwr["highway"~"^(services|rest_area)$"]({bb}););out tags center;')
        for el in overpass(q).get("elements", []):
            nodes[el["type"] + str(el["id"])] = el
        print("region", bb, len(ways), len(nodes), file=sys.stderr)

    # 同じ道路（名前・路線番号が同じ）で端がつながる区間を1本の線にまとめる
    groups = {}
    for el in ways.values():
        t = el.get("tags", {})
        geom = [(round(g["lon"], 6), round(g["lat"], 6)) for g in el.get("geometry", []) if g]
        if len(geom) < 2:
            continue
        key = (t.get("name", ""), t.get("ref", ""), 0 if t.get("highway") == "motorway" else 1)
        groups.setdefault(key, []).append(geom)
    lines = []
    for key, segs in groups.items():
        by_start = {}
        for i, sg in enumerate(segs):
            by_start.setdefault(sg[0], []).append(i)
        used = [False] * len(segs)
        ends = {}
        for i, sg in enumerate(segs):
            ends.setdefault(sg[-1], []).append(i)
        for i in range(len(segs)):
            if used[i]:
                continue
            used[i] = True
            line = list(segs[i])
            while True:  # 後ろへ伸ばす
                nxt = [j for j in by_start.get(line[-1], []) if not used[j]]
                if not nxt:
                    break
                used[nxt[0]] = True
                line += segs[nxt[0]][1:]
            while True:  # 前へ伸ばす
                prv = [j for j in ends.get(line[0], []) if not used[j]]
                if not prv:
                    break
                used[prv[0]] = True
                line = segs[prv[0]][:-1] + line
            line = simplify(line, 25)
            flat = []
            for x, y in line:
                flat += [round(x, 4), round(y, 4)]
            lines.append({"n": key[0], "r": key[1], "t": key[2], "c": flat})

    pts = []
    for el in nodes.values():
        t = el.get("tags", {})
        name = t.get("name", "")
        if not name:
            continue
        lat = el.get("lat", (el.get("center") or {}).get("lat"))
        lon = el.get("lon", (el.get("center") or {}).get("lon"))
        if lat is None:
            continue
        hw = t.get("highway")
        if hw == "motorway_junction":
            kind = "j" if ("JCT" in name or "ジャンクション" in name) else "i"
        else:
            kind = "s" if hw == "services" else "p"
        pts.append([round(lon, 4), round(lat, 4), name, kind])

    out = {"v": 1, "src": "© OpenStreetMap contributors (ODbL)", "built": time.strftime("%Y-%m-%d"),
           "lines": lines, "pts": pts}
    with open("data/expressways.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    print("lines", len(lines), "points", sum(len(l["c"]) // 2 for l in lines), "pts", len(pts), file=sys.stderr)

if __name__ == "__main__":
    main()
