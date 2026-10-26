"""Convert the supplied deployed PADO-SAT STEP model to a bounded ROS mesh.

Build-time tool only. Requires the OCP (Open CASCADE) Python wheel; the browser
loads only the generated JSON. The palette and STEP/body alignment are visual
inferences from the supplied deployed-view reference, not a flight ICD.
"""

from __future__ import annotations

import argparse
import hashlib
import heapq
import json
import math
from pathlib import Path

from OCP.BRep import BRep_Tool
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.Bnd import Bnd_Box
from OCP.BRepBndLib import BRepBndLib
from OCP.STEPControl import STEPControl_Reader
from OCP.TopAbs import TopAbs_FACE
from OCP.TopExp import TopExp_Explorer
from OCP.TopLoc import TopLoc_Location
from OCP.TopoDS import TopoDS


PALETTE = [
    {"name": "bus_aluminum", "color": "#B9C7CF"},
    {"name": "solar_cell", "color": "#151B22"},
    {"name": "panel_trim", "color": "#E5BF58"},
    {"name": "panel_frame", "color": "#58636C"},
    {"name": "antenna", "color": "#AA704D"},
]


def triangle_area(points: tuple[tuple[float, ...], ...]) -> float:
    a, b, c = points
    ab = [b[i] - a[i] for i in range(3)]
    ac = [c[i] - a[i] for i in range(3)]
    cross = (ab[1] * ac[2] - ab[2] * ac[1],
             ab[2] * ac[0] - ab[0] * ac[2],
             ab[0] * ac[1] - ab[1] * ac[0])
    return math.sqrt(sum(value * value for value in cross)) * 0.5


def material(points: tuple[tuple[float, ...], ...]) -> int:
    x, y, z = (sum(p[axis] for p in points) / 3 for axis in range(3))
    a, b, c = points
    ab = [b[i] - a[i] for i in range(3)]
    ac = [c[i] - a[i] for i in range(3)]
    nx = ab[1] * ac[2] - ab[2] * ac[1]
    n = math.sqrt((ab[1] * ac[2] - ab[2] * ac[1]) ** 2 +
                  (ab[2] * ac[0] - ab[0] * ac[2]) ** 2 +
                  (ab[0] * ac[1] - ab[1] * ac[0]) ** 2)
    face_x = abs(nx) / n if n else 0
    if abs(x) > 75 and abs(y) < 80:
        return 4  # Deployed thin appendage/antenna.
    if abs(y) > 62 and x > 20:
        if face_x < 0.58:
            return 3  # Solar wing support and exposed edge.
        return 1
    if abs(y) > 62:
        return 3
    return 0


