#!/usr/bin/env python3
"""keep-ship-gate.py -- kept lanes that changed plugin-shipped bytes and carry no committed
ship record (harden advisory 33; spec hypotheses/H-DRAFT-3e26af94-lab-plugin-keep-ships-gate.md).

Ship is a three-part convention (verdict word, On-keep row, closes-when predicate) and none
of its parts knows about plugin bytes: a keep whose ON arm changed a file the plugin ships
can close its ledger row with a lab-only commit and sit unshipped for weeks. This gate
derives the gap from committed HEAD state alone -- nothing is remembered between sessions.

For every committed `hypotheses/<stem>.md` whose run directory carries a committed
`VERDICT.json` with a `files_changed_in_on` list, and whose Status word is `kept`:

  id rule       `H-NNN` when the stem matches `^H-[0-9]+`, else the full stem;
                run directory `experiments/runs/<id>/`.
  plugin-shipped rule   a path P in files_changed_in_on is plugin-shipped iff
                (a) the ON arm's parent sha (`on_parent`, else `off_sha`) is not a commit in
                    this repository (`git cat-file -e <sha>^{commit}` fails): the ON tree
                    was the plugin itself and every P counts; or
                (b) P begins with `experiments/deploy/hyp-machine/`; or
                (c) `experiments/deploy/hyp-machine/<P>` exists at HEAD (same relative
                    path; a matching basename alone never counts).
                A VERDICT.json with neither sha is lab-parented (rules b and c only). When
                the deploy tree is absent at HEAD (a consumer install) rules b and c match
                nothing and rule a alone decides.
  ship record   a committed `experiments/runs/<id>/SHIP.md` at HEAD containing a line
                matching `^pr: [0-9]+$`.

One line per kept lane with >= 1 plugin-shipped path and no ship record:

    KEEP-UNSHIPPED<TAB><id><TAB><n plugin-shipped paths>

sorted by id; silent when nothing is flagged. Exit 0 whenever HEAD is readable (advisory:
the harden-check block counts the lines); exit 2 when the root is not a git repository with a readable HEAD.
Everything is read from HEAD through one persistent `git cat-file --batch` process in three
pipelined stages (the hypotheses tree object; every lane's VERDICT.json; then each
candidate's spec, SHIP.md, deploy-tree twins and parent-commit probe): one git launch and a
handful of pipe round-trips in all. The working tree is never
consulted, so an uncommitted SHIP.md does not quiet the line (Durability invariant).

    python3 scripts/keep-ship-gate.py [repo-root]      # default: .
"""
import json
import os
import re
import subprocess
import sys

DEPLOY_PREFIX = "experiments/deploy/hyp-machine/"
NUMBERED_RE = re.compile(r"^H-[0-9]+")
PR_LINE_RE = re.compile(r"(?m)^pr: [0-9]+$")
GIT_TIMEOUT = 30

try:  # the shared status-word reader (scripts/closes_when.py) when it sits beside this file
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from closes_when import extract_status_word
except Exception:  # pragma: no cover - consumer trees without closes_when.py
    _HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
    _STATUS_HEADING_RE = re.compile(r"(?m)^##\s*Status\s*$")
    _NEXT_HEADING_RE = re.compile(r"(?m)^##\s")

    def extract_status_word(text):
        m = _STATUS_HEADING_RE.search(text)
        if not m:
            return None
        rest = text[m.end():]
        nxt = _NEXT_HEADING_RE.search(rest)
        block = rest[:nxt.start()] if nxt else rest
        block = _HTML_COMMENT_RE.sub(" ", block)
        stripped = block.strip()
        return stripped.split()[0] if stripped else None


