"""Re-lay the k9s crossplane-watch GIF narrower: the command wraps onto 2 lines so the
Every / Command / Time boxes all fit, and the Policy error wraps under its row.
usage: regif.py in.gif out.gif [notitle]"""
import sys
from PIL import Image

BG, TITLE, EDGE = (22, 24, 28), (34, 36, 40), (44, 46, 50)
PITCH = 22                                   # one text line in the GIF


def ink(p):
    return sum(abs(a - b) for a, b in zip(p, BG)) > 40


def word_gap(img, x0, x1, y0, y1, target):
    """The blank (space) column run closest to target."""
    blank = [x for x in range(x0, x1) if not any(ink(img.getpixel((x, y))) for y in range(y0, y1))]
    runs, s, prev = [], None, None
    for x in blank:
        if s is None or x != prev + 1:
            if s is not None:
                runs.append((s, prev))
            s = x
        prev = x
    runs.append((s, prev))
    return min((r for r in runs if r[1] - r[0] >= 5), key=lambda r: abs(sum(r) // 2 - target))


def vstretch(img, at, n):
    w, h = img.size
    out = Image.new('RGB', (w, h + n))
    out.paste(img.crop((0, 0, w, at)), (0, 0))
    for i in range(n):
        out.paste(img.crop((0, at, w, at + 1)), (0, at + i))
    out.paste(img.crop((0, at, w, h)), (0, at + n))
    return out


NOTITLE = len(sys.argv) > 3 and sys.argv[3] == 'notitle'   # slide window draws its own
src = Image.open(sys.argv[1])
frames, durs = [], []
for i in range(src.n_frames):
    src.seek(i)
    frames.append(src.convert('RGB'))
    durs.append(src.info.get('duration', 2000))
f0 = frames[0]
g1 = word_gap(f0, 117, 1155, 56, 78, 117 + (1155 - 117) // 2)          # command: 2 lines
L1, L2 = (117, g1[0]), (g1[1] + 1, 1155)
CMD_W = 9 + max(L1[1] - L1[0], L2[1] - L2[0]) + 18
NW = 108 + CMD_W + (1410 - 1213)
errf = max(frames, key=lambda f: max([x for x in range(2, 1405)
                                       if any(ink(f.getpixel((x, y))) for y in (141, 145, 149))]))
g2 = word_gap(errf, 600, NW - 12, 138, 156, NW - 60)                   # Policy error: 2 lines
out_frames = []
for f in frames:
    hdr = Image.new('RGB', (NW, 54), BG)
    hdr.paste(f.crop((0, 33, 108, 87)), (0, 0))                          # border + Every + gap
    hdr.paste(f.crop((1180, 33, 1181, 87)).resize((CMD_W - 18, 54)), (117, 0))
    hdr.paste(f.crop((108, 33, 117, 87)), (108, 0))
    hdr.paste(f.crop((1204, 33, 1213, 87)), (108 + CMD_W - 9, 0))
    hdr.paste(f.crop((108, 33, 200, 48)), (108, 0))                       # "Command" label
    hdr.paste(f.crop((1213, 33, 1410, 87)), (108 + CMD_W, 0))              # gap + Time + border
    hdr = vstretch(hdr, 78 - 33, PITCH)               # grow the boxes below line 1
    hdr.paste(f.crop((L1[0], 52, L1[1], 80)), (117, 52 - 33))
    hdr.paste(f.crop((L2[0], 52, L2[1], 80)), (117, 52 - 33 + PITCH))
    tb = Image.new('RGB', (NW, 33), TITLE)
    tb.paste(f.crop((0, 0, 140, 33)), (0, 0))
    tb.paste(f.crop((1380, 0, 1410, 33)), (NW - 30, 0))
    tb.paste(f.crop((700, 0, 701, 1)).resize((NW - 170, 1)), (140, 0))
    tb.paste(f.crop((620, 0, 790, 33)), (NW // 2 - 85, 0))
    t_top = f.crop((0, 87, NW, 159))
    t_top.paste(Image.new('RGB', (NW - g2[1] - 1, PITCH), BG), (g2[1] + 1, 137 - 87))
    cont = Image.new('RGB', (NW, PITCH), BG)
    cont.paste(f.crop((g2[1] + 1, 137, 1405, 159)), (485 + 18, 0))         # under STATUS
    t_bot = f.crop((0, 159, NW, 252))
    # the extra line only when this frame shows the error; the frame keeps its size (blank
    # space goes to the bottom instead of under the Policy row)
    has_err = any(ink(f.getpixel((x, y))) for x in range(g2[1] + 1, 1405) for y in (141, 145, 149))
    body = (t_top, cont, t_bot) if has_err else (t_top, t_bot)
    parts = ((Image.new('RGB', (NW, 8), BG),) if NOTITLE else (tb,)) + (hdr,) + body
    H = sum(p.size[1] for p in parts) + (0 if has_err else PITCH)
    out = Image.new('RGB', (NW, H), BG)
    y = 0
    for part in parts:
        out.paste(part, (0, y))
        y += part.size[1]
    for x in (0, 1, NW - 2, NW - 1):
        for yy in range(0 if NOTITLE else 33, H):
            out.putpixel((x, yy), EDGE)
    out_frames.append(out)
out_frames[0].save(sys.argv[2], save_all=True, append_images=out_frames[1:], duration=durs,
                   loop=0)
for k, fr in enumerate(out_frames):
    fr.save(f'{sys.argv[2]}.{k}.png')
print(NW, out_frames[0].size)
