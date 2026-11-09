"""Merge challenges + solutions into per-challenge phases. usage: build.py in.pptx out.pptx"""
import copy, re, sys
from lxml import etree
from pptx import Presentation
from pptx.opc.package import _Relationship
from pptx.opc.constants import RELATIONSHIP_TYPE as RT

NS = {'p': 'http://schemas.openxmlformats.org/presentationml/2006/main',
      'a': 'http://schemas.openxmlformats.org/drawingml/2006/main',
      'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'}
P = '{%s}' % NS['p']

prs = Presentation(sys.argv[1])
orig = list(prs.slides)
S = lambda n: orig[n - 1]          # original 1-based slide number


def dup(src):
    new = prs.slides.add_slide(src.slide_layout)
    part = new.part
    part._element = copy.deepcopy(src._element)
    part.__dict__.pop('slide', None)
    part.rels._rels.clear()
    for rId, rel in src.part.rels.items():
        if rel.reltype == RT.NOTES_SLIDE:
            continue
        part.rels._rels[rId] = _Relationship(
            part.partname.baseURI, rId, rel.reltype, rel._target_mode, rel._target)
    return part.slide


def tree(slide):
    return slide._element.find('.//p:cSld/p:spTree', NS)


def by_name(slide, name, top=True):
    path = './*' if top else './/*'
    return [e for e in etree._Element.xpath(tree(slide), path, namespaces=NS)
            if e.find('./*/p:cNvPr', NS) is not None and e.find('./*/p:cNvPr', NS).get('name') == name]


def one(slide, name, top=True):
    r = by_name(slide, name, top)
    assert len(r) == 1, (name, len(r))
    return r[0]


def spid(el):
    return el.find('./*/p:cNvPr', NS).get('id')


def para_texts(el, texts):
    """Set paragraph texts of the (first) text body in el: one run per paragraph."""
    body = el.find('.//p:txBody', NS)
    paras = body.findall('a:p', NS)
    for p, t in zip(paras, texts):
        runs = p.findall('a:r', NS)
        runs[0].find('a:t', NS).text = t
        for r in runs[1:]:
            p.remove(r)


def last_run(el, text):
    el.findall('.//a:r', NS)[-1].find('a:t', NS).text = text


def remove_shapes(slide, names):
    """Delete shapes and any timing that targets them."""
    for nm in names:
        for el in by_name(slide, nm):
            ids = {e.get('id') for e in el.iter(P + 'cNvPr')}
            el.getparent().remove(el)
            if slide._element.find('p:timing', NS) is not None:
                strip_effects(slide, ids)
                cleanup_timing(slide)


def strip_timing_for(slide, ids):
    t = slide._element.find('p:timing', NS)
    if t is None:
        return
    for tgt in etree._Element.xpath(t, './/p:spTgt', namespaces=NS):
        if tgt.get('spid') in ids:
            par = tgt
            while par is not None and not (par.tag == P + 'par' and
                                           par.getparent().tag == P + 'childTnLst'):
                par = par.getparent()
            if par is not None:
                par.getparent().remove(par)
    for b in etree._Element.xpath(t, './/p:bldP', namespaces=NS):
        if b.get('spid') in ids:
            b.getparent().remove(b)



def strip_effects(slide, ids, cls=None):
    """Remove effect pars targeting ids (optionally only presetClass=cls)."""
    t = slide._element.find('p:timing', NS)
    for ctn in list(t.iter(P + 'cTn')):
        if ctn.get('presetClass') is None or (cls and ctn.get('presetClass') != cls):
            continue
        if {e.get('spid') for e in ctn.iter(P + 'spTgt')} & ids:
            par = ctn.getparent()
            par.getparent().remove(par)


def cleanup_timing(slide):
    """Drop emptied pars, re-mark each click group's first effect, prune unused bldP."""
    t = slide._element.find('p:timing', NS)
    changed = True
    while changed:
        changed = False
        for par in list(t.iter(P + 'par')):
            ctn = par.find('p:cTn', NS)
            if ctn.get('presetClass') is None:
                kids = ctn.find('p:childTnLst', NS)
                if kids is None or len(kids) == 0:
                    par.getparent().remove(par)
                    changed = True
    main = [c for c in t.iter(P + 'cTn') if c.get('nodeType') == 'mainSeq'][0]
    for grp in main.find('p:childTnLst', NS).findall('p:par', NS):
        effects = [c for c in grp.iter(P + 'cTn') if c.get('presetClass')]
        if effects[0].get('nodeType') != 'clickEffect':
            effects[0].set('nodeType', 'clickEffect')
    used = {e.get('spid') for e in t.iter(P + 'spTgt')}
    for b in list(t.iter(P + 'bldP')):
        if b.get('spid') not in used:
            b.getparent().remove(b)


def drop_timing(slide):
    t = slide._element.find('p:timing', NS)
    if t is not None:
        slide._element.remove(t)


def transitions(slide):
    return etree._Element.xpath(slide._element, './/p:transition', namespaces=NS)


def manual_advance(slide):
    for tr in transitions(slide):
        tr.attrib.pop('advClick', None)
        tr.attrib.pop('advTm', None)


# ---------------------------------------------------------------- challenges
CH = [  # new numbering 1..4
    ('Fetch data from another resource',
     "The Policy needs the Bucket's ARN – a value that lives on another resource."),
    ('Wrong order',
     'All MRs are applied at once – the attachment fails until Role and Policy exist.'),
    ('Never Ready',
     'Nothing marks the composed resources Ready, so the XR stays not Ready.'),
    ('Hidden AWS errors',
     'A failed update keeps the XR Ready – ArgoCD says Healthy.'),
]


def set_pill(slide, old, new, retext=False):
    """Rename the !!Challenge <old> pill on a slide to <new> (number + optionally text)."""
    g = one(slide, f'!!Challenge {old}')
    g.find('./*/p:cNvPr', NS).set('name', f'!!Challenge {new}')
    num = one(slide, f'!!Challenge {old} num')
    num.find('./*/p:cNvPr', NS).set('name', f'!!Challenge {new} num')
    last_run(num, str(new))
    num.findall('.//p:sp', NS)[1].find('./p:nvSpPr/p:cNvPr', NS).set('name', str(new))
    hl = one(slide, f'!!Challenge {old} highlight')
    hl.find('./*/p:cNvPr', NS).set('name', f'!!Challenge {new} highlight')
    if retext:
        txt = [e for e in g.iter(P + 'sp') if e.find('.//a:t', NS) is not None][0]
        para_texts(txt, CH[new - 1])
        # Morph also matches shapes inside groups by name: the old name ('Wrong order…') would
        # pull the pill from that row of the list instead of this one.
        txt.find('./p:nvSpPr/p:cNvPr', NS).set('name', CH[new - 1][0] + '…')


# New challenge-1 pill slides, copied from the Wrong-order ones before renumbering.
c1_center, c1_top, c1_top_out, c1_center_out = (dup(S(n)) for n in (30, 31, 33, 34))
for s in (c1_center, c1_top, c1_top_out, c1_center_out):
    set_pill(s, 1, 1, retext=True)

# Renumber the existing challenge chains 1..4 -> 2..5.
# Values (old 2) is dropped; Never Ready / Hidden keep 3 / 4.
for old, nums in ((1, range(30, 35)),):
    for n in nums:
        set_pill(S(n), old, old + 1)

# ---------------------------------------------------------------- hubs
# Hubs use the 'Meet MyApp' layout: Argo frame left, challenge list where the pipeline was.
H_ROW_Y0, H_PITCH, H_ROW_H = 1389888, 700000, 603505
ROW_X, ROW_W = 5943600, 2971800
TXT_H, NUM_H = 252992, 190197
hub_tpl = S(29)
ROW_TPL = copy.deepcopy(one(hub_tpl, 'Challenge 1'))
NUM_TPL = copy.deepcopy(one(hub_tpl, 'Challenge 1 num'))
HL_TPL = copy.deepcopy(one(hub_tpl, '!!Challenge 1 highlight'))
LABEL_TPL = copy.deepcopy(one(hub_tpl, 'challenges label'))
HUB_BASE = S(55)                      # Meet MyApp + pipeline slide
HUB_BASE_SHAPES = ['Google Shape;528;p61', 'Google Shape;529;p61', '!!MyApp Argo frame']


def next_id(slide):
    return max(int(e.get('id')) for e in slide._element.iter(P + 'cNvPr')) + 1


