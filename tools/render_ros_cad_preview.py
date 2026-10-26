"""Render the ROS CAD appearance with a CPU depth buffer for visual checks.

This developer utility reads the same bounded mesh/appearance assets as the
viewer. It does not edit the supplied PNGs. Pillow and NumPy are required only
for this offline preview, not by the ground-station containers.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np
from PIL import Image


ROOT = Path(__file__).resolve().parents[1]


def quad_homography(quad: np.ndarray) -> np.ndarray:
    rows = []
    values = []
    for (u, v), (x, y) in zip(((0, 0), (1, 0), (1, 1), (0, 1)), quad):
        rows.extend(((u, v, 1, 0, 0, 0, -x * u, -x * v),
                     (0, 0, 0, u, v, 1, -y * u, -y * v)))
        values.extend((x, y))
    return np.r_[np.linalg.solve(np.array(rows, float), values), 1].reshape(3, 3)


def texture_triangles(appearance: dict, centre: np.ndarray, scale: float) -> list:
    triangles = []
    for patch in appearance["patches"]:
        corners = (np.array(patch["corners"], float) - centre) * scale
        normal = np.array(patch["normal"], float)
        nu, nv = patch["subdivisions"]
        homography = quad_homography(np.array(patch["image_quad"], float))

        def vertex(i: int, j: int) -> tuple[np.ndarray, np.ndarray]:
            u, v = i / nu, j / nv
            point = ((1 - u) * (1 - v) * corners[0] + u * (1 - v) * corners[1]
                     + u * v * corners[2] + (1 - u) * v * corners[3])
            image_uv = homography @ np.array((u, v, 1))
            return point, image_uv[:2] / image_uv[2]

        for i in range(nu):
            for j in range(nv):
                quad = [vertex(i, j), vertex(i + 1, j),
                        vertex(i + 1, j + 1), vertex(i, j + 1)]
                for indices in ((0, 1, 2), (0, 2, 3)):
                    triangles.append((np.array([quad[k][0] for k in indices]),
                                      np.array([quad[k][1] for k in indices]),
                                      normal, patch["texture"]))
    return triangles


def render(mesh: dict, appearance: dict, width: int, height: int,
           yaw: float, pitch: float) -> Image.Image:
    raw_vertices = np.array(mesh["vertices"], float)
    low, high = raw_vertices.min(axis=0), raw_vertices.max(axis=0)
    centre = (low + high) / 2
    scale = 2.5 / max(high - low)
    vertices = (raw_vertices - centre) * scale
    images = {}
    for name, route in appearance["textures"].items():
        source = ROOT / "web" / route.removeprefix("/")
        images[name] = np.asarray(Image.open(source).convert("RGB"), dtype=float)
    eye = np.array((math.cos(pitch) * math.cos(yaw),
                    math.cos(pitch) * math.sin(yaw), math.sin(pitch)))
    right = np.cross((0, 0, 1), eye)
    right /= np.linalg.norm(right)
    up = np.cross(eye, right)
    pixels = min(width, height) * 0.30
    colour = np.empty((height, width, 3), dtype=np.uint8)
    colour[:] = (8, 24, 36)
    # Larger 1/(camera distance) means a surface is closer to the camera.
    depth_buffer = np.full((height, width), -np.inf, dtype=float)

    def raster(points: np.ndarray, solid: np.ndarray | None = None,
               source_uv: np.ndarray | None = None, texture: str | None = None) -> None:
        depth = points @ eye
        camera_distance = 4.3 - depth
        reciprocal = 1 / camera_distance
        projected = np.c_[width / 2 + (points @ right) * pixels * 4.3 * reciprocal,
                           height / 2 - (points @ up) * pixels * 4.3 * reciprocal]
        left = max(0, math.floor(projected[:, 0].min()))
        top = max(0, math.floor(projected[:, 1].min()))
        right_edge = min(width, math.ceil(projected[:, 0].max()))
        bottom = min(height, math.ceil(projected[:, 1].max()))
        if left >= right_edge or top >= bottom:
            return
        x0, y0 = projected[0]
        x1, y1 = projected[1]
        x2, y2 = projected[2]
        denominator = (y1 - y2) * (x0 - x2) + (x2 - x1) * (y0 - y2)
        if abs(denominator) < 1e-10:
            return
        yy, xx = np.mgrid[top:bottom, left:right_edge]
        xx = xx + 0.5
        yy = yy + 0.5
        a = ((y1 - y2) * (xx - x2) + (x2 - x1) * (yy - y2)) / denominator
        b = ((y2 - y0) * (xx - x2) + (x0 - x2) * (yy - y2)) / denominator
        c = 1 - a - b
        inverse_distance = a * reciprocal[0] + b * reciprocal[1] + c * reciprocal[2]
        old_depth = depth_buffer[top:bottom, left:right_edge]
        visible = ((a >= -1e-7) & (b >= -1e-7) & (c >= -1e-7)
                   & (inverse_distance > old_depth + 1e-9))
        if not visible.any():
            return
        target = colour[top:bottom, left:right_edge]
        if solid is not None:
            target[visible] = solid
        else:
            assert source_uv is not None and texture is not None
            weights = np.stack((a[visible], b[visible], c[visible]), axis=-1) * reciprocal
            uv = weights @ source_uv / inverse_distance[visible, None]
            image = images[texture]
            tex_height, tex_width = image.shape[:2]
            tx = np.clip(uv[:, 0], 0, 1) * (tex_width - 1)
            ty = np.clip(uv[:, 1], 0, 1) * (tex_height - 1)
            ix, iy = tx.astype(int), ty.astype(int)
            jx, jy = np.minimum(ix + 1, tex_width - 1), np.minimum(iy + 1, tex_height - 1)
            fx, fy = (tx - ix)[:, None], (ty - iy)[:, None]
            sampled = ((1 - fx) * (1 - fy) * image[iy, ix]
                       + fx * (1 - fy) * image[iy, jx]
                       + (1 - fx) * fy * image[jy, ix] + fx * fy * image[jy, jx])
            target[visible] = np.clip(sampled, 0, 255).astype(np.uint8)
        old_depth[visible] = inverse_distance[visible]

    for face in mesh["triangles"]:
        points = vertices[face[:3]]
        normal = np.cross(points[1] - points[0], points[2] - points[0])
        normal /= max(np.linalg.norm(normal), 1e-20)
        shade = 0.55 + 0.38 * abs(normal @ eye)
        rgb = mesh["materials"][face[3]]["color"]
        solid = np.array([min(245, max(24, round(int(rgb[i:i + 2], 16) * shade + 20)))
                          for i in (1, 3, 5)], dtype=np.uint8)
        raster(points, solid=solid)
    for points, uv, normal, texture in texture_triangles(appearance, centre, scale):
        if normal @ eye > 0:
            raster(points, source_uv=uv, texture=texture)
    return Image.fromarray(colour)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=ROOT / "out/ros-cad-texture-depth-preview.png")
    args = parser.parse_args()
    mesh = json.loads((ROOT / "web/assets/pado-sat-deployed-mesh.json").read_text())
    appearance = json.loads((ROOT / "web/assets/pado-sat-appearance.json").read_text())
    front = render(mesh, appearance, 900, 650, -1.0, 0.62)
    rear = render(mesh, appearance, 900, 650, math.pi - 1.0, -0.62)
    preview = Image.new("RGB", (1800, 650))
    preview.paste(front, (0, 0))
    preview.paste(rear, (900, 0))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    preview.save(args.out)
    print(args.out.resolve())


if __name__ == "__main__":
    main()
