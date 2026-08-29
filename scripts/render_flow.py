#!/usr/bin/env python3
"""render_flow.py — headless PNG renderer for a saved FLOW artifact (*.flow.json).

The flow-view artifact (journey-composer contract key decision 4; acceptance
item 11), v1.1 per the amended layout rulings (AM-1..AM-5): within a
capsule, cards occupy role columns — horizontal position encodes DEPENDENCY ONLY —
and same-role parallels STACK VERTICALLY at (capsule × lane × role), alphabetical
within a stack, so connectors become short parallel runs instead of under-box
crossings (AM-1/AM-2); lane bands and capsules grow dynamically to fit stacks
(AM-3); no ghost geometry exists anywhere — the +N badge is the sole disclosure
affordance and hides at zero (AM-4); and every layout passes the AM-5 hard collision
assert (nothing inside or behind a capsule's footprint except its own members).

AMENDED per the label-legibility contract (§2.2 ships whatever the verdict):
every drawn label is a first-class rect from flow_geometry — stitch verb + to-slice
pattern chip, intra verb, capsule header band, V-C legend — the collision assert
covers labels, connector_clashes is a hard assert, and parallel stitches into one
target take fixed per-track offsets by sorted key (no more left-margin bundle).
--label-mode selects the rung-1 treatment over ONE shared substrate:
  clearance  V-A: labels on/beside their edge, padded backplates, greedy
             station deconfliction with thin leader lines for displaced slots
  routed     V-B: orthogonal corridor routing (gutter channels + a reserved
             highway strip), labels at stations on their track's horizontal run
  minimal    V-C: clean strokes, no persistent edge labels, always-on legend
             block; --hover <entity-id> renders the hover-state exemplar (the
             stitches consuming that card highlighted with their labels shown)

Still drawn as before: lane-pinned stitch-depth columns (LO-2/LO-3), capsule halos
(CP-3 header vocabulary), coalesced input stitches (CP-4), per-capsule repeated
outputs (C7), un-stitched inputs with upstream stubs (CP-5), +N badges on frontier
output cards (CP-6), EM-L10/D12 gap badges (FA-7a), collapsed capsule chips (CP-9).

Reuse chain (nothing copied): cards, edge lines and lane chrome come from
render_path.py (render_lanes/render_slices/render_board underneath); ALL pixel
geometry comes from flow_composer.flow_geometry — one pure source shared with the
selftest asserts; this file only draws the returned rects (FA-5) and REFUSES
(FlowError) to draw any text wider than its reserved rect, so a font drift fails
loud instead of shipping an overlap the geometry lint cannot see.

CLI: render_flow.py --flow <path-or-slug> [--board slice-board.json]
                    [--lanes lanes-layout.json] [--out board-flow.png]
                    [--label-mode clearance|routed|minimal] [--hover <entity-id>]
Deterministic: pure function of (flow file, board, lanes layout, label mode, hover);
stdlib + Pillow, no wall-clock, no RNG — two runs are byte-identical.
"""
import argparse
import json
import pathlib
import sys

from PIL import Image, ImageDraw

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import importlib.util


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


rp = load_module(HERE / "render_path.py", "render_path_for_flow")
rl, rs, rb = rp.rl, rp.rs, rp.rb
fc = load_module(HERE / "flow_composer.py", "flow_composer_for_render")

# ------------------------------------------------- geometry config (fc.GEO mirror)
CARD_W, CARD_H = rp.CARD_W, rp.CARD_H          # 190 x 76, the path-render card
CHIP_H = 48                                    # collapsed capsule chip node
CAPS_HDR = 42                                  # capsule header row (chip + dots)
TITLE_H = 56
STAT_H = 34
DEPTH_HDR_H = 64
CAPSULE_TINT_ALPHA = 0.16                      # CP-3 low-alpha status tint (flag F10)
INK = rp.INK
GHOST = rp.GHOST
CHIP_ACCENT = {v: fc.PATTERN_ACCENT[k] for k, v in fc.PATTERN_CHIP.items()}