def make_row(slide, k, name_prefix):
    y = H_ROW_Y0 + (k - 1) * H_PITCH
    nid = next_id(slide)
    row = copy.deepcopy(ROW_TPL)
    num = copy.deepcopy(NUM_TPL)
    for el in (row, num):
        for c in el.iter(P + 'cNvPr'):
            c.set('id', str(nid)); nid += 1
    row.find('./p:nvGrpSpPr/p:cNvPr', NS).set('name', f'{name_prefix}Challenge {k}')
    num.find('./p:nvGrpSpPr/p:cNvPr', NS).set('name', f'{name_prefix}Challenge {k} num')
    gx = row.find('./p:grpSpPr/a:xfrm', NS)
    gx.find('a:off', NS).set('y', str(y))
    gx.find('a:off', NS).set('x', str(ROW_X))
    for k2 in ('a:ext', 'a:chExt'):
        gx.find(k2, NS).set('cy', str(H_ROW_H)); gx.find(k2, NS).set('cx', str(ROW_W))
    rect, txt = row.findall('p:sp', NS)
    rect.find('.//a:ext', NS).set('cy', str(H_ROW_H))
    rect.find('.//a:ext', NS).set('cx', str(ROW_W))
    txt.find('.//a:ext', NS).set('cx', str(ROW_W - 337561 - 67562))
    txt.find('.//a:off', NS).set('y', str((H_ROW_H - TXT_H) // 2))
    txt.find('./p:nvSpPr/p:cNvPr', NS).set('name', CH[k - 1][0] + '…')
    para_texts(txt, CH[k - 1])
    num.find('./p:grpSpPr/a:xfrm/a:off', NS).set('y', str(y + (H_ROW_H - NUM_H) // 2))
    num.find('./p:grpSpPr/a:xfrm/a:off', NS).set('x', str(ROW_X + 79553))
    last_run(num, str(k))
    num.findall('.//p:sp', NS)[1].find('./p:nvSpPr/p:cNvPr', NS).set('name', str(k))
    if not name_prefix:
        # Static rows look the same but are freeform shapes, not roundRect/ellipse: Keynote then
        # cannot pick one of them as the Magic Move partner of the !! pill (it ignores names and
        # takes the nearest look-alike, which was row 2).
        as_freeform(rect, round_rect_path(ROW_W, H_ROW_H, round(0.12 * min(ROW_W, H_ROW_H))))
        circ = num.findall('p:sp', NS)[0]
        d = int(xfrm(circ).find('a:ext', NS).get('cx'))
        as_freeform(circ, round_rect_path(d, d, d // 2))
    tree(slide).append(row)
    tree(slide).append(num)
    return row, num


def round_rect_path(w, h, r):
    return (f'<a:path w="{w}" h="{h}"><a:moveTo><a:pt x="{r}" y="0"/></a:moveTo>'
            f'<a:lnTo><a:pt x="{w - r}" y="0"/></a:lnTo>'
            f'<a:arcTo wR="{r}" hR="{r}" stAng="16200000" swAng="5400000"/>'
            f'<a:lnTo><a:pt x="{w}" y="{h - r}"/></a:lnTo>'
            f'<a:arcTo wR="{r}" hR="{r}" stAng="0" swAng="5400000"/>'
            f'<a:lnTo><a:pt x="{r}" y="{h}"/></a:lnTo>'
            f'<a:arcTo wR="{r}" hR="{r}" stAng="5400000" swAng="5400000"/>'
            f'<a:lnTo><a:pt x="0" y="{r}"/></a:lnTo>'
            f'<a:arcTo wR="{r}" hR="{r}" stAng="10800000" swAng="5400000"/>'
            f'<a:close/></a:path>')


def as_freeform(sp, path):
    spPr = sp.find('p:spPr', NS)
    geom = etree.fromstring(
        f'<a:custGeom xmlns:a="{NS["a"]}"><a:avLst/><a:gdLst/><a:ahLst/><a:cxnLst/>'
        f'<a:rect l="0" t="0" r="r" b="b"/><a:pathLst>{path}</a:pathLst></a:custGeom>')
    spPr.replace(spPr.find('a:prstGeom', NS), geom)


def make_hl(slide, k):
    hl = copy.deepcopy(HL_TPL)
    hl.find('./p:nvSpPr/p:cNvPr', NS).set('id', str(next_id(slide)))
    hl.find('./p:nvSpPr/p:cNvPr', NS).set('name', f'!!Challenge {k} highlight')
    off = hl.find('.//a:off', NS)
    off.set('y', str(H_ROW_Y0 + (k - 1) * H_PITCH - 54864))
    off.set('x', str(ROW_X - 54864))
    hl.find('.//a:ext', NS).set('cy', str(H_ROW_H + 109728))
    hl.find('.//a:ext', NS).set('cx', str(ROW_W + 109728))
    tree(slide).append(hl)
    return hl


SET = ('<p:par><p:cTn id="{id}" presetClass="{cls}" nodeType="{node}" presetID="1" grpId="{g}" '
       'fill="hold" presetSubtype="0"><p:stCondLst><p:cond delay="0"/></p:stCondLst>'
       '<p:childTnLst><p:set><p:cBhvr><p:cTn id="{id2}" fill="hold" dur="1"/><p:tgtEl>'
       '<p:spTgt spid="{sp}"/></p:tgtEl><p:attrNameLst><p:attrName>style.visibility</p:attrName>'
       '</p:attrNameLst></p:cBhvr><p:to><p:strVal val="{vis}"/></p:to></p:set></p:childTnLst>'
       '</p:cTn></p:par>')
FADE = ('<p:par><p:cTn id="{id}" presetClass="{cls}" nodeType="withEffect" presetID="10" '
        'grpId="{g}" fill="hold"><p:stCondLst><p:cond delay="0"/></p:stCondLst><p:childTnLst>'
        '{body}</p:childTnLst></p:cTn></p:par>')
FADE_IN = ('<p:set><p:cBhvr><p:cTn id="{id2}" fill="hold"/><p:tgtEl><p:spTgt spid="{sp}"/>'
           '</p:tgtEl><p:attrNameLst><p:attrName>style.visibility</p:attrName></p:attrNameLst>'
           '</p:cBhvr><p:to><p:strVal val="visible"/></p:to></p:set><p:animEffect filter="fade" '
           'transition="in"><p:cBhvr><p:cTn id="{id3}" dur="150"/><p:tgtEl><p:spTgt spid="{sp}"/>'
           '</p:tgtEl></p:cBhvr></p:animEffect>')
FADE_OUT = ('<p:animEffect filter="fade" transition="out"><p:cBhvr><p:cTn id="{id2}" dur="150" '
            'fill="hold"/><p:tgtEl><p:spTgt spid="{sp}"/></p:tgtEl></p:cBhvr></p:animEffect>'
            '<p:set><p:cBhvr><p:cTn id="{id3}" fill="hold"><p:stCondLst><p:cond delay="149"/>'
            '</p:stCondLst></p:cTn><p:tgtEl><p:spTgt spid="{sp}"/></p:tgtEl><p:attrNameLst>'
            '<p:attrName>style.visibility</p:attrName></p:attrNameLst></p:cBhvr><p:to>'
            '<p:strVal val="hidden"/></p:to></p:set>')
TIMING = ('<p:timing xmlns:p="{p}"><p:tnLst><p:par><p:cTn id="1" nodeType="tmRoot" restart="never" '
          'dur="indefinite" fill="hold"><p:childTnLst><p:seq concurrent="1" prevAc="none" '
          'nextAc="seek"><p:cTn id="2" nodeType="mainSeq" dur="indefinite" fill="hold">'
          '<p:childTnLst><p:par><p:cTn id="3" fill="hold"><p:stCondLst><p:cond delay="indefinite"/>'
          '</p:stCondLst><p:childTnLst><p:par><p:cTn id="4" fill="hold"><p:stCondLst>'
          '<p:cond delay="0"/></p:stCondLst><p:childTnLst>{pars}</p:childTnLst></p:cTn></p:par>'
          '</p:childTnLst></p:cTn></p:par></p:childTnLst></p:cTn><p:prevCondLst>'
          '<p:cond evt="onPrev"><p:tgtEl><p:sldTgt/></p:tgtEl></p:cond></p:prevCondLst>'
          '<p:nextCondLst><p:cond evt="onNext"><p:tgtEl><p:sldTgt/></p:tgtEl></p:cond>'
          '</p:nextCondLst></p:seq></p:childTnLst></p:cTn></p:par></p:tnLst><p:bldLst>{blds}'
          '</p:bldLst></p:timing>')
BLD = '<p:bldP build="whole" bldLvl="1" animBg="1" rev="0" advAuto="0" spid="{sp}" grpId="{g}"/>'


# ---------------------------------------------------------------- challenge 1 + render (A, B, C)
# A: Argo frame, XR creating Bucket + Policy; click draws "needs the bucket ARN".
# B: morph - frame shrinks to the left, render step on the right with its 3 balloons.
# C: morph - render window moves up, the rendered policy document builds below it,
#    zooms into the ARN lookup, and on the last click the ARN resolves and both go Ready.
A_NAME = lambda i, n: f'!!f{i:02d} {n}'
MORPH = ('<mc:AlternateContent xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006">'
         '<mc:Choice xmlns:p159="http://schemas.microsoft.com/office/powerpoint/2015/09/main" '
         'Requires="p159"><p:transition xmlns:p="{p}" xmlns:p14="http://schemas.microsoft.com/office/'
         'powerpoint/2010/main" spd="slow" p14:dur="1200"><p159:morph option="byObject"/>'
         '</p:transition></mc:Choice><mc:Fallback><p:transition xmlns:p="{p}" spd="slow"><p:fade/>'
         '</p:transition></mc:Fallback></mc:AlternateContent>').format(p=NS['p'])


def set_morph(slide):
    el = slide._element
    for old in el.findall('{http://schemas.openxmlformats.org/markup-compatibility/2006}'
                          'AlternateContent') + el.findall('p:transition', NS):
        el.remove(old)
    anchor = el.find('p:timing', NS)
    if anchor is None:
        anchor = el.find('p:extLst', NS)
    new = etree.fromstring(MORPH)
    anchor.addprevious(new) if anchor is not None else el.append(new)


def xfrm(el):
    return el.find('./p:spPr/a:xfrm', NS) if el.tag != P + 'grpSp' else \
        el.find('./p:grpSpPr/a:xfrm', NS)


def transform(el, s, src, dst):
    """Scale el by s about src (x, y) and place that point at dst; scale fonts and lines too."""
    x = xfrm(el)
    off, ext = x.find('a:off', NS), x.find('a:ext', NS)
    off.set('x', str(round(dst[0] + (int(off.get('x')) - src[0]) * s)))
    off.set('y', str(round(dst[1] + (int(off.get('y')) - src[1]) * s)))
    ext.set('cx', str(round(int(ext.get('cx')) * s)))
    ext.set('cy', str(round(int(ext.get('cy')) * s)))
    if s == 1:
        return
    A = '{%s}' % NS['a']
    for e in el.iter():
        if not isinstance(e.tag, str) or not e.tag.startswith(A):
            continue
        if 'sz' in e.attrib and e.tag != A + 'spcPts':
            e.set('sz', str(max(100, round(int(e.get('sz')) * s))))
        if e.tag == A + 'ln' and 'w' in e.attrib:
            e.set('w', str(max(3175, round(int(e.get('w')) * s))))
        if e.tag == A + 'bodyPr':
            for k in ('lIns', 'tIns', 'rIns', 'bIns'):
                if k in e.attrib:
                    e.set(k, str(round(int(e.get(k)) * s)))


R_ATTRS = ['{%s}embed' % NS['r'], '{%s}link' % NS['r'], '{%s}id' % NS['r']]


def copy_into(src_slide, dst_slide, el, first=False):
    """Deep-copy el from src_slide into dst_slide, re-linking images."""
    el = copy.deepcopy(el)
    for e in el.iter():
        for a in R_ATTRS:
            if a in e.attrib:
                rel = src_slide.part.rels[e.get(a)]
                e.set(a, dst_slide.part.relate_to(rel._target, rel.reltype))
    t = tree(dst_slide)
    if first:
        t.find('p:grpSpPr', NS).addnext(el)
    else:
        t.append(el)
    return el


def rename(el, name):
    el.find('./*/p:cNvPr', NS).set('name', name)


def name_of(el):
    return el.find('./*/p:cNvPr', NS).get('name')


# ---- timing helpers: effects are <p:par> elements holding one preset cTn
def fx_fade(cls, sp, dur=300):
    if cls == 'entr':
        body = FADE_IN.replace('dur="150"', f'dur="{dur}"')
    else:
        body = FADE_OUT.replace('dur="150"', f'dur="{dur}"').replace('delay="149"',
                                                                       f'delay="{dur - 1}"')
    xml = FADE.format(id=0, cls=cls, g=0, body=body.format(id2=0, id3=0, sp=sp))
    return etree.fromstring(f'<x xmlns:p="{NS["p"]}">{xml}</x>')[0]


def fx_wipe_down(sp, dur=600):
    xml = (f'<p:par xmlns:p="{NS["p"]}"><p:cTn id="0" presetClass="entr" nodeType="clickEffect" '
           f'presetID="22" presetSubtype="1" grpId="0" fill="hold"><p:stCondLst><p:cond delay="0"/>'
           f'</p:stCondLst><p:childTnLst><p:set><p:cBhvr><p:cTn id="0" dur="1" fill="hold">'
           f'<p:stCondLst><p:cond delay="0"/></p:stCondLst></p:cTn><p:tgtEl><p:spTgt spid="{sp}"/>'
           f'</p:tgtEl><p:attrNameLst><p:attrName>style.visibility</p:attrName></p:attrNameLst>'
           f'</p:cBhvr><p:to><p:strVal val="visible"/></p:to></p:set><p:animEffect transition="in" '
           f'filter="wipe(down)"><p:cBhvr><p:cTn id="0" dur="{dur}"/><p:tgtEl><p:spTgt spid="{sp}"/>'
           f'</p:tgtEl></p:cBhvr></p:animEffect></p:childTnLst></p:cTn></p:par>')
    return etree.fromstring(xml)


def fx_from(slide, sp, cls):
    """Copy the effect par(s) that animate shape sp with presetClass cls on slide."""
    t = slide._element.find('p:timing', NS)
    out = []
    for ctn in t.iter(P + 'cTn'):
        if ctn.get('presetClass') == cls and \
                sp in {e.get('spid') for e in ctn.iter(P + 'spTgt')}:
            out.append(copy.deepcopy(ctn.getparent()))
    assert out, (sp, cls)
    return out


def fx_dur(par):
    durs = [int(c.get('dur')) for c in par.iter(P + 'cTn') if (c.get('dur') or '').isdigit()]
    delays = [int(c.get('delay')) for c in par.iter(P + 'cond') if (c.get('delay') or '').isdigit()]
    return max(durs + [0]) + max(delays + [0])


def set_timing(slide, clicks, auto_first=False):
    """clicks: [[step, step...], ...]; step = [effect par, ...]; steps run one after another.
    auto_first: the first group starts by itself after the transition (no click)."""
    drop_timing(slide)
    groups = []
    for ci, click in enumerate(clicks):
        auto = auto_first and ci == 0
        steps, delay = [], 0
        for k, step in enumerate(click):
            for j, par in enumerate(step):
                par.find('p:cTn', NS).set('nodeType', ('afterEffect' if auto else 'clickEffect')
                                          if k == j == 0 else
                                          'afterEffect' if j == 0 else 'withEffect')
            inner = ''.join(etree.tostring(p).decode() for p in step)
            steps.append(f'<p:par><p:cTn id="0" fill="hold"><p:stCondLst><p:cond delay="{delay}"/>'
                         f'</p:stCondLst><p:childTnLst>{inner}</p:childTnLst></p:cTn></p:par>')
            delay += max(fx_dur(p) for p in step)
        on_begin = '<p:cond evt="onBegin"><p:tn val="2"/></p:cond>' if auto else ''
        groups.append('<p:par><p:cTn id="0" fill="hold"><p:stCondLst><p:cond delay="indefinite"/>'
                      + on_begin +
                      '</p:stCondLst><p:childTnLst>' + ''.join(steps) + '</p:childTnLst></p:cTn>'
                      '</p:par>')
    xml = TIMING.format(p=NS['p'], pars='', blds='')
    xml = xml.replace('<p:par><p:cTn id="4" fill="hold"><p:stCondLst><p:cond delay="0"/>'
                      '</p:stCondLst><p:childTnLst></p:childTnLst></p:cTn></p:par>', '')
    t = etree.fromstring(xml)
    main = [c for c in t.iter(P + 'cTn') if c.get('nodeType') == 'mainSeq'][0]
    lst = main.find('p:childTnLst', NS)
    for c in list(lst):
        lst.remove(c)
    for g in groups:
        lst.append(etree.fromstring(f'<x xmlns:p="{NS["p"]}">{g}</x>')[0])
    for n, c in enumerate(t.iter(P + 'cTn'), 1):
        c.set('id', str(n))
    bld = t.find('p:bldLst', NS)
    for g, c in enumerate((c for c in t.iter(P + 'cTn') if c.get('presetClass')), 1):
        c.set('grpId', str(g))
        for sp in sorted({e.get('spid') for e in c.iter(P + 'spTgt')}):
            bld.append(etree.fromstring(BLD.format(sp=sp, g=g).replace(
                '<p:bldP ', f'<p:bldP xmlns:p="{NS["p"]}" ')))
    ext = slide._element.find('p:extLst', NS)
    ext.addprevious(t) if ext is not None else slide._element.append(t)


def set_notes(slide, text):
    slide.notes_slide.notes_text_frame.text = text


src38 = S(38)                       # the old "Values" problem slide (Argo frame, policy doc)

# ---- A
slA = dup(src38)
drop_timing(slA)
set_pill(slA, 2, 1, retext=True)
for nm in ['Policy window body', 'Policy window title bar', 'Policy window button 1',
           'Policy window button 2', 'Policy window button 3', 'Policy window title',
           'Policy code', 'Policy code B', 'render highlight', 'render zoom',
           'why synced false (box + pointer)', 'CreateBucket', 'CreateBucket label',
           'ARN label', 'Bucket -> AWS', 'AWS Bucket', 'AWS Bucket label',
           'AWS resources', 'AWS area label', 'Bucket ready', 'Policy ready',
           'Policy status true', 'Policy status false', 'checkout health Healthy']:
    for el in by_name(slA, nm):
        tree(slA).remove(el)
xfrm(one(slA, 'AWS Account')).find('a:ext', NS).set('cx', '6680000')
para_texts(one(slA, 'Policy node label'), ['Policy', 'for the app'])
# Policy blinks "creating" like the bucket.
bucket_gif = one(slA, 'bucket creating (blinking gif)')
pol_gif = one(slA, 'policy waiting (blinking gif)')
rename(pol_gif, 'policy creating (blinking gif)')
pol_gif.find('.//a:blip', NS).set('{%s}embed' % NS['r'],
                                  bucket_gif.find('.//a:blip', NS).get('{%s}embed' % NS['r']))
# The ARN arrow (Bucket -> Policy) and its label, re-using the old ARN-back styling.
arrow = one(slA, 'ARN back')
rename(arrow, 'bucket arn arrow')
ax = xfrm(arrow)
ax.attrib.pop('flipH'); ax.attrib.pop('flipV')
ax.find('a:off', NS).set('x', '3287268'); ax.find('a:off', NS).set('y', '3293553')
ax.find('a:ext', NS).set('cx', '0'); ax.find('a:ext', NS).set('cy', '417199')
label = copy.deepcopy(one(src38, 'ARN label'))
tree(slA).append(label)
rename(label, 'bucket arn label')
nid = next_id(slA)
for c in label.iter(P + 'cNvPr'):
    c.set('id', str(nid)); nid += 1
last_run(label, 'needs the bucket ARN')
lx = xfrm(label)
lx.find('a:off', NS).set('x', '3357268'); lx.find('a:off', NS).set('y', '3438144')
# Frame = everything except the pill; give each shape a unique morph name.
frame = [el for el in tree(slA) if el.find('./*/p:cNvPr', NS) is not None
         and not name_of(el).startswith('!!Challenge')]
FRAME_SRC = []
for i, el in enumerate(frame):
    rename(el, A_NAME(i, name_of(el)))
    FRAME_SRC.append(copy.deepcopy(el))        # 38 coordinates, for B / C
    transform(el, 1, (0, 0), (982000, 0))       # centre the frame (AWS panel removed)
set_timing(slA, [[[fx_wipe_down(spid(arrow))], [fx_fade('entr', spid(label))]]])
manual_advance(slA)
set_notes(slA, (
    "MyApp composes two resources: an S3 Bucket and an IAM Policy for the app. Both are being "
    "created right now.\n"
    "[click] The Policy grants access to that bucket – so it needs the bucket's ARN. That value "
    "is not in the XR spec; it lives on another resource, and AWS only returns it after create.\n"
    "So: how does one composed resource read data from another?"))

# ---- B: the render step (slide 58, in place)
slB = S(58)
FS, FDST = 0.59, (250000, 1188719)           # frame scale / top-left on B and C
CODE_DX = 4320000 - 274320                   # render window to the right column
win = ['Render step window body', 'Render step window title bar', 'Render step window button 1',
       'Render step window button 2', 'Render step window button 3', 'Render step window title',
       'Render step code']
marks = ['gotemplate highlight', 'gotemplate zoom', 'delims highlight', 'delims zoom',
         'observed highlight', 'observed zoom']
balloons = ['gotemplate explained (box + pointer)', 'delims explained (box + pointer)',
            'observed explained (box + pointer)']
para_texts(one(slB, 'Render step window title'), ['function-go-templating'])
strip_effects(slB, {spid(one(slB, n)) for n in win})      # window is just there (morphs in)
cleanup_timing(slB)
for n in win + marks:
    transform(one(slB, n), 1, (0, 0), (CODE_DX, 0))
BAL_X, BAL_Y = 250000, 3600000
for n in balloons:
    g = one(slB, n)
    box, ptr = g.findall('./p:grpSp', NS)[0], g.findall('./p:sp', NS)[0]
    bx = xfrm(box)
    bx0, by0 = int(bx.find('a:off', NS).get('x')), int(bx.find('a:off', NS).get('y'))
    bw, bh = int(bx.find('a:ext', NS).get('cx')), int(bx.find('a:ext', NS).get('cy'))
    px = xfrm(ptr)                                       # pointer -> right edge, pointing right
    px.set('flipH', '1')
    px.find('a:off', NS).set('x', str(bx0 + bw - 36576))
    px.find('a:off', NS).set('y', str(by0 + bh // 2 - 73152))
    gx = xfrm(g)
    gx.find('a:chOff', NS).set('x', str(bx0)); gx.find('a:chOff', NS).set('y', str(by0))
    w = bw - 36576 + 237744
    for k in ('a:ext', 'a:chExt'):
        gx.find(k, NS).set('cx', str(w)); gx.find(k, NS).set('cy', str(bh))
    gx.find('a:off', NS).set('x', str(BAL_X)); gx.find('a:off', NS).set('y', str(BAL_Y))
def frame_place(el):
    """38 coords -> small frame on B/C/D; arrow labels keep their full-size font."""
    if name_of(el).endswith('arn label'):
        off = xfrm(el).find('a:off', NS)
        off.set('x', str(round(FDST[0] + (int(off.get('x')) - 250000) * FS)))
        off.set('y', str(round(FDST[1] + (int(off.get('y')) - 1200000) * FS)))
    else:
        transform(el, FS, (250000, 1200000), FDST)


for el in reversed(FRAME_SRC):
    frame_place(copy_into(slA, slB, el, first=True))
for nm in ['!!Step 1', '!!Step 1 num', '!!Step 1 highlight']:
    for el in by_name(slB, nm):
        tree(slB).remove(el)
for nm in ['!!Challenge 1', '!!Challenge 1 num', '!!Challenge 1 highlight']:
    copy_into(slA, slB, one(slA, nm))
set_morph(slB)
set_notes(slB, (
    "The answer is the render step: function-go-templating.\n"
    "[click] kind: GoTemplate – the function's input is a Go template of plain Kubernetes YAML, "
    "inline in the Composition. It renders one document per composed resource.\n"
    "[click] delims [[ ]] – the Composition ships in a Helm chart, so we swap {{ }} for [[ ]] "
    "and Helm leaves the template alone.\n"
    "[click] .observed – on every reconcile the function gets the XR (.observed.composite) and "
    "every composed resource with its live status (.observed.resources). That is how one "
    "resource reads another."))

# ---- C: rendered policy document
slC = dup(slB)
drop_timing(slC)
for n in marks + balloons:
    for el in by_name(slC, n):
        tree(slC).remove(el)
CS, CDST = 0.85, (4320000, 320000)
for n in win:
    transform(one(slC, n), CS, (4320000, 1188719), CDST)
doc = ['Policy window body', 'Policy window title bar', 'Policy window button 1',
       'Policy window button 2', 'Policy window button 3', 'Policy window title',
       'Policy code', 'render highlight', 'render zoom']
DS, DSRC, DDST = 700 * CS / 500, (4206240, 3246120), (4320000, 3260000)  # code font = render's
PITCH, CH_W = 96041, 39187                 # code line pitch / monospace char width (38 units)
ARN_TAIL = '.resource.status.atProvider.arn ]]"'


def add_run(line, src_run, text):
    r_ = copy.deepcopy(src_run)
    r_.find('a:t', NS).text = text
    line.findall('.//a:r', NS)[-1].addnext(r_)


def renumber(line, n):
    t = line.findall('.//a:r', NS)[0].find('a:t', NS)
    t.text = f'{n:2d}  '


def fix_doc(el):
    """38's two-line ARN lookup -> one line (38 coordinates, before scaling)."""
    nm = name_of(el)
    if nm == 'Policy code':
        ln = {name_of(c): c for c in el if c.find('./*/p:cNvPr', NS) is not None}
        l5 = ln['Policy code line 5']
        runs = l5.findall('.//a:r', NS)
        runs[1].find('a:t', NS).text = '  '
        add_run(l5, runs[2], ARN_TAIL)
        xfrm(l5).find('a:ext', NS).set('cx', str(CH_W * len(''.join(
            r_.find('a:t', NS).text for r_ in l5.findall('.//a:r', NS)))))
        el.remove(ln['Policy code line 6'])
        for n, k in (('Policy code line 7', 6), ('Policy code line 8', 7)):
            o = xfrm(ln[n]).find('a:off', NS)
            o.set('y', str(int(o.get('y')) - PITCH))
            renumber(ln[n], k)
    elif nm == 'Policy window body':
        e = xfrm(el).find('a:ext', NS)
        e.set('cy', str(int(e.get('cy')) - PITCH))
    elif nm == 'render highlight':
        x = xfrm(el)
        x.find('a:off', NS).set('x', str(4629912 - 2 * CH_W))    # line 5 lost 2 spaces
        x.find('a:off', NS).set('y', '3884980')
        x.find('a:ext', NS).set('cy', '117000')
        x.find('a:ext', NS).set('cx', str(CH_W * len('"[[ (index $.observed.resources "S3Bucket")'
                                                      + ARN_TAIL) + 40000))
    elif nm == 'render zoom':
        z1 = one_in(el, 'render zoom code line 1')
        add_run(z1, z1.findall('.//a:r', NS)[0], ARN_TAIL)
        z2 = one_in(el, 'render zoom code line 2')
        z2.getparent().remove(z2)
        w = CH_W * len('"[[ (index $.observed.resources "S3Bucket")' + ARN_TAIL) + 40000
        x = xfrm(el)
        x.find('a:off', NS).set('x', str(4629912 - 2 * CH_W))
        x.find('a:ext', NS).set('cx', str(w)); x.find('a:chExt', NS).set('cx', str(w))
        zc = xfrm(one_in(el, 'render zoom code'))
        for k in ('a:ext', 'a:chExt'):
            zc.find(k, NS).set('cx', str(w - 40000))
        xfrm(z1).find('a:ext', NS).set('cx', str(w - 40000))
        for k in ('a:ext', 'a:chExt'):
            x.find(k, NS).set('cy', '132479')
        card = one_in(el, 'render zoom card')
        xfrm(card).find('a:ext', NS).set('cx', str(w))
        xfrm(card).find('a:ext', NS).set('cy', '117000')


def one_in(el, name):
    r_ = [e for e in el.iter(P + 'sp', P + 'grpSp')
          if e.find('./*/p:cNvPr', NS).get('name') == name]
    assert len(r_) == 1, name
    return r_[0]


for n in doc:
    el = copy_into(src38, slC, one(src38, n))
    fix_doc(el)
    transform(el, DS, DSRC, DDST)
# Both windows get the same width - wide enough for the one-line lookup; same title font.
DOC_W = 4150000
for n in ('Policy window body', 'Policy window title bar', 'Render step window body',
          'Render step window title bar'):
    xfrm(one(slC, n)).find('a:ext', NS).set('cx', str(DOC_W))
tt = one(slC, 'Policy window title')
for e in tt.iter('{%s}defRPr' % NS['a'], '{%s}rPr' % NS['a']):
    if 'sz' in e.attrib:
        e.set('sz', str(round(800 * CS)))


def center_title(slide, name, x0):
    tx = xfrm(one(slide, name))
    tx.find('a:off', NS).set('x', str(x0 + (DOC_W - int(tx.find('a:ext', NS).get('cx'))) // 2))


center_title(slC, 'Policy window title', DDST[0])
center_title(slC, 'Render step window title', CDST[0])
C_NOTE = " 7  # the Bucket's ARN, from its observed status"
last_run(one(slC, 'Policy code line 8', top=False), C_NOTE)
l8 = one(slC, 'Policy code line 8', top=False)          # keep the box hugging the new text
xfrm(l8).find('a:ext', NS).set('cx', str(CH_W * len(C_NOTE)))
para_texts(one(slC, 'Policy window title'), ['observe in-composition resource'])
ready = []
for n in ['Bucket ready', 'Policy ready', 'checkout health Healthy']:
    el = copy_into(src38, slC, one(src38, n))
    rename(el, '!!f-ready ' + n)
    transform(el, FS, (250000, 1200000), FDST)
    ready.append(el)
shapes_C = [el for el in tree(slC) if el.find('./*/p:cNvPr', NS) is not None]
gif_b = [el for el in shapes_C if name_of(el).endswith('bucket creating (blinking gif)')][0]
gif_p = [el for el in shapes_C if name_of(el).endswith('policy creating (blinking gif)')][0]
win_build = [fx_from(src38, spid(one(slC, n)), 'entr')[0] for n in doc[:7]]
hl, zm = (spid(one(slC, n)) for n in ('render highlight', 'render zoom'))
set_timing(slC, [
    # 1: the policy document builds in (no click: starts after the morph)
    [win_build[:2], *[[p] for p in win_build[2:]]],
    # 2: zoom into the ARN lookup (38's highlight + zoom-in)
    [fx_from(src38, hl, 'entr'), fx_from(src38, zm, 'entr') + fx_from(src38, zm, 'emph')],
    # 3: next reconcile - the lookup resolves, both resources Ready (template stays on screen)
    [fx_from(src38, hl, 'exit') + fx_from(src38, zm, 'exit'),
     [fx_fade('exit', spid(gif_b)), fx_fade('exit', spid(gif_p))] +
     [fx_fade('entr', spid(el)) for el in ready]],
], auto_first=True)                                  # 1 runs right after the morph
set_morph(slC)
set_notes(slC, (
    "(builds in automatically) This is the Policy in the template – observing a resource in our own "
    "composition.\n"
    "[click] The Resource line is the lookup: index .observed.resources \"S3Bucket\", then "
    ".resource.status.atProvider.arn – the ARN AWS returned for the Bucket.\n"
    "[click] On the first reconcile the bucket does not exist yet, so the lookup is empty. On "
    "the next one the ARN is in observed state, the Policy renders, and both resources go Ready.\n"
    "And if the resource is not ours at all – a shared bucket another team owns? That's observe."))


# ---- D: in-composition result moves under the frame; the out-of-composition Object comes in
slD = dup(slC)
drop_timing(slD)
for n in win + ['render highlight', 'render zoom']:
    for el in by_name(slD, n):
        tree(slD).remove(el)
for el in [el for el in tree(slD) if el.find('./*/p:cNvPr', NS) is not None
           and 'creating (blinking gif)' in name_of(el)]:
    tree(slD).remove(el)                          # C ended with both resources Ready
D_DOC = (250000, 3530000)
for n in doc:
    for el in by_name(slD, n):
        transform(el, 1, DDST, D_DOC)
para_texts(one(slD, 'Policy window title'), ['composition.yaml - policy resource'])
center_title(slD, 'Policy window title', D_DOC[0])
# Make room for the Object's lookup (lines 6-7): push "] }" and the comment down two lines.
pc = one(slD, 'Policy code')
pcx = xfrm(pc)
RATIO = int(pcx.find('a:ext', NS).get('cy')) / int(pcx.find('a:chExt', NS).get('cy'))
for n, k in (('Policy code line 7', 8), ('Policy code line 8', 9)):
    c = one_in(pc, n)
    o = xfrm(c).find('a:off', NS)
    o.set('y', str(int(o.get('y')) + 2 * PITCH))
    renumber(c, k)
D_NOTE = ' 9  # both ARNs from .observed.resources'
last_run(one_in(pc, 'Policy code line 8'), D_NOTE)
xfrm(one_in(pc, 'Policy code line 8')).find('a:ext', NS).set('cx', str(CH_W * len(D_NOTE)))
for k in ('a:ext', 'a:chExt'):
    e = pcx.find(k, NS)
    e.set('cy', str(int(e.get('cy')) + round(2 * PITCH * (RATIO if k == 'a:ext' else 1))))
body = xfrm(one(slD, 'Policy window body')).find('a:ext', NS)
body.set('cy', str(int(body.get('cy')) + round(2 * PITCH * RATIO)))
l5 = one_in(pc, 'Policy code line 5')
last_run(l5, ARN_TAIL + ',')
# Lines 6-7 are top-level so they can fade in with the Object.
gx, gy = int(pcx.find('a:off', NS).get('x')), int(pcx.find('a:off', NS).get('y'))
cox, coy = int(pcx.find('a:chOff', NS).get('x')), int(pcx.find('a:chOff', NS).get('y'))
obj_lines = []
for k, texts in ((6, [' 6  ', '  ', '"[[ (index $.observed.resources "', 'shared-logs")',
                      '.resource.status']),
                 (7, [' 7  ', '      ', '.atProvider.manifest.status.atProvider.arn ]]"'])):
    ln = copy.deepcopy(l5)
    rename(ln, f'Policy code line {k} (object)')
    ln.find('./*/p:cNvPr', NS).set('id', str(next_id(slD)))
    runs = ln.findall('.//a:r', NS)
    if k == 7:
        runs = [runs[0], runs[1], runs[2]]
        for extra in ln.findall('.//a:r', NS)[3:]:
            extra.getparent().remove(extra)
    for r_, t in zip(runs, texts):
        r_.find('a:t', NS).text = t
    x = xfrm(ln)
    cy0 = int(x.find('a:off', NS).get('y')) + (k - 5) * PITCH
    x.find('a:off', NS).set('x', str(round(gx + (int(x.find('a:off', NS).get('x')) - cox) * RATIO)))
    x.find('a:off', NS).set('y', str(round(gy + (cy0 - coy) * RATIO)))
    x.find('a:ext', NS).set('cx', str(round(CH_W * len(''.join(texts)) * RATIO)))
    x.find('a:ext', NS).set('cy', str(round(int(x.find('a:ext', NS).get('cy')) * RATIO)))
    tree(slD).append(ln)
    obj_lines.append(ln)
src64 = S(64)
obj_win = ['Object window body', 'Object window title bar', 'Object window button 1',
           'Object window button 2', 'Object window button 3', 'Object window title',
           'Object code']
obj_marks = ['managementPolicies highlight', 'managementPolicies zoom', 'manifest highlight',
             'manifest zoom']
OS, OSRC, ODST = 1.1, (274320, 1188719), (4480000, 1188719)
for n in obj_win + obj_marks:
    transform(copy_into(src64, slD, one(src64, n)), OS, OSRC, ODST)
para_texts(one(slD, 'Object window title'), ['observe out-composition resource'])
OB_X, OB_Y = 4480000, 3950000                     # balloons under the window, pointing up
for n in ['managementPolicies explained (box + pointer)', 'manifest explained (box + pointer)']:
    g = copy_into(src64, slD, one(src64, n))
    box, ptr = g.findall('./p:grpSp', NS)[0], g.findall('./p:sp', NS)[0]
    bx = xfrm(box)
    bw, bh = int(bx.find('a:ext', NS).get('cx')), int(bx.find('a:ext', NS).get('cy'))
    bx.find('a:off', NS).set('x', str(OB_X)); bx.find('a:off', NS).set('y', str(OB_Y))
    px = xfrm(ptr)
    px.set('rot', '5400000')
    cx, cy = OB_X + 600000, OB_Y + 36576 - 237744 // 2
    px.find('a:off', NS).set('x', str(cx - 237744 // 2))
    px.find('a:off', NS).set('y', str(cy - 146304 // 2))
    top = cy - 237744 // 2
    gx = xfrm(g)
    for k in ('a:off', 'a:chOff'):
        gx.find(k, NS).set('x', str(OB_X)); gx.find(k, NS).set('y', str(top))
    for k in ('a:ext', 'a:chExt'):
        gx.find(k, NS).set('cx', str(bw)); gx.find(k, NS).set('cy', str(OB_Y + bh - top))
ids = lambda n: spid(one(slD, n))
mp = [fx_from(src64, ids(n), 'entr') for n in ('managementPolicies highlight',
                                                'managementPolicies zoom',
                                                'managementPolicies explained (box + pointer)')]
mf = [fx_from(src64, ids(n), 'entr') for n in ('manifest highlight', 'manifest zoom',
                                                'manifest explained (box + pointer)')]
mpx = [fx_from(src64, ids(n), 'exit') for n in ('managementPolicies highlight',
                                                 'managementPolicies zoom',
                                                 'managementPolicies explained (box + pointer)')]
# The shared bucket joins the Argo tree as an Object above the Bucket, with an ARN arrow to
# the Policy. Rows are laid out in 38 coordinates, then mapped onto the small frame.
fsrc = lambda n: [e for e in FRAME_SRC if name_of(e).split(' ', 1)[1] == n][0]
OBJ_Y, BKT_Y, POL_Y, ROW_H = 2850000, 3400000, 4150000, 365761
XR_PT, MR_X, MR_R = (1874520, 3447287), 2432304, 2432304 + 1709929


def set_line(el, p1, p2):
    x = xfrm(el)
    for k in ('flipH', 'flipV'):
        x.attrib.pop(k, None)
    if p2[1] < p1[1]:
        x.set('flipV', '1')
    x.find('a:off', NS).set('x', str(min(p1[0], p2[0])))
    x.find('a:off', NS).set('y', str(min(p1[1], p2[1])))
    x.find('a:ext', NS).set('cx', str(abs(p2[0] - p1[0])))
    x.find('a:ext', NS).set('cy', str(abs(p2[1] - p1[1])))


def replace_frame_shape(name, mutate):
    """Re-place one frame shape on D from its 38-coordinate source."""
    new = copy.deepcopy(fsrc(name))
    mutate(new)
    old = [e for e in tree(slD) if e.find('./*/p:cNvPr', NS) is not None
           and name_of(e) == name_of(new)][0]
    tree(slD).replace(old, new)
    frame_place(new)


def shift(dy):
    return lambda el: transform(el, 1, (0, 0), (0, dy))


for n in ('Bucket MR frame', 'Bucket node', 'Bucket node label'):
    replace_frame_shape(n, shift(BKT_Y - 2907792))
for n in ('Policy MR frame', 'Policy node', 'Policy node label'):
    replace_frame_shape(n, shift(POL_Y - 3730752))
for n, y in (('Bucket ready', BKT_Y), ('Policy ready', POL_Y)):
    transform(one(slD, '!!f-ready ' + n), 1, (0, 0),
              (0, round((y - (2907792 if n[0] == 'B' else 3730752)) * FS)))
replace_frame_shape('xr -> bucket', lambda e: set_line(e, XR_PT, (MR_X, BKT_Y + ROW_H // 2)))
replace_frame_shape('xr -> policy', lambda e: set_line(e, XR_PT, (MR_X, POL_Y + ROW_H // 2)))
replace_frame_shape('bucket arn arrow',
                    lambda e: set_line(e, (3287268, BKT_Y + ROW_H + 20000), (3287268, POL_Y - 20000)))
replace_frame_shape('bucket arn label', lambda e: transform(e, 1, (0, 0), (0, (
    BKT_Y + ROW_H + POL_Y) // 2 - 64008 - 3438144)))

obj_shapes = []
for n in ('Bucket MR frame', 'Bucket node', 'Bucket node label'):
    obj_shapes.append(copy.deepcopy(fsrc(n)))
    shift(OBJ_Y - 2907792)(obj_shapes[-1])
arr = copy.deepcopy(fsrc('xr -> policy'))
set_line(arr, XR_PT, (MR_X, OBJ_Y + ROW_H // 2))
obj_shapes.insert(0, arr)
rdy = copy.deepcopy(one(src38, 'Bucket ready'))
shift(OBJ_Y - 2907792)(rdy)
obj_shapes.append(rdy)
# Object -> Policy: out to the right of both rows, down, back in to the Policy (arrowhead).
oarr = copy.deepcopy(fsrc('bucket arn arrow'))
OA_X, OA_W, OA_H = MR_R, 1100000, POL_Y - OBJ_Y   # clear of the bucket ARN label
geom = etree.fromstring(
    f'<a:custGeom xmlns:a="{NS["a"]}"><a:avLst/><a:gdLst/><a:ahLst/><a:cxnLst/>'
    f'<a:rect l="0" t="0" r="r" b="b"/><a:pathLst><a:path w="{OA_W}" h="{OA_H}" fill="none">'
    f'<a:moveTo><a:pt x="0" y="0"/></a:moveTo><a:lnTo><a:pt x="{OA_W}" y="0"/></a:lnTo>'
    f'<a:lnTo><a:pt x="{OA_W}" y="{OA_H}"/></a:lnTo><a:lnTo><a:pt x="0" y="{OA_H}"/></a:lnTo>'
    f'</a:path></a:pathLst></a:custGeom>')
spPr = oarr.find('p:spPr', NS)
spPr.replace(spPr.find('a:prstGeom', NS), geom)
set_line(oarr, (OA_X, OBJ_Y + ROW_H // 2), (OA_X + OA_W, POL_Y + ROW_H // 2))
obj_shapes.append(oarr)
for el, n in zip(obj_shapes, ['xr -> object', 'Object MR frame', 'Object node',
                              'Object node label', 'Object ready', 'object arn arrow']):
    rename(el, '!!fobj ' + n)
    nid = next_id(slD)
    for c in el.iter(P + 'cNvPr'):
        c.set('id', str(nid)); nid += 1
    tree(slD).append(el)
    frame_place(el)
node = obj_shapes[2]
node.find('.//a:solidFill/a:srgbClr', NS).set('val', '326CE5')
last_run(node, 'OBJ')
para_texts(obj_shapes[3], ['Object', 'shared bucket'])
set_timing(slD, [
    # 1: the Object window ("observe out-composition resource") fades in - no click,
    #    right after the morph
    [[fx_fade('entr', ids(n)) for n in obj_win] +
     [fx_fade('entr', spid(el)) for el in obj_shapes + obj_lines]],
    # 2: managementPolicies: ["Observe"]
    [mp[0] + mp[1], mp[2]],
    # 3: forProvider.manifest - the shared bucket it watches
    [mpx[0] + mpx[1] + mpx[2], mf[0] + mf[1], mf[2]],
], auto_first=True)
set_morph(slD)
manual_advance(slD)
set_notes(slD, (
    "That covers resources we compose ourselves – the bottom-left box. But what if the bucket "
    "is not ours: a shared bucket another team owns, or something Terraform still manages?\n"
    "(appears automatically) We wrap it in a provider-kubernetes Object – an out-of-composition resource. It shows "
    "up in Argo as one more child of MyApp – the Object that represents the shared bucket – and the "
    "Policy gets a second lookup: the Object's status.atProvider.manifest.status.atProvider.arn.\n"
    "[click] managementPolicies: [\"Observe\"] – Crossplane only GETs it. It never creates, "
    "updates or deletes it.\n"
    "[click] forProvider.manifest is the thing to watch. Its whole state lands in "
    "status.atProvider.manifest, so go-templating reads the ARN from .observed.resources – "
    "exactly like our own bucket."))

# ---- E: Wrong order -> sequencer. 42's last click morphs straight into this slide: the Argo
# frame (in its final state) shrinks to the left, the function-sequencer window comes in on the
# right. The old sequence pill slides and the reconcile #1/#2/#3 cards are gone.
src32, src76 = S(32), S(76)


def final_hidden(slide):
    """Shape ids that are hidden once all of the slide's clicks have played."""
    t = slide._element.find('p:timing', NS)
    first, last = {}, {}
    for c in t.iter(P + 'cTn'):
        cls = c.get('presetClass')
        if cls in ('entr', 'exit'):
            for e in c.iter(P + 'spTgt'):
                first.setdefault(e.get('spid'), cls)
                last[e.get('spid')] = cls
    return {i for i, c in last.items() if c == 'exit'}


wframe = [el for el in tree(src32) if el.find('./*/p:cNvPr', NS) is not None
          and not name_of(el).startswith('!!Challenge')]
for i, el in enumerate(wframe):
    rename(el, f'!!w{i:02d} {name_of(el)}')
gone = final_hidden(src32)
xs, ys, xe, ye = [], [], [], []
for el in wframe:
    x = xfrm(el)
    ox, oy = int(x.find('a:off', NS).get('x')), int(x.find('a:off', NS).get('y'))
    xs.append(ox); ys.append(oy)
    xe.append(ox + int(x.find('a:ext', NS).get('cx'))); ye.append(oy + int(x.find('a:ext', NS).get('cy')))
WB = (min(xs), min(ys))
WS = 3950000 / (max(xe) - min(xs))
slE = dup(src76)
seq_win = ['Sequencer window body', 'Sequencer window title bar', 'Sequencer window button 1',
           'Sequencer window button 2', 'Sequencer window button 3', 'Sequencer window title',
           'Sequencer code']
seq_marks = ['sequence highlight', 'sequence zoom', 'observe first highlight', 'observe first zoom']
seq_term = ['events terminal body', 'events terminal header', 'events terminal text']
seq_bal = ['sequence explained (box + pointer)', 'observe first explained (box + pointer)']
drop_timing(slE)
for el in list(tree(slE)):
    if el.find('./*/p:cNvPr', NS) is not None and (
            name_of(el).startswith('reconcile #') or name_of(el).startswith('#')
            or name_of(el).startswith('!!Step 4')):
        tree(slE).remove(el)
para_texts(one(slE, 'Sequencer window title'), ['function-sequencer'])
SEQ_DX = 4550000 - 274320
for n in seq_win + seq_marks + seq_term:
    transform(one(slE, n), 1, (0, 0), (SEQ_DX, 0))
EBAL_Y = 3150000
for n in seq_bal:                                    # balloons under the frame, pointing right
    g = one(slE, n)
    box, ptr = g.findall('./p:grpSp', NS)[0], g.findall('./p:sp', NS)[0]
    bx = xfrm(box)
    bx0, by0 = int(bx.find('a:off', NS).get('x')), int(bx.find('a:off', NS).get('y'))
    bw, bh = int(bx.find('a:ext', NS).get('cx')), int(bx.find('a:ext', NS).get('cy'))
    px = xfrm(ptr)
    px.set('flipH', '1')
    px.find('a:off', NS).set('x', str(bx0 + bw - 36576))
    px.find('a:off', NS).set('y', str(by0 + bh // 2 - 73152))
    gx = xfrm(g)
    gx.find('a:chOff', NS).set('x', str(bx0)); gx.find('a:chOff', NS).set('y', str(by0))
    w = bw - 36576 + 237744
    for k in ('a:ext', 'a:chExt'):
        gx.find(k, NS).set('cx', str(w)); gx.find(k, NS).set('cy', str(bh))
    gx.find('a:off', NS).set('x', '250000'); gx.find('a:off', NS).set('y', str(EBAL_Y))
for el in reversed(wframe):
    if spid(el) in gone:
        continue
    transform(copy_into(src32, slE, el, first=True), WS, WB, (250000, 1188719))
# the Wrong-order pill rides along (same top-left position)
for nm in ['!!Challenge 2', '!!Challenge 2 num', '!!Challenge 2 highlight']:
    copy_into(src32, slE, one(src32, nm))
fx76 = lambda n, cls: fx_from(src76, spid(one(slE, n)), cls)
set_timing(slE, [
    # 1: sequence rules
    [fx76('sequence highlight', 'entr'),
     fx76('sequence zoom', 'entr') + fx76('sequence zoom', 'emph'),
     fx76('sequence explained (box + pointer)', 'entr')],
    # 2: wait for what we observe
    [fx76('sequence highlight', 'exit') + fx76('sequence zoom', 'exit')
     + fx76('sequence explained (box + pointer)', 'exit'),
     fx76('observe first highlight', 'entr'),
     fx76('observe first zoom', 'entr') + fx76('observe first zoom', 'emph'),
     fx76('observe first explained (box + pointer)', 'entr')],
    # 3: kubectl events - the sequencer holding the attachment back
    [fx76('observe first highlight', 'exit') + fx76('observe first zoom', 'exit')
     + fx76('observe first explained (box + pointer)', 'exit'),
     [p for n in seq_term for p in fx76(n, 'entr')]],
    # 4: no more error storm
    [fx76('no storm', 'entr')],
])
set_morph(slE)
manual_advance(slE)
set_notes(slE, (
    "Crossplane is eventually consistent – without ordering the attachment is created first and "
    "floods you with errors until the role exists. Harmless, but it hides real errors. The fix "
    "is function-sequencer.\n"
    "[click] One rule, in order: each resource waits until the ones before it are Ready – until "
    "then it is left out of the desired state.\n"
    "[click] Wait for what we observe: the Policy also waits for the observed shared bucket "
    "Object to be Ready.\n"
    "[click] kubectl events shows it: \"Delaying creation ... because policy is not fully "
    "ready\".\n"
    "[click] No more 'referenced field was empty' error storm.\n"
    "Bonus: enableDeletionSequencing also deletes in reverse order (Usage objects block early "
    "deletes)."))


# ---- Hidden AWS errors: problem (P5) + status solution (S5), joined by a morph.
# P5: the 5-resource Argo frame from 83, big and calm (all Ready, Healthy).
# S5: the frame shrinks left; the myapp.yaml change and its error terminal come in under it,
#     then the function-status-transformer window on the right with its balloons, then Lua.
src50, src82, src83 = S(50), S(82), S(83)
p5 = dup(src50)
drop_timing(p5)
for el in list(tree(p5)):
    if el.find('./*/p:cNvPr', NS) is not None and not name_of(el).startswith('!!Challenge'):
        tree(p5).remove(el)
F83 = ['!!MyApp Argo frame', 'Object ready', 'Bucket ready', 'Role ready', 'Policy ready',
       'RolePolicyAttachment ready', 'XR healthy']
for n in reversed(F83):
    copy_into(src83, p5, one(src83, n), first=True)
rename(one(p5, '!!MyApp Argo frame'), '!!hf Argo frame')
F_S, F_SRC, F_DST = 1.45, (3749040, 1234439), (856000, 1150000)
for el in tree(p5):
    if el.find('./*/p:cNvPr', NS) is not None and not name_of(el).startswith('!!Challenge'):
        transform(el, F_S, F_SRC, F_DST)
manual_advance(p5)
set_notes(p5, "MyApp is happy: five resources, all Ready, Argo CD Healthy. Now a developer "
              "pushes a change...")


def place(el, x, y, w, h):
    x_ = xfrm(el)
    x_.find('a:off', NS).set('x', str(x)); x_.find('a:off', NS).set('y', str(y))
    x_.find('a:ext', NS).set('cx', str(w)); x_.find('a:ext', NS).set('cy', str(h))


s5 = dup(src82)
drop_timing(s5)
for el in list(tree(s5)):
    if el.find('./*/p:cNvPr', NS) is not None and name_of(el).startswith('!!Step 5'):
        tree(s5).remove(el)
para_texts(one(s5, '!!Status window title'), ['function-status-transformer'])
st_win = ['!!Status window body', '!!Status window title bar', '!!Status window button 1',
          '!!Status window button 2', '!!Status window button 3', '!!Status window title',
          '!!Status code']
st_marks = ['matchers highlight', 'matchers zoom', 'setConditions highlight', 'setConditions zoom']
lua = ['!!Lua window body', '!!Lua window title bar', '!!Lua window button 1',
       '!!Lua window button 2', '!!Lua window button 3', '!!Lua window title', '!!Lua code']
ST_X = 4600000
for n in st_win + st_marks:
    transform(one(s5, n), 1, (274320, 0), (ST_X, 0))
for n in lua:
    transform(one(s5, n), 1, (3749040, 1188719), (ST_X, 3560000))
# the frame, small, top-left (same shapes/names as P5 so it morphs)
S5_S = 3950000 / round(5120641 * F_S)
for el in [e for e in tree(p5) if e.find('./*/p:cNvPr', NS) is not None
           and not name_of(e).startswith('!!Challenge')][::-1]:
    transform(copy_into(p5, s5, el, first=True), S5_S, F_DST, (250000, 1188719))
# Policy "updating" gif and Synced: False badge, in frame coordinates (83 -> P5 -> S5)
to_s5 = lambda el: (transform(el, F_S, F_SRC, F_DST), transform(el, S5_S, F_DST, (250000, 1188719)))
pb = xfrm(one(src83, 'Policy ready'))
PB = [int(pb.find('a:off', NS).get(k)) for k in ('x', 'y')] + \
     [int(pb.find('a:ext', NS).get(k)) for k in ('cx', 'cy')]
gif = copy_into(src50, s5, one(src50, 'policy updating (blinking gif)'))
place(gif, *PB)
to_s5(gif)
sfalse = copy_into(src83, s5, one(src83, 'Policy synced false'))
to_s5(sfalse)
# myapp.yaml change + error terminal: full size, under the frame
FB = 1188719 + round(2518891 * F_S * S5_S)
git = ['git push window body', 'git push window title bar', 'git push window button 1',
       'git push window button 2', 'git push window button 3', 'git push window title',
       'git push code']
term = ['get policy terminal body', 'get policy terminal header', 'get policy terminal text']
for n in git:
    transform(copy_into(src50, s5, one(src50, n)), 1, (1280160, 3767328), (250000, FB + 120000))
GIT_B = FB + 120000 + 707509
for n in term:
    transform(copy_into(src50, s5, one(src50, n)), 1, (4434840, 2907792), (250000, GIT_B + 90000))
# balloons: under the function-status-transformer window, pointing up at it
OB_Y2 = 1188719 + 2273250 + 230000
for n in ['matchers explained (box + pointer)', 'setConditions explained (box + pointer)']:
    g = one(s5, n)
    box, ptr = g.findall('./p:grpSp', NS)[0], g.findall('./p:sp', NS)[0]
    bx = xfrm(box)
    bw, bh = int(bx.find('a:ext', NS).get('cx')), int(bx.find('a:ext', NS).get('cy'))
    bx.find('a:off', NS).set('x', str(ST_X)); bx.find('a:off', NS).set('y', str(OB_Y2))
    px = xfrm(ptr)
    px.attrib.pop('flipH', None)
    px.set('rot', '5400000')
    cx, cy = ST_X + 600000, OB_Y2 + 36576 - 237744 // 2
    px.find('a:off', NS).set('x', str(cx - 237744 // 2))
    px.find('a:off', NS).set('y', str(cy - 146304 // 2))
    top = cy - 237744 // 2
    gx_ = xfrm(g)
    for k in ('a:off', 'a:chOff'):
        gx_.find(k, NS).set('x', str(ST_X)); gx_.find(k, NS).set('y', str(top))
    for k in ('a:ext', 'a:chExt'):
        gx_.find(k, NS).set('cx', str(bw)); gx_.find(k, NS).set('cy', str(OB_Y2 + bh - top))
for nm in ['!!Challenge 4', '!!Challenge 4 num', '!!Challenge 4 highlight']:
    copy_into(src50, s5, one(src50, nm))
# S5a = the change and its error on the right; S5b (morph) = they move under the frame and the
# function-status-transformer window comes in on the right, then its balloons, then Lua.
s5a = dup(s5)
for n in st_win + st_marks + lua + ['matchers explained (box + pointer)',
                                    'setConditions explained (box + pointer)']:
    tree(s5a).remove(one(s5a, n))
RIGHT_DX = ST_X - 250000
for n in git:
    transform(one(s5a, n), 1, (0, FB + 120000), (RIGHT_DX, 1188719))
for n in term:
    transform(one(s5a, n), 1, (0, GIT_B + 90000), (RIGHT_DX, 1188719 + 707509 + 90000))
fx50 = lambda sl, n, cls: fx_from(src50, spid(one(sl, n)), cls)
fx82 = lambda n, cls: fx_from(src82, spid(one(s5, n)), cls)
set_timing(s5a, [
    # 1: a spec change lands in Git -> the Policy starts updating
    [[p for n in git for p in fx50(s5a, n, 'entr')]
     + fx50(s5a, 'policy updating (blinking gif)', 'entr')
     + [fx_fade('exit', spid(one(s5a, 'Policy ready')), 1)]],
    # 2: AWS rejects it - Synced: False, the error only in the Policy's status
    [fx50(s5a, 'policy updating (blinking gif)', 'exit')
     + [fx_fade('entr', spid(one(s5a, 'Policy synced false')))],
     [p for n in term for p in fx50(s5a, n, 'entr')]],
])
set_morph(s5a)
manual_advance(s5a)
set_notes(s5a, (
    "[click] A developer pushes a spec change – one more policy action. The Policy starts "
    "updating.\n"
    "[click] AWS rejects it. The Policy goes Synced: False – and the real error only lives in "
    "that child's status. The XR is still Ready, Argo CD still says Healthy."))
# S5b: end state of S5a (Policy Synced: False), change + error under the frame
for n in ('policy updating (blinking gif)', 'Policy ready'):
    tree(s5).remove(one(s5, n))
set_timing(s5, [
    [fx82('matchers highlight', 'entr'), fx82('matchers zoom', 'entr') + fx82('matchers zoom', 'emph'),
     fx82('matchers explained (box + pointer)', 'entr')],
    [fx82('matchers highlight', 'exit') + fx82('matchers zoom', 'exit')
     + fx82('matchers explained (box + pointer)', 'exit'),
     fx82('setConditions highlight', 'entr'),
     fx82('setConditions zoom', 'entr') + fx82('setConditions zoom', 'emph'),
     fx82('setConditions explained (box + pointer)', 'entr')],
    [fx82('setConditions highlight', 'exit') + fx82('setConditions zoom', 'exit')
     + fx82('setConditions explained (box + pointer)', 'exit'),
     [p for n in lua for p in fx82(n, 'entr')]],
])
set_morph(s5)
manual_advance(s5)
set_notes(s5, (
    "The fix is function-status-transformer.\n"
    "[click] matchers: any composed resource with Synced=False / ReconcileError – the regex "
    "captures the child's error message.\n"
    "[click] setConditions: put it on the XR as ResourcesHealthy=False with that message.\n"
    "[click] Argo CD reads the condition with a few lines of Lua, and the app goes Degraded with "
    "the real AWS error."))


def build_hub(slide, prev, cur):
    """Static rows stay groups; the !! pills on top are single shapes (see flatten_pill), so
    Keynote can only match a pill with a pill. The click hides !!prev and shows !!cur, so before
    the click only prev is a single shape and after it only cur is."""
    for el in list(tree(slide)):
        if el.find('./*/p:cNvPr', NS) is not None:
            tree(slide).remove(el)
    drop_timing(slide)
    for n in HUB_BASE_SHAPES:
        copy_into(HUB_BASE, slide, one(HUB_BASE, n))
    lab = copy.deepcopy(LABEL_TPL)
    lab.find('./p:nvSpPr/p:cNvPr', NS).set('id', str(next_id(slide)))
    lx = lab.find('./p:spPr/a:xfrm', NS)
    lx.find('a:off', NS).set('x', str(ROW_X)); lx.find('a:ext', NS).set('cx', str(ROW_W))
    tree(slide).append(lab)
    for k in range(1, len(CH) + 1):
        make_row(slide, k, '')
    pill = {}
    for k in (prev, cur):
        if k:
            pill[k] = [*make_row(slide, k, '!!'), make_hl(slide, k)]
    if not cur:
        return
    step = []
    if prev:
        g, n, h = pill[prev]
        step += [fx_fade('exit', spid(g), 1), fx_fade('exit', spid(n), 1),
                 fx_fade('exit', spid(h), 150)]
    g, n, h = pill[cur]
    step += [fx_fade('entr', spid(g), 1), fx_fade('entr', spid(n), 1), fx_fade('entr', spid(h), 150)]
    set_timing(slide, [[step]])


h2 = dup(S(35))
for s, prev, cur in ((S(29), None, 1), (h2, 1, 2), (S(35), 2, 3), (S(41), 3, 4),
                     (S(53), 4, None)):
    build_hub(s, prev, cur)
set_morph(S(29))                      # Meet MyApp -> hub: the Argo frame slides left
manual_advance(S(53))                 # last hub: wait before crossplane render

# ---------------------------------------------------------------- pipeline recap (55)
recap = S(55)
drop_timing(recap)
remove_shapes(recap, ['!!Step 1', '!!Step 1 num', '!!Step 1 highlight'])
manual_advance(recap)

# ---------------------------------------------------------------- order
O = lambda *ns: [S(n) for n in ns]
order = (O(*range(1, 29))
         + [S(54), S(29), c1_center, c1_top, slA, slB, slC, slD]
         + [c1_top_out, c1_center_out]
         + [h2]
         + O(30, 31, 32) + [slE] + O(33, 34)
         + O(35, 42, 43, 44, 70, 45, 46)
         + O(41, 48, 49) + [p5, s5a, s5] + O(51, 52)
         + O(53) + O(*range(87, len(orig) + 1)))
assert len(set(map(id, order))) == len(order)

# ---------------------------------------------------------------- flatten the pills
# Keynote's Magic Move ignores shape names and matches groups by look: all list rows are the
# same rounded-rect group, so it pulled the wrong row. One shape per pill (box + its own text)
# is matched by its unique text in Keynote and by its !! name in PowerPoint.
def flatten_pill(g):
    gx = xfrm(g)
    ox, oy = int(gx.find('a:off', NS).get('x')), int(gx.find('a:off', NS).get('y'))
    ew, eh = int(gx.find('a:ext', NS).get('cx')), int(gx.find('a:ext', NS).get('cy'))
    cx0, cy0 = int(gx.find('a:chOff', NS).get('x')), int(gx.find('a:chOff', NS).get('y'))
    cw, chh = int(gx.find('a:chExt', NS).get('cx')), int(gx.find('a:chExt', NS).get('cy'))
    sx, sy = ew / cw, eh / chh
    shape, text = g.findall('p:sp', NS)
    tx = xfrm(text)
    tox = (int(tx.find('a:off', NS).get('x')) - cx0) * sx
    toy = (int(tx.find('a:off', NS).get('y')) - cy0) * sy
    tw = int(tx.find('a:ext', NS).get('cx')) * sx
    th = int(tx.find('a:ext', NS).get('cy')) * sy
    sp = copy.deepcopy(shape)
    c = sp.find('./p:nvSpPr/p:cNvPr', NS)
    gc = g.find('./p:nvGrpSpPr/p:cNvPr', NS)
    c.set('id', gc.get('id')); c.set('name', gc.get('name'))
    x = xfrm(sp)
    x.find('a:off', NS).set('x', str(ox)); x.find('a:off', NS).set('y', str(oy))
    x.find('a:ext', NS).set('cx', str(ew)); x.find('a:ext', NS).set('cy', str(eh))
    body = copy.deepcopy(text.find('p:txBody', NS))
    bp = body.find('a:bodyPr', NS)
    for k in list(bp):
        bp.remove(k)
    ti = lambda v: str(max(0, round(v)))
    bp.set('wrap', 'square'); bp.set('anchor', 'ctr')
    bp.set('lIns', ti(tox)); bp.set('rIns', ti(ew - tox - tw))
    bp.set('tIns', '0'); bp.set('bIns', '0')
    sp.replace(sp.find('p:txBody', NS), body)
    g.getparent().replace(g, sp)


PILL_RE = re.compile(r'!!Challenge \d( num)?')
for s_ in order:
    for el in list(tree(s_)):
        if el.tag == P + 'grpSp' and PILL_RE.fullmatch(name_of(el)):
            flatten_pill(el)

para_texts(one(S(70), 'auto-ready window title'), ['function-auto-ready - composition.yaml'])
# Never Ready -> auto-ready: after the get-myapp terminal, one click morphs into 70 - the
# terminal slides down and the auto-ready window builds in by itself; next click flips Ready.
manual_advance(S(44))
set_morph(S(70))


def make_first_auto(slide):
    t = slide._element.find('p:timing', NS)
    main = [c for c in t.iter(P + 'cTn') if c.get('nodeType') == 'mainSeq'][0]
    g = main.find('p:childTnLst', NS).find('p:par', NS)
    conds = g.find('p:cTn/p:stCondLst', NS)
    conds.append(etree.fromstring(f'<p:cond xmlns:p="{NS["p"]}" evt="onBegin"><p:tn val="'
                                  f'{main.get("id")}"/></p:cond>'))
    [c for c in g.iter(P + 'cTn') if c.get('presetClass')][0].set('nodeType', 'afterEffect')


make_first_auto(S(70))
# Trace slides (92, 93): bigger trace text - wrap the command, then scale the block 1.6x.
TR_S, TR_PITCH, TR_O = 1.6, 96041, (365759, 1401840)
for sl in (S(92), S(93)):
    g = one(sl, 'trace run 1')
    kids = {name_of(c): c for c in g if c.find('./*/p:cNvPr', NS) is not None}
    for k in range(8, 1, -1):
        o = xfrm(kids[f'trace run 1 line {k}']).find('a:off', NS)
        o.set('y', str(int(o.get('y')) + TR_PITCH))
    l1 = kids['trace run 1 line 1']
    l1b = copy.deepcopy(l1)
    l1.findall('.//a:r', NS)[1].find('a:t', NS).text = 'crossplane resource trace \\'
    rb = l1b.findall('.//a:r', NS)
    rb[0].find('a:t', NS).text = '  '
    rb[1].find('a:t', NS).text = 'myapps.crossplane.best-practice.com/checkout -n team-payments'
    rename(l1b, 'trace run 1 line 1b')
    l1b.find('./*/p:cNvPr', NS).set('id', str(next_id(sl)))
    xfrm(l1b).find('a:off', NS).set('y', str(TR_PITCH - 1))
    l1.addnext(l1b)
    gx = xfrm(g)
    for k_ in ('a:ext', 'a:chExt'):
        e = gx.find(k_, NS)
        e.set('cy', str(int(e.get('cy')) + TR_PITCH))
    # boxes hug their text (wrap=none boxes wider than the text get centred in some viewers)
    for c in g.iter(P + 'sp'):
        txt = ''.join(t.text or '' for t in c.iter('{%s}t' % NS['a']))
        xfrm(c).find('a:ext', NS).set('cx', str(38684 * len(txt)))
    transform(g, TR_S, TR_O, TR_O)
    last_run(one(sl, 'trace policy row bad line 1'),
             '├─ Policy/checkout                False   True   ReconcileError…')
    for n in ('trace policy row bad line 1', 'trace policy row healed line 1'):
        el = one(sl, n)
        txt = ''.join(t.text or '' for t in el.iter('{%s}t' % NS['a']))
        xfrm(el).find('a:ext', NS).set('cx', str(38684 * len(txt)))
    # the Policy row overlays sit on row 5 now (command takes two lines)
    for n in ('trace policy row cover', 'trace policy row bad line 1', 'trace policy row cover 2',
              'trace policy row healed line 1'):
        el = one(sl, n)
        transform(el, 1, (0, 0), (0, TR_PITCH))
        w0 = xfrm(el).find('a:ext', NS).get('cx')
        transform(el, TR_S, TR_O, TR_O)
        if 'cover' in n:
            xfrm(el).find('a:ext', NS).set('cx', w0)       # stay inside the terminal

# Slide 93 (k9s): click 1 - myapp.yaml leaves, the plugin window fills the right column with
# bigger code (long viddy lines wrapped); click 2 - plugin leaves, the GIF (cropped to its
# content) comes in full width at the bottom.
k9 = S(93)
K9_PITCH, K9_CH = 75087, 35013
code = one(k9, 'k9s plugin code')
lines = {name_of(c): c for c in code if c.find('./*/p:cNvPr', NS) is not None}
order_ = [lines[f'k9s plugin code line {i}'] for i in range(1, 17)]
for k, (a1, a2) in ((13, ("viddy -pw 'crossplane resource trace --context $CONTEXT",
                          "-n $NAMESPACE $RESOURCE_NAME.$RESOURCE_GROUP $NAME -owide'")),
                    (15, ("viddy -pw 'crossplane resource trace --context $CONTEXT",
                          "$RESOURCE_NAME.$RESOURCE_GROUP $NAME -owide'"))):   # top-down
    ln = order_[k - 1]
    nb = copy.deepcopy(ln)
    ln.findall('.//a:r', NS)[1].find('a:t', NS).text = a1
    rs = nb.findall('.//a:r', NS)
    rs[0].find('a:t', NS).text = ' ' * 10
    rs[1].find('a:t', NS).text = a2
    rename(nb, f'k9s plugin code line {k}b')
    nb.find('./*/p:cNvPr', NS).set('id', str(next_id(k9)))
    o = xfrm(nb).find('a:off', NS)
    o.set('y', str(int(o.get('y')) + K9_PITCH))
    for later in order_[k:]:
        lo = xfrm(later).find('a:off', NS)
        lo.set('y', str(int(lo.get('y')) + K9_PITCH))
    ln.addnext(nb)
for c in code.iter(P + 'sp'):
    txt = ''.join(t.text or '' for t in c.iter('{%s}t' % NS['a']))
    xfrm(c).find('a:ext', NS).set('cx', str(K9_CH * max(1, len(txt))))
cx_ = xfrm(code)
for k_ in ('a:ext', 'a:chExt'):
    e = cx_.find(k_, NS)
    e.set('cy', str(int(e.get('cy')) + 2 * K9_PITCH))
K9_F, K9_X, K9_W = 1.45, 5350000, 9144000 - 220000 - 5350000   # plugin window: narrower right column
transform(code, K9_F, (4764024, 1517904), (K9_X + 146304, 1517904))
K9_DX = K9_X - 4617720
for n in ('k9s plugin window body', 'k9s plugin window title bar', 'k9s plugin window button 1',
          'k9s plugin window button 2', 'k9s plugin window button 3', 'k9s plugin window title'):
    transform(one(k9, n), 1, (0, 0), (K9_DX, 0))
for n in ('k9s plugin window body', 'k9s plugin window title bar'):
    xfrm(one(k9, n)).find('a:ext', NS).set('cx', str(K9_W))
kt = xfrm(one(k9, 'k9s plugin window title'))
kt.find('a:off', NS).set('x', str(K9_X + (K9_W - int(kt.find('a:ext', NS).get('cx'))) // 2))
body = xfrm(one(k9, 'k9s plugin window body')).find('a:ext', NS)
body.set('cy', str(4250000 - 1188720))   # clear of the logo
gif9 = one(k9, 'k9s crossplane-watch (gif)')
# Re-lay the GIF itself (Keynote ignores picture crop on GIFs): regif.py wraps the command onto
# two lines so Every/Command/Time all fit, and wraps the Policy error - all 6 frames, same timing.
import io as _io, subprocess as _sp, os as _os
_blob = k9.part.related_part(gif9.find('.//a:blip', NS).get('{%s}embed' % NS['r'])).blob
_d = _os.path.dirname(_os.path.abspath(__file__))
open(_os.path.join(_d, 'k9s_orig.gif'), 'wb').write(_blob)
_sp.run(['python3', _os.path.join(_d, 'regif.py'), _os.path.join(_d, 'k9s_orig.gif'),
         _os.path.join(_d, 'k9s_new.gif'), 'notitle'], check=True, stdout=_sp.DEVNULL)
_buf = _io.BytesIO(open(_os.path.join(_d, 'k9s_new.gif'), 'rb').read())
from PIL import Image as _Img
GIF_BOX = (0, 0) + _Img.open(_os.path.join(_d, 'k9s_new.gif')).size
_buf.seek(0)
_tmp = k9.shapes.add_picture(_buf, 0, 0)
gif9.find('.//a:blip', NS).set('{%s}embed' % NS['r'],
                               _tmp._element.find('.//a:blip', NS).get('{%s}embed' % NS['r']))
_tmp._element.getparent().remove(_tmp._element)
GIF_W = 5350000 - 100000 - 274320
# A window like the plugin's holds the GIF, where the trace terminal was.
GW_PAD = 30000
GIF_H = round((GIF_W - 2 * GW_PAD) * GIF_BOX[3] / GIF_BOX[2])
gwin = []
for n in ('k9s plugin window body', 'k9s plugin window title bar', 'k9s plugin window button 1',
          'k9s plugin window button 2', 'k9s plugin window button 3', 'k9s plugin window title'):
    c = copy.deepcopy(one(k9, n))
    rename(c, n.replace('k9s plugin', 'k9s watch'))
    c.find('./*/p:cNvPr', NS).set('id', str(next_id(k9)))
    transform(c, 1, (K9_X, 0), (274320, 0))
    tree(k9).append(c)
    gwin.append(c)
for c in gwin[:2]:
    xfrm(c).find('a:ext', NS).set('cx', str(GIF_W))
xfrm(gwin[0]).find('a:ext', NS).set('cy', str(237744 + GIF_H + 2 * GW_PAD))
para_texts(gwin[5], ['k9s  ·  crossplane-watch'])
wt = xfrm(gwin[5])
wt.find('a:off', NS).set('x', str(274320 + (GIF_W - int(wt.find('a:ext', NS).get('cx'))) // 2))
place(gif9, 274320 + GW_PAD, 1188719 + 237744 + GW_PAD, GIF_W - 2 * GW_PAD, GIF_H)
tree(k9).append(gif9)                                   # on top of its window
myapp_ = [el for el in tree(k9) if el.find('./*/p:cNvPr', NS) is not None
          and name_of(el).startswith('myapp ')]
plug = [el for el in tree(k9) if el.find('./*/p:cNvPr', NS) is not None
        and name_of(el).startswith('k9s plugin')]
fx93 = lambda el, cls: fx_from(k9, spid(el), cls)
trace_ = [el for el in tree(k9) if el.find('./*/p:cNvPr', NS) is not None
          and name_of(el).startswith('trace ')]
chrome = [el for el in plug if name_of(el) != 'k9s plugin code'] + gwin
set_timing(k9, [
    # one click: myapp + trace leave, both windows open together (plugin right, k9s watch where
    # the trace was), then their contents fade in together - the YAML and the GIF
    [[fx_fade('exit', spid(el), 300) for el in myapp_ + trace_]
     + [fx_fade('entr', spid(el), 300) for el in chrome],
     [fx_fade('entr', spid(one(k9, 'k9s plugin code')), 400), fx_fade('entr', spid(gif9), 400)]],
])

sldIdLst = prs.slides._sldIdLst
by_part = {}
for sldId in list(sldIdLst):
    by_part[prs.part.related_part(sldId.rId)] = sldId
    sldIdLst.remove(sldId)
keep = set()
for s in order:
    sldIdLst.append(by_part[s.part])
    keep.add(s.part)
for part, sldId in by_part.items():
    if part not in keep:
        prs.part.drop_rel(sldId.rId)
prs.save(sys.argv[2])
print('slides:', len(order))
