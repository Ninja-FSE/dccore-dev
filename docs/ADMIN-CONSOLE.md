# DCCore Admin Console (DCC CHAT)

Administrative access over an authenticated DCC CHAT session instead of channel
commands.

**Status: phase 1.** The console authenticates and accepts `help` and `quit`. The
admin commands themselves (`ban`, `unban`, `rehash`, `update`, `clearqueue`) are
still the channel commands they always were — phase 2 moves them in. Nothing
changes until you configure it: with `ADMIN_HOSTMASKS` empty, every DCC CHAT
request is ignored and the daemon behaves exactly as before.

---

## Why

`is_admin()` compares a nick against `ADMIN_NICK`. On Undernet a nick is not owned
without services auth, so anyone can take the admin nick while you are offline and
inherit every admin command, including the destructive `!clearqueue`.

The console replaces that with two independent factors:

1. **Your Undernet services login**, proved by your host.
2. **A password**, which never travels through the IRC network.

Stealing your nick gets an attacker nothing without your X account. Learning the
password gets them nothing from the wrong host.

---

## How the host proves your login

When you log into **X** and set usermode `+x`, the Undernet server replaces your
host with:

```
<your-account>.users.undernet.org
```

Only the server can issue that host, and only to someone holding that account. So
matching the host *is* verifying your services login — no password is shared with
this bot, and there is nothing in its config for an attacker to steal.

> **The ident is deliberately ignored.**
> In `nick!ident@host` the ident half is supplied by your client — anyone can set
> theirs to `flac`. Only the host is issued by the server. DCCore discards the
> nick and ident parts of any configured mask on purpose: constraining them would
> grant no security while breaking the moment your client's ident setting changes.

---

## Setup

### 1. Make sure you are authed and hidden

In your IRC client:

```
/msg X@channels.undernet.org login <youraccount> <yourpassword>
/mode <yournick> +x
```

Then check what the network now sees:

```
/whois <yournick>
```

You are looking for a host ending in `.users.undernet.org`. If you still see your
ISP hostname, `+x` did not take and the console will not let you in.

### 2. Generate a password hash

From the DCCore directory, on either platform:

```
python adminchat.py
```

It prompts twice, then prints a line ready to paste:

```
ADMIN_PASSWORD_HASH = "pbkdf2_sha256$200000$3f0a...$91c4..."
```

The password itself is never stored — only a salted PBKDF2-SHA256 digest, and it
is compared in constant time. Two hashes of the same password look different,
which is the salt doing its job.

### 3. Put both values in `local_config.py`

Create `local_config.py` next to `config.py` if it does not exist. **It is
gitignored**, so nothing here reaches GitHub. Do not put these in `config.py`.

```python
# local_config.py

ADMIN_HOSTMASKS = ["*!*@FLAC.users.undernet.org"]

ADMIN_PASSWORD_HASH = "pbkdf2_sha256$200000$3f0a...$91c4..."
```

The mask may be written either way — both mean the same thing, because only the
part after the last `@` is used:

```python
ADMIN_HOSTMASKS = ["FLAC.users.undernet.org"]        # bare host
ADMIN_HOSTMASKS = ["*!*@FLAC.users.undernet.org"]    # familiar IRC form
```

Wildcards work, and more than one entry is allowed:

```python
ADMIN_HOSTMASKS = ["FLAC.users.undernet.org", "Neo.users.undernet.org"]
```

A pattern that reduces to bare `*` is refused and logged — it would admit the
whole network and make the gate decorative.

### 4. Restart the daemon

`local_config.py` is read at import. `!rehash` reloads `config`, so it picks up
changes too, but a restart is the sure thing while you are setting this up.

---

## Using it

From your IRC client:

```
/dcc chat DCCore
```

Your client opens a listening socket and sends the offer; the bot connects back to
you. (This is iroffer's non-passive path. DCCore opens **no** listening port for
chat, so there is no firewall change to make.)

```
Chat with DCCore
Waiting for acknowledgement...
DCC Chat connection established

Welcome to DCCore
DCCore v1.10.0-RC1 - platform=posix python=3.10 rar=/usr/bin/rar

Enter Your Password:
```

Type the password and press enter:

```
Entering DCC Chat Admin Interface
For help type "help"
```

Commands are bare words — there is no channel to disambiguate from, so no `!`
prefix.

| Command | Effect |
|---|---|
| `help` | list the commands |
| `quit` | close the session |

---

## Limits and timeouts

| | |
|---|---|
| Time to enter the password | 60 seconds, then the socket closes |
| Password attempts | 3, then the socket closes and your IP is blocked |
| IP block after failed attempts | 15 minutes |
| Idle timeout once logged in | 30 minutes |
| Sessions at once | 1 |

**A second login replaces the first.** If you left a session open on another
machine, or your client froze and the server has not timed the nick out yet, just
connect again — the old session is told it was taken over and closed. The
replacement happens only *after* the new session authenticates, so nobody who
merely matches the host can drop your live console without the password.

---

## When it does not work

**The bot ignores you completely — no reply, nothing.**
This is by design: an unrecognised host gets no answer at all, so a stranger
cannot learn whether they guessed the mask correctly. It also means a wrong mask
looks identical to a broken bot. Check the daemon log:

```
[ADMINCHAT] Ignored DCC CHAT from unauthorised host: cpe-91-22-33-44.isp.net
```

That line tells you the host the server actually saw. Usually it means `+x` is not
set, or `ADMIN_HOSTMASKS` has a typo.

**The log says the password is not set.**

```
[ADMINCHAT] DCC CHAT from an authorised host refused: ADMIN_PASSWORD_HASH is not set.
```

Step 2 was skipped, or the value did not reach `local_config.py`. A console with
no password refuses everyone rather than letting anyone in.

**"address is temporarily blocked."**
Three wrong passwords from that IP. Wait 15 minutes, or restart the daemon — the
block lives in memory only.

**Locked out entirely.**
Edit `local_config.py` and restart. Until phase 2 flips the switch, the channel
commands still work, so you are never without a way in.

---

## What this does and does not protect

**Protects against:** someone taking your nick while you are offline; a hostmask
alone without the password; a stolen password used from the wrong host; brute
force, via the attempt limit and the IP block.

**Does not protect against:** anyone who has compromised your Undernet X account
*and* knows the password. The DCC CHAT connection is plaintext, so the password
crosses the wire in clear — the host check is the real gate, and the password is
depth behind it. Optional TLS is on the list for a later phase.

---

## Coming next

- **Phase 2** — the admin commands move into the console, plus `status`, `queue`,
  `uptime` and `version`. Channel commands stay working in parallel until the
  console has proved itself, then a config flag disables them.
- **Phase 3** — runtime reports route into the session, so the debug channel can
  be switched off entirely.
- **Phase 4, optional** — TLS on the chat, and a second restricted admin tier.