def checksum(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def convert(source: Path, destination: Path, budget: int) -> dict:
    if not source.is_file() or source.suffix.lower() not in (".step", ".stp"):
        raise ValueError("a regular STEP source file is required")
    if not 1000 <= budget <= 25000:
        raise ValueError("triangle budget must be 1000..25000")
    reader = STEPControl_Reader()
    if not reader.ReadFile(str(source)).name.endswith("RetDone"):
        raise ValueError("STEP import failed")
    reader.TransferRoots()
    shape = reader.OneShape()
    bbox = Bnd_Box()
    BRepBndLib.Add_s(shape, bbox)
    bounds = bbox.Get()
    BRepMesh_IncrementalMesh(shape, 5.0, False, 0.8, True)
    main: list[tuple] = []
    antenna: list[tuple] = []
    face_cursor = TopExp_Explorer(shape, TopAbs_FACE)
    sequence = 0
    while face_cursor.More():
        face = TopoDS.Face_s(face_cursor.Current())
        location = TopLoc_Location()
        triangulation = BRep_Tool.Triangulation_s(face, location)
        if triangulation is not None:
            transform = location.Transformation()
            nodes = [None] + [triangulation.Node(index).Transformed(transform).Coord()
                              for index in range(1, triangulation.NbNodes() + 1)]
            for index in range(1, triangulation.NbTriangles() + 1):
                points = tuple(nodes[node] for node in triangulation.Triangle(index).Get())
                area = triangle_area(points)
                if area < 0.1:
                    continue
                sequence += 1
                entry = (area, sequence, points)
                if len(main) < budget:
                    heapq.heappush(main, entry)
                elif area > main[0][0]:
                    heapq.heapreplace(main, entry)
                if max(abs(point[0]) for point in points) > 75:
                    if len(antenna) < min(700, budget // 8):
                        heapq.heappush(antenna, entry)
                    elif area > antenna[0][0]:
                        heapq.heapreplace(antenna, entry)
        face_cursor.Next()

    selected = {entry[1]: entry for entry in antenna}
    for entry in sorted(main, reverse=True):
        if len(selected) >= budget:
            break
        selected.setdefault(entry[1], entry)
    vertex_index: dict[tuple[float, ...], int] = {}
    vertices: list[list[float]] = []
    triangles: list[list[int]] = []
    counts = [0] * len(PALETTE)
    for _, _, points in sorted(selected.values(), key=lambda item: item[1]):
        ids = []
        for point in points:
            coordinate = tuple(round(value, 1) for value in point)
            if coordinate not in vertex_index:
                vertex_index[coordinate] = len(vertices)
                vertices.append(list(coordinate))
            ids.append(vertex_index[coordinate])
        if len(set(ids)) != 3:
            continue
        group = material(points)
        triangles.append(ids + [group])
        counts[group] += 1
    # The area-limited decimation can omit small triangles inside the broad
    # coplanar solar-cell surfaces. A recessed underlay preserves the continuous
    # panel silhouette while retained STEP triangles supply the visible detail.
    for y_low, y_high in ((-229.0, -66.0), (66.0, 229.0)):
        corners = [(37.0, y_low, -157.0), (37.0, y_high, -157.0),
                   (37.0, y_high, 164.0), (37.0, y_low, 164.0)]
        base = len(vertices)
        vertices.extend([list(corner) for corner in corners])
        triangles.extend(([base, base + 1, base + 2, 1],
                          [base, base + 2, base + 3, 1]))
        counts[1] += 2
        # Thin straight grid/edge marks reconstruct the reference photograph's
        # restrained cell outlines. Their color is illustrative, not AP214 data.
        def trim(y0: float, y1: float, z0: float, z1: float) -> None:
            base = len(vertices)
            vertices.extend([[41.5, y0, z0], [41.5, y1, z0],
                             [41.5, y1, z1], [41.5, y0, z1]])
            triangles.extend(([base, base + 1, base + 2, 2],
                              [base, base + 2, base + 3, 2]))
            counts[2] += 2

        for z in (-155.0, -90.0, 0.0, 90.0, 162.0):
            trim(y_low, y_high, z, z + 1.0)
        for y in (y_low, y_high - 1.0):
            trim(y, y + 1.0, -155.0, 163.0)
    result = {
        "schema": "pado.cad-mesh.v1",
        "source_file": "STCS-CAD-002. Deployed_v1.76.step",
        "source_sha256": checksum(source),
        "source_format": "ISO-10303-21 AP214 / Open CASCADE STEP",
        "units": "mm",
        "vertices_frame": "BODY",
        "center": [0, 0, 0],
        "source_bounds_mm": [round(value, 1) for value in bounds],
        "cad_to_body": {
            "matrix": [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
            "translation_mm": [0, 0, 0],
            "status": "REFERENCE_IMAGE_INFERRED_UNVERIFIED",
            "basis": "STEP X is panel-face normal, Y spans deployed wings, Z is bus long axis. Signs match the supplied reference image provisionally; CAD has no CubeADCS body-frame annotation."
        },
        "palette_status": "RECONSTRUCTED_FROM_REFERENCE_IMAGE",
        "geometry_note": "Two recessed solar-panel underlays fill decimation gaps; thin straight panel marks and palette are reconstructed from the reference image. Remaining visible geometry comes from STEP triangles.",
        "materials": PALETTE,
        "vertices": vertices,
        "triangles": triangles,
    }
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(result, separators=(",", ":")), encoding="utf-8")
    print(f"Wrote {destination}: {len(vertices)} vertices, {len(triangles)} triangles, "
          f"{destination.stat().st_size} bytes, material counts {counts}", flush=True)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--triangle-budget", type=int, default=8000)
    args = parser.parse_args()
    convert(args.source, args.destination, args.triangle_budget)


if __name__ == "__main__":
    main()
