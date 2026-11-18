"""Turn extruded (prismatic) parts so their profile is the front view.

A part is prismatic along axis a when most of its side area runs parallel to a (planes whose normal is
perpendicular to a, cylinders whose axis is a) and it has several arcs around a. The front view looks
along y, so such a part is rotated to bring a onto y, keeping the profile upright.
"""
import math

import cadquery as cq
from OCP.BRepAdaptor import BRepAdaptor_Surface

AX = "xyz"


def _axis(v, tol=1e-6):
    for i, a in enumerate(AX):
        if abs(abs(v[i]) - 1) < tol:
            return a
    return None


def prismatic_axis(solid):
    side = {a: 0.0 for a in AX}
    arcs = {a: 0 for a in AX}
    total = 0.0
    for f in solid.Faces():
        A = f.Area(); total += A
        gt = f.geomType()
        if gt == "PLANE":
            n = f.normalAt(f.Center()); n = (n.x, n.y, n.z)
            for i, a in enumerate(AX):
                if abs(n[i]) < 1e-6:
                    side[a] += A
        elif gt == "CYLINDER":
            d = BRepAdaptor_Surface(f.wrapped).Cylinder().Axis().Direction()
            a = _axis((d.X(), d.Y(), d.Z()))
            if a:
                side[a] += A; arcs[a] += 1
    if not total:
        return None
    best = max(AX, key=lambda a: (arcs[a] >= 3) * side[a])
    if arcs[best] >= 3 and side[best] / total >= 0.6:
        return best
    return None


def orient(solid):
    """Return (solid, note). Rotates only when the part is prismatic along x or z."""
    a = prismatic_axis(solid)
    if a in (None, "y"):
        return solid, None
    if a == "z":       # profile in XY -> rotate +90 deg about X: y -> z (up), z -> -y (towards viewer)
        rot = solid.rotate(cq.Vector(0, 0, 0), cq.Vector(1, 0, 0), 90)
    else:              # a == "x": profile in YZ -> rotate about Z: x -> y
        rot = solid.rotate(cq.Vector(0, 0, 0), cq.Vector(0, 0, 1), 90)
    return rot, f"profile view: part turned so its {a.upper()} extrusion faces the front view"
