"""What every setting means, read from the one place it is written down.

Every setting in defaults.py carries a comment block above it, and an
inline comment after it for the short ones. scripts/gen_settings_sample.py
has parsed those into settings.conf.sample since the sample was first
generated - one source, so the explanation cannot drift from the code. The
dashboard's Settings page is the third reader (#528): "a lot of settings
are not easy to understand", and the text that explains them already
existed, one file away from the page that needed it.

So the parser lives here, and both the generator and the /api/settings
payload call it. Parsed from the SOURCE with ast, never from the imported
module: the comments are not in the module, and the file is small enough
that a parse per change of defaults.py costs nothing (cached on mtime, so
the dashboard does not re-read it per request).
"""

import ast
import io
import os

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
DEFAULTS_PATH = os.path.join(REPO_ROOT, "defaults.py")


def assignment_parts(node):
    """(targets, value_node) for a module-level assignment, else (None, None).

    `MAX_DCC_SLOTS = 3` parses to ast.Assign, but `MAX_DCC_SLOTS: int = 3`
    parses to ast.AnnAssign - a different node type, with `.target` rather
    than `.targets`. Matching only Assign makes every annotated setting
    invisible, which for the sample generator means silently emitting a
    sample with nothing in it.

    `value_node` is None for a bare annotation (`NICKNAME: str`), which
    declares a name's type without giving it a value.
    """
    if isinstance(node, ast.Assign):
        return node.targets, node.value
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        return [node.target], node.value
    return None, None


def doc_lines(source_lines, node):
    """The comment block immediately above a setting, plus its inline comment.

    Section rules (`# ---`, `# ===`) end the block rather than joining it: they
    separate settings from the numbered headers above them, and are not
    about any one setting.
    """
    doc = []
    index = node.lineno - 2                  # the line above, 0-based
    block = []
    while index >= 0:
        stripped = source_lines[index].strip()
        if stripped.startswith("#") and not stripped.startswith("# ---") \
                and not stripped.startswith("# ==="):
            block.append(stripped.lstrip("#").strip())
            index -= 1
            continue
        break
    doc.extend(reversed(block))

    # Look for the inline comment only AFTER the value ends. Splitting the
    # whole line on "#" cuts inside a string literal, so
    #     CHANNEL = "#example-one,#example-two,..."
    # produced a junk comment line reading `example-one,...#example-three"` above
    # every channel-valued setting in the generated sample.
    own = source_lines[node.lineno - 1]
    if node.end_lineno == node.lineno:
        tail = own[node.end_col_offset:]
        if "#" in tail:
            inline = tail.split("#", 1)[1].strip()
            if inline:
                doc.append(inline)
    return doc


def parse_help(source):
    """{setting name: [comment lines]} for every module-level assignment in
    `source`. Every assignment, not only overridable ones: which settings an
    operator may change is settings_file's question, and the callers ask it."""
    lines = source.split("\n")
    tree = ast.parse(source)
    found = {}
    for node in tree.body:
        targets, _value = assignment_parts(node)
        if targets is None:
            continue
        for target in targets:
            if isinstance(target, ast.Name):
                found[target.id] = doc_lines(lines, node)
    return found


_cache = {"stamp": None, "help": {}}


def help_lines():
    """{name: [lines]} for defaults.py as it is on disk right now.

    Re-parsed only when the file's mtime or size changes - a rehash after an
    edit to defaults.py sees the new text; every other call is a dict lookup.
    A defaults.py that cannot be read (a packaged install with no source, an
    unreadable file) yields an empty map: the page then shows no "?" rather
    than failing to load at all.
    """
    try:
        stat = os.stat(DEFAULTS_PATH)
        stamp = (stat.st_mtime_ns, stat.st_size)
    except OSError:
        return {}
    if _cache["stamp"] != stamp:
        try:
            with io.open(DEFAULTS_PATH, encoding="utf-8") as handle:
                _cache["help"] = parse_help(handle.read())
        except (OSError, SyntaxError, UnicodeDecodeError):
            _cache["help"] = {}
        _cache["stamp"] = stamp
    return _cache["help"]


def help_text(name):
    """One string for the page: the comment lines joined with spaces, blank
    comment lines becoming paragraph breaks. "" when there is nothing."""
    lines = help_lines().get(name) or []
    paragraphs, current = [], []
    for line in lines:
        if line.strip():
            current.append(line.strip())
        elif current:
            paragraphs.append(" ".join(current))
            current = []
    if current:
        paragraphs.append(" ".join(current))
    return "\n\n".join(paragraphs)
