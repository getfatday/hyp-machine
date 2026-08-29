#!/usr/bin/env python3
"""PreToolUse write-once guard (hyp).

Deterministic deny rules:
  - Edit/NotebookEdit anywhere under the raw directory: always denied — raw
    sources are never edited after creation.
  - Write under the raw directory: denied only when the target already exists,
    so write-once creation stays legal (shell heredoc creation is likewise
    untouched on this path).
  - The same shape over the journal fragments directory: fragments are
    write-once entries; past entries are never rewritten.
  - Edit/Write/NotebookEdit on the base journal file: always denied — it is
    append-only history, and new entries are fragments.
  - Bash: a mistake-net branch parses the command string and denies plain
    destructive forms aimed at an EXISTING file under the raw or fragment
    directories — rm/unlink/shred/truncate, mv (rename-away or overwrite),
    cp onto, git rm/mv, truncating redirection (>, >|, &>), in-place editors
    (sed -i, perl -i), tee without -a, and interpreter one-liners that name
    the path with a write call. Creation onto new paths, tee -a, reads, >>
    appends, and `git checkout <sha> -- <path>` recovery are never matched.

The Bash branch is a mistake-net, not a security boundary: command-string
inspection cannot be exhaustive (substitution, xargs, scripts, cwd games all
evade it). It targets the plain-text commands a colleague types by mistake;
durability against everything else is the committed-recovery floor plus a
pushed remote, not this net.

Fails open on any error: a crashing PreToolUse hook would block every tool
call, which is worse than a missed deny. The consumer's settings deny rules
(written by init) are the durability layer when this plugin is disabled.
"""
import json
import os
import re
import shlex
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hyp_config import in_dir, load_config, rel_to_root, resolve_root

_SEGMENT_BREAKS = (";", "&&", "||", "|", "&", "(", ")")
_TRUNCATING = (">", ">|", "&>")
_WRAPPERS = ("sudo", "command", "env", "nohup", "time", "nice")
_INTERPRETERS = ("python", "python2", "python3", "perl", "ruby", "node")
_ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z_0-9]*=")
_WRITE_HINT_RE = re.compile(
    r"open\s*\([^)]*['\"](?:[wa]|r\+)"
    r"|os\.(?:remove|unlink|rename|replace|truncate)"
    r"|shutil\.(?:rmtree|move)"
    r"|write_text|write_bytes"
    r"|fs\.(?:rm|unlink|rename|writeFile)")


def deny(reason):
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }))
    sys.exit(0)


def _deny_bash(rel, form):
    deny("Bash (%s) would destroy %s — raw files and journal fragments are "
         "write-once, and this guard denies plain destructive commands against "
         "existing ones (a mistake-net, not a security boundary). Creation of "
         "new files and tee -a appends stay open. If a committed file was "
         "already wiped, recover it byte-identical: git checkout <sha> -- <path>."
         % (form, rel))


def guarded_existing(tok, root, cfg):
    """Repo-relative path when tok names an EXISTING path at or under the raw
    or journal-fragment directories; else None."""
    if not tok or not isinstance(tok, str) or tok.startswith("-"):
        return None
    rel = rel_to_root(tok, root)
    if rel is None:
        return None
    if not (in_dir(rel, cfg["raw_dir"]) or in_dir(rel, cfg["journal_dir"])):
        return None
    if os.path.exists(os.path.join(root, rel)):
        return rel
    return None


