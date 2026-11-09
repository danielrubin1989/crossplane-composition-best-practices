"""usage: states.py deck.pptx out.pptx N [N...] - one slide per click state of the given slides"""
import sys, copy
from pptx import Presentation
P='{http://schemas.openxmlformats.org/presentationml/2006/main}'
src, out, nums = sys.argv[1], sys.argv[2], [int(n) for n in sys.argv[3:]]
prs = Presentation(src)
sl = list(prs.slides)
plan = []  # (slide, hidden set)
for n in nums:
    s = sl[n-1]; t = s._element.find(P+'timing')
    if t is None: plan.append((s, set())); continue
    main = [c for c in t.iter(P+'cTn') if c.get('nodeType')=='mainSeq'][0]
    groups = main.find(P+'childTnLst').findall(P+'par')
    eff = lambda g: [(c.get('presetClass'), {e.get('spid') for e in c.iter(P+'spTgt')}) for c in g.iter(P+'cTn') if c.get('presetClass')]
    hidden = set()
    first_seen = {}
    for g in groups:
        for cls, ids in eff(g):
            for i in ids:
                first_seen.setdefault(i, cls)
    hidden = {i for i, c in first_seen.items() if c == 'entr'}
    plan.append((s, set(hidden)))
    for g in groups:
        for cls, ids in eff(g):
            if cls == 'entr': hidden -= ids
            elif cls == 'exit': hidden |= ids
        plan.append((s, set(hidden)))
# build: keep only planned slides (duplicates allowed)
from pptx.opc.package import _Relationship
from pptx.opc.constants import RELATIONSHIP_TYPE as RT
news = []
for s, hid in plan:
    new = prs.slides.add_slide(s.slide_layout); part = new.part
    part._element = copy.deepcopy(s._element); part.__dict__.pop('slide', None)
    part.rels._rels.clear()
    for rId, rel in s.part.rels.items():
        if rel.reltype == RT.NOTES_SLIDE: continue
        part.rels._rels[rId] = _Relationship(part.partname.baseURI, rId, rel.reltype, rel._target_mode, rel._target)
    for e in list(part._element.iter(P+'sp', P+'grpSp', P+'pic', P+'cxnSp')):
        c = e.find('./*/'+P+'cNvPr')
        if c is not None and c.get('id') in hid and e.getparent() is not None:
            e.getparent().remove(e)
    news.append(part)
lst = prs.slides._sldIdLst
for sid in list(lst):
    if prs.part.related_part(sid.rId) not in news:
        lst.remove(sid)
prs.save(out)
print(len(plan))
