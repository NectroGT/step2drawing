"""Read a STEP file with its assembly structure: every solid, with the name of the part it belongs to,
in the file's own length units."""
import re
import cadquery as cq

UNIT_MM = {"inch": 25.4, "foot": 304.8, "mm": 1.0, "cm": 10.0, "m": 1000.0}


def step_units(path):
    """Length unit the STEP file declares, as (name, mm per unit)."""
    txt = open(path, errors="ignore").read()
    m = re.search(r"CONVERSION_BASED_UNIT\s*\(\s*'(INCH|FOOT)'", txt, re.I)
    if m:
        u = m.group(1).lower(); return u, UNIT_MM[u]
    m = re.search(r"SI_UNIT\s*\(\s*\.(MILLI|CENTI)\.\s*,\s*\.METRE\.", txt, re.I)
    if m:
        return ("mm", 1.0) if m.group(1).upper() == "MILLI" else ("cm", 10.0)
    if re.search(r"SI_UNIT\s*\(\s*\$\s*,\s*\.METRE\.", txt, re.I):
        return "m", 1000.0
    return "mm", 1.0


GENERIC = re.compile(r"^(|solid\d*|body\d*|shell\d*|part\d*|=>.*|open cascade.*|unnamed.*|component\d*)$", re.I)


def _good(name):
    return bool(name) and not GENERIC.match(name.strip())


def _name(label):
    from OCP.TDataStd import TDataStd_Name
    from OCP.TCollection import TCollection_AsciiString
    n = TDataStd_Name()
    if label.FindAttribute(TDataStd_Name.GetID_s(), n):
        try:
            return TCollection_AsciiString(n.Get()).ToCString().strip()
        except Exception:
            return ""
    return ""


def read_parts(path, units=None):
    """Return (list of (part_name, cq.Solid), unit_name). Geometry is in the file's own units."""
    from OCP.STEPCAFControl import STEPCAFControl_Reader
    from OCP.TDocStd import TDocStd_Document
    from OCP.TCollection import TCollection_ExtendedString
    from OCP.XCAFDoc import XCAFDoc_DocumentTool, XCAFDoc_ShapeTool
    from OCP.TDF import TDF_LabelSequence, TDF_Label
    from OCP.TopLoc import TopLoc_Location
    from OCP.IFSelect import IFSelect_RetDone

    unit, mm = units or step_units(path)
    out = []
    try:
        doc = TDocStd_Document(TCollection_ExtendedString("XmlOcaf"))
        r = STEPCAFControl_Reader(); r.SetNameMode(True)
        if r.ReadFile(path) != IFSelect_RetDone:
            raise IOError("cannot read STEP")
        r.Transfer(doc)
        st = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())

        def walk(label, loc, best):
            """best = deepest meaningful name seen on the way down (part beats assembly)."""
            ln = _name(label)
            if XCAFDoc_ShapeTool.IsAssembly_s(label):
                if _good(ln) and not re.search(r"(_ASM|_ASSEMBLY)$", ln, re.I):
                    best = ln
                comps = TDF_LabelSequence(); XCAFDoc_ShapeTool.GetComponents_s(label, comps, False)
                for i in range(1, comps.Length() + 1):
                    c = comps.Value(i); ref = TDF_Label()
                    XCAFDoc_ShapeTool.GetReferredShape_s(c, ref)
                    cn = _name(c)
                    walk(ref, loc.Multiplied(XCAFDoc_ShapeTool.GetLocation_s(c)), cn if _good(cn) else best)
            else:
                shp = XCAFDoc_ShapeTool.GetShape_s(label).Moved(loc)
                nm = ln if _good(ln) else best
                for s in cq.Shape.cast(shp).Solids():
                    out.append((nm, s))

        free = TDF_LabelSequence(); st.GetFreeShapes(free)
        for i in range(1, free.Length() + 1):
            walk(free.Value(i), TopLoc_Location(), "")
    except Exception:
        out = []
    if not out:                                   # fall back to plain geometry without names
        s = cq.importers.importStep(path)
        out = [("", x) for v in s.vals() for x in v.Solids()]
    if mm != 1.0:                                 # OCC reads in mm; work in the file's own units
        out = [(n, s.scale(1 / mm)) for n, s in out]
    return out, unit
