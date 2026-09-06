#!/usr/bin/env python3
"""eval-grade.py: stdlib, session-free scorer for the deterministic graders of
`claude plugin eval` case files (evals/<skill>/<case>/case.yaml).

Usage:
  eval-grade.py <case.yaml> <tree-dir>   grade one case against a directory
  eval-grade.py --list <evals-dir>       census of every <skill>/<case>/case.yaml
  eval-grade.py --version-line ...       also print `sys.executable sys.version`
                                         as the first stderr line (diagnostic)

Grade mode prints, per grader in file order, one line
    PASS|FAIL|SKIPPED<TAB><grader name>
then one summary line
    CASE <name> <k>/<n> deterministic, <m> skipped
where n counts the deterministic graders (file_exists, and regex with a tree
target), k those that passed, and m the SKIPPED lines. Exit 0 whenever the case
parsed; exit 2 on a parse error (message on stderr). Nothing here runs a
session, a skill, or a subprocess.

Grader semantics (frozen with the lane that introduced this file):
  file_exists  `path` is a glob relative to the tree (recursive); passes exactly
               when `exists` (default true) equals "the glob hits at least once".
  regex        `target: files` searches every regular file under the tree
               outside .git/; `target: {source: file, path: P}` searches P,
               a missing P being FAIL for `contains` and PASS for
               `not_contains`; `match` defaults to `contains`; `flags: i` sets
               case-insensitive. A regex whose target is neither form is a
               session-text grader and prints SKIPPED.
  llm          prints SKIPPED.
  `weight` is parsed and ignored. Any other grader type is an error (exit 2).

YAML reader: covers exactly the constructs the shipped case files use: block
mappings; block sequences (including sequences of mappings); `|` and `>` block
scalars (bodies literal, so `#` and quotes inside are text); flow mappings and
flow sequences on one line; single-quoted scalars ('' escape); double-quoted
scalars with the escapes \\\\ \\" \\n \\t; plain scalars (true/false as booleans,
integer and decimal forms as numbers); full-line comments. Anchors, aliases,
tags, document markers, multi-line plain scalars, trailing comments, tabs in
indentation and any other construct are parse errors (exit 2).
"""
import glob as globmod
import os
import re
import sys


class ParseError(Exception):
    pass


# ----------------------------------------------------------------------------
# YAML subset reader
# ----------------------------------------------------------------------------

_KEY_RE = re.compile(r"^([A-Za-z0-9_][A-Za-z0-9_.-]*):(?:[ ]+(.*))?$")
_INT_RE = re.compile(r"^-?[0-9]+$")
_FLOAT_RE = re.compile(r"^-?[0-9]+\.[0-9]+$")
_BLOCK_HDR_RE = re.compile(r"^([|>])([+-]?)$")


