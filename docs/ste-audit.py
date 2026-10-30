#!/usr/bin/env python3
"""Audit markdown technical documents against the checkable ASD-STE100 rules."""
import re, sys, glob, os

BANNED = {
 r'\bcommence[sd]?\b':"start", r'\binitiate[sd]?\b':"start", r'\bterminate[sd]?\b':"stop",
 r'\butilize[sd]?\b':"use", r'\bin order to\b':"to", r'\bprior to\b':"before",
 r'\bsubsequent to\b':"after", r'\bin the event that\b':"if", r'\bis capable of\b':"can",
 r'\battempt(s|ed)?\b':"try", r'\bassist(s|ed)?\b':"help", r'\bapproximately\b':"about",
 r'\bsufficient\b':"enough", r'\brequire(s|d)?\b':"need", r'\bindicate(s|d)?\b':"show",
 r'\bascertain\b':"find",
}
SKIP_LINE = re.compile(r'^\s*(\||```|\s*$|[-*]\s*$|\d+\.\s*$)')  # tables/code/blank
PATHISH = re.compile(r'`[^`]*`|\S*/\S*')

def sentences(text):
    # strip code spans and inline code
    text = text.replace('**','').replace('*','')
    t = re.sub(r'`[^`]*`', ' CODE ', text)
    return [s.strip() for s in re.split(r'(?<=[.!?])\s+', t) if s.strip()]

def audit(path):
    lines = open(path).read().split('\n')
    res = dict(em=0, banned={}, slash=0, long=0, longest=[], passive=0, gerund=0)
    for ln, line in enumerate(lines, 1):
        if SKIP_LINE.match(line): continue
        if line.startswith('#'): continue
        if '—' in line: res['em'] += 1
        for pat, repl in BANNED.items():
            for m in re.finditer(pat, line, re.I):
                res['banned'].setdefault(m.group(0).lower(), 0)
                res['banned'][m.group(0).lower()] += 1
        stripped = PATHISH.sub(' PATH ', line)
        if '/' in stripped: res['slash'] += 1
        for s in sentences(line):
            w = len(s.split())
            if w > 25:
                res['long'] += 1
                res['longest'].append((w, ln, s[:90]))
            if re.search(r'\b(is|are|was|were|be|been)\s+\w+(ed|en)\b', s, re.I): res['passive'] += 1
    res['longest'].sort(reverse=True)
    return res

paths = sys.argv[1:] or sorted(glob.glob('*.md'))
print(f"{'document':38s} {'em':>4s} {'slash':>6s} {'long':>5s} {'passive':>8s} {'banned':>7s}")
tot = dict(em=0, slash=0, long=0, passive=0, banned=0)
for p in paths:
    r = audit(p)
    b = sum(r['banned'].values())
    print(f"{os.path.basename(p)[:38]:38s} {r['em']:4d} {r['slash']:6d} {r['long']:5d} {r['passive']:8d} {b:7d}")
    for k,v in tot.items(): tot[k] += (b if k=='banned' else r[k])
    if r['banned']: print(f"    banned words: {r['banned']}")
    for w,ln,s in r['longest'][:3]: print(f"    long {w}w L{ln}: {s}")
print(f"{'TOTAL':38s} {tot['em']:4d} {tot['slash']:6d} {tot['long']:5d} {tot['passive']:8d} {tot['banned']:7d}")
