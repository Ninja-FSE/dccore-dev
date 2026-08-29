# settings_file.py - reads settings.conf and applies it over config.py's defaults.
"""Plain-text settings, without turning config.py into a text file.

WHY THE DEFAULTS STAY IN PYTHON

The daemon targets Python 3.10, where the only stdlib config parser is
configparser - and configparser returns strings. tomllib, which carries real
types, arrived in 3.11. Adding a third-party TOML parser would spend this
project's "no third-party packages" property on a settings file.

So if the defaults lived in the text file too, something would have to declare
each value's type: 23 of the settings are not strings. That something is a
second list to keep in step with the first, and this codebase has already been
bitten by exactly that shape - PRESERVE_RUNTIME was a hand-maintained list of
names that drifted out of step with config.py and silently emptied live state.

Keeping the default as a Python literal makes the default ITSELF the type
declaration: `MAX_DCC_SLOTS = 5` is an int, so its override is read as an int.
There is nothing to maintain and nothing to drift.

The operator never edits Python: they edit settings.conf, and
settings.conf.sample lists every setting with its default and explanation.

NOTHING BREAKS FOR AN EXISTING INSTALL

local_config.py still works exactly as it did. config.py applies it first and
then this file on top, so an operator who has one can ignore settings.conf
entirely, or migrate at their own pace, or use both. Where both set the same
name, settings.conf wins - it is the one the operator edited most recently by
definition, being the newer mechanism.

A BAD VALUE DOES NOT STOP THE DAEMON

An unreadable file, an unknown key, or a value that will not convert are all
reported loudly and then skipped, leaving the default in place. That matches
how the rest of this codebase treats its own edges - webserver.start() logs and
returns rather than raising, and the console encoding guard exists so that one
character cannot take the process down. A daemon that refuses to start at 3am
because of one typo in one setting is worse than one that starts and says
clearly which line it ignored.
"""

import configparser
import io
import os

# Where the file lives, unless DCCORE_SETTINGS_FILE points somewhere else.
# The environment variable exists for tests and for running two instances off
# one checkout; ordinary installs never set it.
DEFAULT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "settings.conf")

# configparser insists on sections. The file is allowed to use them purely to
# group things for a human reader - they are flattened away on read, so no
# name-to-section map has to be kept in step with config.py.
_SYNTHETIC_SECTION = "__dccore__"

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


class SettingsError(Exception):
    """The file could not be parsed at all. Individual bad values do not
    raise - they are reported and skipped."""


def settings_path():
    return os.environ.get("DCCORE_SETTINGS_FILE") or DEFAULT_PATH


def is_overridable(name, value):
    """Is `name` something an operator may set in settings.conf?

    Settings are the uppercase names. The C_* colour codes are excluded: they
    are raw mIRC control bytes (`\\x0303`), protocol constants rather than
    preferences, and expressing them in a text file would mean typing escape
    sequences. A named theme setting is the right shape for that, if themes
    ever land - a theme name is an ordinary string.
    """
    if not name.isupper() or name.startswith("_"):
        return False
    if name.startswith("C_"):
        return False
    return isinstance(value, (str, bool, int, float, list, type(None)))


def parse(text):
    """Return {KEY: raw string} for every entry, sections flattened.

    Raises SettingsError if the text is not parseable, or if one key appears
    twice - silently keeping one of two conflicting values for the same
    setting is exactly the kind of quiet wrongness this file exists to avoid.
    """
    # interpolation=None: configparser otherwise treats '%' as a reference and
    # a Windows path or a password containing one fails in a way that reads
    # like a corrupt file.
    parser = configparser.ConfigParser(interpolation=None)
    # Keep the case of keys. Settings are uppercase; the default optionxform
    # lowercases everything and nothing would ever match.
    parser.optionxform = str

    try:
        parser.read_string(f"[{_SYNTHETIC_SECTION}]\n" + text)
    except configparser.Error as err:
        raise SettingsError(str(err)) from err

    flat = {}
    seen_in = {}
    for section in parser.sections():
        for key, raw in parser.items(section):
            upper = key.upper()
            if upper in flat:
                raise SettingsError(
                    f"{key!r} is set twice - once in [{seen_in[upper]}] and "
                    f"again in [{section}]. Remove one; there is no way to "
                    f"tell which was meant.")
            flat[upper] = raw
            seen_in[upper] = section
    return flat