class _Reader(object):
    def __init__(self, text):
        self.lines = text.split("\n")
        self.i = 0

    # -- line access ---------------------------------------------------------
    def _indent_of(self, raw):
        stripped = raw.lstrip(" ")
        lead = raw[: len(raw) - len(stripped)]
        return len(lead), stripped.rstrip()

    def peek(self):
        """Next significant line as (indent, text, lineno) or None at EOF.
        Blank lines and full-line comments are skipped, tabs in indentation
        and document markers are errors."""
        while self.i < len(self.lines):
            raw = self.lines[self.i]
            if raw.strip() == "":
                self.i += 1
                continue
            if raw.lstrip().startswith("#"):
                self.i += 1
                continue
            if raw[: len(raw) - len(raw.lstrip())].find("\t") >= 0:
                raise ParseError("line %d: tab in indentation" % (self.i + 1))
            indent, text = self._indent_of(raw)
            if indent == 0 and (text == "---" or text == "..." or text.startswith("%")):
                raise ParseError("line %d: document marker or directive unsupported" % (self.i + 1))
            return indent, text, self.i + 1
        return None

    def take(self):
        p = self.peek()
        if p is None:
            raise ParseError("unexpected end of file")
        self.i += 1
        return p

    # -- nodes ---------------------------------------------------------------
    def parse_document(self):
        p = self.peek()
        if p is None:
            return None
        indent, text, _ = p
        if indent != 0:
            raise ParseError("line %d: top-level node must start at column 0" % p[2])
        node = self.parse_node(0)
        rest = self.peek()
        if rest is not None:
            raise ParseError("line %d: trailing content after document" % rest[2])
        return node

    def parse_node(self, indent):
        p = self.peek()
        if p is None:
            raise ParseError("unexpected end of file")
        ind, text, ln = p
        if ind != indent:
            raise ParseError("line %d: bad indentation (%d, expected %d)" % (ln, ind, indent))
        if text == "-" or text.startswith("- "):
            return self.parse_sequence(indent)
        return self.parse_mapping(indent)

    def parse_mapping(self, indent):
        out = {}
        while True:
            p = self.peek()
            if p is None:
                break
            ind, text, ln = p
            if ind < indent:
                break
            if ind > indent:
                raise ParseError("line %d: unexpected indentation (multi-line plain scalars unsupported)" % ln)
            if text == "-" or text.startswith("- "):
                raise ParseError("line %d: sequence item where a mapping key was expected" % ln)
            m = _KEY_RE.match(text)
            if not m:
                raise ParseError("line %d: not a `key: value` line" % ln)
            key, rest = m.group(1), m.group(2)
            if key in out:
                raise ParseError("line %d: duplicate key %r" % (ln, key))
            self.i += 1
            if rest is None or rest == "":
                out[key] = self.parse_nested_value(indent)
            else:
                bh = _BLOCK_HDR_RE.match(rest)
                if bh:
                    out[key] = self.parse_block_scalar(indent, bh.group(1), bh.group(2), ln)
                else:
                    out[key] = parse_inline_scalar(rest, ln)
        return out

    def parse_nested_value(self, parent_indent):
        p = self.peek()
        if p is None:
            return None
        ind, text, ln = p
        if ind > parent_indent:
            return self.parse_node(ind)
        if ind == parent_indent and (text == "-" or text.startswith("- ")):
            return self.parse_sequence(ind)
        return None

    def parse_sequence(self, indent):
        out = []
        while True:
            p = self.peek()
            if p is None:
                break
            ind, text, ln = p
            if ind < indent:
                break
            if ind > indent:
                raise ParseError("line %d: unexpected indentation in sequence" % ln)
            if not (text == "-" or text.startswith("- ")):
                break
            item = text[1:]
            stripped = item.lstrip(" ")
            if stripped == "":
                self.i += 1
                out.append(self.parse_nested_value(indent))
                continue
            item_indent = indent + 1 + (len(item) - len(stripped))
            km = _KEY_RE.match(stripped)
            if km and not (stripped.startswith("'") or stripped.startswith('"')):
                # mapping starting inline on the item line: rewrite the raw line
                # so the mapping parser sees its first key at item_indent.
                self.lines[self.i] = " " * item_indent + stripped
                out.append(self.parse_mapping(item_indent))
            else:
                self.i += 1
                bh = _BLOCK_HDR_RE.match(stripped)
                if bh:
                    out.append(self.parse_block_scalar(indent, bh.group(1), bh.group(2), ln))
                else:
                    out.append(parse_inline_scalar(stripped, ln))
        return out

    def parse_block_scalar(self, parent_indent, style, chomp, header_ln):
        body = []
        block_indent = None
        while self.i < len(self.lines):
            raw = self.lines[self.i]
            if raw.strip() == "":
                body.append("")
                self.i += 1
                continue
            lead = len(raw) - len(raw.lstrip(" "))
            if block_indent is None:
                if lead <= parent_indent:
                    break
                block_indent = lead
            if lead < block_indent:
                break
            body.append(raw[block_indent:])
            self.i += 1
        while body and body[-1] == "":
            body.pop()
        if style == "|":
            content = "\n".join(body)
        else:
            content = ""
            prev_blank = True
            first = True
            for ln in body:
                if ln == "":
                    content += "\n"
                    prev_blank = True
                    continue
                if not first and not prev_blank:
                    content += " "
                content += ln
                prev_blank = False
                first = False
        if content == "":
            return ""
        if chomp == "-":
            return content
        if chomp == "+":
            return content + "\n"
        return content + "\n"


