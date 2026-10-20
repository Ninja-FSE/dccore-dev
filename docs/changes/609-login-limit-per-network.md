### 🌐 Failed dashboard logins over IPv6 are counted per /64 network, and per /48 (#609 follow-up)

Security advisory GHSA-jhr2-gm74-h4gh (low), fixed in its private fork and merged with this release.

The dashboard's failed-login pool (`_web_bad_ips`) and its count of logins in flight (`_web_logins_in_flight`) count
an IPv6 client under its /64 network and its /48, and an IPv6 address that carries an IPv4 one under that IPv4
address. An IPv4 client is counted as before.

- **`web_login._login_counts(ip)`** is the one list of `(key, limit)` pairs every pool function works through:
  - IPv4: `(("192.0.2.1", MAX_PASSWORD_ATTEMPTS),)`, as before.
  - IPv6: its /64 (`2001:db8:1:2::/64`) with `MAX_PASSWORD_ATTEMPTS`, and its /48 (`2001:db8:1::/48`) with
    `WEB_LOGIN_WIDE_ATTEMPTS` (30). A zone id (`fe80::1%eth0`) never reaches a key: the networks are built from the
    address's integer (`_network_key()`).
  - An IPv6 address carrying an IPv4 one is that IPv4 address (`_client_address()`): IPv4-mapped (`::ffff:192.0.2.1`,
    what a dual-stack bind reports for an IPv4 client), NAT64's well-known `64:ff9b::/96` (the last 32 bits), 6to4
    (`address.sixtofour`) and Teredo (the client half of `address.teredo`). 6to4 and Teredo are one line each, so
    they are in: a 6to4 /48 is one IPv4 host's, and without unwrapping every Teredo client of one server would share
    one /64.
  - Anything else that is not empty (a name) is one count under itself; `None` and `""` are no count, so a login
    without an address is not limited here, as before.
- `web_login._login_key(ip)` is the narrowest of those keys (derived from `_login_counts()`), unchanged for every
  input it answered before.
- `_reserve_web_login()` checks every key and claims at every key or at none, in one step under
  `_web_bad_ips_lock`: "blocked" when any key is blocked, "busy" when at any key the failures plus the logins in
  flight reach that key's limit. `_settle_web_login()` releases at every key; a right password clears every key, a
  wrong one is counted at every key by `_note_bad_web_login_locked()`. `_is_bad_web_ip()` is true while any key is
  blocked (each expired block is forgotten), and `_clear_bad_web_ip()` clears every key. One counting function, one
  block check (`_blocked_locked()`), one pool: the /64 and /48 keys never collide, and the existing expiry
  (`adminchat.forget_stale_failures()`, `BAD_IP_BLOCK_SECONDS`) applies to both. `login()` is unchanged.
- The prefixes and the wide limit are `WEB_LOGIN_IPV6_PREFIX` (64), `WEB_LOGIN_IPV6_WIDE_PREFIX` (48) and
  `WEB_LOGIN_WIDE_ATTEMPTS` (30) in `web_login.py`, not settings. IPv4 has no wide count.
- The 429 reply now reads "Another login from this address or network is still being checked."
- Not changed: the web console's session label (`build_console_command_result(command, remote_addr)` names the
  session `web:<address>` for the log and command attribution; it counts nothing, and runs behind the login). The DCC
  console's pool (`adminchat._bad_ips`) only ever sees IPv4: `parse_offer()` reads the CTCP address as an IPv4 long,
  the dial-out goes to that address, and the listener binds `AF_INET` `0.0.0.0`.
- No count across every address: it would refuse the operator's own login once others had failed (#609). The /48
  count blocks only its /48, and the global cap on password checks at once (`runtime.WEB_PASSWORD_CHECKS_AT_ONCE`)
  stays the bound across networks.
- `docs/ADMIN-CONSOLE.md` says how the web login counts wrong passwords: per address for IPv4, per /64 and per /48
  for IPv6, an IPv6 address carrying an IPv4 one as that address, and behind a reverse proxy every client as the
  proxy's address.
- **Tests:** `tests/test_failed_dashboard_logins_over_ipv6_are_counted_per_network.py` (49 tests in 7 classes; the
  clock is patched only in `web_login`, with `mock.patch.object(web_login, "time", ...)`). Through the real app
  (`create_app()`, POST /login with `REMOTE_ADDR` set, `_check_web_password` counted): three wrong passwords from three
  addresses of one /64 block a fourth unchecked, while the next /64 logs in; thirty wrong passwords from thirty /64s
  of one /48 block a fresh /64 of it unchecked, while another /48 logs in; twenty-nine leave it open; a NAT64 client
  and a plain IPv4 client share one count, as do a mapped and a plain one; a right password from another address of
  the /64 forgets its failures; a login turned away while others are checked gets the 429 text. The pools directly:
  thirty failures block, twenty-nine do not; a /64 still blocks at three inside a /48; a right password clears both
  the /64 and the /48 (and both counts start again); `_clear_bad_web_ip()` clears the /48; thirty claims in flight,
  or twenty-eight failures plus two claims, make the next /64 "busy"; a turned-away claim takes nothing at either
  key; forty parallel claims from forty /64s get exactly thirty "ok", twelve from one /64 exactly three; both blocks,
  and the /48 block alone, run out after `BAD_IP_BLOCK_SECONDS`; plus the /64 cases from before (shared in-flight
  count, settled failures, failures counted under the lock, different IPv4 clients apart, no address counts
  nothing). `_login_key()` and `_login_counts()` cases: IPv4 unchanged; `2001:DB8:1:2::9` -> `2001:db8:1:2::/64` and
  `2001:db8:1::/48`; zone ids dropped (also `fe80::%eth0`); mapped, NAT64 (`64:ff9b::c000:201` -> `192.0.2.1`, not
  `64:ff9b:1::/48`), 6to4 and Teredo addresses unwrapped; non-addresses and non-text values unchanged; every key
  unchanged when passed in again.
- **Mutants:** 24 of 24 caught, each run against the file above: the claim taken without the lock (the burst tests read the counts slowly, so they need it); no wide count; the wide limit at 31 and at 29;
  blocking at `>` instead of `>=`; the /48 not cleared by a right password; the wide prefix at 64; the wide limit
  set to `MAX_PASSWORD_ATTEMPTS`; NAT64, 6to4, Teredo and IPv4-mapped unwrapping each removed; `_is_bad_web_ip()`,
  `_clear_bad_web_ip()`, the reserve's blocked check, its busy check and the settle's release each limited to the
  first key; the claim taken key by key (a turned-away claim keeps its /64 claim); the network key built from the
  address object or the text instead of the integer; `_login_key()` returning its argument; the `isinstance` guard
  removed; the 429 text reverted; and `src/web_login.py` + `src/webserver.py` at upstream main.
