from typing import Sequence, Tuple

import numba as nb
import numpy as np

from .constants import (
    FILL_VALUE,
    NDIM,
    PARALLEL,
    TOLERANCE_ON_EDGE,
    Box,
    FloatArray,
    FloatDType,
    IntArray,
    Point,
    Triangle,
    Vector,
)
from .utils import allocate_box_polygon, allocate_polygon


@nb.njit(inline="always")
def to_vector(a: Point, b: Point) -> Vector:
    return Vector(b.x - a.x, b.y - a.y)


@nb.njit(inline="always")
def as_point(a: FloatArray) -> Point:
    return Point(a[0], a[1])


@nb.njit(inline="always")
def as_box(arr: FloatArray) -> Box:
    return Box(
        arr[0],
        arr[1],
        arr[2],
        arr[3],
    )


@nb.njit(inline="always")
def as_triangle(vertices: FloatArray, face: IntArray) -> Triangle:
    return Triangle(
        as_point(vertices[face[0]]),
        as_point(vertices[face[1]]),
        as_point(vertices[face[2]]),
    )


@nb.njit(inline="always")
def to_point(t: float, a: Point, V: Vector) -> Point:
    return Point(a.x + t * V.x, a.y + t * V.y)


@nb.njit(inline="always")
def cross_product(u: Vector, v: Vector) -> float:
    return u.x * v.y - u.y * v.x


@nb.njit(inline="always")
def dot_product(u: Vector, v: Vector) -> float:
    return u.x * v.x + u.y * v.y


@nb.njit(inline="always")
def polygon_length(face: IntArray) -> int:
    # A minimal polygon is a triangle
    n = len(face)
    for i in range(3, n):
        if face[i] == FILL_VALUE:
            return i
    return n


@nb.njit(inline="always")
def polygon_area(polygon: Sequence) -> float:
    length = len(polygon)
    area = 0.0
    a = Point(polygon[0][0], polygon[0][1])
    b = Point(polygon[1][0], polygon[1][1])
    U = to_vector(a, b)
    for i in range(2, length):
        c = Point(polygon[i][0], polygon[i][1])
        V = to_vector(c, a)
        area += abs(cross_product(U, V))
        b = c
        U = V
    return 0.5 * area


@nb.njit(inline="always")
def point_in_polygon(p: Point, poly: Sequence) -> bool:
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
    v0 = as_point(poly[-1])
    c = False
    for i in range(length):
        v1 = as_point(poly[i])
        # Do not split this in two conditionals: if the first conditional fails,
        # the second will not be executed in Python's (and C's) execution model.
        # This matters because the second can result in division by zero.
        if (v0.y > p.y) != (v1.y > p.y) and p.x < (
            (v1.x - v0.x) * (p.y - v0.y) / (v1.y - v0.y) + v0.x
        ):
            c = not c
        v0 = v1
    return c


@nb.njit(inline="always")
def point_in_polygon_or_on_edge(p: Point, poly: FloatArray) -> bool:
    length = len(poly)
    v0 = as_point(poly[-1])
    U = to_vector(p, v0)
    c = False
    for i in range(length):
        v1 = as_point(poly[i])
        V = to_vector(p, v1)

        twice_area = abs(cross_product(U, V))
        if twice_area < TOLERANCE_ON_EDGE:
            W = to_vector(v0, v1)
            if W.x != 0.0:
                t = (p.x - v0.x) / W.x
            elif W.y != 0.0:
                t = (p.y - v0.y) / W.y
            else:
                continue
            if 0 <= t <= 1:
                return True

        if (v0.y > p.y) != (v1.y > p.y) and p.x < (
            (v1.x - v0.x) * (p.y - v0.y) / (v1.y - v0.y) + v0.x
        ):
            c = not c

        v0 = v1
        U = V
    return c


@nb.njit(inline="always")
def boxes_intersect(a: Box, b: Box) -> bool:
    """
    Parameters
    ----------
    a: (xmin, xmax, ymin, ymax)
    b: (xmin, xmax, ymin, ymax)
    """
    return a.xmin < b.xmax and b.xmin < a.xmax and a.ymin < b.ymax and b.ymin < a.ymax


@nb.njit(inline="always")
def box_contained(a: Box, b: Box) -> bool:
    """
    Whether a is contained by b.

    Parameters
    ----------
    a: (xmin, xmax, ymin, ymax)
    b: (xmin, xmax, ymin, ymax)
    """
    return (
        a.xmin >= b.xmin and a.xmax <= b.xmax and a.ymin >= b.ymin and a.ymax <= b.ymax
    )


@nb.njit(inline="always")
def bounding_box(
    polygon: IntArray, vertices: FloatArray
) -> Tuple[float, float, float, float]:
    max_n_verts = len(polygon)
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


@nb.njit(cache=True)
def build_bboxes(
    faces: IntArray,
    vertices: FloatArray,
) -> Tuple[FloatArray, IntArray]:
    # Make room for the bounding box of every polygon.
    n_polys = len(faces)
    bbox_coords = np.empty((n_polys, NDIM * 2), FloatDType)

    for i in nb.prange(n_polys):  # pylint: disable=not-an-iterable
        polygon = faces[i]
        bbox_coords[i] = bounding_box(polygon, vertices)

    return bbox_coords


@nb.njit(inline="always")
def copy_vertices(vertices: FloatArray, face: IntArray) -> FloatArray:
    length = polygon_length(face)
    out = allocate_polygon()
    for i in range(length):
        v = vertices[face[i]]
        out[i, 0] = v[0]
        out[i, 1] = v[1]
    return out[:length]


@nb.njit(inline="always")
def copy_vertices_into(
    vertices: FloatArray, face: IntArray, out: FloatArray
) -> FloatArray:
    length = polygon_length(face)
    for i in range(length):
        v = vertices[face[i]]
        out[i, 0] = v[0]
        out[i, 1] = v[1]
    return out[:length]


@nb.njit(inline="always")
def copy_box_vertices(box: Box) -> FloatArray:
    a = allocate_box_polygon()
    a[0, 0] = box.xmin
    a[0, 1] = box.ymin
    a[1, 0] = box.xmax
    a[1, 1] = box.ymin
    a[2, 0] = box.xmax
    a[2, 1] = box.ymax
    a[3, 0] = box.xmin
    a[3, 1] = box.ymax
    return a


@nb.njit(inline="always")
def point_inside_box(a: Point, box: Box):
    return box.xmin < a.x and a.x < box.xmax and box.ymin < a.y and a.y < box.ymax


@nb.njit(inline="always")
def flip(face: IntArray, length: int) -> None:
    end = length - 1
    for i in range(int(length / 2)):
        j = end - i
        face[i], face[j] = face[j], face[i]
    return


@nb.njit(parallel=PARALLEL, cache=True)
def counter_clockwise(vertices: FloatArray, faces: IntArray) -> None:
    n_face = len(faces)
    for i_face in nb.prange(n_face):
        face = faces[i_face]
        length = polygon_length(face)
        a = as_point(vertices[face[length - 2]])
        b = as_point(vertices[face[length - 1]])
        for i in range(length):
            c = as_point(vertices[face[i]])
            u = to_vector(a, b)
            v = to_vector(a, c)
            product = cross_product(u, v)
            if product == 0:
                a = b
                b = c
            elif product < 0:
                flip(face, length)
            else:
                break
    return