def _decode_double_quoted(s, ln):
    out = []
    i = 0
    while i < len(s):
        c = s[i]
        if c == "\\":
            if i + 1 >= len(s):
                raise ParseError("line %d: dangling backslash" % ln)
            e = s[i + 1]
            if e == "\\":
                out.append("\\")
            elif e == '"':
                out.append('"')
            elif e == "n":
                out.append("\n")
            elif e == "t":
                out.append("\t")
            else:
                raise ParseError("line %d: unsupported escape \\%s" % (ln, e))
            i += 2
        else:
            out.append(c)
            i += 1
    return "".join(out)


def _split_quoted_end(s, ln):
    """s starts with a quote; return (decoded, rest_after_closing_quote)."""
    q = s[0]
    i = 1
    if q == '"':
        while i < len(s):
            if s[i] == "\\":
                i += 2
                continue
            if s[i] == '"':
                return _decode_double_quoted(s[1:i], ln), s[i + 1:]
            i += 1
        raise ParseError("line %d: unterminated double-quoted scalar" % ln)
    buf = []
    while i < len(s):
        if s[i] == "'":
            if i + 1 < len(s) and s[i + 1] == "'":
                buf.append("'")
                i += 2
                continue
            return "".join(buf), s[i + 1:]
        buf.append(s[i])
        i += 1
    raise ParseError("line %d: unterminated single-quoted scalar" % ln)


def _plain_scalar(s, ln):
    if s == "":
        return None
    if s[0] in "&*!%@`|>":
        raise ParseError("line %d: unsupported construct starting with %r" % (ln, s[0]))
    if " #" in s or s.startswith("#"):
        raise ParseError("line %d: trailing comment unsupported" % ln)
    if s == "true":
        return True
    if s == "false":
        return False
    if _INT_RE.match(s):
        return int(s)
    if _FLOAT_RE.match(s):
        return float(s)
    return s


def _split_flow_items(body, ln):
    """Split the inside of {...} or [...] on top-level commas, honouring quotes."""
    items = []
    buf = []
    q = None
    i = 0
    while i < len(body):
        c = body[i]
        if q:
            buf.append(c)
            if c == "\\" and q == '"' and i + 1 < len(body):
                buf.append(body[i + 1])
                i += 2
                continue
            if c == q:
                if q == "'" and i + 1 < len(body) and body[i + 1] == "'":
                    buf.append("'")
                    i += 2
                    continue
                q = None
            i += 1
            continue
        if c in "\"'":
            q = c
            buf.append(c)
        elif c in "{}[]":
            raise ParseError("line %d: nested flow collections unsupported" % ln)
        elif c == ",":
            items.append("".join(buf).strip())
            buf = []
        else:
            buf.append(c)
        i += 1
    tail = "".join(buf).strip()
    if tail or items:
        items.append(tail)
    return items


def _flow_scalar(s, ln):
    s = s.strip()
    if s == "":
        raise ParseError("line %d: empty flow item" % ln)
    if s[0] in "\"'":
        val, rest = _split_quoted_end(s, ln)
        if rest.strip() != "":
            raise ParseError("line %d: content after quoted scalar" % ln)
        return val
    return _plain_scalar(s, ln)