def dash_line(draw, p0, p1, width, color):
    """Straight dashed leg (the render_path dash walk, arrowhead-free for the
    non-final legs of a routed stitch)."""
    import math
    dx, dy = p1[0] - p0[0], p1[1] - p0[1]
    dist = max(1.0, math.hypot(dx, dy))
    n = max(1, int(dist // 12))
    for i in range(0, n, 2):
        a = (p0[0] + dx * i / n, p0[1] + dy * i / n)
        b = (p0[0] + dx * min(i + 1, n) / n, p0[1] + dy * min(i + 1, n) / n)
        draw.line([a, b], fill=color, width=width)


def fit_text(draw, font, text, max_w, what):
    """§2.2 pixel-truth guard: refuse to draw text wider than its reserved rect
    (geometry reserves via flow_composer._BADGE_W; a font swap must fail loud)."""
    tw = draw.textlength(text, font=font)
    if tw > max_w + 0.5:
        raise fc.FlowError(
            "drawn text exceeds its reserved rect (%s: %.1fpx > %dpx) — font "
            "drift vs flow_composer._BADGE_W" % (what, tw, max_w))
    return tw


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--flow", required=True,
                    help="path to a *.flow.json, or a slug resolved in views/")
    ap.add_argument("--board", default=str(HERE / "slice-board.json"))
    ap.add_argument("--lanes", default=str(HERE / "lanes-layout.json"))
    ap.add_argument("--out", default=str(HERE / "board-flow.png"))
    ap.add_argument("--label-mode", choices=list(fc.LABEL_MODES),
                    default="clearance",
                    help="rung-1 edge-label treatment (default: clearance)")
    ap.add_argument("--hover", default=None,
                    help="minimal-mode hover exemplar: entity id whose incoming "
                         "stitches surface their verb+chip labels")
    args = ap.parse_args(argv)

    flow_path = pathlib.Path(args.flow)
    if not flow_path.is_file():
        flow_path = HERE / "views" / (args.flow + ".flow.json")
    flow_doc = json.loads(flow_path.read_text(encoding="utf-8"))
    board = json.loads(pathlib.Path(args.board).read_text(encoding="utf-8"))
    lanes_doc = json.loads(pathlib.Path(args.lanes).read_text(encoding="utf-8"))

    index = fc.build_index(board, lanes_doc)
    nspec = fc.normalize_flow_spec(flow_doc, index)   # FA-4: loud fail, never wrong
    res = fc.resolve_flow(index, nspec)
    geo_cfg = {"card_w": CARD_W, "card_h": CARD_H, "chip_h": CHIP_H,
               "caps_hdr": CAPS_HDR, "top": TITLE_H + STAT_H + DEPTH_HDR_H,
               "label_mode": args.label_mode, "hover": args.hover}
    geo = fc.flow_geometry(index, res, geo_cfg)       # AM-1..AM-3 shared geometry
    fc.assert_flow_layout(geo, res)   # AM-5 + §2.2 labels + promoted clash assert

    fonts = {
        "view": rb.load_font(rs.LABEL_FONT_PX["view"]),
        "chip": rb.load_font(rs.CHIP_FONT_PX),
        "lane": rb.load_font(rb.FONT_LANE_PX),
        "badge": rb.load_font(rb.FONT_BADGE_PX),
        "title": rb.load_font(rs.TITLE_FONT_PX),
    }

    em_colors = lanes_doc["meta"]["em_colors"]
    lane_meta = {l["lane"]: l for l in lanes_doc["stream_lanes"]}
    lane_bg = {lid: rb.blend(lm["tint"], rb.BG, rl.LANE_TINT_ALPHA)
               for lid, lm in lane_meta.items()}

    total_w, total_h = geo["size"]
    card_rect = {k: tuple(v) for k, v in geo["cards"].items()}
    chip_rect = {k: tuple(v) for k, v in geo["chips"].items()}
    lane_reserve = {l["lane"]: l["rect"] for l in geo["lane_labels"]}

    # ---------------------------------------------------------------- draw
    img = Image.new("RGB", (total_w, total_h), rb.BG)
    draw = ImageDraw.Draw(img)

    # title bar + review stat strip (FA-7 header strip)
    title = "FLOW — %s (seed %s)" % (flow_doc.get("title") or flow_path.stem,
                                     nspec["seed"])
    draw.rectangle([0, 0, total_w, TITLE_H], fill="#22303C")
    draw.text((16, 14), title, font=fonts["title"], fill="#F7F6F2")
    rev = res["review"]
    stats = "REVIEW   GAPS %d   |   HOTSPOT/DEBT %d   |   GWT %d/%d   |   FRONTIER STUBS %d   |   STITCHES %d" % (
        len(rev["gaps"]), rev["hotspot_debt"]["count"],
        rev["gwt_coverage"]["slices_with_gwt"], rev["gwt_coverage"]["slices_total"],
        rev["frontier_size"], len(res["stitches"]))
    draw.rectangle([0, TITLE_H, total_w, TITLE_H + STAT_H],
                   fill=rb.blend(INK, rb.BG, 0.08))
    draw.text((16, TITLE_H + 9), stats, font=fonts["badge"], fill=INK)

    # stitch-depth header chips (one per column slot; slots widen with AM-5 tracks)
    hdr_y = TITLE_H + STAT_H
    draw.text((geo["columns"][0]["x"] - 130 if geo["columns"] else 16, hdr_y + 8),
              "STITCH DEPTH →", font=fonts["badge"], fill=INK)
    for colg in geo["columns"]:
        cx = colg["x"] + colg["width"] // 2
        c = colg["col"]
        draw.rounded_rectangle([cx - 14, hdr_y + 28, cx + 14, hdr_y + 52], radius=6,
                               fill=INK if c == 0 else rb.blend(INK, rb.BG, 0.12),
                               outline=INK, width=1)
        draw.text((cx - 4, hdr_y + 32), str(c), font=fonts["chip"],
                  fill="#FFFFFF" if c == 0 else INK)

    # lane bands + separators + labels (RE-2/L1-L6 chrome; AM-3: heights are
    # content-grown by the shared geometry; label width guarded by its reserve)
    for i, lg in enumerate(geo["lanes"]):
        lm = lane_meta.get(lg["lane"], {})
        t, h = lg["top"], lg["height"]
        draw.rectangle([0, t, total_w, t + h],
                       fill=lane_bg.get(lg["lane"], rb.BG))
        label = lm.get("label", lg["lane"].upper()) \
            + ("  — we only observe" if lm.get("foreign") else "")
        fit_text(draw, fonts["lane"], label,
                 lane_reserve[lg["lane"]][2], "lane label " + lg["lane"])
        draw.text((10, t + 8), label, font=fonts["lane"], fill=INK)
        if i:
            if lm.get("foreign"):
                draw.line([(0, t), (total_w, t)], fill="#111111", width=rl.HEAVY_W)
            else:
                rl.dotted_hline(draw, 0, total_w, t,
                                rb.blend(INK, rb.BG, rl.SEP_ALPHA), rl.SEP_W)

    # capsule halos (under everything of their interior)
    for cap in res["capsules"]:
        if cap["collapsed"]:
            continue
        x0, y0, w, h = geo["hulls"][cap["slice"]]
        x1, y1 = x0 + w, y0 + h
        top_lane = min((c["lane"] for c in cap["cards"] if c["lane"]),
                       key=lambda l: index["lane_order"].get(l, 99),
                       default=index["lanes"][0] if index["lanes"] else None)
        fill = rb.blend(cap["status_accent"], lane_bg.get(top_lane, rb.BG),
                        CAPSULE_TINT_ALPHA)
        draw.rounded_rectangle([x0, y0, x1, y1], radius=14, fill=fill,
                               outline=rb.blend(cap["status_accent"], rb.BG, 0.75),
                               width=3)
        # header band: slice/<id> chip on the pattern accent + status dot + GWT
        # chip, inside the hull's reserved header band (§2.2 — width asserted)
        chip_txt = cap["slice"]
        tw = draw.textlength(chip_txt, font=fonts["badge"])
        draw.rounded_rectangle([x0 + 10, y0 + 8, x0 + 22 + tw, y0 + 28], radius=6,
                               fill=cap["pattern_accent"], outline=INK, width=1)
        draw.text((x0 + 16, y0 + 11), chip_txt, font=fonts["badge"], fill=INK)
        dx = x0 + 30 + tw
        draw.ellipse([dx, y0 + 13, dx + 12, y0 + 25], fill=cap["status_accent"])
        draw.text((dx + 18, y0 + 11), cap["status"].upper(), font=fonts["badge"],
                  fill=INK)
        sw = draw.textlength(cap["status"].upper(), font=fonts["badge"])
        gw = draw.textlength(cap["gwt_label"], font=fonts["badge"])
        draw.text((dx + 30 + sw, y0 + 11), cap["gwt_label"], font=fonts["badge"],
                  fill=rb.blend(INK, rb.BG, 0.75))
        if 30 + tw + 30 + sw + gw > w + 0.5:  # geometry reserved header_w >= run
            raise fc.FlowError("capsule header run exceeds its hull reserve (%s)"
                               % cap["slice"])

    # collapsed capsule chip nodes (CP-9)
    for cap in res["capsules"]:
        if not cap["collapsed"]:
            continue
        cx, cy = chip_rect[cap["slice"]][:2]
        draw.rounded_rectangle([cx, cy, cx + CARD_W, cy + CHIP_H], radius=10,
                               fill=rb.blend(cap["pattern_accent"], rb.BG, 0.45),
                               outline=INK, width=2)
        draw.text((cx + 10, cy + 6), cap["slice"].replace("slice/", ""),
                  font=fonts["badge"], fill=INK)
        draw.ellipse([cx + 10, cy + 28, cx + 22, cy + 40],
                     fill=cap["status_accent"])
        draw.text((cx + 28, cy + 27), "%s · collapsed" % cap["chip"],
                  font=fonts["badge"], fill=rb.blend(INK, rb.BG, 0.75))

    # intra-capsule connections (thin, dimmed; verbs now ride geo["labels"]) —
    # with same-role stacks these are the AM-2 short parallel runs
    dim = rb.blend(INK, rb.BG, 0.45)
    for cap in res["capsules"]:
        if cap["collapsed"]:
            continue
        for conn in cap["connections"]:
            if conn["from_external"]:
                continue  # drawn as a stitch (or from the stitch source below)
            p0r = card_rect.get(cap["slice"] + "|" + conn["from"])
            p1r = card_rect.get(cap["slice"] + "|" + conn["to"])
            if not p0r or not p1r:
                continue
            p0 = (p0r[0] + CARD_W, p0r[1] + CARD_H // 2)
            p1 = (p1r[0], p1r[1] + CARD_H // 2)
            rp.edge_line(draw, p0, p1, rl.INTRA_W, dim,
                         dashed=conn["type"] == "display")

    # stitches: routes from the shared geometry (CP-4 attachment; §2.2 offsets;
    # sub-column corridor / V-B orthogonal channels), hover-aware in minimal mode
    hover = geo.get("hover")
    hov = set(hover["stitch_indexes"]) if hover else set()
    for i, route in enumerate(geo["stitch_routes"]):
        pts = route.get("points")
        if not pts:
            continue
        st = res["stitches"][i]
        dashed = st["type"] == "display"
        color, width = INK, rl.CROSS_W
        if hover:
            if i in hov:
                width = rl.CROSS_W + 1
            else:
                color = rb.blend(INK, rb.BG, 0.40)
        for k in range(len(pts) - 2):
            if dashed:
                dash_line(draw, tuple(pts[k]), tuple(pts[k + 1]), width, color)
            else:
                draw.line([tuple(pts[k]), tuple(pts[k + 1])], fill=color,
                          width=width)
        rp.edge_line(draw, tuple(pts[-2]), tuple(pts[-1]), width, color,
                     dashed=dashed)

    # leader lines (under the cards: masked where they cross content, visible in
    # the open space where their displaced labels live — external-labeling rule)
    for la in geo["labels"]:
        if not la.get("leader"):
            continue
        x, y, w, h = la["rect"]
        ax, ay = la["anchor"]
        ly = y + h if ay > y + h else y
        draw.line([(x + w // 2, ly), (ax, ay)], fill=GHOST, width=1)

    # cards on top (render_path's card, gap outputs badged EM-L10/D12)
    gap_entities = {g["entity"]: g["class"] for g in res["review"]["gaps"]}
    if res["seed_card"]:
        sc = res["seed_card"]
        x0, y0 = card_rect["@|" + sc["entity"]][:2]
        rp.draw_card(draw, x0, y0, sc["kind"], sc["label"], fonts, em_colors,
                     terminal=gap_entities.get(sc["entity"]))
    for cap in res["capsules"]:
        if cap["collapsed"]:
            continue
        for c in cap["cards"]:
            x0, y0 = card_rect[cap["slice"] + "|" + c["entity"]][:2]
            rp.draw_card(draw, x0, y0, c["kind"], c["label"], fonts, em_colors,
                         terminal=gap_entities.get(c["entity"])
                         if c["is_output"] else None)

    # hover selection ring (V-C exemplar: the consuming card reads as selected)
    if hover:
        sel = next((card_rect[k] for k in sorted(card_rect)
                    if k.split("|", 1)[1] == hover["entity"]), None)
        if sel:
            draw.rounded_rectangle([sel[0] - 5, sel[1] - 5,
                                    sel[0] + CARD_W + 5, sel[1] + CARD_H + 5],
                                   radius=10, outline=INK, width=3)

    # §2.2 labels: backplates + text drawn INSIDE their reserved rects only
    def draw_legend(rect):
        x, y, w, h = rect
        draw.rounded_rectangle([x, y, x + w, y + h], radius=8, fill=rb.BG,
                               outline=rb.blend(INK, rb.BG, 0.5), width=2)
        yy = y + 10
        for verb in fc.LEGEND_VERBS:
            if verb == fc.VERBS["display"]:
                for sx in (x + 10, x + 21, x + 32):
                    draw.line([(sx, yy + 8), (sx + 6, yy + 8)], fill=INK, width=2)
            else:
                draw.line([(x + 10, yy + 8), (x + 38, yy + 8)], fill=INK, width=2)
            fit_text(draw, fonts["badge"], verb, w - 46, "legend verb " + verb)
            draw.text((x + 46, yy + 1), verb, font=fonts["badge"], fill=INK)
            yy += 18
        cx = x + 10
        yy += 4
        for chip in fc.PATTERN_CHIP.values():
            cw = fc._text_w(chip) + 10
            draw.rounded_rectangle([cx, yy, cx + cw, yy + 16], radius=4,
                                   fill=CHIP_ACCENT.get(chip, "#DDDDD6"),
                                   outline=INK, width=1)
            fit_text(draw, fonts["badge"], chip, cw - 4, "legend chip " + chip)
            draw.text((cx + 5, yy + 2), chip, font=fonts["badge"], fill=INK)
            cx += cw + 6
        yy += 26
        fit_text(draw, fonts["badge"], fc.LEGEND_HINT, w - 20, "legend hint")
        draw.text((x + 10, yy), fc.LEGEND_HINT, font=fonts["badge"],
                  fill=rb.blend(INK, rb.BG, 0.75))

    for la in geo["labels"]:
        cls = la["class"]
        x, y, w, h = la["rect"]
        if cls == "caps-header":
            continue  # drawn with the capsule (width asserted there)
        if cls == "legend":
            draw_legend(la["rect"])
            continue
        if cls == "intra":
            draw.rounded_rectangle([x, y, x + w, y + h], radius=4, fill=rb.BG,
                                   outline=rb.blend(INK, rb.BG, 0.30), width=1)
            fit_text(draw, fonts["badge"], la["text"], w - 8,
                     "intra verb " + la["text"])
            draw.text((x + 4, y + 2), la["text"], font=fonts["badge"], fill=dim)
            continue
        # stitch label: verb + to-slice pattern chip on a padded backplate
        draw.rounded_rectangle([x, y, x + w, y + h], radius=4, fill="#FFFFFF",
                               outline=rb.blend(INK, rb.BG, 0.55), width=1)
        vw = fc._text_w(la["text"])
        fit_text(draw, fonts["badge"], la["text"], vw, "stitch verb " + la["text"])
        draw.text((x + 5, y + 3), la["text"], font=fonts["badge"], fill=INK)
        cx = x + 5 + vw + 6
        cw = fc._text_w(la["chip"]) + 8
        draw.rounded_rectangle([cx, y + 2, cx + cw, y + 16], radius=4,
                               fill=la.get("chip_accent") or "#DDDDD6",
                               outline=INK, width=1)
        fit_text(draw, fonts["badge"], la["chip"], cw - 4,
                 "stitch chip " + la["chip"])
        draw.text((cx + 4, y + 3), la["chip"], font=fonts["badge"], fill=INK)

    # +N badges (CP-6 — the SOLE disclosure affordance per AM-4, absent at zero)
    # + upstream stubs (CP-5)
    def plus_badge(x0, y0, n):
        txt = "+%d" % n
        tw = draw.textlength(txt, font=fonts["chip"])
        cx, cy = x0 + CARD_W - 2, y0 + CARD_H // 2
        r = max(15, int(tw / 2) + 8)
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=INK,
                     outline="#FFFFFF", width=2)
        draw.text((cx - tw / 2, cy - 8), txt, font=fonts["chip"], fill="#FFFFFF")

    if res["seed_card"] and res["seed_card"]["plus_new"] >= 1:
        x0, y0 = card_rect["@|" + res["seed_card"]["entity"]][:2]
        plus_badge(x0, y0, res["seed_card"]["plus_new"])
    for cap in res["capsules"]:
        if cap["collapsed"]:
            continue
        for c in cap["cards"]:
            x0, y0 = card_rect[cap["slice"] + "|" + c["entity"]][:2]
            if c["is_output"] and c["plus_new"] >= 1:
                plus_badge(x0, y0, c["plus_new"])
            if c["upstream_stub"]:
                txt = "< UPSTREAM"
                tw = draw.textlength(txt, font=fonts["badge"])
                bx, by = x0 - 6, y0 + CARD_H + 4
                draw.rounded_rectangle([bx, by, bx + tw + 12, by + 17], radius=4,
                                       fill=rb.blend(INK, rb.BG, 0.14),
                                       outline=GHOST, width=1)
                draw.text((bx + 6, by + 2), txt, font=fonts["badge"], fill=INK)

    pathlib.Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    img.save(args.out, format="PNG")
    clashes = fc.connector_clashes(geo, res)
    n_labels = len(geo["labels"])
    print("wrote %s (%dx%d: %d capsule(s), %d stitch(es), %d frontier stub(s), "
          "%d gap(s); mode=%s%s, %d label rect(s); AM-5+§2.2 collision assert "
          "PASS, %d connector clash(es))"
          % (args.out, total_w, total_h, len(res["capsules"]),
             len(res["stitches"]), rev["frontier_size"], len(rev["gaps"]),
             geo["label_mode"], " hover" if hover else "", n_labels,
             len(clashes)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