def bash_guard(command, root, cfg):
    """Deny when a plain destructive form resolves onto an existing write-once
    file. Unparseable or exotic commands fall through silently — legitimate
    operations must be silent, and the recovery floor covers what this misses."""
    head = command.split("<<", 1)[0]  # heredoc bodies are content, never commands
    lex = shlex.shlex(head, posix=True, punctuation_chars=True)
    lex.whitespace_split = True
    tokens = list(lex)
    segments, seg = [], []
    for tok in tokens:
        if tok in _SEGMENT_BREAKS:
            segments.append(seg)
            seg = []
        else:
            seg.append(tok)
    segments.append(seg)

    for seg in segments:
        if not seg:
            continue
        # truncating redirection onto an existing guarded path, anywhere
        for i, tok in enumerate(seg[:-1]):
            if tok in _TRUNCATING:
                rel = guarded_existing(seg[i + 1], root, cfg)
                if rel:
                    _deny_bash(rel, "truncating redirection onto")
        # drop redirection operators + their targets, leading assignments/wrappers
        words, skip = [], False
        for tok in seg:
            if skip:
                skip = False
                continue
            if tok in _TRUNCATING or tok in (">>", "<", ">&", "<&"):
                skip = True
                continue
            words.append(tok)
        while words and (_ASSIGN_RE.match(words[0])
                         or words[0].rsplit("/", 1)[-1] in _WRAPPERS):
            words.pop(0)
        if not words:
            continue
        verb = words[0].rsplit("/", 1)[-1]
        args = words[1:]
        hits = [r for r in (guarded_existing(a, root, cfg) for a in args) if r]

        if verb in ("rm", "unlink", "shred", "truncate") and hits:
            _deny_bash(hits[0], verb + " on")
        elif verb == "git":
            sub = next((a for a in args if not a.startswith("-")), "")
            if sub in ("rm", "mv") and hits:
                _deny_bash(hits[0], "git " + sub + " on")
            # every other git subcommand — including checkout recovery — is silent
        elif verb == "mv" and hits:
            _deny_bash(hits[0], "mv (rename-away or overwrite) on")
        elif verb == "cp":
            positional = [a for a in args if not a.startswith("-")]
            if positional:
                rel = guarded_existing(positional[-1], root, cfg)
                if rel and os.path.isfile(os.path.join(root, rel)):
                    _deny_bash(rel, "cp onto")
        elif verb == "sed":
            inplace = any(a == "--in-place" or a.startswith("--in-place=")
                          or (a.startswith("-i") and not a.startswith("--"))
                          for a in args)
            if inplace and hits:
                _deny_bash(hits[0], "sed -i on")
        elif verb == "perl":
            inplace = any(a.startswith("-") and not a.startswith("--")
                          and "i" in a for a in args)
            if inplace and hits:
                _deny_bash(hits[0], "perl -i on")
        elif verb == "tee":
            append = any(a in ("-a", "--append") for a in args)
            if hits and not append:
                _deny_bash(hits[0], "tee overwrite of")

        if verb in _INTERPRETERS:
            snippets, take = [], False
            for a in args:
                if take:
                    snippets.append(a)
                    take = False
                elif a in ("-c", "-e"):
                    take = True
            code = " ".join(snippets)
            if code and _WRITE_HINT_RE.search(code):
                for d in (cfg["raw_dir"], cfg["journal_dir"]):
                    pattern = re.escape(d.strip("/")) + r"/[^\s'\"]+"
                    for m in re.finditer(pattern, code):
                        rel = guarded_existing(m.group(0).rstrip("\"'),;:"), root, cfg)
                        if rel:
                            _deny_bash(rel, "an inline write naming")


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        sys.exit(0)
    tool = payload.get("tool_name", "")
    if tool == "Bash":
        command = (payload.get("tool_input") or {}).get("command")
        if isinstance(command, str) and command:
            root = resolve_root(payload)
            cfg = load_config(root)
            try:
                bash_guard(command, root, cfg)
            except Exception:
                pass  # mistake-net: unparseable commands fall through, never crash
        sys.exit(0)
    if tool not in ("Edit", "Write", "NotebookEdit"):
        sys.exit(0)
    tool_input = payload.get("tool_input") or {}
    fpath = (tool_input.get("file_path")
             or tool_input.get("notebook_path")
             or tool_input.get("path"))
    if not fpath or not isinstance(fpath, str):
        sys.exit(0)

    root = resolve_root(payload)
    cfg = load_config(root)
    rel = rel_to_root(fpath, root)
    if rel is None:
        sys.exit(0)
    exists = os.path.exists(os.path.join(root, rel))

    if rel == cfg["journal_file"]:
        deny("%s is the base journal file: append-only history, never edited or "
             "rewritten. New entries are write-once fragments — create "
             "%s/<id>-<slug>.md (id = highest existing + 1) and refresh the "
             "compiled view with scripts/compile-journal.py."
             % (rel, cfg["journal_dir"]))

    if in_dir(rel, cfg["raw_dir"]):
        if tool in ("Edit", "NotebookEdit"):
            deny("%s is under the raw directory (%s/): raw sources are write-once "
                 "and never edited after creation. Correct the record in a "
                 "distilled note that links here, or capture a new dated raw file."
                 % (rel, cfg["raw_dir"]))
        if exists:
            deny("%s already exists, and raw files are write-once. Create a new "
                 "dated raw file instead of overwriting." % rel)

    if in_dir(rel, cfg["journal_dir"]):
        if tool in ("Edit", "NotebookEdit"):
            deny("%s is a journal fragment: fragments are write-once and past "
                 "entries are never rewritten. Record a correction as a new "
                 "fragment with the next id." % rel)
        if exists:
            deny("%s already exists, and journal fragments are write-once. Add a "
                 "new fragment with the next id instead." % rel)

    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        sys.exit(0)