def parse_inline_scalar(rest, ln):
    rest = rest.strip()
    if rest.startswith("{"):
        if not rest.endswith("}"):
            raise ParseError("line %d: flow mapping must close on the same line" % ln)
        out = {}
        for item in _split_flow_items(rest[1:-1], ln):
            if item == "":
                raise ParseError("line %d: empty flow mapping entry" % ln)
            if item[0] in "\"'":
                k, after = _split_quoted_end(item, ln)
                after = after.lstrip()
                if not after.startswith(":"):
                    raise ParseError("line %d: flow mapping entry without ':'" % ln)
                v = after[1:]
            else:
                if ":" not in item:
                    raise ParseError("line %d: flow mapping entry without ':'" % ln)
                k, v = item.split(":", 1)
                k = k.strip()
                if k == "" or not _KEY_RE.match(k + ":"):
                    raise ParseError("line %d: bad flow mapping key %r" % (ln, k))
            if k in out:
                raise ParseError("line %d: duplicate flow key %r" % (ln, k))
            out[k] = _flow_scalar(v, ln)
        return out
    if rest.startswith("["):
        if not rest.endswith("]"):
            raise ParseError("line %d: flow sequence must close on the same line" % ln)
        return [_flow_scalar(x, ln) for x in _split_flow_items(rest[1:-1], ln)]
    if rest[0] in "\"'":
        val, after = _split_quoted_end(rest, ln)
        if after.strip() != "":
            raise ParseError("line %d: content after quoted scalar" % ln)
        return val
    return _plain_scalar(rest, ln)


def load_yaml(text):
    return _Reader(text).parse_document()


def load_case(path):
    with open(path, "r", encoding="utf-8") as fh:
        text = fh.read()
    doc = load_yaml(text)
    if not isinstance(doc, dict):
        raise ParseError("%s: top-level node is not a mapping" % path)
    graders = doc.get("graders")
    if not isinstance(graders, list) or not graders:
        raise ParseError("%s: `graders` must be a non-empty sequence" % path)
    for idx, g in enumerate(graders):
        if not isinstance(g, dict):
            raise ParseError("%s: grader %d is not a mapping" % (path, idx))
        if not isinstance(g.get("type"), str):
            raise ParseError("%s: grader %d lacks a string `type`" % (path, idx))
        if not isinstance(g.get("name"), str):
            raise ParseError("%s: grader %d lacks a string `name`" % (path, idx))
        t = g["type"]
        if t == "file_exists":
            if not isinstance(g.get("path"), str):
                raise ParseError("%s: file_exists grader %r lacks a string `path`" % (path, g["name"]))
            if "exists" in g and not isinstance(g["exists"], bool):
                raise ParseError("%s: grader %r `exists` is not a boolean" % (path, g["name"]))
        elif t == "regex":
            if not isinstance(g.get("pattern"), str):
                raise ParseError("%s: regex grader %r lacks a string `pattern`" % (path, g["name"]))
            if "match" in g and g["match"] not in ("contains", "not_contains"):
                raise ParseError("%s: grader %r `match` must be contains or not_contains" % (path, g["name"]))
            if "flags" in g and not isinstance(g["flags"], str):
                raise ParseError("%s: grader %r `flags` is not a string" % (path, g["name"]))
        elif t == "llm":
            pass
        else:
            raise ParseError("%s: grader %r has unknown type %r" % (path, g["name"], t))
    if not isinstance(doc.get("name"), str):
        raise ParseError("%s: case lacks a string `name`" % path)
    return doc


# ----------------------------------------------------------------------------
# Grading
# ----------------------------------------------------------------------------

def regex_has_tree_target(g):
    target = g.get("target")
    if isinstance(target, dict) and target.get("source") == "file":
        return True
    return target == "files"


def is_deterministic(g):
    t = g.get("type")
    if t == "file_exists":
        return True
    if t == "regex":
        return regex_has_tree_target(g)
    return False


