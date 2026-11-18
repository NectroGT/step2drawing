"""Turn model-selected candidates into drawing specs."""
import json

CIRCLE_VIEW = {"z": "top", "x": "right", "y": "front"}
HV = {"front": {"x": "h", "z": "v"}, "top": {"x": "h", "y": "v"}, "right": {"y": "h", "z": "v"}}
ANGLES = [45, 135, 225, 315, 20, 160, 200, 340, 70, 110, 250, 290]


def p2(view, p):
    return {"front": (p["x"], p["z"]), "top": (p["x"], p["y"]), "right": (p["y"], p["z"])}[view]


def to_specs(sel):
    dims, centers, axes, ang = [], [], [], {}

    def next_angle(v):
        ang[v] = ang.get(v, -1) + 1; return ANGLES[ang[v] % len(ANGLES)]

    lin = []
    for c in sel:
        a = c["anchor"]
        if a["type"] == "lin":
            ax = a["axis"]
            if "hole_axis" in a:
                view = CIRCLE_VIEW[a["hole_axis"]]
            else:
                view = "top" if ax == "y" else "front"
            kind = HV[view][ax]
            side = ("below" if kind == "h" else "right") if "hole_axis" not in a else ("above" if kind == "h" else "left")
            lin.append(dict(view=view, kind=kind, p1=p2(view, a["p1"]), p2=p2(view, a["p2"]), value=c["value"],
                            name=json.dumps(c["key"]), side=side))
        elif a["type"] == "dia":
            ax = a["axis"]
            if a.get("bc"):
                view = CIRCLE_VIEW[ax]
                dims.append(dict(view=view, kind="dia", c=p2(view, a["center"]), r=a["r"], value=c["value"],
                                 name=json.dumps(c["key"]), angle=next_angle(view), text="Ø<> BC"))
                axes.append(dict(view=view, circle=a["r"], center=p2(view, a["center"])))
            elif not c["internal"] and ax == "x":          # shaft convention: Ø as linear dim in the side view
                xm = (a["lo"] + a["hi"]) / 2
                ctr = a["center"]
                lo = dict(ctr, x=xm, z=ctr["z"] - a["r"]); hi = dict(ctr, x=xm, z=ctr["z"] + a["r"])
                dims.append(dict(view="front", kind="v", p1=p2("front", lo), p2=p2("front", hi), value=c["value"],
                                 name=json.dumps(c["key"]), at=xm, text="Ø<>", side="right", tier=1))
            else:
                view = CIRCLE_VIEW[ax]
                n = c["count"]
                body = c["thread"] if (c.get("thread") and c["internal"]) else "Ø<>"
                txt = (f"{n}X " if n > 1 else "") + body + (" THRU" if c["internal"] and c["through"] else "")
                if c.get("csk"):
                    cd, ca = c["csk"]
                    txt += f" CSK Ø{cd:g} X {ca:g}°"
                dims.append(dict(view=view, kind="dia", c=p2(view, a["center"]), r=a["r"], value=c["value"],
                                 name=json.dumps(c["key"]), angle=next_angle(view), text=txt))
        else:  # radius
            view = CIRCLE_VIEW[a["axis"]]
            dims.append(dict(view=view, kind="rad", c=p2(view, a["center"]), r=a["r"], value=c["value"],
                             name=json.dumps(c["key"]), angle=next_angle(view), text="R<>"))
    # stack linear dims in tiers per (view, side): shortest closest to the part
    groups = {}
    for d in lin:
        groups.setdefault((d["view"], d["side"]), []).append(d)
    for g in groups.values():
        for i, d in enumerate(sorted(g, key=lambda d: d["value"])):
            d["tier"] = i + 1; dims.append(d)
    return dims, centers, axes


def hole_centers(F):
    out = []
    for (a, d), gs in F["holes"].items():
        view = CIRCLE_VIEW[a]
        for g in gs:
            o = g["other"]; p = {a: 0, o[0]: g["c"][0], o[1]: g["c"][1]}
            out.append(dict(view=view, c=p2(view, p), r=d / 2))
    return out



def drop_redundant(sel):
    """An overall size that equals the diameter of a round part is the same dimension twice."""
    ext = [c for c in sel if c["kind"] == "dia" and not c["internal"]]
    out = []
    for c in sel:
        if c["kind"] == "lin" and c["srcs"] <= {"overall", "level_gap", "level_pos"} and any(
                abs(c["value"] - e["value"]) < 1e-3 and e["anchor"]["axis"] != c["axis"] for e in ext):
            continue
        out.append(c)
    return out


