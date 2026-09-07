#!/usr/bin/env python3
"""compile-north-star-progress.py -- compile one north-star file into a single-file progress
checkpoint (north-star-progress.html) with a replay slider over sampled commits.

A checkpoint (NAMING.md) is a single-file HTML PROJECTION of committed bytes: the destination
line, the reached-when states, condition cards by derived status, the needs edges, the
frontier with verbs, the horizon and excluded panels, the distance, and the drift chips, at
every sampled commit ("stop"), rendered client-side from one inline JSON data block. The
derivation is spec 1's (scripts/north-star-check.py, imported from this script's directory);
that is exactly why the lane that owns this compiler measures parity against an INDEPENDENT
reference evaluator and never against that CLI.

Determinism (the compile family's rules): no wall-clock read in the render path when --now is
given (the claim join evaluates against --now; the header records it), no timestamps, sorted
keys, atomic write, `--check` by digest, byte-identical recompiles.

Usage
  compile-north-star-progress.py <north-star-file> [--repo R] [--stops STOPS.json]
                                 [--now UNIX] [--ttl-s N] [--today YYYY-MM-DD] [--out PATH]
  compile-north-star-progress.py --check PAGE [--repo R]
  compile-north-star-progress.py --selftest

  <north-star-file>  a path to the north-star file; its bytes are read AS COMMITTED at each
                     stop (never from the working tree). The repository is the one containing
                     the file unless --repo is given.
  --stops            a JSON file: either {"stops": [{"sha": ...}, ...]} or a plain list of
                     shas. HEAD is always a stop (appended when absent). Without --stops the
                     stops are every commit in HEAD's history whose subject matches
                     KEPT|DISCARDED|decision: (the most recent %d) plus HEAD.
  --now              the clock for the claim join (H-215), recorded in the header; default is
                     the wall clock (recorded, so --check stays deterministic).
  --out              default: north-star-progress.html beside the north-star file.
  --check PAGE       no render: re-derive the data block for the page's stops plus HEAD and
                     compare against the header. Exit 0 fresh, 1 stale, 2 error.
  --selftest         build a throwaway repository, prove byte-identical recompiles, --check 0
                     on the clean tree, --check 1 after one seeded status flip, exactly three
                     drift chips for the three seeded drift classes and zero on the clean page,
                     an exact panel partition at every stop, and the claim overlay. Exit 0 iff
                     every check passes.

Drift classes (HEAD-contradictions, disjoint from spec 1's --strict lint):
  BANKS-ACTIVE          an Excluded banks: target H-NNN whose committed spec's Status word is
                        not `discarded` (the null is not banked); chip id = X-NN
  REACHED-WHEN-RETIRED  a reached-when id deriving retired:C-NN; chip id = C-NN
  DUAL-BINDING          an Excluded banks: target equal to some condition's bound; chip id = X-NN

Stdlib + git only (plus the sibling north-star-check.py). Python 3.9+.
"""
import argparse
import datetime
import hashlib
import html
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
COMPILER_PATH = "scripts/compile-north-star-progress.py"
OUTPUT_NAME = "north-star-progress.html"
STOP_RULE = re.compile(r"KEPT|DISCARDED|decision:")
DEFAULT_MAX_STOPS = 60
__doc__ = __doc__ % DEFAULT_MAX_STOPS

HEADER_RE = {
    "source": re.compile(r"^\s*source: (\S+) sha256:([0-9a-f]{64})\s*$", re.M),
    "head": re.compile(r"^\s*head: ([0-9a-f]{40})\s*$", re.M),
    "clock": re.compile(r"^\s*clock: now=(\d+) ttl_s=(\d+) today=(\S+)\s*$", re.M),
    "data": re.compile(r"^\s*data: sha256:([0-9a-f]{64})\s*$", re.M),
}
DATA_BLOCK_RE = re.compile(
    r'<script type="application/json" id="north-star-data">(.*?)</script>', re.S)


