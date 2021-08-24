from typing import NamedTuple, Sequence, Tuple

import numba as nb
import numpy as np

from .constants import FILL_VALUE, NDIM, FloatArray, FloatDType, IntArray, IntDType
from .utils import allocate_polygon, copy


class Point(NamedTuple):
    x: float
    y: float


class Vector(NamedTuple):
    x: float
    y: float


class Interval(NamedTuple):
    xmin: float
    xmax: float


class Box(NamedTuple):
    xmin: float
    xmax: float
    ymin: float
    ymax: float


@nb.njit(inline="always")
def cross_product(u: Vector, v: Vector) -> float:
    return u.x * v.y - u.y * v.x


@nb.njit(inline="always")
def dot_product(u: Vector, v: Vector) -> float:
    return u.x * v.x + u.y * v.y


@nb.njit(inline="always")
def point_norm(p: Point, v0: Vector, v1: Vector) -> Vector:
    # Use in case the polygon in not guaranteed counter-clockwise.
    n = Vector(-(v1.y - v0.y), (v1.x - v0.x))
    v = Vector(v0.x - p.x, v0.y - p.y)
    dot = dot_product(n, v)
    if dot == 0:
        raise ValueError
    elif dot < 0:
        n = Vector(-n.x, -n.y)
    return n


@nb.njit(inline="always")
def intersection(a: Point, V: Vector, r: Point, N: Vector) -> Tuple[bool, Point]:
    W = Vector(r.x - a.x, r.y - a.y)
    nw = dot_product(N, W)
    nv = dot_product(N, V)
    if nv != 0:
        t = nw / nv
        return True, Point(a.x + t * V.x, a.y + t * V.y)
    else:
        return False, Point(0.0, 0.0)


@nb.njit(inline="always")
def _polygon_area(polygon):
    length = len(polygon)
    area = 0.0
    a = polygon[0]
    b = polygon[1]
    u = Point(b.x - a.x, b.y - a.y)
    for i in range(2, length):
        c = polygon[i]
        v = Point(a.x - c.x, a.y - c.y)
        area += abs(cross_product(u, v))
        b = c
        u = v
    return 0.5 * area


@nb.njit(inline="always")
def polygon_length(face: IntArray) -> int:
    # A minimal polygon is a triangle
    n = face.size
    for i in range(3, n):
        if face[i] == FILL_VALUE:
            return i
    return n


@nb.njit(inline="always")
def polygon_area(polygon: Sequence, length: int) -> float:
    area = 0.0
    a = Point(polygon[0][0], polygon[0][1])
    b = Point(polygon[1][0], polygon[1][1])
    U = Vector(b.x - a.x, b.y - a.y)
    for i in range(2, length):
        c = Point(polygon[i][0], polygon[i][1])
        V = Vector(a.x - c.x, a.y - c.y)
        area += abs(cross_product(U, V))
        b = c
        U = V
    return 0.5 * area


@nb.njit(inline="always")
def point_in_polygon(p: Point, poly: Sequence[Point]) -> bool:
    # Refer to: https://wrf.ecse.rpi.edu/Research/Short_Notes/pnpoly.html
    # Copyright (c) 1970-2003, Wm. Randolph Franklin
    # MIT license.
    #
    # Quote:
    # > I run a semi-infinite ray horizontally (increasing x, fixed y) out from
    # > the test point, and count how many edges it crosses. At each crossing,
    # > the ray switches between inside and outside. This is called the Jordan
    # > curve theorem.
    # >
    # > The case of the ray going thru a vertex is handled correctly via a
    # > careful selection of inequalities. Don't mess with this code unless
    # > you're familiar with the idea of Simulation of Simplicity. This pretends
    # > to shift the ray infinitesimally down so that it either clearly
    # > intersects, or clearly doesn't touch. Since this is merely a conceptual,
    # > infinitesimal, shift, it never creates an intersection that didn't exist
    # > before, and never destroys an intersection that clearly existed before.
    # >
    # > The ray is tested against each edge thus:
    # > 1. Is the point in the half-plane to the left of the extended edge? and
    # > 2. Is the point's Y coordinate within the edge's Y-range?
    # >
    # > Handling endpoints here is tricky.
    #
    # For the Simulation of Simplicity concept, see:
    # Edelsbrunner, H., & Mücke, E. P. (1990). Simulation of simplicity: a
    # technique to cope with degenerate cases in geometric algorithms. ACM
    # Transactions on Graphics (tog), 9(1), 66-104.
    #
    # In this case, this guarantees there will be no "on-edge" answers, which
    # are degenerative. For another application of simulation of simplicity,
    # see:
    # Rappoport, A. (1991). An efficient algorithm for line and polygon
    # clipping. The Visual Computer, 7(1), 19-28.
    length = len(poly)
    c = False
    for i in range(length):
        v0 = poly[i]
        v1 = poly[(i + 1) % length]
        # Do not split this in two conditionals: if the first conditional fails,
        # the second will not be executed in Python's (and C's) execution model.
        # This matters because the second can result in division by zero.
        if (v0.y > p.y) != (v1.y > p.y) and p.x < (
            (v1.x - v0.x) * (p.y - v0.y) / (v1.y - v0.y) + v0.x
        ):
            c = not c
    return c


@nb.njit(inline="always")
def intervals_intersect(a: Sequence[float], b: Sequence[float]) -> bool:
    return a[0] < b[1] and b[0] < a[1]


@nb.njit(inline="always")
def boxes_intersect(a: Sequence[float], b: Sequence[float]) -> bool:
    """
    Parameters
    ----------
    a: (xmin, xmax, ymin, ymax)
    b: (xmin, xmax, ymin, ymax)
    """
    return a[0] < b[1] and b[0] < a[1] and a[2] < b[3] and b[2] < a[3]


@nb.njit(inline="always")
def bounding_box(
    polygon: IntArray, vertices: FloatArray, max_n_verts: int
) -> Tuple[float, float, float, float]:
    first_vertex = vertices[polygon[0]]
    xmin = xmax = first_vertex[0]
    ymin = ymax = first_vertex[1]
    for i in range(1, max_n_verts):
        index = polygon[i]
        if index == FILL_VALUE:
            break
        vertex = vertices[index]
        x = vertex[0]
        y = vertex[1]
        xmin = min(xmin, x)
        xmax = max(xmax, x)
        ymin = min(ymin, y)
        ymax = max(ymax, y)
    return (xmin, xmax, ymin, ymax)


@nb.njit
def build_bboxes(
    faces: IntArray,
    vertices: FloatArray,
) -> Tuple[FloatArray, IntArray]:
    # Make room for the bounding box of every polygon.
    n_polys, max_n_verts = faces.shape
    bbox_coords = np.empty((n_polys, NDIM * 2), FloatDType)

    for i in nb.prange(n_polys):  # pylint: disable=not-an-iterable
        polygon = faces[i]
        bbox_coords[i] = bounding_box(polygon, vertices, max_n_verts)

    return bbox_coords


@nb.njit(inline="always")
def copy_vertices(vertices: FloatArray, face: IntArray) -> Tuple[FloatArray, int]:
    length = polygon_length(face)
    out = allocate_polygon()
    copy(vertices[face], out, length)
    return out, length
