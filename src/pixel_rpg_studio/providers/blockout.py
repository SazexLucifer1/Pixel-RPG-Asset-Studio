"""Procedural blockout meshes (OBJ).

Used by the mock/test 3D provider and as an honest placeholder when no 3D AI
model is installed: a simple box-built object shape that still goes through
the full Blender pipeline (cleanup, colour projection, rendering) so the
workflow can be tested end-to-end.
"""

from __future__ import annotations

from pathlib import Path

Box = tuple[tuple[float, float, float], tuple[float, float, float]]  # (min, max)


def _box_faces(offset: int) -> list[tuple[int, int, int, int]]:
    o = offset
    return [
        (o + 1, o + 2, o + 3, o + 4), (o + 5, o + 8, o + 7, o + 6),
        (o + 1, o + 5, o + 6, o + 2), (o + 2, o + 6, o + 7, o + 3),
        (o + 3, o + 7, o + 8, o + 4), (o + 5, o + 1, o + 4, o + 8),
    ]


def _box_vertices(b: Box) -> list[tuple[float, float, float]]:
    (x0, y0, z0), (x1, y1, z1) = b
    return [(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0), (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)]


def object_boxes(kind: str = "generic", height: float = 2.0) -> list[Box]:
    h = height
    if kind in ("weapon", "sword"):
        return [((-0.03 * h, -0.02 * h, 0.25 * h), (0.03 * h, 0.02 * h, h)), ((-0.15 * h, -0.04 * h, 0.2 * h), (0.15 * h, 0.04 * h, 0.25 * h)),
                ((-0.03 * h, -0.03 * h, 0.0), (0.03 * h, 0.03 * h, 0.2 * h))]
    if kind == "building":
        return [((-0.5 * h, -0.4 * h, 0.0), (0.5 * h, 0.4 * h, 0.6 * h)), ((-0.4 * h, -0.3 * h, 0.6 * h), (0.4 * h, 0.3 * h, h))]
    return [((-0.35 * h, -0.35 * h, 0.0), (0.35 * h, 0.35 * h, 0.7 * h)), ((-0.3 * h, -0.3 * h, 0.7 * h), (0.3 * h, 0.3 * h, 0.8 * h))]


def write_obj(boxes: list[Box], path: Path, subdivide: int = 3) -> Path:
    """Write boxes as one OBJ. Each box face is subdivided so skinning bends smoothly."""
    lines = ["# Pixel RPG Asset Studio blockout mesh"]
    vcount = 0
    for b in boxes:
        verts = _box_vertices(b)
        for face in _box_faces(0):
            corners = [verts[i - 1] for i in face]
            grid = []
            n = max(1, subdivide)
            for i in range(n + 1):
                row = []
                for j in range(n + 1):
                    u, v = i / n, j / n
                    p = [
                        (1 - u) * (1 - v) * corners[0][k] + u * (1 - v) * corners[1][k] + u * v * corners[2][k] + (1 - u) * v * corners[3][k]
                        for k in range(3)
                    ]
                    lines.append("v %.5f %.5f %.5f" % tuple(p))
                    vcount += 1
                    row.append(vcount)
                grid.append(row)
            for i in range(n):
                for j in range(n):
                    lines.append(f"f {grid[i][j]} {grid[i + 1][j]} {grid[i + 1][j + 1]} {grid[i][j + 1]}")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