def load_ncs():
    p = os.path.join(HERE, "north-star-check.py")
    spec = importlib.util.spec_from_file_location("north_star_check", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def sha256_text(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def sha256_bytes(b):
    return hashlib.sha256(b).hexdigest()


# ---------------------------------------------------------------- derivation per stop -------

def drift_for(ncs, doc, repo, sha):
    """The three HEAD-contradiction classes over one derived doc at one commit."""
    out = []
    by_id = {c["id"]: c for c in doc["conditions"]}
    bounds = {}
    for c in doc["conditions"]:
        bounds.setdefault(c["bound"], c["id"])
    for x in doc["excluded"]:
        target = x["banks"].strip()
        if re.match(r"^H-\d{3,}$", target):
            pat = re.compile(r"^hypotheses/%s-.*\.md$" % re.escape(target))
            matches = [p for p in repo.ls(sha, "hypotheses") if pat.match(p)]
            if len(matches) == 1:
                word = (ncs.status_word(repo.show(sha, matches[0]) or "") or "").lower()
                if not word.startswith("discarded"):
                    out.append({"class": "BANKS-ACTIVE", "id": x["id"], "ref": target})
        if target in bounds:
            out.append({"class": "DUAL-BINDING", "id": x["id"], "ref": bounds[target]})
    for t in doc["reached_when"]:
        c = by_id.get(t)
        if c and str(c.get("status", "")).startswith("retired:"):
            out.append({"class": "REACHED-WHEN-RETIRED", "id": t, "ref": c["status"]})
    out.sort(key=lambda d: (d["class"], d["id"]))
    return out


def distance_path(doc):
    """One longest open-or-unbound path into a reached-when condition: (ids, length)."""
    by_id = {c["id"]: c for c in doc["conditions"]}
    memo = {}

    def longest(cid):
        if cid in memo:
            return memo[cid]
        c = by_id[cid]
        w = 1 if c["status"] in ("open", "unbound") else 0
        best, best_path = 0, []
        for n in c["needs"]:
            if n["id"] not in by_id:
                continue
            l, p = longest(n["id"])
            if l > best:
                best, best_path = l, p
        path = best_path + ([cid] if w else [])
        memo[cid] = (w + best, path)
        return memo[cid]

    best, path = 0, []
    for t in doc["reached_when"]:
        if t not in by_id:
            continue
        l, p = longest(t)
        if l > best:
            best, path = l, p
    return path, best


def entry_for(ncs, repo, sha, path, index, subject, is_head, live_root, now, ttl_s, today):
    text = repo.show(sha, path)
    if text is None:
        raise RuntimeError("north-star file %s absent at %s" % (path, sha[:12]))
    doc = ncs.parse_north_star(text, path)
    ncs.derive(doc, repo, sha, live_root=(live_root if is_head else None), ttl_s=ttl_s,
               now=now, today=today)
    entry = {"sha": sha, "index": index, "subject": subject, "is_head": is_head,
             "destination": doc["destination"], "reached_when_ids": list(doc["reached_when"]),
             "findings": [{"class": f["class"], "line": f["line"], "message": f["message"]}
                          for f in doc["findings"]],
             "derived": bool(doc.get("derived"))}
    if not doc.get("derived"):
        entry.update({"statuses": {}, "effective": {}, "needs_met": {}, "frontier": [],
                      "claimed_fresh": [], "retired": [], "horizon": [h["id"] for h in doc["horizon"]],
                      "excluded": [x["id"] for x in doc["excluded"]], "reached_when": {},
                      "reached": None, "reached_count": 0, "distance": None, "distance_path": [],
                      "drift": [], "conditions": [], "edges": [],
                      "panels": {"done": [], "open": [], "unbound": [], "retired": [],
                                 "horizon": [h["id"] for h in doc["horizon"]],
                                 "excluded": [x["id"] for x in doc["excluded"]]}})
        return entry
    conds = doc["conditions"]
    statuses = {c["id"]: c["status"] for c in conds}
    panels = {"done": [], "open": [], "unbound": [], "retired": [],
              "horizon": [h["id"] for h in doc["horizon"]],
              "excluded": [x["id"] for x in doc["excluded"]]}
    for c in conds:
        key = "retired" if c["status"].startswith("retired:") else c["status"]
        panels[key].append(c["id"])
    path_ids, dist = distance_path(doc)
    rw = {t: statuses.get(t) for t in doc["reached_when"]}
    reached_count = sum(1 for t, s in rw.items()
                        if s == "done" or (s or "").startswith("retired:"))
    entry.update({
        "statuses": statuses,
        "effective": {c["id"]: c["effective"] for c in conds if c["resolver"] == "hypothesis"},
        "needs_met": {c["id"]: bool(c["needs_met"]) for c in conds},
        "frontier": [{"id": f["id"], "verb": f["verb"]} for f in doc["frontier"]],
        "claimed_fresh": [{"id": k["id"], "lane": k["lane"]} for k in doc["claimed_fresh"]],
        "retired": list(doc["retired"]),
        "horizon": panels["horizon"], "excluded": panels["excluded"],
        "horizon_lines": [{"id": h["id"], "date": h["date"], "text": h["text"],
                           "graduated_to": h["graduated_to"]} for h in doc["horizon"]],
        "excluded_lines": [{"id": x["id"], "text": x["text"], "banks": x["banks"]}
                           for x in doc["excluded"]],
        "reached_when": rw, "reached": bool(doc["reached"]), "reached_count": reached_count,
        "distance": doc["distance"], "distance_path": path_ids,
        "drift": drift_for(ncs, doc, repo, sha),
        "conditions": [{"id": c["id"], "text": c["text"], "resolver": c["resolver"],
                        "bound": c["bound"], "effective": c["effective"], "status": c["status"],
                        "verb": c.get("verb"), "needs": c["needs"],
                        "needs_met": bool(c["needs_met"]), "in_frontier": bool(c["in_frontier"]),
                        "claimed_fresh": bool(c["claimed_fresh"])} for c in conds],
        "edges": [{"from": n["id"], "to": c["id"], "outcome": n["outcome"]}
                  for c in conds for n in c["needs"]],
        "panels": panels,
    })
    if dist != doc["distance"]:
        # the path walk and spec 1's DP disagree only if the file has dangling needs; surface it
        entry["distance_path_note"] = "path length %d != distance %s" % (dist, doc["distance"])
    return entry


# ---------------------------------------------------------------- stops --------------------

def load_stops(repo, stops_arg):
    """-> ordered list of (sha, subject) with HEAD last (appended when absent)."""
    head = repo.resolve("HEAD")
    if head is None:
        raise RuntimeError("cannot resolve HEAD")
    shas = []
    if stops_arg:
        with open(stops_arg, encoding="utf-8") as fh:
            data = json.load(fh)
        items = data["stops"] if isinstance(data, dict) else data
        for it in items:
            s = it["sha"] if isinstance(it, dict) else str(it)
            full = repo.resolve(s)
            if full is None:
                raise RuntimeError("cannot resolve stop %s" % s)
            if full not in shas:
                shas.append(full)
    else:
        code, out = repo.git("log", "--reverse", "--format=%H%x1f%s", head)
        matched = []
        for line in (out or "").splitlines():
            if "\x1f" not in line:
                continue
            sha, subj = line.split("\x1f", 1)
            if STOP_RULE.search(subj):
                matched.append(sha)
        shas = matched[-DEFAULT_MAX_STOPS:]
    if head not in shas:
        shas.append(head)
    out = []
    for s in shas:
        code, subj = repo.git("log", "-1", "--format=%s", s)
        out.append((s, (subj or "").strip()))
    return out, head


def build_block(ncs, repo, root, path, stops, head, now, ttl_s, today, today_label):
    entries = []
    for i, (sha, subject) in enumerate(stops):
        entries.append(entry_for(ncs, repo, sha, path, i, subject, sha == head, root, now,
                                 ttl_s, today))
    blob = repo.show(head, path)
    meta = {"compiler": COMPILER_PATH, "source": path,
            "source_sha256": sha256_text(blob) if blob is not None else None,
            "head": head, "now": int(now), "ttl_s": int(ttl_s), "today": today_label,
            "n_stops": len(entries), "stop_rule": "KEPT|DISCARDED|decision: plus HEAD"}
    return {"meta": meta, "stops": entries}


def block_json(block):
    return json.dumps(block, sort_keys=True, separators=(",", ":")).replace("</", "<\\/")


# ---------------------------------------------------------------- render -------------------

CSS = """
:root{--bg:#f7f7f5;--ink:#1e1e1e;--mute:#6b6b6b;--line:#d9d9d4;--card:#fff;
--done:#2f7d4f;--open:#2a5fa8;--unbound:#8a6d1f;--retired:#8a8a8a;--claim:#7a3fa0;
--frontier:#e3f0ff;--drift:#b3261e;--horizon:#5c6b7a;--excluded:#5a5a5a}
*{box-sizing:border-box}body{margin:0;font:14px/1.45 -apple-system,Helvetica,Arial,sans-serif;
color:var(--ink);background:var(--bg)}main{max-width:1180px;margin:0 auto;padding:20px 24px 48px}
h1{font-size:20px;margin:0 0 4px}h2{font-size:13px;text-transform:uppercase;letter-spacing:.06em;
color:var(--mute);margin:22px 0 8px}.dest{font-size:16px;margin:0 0 14px}
.replay{display:flex;gap:12px;align-items:center;padding:12px;border:1px solid var(--line);
background:var(--card);border-radius:6px}.replay input{flex:1}.replay .lbl{min-width:340px;
font-family:ui-monospace,Menlo,monospace;font-size:12px;color:var(--mute)}
.stats{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-top:12px}
.stat{background:var(--card);border:1px solid var(--line);border-radius:6px;padding:10px 12px}
.stat b{display:block;font-size:22px}.stat span{color:var(--mute);font-size:12px}
.chips{display:flex;flex-wrap:wrap;gap:6px}.chip{display:inline-block;padding:2px 8px;
border-radius:12px;font-size:12px;border:1px solid var(--line);background:var(--card)}
.chip.done{border-color:var(--done);color:var(--done)}.chip.open{border-color:var(--open);
color:var(--open)}.chip.retired{border-color:var(--retired);color:var(--retired)}
.chip.unbound{border-color:var(--unbound);color:var(--unbound)}
.chip.drift{border-color:var(--drift);color:var(--drift);font-weight:600}
.chip.none{color:var(--mute);border-style:dashed}
.panels{display:grid;grid-template-columns:repeat(2,1fr);gap:12px}
.panel{background:var(--card);border:1px solid var(--line);border-radius:6px;padding:10px 12px}
.panel h3{margin:0 0 8px;font-size:13px}.panel h3 small{color:var(--mute);font-weight:400}
.card{border-top:1px solid var(--line);padding:6px 0}.card:first-of-type{border-top:0}
.card .id{font-family:ui-monospace,Menlo,monospace;font-weight:600;margin-right:6px}
.card .meta{color:var(--mute);font-size:12px}.card.frontier{background:var(--frontier);
margin:0 -6px;padding:6px}.card .verb{color:var(--open);font-weight:600}
.card .claim{color:var(--claim);font-weight:600}.status-done .id{color:var(--done)}
.status-open .id{color:var(--open)}.status-unbound .id{color:var(--unbound)}
.status-retired .id{color:var(--retired)}svg{width:100%;height:auto;background:var(--card);
border:1px solid var(--line);border-radius:6px}.node text{font:11px ui-monospace,Menlo,monospace}
.empty{color:var(--mute);font-style:italic}footer{margin-top:28px;color:var(--mute);font-size:12px}
"""

JS = r"""
(function(){
var data=JSON.parse(document.getElementById('north-star-data').textContent);
var stops=data.stops, meta=data.meta;
var el=function(id){return document.getElementById(id)};
function clear(n){while(n.firstChild)n.removeChild(n.firstChild)}
function mk(tag,cls,text){var e=document.createElement(tag);if(cls)e.className=cls;
 if(text!==undefined&&text!==null)e.textContent=String(text);return e}
function statusKey(s){return s&&s.indexOf('retired:')===0?'retired':s}
function chip(text,cls){return mk('span','chip '+(cls||''),text)}
function card(c){var d=mk('div','card status-'+statusKey(c.status)+(c.in_frontier?' frontier':''));
 var line=mk('div');line.appendChild(mk('span','id',c.id));line.appendChild(mk('span','',c.text));
 d.appendChild(line);var m=mk('div','meta');
 var eff=c.effective&&c.effective!==c.bound?(' -> '+c.effective):'';
 m.appendChild(mk('span','',c.resolver+' '+c.bound+eff+' | '+c.status));
 if(c.needs&&c.needs.length){m.appendChild(mk('span','',' | needs '+c.needs.map(function(n){
  return n.id+(n.outcome?(':'+n.outcome):'')}).join(', ')))}
 if(c.in_frontier){m.appendChild(mk('span','',' | frontier '));m.appendChild(mk('span','verb',c.verb))}
 if(c.claimed_fresh){m.appendChild(mk('span','',' | '));m.appendChild(mk('span','claim','claimed fresh'))}
 d.appendChild(m);return d}
function panel(title,items,render){var p=mk('div','panel');var h=mk('h3',null,title+' ');
 var s=mk('small',null,'('+items.length+')');h.appendChild(s);p.appendChild(h);
 if(!items.length){p.appendChild(mk('div','empty','none'));}
 items.forEach(function(it){p.appendChild(render(it))});return p}
function edgesSvg(e){var conds=e.conditions||[];if(!conds.length)return mk('div','empty','no conditions');
 var idx={};conds.forEach(function(c,i){idx[c.id]=i});var depth={};
 function d(id,seen){if(depth[id]!==undefined)return depth[id];if(seen[id])return 0;seen[id]=1;
  var c=conds[idx[id]];var best=0;(c.needs||[]).forEach(function(n){if(idx[n.id]!==undefined)best=Math.max(best,d(n.id,seen)+1)});
  depth[id]=best;return best}
 conds.forEach(function(c){d(c.id,{})});var maxD=0;conds.forEach(function(c){maxD=Math.max(maxD,depth[c.id])});
 var colW=110,rowH=64,W=Math.max(400,colW*conds.length+20),H=rowH*(maxD+1)+30;
 var NS='http://www.w3.org/2000/svg';var svg=document.createElementNS(NS,'svg');
 svg.setAttribute('viewBox','0 0 '+W+' '+H);var defs=document.createElementNS(NS,'defs');
 var mk_=document.createElementNS(NS,'marker');mk_.setAttribute('id','arr');mk_.setAttribute('markerWidth','8');
 mk_.setAttribute('markerHeight','8');mk_.setAttribute('refX','6');mk_.setAttribute('refY','3');mk_.setAttribute('orient','auto');
 var pth=document.createElementNS(NS,'path');pth.setAttribute('d','M0,0 L6,3 L0,6 z');pth.setAttribute('fill','#666');
 mk_.appendChild(pth);defs.appendChild(mk_);svg.appendChild(defs);
 var pos={};conds.forEach(function(c,i){pos[c.id]={x:20+i*colW+40,y:20+depth[c.id]*rowH+14}});
 var colors={done:'#2f7d4f',open:'#2a5fa8',unbound:'#8a6d1f',retired:'#8a8a8a'};
 (e.edges||[]).forEach(function(ed){var a=pos[ed.from],b=pos[ed.to];if(!a||!b)return;
  var l=document.createElementNS(NS,'line');l.setAttribute('x1',a.x);l.setAttribute('y1',a.y+12);
  l.setAttribute('x2',b.x);l.setAttribute('y2',b.y-12);l.setAttribute('stroke','#666');l.setAttribute('stroke-width','1.2');
  l.setAttribute('marker-end','url(#arr)');if(ed.outcome){l.setAttribute('stroke-dasharray','4 3')}svg.appendChild(l);
  if(ed.outcome){var t=document.createElementNS(NS,'text');t.setAttribute('x',(a.x+b.x)/2+4);t.setAttribute('y',(a.y+b.y)/2);
   t.setAttribute('font-size','10');t.setAttribute('fill','#666');t.textContent=ed.outcome;svg.appendChild(t)}});
 conds.forEach(function(c){var p=pos[c.id];var g=document.createElementNS(NS,'g');g.setAttribute('class','node');
  var r=document.createElementNS(NS,'rect');r.setAttribute('x',p.x-34);r.setAttribute('y',p.y-12);r.setAttribute('width','68');
  r.setAttribute('height','24');r.setAttribute('rx','5');r.setAttribute('fill',c.in_frontier?'#e3f0ff':'#fff');
  r.setAttribute('stroke',colors[statusKey(c.status)]||'#999');r.setAttribute('stroke-width',c.in_frontier?'2':'1.2');g.appendChild(r);
  var t=document.createElementNS(NS,'text');t.setAttribute('x',p.x);t.setAttribute('y',p.y+4);t.setAttribute('text-anchor','middle');
  t.setAttribute('fill',colors[statusKey(c.status)]||'#333');t.textContent=c.id+(c.claimed_fresh?' *':'');g.appendChild(t);svg.appendChild(g)});
 return svg}
function render(i){var e=stops[i];el('stoplbl').textContent='stop '+(i+1)+'/'+stops.length+' | '+e.sha.slice(0,10)+(e.is_head?' (HEAD)':'')+' | '+e.subject;
 el('dest').textContent=e.destination||'(missing destination)';
 var byId={};(e.conditions||[]).forEach(function(c){byId[c.id]=c});
 el('distance').textContent=e.distance===null?'-':e.distance;
 el('reached').textContent=e.reached_count+'/'+(e.reached_when_ids||[]).length;
 el('frontiern').textContent=e.frontier.length+(e.claimed_fresh.length?(' +'+e.claimed_fresh.length+' claimed'):'');
 var nxt=e.frontier.length?(e.frontier[0].id+' '+e.frontier[0].verb):(e.claimed_fresh.length?(e.claimed_fresh[0].id+' claimed @'+e.claimed_fresh[0].lane):(e.reached?'reached':'-'));
 el('next').textContent=nxt;
 var rw=el('reachedwhen');clear(rw);(e.reached_when_ids||[]).forEach(function(id){rw.appendChild(chip(id+' '+(e.reached_when[id]||'?'),statusKey(e.reached_when[id])))});
 var fr=el('frontier');clear(fr);if(!e.frontier.length)fr.appendChild(chip('empty','none'));
 e.frontier.forEach(function(f){fr.appendChild(chip(f.id+' '+f.verb,'open'))});
 e.claimed_fresh.forEach(function(k){fr.appendChild(chip(k.id+' claimed @'+k.lane,'open'))});
 var dr=el('drift');clear(dr);if(!e.drift.length)dr.appendChild(chip('no drift','none'));
 e.drift.forEach(function(d){dr.appendChild(chip(d.class+' '+d.id+(d.ref?(' ('+d.ref+')'):''),'drift'))});
 var fi=el('findings');clear(fi);(e.findings||[]).forEach(function(f){fi.appendChild(chip(f.class+' L'+f.line+' '+f.message,'drift'))});
 var pn=el('panels');clear(pn);var P=e.panels;
 pn.appendChild(panel('done',P.done.map(function(id){return byId[id]}),card));
 pn.appendChild(panel('open',P.open.map(function(id){return byId[id]}),card));
 pn.appendChild(panel('unbound',P.unbound.map(function(id){return byId[id]}),card));
 pn.appendChild(panel('retired',P.retired.map(function(id){return byId[id]}),card));
 pn.appendChild(panel('horizon',e.horizon_lines||[],function(h){var d=mk('div','card');d.appendChild(mk('span','id',h.id));
  d.appendChild(mk('span','',h.text+' ('+h.date+')'+(h.graduated_to?(' -> '+h.graduated_to):'')));return d}));
 pn.appendChild(panel('excluded',e.excluded_lines||[],function(x){var d=mk('div','card');d.appendChild(mk('span','id',x.id));
  d.appendChild(mk('span','',x.text+' | banks: '+x.banks));return d}));
 var ed=el('edges');clear(ed);ed.appendChild(edgesSvg(e));
 var dp=el('dpath');dp.textContent=(e.distance_path&&e.distance_path.length)?e.distance_path.join(' -> '):'-';
}
var slider=el('replay');slider.max=stops.length-1;slider.value=stops.length-1;
slider.addEventListener('input',function(){render(parseInt(slider.value,10))});
render(stops.length-1);
})();
"""


def render_html(block, data_json):
    meta = block["meta"]
    header = "\n".join([
        "<!-- north-star-progress checkpoint",
        "     compiler: %s" % meta["compiler"],
        "     source: %s sha256:%s" % (meta["source"], meta["source_sha256"] or "0" * 64),
        "     head: %s" % meta["head"],
        "     clock: now=%d ttl_s=%d today=%s" % (meta["now"], meta["ttl_s"], meta["today"]),
        "     stops: %d" % meta["n_stops"],
        "     data: sha256:%s" % sha256_text(data_json),
        "     freshness check: python3 %s --check %s --repo <root> -->" % (meta["compiler"],
                                                                          OUTPUT_NAME),
    ])
    title = "north-star progress: %s" % html.escape(os.path.splitext(
        os.path.basename(meta["source"]))[0])
    parts = [
        "<!DOCTYPE html>", '<html lang="en"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        "<title>%s</title>" % title, header, "<style>%s</style></head><body><main>" % CSS,
        "<h1>%s</h1>" % title,
        '<p class="dest" id="dest"></p>',
        '<div class="replay"><span>replay</span><input type="range" id="replay" min="0" max="0" value="0">'
        '<span class="lbl" id="stoplbl"></span></div>',
        '<div class="stats"><div class="stat"><b id="distance"></b><span>distance (open on the longest needs path)</span></div>'
        '<div class="stat"><b id="reached"></b><span>reached-when done or retired</span></div>'
        '<div class="stat"><b id="frontiern"></b><span>frontier</span></div>'
        '<div class="stat"><b id="next"></b><span>next step</span></div></div>',
        "<h2>reached-when</h2>", '<div class="chips" id="reachedwhen"></div>',
        "<h2>frontier</h2>", '<div class="chips" id="frontier"></div>',
        "<h2>drift</h2>", '<div class="chips" id="drift"></div>',
        '<div class="chips" id="findings"></div>',
        "<h2>needs edges</h2>", '<div id="edges"></div>',
        '<p class="meta">distance path: <span id="dpath"></span></p>',
        "<h2>panels</h2>", '<div class="panels" id="panels"></div>',
        "<footer>source %s at %s | clock now=%d | %d stops | compiled by %s | every number "
        "above is read from the inline data block, which is re-derivable from committed bytes "
        "(--check).</footer>" % (html.escape(meta["source"]), meta["head"][:12], meta["now"],
                                 meta["n_stops"], html.escape(meta["compiler"])),
        '<script type="application/json" id="north-star-data">%s</script>' % data_json,
        "<script>%s</script>" % JS,
        "</main></body></html>\n",
    ]
    return "\n".join(parts)


def atomic_write(path, text):
    d = os.path.dirname(os.path.abspath(path)) or "."
    fd, tmp = tempfile.mkstemp(prefix=".nsp-", dir=d)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


# ---------------------------------------------------------------- compile / check ----------

def repo_root_for(path_hint, repo_arg):
    if repo_arg:
        return os.path.realpath(repo_arg)
    start = os.path.dirname(os.path.realpath(path_hint)) if path_hint else os.getcwd()
    p = subprocess.run(["git", "-C", start, "rev-parse", "--show-toplevel"], capture_output=True,
                       text=True, timeout=30)
    if p.returncode != 0:
        raise RuntimeError("not inside a git repository (use --repo)")
    return os.path.realpath(p.stdout.strip())


def rel_in_repo(path, root):
    rp = os.path.realpath(path)
    if rp.startswith(root + os.sep):
        return os.path.relpath(rp, root).replace(os.sep, "/")
    return path.replace(os.sep, "/")


def compile_page(ncs, root, rel_path, stops_arg, now, ttl_s, today, today_label, out_path):
    repo = ncs.Repo(root)
    stops, head = load_stops(repo, stops_arg)
    block = build_block(ncs, repo, root, rel_path, stops, head, now, ttl_s, today, today_label)
    data_json = block_json(block)
    page = render_html(block, data_json)
    atomic_write(out_path, page)
    return block, sha256_text(page)


def check_page(ncs, page_path, root):
    """-> (exit_code, message)."""
    try:
        with open(page_path, encoding="utf-8") as fh:
            page = fh.read()
    except OSError as e:
        return 2, "error: %s" % e
    hdr = {}
    for k, rx in HEADER_RE.items():
        m = rx.search(page)
        if not m:
            return 2, "error: header line %r unreadable" % k
        hdr[k] = m.groups()
    m = DATA_BLOCK_RE.search(page)
    if not m:
        return 2, "error: data block missing"
    try:
        block = json.loads(m.group(1).replace("<\\/", "</"))
    except ValueError:
        return 2, "error: data block unparseable"
    recorded_digest = hdr["data"][0]
    if sha256_text(m.group(1)) != recorded_digest:
        return 1, "stale: data block bytes do not match the recorded digest"
    repo = ncs.Repo(root)
    head = repo.resolve("HEAD")
    if head is None:
        return 2, "error: cannot resolve HEAD in %s" % root
    rel_path = hdr["source"][0]
    now, ttl_s, today_label = int(hdr["clock"][0]), int(hdr["clock"][1]), hdr["clock"][2]
    today = parse_today(today_label)
    shas = []
    for e in block.get("stops", []):
        s = repo.resolve(e.get("sha", ""))
        if s is None:
            return 2, "error: recorded stop %s unresolvable" % str(e.get("sha"))[:12]
        if s not in shas:
            shas.append(s)
    if head not in shas:
        shas.append(head)
    stops = []
    for s in shas:
        code, subj = repo.git("log", "-1", "--format=%s", s)
        stops.append((s, (subj or "").strip()))
    try:
        fresh = build_block(ncs, repo, root, rel_path, stops, head, now, ttl_s, today,
                            today_label)
    except RuntimeError as e:
        return 2, "error: %s" % e
    current = sha256_text(block_json(fresh))
    reasons = []
    if hdr["head"][0] != head:
        reasons.append("HEAD moved %s -> %s" % (hdr["head"][0][:12], head[:12]))
    if (fresh["meta"]["source_sha256"] or "0" * 64) != hdr["source"][0 + 1]:
        reasons.append("source bytes changed")
    if current != recorded_digest:
        reasons.append("derived data changed")
    if reasons:
        return 1, "stale: " + "; ".join(reasons)
    return 0, "fresh"


def parse_today(label):
    try:
        return datetime.date.fromisoformat(label)
    except (ValueError, TypeError):
        return datetime.date(1970, 1, 1)


# ---------------------------------------------------------------- selftest -----------------

def _git(dest, args, env):
    p = subprocess.run(["git", "-C", dest] + args, capture_output=True, text=True, timeout=60,
                       env=env)
    if p.returncode != 0:
        raise RuntimeError("git %s: %s" % (args[:2], p.stderr.strip()[:300]))
    return p.stdout.strip()


def _commit_file(ncs, dest, rel, content, message, minute):
    env = ncs.scenario_env("%sT01:%02d:00Z" % (ncs.SCENARIO_DATE, minute))
    p = os.path.join(dest, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(content)
    _git(dest, ["add", "-A"], env)
    _git(dest, ["commit", "-q", "--no-verify", "-m", message], env)
    return _git(dest, ["rev-parse", "HEAD"], env)


def _extract_block(page_path):
    with open(page_path, encoding="utf-8") as fh:
        m = DATA_BLOCK_RE.search(fh.read())
    return json.loads(m.group(1).replace("<\\/", "</"))


def _partition_ok(entry):
    P = entry["panels"]
    seen = []
    for k in ("done", "open", "unbound", "retired", "horizon", "excluded"):
        seen.extend(P[k])
    ids = set(entry["statuses"]) | set(entry["horizon"]) | set(entry["excluded"])
    return sorted(seen) == sorted(ids) and len(seen) == len(set(seen))


def selftest():
    ncs = load_ncs()
    checks = []

    def ok(name, cond, detail=""):
        checks.append((name, bool(cond), detail))

    tmp = tempfile.mkdtemp(prefix="nsp-selftest-")
    now = 1788523200
    # spec 1's scenario file names C-06 (retired:C-03 after event 2) in reached-when, which is
    # a REACHED-WHEN-RETIRED contradiction under this compiler's drift classes; the selftest's
    # clean base drops it so "zero chips on the clean page" is a real check, not a seeded one.
    base_text = ncs.NORTH_STAR_FIXTURE.replace("reached-when: C-02, C-06, C-08",
                                               "reached-when: C-02, C-08")
    try:
        lab = os.path.join(tmp, "lab")
        ncs.scenario_build(lab, north_star_override=base_text)
        for n in range(1, 10):
            ncs.scenario_play(lab, n)
        ns = ncs.SCENARIO["north_star"]
        out = os.path.join(tmp, OUTPUT_NAME)
        _, d1 = compile_page(ncs, lab, ns, None, now, 1800, datetime.date(2026, 9, 4),
                             "2026-09-04", out)
        _, d2 = compile_page(ncs, lab, ns, None, now, 1800, datetime.date(2026, 9, 4),
                             "2026-09-04", out)
        os.unlink(out)
        _, d3 = compile_page(ncs, lab, ns, None, now, 1800, datetime.date(2026, 9, 4),
                             "2026-09-04", out)
        ok("double compile byte-identical", d1 == d2, "%s %s" % (d1[:12], d2[:12]))
        ok("remove-and-recompile byte-identical", d1 == d3, "%s %s" % (d1[:12], d3[:12]))
        block = _extract_block(out)
        stops = block["stops"]
        ok("stop rule yields matching commits plus HEAD (6)", len(stops) == 6, str(len(stops)))
        ok("clean page renders zero drift chips at every stop",
           all(not e["drift"] for e in stops))
        ok("exact panel partition at every stop", all(_partition_ok(e) for e in stops))
        ok("retired and frontier never intersect",
           all(not (set(e["retired"]) & set(f["id"] for f in e["frontier"])) for e in stops))
        rc, msg = check_page(ncs, out, lab)
        ok("--check exits 0 on the clean tree", rc == 0, msg)
        # claim overlay at HEAD: C-02's effective resolver is H-905 after the refine
        lane_dir = os.path.join(lab, "experiments", "runs", "H-905")
        os.makedirs(lane_dir, exist_ok=True)
        with open(os.path.join(lane_dir, "LANE-STATE.json"), "w", encoding="utf-8") as fh:
            json.dump({"lane": "H-905", "heartbeat_unix": now - 10, "ttl_s": 1800}, fh)
        out2 = os.path.join(tmp, "claimed.html")
        compile_page(ncs, lab, ns, None, now, 1800, datetime.date(2026, 9, 4), "2026-09-04",
                     out2)
        head_e = _extract_block(out2)["stops"][-1]
        ok("fresh claim moves C-02 to claimed_fresh at HEAD",
           "C-02" in [k["id"] for k in head_e["claimed_fresh"]]
           and "C-02" not in [f["id"] for f in head_e["frontier"]])
        with open(os.path.join(lane_dir, "LANE-STATE.json"), "w", encoding="utf-8") as fh:
            json.dump({"lane": "H-905", "heartbeat_unix": now - 100000, "ttl_s": 1800}, fh)
        compile_page(ncs, lab, ns, None, now, 1800, datetime.date(2026, 9, 4), "2026-09-04",
                     out2)
        head_e = _extract_block(out2)["stops"][-1]
        ok("stale claim returns C-02 to the frontier",
           "C-02" in [f["id"] for f in head_e["frontier"]])
        shutil.rmtree(lane_dir)
        rc, msg = check_page(ncs, out, lab)
        ok("--check still 0 after the overlay is removed", rc == 0, msg)
        # flip seed: H-901 kept -> draft (C-01 done -> open), committed
        _commit_file(ncs, lab, "hypotheses/H-901-alpha-channel.md",
                     ncs._spec("H-901", "alpha-channel", "alpha channel", "draft"),
                     "H-901 status flipped (selftest flip seed)", 1)
        rc, msg = check_page(ncs, out, lab)
        ok("--check exits exactly 1 after one seeded status flip", rc == 1, "%s %s" % (rc, msg))
        # drift seed in a second throwaway lab: three classes, --strict clean
        lab2 = os.path.join(tmp, "lab2")
        ncs.scenario_build(lab2, north_star_override=base_text)
        for n in range(1, 10):
            ncs.scenario_play(lab2, n)
        # X-01 -> H-905 (draft at HEAD, bound by no row: BANKS-ACTIVE only); C-04 is
        # retired:C-03 (REACHED-WHEN-RETIRED); X-02 -> H-903 (C-03's bound, discarded:
        # DUAL-BINDING only)
        drift_text = (base_text
                      .replace("reached-when: C-02, C-08", "reached-when: C-02, C-04, C-08")
                      .replace("banks: H-242", "banks: H-905")
                      .replace("banks: H-244", "banks: H-903"))
        _commit_file(ncs, lab2, ns, drift_text, "north-star edit (selftest drift seed)", 2)
        doc = ncs.parse_north_star(drift_text, ns)
        ok("drift seed passes spec 1's lint (zero hard findings)", not doc["findings"],
           json.dumps(doc["findings"]))
        out3 = os.path.join(tmp, "drift.html")
        compile_page(ncs, lab2, ns, None, now, 1800, datetime.date(2026, 9, 4), "2026-09-04",
                     out3)
        head_e = _extract_block(out3)["stops"][-1]
        chips = sorted((d["class"], d["id"]) for d in head_e["drift"])
        want = [("BANKS-ACTIVE", "X-01"), ("DUAL-BINDING", "X-02"), ("REACHED-WHEN-RETIRED", "C-04")]
        ok("exactly three drift chips, one per seeded class", chips == want, str(chips))
        ok("--check on a missing page exits 2", check_page(ncs, os.path.join(tmp, "nope.html"),
                                                            lab)[0] == 2)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    failed = [c for c in checks if not c[1]]
    for name, passed, detail in checks:
        print("%s %s%s" % ("ok  " if passed else "FAIL", name,
                           ("  [%s]" % detail) if (detail and not passed) else ""))
    print("selftest: %d checks, %d failed" % (len(checks), len(failed)))
    return 0 if not failed else 1


# ---------------------------------------------------------------- main ---------------------

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("north_star", nargs="?", help="path to the north-star file")
    ap.add_argument("--repo")
    ap.add_argument("--stops")
    ap.add_argument("--now", type=int)
    ap.add_argument("--ttl-s", type=int, default=1800)
    ap.add_argument("--today")
    ap.add_argument("--out")
    ap.add_argument("--check", metavar="PAGE")
    ap.add_argument("--selftest", action="store_true")
    o = ap.parse_args(argv)

    if o.selftest:
        return selftest()
    try:
        ncs = load_ncs()
        if o.check:
            root = repo_root_for(None, o.repo)
            rc, msg = check_page(ncs, o.check, root)
            print(msg)
            return rc
        if not o.north_star:
            ap.error("north-star file required (or --check / --selftest)")
        root = repo_root_for(o.north_star, o.repo)
        rel_path = rel_in_repo(o.north_star, root)
        now = o.now if o.now is not None else int(time.time())
        today_label = o.today if o.today else "unpinned"
        today = parse_today(o.today) if o.today else datetime.date(1970, 1, 1)
        out_path = o.out or os.path.join(os.path.dirname(os.path.abspath(o.north_star)),
                                         OUTPUT_NAME)
        block, digest = compile_page(ncs, root, rel_path, o.stops, now, o.ttl_s, today,
                                     today_label, out_path)
        head_e = block["stops"][-1]
        print("compiled %s: %d stops, HEAD %s, distance %s, reached %s/%d, frontier %d, "
              "drift %d, page sha256 %s" % (
                  out_path, len(block["stops"]), block["meta"]["head"][:12], head_e["distance"],
                  head_e["reached_count"], len(head_e["reached_when_ids"]),
                  len(head_e["frontier"]), len(head_e["drift"]), digest[:12]))
        return 0
    except RuntimeError as e:
        print("compile-north-star-progress: %s" % e, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
