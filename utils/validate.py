"""
S2 — Autoware LL2 requirement validator.

Grades an exported Lanelet2 `.osm` against the mandatory, machine-checkable
requirements from `20260407 - Autoware LL2 Map Requirements.xlsx`. Each check is
a pure function of the parsed OSM tree and returns a `CheckResult`:

    PASS  — requirement satisfied
    FAIL  — requirement applicable but not met (with offending counts)
    SKIP  — not applicable / source-dependent and no relevant source data present

The check predicates mirror the S1 gap-analysis (docs/roadmap/autoware-ll2/
s1-findings.md). Geometry checks use the `local_x`/`local_y` metric node tags.

Usage:
    from utils.validate import validate_file
    results = validate_file("output/S1/Town04_no_georef.osm")
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from collections import defaultdict

from lxml import etree

PASS, FAIL, SKIP = "PASS", "FAIL", "SKIP"

# Geometry thresholds (vm-01-24, vm-01-05).
MAX_LEN_STRAIGHT_M = 100.0
MAX_LEN_CURVED_M = 20.0
CURVE_ANGLE_DEG = 175.0        # interior angle below this ⇒ the polyline is "curved"
SMOOTH_MIN_ANGLE_DEG = 60.0    # interior angle below this ⇒ a "jagged" kink (vm-01-05)


# --------------------------------------------------------------------------- #
# Result type
# --------------------------------------------------------------------------- #
@dataclass
class CheckResult:
    req_id: str
    name: str
    status: str
    detail: str = ""
    offenders: int = 0
    total: int = 0

    def line(self) -> str:
        frac = f" ({self.offenders}/{self.total})" if self.total else ""
        return f"  {self.req_id:<9} {self.status:<4} {self.name} — {self.detail}{frac}"


# --------------------------------------------------------------------------- #
# Parsed-map facade
# --------------------------------------------------------------------------- #
def _tags(el) -> dict:
    return {t.get("k"): t.get("v") for t in el.findall("tag")}


@dataclass
class Map:
    root: etree._Element
    nodes: dict = field(default_factory=dict)          # id -> (x, y, ele)
    ways: dict = field(default_factory=dict)           # id -> element
    way_tags: dict = field(default_factory=dict)       # id -> tag dict
    way_nodes: dict = field(default_factory=dict)      # id -> [node ids]
    lanelets: list = field(default_factory=list)       # relation elements (type=lanelet)
    regelems: list = field(default_factory=list)       # relation elements (type=regulatory_element)

    @classmethod
    def parse(cls, path: str) -> "Map":
        root = etree.parse(path).getroot()
        m = cls(root=root)
        for n in root.findall("node"):
            t = _tags(n)
            try:
                x = float(t.get("local_x")) if "local_x" in t else float(n.get("lon"))
                y = float(t.get("local_y")) if "local_y" in t else float(n.get("lat"))
            except (TypeError, ValueError):
                x = y = 0.0
            ele = float(t["ele"]) if "ele" in t else None
            m.nodes[n.get("id")] = (x, y, ele)
        for w in root.findall("way"):
            wid = w.get("id")
            m.ways[wid] = w
            m.way_tags[wid] = _tags(w)
            m.way_nodes[wid] = [nd.get("ref") for nd in w.findall("nd")]
        for r in root.findall("relation"):
            rt = _tags(r).get("type")
            if rt == "lanelet":
                m.lanelets.append(r)
            elif rt == "regulatory_element":
                m.regelems.append(r)
        return m

    # -- helpers ---------------------------------------------------------- #
    def way_polyline(self, wid):
        return [self.nodes[n][:2] for n in self.way_nodes.get(wid, []) if n in self.nodes]

    def lanelet_bound_ways(self, ll):
        out = {}
        for mem in ll.findall("member"):
            if mem.get("type") == "way" and mem.get("role") in ("left", "right"):
                out[mem.get("role")] = mem.get("ref")
        return out

    def regelem_subtypes(self):
        return [_tags(r).get("subtype") for r in self.regelems]


# --------------------------------------------------------------------------- #
# Geometry helpers
# --------------------------------------------------------------------------- #
def _polyline_len(pts):
    return sum(math.dist(pts[i], pts[i + 1]) for i in range(len(pts) - 1))


def _min_interior_angle(pts):
    """Smallest interior angle (deg) over the polyline; 180 if <3 points."""
    if len(pts) < 3:
        return 180.0
    worst = 180.0
    for i in range(1, len(pts) - 1):
        a, b, c = pts[i - 1], pts[i], pts[i + 1]
        v1 = (a[0] - b[0], a[1] - b[1])
        v2 = (c[0] - b[0], c[1] - b[1])
        n1, n2 = math.hypot(*v1), math.hypot(*v2)
        if n1 == 0 or n2 == 0:
            continue
        cosang = max(min((v1[0] * v2[0] + v1[1] * v2[1]) / (n1 * n2), 1.0), -1.0)
        worst = min(worst, math.degrees(math.acos(cosang)))
    return worst


# --------------------------------------------------------------------------- #
# Checks  (one per requirement id)
# --------------------------------------------------------------------------- #
def _road_lanelets(m):
    return [ll for ll in m.lanelets if _tags(ll).get("subtype") in ("road", "road_shoulder")]


def check_vm_01_01(m):
    """Road lanelets must have subtype, location, one_way, speed_limit."""
    roads = _road_lanelets(m)
    if not roads:
        return CheckResult("vm-01-01", "Lanelet basics tags", SKIP, "no road lanelets")
    miss_loc = miss_ow = miss_spd = 0
    # speed_limit may be a tag OR a regulatory element referenced by the lanelet
    speed_regelem_ids = {r.get("id") for r in m.regelems if _tags(r).get("subtype") == "speed_limit"}
    for ll in roads:
        t = _tags(ll)
        refs = {mem.get("ref") for mem in ll.findall("member") if mem.get("type") == "relation"}
        if "location" not in t:
            miss_loc += 1
        if "one_way" not in t:
            miss_ow += 1
        if "speed_limit" not in t and not (refs & speed_regelem_ids):
            miss_spd += 1
    worst = max(miss_loc, miss_ow, miss_spd)
    status = PASS if worst == 0 else FAIL
    return CheckResult("vm-01-01", "Lanelet basics tags", status,
                       f"missing location={miss_loc} one_way={miss_ow} speed_limit={miss_spd}",
                       worst, len(roads))


def check_vm_01_02(m):
    """Boundary line ways must carry lane_change."""
    lines = [wid for wid, t in m.way_tags.items()
             if t.get("type") in ("line_thin", "line_thick", "road_border")]
    if not lines:
        return CheckResult("vm-01-02", "Lane-change allowance", SKIP, "no marking line ways")
    miss = sum(1 for wid in lines if "lane_change" not in m.way_tags[wid])
    return CheckResult("vm-01-02", "Lane-change allowance", PASS if miss == 0 else FAIL,
                       f"lines without lane_change", miss, len(lines))


def check_vm_01_03(m):
    """Linestring sharing: boundary ways referenced by >1 lanelet (structural proxy)."""
    refs = defaultdict(int)
    for ll in m.lanelets:
        for wid in m.lanelet_bound_ways(ll).values():
            refs[wid] += 1
    if not refs:
        return CheckResult("vm-01-03", "Linestring sharing", SKIP, "no boundary ways")
    shared = sum(1 for c in refs.values() if c > 1)
    # informational: sharing existing at all is the structural prerequisite
    status = PASS if shared > 0 else FAIL
    return CheckResult("vm-01-03", "Linestring sharing", status,
                       f"boundary ways shared by >1 lanelet", shared, len(refs))


def check_vm_01_05(m):
    """Geometry smoothness: no boundary way has an interior kink below threshold."""
    jagged = 0
    total = 0
    for wid, t in m.way_tags.items():
        if t.get("type") not in ("line_thin", "line_thick", "road_border", "curbstone"):
            continue
        total += 1
        if _min_interior_angle(m.way_polyline(wid)) < SMOOTH_MIN_ANGLE_DEG:
            jagged += 1
    if total == 0:
        return CheckResult("vm-01-05", "Lane geometry smooth", SKIP, "no boundary ways")
    return CheckResult("vm-01-05", "Lane geometry smooth", PASS if jagged == 0 else FAIL,
                       f"ways with a kink <{SMOOTH_MIN_ANGLE_DEG:.0f}°", jagged, total)


def check_vm_01_24(m):
    """Lanelet length: boundary ≤100 m straight / ≤20 m curved."""
    over = 0
    total = 0
    for ll in m.lanelets:
        if _tags(ll).get("subtype") == "crosswalk":
            continue  # crosswalks are short by nature; junction exemption handled in S6
        for wid in m.lanelet_bound_ways(ll).values():
            pts = m.way_polyline(wid)
            if len(pts) < 2:
                continue
            total += 1
            curved = _min_interior_angle(pts) < CURVE_ANGLE_DEG
            limit = MAX_LEN_CURVED_M if curved else MAX_LEN_STRAIGHT_M
            if _polyline_len(pts) > limit:
                over += 1
    if total == 0:
        return CheckResult("vm-01-24", "Lanelet splitting", SKIP, "no lanelet boundaries")
    return CheckResult("vm-01-24", "Lanelet splitting", PASS if over == 0 else FAIL,
                       f"boundaries over length limit", over, total)


def check_vm_03_01(m):
    """intersection_area polygons must exist (if the map has junctions)."""
    areas = [wid for wid, t in m.way_tags.items() if t.get("type") == "intersection_area"]
    if areas:
        return CheckResult("vm-03-01", "Intersection area", PASS,
                           "intersection_area polygons present", 0, len(areas))
    # Heuristic: junction lanelets are the subtype-less ones; if none, skip.
    junctionish = [ll for ll in m.lanelets if "subtype" not in _tags(ll)]
    if not junctionish:
        return CheckResult("vm-03-01", "Intersection area", SKIP, "no junction lanelets detected")
    return CheckResult("vm-03-01", "Intersection area", FAIL,
                       "0 intersection_area polygons despite junction lanelets",
                       len(junctionish), len(m.lanelets))


def check_vm_03_02(m):
    """Junction lanelets must carry turn_direction."""
    junctionish = [ll for ll in m.lanelets if "subtype" not in _tags(ll)]
    if not junctionish:
        return CheckResult("vm-03-02", "Turn direction", SKIP, "no junction lanelets detected")
    miss = sum(1 for ll in junctionish if "turn_direction" not in _tags(ll))
    return CheckResult("vm-03-02", "Turn direction", PASS if miss == 0 else FAIL,
                       "junction lanelets without turn_direction", miss, len(junctionish))


def check_vm_04_01(m):
    """Traffic light basics: traffic_light ways + traffic_light reg-elem + light_bulbs."""
    tl_ways = [wid for wid, t in m.way_tags.items() if t.get("type") == "traffic_light"]
    bulbs = [wid for wid, t in m.way_tags.items() if t.get("type") == "light_bulbs"]
    tl_regelems = [r for r in m.regelems if _tags(r).get("subtype") == "traffic_light"]
    if not tl_ways and not tl_regelems:
        return CheckResult("vm-04-01", "Traffic light basics", SKIP, "no traffic lights in source")
    missing = []
    if not tl_regelems:
        missing.append("traffic_light reg-elem")
    if not bulbs:
        missing.append("light_bulbs")
    status = PASS if not missing else FAIL
    detail = "complete" if not missing else "missing " + ", ".join(missing)
    return CheckResult("vm-04-01", "Traffic light basics", status,
                       f"{len(tl_ways)} TL ways, {len(tl_regelems)} reg-elems; {detail}",
                       len(missing), 3)


def check_vm_05_01(m):
    """Crosswalk basics: crosswalk lanelet + crosswalk_polygon + crosswalk reg-elem."""
    cw_lanelets = [ll for ll in m.lanelets if _tags(ll).get("subtype") == "crosswalk"]
    if not cw_lanelets:
        return CheckResult("vm-05-01", "Crosswalk basics", SKIP, "no crosswalks in source")
    cw_poly = [wid for wid, t in m.way_tags.items() if t.get("type") == "crosswalk_polygon"]
    cw_regelems = [r for r in m.regelems if _tags(r).get("subtype") == "crosswalk"]
    missing = []
    if not cw_poly:
        missing.append("crosswalk_polygon")
    if not cw_regelems:
        missing.append("crosswalk reg-elem")
    status = PASS if not missing else FAIL
    detail = "complete" if not missing else "missing " + ", ".join(missing)
    return CheckResult("vm-05-01", "Crosswalk basics", status,
                       f"{len(cw_lanelets)} crosswalk lanelets; {detail}",
                       len(missing), 3)


def check_vm_07_04(m):
    """Every node must carry an ele tag."""
    if not m.nodes:
        return CheckResult("vm-07-04", "Ellipsoidal height", SKIP, "no nodes")
    miss = sum(1 for (_, _, ele) in m.nodes.values() if ele is None)
    return CheckResult("vm-07-04", "Ellipsoidal height (tag present)", PASS if miss == 0 else FAIL,
                       "nodes without ele tag", miss, len(m.nodes))


CHECKS = [
    check_vm_01_01, check_vm_01_02, check_vm_01_03, check_vm_01_05, check_vm_01_24,
    check_vm_03_01, check_vm_03_02, check_vm_04_01, check_vm_05_01, check_vm_07_04,
]


def validate_map(m: Map) -> list[CheckResult]:
    return [chk(m) for chk in CHECKS]


def validate_file(path: str) -> list[CheckResult]:
    return validate_map(Map.parse(path))
