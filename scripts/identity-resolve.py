#!/usr/bin/env python3
"""identity-resolve.py -- git-config-derived per-user attribution + YOURS/OTHERS lens.

PROVENANCE — COUNTED, byte-preserving port of the kept H-156 fixture lens
(experiments/runs/H-156/fixture/lens.py in the source lab; hypothesis
H-156-observatory-identity-attribution KEPT 2026-08-28, two consecutive counted
5/5, fully offline: every fixture artifact attributed to its mailmap-resolved
ground-truth owner with exact agent-assist trailer totals, correct acting-as +
YOURS/OTHERS partition under both fixture identities, committed projection
byte-identical across viewers, zero outbound avatar requests, zero third-party
name leaks). Only this provenance framing, the script name, the ledger family
path (read from .claude/hyp.json ledger_file, default ledger/ledger.jsonl), and
a contributors.json shape-compat shim (the hyp dict form joins the fixture list
form) differ from the counted fixture copy; the attribution, lens, and avatar
logic is untouched.

Stdlib only, fully offline BY CONSTRUCTION: this module performs no network I/O
anywhere -- it does not import urllib/socket/http. The remote avatar tier is an
opt-in LOG-ONLY stub (records the attempt it would have made to
request-log.jsonl and falls through to local initials); keep that tier OFF
(OBS_AVATAR_REMOTE unset/off) — a real fetch tier is a separate future
hypothesis in the source lab.

Identity doctrine (survey experiments/runs/DESIGN-lab-observatory/research/git-identity.md):
  - email is the universal join key; the committed .mailmap is the universal fixer
    (canonicalization via `git check-mailmap`, case-insensitive, alias folding);
  - artifact owner = author email of the artifact's REGISTERING commit (first commit that
    touched the path), mailmap-canonicalized;
  - agent work is disclosed by Co-authored-by trailer, owned by the author;
  - the committed projection is viewer-independent ("the file is the shared truth, the
    session is the personal lens", staged lab-intake 0.1.5); the viewer perspective
    (acting-as, YOURS/OTHERS) exists only in render-time output;
  - avatar ladder, privacy-ordered: committed image -> [opt-in remote, OFF here] -> local
    deterministic initials SVG. Names render from contributors.json/.mailmap at render
    time, never from artifact prose (name-neutrality law).

Modes (mutually exclusive; all write only under --out):
  --attribute    attribution.json over the artifact families
  --render       lens-output.json + projection.md + avatars/ + request-log.jsonl
  --projection   projection.md only
  --avatars      avatars/ + request-log.jsonl only

Calibration-only env knobs (a counted run never sets them):
  OBS_LENS_EMBED_VIEWER=1  append a viewer line to projection.md (seeds the H-156
                           viewer-independence defect; documented, never default)
  OBS_AVATAR_REMOTE=on     enable the log-only remote tier stub (seeds the H-156
                           offline-avatar defect; still zero real network by construction)

Deterministic: sorted iteration everywhere, no timestamps, byte-stable output.
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys

def _config_ledger_rel(repo):
    """Consumer-repo ledger path from .claude/hyp.json (ledger_file), default
    ledger/ledger.jsonl — the one lab path that diverges in a hyp install."""
    rel = "ledger/ledger.jsonl"
    try:
        with open(os.path.join(repo, ".claude", "hyp.json"), encoding="utf-8") as fh:
            data = json.load(fh)
        val = data.get("ledger_file") if isinstance(data, dict) else None
        if isinstance(val, str) and val.strip():
            rel = val.strip().strip("/")
    except (OSError, ValueError):
        pass
    return rel


def artifact_families(repo):
    return (
        ("hypotheses/", r"^hypotheses/[^/]+\.md$"),
        ("experiments/journal-fragments/", r"^experiments/journal-fragments/[^/]+\.md$"),
        ("experiments/runs/", r"^experiments/runs/.+"),
        ("ledger/", r"^%s$" % re.escape(_config_ledger_rel(repo))),
    )

LENS_VERSION = "h156-lens/1"


def git(repo, *args):
    r = subprocess.run(["git", "-C", repo] + list(args), capture_output=True,
                       text=True, timeout=120)
    if r.returncode != 0:
        raise RuntimeError("git %s failed: %s" % (" ".join(args),
                                                  (r.stderr or "").strip()[:200]))
    return r.stdout


def artifact_paths(repo):
    tracked = [l for l in git(repo, "ls-files").splitlines() if l.strip()]
    out = []
    for path in tracked:
        for _, rx in artifact_families(repo):
            if re.match(rx, path):
                out.append(path)
                break
    return sorted(out)


def check_mailmap(repo, name, email):
    """Canonicalize one identity through the committed .mailmap (git-native fold)."""
    line = git(repo, "check-mailmap", "%s <%s>" % (name, email)).strip()
    m = re.match(r"^(.*)\s*<([^>]+)>$", line)
    if not m:
        return name, email
    return (m.group(1).strip() or name), m.group(2).strip()


def registering_commit(repo, path):
    """First commit that touched the path: sha + RAW author identity + trailers."""
    out = git(repo, "log", "--reverse", "--format=%H%x00%an%x00%ae", "--", path)
    first = out.splitlines()[0] if out.splitlines() else ""
    if not first:
        return None
    sha, an, ae = first.split("\x00")
    trailers = git(repo, "log", "-1",
                   "--format=%(trailers:key=Co-authored-by,valueonly)", sha)
    tvals = sorted({t.strip() for t in trailers.splitlines() if t.strip()})
    return {"sha": sha, "raw_author_name": an, "raw_author_email": ae,
            "co_authored_by": tvals}


def load_contributors(repo):
    p = os.path.join(repo, "contributors.json")
    if not os.path.isfile(p):
        return {}
    with open(p, encoding="utf-8") as f:
        data = json.load(f)
    by_email = {}
    if isinstance(data, dict) and isinstance(data.get("contributors"), list):
        # fixture list form: {"contributors": [{"email", "name", "avatar"?}, ...]}
        for c in data["contributors"]:
            by_email[c["email"].strip().lower()] = c
    elif isinstance(data, dict):
        # hyp dict form (README "Identity and attribution"): {"<canonical email>":
        # {"name": ..., "aka": [...]}} — normalized to the fixture entry shape.
        for email, entry in data.items():
            if isinstance(entry, dict):
                rec = dict(entry)
                rec.setdefault("email", email)
                by_email[str(email).strip().lower()] = rec
    return by_email


def display_name(contributors, canonical_email, canonical_name):
    c = contributors.get(canonical_email.strip().lower())
    if c and c.get("name"):
        return c["name"]
    return canonical_name or canonical_email.split("@")[0]


def attribute(repo):
    """Attribution table: every artifact -> canonical owner + agent-assist disclosure."""
    contributors = load_contributors(repo)
    artifacts = {}
    totals = {}
    trailer_commits = set()
    for path in artifact_paths(repo):
        reg = registering_commit(repo, path)
        if reg is None:
            continue
        cname, cemail = check_mailmap(repo, reg["raw_author_name"],
                                      reg["raw_author_email"])
        assisted = bool(reg["co_authored_by"])
        if assisted:
            trailer_commits.add(reg["sha"])
        artifacts[path] = {
            "registering_commit": reg["sha"],
            "raw_author_name": reg["raw_author_name"],
            "raw_author_email": reg["raw_author_email"],
            "canonical_name": cname,
            "canonical_email": cemail,
            "display_name": display_name(contributors, cemail, cname),
            "agent_assisted": assisted,
            "co_authored_by": reg["co_authored_by"],
        }
    # agent-assist share counts REGISTERING COMMITS carrying the trailer, grouped by the
    # commit's canonical owner (git shortlog --group=trailer prior art, computed per owner).
    seen = set()
    for path in sorted(artifacts):
        rec = artifacts[path]
        key = rec["registering_commit"]
        if rec["agent_assisted"] and key not in seen:
            seen.add(key)
            totals[rec["canonical_email"]] = totals.get(rec["canonical_email"], 0) + 1
    return {"lens": LENS_VERSION,
            "artifacts": artifacts,
            "agent_assist_totals": {k: totals[k] for k in sorted(totals)},
            "trailer_commits_total": len(trailer_commits)}


def viewer_identity(repo):
    """who-am-i: repo-resolved git config identity, mailmap-canonicalized at render time.

    Reads `git config user.email` with full git resolution (repo-local wins) -- the
    fixture sets identities repo-locally inside scratch clones only; the operator's real
    config is never written (H-156 Method step 3)."""
    def cfg(key):
        r = subprocess.run(["git", "-C", repo, "config", "--get", key],
                           capture_output=True, text=True, timeout=60)
        return (r.stdout or "").strip()
    raw_email = cfg("user.email")
    raw_name = cfg("user.name") or (raw_email.split("@")[0] if raw_email else "")
    if not raw_email:
        return {"resolved": False, "raw_name": raw_name, "raw_email": "",
                "canonical_name": "", "canonical_email": "", "display_name": ""}
    cname, cemail = check_mailmap(repo, raw_name, raw_email)
    contributors = load_contributors(repo)
    return {"resolved": True, "raw_name": raw_name, "raw_email": raw_email,
            "canonical_name": cname, "canonical_email": cemail,
            "display_name": display_name(contributors, cemail, cname),
            "known_contributor": cemail.strip().lower() in contributors}


def render_projection(repo, attribution):
    """The COMMITTED projection: viewer-independent, byte-stable, no timestamps."""
    lines = ["# Observatory projection (viewer-independent)", "",
             "Owner = mailmap-canonicalized author of the registering commit.",
             "Agent-assisted = registering commit carries a Co-authored-by trailer.", "",
             "| artifact | owner | canonical email | agent-assisted |",
             "|---|---|---|---|"]
    arts = attribution["artifacts"]
    for path in sorted(arts):
        a = arts[path]
        lines.append("| %s | %s | %s | %s |" % (
            path, a["display_name"], a["canonical_email"],
            "yes" if a["agent_assisted"] else "no"))
    lines.append("")
    lines.append("Agent-assist share (registering commits with trailer, per owner):")
    for email in sorted(attribution["agent_assist_totals"]):
        lines.append("- %s: %d" % (email, attribution["agent_assist_totals"][email]))
    lines.append("- total trailer commits: %d" % attribution["trailer_commits_total"])
    if os.environ.get("OBS_LENS_EMBED_VIEWER") == "1":
        # calibration-only defect knob: makes the projection viewer-DEPENDENT on purpose.
        v = viewer_identity(repo)
        lines.append("viewer: %s" % (v["canonical_email"] or "unresolved"))
    return "\n".join(lines) + "\n"


def initials_for(name):
    tokens = [t for t in re.split(r"[\s._-]+", name.strip()) if t]
    alpha = [next((ch for ch in t if ch.isalpha()), "") for t in tokens]
    alpha = [a for a in alpha if a]
    if not alpha:
        return "??"
    if len(alpha) == 1:
        stem = re.sub(r"[^A-Za-z]", "", tokens[0])
        return (stem[:2] or alpha[0]).upper()
    return (alpha[0] + alpha[-1]).upper()


def avatar_svg(name, email):
    hue = int(hashlib.sha256(email.strip().lower().encode()).hexdigest()[:8], 16) % 360
    initials = initials_for(name)
    return ('<svg xmlns="http://www.w3.org/2000/svg" width="96" height="96" '
            'viewBox="0 0 96 96"><rect width="96" height="96" rx="12" '
            'fill="hsl(%d, 55%%, 45%%)"/><text x="48" y="60" font-family="monospace" '
            'font-size="38" text-anchor="middle" fill="#ffffff">%s</text></svg>\n'
            % (hue, initials))


def email_slug(email):
    return re.sub(r"[^A-Za-z0-9._-]", "-", email.strip().lower().replace("@", "_at_"))


def render_avatars(repo, out_dir):
    """Privacy-first ladder. Tier 1 committed image -> tier 2 remote (opt-in, LOG-ONLY
    stub -- zero network possible by construction) -> tier 3 deterministic local initials.
    Every would-be outbound request is a line in request-log.jsonl; H-156 asserts that log
    stays EMPTY with the remote tier off."""
    avatars_dir = os.path.join(out_dir, "avatars")
    os.makedirs(avatars_dir, exist_ok=True)
    request_log = os.path.join(out_dir, "request-log.jsonl")
    log_lines = []
    contributors = load_contributors(repo)
    remote_on = os.environ.get("OBS_AVATAR_REMOTE", "off").lower() == "on"
    written = []
    for email in sorted(contributors):
        c = contributors[email]
        name = c.get("name") or email.split("@")[0]
        target = os.path.join(avatars_dir, email_slug(email) + ".svg")
        # tier 1: committed local image referenced by contributors.json
        committed = c.get("avatar")
        if committed:
            src = os.path.join(repo, committed)
            if os.path.isfile(src):
                with open(src, "rb") as f:
                    data = f.read()
                with open(target, "wb") as f:
                    f.write(data)
                written.append({"email": email, "tier": "committed"})
                continue
        # tier 2: opt-in remote (LOG-ONLY stub; fixture rule keeps this OFF)
        if remote_on:
            ehash = hashlib.sha256(email.strip().lower().encode()).hexdigest()
            log_lines.append(json.dumps(
                {"tier": "remote", "would_request":
                 "https://gravatar.example.invalid/avatar/%s?d=404" % ehash,
                 "email_sha256": ehash}, sort_keys=True))
        # tier 3: deterministic local initials (the H-156 default landing tier)
        with open(target, "w", encoding="utf-8") as f:
            f.write(avatar_svg(name, email))
        written.append({"email": email, "tier": "initials"})
    with open(request_log, "w", encoding="utf-8") as f:
        for line in log_lines:
            f.write(line + "\n")
    return {"avatars": written, "outbound_attempts": len(log_lines)}


def render(repo, out_dir):
    attribution = attribute(repo)
    viewer = viewer_identity(repo)
    arts = attribution["artifacts"]
    yours = sorted(p for p in arts
                   if viewer["resolved"]
                   and arts[p]["canonical_email"] == viewer["canonical_email"])
    others = sorted(p for p in arts if p not in set(yours))
    lens_out = {
        "lens": LENS_VERSION,
        "acting_as": viewer,
        "yours": yours,
        "others": others,
        "counts": {"yours": len(yours), "others": len(others),
                   "artifacts": len(arts)},
    }
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "lens-output.json"), "w", encoding="utf-8") as f:
        json.dump(lens_out, f, indent=1, sort_keys=True)
        f.write("\n")
    with open(os.path.join(out_dir, "projection.md"), "w", encoding="utf-8") as f:
        f.write(render_projection(repo, attribution))
    avatars = render_avatars(repo, out_dir)
    return {"acting_as": viewer.get("canonical_email"),
            "yours": len(yours), "others": len(others),
            "avatars": len(avatars["avatars"]),
            "outbound_attempts": avatars["outbound_attempts"]}


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--attribute", action="store_true")
    mode.add_argument("--render", action="store_true")
    mode.add_argument("--projection", action="store_true")
    mode.add_argument("--avatars", action="store_true")
    ap.add_argument("--repo", default=".")
    ap.add_argument("--out", required=True)
    o = ap.parse_args()
    repo = os.path.abspath(o.repo)
    out_dir = os.path.abspath(o.out)
    os.makedirs(out_dir, exist_ok=True)
    if o.attribute:
        att = attribute(repo)
        with open(os.path.join(out_dir, "attribution.json"), "w",
                  encoding="utf-8") as f:
            json.dump(att, f, indent=1, sort_keys=True)
            f.write("\n")
        print("attributed %d artifacts" % len(att["artifacts"]))
        return 0
    if o.projection:
        att = attribute(repo)
        with open(os.path.join(out_dir, "projection.md"), "w",
                  encoding="utf-8") as f:
            f.write(render_projection(repo, att))
        print("projection written")
        return 0
    if o.avatars:
        rec = render_avatars(repo, out_dir)
        print("avatars %d outbound_attempts %d"
              % (len(rec["avatars"]), rec["outbound_attempts"]))
        return 0
    rec = render(repo, out_dir)
    print("render acting_as=%s yours=%d others=%d avatars=%d outbound=%d"
          % (rec["acting_as"], rec["yours"], rec["others"], rec["avatars"],
             rec["outbound_attempts"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