def tree_files(tree):
    out = []
    for dirpath, dirnames, filenames in os.walk(tree):
        dirnames[:] = sorted(d for d in dirnames if d != ".git")
        for fn in sorted(filenames):
            p = os.path.join(dirpath, fn)
            if os.path.isfile(p):
                out.append(p)
    return out


def grade_file_exists(g, tree):
    pat = g["path"]
    hits = [h for h in globmod.glob(os.path.join(tree, pat), recursive=True)
            if os.path.exists(h)]
    want = bool(g.get("exists", True))
    return bool(hits) == want


def grade_regex(g, tree):
    flags = re.IGNORECASE if "i" in str(g.get("flags", "")) else 0
    rx = re.compile(g["pattern"], flags)
    want_hit = g.get("match", "contains") != "not_contains"
    target = g["target"]
    if isinstance(target, dict):
        p = os.path.join(tree, str(target.get("path", "")))
        if not os.path.isfile(p):
            return not want_hit
        with open(p, "r", encoding="utf-8", errors="replace") as fh:
            texts = [fh.read()]
    else:
        texts = []
        for p in tree_files(tree):
            try:
                with open(p, "r", encoding="utf-8", errors="replace") as fh:
                    texts.append(fh.read())
            except OSError:
                pass
    hit = any(rx.search(t) for t in texts)
    return hit == want_hit


def grade_case(case, tree, out):
    k = n = m = 0
    for g in case["graders"]:
        if not is_deterministic(g):
            out.write("SKIPPED\t%s\n" % g["name"])
            m += 1
            continue
        n += 1
        ok = grade_file_exists(g, tree) if g["type"] == "file_exists" else grade_regex(g, tree)
        if ok:
            k += 1
        out.write("%s\t%s\n" % ("PASS" if ok else "FAIL", g["name"]))
    out.write("CASE %s %d/%d deterministic, %d skipped\n" % (case["name"], k, n, m))


def census(evals_dir, out):
    files = sorted(globmod.glob(os.path.join(evals_dir, "*", "*", "case.yaml")))
    tot = {"cases": 0, "regex": 0, "file_exists": 0, "llm": 0, "session": 0}
    for path in files:
        case = load_case(path)
        rel = os.path.relpath(os.path.dirname(path), evals_dir).replace(os.sep, "/")
        c = {"regex": 0, "file_exists": 0, "llm": 0, "session": 0}
        for g in case["graders"]:
            c[g["type"]] += 1
            if g["type"] == "regex" and not regex_has_tree_target(g):
                c["session"] += 1
        out.write("%s regex=%d file_exists=%d llm=%d session=%d\n"
                  % (rel, c["regex"], c["file_exists"], c["llm"], c["session"]))
        tot["cases"] += 1
        for key in c:
            tot[key] += c[key]
    out.write("TOTAL cases=%d regex=%d file_exists=%d llm=%d session=%d\n"
              % (tot["cases"], tot["regex"], tot["file_exists"], tot["llm"], tot["session"]))


def main(argv):
    args = list(argv)
    if "--version-line" in args:
        args.remove("--version-line")
        sys.stderr.write("%s %s\n" % (sys.executable, sys.version.replace("\n", " ")))
    try:
        if len(args) == 2 and args[0] == "--list":
            census(args[1], sys.stdout)
            sys.stdout.flush()
            return 0
        if len(args) == 2 and not args[0].startswith("-"):
            case = load_case(args[0])
            if not os.path.isdir(args[1]):
                sys.stderr.write("eval-grade: not a directory: %s\n" % args[1])
                return 2
            grade_case(case, args[1], sys.stdout)
            sys.stdout.flush()
            return 0
    except ParseError as e:
        sys.stderr.write("eval-grade: parse error: %s\n" % e)
        return 2
    except (OSError, re.error) as e:
        sys.stderr.write("eval-grade: %s\n" % e)
        return 2
    sys.stderr.write(__doc__.split("\n\n")[1] + "\n")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