class Reader(object):
    """Committed-state reader over ONE persistent `git cat-file --batch` process: tree
    objects (parsed here: `<mode> <name>\\0<20-byte sha>` entries), blobs, and
    commit-existence probes (`<sha>^{commit}` answers `missing` when the sha is not a commit
    in this repository). Requests are pipelined in chunks -- every request of a stage is
    written before its answers are read -- so a corpus of hundreds of specs costs a handful of
    pipe round-trips, not hundreds. One git launch in all: the Apple git shim costs 0.4-2.5 s
    per launch under load, and scheduler latency per round-trip is what the rest of the wall
    is spent on."""

    CHUNK = 128  # requests per pipelined write (~50 bytes each: far inside the pipe buffer)

    def __init__(self, root):
        self.root = root
        self.proc = None
        self.ok = True

    def _start(self):
        if self.proc is None:
            try:
                self.proc = subprocess.Popen(["git", "-C", self.root, "cat-file", "--batch"],
                                             stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                             stderr=subprocess.DEVNULL)
            except OSError:
                self.ok = False
        return self.proc is not None

    def _read_one(self):
        header = self.proc.stdout.readline().decode("utf-8", "replace").split()
        if len(header) != 3:
            if not header:
                self.ok = False  # the process died (not a repository, HEAD unreadable)
            return None, None
        size = int(header[2])
        body = self.proc.stdout.read(size + 1)[:size]
        return header[1], body

    def ask_many(self, requests):
        """[(kind, body)] aligned with requests; (None, None) for a missing object."""
        results = []
        if not requests or not self._start():
            return [(None, None)] * len(requests)
        for i in range(0, len(requests), self.CHUNK):
            chunk = requests[i:i + self.CHUNK]
            try:
                self.proc.stdin.write("".join(r + "\n" for r in chunk).encode("utf-8"))
                self.proc.stdin.flush()
            except (OSError, ValueError):
                self.ok = False
                return results + [(None, None)] * (len(requests) - len(results))
            for _ in chunk:
                results.append(self._read_one() if self.ok else (None, None))
        return results

    def ask(self, request):
        return self.ask_many([request])[0]

    def blobs(self, paths):
        return [body if kind == "blob" else None for kind, body in self.ask_many(["HEAD:" + p for p in paths])]

    def tree_entries(self, path):
        """[(mode, name)] of a committed directory, or None when it is not a tree."""
        kind, body = self.ask("HEAD:" + path if path else "HEAD^{tree}")
        if kind != "tree":
            return None
        entries, pos = [], 0
        while pos < len(body):
            sp = body.index(b" ", pos)
            nul = body.index(b"\0", sp)
            entries.append((body[pos:sp].decode("ascii"), body[sp + 1:nul].decode("utf-8", "replace")))
            pos = nul + 21
        return entries

    def commits_exist(self, shas):
        return [kind == "commit" for kind, _ in self.ask_many(["%s^{commit}" % s for s in shas])]

    def close(self):
        if self.proc is not None:
            try:
                self.proc.stdin.close()
                self.proc.wait(timeout=GIT_TIMEOUT)
            except (OSError, subprocess.SubprocessError):
                self.proc.kill()


def lane_id(stem):
    m = NUMBERED_RE.match(stem)
    return m.group(0) if m else stem


def parent_of(record):
    parent = record.get("on_parent") or record.get("off_sha")
    return parent.strip() if isinstance(parent, str) and parent.strip() else None


def plugin_shipped_count(files, foreign, deploy_present):
    """deploy_present: {P: bool} -- whether experiments/deploy/hyp-machine/<P> is a blob at HEAD."""
    n = 0
    for p in files:
        if not isinstance(p, str):
            continue
        if foreign or p.startswith(DEPLOY_PREFIX) or deploy_present.get(p, False):
            n += 1
    return n


def gate(root):
    reader = Reader(root)
    try:
        # stage 1: the committed hypotheses directory
        entries = reader.tree_entries("hypotheses")
        if entries is None:
            return None if not reader.ok else []
        specs = sorted((lane_id(name[:-3]), name) for mode, name in entries
                       if not mode.startswith("040") and name.endswith(".md"))
        # stage 2: which lanes carry a committed VERDICT.json (one pipelined pass)
        verdicts = reader.blobs(["experiments/runs/%s/VERDICT.json" % lid for lid, _ in specs])
        lanes = []
        for (lid, name), blob in zip(specs, verdicts):
            if blob is None:
                continue
            try:
                record = json.loads(blob.decode("utf-8", "replace"))
            except ValueError:
                continue
            files = record.get("files_changed_in_on") if isinstance(record, dict) else None
            if isinstance(files, list):
                lanes.append((lid, name, files, parent_of(record)))
        if not lanes:
            return []
        # stage 3: spec status words, ship records, deploy-tree twins, parent-commit probes (one pass)
        paths = []
        for lid, name, files, parent in lanes:
            paths.append("hypotheses/" + name)
            paths.append("experiments/runs/%s/SHIP.md" % lid)
            for p in files:
                if isinstance(p, str) and not p.startswith(DEPLOY_PREFIX):
                    paths.append(DEPLOY_PREFIX + p)
        blobs = dict(zip(paths, reader.blobs(paths)))
        shas = sorted(set(parent for _, _, _, parent in lanes if parent))
        exists = dict(zip(shas, reader.commits_exist(shas)))
        rows = []
        for lid, name, files, parent in lanes:
            word = extract_status_word((blobs.get("hypotheses/" + name) or b"").decode("utf-8", "replace"))
            if word is None or word.lower() != "kept":
                continue
            foreign = parent is not None and not exists.get(parent, False)
            deploy_present = dict((p, blobs.get(DEPLOY_PREFIX + p) is not None) for p in files if isinstance(p, str))
            n = plugin_shipped_count(files, foreign, deploy_present)
            if n == 0:
                continue
            ship_text = (blobs.get("experiments/runs/%s/SHIP.md" % lid) or b"").decode("utf-8", "replace")
            if PR_LINE_RE.search(ship_text):
                continue
            rows.append((lid, n))
    finally:
        reader.close()
    return sorted(rows)


def main(argv):
    root = os.path.abspath(argv[1]) if len(argv) > 1 else os.path.abspath(".")
    rows = gate(root)
    if rows is None:
        sys.stderr.write("keep-ship-gate: %s is not a git repository with a readable HEAD\n" % root)
        return 2
    for lid, n in rows:
        sys.stdout.write("KEEP-UNSHIPPED\t%s\t%d\n" % (lid, n))
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