def declared_types(namespace):
    """{NAME: type} for every setting config.py annotates.

    A module's annotated assignments populate its `__annotations__`, so this
    reads config.py's own declaration back rather than being a second list to
    keep in step with it - the same reason the default value itself was the
    type declaration before annotations existed.

    Anything whose annotation is not a plain type is ignored rather than
    guessed at, and the caller falls back to the default's own type.
    """
    annotations = namespace.get("__annotations__") or {}
    return {name: kind for name, kind in annotations.items() if isinstance(kind, type)}


def coerce(name, raw, default, declared=None):
    """Convert `raw` to `declared`, or to the type of `default` without one.

    Raises ValueError on failure.

    The declared type is what lets a setting be unset-until-configured. A
    default of None says nothing about what the value should become, so
    `RAR_BINARY: str = None` is the annotation carrying that meaning instead
    of the value having to imply it.
    """
    text = raw.strip()
    kind = declared if declared is not None else type(default)

    if default is None and not text:
        # A setting whose default is None is "unset unless you say otherwise"
        # - RAR_BINARY is the example, meaning "look on PATH". An empty value
        # in the file means the same thing rather than an empty string.
        return None

    # bool is checked BEFORE int for readers who remember why it had to be:
    # bool is a SUBCLASS of int, so the isinstance() test this used to do
    # matched True and False as well and read every flag as a number. Matching
    # the type exactly removes that trap rather than ordering around it.
    if kind is bool:
        lowered = text.lower()
        if lowered in _TRUE:
            return True
        if lowered in _FALSE:
            return False
        raise ValueError(
            f"expected a yes/no value (one of {sorted(_TRUE | _FALSE)}), got {raw!r}")

    if kind is int:
        try:
            return int(text)
        except ValueError:
            raise ValueError(f"expected a whole number, got {raw!r}") from None

    if kind is float:
        try:
            return float(text)
        except ValueError:
            raise ValueError(f"expected a number, got {raw!r}") from None

    if kind is list:
        return [part.strip() for part in text.split(",") if part.strip()]

    if kind is type(None):
        # No annotation and a None default: nothing declares what this should
        # become, so it stays the text the operator wrote.
        return text

    return raw.strip("\n")


def apply_to(namespace, path=None, log=print):
    """Read the settings file and write recognised values into `namespace`.

    `namespace` is config.py's globals(). Returns a report dict; nothing here
    raises for ordinary problems, so a caller can ignore the return value and
    still get a correct daemon with the defaults intact.
    """
    path = path or settings_path()
    report = {"path": path, "applied": {}, "unknown": [], "bad": [], "read_error": None}

    if not os.path.exists(path):
        return report

    try:
        with io.open(path, encoding="utf-8") as handle:
            entries = parse(handle.read())
    except (OSError, UnicodeDecodeError, SettingsError) as err:
        report["read_error"] = str(err)
        log(f"[CONFIG] Could not read {os.path.basename(path)}, "
            f"continuing with the built-in defaults: {err}")
        return report

    types = declared_types(namespace)
    for key, raw in entries.items():
        if key not in namespace or not is_overridable(key, namespace[key]):
            report["unknown"].append(key)
            continue
        try:
            value = coerce(key, raw, namespace[key], types.get(key))
        except ValueError as err:
            report["bad"].append((key, str(err)))
            continue
        namespace[key] = value
        report["applied"][key] = value

    _log_summary(report, path, log)
    return report


def _log_summary(report, path, log):
    name = os.path.basename(path)
    if report["applied"]:
        log(f"[CONFIG] Applied {len(report['applied'])} setting(s) from {name}.")
    for key in report["unknown"]:
        log(f"[CONFIG] {name}: ignoring {key!r} - not a setting this version "
            f"recognises. Check the spelling against settings.conf.sample.")
    for key, why in report["bad"]:
        log(f"[CONFIG] {name}: ignoring {key} - {why}. Keeping the default.")
