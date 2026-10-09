import re
import sys
import html as htmlmod
VERDANA = {' ':.352,'a':.599,'b':.631,'c':.524,'d':.631,'e':.594,'f':.351,'g':.631,'h':.635,'i':.274,'j':.326,
 'k':.575,'l':.274,'m':.973,'n':.635,'o':.611,'p':.631,'q':.631,'r':.421,'s':.508,'t':.389,'u':.635,'v':.592,
 'w':.817,'x':.592,'y':.592,'z':.524,'A':.684,'B':.686,'C':.698,'D':.770,'E':.632,'F':.575,'G':.775,'H':.752,
 'I':.420,'J':.455,'K':.693,'L':.562,'M':.855,'N':.752,'O':.787,'P':.603,'Q':.787,'R':.695,'S':.684,'T':.616,
 'U':.732,'V':.684,'W':.989,'X':.684,'Y':.616,'Z':.684,'0':.636,'1':.636,'2':.636,'3':.636,'4':.636,'5':.636,
 '6':.636,'7':.636,'8':.636,'9':.636,'.':.363,',':.363,':':.454,';':.454,'-':.454,'–':.636,'—':1.0,'·':.363,
 '(':.454,')':.454,"'":.268,'’':.268,'"':.459,'€':.636,'×':.838,'→':1.0,'⇅':.9,'↻':.9,'/':.454,'?':.545,
 '&':.726,'%':1.076,'+':.838,'=':.838,'…':1.0,'✓':.8,'◆':.8}
BOLD, ASC, DESC = 1.08, 0.76, 0.22
def attr(t, n, d=None):
    m = re.search(r'\b'+n+r'="([^"]*)"', t)
    return m.group(1) if m else d
def _texts(body, fi, out):
    """Estimated bounding box per <text>: (string, (x0,y0,x1,y1), (anchor_x, anchor_y))."""
    found = []
    for m in re.finditer(r'<text\b([^>]*)>([^<]*)</text>', body):
        t, s = m.group(1), htmlmod.unescape(m.group(2)).strip()
        if not s:
            continue
        if 'transform=' in t:
            out.append((fi, 'rotated text (not audited)', s))
            continue
        x, y, size = float(attr(t,'x',0)), float(attr(t,'y',0)), float(attr(t,'font-size',13))
        w = sum(VERDANA.get(c,.62) for c in s)*size*(BOLD if attr(t,'font-weight','')=='700' else 1)
        a = attr(t,'text-anchor','start')
        x0 = x-w/2 if a=='middle' else (x-w if a=='end' else x)
        found.append((s,(x0,y-ASC*size,x0+w,y+DESC*size),(x,y)))
    return found

def _check_one(box, rects, lines, vb, fi, out):
    """viewBox margin, box containment at 8 units, and lines crossing the label."""
    s,(x0,y0,x1,y1),(ax,ay) = box
    if x0 < vb[0]+6 or x1 > vb[0]+vb[2]-6:
        out.append((fi,'exceeds viewBox',s))
    c = [r for r in rects if r[0]<=ax<=r[0]+r[2] and r[1]<=ay<=r[1]+r[3] and r[2] < vb[2]*0.95]
    if c:
        r = min(c, key=lambda r: r[2]*r[3])
        over = max(r[0]+8-x0, x1-(r[0]+r[2]-8), 0)
        if over > 0:
            out.append((fi, f'overflows its box by {over:.0f}', s))
    for lx1,ly1,lx2,ly2 in lines:
        if abs(lx1-lx2)<1 and x0<lx1<x1 and min(ly1,ly2)<y1 and max(ly1,ly2)>y0:
            out.append((fi,'crossed by vertical line',s))
        if abs(ly1-ly2)<1 and y0<ly1<y1 and min(lx1,lx2)<x1 and max(lx1,lx2)>x0:
            out.append((fi,'crossed by horizontal line',s))

def _check_collisions(texts, fi, out):
    for i in range(len(texts)):
        for j in range(i+1,len(texts)):
            a,b = texts[i][1],texts[j][1]
            if a[0]<b[2] and b[0]<a[2] and a[1]<b[3] and b[1]<a[3]:
                out.append((fi,'collides with '+texts[j][0],texts[i][0]))

def audit(path):
    src = open(path).read()
    out = []
    for fi, svg in enumerate(re.finditer(r'<svg\b[^>]*viewBox="([^"]+)"[^>]*>(.*?)</svg>', src, flags=re.S), 1):
        vb = [float(v) for v in svg.group(1).split()]
        body = svg.group(2)
        rb = re.sub(r'<g\b[^>]*transform=[^>]*>.*?</g>', '', body, flags=re.S)
        rects = [tuple(float(attr(t,k,0)) for k in ('x','y','width','height'))
                 for t in re.findall(r'<rect\b[^>]*/?>', rb) if 'transform=' not in t]
        lines = [tuple(float(attr(t,k,0)) for k in ('x1','y1','x2','y2'))
                 for t in re.findall(r'<line\b[^>]*/?>', body)]
        texts = _texts(body, fi, out)
        for box in texts:
            _check_one(box, rects, lines, vb, fi, out)
        _check_collisions(texts, fi, out)
    return out

if __name__ == '__main__':
    f = audit(sys.argv[1])
    [print(f'figure {a}: {b} :: "{c}"') for a,b,c in f]
    print(f'--- {len(f)} finding(s)')
    sys.exit(1 if f else 0)
