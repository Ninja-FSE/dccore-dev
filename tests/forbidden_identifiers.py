"""The names this project must never publish, as hashes.

WHY THIS FILE IS NOT IN THE EXPORT

It used to be a dict inside tests/test_no_personal_identifiers_ship.py, which
ships. That file also documents the recipe - SHA-256 of the lowercased word,
first 16 hex characters - on the line above the list, and labels every entry
with what it is: "a co-maintainer's handle", "a channel this bot serves".

A 16-hex-character truncation of an unsalted hash of a single lowercased word
is not a secret. A wordlist of IRC channel names recovers these in seconds,
and the labels tell whoever did it what they have. During the pre-publication
sweep two of this operator's channels were recovered from the shipped list by
hand, without a wordlist, by typing three guesses.

So the list moved here and this file is export-ignore'd in .gitattributes.
The guard itself still ships and still runs every structural check it has -
only the answers stopped travelling with the question.

WHAT THAT COSTS, SAID PLAINLY

The public tree has no denylist. A contributor there can add a nickname and
nothing will stop them. That is the trade: the denylist exists to protect the
people already in this project from a publication that has not happened yet,
and once the tree is public it protects nobody it can still reach. The
structural checks - real IP addresses, channel-shaped tokens, escape-decoded
words - do still run there, and they are the half that generalises.

ADDING ONE

    python -c "import hashlib;print(hashlib.sha256(b'thename').hexdigest()[:16])"

Lowercased, always. Never write the name itself in this file, or in a commit
message, or in an issue - the hash is the whole point.

A DENYLIST ONLY KNOWS WHAT IT HAS BEEN TAUGHT, which is why every entry below
was added AFTER the name had already shipped at least once. Do not read a
passing test as proof the tree is clean; read it as proof that these
particular names are gone.
"""

FORBIDDEN = {
    "73ef176d9f12809e": "a co-maintainer's handle, the same one used on IRC "
                        "and the issue tracker. Attribute an observation to "
                        "'an operator' instead",
    "5dade860d3d5eadd": "a real serving bot on a real network; it shipped in "
                        "two test files before an audit found it",
    "f0756a8e416936e7": "the maintainer's own account name",
    "13ea59307fc3f4ec": "the private development repository. Naming it in the "
                        "public tree points strangers at a repo they cannot "
                        "read, whose issue numbers resolve to nothing",
    "fcfd075cbe367c15": "a real bot on a real network",
    "33870ebe3595990b": "a channel this bot serves",
    "0f3fcff0f5c1e22d": "a channel this bot serves",
    "6205a0d9a6086904": "a channel this bot serves",
    # THE FIFTH SWEEP, and the one that showed the sweep itself needs a
    # method. A scrub of five known names still left three more in the tree,
    # and a fourth that could not be ruled out - all of them nick-shaped
    # tokens pasted from a live console into a comment or a fixture, none
    # findable by a denylist that had not been taught them.
    #
    # What found them: extract the real `git archive` export, list every token
    # used in an IRC nick POSITION - a `!nick`/`@nick` request, a
    # ":nick!user@host PRIVMSG" prefix, a "nick": fixture field - and subtract
    # the invented cast. What is left is short enough to read by eye, and the
    # real ones stand out because they arrive with a real file count and a
    # real library size beside them.
    "d50e92c47be2206c": "a real bot, with its real file count and library size",
    "4d65a69389a3655d": "a real bot, from a pasted advert line",
    "d316ed577e44e5ac": "a real requester, with a real request line",
    # Not proven real, and replaced anyway: it sat in the same fixtures as a
    # confirmed one, so it was most likely seen beside it. Replacing an
    # invented name costs nothing; missing a real one ships it.
    "62f42c35a98d86c1": "a bot seen alongside a confirmed real one",
    # A SEPARATE, INDEPENDENT SWEEP, done in parallel with the one above -
    # five more names, each riding in on an unrelated bug report the same
    # way every prior leak did.
    "964b3e699ec98455": "a real bot on a real network, named in a bug "
                        "report about its list's size-suffix format",
    "4bf41f93d01e4044": "a real bot on a real network, named in a bug "
                        "report about its RAR-trigger not matching its nick",
    "53b6f872a5e617cd": "an operator's own account name",
    "79aa93c94eb078df": "a real bot on a real network, used as a "
                        "list-browser test fixture",
    "590ab98251d542a2": "a real bot on a real network, used as a "
                        "list-fetch test fixture",

    # Found by a pre-publication audit, after three earlier passes had walked
    # past them. Every one of these was reachable by `git grep` the whole
    # time; what kept them here was where they sat, not how well they hid.
    "1df80a0541cf3a97": "a real third-party bot, with its real library size, "
                        "in a worked example copied into the daemon source, "
                        "the dashboard's own comments and a parser test",
    "87b26fb49df919fc": "a real nick holding a real speed record, inside the "
                        "BODY of a captured advert - the sender field beside "
                        "it had already been renamed",
    "8b04871d9242d20c": "a second real record-holder nick, same position, "
                        "same reason it was missed",
    "808fda88007c81d8": "a real bot's own slogan, carrying its nick, inside "
                        "the advert text rather than the sender field",
    "82ba622139a37c9b": "a real bot that requested a list, left in a pasted "
                        "console line by a scrub that edited the filename on "
                        "the SAME LINE and stopped there",
    # THE SWEEP BEFORE THE PUBLIC RELEASE. Five independent passes over the
    # real `git archive` export, each with a different lens. What they found
    # is below; what they could NOT be expected to find is why this file now
    # lives outside the export.
    "582a77c1e8f4fbd1":
        "an operator's own file-server name. It was self-declared as such in "
        "a comment and still shipped in nine files, because it was load-"
        "bearing for a migration whose entire population was two installs",
    "69f5fd0ac901ed0e": "an operator's live bot nick",
    "b3e6c1c3187f2cf7": "a channel this bot serves",
    "086edcff30f62231": "a channel this bot serves",
    "af20c9d212488bb3": "a channel this bot serves",
    "dd217f567acf0791": "a channel this bot serves",
    "df177e237270a3ed": "a channel this bot serves",
    "a4692387fc741fe7": "a channel this bot serves",
    "be60de72073464d9": "a channel this bot serves",
    "fb78768feb08ee71": "a channel this bot serves",
    "ac53fe068871b0d0": "a channel this bot serves",
    "3f1f562772e1d3f4": "a channel this bot serves",
    "0aafc3349c01aa53":
        "a channel this bot serves, and the reason channel_tokens() exists - "
        "the word regex split it on its '&' and could never have matched it",
    "2f061b26f39233c0":
        "a beta operator's channel, named in a bug report and then repeated "
        "in the SHIPPING changelog",
    "f9938da8e96d0b01":
        "a bot nick left inside a captured advert BODY. The rename pass "
        "covered sender fields; this sat in the text",
    "29f02fa6bd9a4500":
        "a bot's own tag inside a captured advert body, written with high-"
        "byte escapes - the reason words() now also reads a decoded variant",
}
