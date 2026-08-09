# Offline mode — design

Status: **proposal. Nothing is built.** No code in `src/` implements any part
of this document; every `player.py:N` is a citation, never a description of
something that exists.

Written 2026-08-07 against the working tree at d22aa81 ("Refuse to start a
second ticli"), where `src/ticli/player.py` is 9011 lines. None of the line
numbers resolve against f90f1c7, which is 148 lines shorter and predates both
the instance lock (`player.py:8889`) and the `never_the_real_state_dir` rail
§10 leans on (`tests/conftest.py:31-35`).

**Produced under adversarial review, three rounds.** A four-reader survey, then
a first adversarial pass, then a second gauntlet of three readers — a
maintainer looking for grounds to reject, the engineer who would have to build
it alone, and the user on a plane. The third round found **two closed loops
that would have shipped**: the promotion could not fire, and it could not
finish. Both are corrected below in §2 and §3, and the wrong versions are
stated rather than deleted, because the wrong version is the one a later reader
rediscovers. Every load-bearing claim was re-checked against the named file a
third time; §12 lists the findings that did **not** survive that check, with
why, because a design doc that records only the corrections it accepted teaches
the next reviewer nothing.

The decisions below are the author's and are meant to be argued with, but they
are decisions, not a menu. Anything that is Garrett's to settle is called out
as his.

---

## 1. What "fully offline" promises

The promise is about **the bytes already on this disk**, and it is stated as
something a test can fail:

> With every network entry point raising, ticli starts, reaches the TUI,
> restores its last queue paused, and plays every track that has a file in
> `~/Music/Ticli` or in the audio cache — handing the audio backend a real
> local path and making **zero** requests to do it. Every screen that cannot
> be answered from disk says *why*, in the colour this project reserves for
> failure, and never by looking empty.

That is the whole of it. Three things follow from the wording and each is a
deliberate exclusion.

**Playable means a file, not a record.** A track that ticli knows about but
has never fetched is not offline-playable, and the interface must never imply
otherwise. `downloads.path_for` (`utils/downloads.py:366`) and
`cached_audio_path` (`utils/cache.py:120`) both stat the file every time —
the index is a hint about where to look, never an answer about what exists —
so "playable" is a question with a fresh answer, and offline it becomes a
question the *list* has to answer too, not just the player. Which is a cost:
`cached_audio_path` is a directory `glob` per call and `path_for` re-reads
`downloads.json` per call, and asking either one per row per repaint is
re-committing a regression this project already measured and removed. §4(e)
gives the list layer a memo and leaves the stat to the play path, where it
belongs.

The two stores in that sentence are not equally durable and the promise does
not pretend they are. `~/Music/Ticli` is the user's, and nothing in ticli
deletes from it without being asked. The audio cache is machine-owned and
**evictable**: `enforce_budget` runs after every cache write and unlinks
fewest-plays-first (`utils/cache.py:925-1014`), and `should_cache` refuses new
admissions under pressure (`:793-822`). So the offline library that *survives*
is the download folder; the cache is the offline library that happens to still
be there. That is not changed by this feature — §5 argues why — but it is
surfaced by it, in the two confirmations §5 adds.

**Browsing degrades to what was cached, and says so.** The metadata index
holds exactly two key shapes, `playlists` and `playlist:{id}`
(`utils/cache.py:59`, `:554`, `:564`), written only by `_load_playlists` and
`_open_playlist`. So offline coverage of the browse tree is a function of
browsing history, not of what was downloaded. Albums, artist pages, favorites
and every server-side search category have no cache tier at all and will not
grow one (see §8).

**Nothing is promised for writes.** Likes, playlist edits, playlist creation
and downloads are requests by definition. Offline they are pre-empted with a
message before they are attempted, not queued.

What is explicitly *not* promised: new music, artwork for a cover-and-size
pair never rendered before, an accurate liked/not-liked heart, an album page,
an artist page, radio, or any server-side search result. Each of those has a
named rendering in §5.

**The promise and the staging must be read against each other.** The sentence
above is delivered by steps 1 and 2 of §9, not by step 5. That is a change
from the previous draft, which put "Enter plays a row on the downloads screen"
last and therefore staged the headline promise behind four other steps — a
user whose library is a download folder would have been given four shippable
steps of honesty and no way to play any of it. §9 and §11 now say the same
thing about where the work can stop.

---

## 2. The hard constraint: authentication

This section decides whether the feature exists, so it is the one to read
twice. It is also where the third review found the design's worst defect, so
the corrections are marked as such.

### What actually happens today

`run()` gates on one line — `if not self._login(): return`
(`player.py:8910`) — and `_login` reaches for the stored tokens by calling
`self.session.load_oauth_session(...)` (`player.py:2450`). That call is not
a local adoption of a token. tidalapi assigns the five fields and then, before
it can return anything, issues `GET sessions`:

```python
# tidalapi/session.py:391-405
self.token_type = token_type
self.access_token = access_token
self.refresh_token = refresh_token
self.expiry_time = expiry_time
self.is_pkce = is_pkce

request = self.request.request("GET", "sessions")
json = request.json()
if not request.ok:
    return False

self.session_id = json["sessionId"]
self.country_code = json["countryCode"]
self.locale = "en_US"
self.user = user.User(self, user_id=json["userId"]).factory()
```

Offline that raises rather than returning False, the exception is swallowed by
the broad `except Exception` at `player.py:2464`, and control falls through to
a *fresh device login* — `_login_device` prints `Starting TIDAL login...`
(`player.py:2514`) before it too fails, `_login` prints `Login failed.`
(`player.py:2483`), and `run()` returns before the TUI is ever created. A user
with a full download folder gets two misleading lines and a shell prompt.

`check_login()` is a second request (`GET users/{id}/subscription`,
`tidalapi/session.py:830-836`) but it never gets that far: it short-circuits
to False when `self.user` or `self.session_id` is unset, with no network. So
`check_login()` is safe to call offline and always answers False — it can
never be the thing that authorises an offline start.

### `load_oauth_session`'s boolean is not a signal, and never was

**Correction, third review — and this one has to be read before anything else
in the section, because two later paragraphs of the previous draft were built
on the value it deletes.** `if not request.ok: return False` at
`session.py:397-399` is **unreachable for every non-2xx response.** The
response comes from `Requests.request`, which calls `raise_for_status()` and
re-raises: `http_error_to_tidal_error(e)` for the two statuses it maps, and a
bare re-raise of `requests.HTTPError` for everything else
(`tidalapi/request.py:149-158`). `Response.ok` is False exactly when
`raise_for_status()` raises, so control never arrives at line 397 with a falsy
`ok`.

`load_oauth_session` therefore has exactly two observable outcomes: it returns
True, or it raises. A revoked token is a `requests.HTTPError` carrying a 401 —
measured against the live API, `GET /v1/sessions` with a junk Bearer answers
`401 {"subStatus":11002,"userMessage":"Token has invalid payload"}` — and not a
`False`. Every contract in this document is written in terms of exceptions
because of that. This is the *same* defect §2 correctly diagnoses in `_login`
one page above, and the previous draft reintroduced it one function over; that
is worth noticing, because it is evidently an easy mistake to make twice.

### What genuinely cannot be done, and what merely has not been done

**`session.user` is the one field with no offline path at all.** It is built
by `user.User(self, user_id=…).factory()`, and `factory()` is a request —
`users/{id}` (`tidalapi/user.py:81-85`). Nothing local can produce it, which
means **no authenticated user call can be faked**. Offline `session.user` is
None, so `session.user.favorites.tracks(...)` (`player.py:2696`) and
`session.user.playlists()` (`player.py:6511`, `:7829`) raise `AttributeError`,
not `ConnectionError` — a class `_looks_offline` will never recognise, which
is why §5 makes those three screens consult the flag rather than their
exception.

The first draft said `session_id` and `country_code` were in the same
position. They are not — both are plain attributes, `load_session` assigns
them from its arguments with no request when both `user_id` and `country_code`
were supplied (`session.py:342-370`) — and the first draft then drew the wrong
conclusion from that correction, which is the defect below.

### The deadlock the third review found

The previous draft observed, correctly, that ticli neither stores nor sets
`session_id` or `country_code` (a grep for either across `src/ticli/` returns
no hits), that `Requests.basic_request` puts both into every request's params
(`tidalapi/request.py:64-66`), and that `requests` drops params whose value is
None before the URL is built — measured:
`requests.Request("GET", url, params={"sessionId": None, "countryCode": None,
"limit": 100}).prepare().url` ends in `?limit=100`. It then concluded that "an
adopted-token session sends neither, and goes on not sending them", as though
the omission were benign.

**It is not benign. It is fatal, and it closes a loop.** TIDAL's v1 API
rejects a missing `countryCode` outright, and it does so *before* it looks at
the Bearer token. Measured against the live API on 2026-08-07:

```
GET https://api.tidal.com/v1/tracks/154291836
  → 400 {"status":400,"subStatus":1002,"userMessage":"countryCode parameter missing"}
GET https://api.tidal.com/v1/search?query=test
  → 400 {"status":400,"subStatus":1002,"userMessage":"countryCode parameter missing"}
GET https://api.tidal.com/v1/tracks/154291836?countryCode=US   (no auth header)
  → 401 {"status":401,"subStatus":1005,"userMessage":"Missing auth parameter"}
```

The third line is the proof that the `countryCode` check runs first: with the
parameter present and no credentials at all, the same endpoint gets past it and
fails on auth instead. So **a token-only session cannot make a single v1
content request.** Not a track, not a search, not a playlist. Every one is a
400, `Requests.request` raises it as a plain `requests.HTTPError`
(`http_error_to_tidal_error` maps only 404 and 429), `_looks_offline` correctly
declines to call an HTTP error a transport failure, and it is a failure, so
nothing that keys off success ever runs.

The previous draft's exit from offline was `_note_network_ok()`, "called from
the successful side of any network operation". On an offline-adopted session
there is no successful network operation to call it from, because the one
thing that would make requests succeed — the promotion, which fetches
`country_code` — was itself gated on a request succeeding. The play path,
which §3 designated as the reconnect detector, is the loop traced end to end:
`_play_track._run` → `_resolve_track` (`player.py:3309-3318`) →
`self.session.track(track.id)` → 400 → `except Exception: return None` →
`self._playing = False`. And on a process that started offline every queue row
is a `CachedTrack` by construction, so `_resolve_track` can never take its
hand-it-back fast path; the 400 is unavoidable.

There was exactly one accidental escape in the whole design — the artwork fetch
to `resources.tidal.com`, which is unauthenticated and needs no country code —
and §4(a) wired that as a *consumer* of `_note_network_ok()`, downstream of the
signal rather than a source of it.

The consequence, had this shipped: an offline-started ticli would be
permanently offline for the life of the process on a perfectly good link.
Search dead, artist pages dead, radio dead, playlists frozen behind §4(d)'s
staleness label — a label §4(d) explicitly promises is not permanent — the
heart stuck tri-state, every un-downloaded track unplayable, quit-and-restart
the only cure, and nothing on screen suggesting it. Which is precisely the
"latched `_offline` on a working link" that §11 names as the worst failure mode
and claims three structural mitigations against.

### The chosen answer

**`_login()` stops returning a bool and returns one of three module
constants** — `LOGIN_ONLINE`, `LOGIN_OFFLINE`, `LOGIN_NONE`. Named constants
rather than the strings the previous draft used, because all three strings are
truthy and `run()`'s existing `if not self._login(): return` would have started
the TUI on the no-credentials case, which §2 says must refuse. The new gate is
explicit:

```python
outcome = self._login()
if outcome is LOGIN_NONE:
    return
self._offline = outcome is LOGIN_OFFLINE
self._session_adopted_offline = self._offline
```

`_login_pkce` has a **second caller** that the previous draft never mentioned:
`_upgrade_to_pkce` at `player.py:2604`, `upgraded = self._login_pkce() and
self._finish_login()`. A truthy `LOGIN_OFFLINE` short-circuits into
`_finish_login()`, whose `check_login()` is False offline (it short-circuits on
`self.user is None`, `session.py:832`), and it prints `Login failed.` into a
suspended TUI — the wrong message, from the wrong cause, on the screen §5 says
should have refused before suspending at all. So `_upgrade_to_pkce` checks
`result is LOGIN_ONLINE` before calling `_finish_login`, and toasts the offline
message on `LOGIN_OFFLINE`.

The offline path is a new `_adopt_tokens_offline()` that does locally what
`load_oauth_session` does before its request — assigns `token_type`,
`access_token`, `refresh_token`, `expiry_time` and `is_pkce` onto the
`tidalapi.Session()` built at `player.py:2118` — and stops there. It never
calls `load_oauth_session`, because that function cannot return without the
network.

Which outcome is chosen is decided by the exception class, not by the absence
of a result (see §3). A `ConnectionError` from the stored-session branch means
`LOGIN_OFFLINE`. Anything else — a rejected token, a 401, a malformed record —
keeps today's behaviour exactly, including the fresh device login, because
"TIDAL logged you out" and "there is no network" are different facts and the
current code's inability to tell them apart is the bug underneath the
misleading message.

### Coming back: the promotion is a precondition, not a reward

`_adopt_tokens_offline()` sets five token fields, and `session.user`,
`session_id` and `country_code` are all still None. `session.user` is assigned
in exactly three places in tidalapi — `load_session` (`session.py:369`),
`load_oauth_session` (`:405`) and `process_auth_token` (`:672`) — every one of
them through `User.factory()`'s `users/{id}` request, and ticli calls only the
middle one. So something has to go and re-run it.

**`_promote_session_online()` re-calls `self.session.load_oauth_session(...)`
with the five fields already on the session.** That is one `GET /v1/sessions`,
and it is the one request in the whole v1 surface that an adopted-token session
can actually make: measured, `GET /v1/sessions` with no `countryCode` and no
auth answers `401 subStatus 1005`, not `400 subStatus 1002` — the country-code
check does not apply to it. Its response is exactly the three things missing
(`sessionId`, `countryCode`, `userId` → `User.factory()`), which is why it is
the same call a normal login already makes.

**It runs *before* the first network operation, not after a successful one.**
This is the correction. Any code path about to touch `self.session` while
`self._session_adopted_offline` is true calls `self._ensure_session_online()`
first and proceeds only if it returns True. It is still user-initiated and it
is still one request once per process: the user's keypress is the trigger, the
promotion is the request, and the operation the user asked for follows it on
the same worker thread. §3's rule that "the play path is never suppressed" then
becomes literally true instead of decorative — Enter on an un-downloaded track
offline runs the promotion and then the stream, and both succeed the moment the
link is real.

`_ensure_session_online()` is single-flighted by a `_promoting` flag the same
way `_search_fetching` and `_radio_fetching` single-flight theirs, and floored
by `PROMOTE_MIN_INTERVAL` (60s) between attempts the same way
`RADIO_FETCH_MIN_INTERVAL` floors radio — so a held-down key, or a queue of
fifty rows each asking, is one attempt.

**The outcome contract is exceptions, not the return value**, per the
correction above:

- **Clean return** — success. Re-save the tokens if `access_token` drifted,
  exactly as `_login` already does at `player.py:2460-2463`; set
  `_user_display_name` from `_get_user_display_name()` (`player.py:2417`);
  re-run `_load_favorites()` (§5); drop the offline-failed artist records and
  the search reservoir (§5); clear `_artwork`/`_artwork_request` when the
  stored pixels are None (§4a); clear `_offline` and `_session_adopted_offline`;
  `_wake()` the loop.
- **`_looks_offline(exc)`** — still no transport. Change nothing at all, leave
  `_offline` set, clear `_promoting`, and let the caller render its offline
  string. Not a failure worth a toast: the user already knows.
- **Any other exception** (a `requests.HTTPError` carrying a 401 is the case
  that matters) — the session is dead. This is the honest "TIDAL logged you
  out" moment §2 has been deferring.

**A dead session does not take the existing logout path.** This is the second
thing the third review found, and it inverts the feature's central promise.
`_logout()` (`player.py:2680-2690`) is not a state change, it is a shutdown: it
calls `delete_tokens()`, `self.audio.stop()`, clears `_current_track`, empties
`_queue`, sets `_queue_index = -1` and sets `self.running = False`. Routed from
a promotion on a daemon thread, that means the wheels touch the tarmac, the
song goes silent, the queue is erased and ticli exits to a shell for a reason
the user never asked about. The toast is very likely never seen, because
`running = False` tears down the `Live` loop that would draw it. And the next
launch is worse: `delete_tokens()` means `load_tokens()` returns nothing, and
§2's own rule is that no stored tokens **refuses** an offline start — so a
token revoked while the machine was in the air destroys the user's ability to
play files he owns.

**Decision: a third session state — signed out, still running.** Audio keeps
playing, `player_state.json` and the tokens stay on disk, every local screen
stays usable, and `Signed out — [o] to sign in again` renders wherever
`Offline` would have gone. Deleting tokens stays what it is today: a thing the
user does with `[o]`, with a confirmation. The rejected alternative is calling
`_logout()` and accepting the exit; it was rejected because the whole feature
exists to keep local files playable and this is the one path in it that would
make them unplayable.

**The flag does not clear until the promotion lands.** For a session that was
never adopted offline, `_note_network_ok()` clears `_offline` outright — its
`session.user` and `country_code` were filled at login and nothing has removed
them. For one that was, only a successful promotion clears it. The alternative
leaves a window in which the app says it is online while every user-scoped
screen is still one `AttributeError` from a red message, and a window like that
is exactly the "displays a value that isn't real" failure this project keeps
re-learning.

### Which user actions attempt the promotion

Naming them, because "any network operation" was the hand-wave that hid the
deadlock. On a process where `_session_adopted_offline` is true, these run
`_ensure_session_online()` first, on the worker thread they already use:

`_play_track`'s worker on a `_local_source` miss; `_do_search` and
`_search_more`; `_open_album`, `_open_playlist`, `_open_artist` and
`_load_artist_section`; `_load_playlists`; `_open_playlist_picker`;
`_toggle_like`; `_start_track_radio`; `_PacedRun.resolve` on its first item;
`_apply_setting`'s `offline_mode` branch when the user turns it off; and
`[u]`/`[R]` before they suspend the TUI or start a job.

Every one of those is either a key the user pressed or a screen he opened, so
no clock is involved and §3's ban on probes and timers is intact. What is
*not* on the list is anything the monitor does on its 0.5s tick, and that is
deliberate.

The residual is worth stating plainly rather than mitigating badly. A user
with an all-local queue who lands, keeps listening, and touches nothing will
stay offline: `_maybe_prefetch_next` returns before spawning its thread when
the next track is local (`player.py:3229-3233`), artwork swallows its own
transport failures (`player.py:3814-3818`), and the monitor's other jobs are
disk and IPC. Nothing on screen lies while that lasts — the status line says
`Offline`, the playlists say when they were cached, the heart says it does not
know — and the first deliberate online action fixes all of it in one request.

The rejected fix is the one the third review proposed: attempt the promotion on
any track change and any list-screen entry, floored at 60s. It was rejected
because a track change is caused by the audio backend rather than by the user,
so on an all-local queue that is a request per minute for the life of a flight
— a reachability probe with a different name, and §3 rejects those on three
grounds that all still apply. Trading a silent-but-honest offline state for a
poll is the wrong direction.

### The refusal with no credentials is necessarily post-attempt

With no stored tokens, offline start is **refused**, with a message naming the
reason: ticli has never signed in on this machine, and that needs a connection
once.

That refusal cannot be a pre-check, and the first draft wrote it as one. With
no stored tokens there is no failed call to classify, and §3 forbids a probe,
so "offline" is not knowable before a login is attempted. The refusal is
therefore decided *by* the attempt: `_login_device` and `_login_pkce` return
`LOGIN_OFFLINE` when their own transport failure satisfies `_looks_offline`,
and `_login` turns that into the named message instead of `Login failed.` (the
device tail, `player.py:2483`) or `Login cancelled.` (the PKCE tail,
`player.py:2474-2477`) — two strings that both describe a decision nobody made.

The device flow makes this easy — `session.login_oauth()` calls
`get_link_login()` immediately (`session.py:559-572`), so the first thing that
happens is the request, and `_login_device` already catches it
(`player.py:2515-2518`). The PKCE flow is the ugly one and needs its own rule.
`pkce_login_url()` is pure string building — a dict and `urlencode`, no request
(`session.py:482-500`) — so `_login_pkce` prints the URL, opens the browser and
enters its `for remaining in range(PKCE_PASTE_TRIES - 1, -1, -1)` paste loop at
`player.py:2564` before anything touches the network. The first call is
`pkce_get_auth_token` → `self.request_session.post` (`session.py:531`).
Offline that raises `ConnectionError`, the loop catches it at
`player.py:2578-2584`, and the user is told **`That didn't work. Copy the full
address, including everything after the '?'. 2 tries left.`** — three times,
blaming his clipboard for a dead network, with a live authorization code
already spent. So: a `_looks_offline` exception **breaks the paste loop on the
first try** and returns `LOGIN_OFFLINE`, rather than consuming
`PKCE_PASTE_TRIES`. §5 fixes the in-app twin of this (`[u]` refusing before it
suspends the TUI); this is the startup half, and it is the same defect at
`player.py:2471-2478`.

Rejected alternative to the refusal: starting anyway into a downloads-only
player. It is a contrived state (the download index lives in `CACHE_DIR`
alongside a credential store that would have to have been cleared separately),
and building a whole second entry point for it buys nothing.

### The expired token

**Expiry is not a gate on an offline start.** Three reasons, in order of
weight.

The stored `expiry_time` is written by `_save_session` as an ISO *string*
(`player.py:2503`) and tidalapi assigns it verbatim (`session.py:394`) and
never compares it — a full read of `session.py` and `request.py` finds
assignments only. Refresh is purely reactive: `basic_request` refreshes when,
and only when, a live response body starts with `"The token has expired."`
(`tidalapi/request.py:107-121`). So the number ticli holds is not consulted by
the machinery that would act on it, and acting on it here would be inventing a
policy nothing else in the stack shares.

The access token's expiry says nothing about the *refresh* token's, and the
refresh token is what decides whether the session comes back. That question can
only be answered by a request.

And most importantly: the expiry has no bearing on whether the files in
`~/Music/Ticli` can be decoded. Refusing to play a user's own files because a
number in a keychain has passed is a refusal with no mechanism behind it.

So: offline, an expired token and a fresh one behave identically. The status
line says `Offline` either way. When the network returns, the promotion either
succeeds, transparently refreshes, or raises a 401 — and only then can the app
honestly say the session is dead, at which point it does, as a state and not as
an exit.

The trust decision this asks Garrett to make is narrow, and it should be
stated to him in exactly these terms: **we trust an unvalidated token far
enough to keep local files playable, and no further.** A revoked token reads
as valid offline, but a revoked token offline can also do nothing — it cannot
stream, cannot search and cannot write. Nothing on screen claims "logged in"
while offline: the settings row says `Offline` and **no name at all**. The
previous draft said the row showed "whatever the last online session recorded
(see §4)", and §4 records nothing of the kind — `_user_display_name` is
assigned in exactly two places, both off `self.session.user`
(`player.py:2462`, `:2492`), and is never persisted. The forward reference
resolved to nothing, in the section arguing the trust boundary is safe. The
alternative — persisting the name into `player_state.json` — was rejected as a
new startup file write, behind a rail (§10), to put a cosmetic string on one
row.

---

## 3. Detection, and the state machine

### One flag, one seam

`self._offline: bool` and `self._session_adopted_offline: bool`, on the player.
Not `_offline_since`: the previous draft declared it and nothing in the design
ever read it — not the staleness line, not the status line, not a step, not an
assertion — and a maintained-but-unread field is worse than a missing one,
because a later reader assumes something depends on it.

The previous draft set and cleared the flag "at a call site that already caught
an exception" and "from the successful side of any network operation", naming
neither. There are 34 `self.session.` call sites and roughly 50 broad
`except Exception` blocks in `player.py`; passive voice was hiding the entire
state machine, and a rule no reviewer can check is how the deadlock in §2
survived two passes.

**Decision: one seam, not a table of call sites.** §3 already installs a
`requests.Session` subclass on `self.session.request_session` to carry
`API_TIMEOUT` (below). That subclass's `request()` override is the single place
every tidalapi request passes through — `request.py:103` reads
`self.session.request_session` on every call — so it is where both edges live:

```python
def request(self, method, url, **kw):
    kw.setdefault("timeout", API_TIMEOUT)
    try:
        response = super().request(method, url, **kw)
    except Exception as exc:
        if _looks_offline(exc):
            self._player._note_offline()
        raise
    self._player._note_network_ok()
    return response
```

The count of touched call sites in a 9000-line file is therefore **zero**, and
the classifier sees the raw `requests` exception before tidalapi has a chance
to wrap it, which is exactly what a type-based classifier wants. There is one
explicit second site, and only one: the CDN GET in
`AudioPlayer._start_download`, which is plain `requests` against
`resources.tidal.com` and never goes through `request_session`.

Note what `_note_network_ok()` does and does not do. It records that transport
is alive. For a session that logged in normally that is the whole answer and
`_offline` clears. For an adopted-offline session it is *not* the answer —
§2 is emphatic about why — and the flag clears only when
`_promote_session_online()` returns, which `_ensure_session_online()` runs as a
precondition rather than this seam running as a consequence. The seam must not
start the promotion, or a promotion in flight would re-enter itself.

### The classifier

A new `_looks_offline(exc)` beside `_looks_rate_limited` (`player.py:592`), and
unlike its neighbour it classifies on the exception **type**, not on the
message. `_looks_rate_limited` matches strings (`RATE_LIMIT_SIGNS`,
`player.py:574`) and is right to: those are TIDAL's own words in a response
body, a stable interface. A transport failure's `repr` is not — it is urllib3's
private wrapping, and matching on it is how a library upgrade silently turns
offline mode off.

```python
def _looks_offline(exc) -> bool:
    return isinstance(exc, requests.exceptions.ConnectionError)
```

**That one class, and deliberately not `Timeout`.** The previous draft said
"`ConnectionError` and `Timeout`, and nothing else", and §11 said the opposite
in the same document — "offline is entered only by a transport-class exception
and never by a 4xx, a 5xx or a timeout-shaped-but-server-side failure". Both
cannot be written. `requests.exceptions.ReadTimeout` subclasses `Timeout`, and
a read timeout is precisely a server-side, timeout-shaped failure: the server
accepted the connection and is answering slowly. Worse, `API_TIMEOUT` *creates*
that exception where none existed — today a slow link blocks — so the draft
would have converted every response slower than 30 seconds into a latched
offline state on hotel wifi, which §11 itself calls "strictly worse than one
that fails per call".

The single-class form is not a narrowing, because
`requests.exceptions.ConnectTimeout` **subclasses `ConnectionError`** (verified
in this venv: `ConnectTimeout.__mro__` is `ConnectTimeout, ConnectionError,
Timeout, RequestException, OSError`). So a connect that never lands still
counts as offline, and only `ReadTimeout` — a live server answering slowly — is
excluded. One `isinstance` check, both rules.

**`_looks_offline` is checked first, everywhere the two meet, and
`_looks_rate_limited` only sees exceptions it rejected.** Without it the string
matcher eats real offline errors. `RATE_LIMIT_SIGNS` contains the bare
substring `"429"` and is matched against `str(e)`, and a `requests` transport
failure stringifies with the whole request path in it. Measured in this repo's
venv, a DNS failure on `/v1/tracks/154291836/playbackinfo` produces

```
HTTPSConnectionPool(host='api.tidal.…', port=443): Max retries exceeded with
url: /v1/tracks/154291836/playbackinfo (Caused by NameResolutionError(…))
```

for which `"429" in str(e).lower()` is **True** — the track id contains it.
Roughly one nine-digit id in a hundred and fifty does, so today, offline, a
sliver of tracks report the rate-limit path instead of the offline one:
`_restore_state` toasts `TIDAL is rate-limiting — restore stopped. Nothing will
be retried.` (`player.py:2987-2990`) and `_stream_at_best_tier` re-raises down
the ladder as a rate limit (`player.py:7761`).

**All four `_looks_rate_limited` call sites, enumerated, because one of them
cannot take a type-based rule at all.** The previous draft said "every site
that has both" and reasoned about the sites it happened to have read.

| Site | What it has | Rule |
| --- | --- | --- |
| `player.py:963`, `_PacedRun.run`'s resolve | the live exception | `_looks_offline(e)` first: set the run's `offline` stop flag and `break`. |
| `player.py:1063`, `_PacedRun._blocked` | **only a string** — `self.results` holds `(ok, message, record)` and the exception was discarded when the worker appended `str(e)` | Unchanged, string-only. Safe because the resolve at 963 breaks the run before `_blocked()` can see an offline worker result. |
| `player.py:2953`, `_restore_state._fetch` | the live exception | `_looks_offline(e)` first: set the offline stop flag beside `blocked` (below). |
| `player.py:7761`, `_stream_at_best_tier` | the live exception | `_looks_offline(e)` first: re-raise rather than step down the ladder. |

The anchoring of `"429"` is a **single string-only predicate** with three
anchored forms — `" 429"`, `"429 "`, `"status 429"` — and nothing else. The
previous draft also proposed "the status where there is a response", which is
unimplementable without changing `_looks_rate_limited(message: str)`'s
signature and deciding per call site what to pass; all four callers pass a
string today and there is no reason for a second matcher. Dropped.

*Open question, to be pinned by a test rather than asserted:* whether a DNS
failure inside a tidalapi call always surfaces as `ConnectionError` at the
seam, or can arrive as a bare `socket.gaierror` through some path. The survey
measured `requests.exceptions.ConnectionError` wrapping `NameResolutionError`
on `load_oauth_session`, and that is the expected shape, but the test that
asserts it is the thing that makes it true for this project.

### Why not a probe, and why not polling

A reachability probe was rejected on three independent grounds, any one of
which is sufficient. It is a new timer, and `ai/WORKING-RULES.md:103` makes
the monitor thread's existing 0.5s tick the ceiling on wakeups — "roughly zero
power" is a product goal, not a nicety. It is a new request against exactly
the API whose burst rate got the owner's IP blocked (`ai/INCIDENTS.md` #1),
spent on a question nobody asked. And it answers the wrong question: a captive
portal returns 200 to a probe and 302 to `api.tidal.com`, so a probe that
passes while every real call fails is not a hypothetical, it is the common
airport case.

The promotion in §2 is not a probe by any of those three tests: it adds no
wakeup (it runs on a worker the user's keypress already started), it is a
request whose *answer the app needs* rather than a proxy for one, and its
result is the real question.

Failure inference also has precedent in this codebase, twice. One failed
search request already poisons all four server-side scopes —
`_search_reservoir` records `stopped` and `message` once
(`player.py:6165-6167`) and `_fail_waiting_search_views` answers every waiting
view from it — and the artist page keys a failure per `(artist, section)` so a
held `Tab` cannot fan out. Offline is that same shape with a wider scope.

### The degraded middle

**Connected but slow, or black-holing.** This is the dangerous case and it
needs a change ticli cannot infer its way out of. tidalapi passes no timeout
on any request: `request.py:103` calls

```python
self.session.request_session.request(method, url, params=..., data=..., headers=...)
```

and a grep for `timeout`, `max_retries`, `HTTPAdapter` or `mount(` across
every module in the installed package returns nothing. `requests`' default is
`timeout=None`. Against a socket that accepts and never answers, the survey
measured `load_oauth_session` still blocked after 15 seconds — on the main
thread, holding the instance lock taken at `player.py:8889`. Every
`Loading albums…`, every `Searching…`, every `_search_fetching` and
`_browse_loading` flag latches for the life of the process, and no amount of
detection in ticli's own code recovers a thread already inside `requests`.

So ticli installs its own — the same subclass that carries the two edges above,
assigned onto `self.session.request_session` immediately after
`tidalapi.Session()` is constructed (`player.py:2118`). No new dependency, no
patching of a package in the venv that a reinstall would overwrite, and
`request_session` is a plain public attribute that `request.py:103` reads on
every call. `API_TIMEOUT = (5, 30)` — five seconds to connect, because a
connect that takes longer is not about to become a good connection, and thirty
to read, sized against the largest real response (a 300-row search page).
Deliberately not `DOWNLOAD_TIMEOUT = (10, 60)` (`player.py:642`), which is a
CDN transfer of a whole track and earns its longer read. A `ReadTimeout` from
this is a per-call failure and never offline, per the classifier above.

This is the one change in the whole design that touches every request the app
makes, online included. It is listed as a risk in §11.

**Connected, TIDAL answering, but refusing.** A 429, a 401 subStatus 4006 or a
bot-detection page is *not* offline and must not be reclassified as one.
`_looks_rate_limited` keeps its existing stop-and-report behaviour untouched,
because the rule it enforces (never retry) is stricter than anything offline
mode wants. A 5xx is a per-call failure and stays one. Offline means **no
transport** specifically, because that is the only class of failure for which
"your library is still here" is the right answer.

### Rules about latching

**The play path is never suppressed — the *user's* play path.** Pressing Enter
on a track with no local copy while `_offline` is true still attempts it: the
promotion if the session was adopted offline, then the stream — already
generation-guarded by `_play_gen`. That attempt is simultaneously the user's
retry and the reconnect detector, and it is the cheapest possible probe because
the user asked for it.

**Auto-advance skips rather than attempts.** The rule as first written left
auto-advance undefined and the undefined default is that offline playback stops
dead at the first un-downloaded row. The monitor advances by calling
`_play_queue_index(self._queue_index + 1)` (`player.py:3640-3641`);
`_play_track` sets `self._playing = True` on the calling thread
(`player.py:3038`), its worker misses `_local_source`, `_stream_description`
raises, and the `except Exception` sets `self._playing = False`
(`player.py:3091-3093`). The monitor's dead-poll branch requires
`self._playing` (`player.py:3586-3590`), so nothing ever advances again: a
restored queue of fifty downloaded tracks with one un-downloaded track at
position three plays three tracks and ends. Nothing warns first either —
`_maybe_prefetch_next` correctly skips the request when the next track is local
(`player.py:3229-3233`), so the queue's first non-local row is discovered only
by arriving at it.

**Decision: offline, the monitor skips queue rows with no local file and toasts
once per run** — `Skipped 4 tracks — not downloaded` — rather than attempting
each. The rejected alternative is attempting each and stopping on the first
failure with the queue screen marking where it stopped; it was rejected because
it spends one request per unplayable row for a result the row already knows
(`_playable`, §4e), and because "the album stopped in the middle" is the exact
silent-halt shape `_monitor_playback`'s existing failure branch was written to
remove. The one-toast-per-run cap is what keeps a mostly-un-downloaded queue
from becoming a toast per track.

**The manual skip is the same rule, and the previous draft put it in the wrong
bucket.** `→` is not the play path. `_next_track` → `_play_queue_index` sets
`self._queue_index = index` first and then calls `_play_track`
(`player.py:3320-3329`), so offline on an un-downloaded row it produces exactly
the dead player above — music stops, cursor parked on a row that cannot play,
`self._playing` False so nothing advances — reached by the most ordinary key on
the screen, and on a plane skipping is what you do. So **`_next_track` and
`_prev_track` move to the next/previous row with a local file** when `_offline`
is set, and toast `Nothing downloaded further ahead` when there is none. Enter
on a row the user deliberately selected keeps the never-suppress rule, because
that is a retry; a skip is not a selection.

**Seeking.** The previous draft said the player "works" and the word `seek`
appeared nowhere in it, which leaves a reader unable to tell whether it was
considered and found safe or simply not considered. It is mostly safe and one
case is not. Offline, seeking a track with a local file is entirely local:
`AudioPlayer.seek_to()` moves an mpv over IPC or respawns ffplay against the
same local source. Seeking a track *without* one falls through — `_flush_seek`
calls `self._play_track(track, seek=target)` whenever `seek_to` returns False
(`player.py:3405-3411`), and `seek_to` returns False for any dead process — so
it lands in the same swallowed `except Exception: self._playing = False` and
must get the same toast step 1 gives the play path. Separately: a pause taken
while streaming cannot be resumed offline unless the cache copy completed,
because `resume()` on ffplay falls back to `self._current_url` when there is no
cache file (`player.py:1774-1786`). Step 2's disk-first change makes the common
case local; these two are what is left.

**The bulk paths are suppressed hard.** Three call sites turn one outage into
many requests today, and all three must treat `_looks_offline` the way they
already treat `_looks_rate_limited`:

- `_stream_at_best_tier` (`player.py:7737-7768`) re-raises on a rate limit and
  `continue`s on everything else, walking `_tier_ladder` down from the
  requested tier. A MAX download offline is therefore four dead `get_stream()`
  calls. Offline must re-raise on the first.
- `_PacedRun.run` (`player.py:956-969`) breaks on a rate limit and otherwise
  records the failure and continues to the next item. A 50-track `[D]` offline
  is 50 × up to 4 requests over roughly 100 seconds, ending in
  `Downloaded 0 songs · 50 failed`. Offline must break, with the count of what
  was not attempted.
- **`_restore_state`'s legacy `track_ids` branch** (`player.py:2925-3019`) —
  the third one, missed by the first draft, and the only one of the three that
  runs unprompted at startup, which is to say in exactly the scenario this
  document exists for. A state file carrying no usable `tracks` records (every
  pre-upgrade file, and also a current file where any one record lost its `id`,
  since the guard at `player.py:2907-2910` requires `all(...)`) spawns a daemon
  thread that calls `self.session.track(tid)` once per queued id
  (`player.py:2951`), paced by `REFETCH_MIN_INTERVAL = 2.0` (`player.py:551`)
  between request *starts*. Its `_fetch` sets `blocked` only for
  `_looks_rate_limited` (`player.py:2952-2957`); a `ConnectionError` is "a
  plain failure [that] just drops the track" and the loop walks the whole
  queue. Fifty ids is fifty dead requests over a hundred seconds, unattended,
  before the first keystroke. Offline, `_fetch` must set its own stop flag and
  break the loop exactly the way `blocked` already does at
  `player.py:2982-2992` — no attach, the file left whole, and a toast naming
  the network rather than the rate limiter.

**What the interrupted `[D]` does with the bytes it already had.** The previous
draft counted requests and never mentioned bytes; the word `.part` did not
appear in it. Today a download that fails mid-transfer is discarded whole —
`_download_deliver`'s except calls `self._discard_staging(track_id)`
(`player.py:7311`), which unlinks `~/Music/Ticli/.ticli-{id}.part`
(`player.py:7671-7683`) — and the cache tier does the same under a
`logger.debug("Audio download did not finish")` (`player.py:1449-1455`).
Nothing resumes and nothing is byte-ranged. **Decision: that stays, and it is
said out loud instead of discovered.** The offline stop toast is
`Stopped — 12 downloaded, 38 not attempted, 1 partial discarded`, so a hundred
megabytes never vanish into a debug log. Range-resume was rejected here as a
feature of its own: it needs a server that honours `Range` on signed CDN URLs,
a length check against the index, and a decision about what a partial at the
wrong tier means, none of which is about playing offline.

There is a related orphan the discard does *not* cover and which this document
should not pretend to fix: if ticli is killed rather than failing — the lid
closing at the gate — `_discard_staging` never runs, and the
`.ticli-{id}.part` is permanent. `downloads.present()` walks the index and
stats recorded entries only (`utils/downloads.py:379-404`), so the file is
invisible on the downloads screen and uncounted in the settings readout.
That sweep joins §4(c)'s deferred adoption work, which is the other job that
has to walk the folder rather than the index.

**The legacy restore's second-order effect, and the latch that actually
expresses it.** With every fetch failing, `tracks` is empty, the attach at
`player.py:3004` is skipped, and `_restore_pending` is never cleared
(`player.py:3015`). The previous draft concluded from that that "the offline
session knowingly saves no queue", and **that conclusion is false.**
`_save_state`'s guard is `if self._restore_pending and not self._queue:`
(`player.py:2806-2808`) — it needs the latch *and* an empty queue. The moment
the offline user plays anything the queue is assigned (`_play_browse_track` at
`player.py:6488`, `_select_search_result` at `:6280`, and step 2's downloads
screen), the guard falls through, and the monitor's 10-second autosave runs
`_save_state_locked`, rewriting `track_ids` and `tracks` from the offline queue
(`player.py:2811-2836`). A fifty-track saved queue is replaced by the four
downloaded songs played over the Atlantic — the exact shrink the decision was
written to prevent, arrived at *through* the latch being held, and §9 would
have put the false version on screen.

**Decision: a distinct latch.** An offline stop of the legacy restore sets
`self._restore_abandoned`, checked unconditionally at the top of `_save_state`,
independent of `self._queue`. While it is set, `_save_state` merges the
position through `_merge_position_into_saved_state_locked()` and returns —
the same thing the existing early return does, and a position merge cannot
shrink the file. It is cleared by a successful restore or a successful
promotion (after which a full save is safe, because the ids can be resolved
again). The trade, stated because it is a real loss: **the queue the user
builds offline is discarded on quit.** That is the right direction — the file
on disk is fifty tracks the user chose and the session's is four he could
reach, and the stale-but-complete copy is the one whose whole job is surviving
a bad launch — but it is a loss, not a free win. The rejected alternative
(release the latch and let the session save) is what the code does today under
any use at all.

### Chosen versus detected

`offline_mode` in settings (§7) is a *demand*, not a report. When it is on,
network call sites are skipped before they are made, including the play-path
attempt above and the legacy restore, because a user who asked for offline must
not have the app go behind his back on a metered link. Detected and chosen
offline render identically except for one word in the status line — `Offline`
versus `Offline (by choice)` — because to every screen they mean the same
thing.

Turning it back **off** is itself an offline→online edge, and the only one a
chosen-offline session will ever get: nothing is attempted while it is on, so
no user action reaches `_ensure_session_online()`. So `_apply_setting`'s
`offline_mode` branch calls it directly for a session that was adopted offline.
Without that, a user who starts offline, flips the setting on, and flips it
back off has a session that can stream but has no `session.user`, and no other
event will fix it.

---

## 4. What the local library must become

### Three stores stay three stores

`downloads.json`, the metadata index and the artwork cache are not merged.
They have three different lifecycles and merging them gives one of them
another's. The download index is machine-owned bookkeeping about **user-owned
files** — its own docstring says so (`utils/downloads.py:165-173`) — and it is
deliberately outside `MetadataCache.clear()`, which clears the index, owned
audio and artwork (`utils/cache.py:469-473`) and must go on not touching the
map to the user's music. The artwork cache is keyed on the *render size*
(`{cover_id}-{cols}x{rows}.art`, `utils/artwork.py:661`) because the pixels
are the render, which is a key space nothing else shares.

Five additions instead.

### (a) `track_record` gains `cover`

`cover_id_of` reads `track.album.cover` (`utils/artwork.py:782-790`), and a
`CachedTrack`'s album is a `_Named` whose `__slots__` is `("name",)`
(`utils/cache.py:233-237`, `:278`) built from a record that stores only
`"album": album.name` (`:303-313`). So a restored row can never carry a cover
id, `_art_layout` returns None, and every `.art` file already rendered on this
disk is unreachable after a restart. Adding `"cover"` to `track_record` — five
fields to six, in both the metadata index and `player_state.json`, since
`_save_state_locked` flattens through the same function (`player.py:2827`) —
makes the existing artwork cache work offline for free.

**Three edits, not one**, because growing `_Named` is not sufficient:

- `cache.track_record` adds `"cover": getattr(album, "cover", None) if album
  else None`.
- `_Named.__init__(self, name, cover=None)` with `__slots__ = ("name",
  "cover")`, so old records read as absent rather than breaking — the same
  defaulted-read migration `is_pkce` already established.
- `CachedTrack.__init__` builds `_Named(album, cover)` whenever **either** the
  name or the cover is present. Today it is `self.album = _Named(album) if
  album else None` (`utils/cache.py:277-279`), so a record with a cover id and
  a missing or empty album name yields `self.album = None` and `cover_id_of`
  returns None off it — no artwork, silently, which is the artwork subsystem's
  answer to everything. The step-2 test would have passed on records with album
  names and quietly not covered that branch.

`utils/artwork.py` is in this step's file list for one line: `cover_id_of`'s
docstring says "Cached rows carry only an album name" (`:785-786`), which
stops being true here.

Offline artwork is then honestly bounded: **the sizes you have already seen**.
A cover never rendered at this terminal size needs a fetch, and offline it
shows nothing.

This addition is not optional and it is not late: it ships in **step 2**, in
the same commit as the disk-before-network swap, because that swap is what
makes a `CachedTrack` the thing left in `self._current_track`. Without the
`cover` field, step 2 alone would silently remove album art from every restored
and cached row, online included.

One live defect to fix while in here: a fetch that fails caches a permanent
negative for that `(cover, cols, rows)` key — a stored None is treated as an
answered question and never re-requested (`player.py:3795-3797`). Offline that
means a cover stays missing after the network returns until the track changes
or the window is resized.

The fix has to touch **two** fields, not one, and the first draft named only
the first. `self._artwork` is a single slot `(cover, cols, rows, pixels)`, and
clearing it is not enough: `_artwork_text` would find no answer and call
`_request_artwork`, which returns immediately because `self._artwork_request`
still equals that same key (`player.py:3809-3812`) — the cover stays missing
exactly as before. So the promotion clears `self._artwork` **and**
`self._artwork_request` whenever the stored pixels are None, then `_wake()`s
the loop. That is not a new idiom: it is the pair the `show_artwork` toggle
already clears together, under a comment that says why — "Forget both the
picture and the request that produced it, so turning it back on re-asks rather
than showing a stale cover" (`player.py:8036-8041`).

It clears **unconditionally on any None**, not only on a None recorded while
offline, because the slot carries no record of *why* the pixels are None and
widening it to carry one would put a flag's history into a render cache to save
at most one refetch of one cover. A None from a genuinely artless album is
re-asked once per reconnect and answers None again from the same code path that
answered it the first time; that is a cheap wrong guess, where the alternative
is a permanently blank cover on a working connection.

### (b) The download index records a track record

The survey claimed the index has no duration. That is true of `record()`,
which writes `path`, `quality`, `granted`, `bytes` and `at`
(`utils/downloads.py:338-343`) — but *not* of `track_metadata`, which already
computes `id` and `duration` alongside the tag fields
(`utils/downloads.py:212-226`) and has them thrown away. The correction
matters, because it means the data exists at the moment of the write.

So: each index entry gains a `"track"` key holding the same flattened dict
`cache.track_record` produces. One record shape, which means `CachedTrack`
reads it and the downloads screen builds rows from the same constructor as
everything else. `INDEX_VERSION` stays 1 — `load_index` only requires the
version to equal 1 (`utils/downloads.py:267`) and an older entry simply has no
`"track"`, falling back to `describe()`'s lossy `(title, artist, album)` from
the path (`:442-464`) with no duration. A version bump here would be equivalent
to deleting the library, since nothing rebuilds the index from the folder;
additive fields and defaulted reads are the only safe shape this file has.

This is a quality improvement to rows that already exist, not the thing that
makes them playable — step 2 plays a downloads row built from `describe()`
alone, because `_local_source` reads `getattr(track, "id", None)` and nothing
else and `_downloads()` rows already carry `{"id", "path", "entry", "bytes"}`
(`utils/downloads.py:379-410`). That separation is what lets §9 move Enter
forward to step 2 and leave the index change in step 5.

### (c) Adoption, deferred but specified

Nothing reconstructs `downloads.json` from the folder, and it lives in
`CACHE_DIR` — the directory `utils/cache.py:13` invites the user to delete at
any moment. `rm -rf ~/Library/Caches/ticli` orphans the entire download
library permanently: the files stay in `~/Music/Ticli` and play in any other
app, and ticli can never see them again. The cache tier does not have this
problem, because `MetadataCache.reconcile()` adopts orphan audio files at zero
plays.

The fix is an embedded identifier plus a folder scan, and it is deferred to
after the staging in §9 — but the shape is worth writing down now because it
constrains (b). For MP4 there is already an unused slot: `_ilst` writes
`\xa9cmt` from `meta["comment"]` (`utils/tags.py:144-151`) and
`track_metadata` never sets that key. For FLAC, `_vorbis_comment`'s field list
(`utils/tags.py:307-313`) would take a new `TIDALTRACKID`. Neither needs a new
box or a freeform atom. The same folder scan is the only thing that can find
the orphaned `.ticli-*.part` files §3 describes, so the two jobs ship together
or not at all. Deferred rather than dropped because it is orthogonal to playing
offline and because a reader for those tags is real new code.

### (d) `MAX_AGE_SECONDS` becomes a label, not a cliff

`MetadataCache.get` returns None for any entry older than 30 days
(`utils/cache.py:448-457`), and the constant's own comment names the case:
"Only reachable by being offline (or not opening a playlist) for a month."
On day 31 offline, the playlists list and every cached playlist's tracks vanish
at once — `get_playlists` and `get_playlist_tracks` both read through `get()`
(`utils/cache.py:553-568`) — while the JSON sits on disk intact and nothing on
screen says a threshold was crossed.

Local search does *not* vanish with them, and the difference is the proof that
the rule is in the wrong place: `iter_tracks` iterates `self._load()` directly
(`utils/cache.py:572-588`) and never consults `get()` or `MAX_AGE_SECONDS`, so
on day 31 it still yields every record. What search loses is only its
*provenance*: `names` comes from `get_playlists()`, so it is empty, every row's
`playlist` field falls back to the literal `"Playlist"` —
`names.get(playlist_id, "Playlist")`, `player.py:6263` — and `matched_playlists`
is empty, so the fourth ranking bucket, the playlist's own name, matches
nothing. The first draft of this document claimed day 31 also produced
`Nothing cached to search yet — open Playlists once to index them`; it does
not, and cannot. That branch requires `scanned == 0` (`player.py:6269-6271`),
and `scanned` counts what `iter_tracks` yielded, which is everything. A query
with no hits gets `No results in your playlists`; a query with hits gets its
rows, from playlists the list screen is simultaneously insisting do not exist.

Two accessors disagreeing about whether the same file has expired is the
argument on its own.

**Decision: additive, not a signature change.** The previous draft asserted
both in consecutive sentences — "`get()` stops dropping and starts reporting"
*and* "a second accessor returns `(records, age_seconds)`" — which are
different changes with different blast radii, and named neither of `get()`'s
two callers. Concretely:

- `MetadataCache.get()` is left **exactly as it is**, threshold included, so
  no future caller silently loses the 30-day guarantee.
- A new `MetadataCache.get_aged(key) -> tuple[list | None, float]` returns the
  records and their age with no threshold applied.
- `get_playlists` (`utils/cache.py:553`) and `get_playlist_tracks` (`:563`) —
  `get()`'s only two callers — switch to it and return `(records, age)`.
- Their three call sites take the tuple: `_load_playlists` (`player.py:6503`),
  `_open_playlist` (`player.py:6543`) and `_search_own_playlists`
  (`player.py:6235`).

**Online behaviour does change, and the previous draft denied it.** Today a
40-day-old entry makes `get()` return None, so the list paints *nothing* first
and waits for the fetch; after this it paints the stale list with its age.
That is intended, it is what §9 step 4's own test asserts, and saying "online
nothing changes in practice" while shipping a test for the change was the kind
of contradiction that makes a reader stop trusting the rest.

Offline a stale list paints with its age on the line above it:
`Cached 6 weeks ago · offline`. For a process that started offline, the
replacing round trip cannot happen until `_ensure_session_online()` has landed,
because `_load_playlists`'s worker dereferences `session.user` — so the age
line persists until the user's first deliberate online action, and then it is
replaced. §2 and this section are written to be read together for that reason.

The justification is the project's own rule read carefully. "Never display a
value that isn't real" forbids showing a six-week-old list *as if it were
current*; it does not forbid showing it labelled. Offline, the alternative to
a labelled stale list is not a current list — it is no list, and "you have no
playlists" is the larger lie. This is the same move gated quality tiers
already make: shown, dimmed, with the reason, rather than hidden.

### (e) A cheap playability predicate for the list layer

Two screens need "does this track have a file" once per row: the queue (§5,
dimming and the `↓` marker) and offline search (§6, playability-first
ordering). Asked the honest way that is `_local_copy` → `_local_source` →
`downloads.path_for` → `load_index()`, a full `json.loads` of `downloads.json`
per call (`utils/downloads.py:261-270`, `:366-375`), plus `cached_audio_path` →
`audio_dir().glob(f"{id}.*")`, a directory glob per call
(`utils/cache.py:120-134`). Per row, per repaint, twice a second.

The codebase has already been here. `_downloaded()` exists precisely to avoid
it and its docstring says so — "the alternative is a `path_for` per row per
repaint, which is finding F6 of `ai/reference/data-path-audit-2026-07-26.md`:
229 ms of UI-thread JSON, twice a second" (`player.py:4425-4433`). But its
memo, `self._downloads_ids` (`player.py:2313`, filled at `:4436-4438`), covers
the **download tier only**, and §1's promise is explicitly both tiers.

**Decision: `self._cached_ids` beside `_downloads_ids`**, filled from one
`audio_dir()` listing and invalidated by the same events that call
`_forget_downloads()` (`player.py:4440-4443`) plus
`MetadataCache.invalidate_audio_count()`. Then
`_playable(track_id) = self._downloaded(id) or str(id) in self._cached_ids`.

The division of labour is the point and should not blur: **`_local_source`
stats, and stays the play path's answer**; `_playable` is a memo, and is the
render and ranking answer only. A file deleted by hand between the memo and
the keypress makes a row look playable for one repaint and then fall through
to the network like any other miss — which is the same freshness contract
`_downloaded()` already has.

---

## 5. Screen by screen

**Player.** Works. Restore lands paused, unchanged — that is locked
(`ai/DECISIONS.md:16`). The status line gains `Offline`. The substantive
changes are that `_play_track` stops resolving before it looks at the disk
(§9, step 2), that `_next_track`/`_prev_track` skip to the nearest local row
(§3), and that the seek fallback gets the play path's toast (§3).

The badge is worth getting right, because two documents get half of it wrong
and the code is missing the other half. `_download_badge` returns
`f"{label} · downloaded"` where `label` comes from `_tier_label` →
`QUALITY_LABELS` (`player.py:3165-3173`, `:2109-2114`), which maps
`LOW/MEDIUM/HIGH/MAX` to `96k AAC` / `320k AAC` / `16/44.1 FLAC` /
`24/192 FLAC`. It cannot emit `LOSSLESS · downloaded` or `HIGH · downloaded`:
`LOSSLESS` is tidalapi's spelling of `granted` and `_tier_label` translates it
away, and `HIGH` is ticli's own tier name, which the label table also
translates. Both spellings survive only in prose written before the tier
rename — `ai/DECISIONS.md:274` and `_local_source`'s own docstring
(`player.py:3135`) — and both should be corrected when something else takes
those lines. A tier that was never recorded still says a bare `downloaded`.

**There is no `· cached` badge, and §8's amendment cannot ship without one.**
`_local_source`'s cache branch returns `(cached, None)` — an explicit None
badge (`player.py:3153-3157`) — and `_build_player_display` falls back to
`QUALITY_LABELS.get(self._quality_name)` when `_playing_badge` is None
(`player.py:3895-3896`), so a cached file is currently announced as *the
setting*, not as itself. Grepping the repo for `· cached` returns nothing. So:
a new `_cached_badge(quality)` beside `_download_badge`, returning
`f"{label} · cached"` and a bare `cached` for an unrecorded tier, returned from
the cache branch instead of None. This improves the online case too, since
`_tier_is_enough` accepts a cached copy stored *above* the setting
(`QUALITY_RANK[stored] >= QUALITY_RANK[wanted]`) and the player currently
announces the lower number. It ships in step 2, next to the other
`_local_source` work.

The like heart becomes tri-state. Today `_load_favorites` swallows its failure
with a bare `except Exception: pass` (`player.py:2692-2699`), so `_liked_ids`
stays empty and every track draws the hollow `♡` — the interface positively
asserting "you have not liked this" when the truth is "we could not ask". A
third state, drawn dim or blank, is the honest answer. `[l]` offline toasts
`Offline — likes need a connection` rather than being a dead key with a
silently swallowed exception (`player.py:3464-3479`).

The third state needs a *leaving* edge as well as an entering one, and the
first draft gave it only the entering one. `_load_favorites` is called exactly
once, from `run()` (`player.py:8919`), so an offline start would leave
`_liked_ids` empty for the life of the process: every track on every screen
would draw the third state for the rest of the session even after hours back
online, and `[l]` would then toggle from a set that knows nothing — the first
press on an already-liked track sending `add_track` again
(`player.py:3471-3476`). So favorites join the promotion's success path in §2
(it has to run *after* the promotion, since it dereferences `session.user`),
guarded by a `_favorites_loaded` flag so a burst cannot refire it. And
`_load_favorites` stops `pass`-ing: it records that it failed, so the third
state has a real source rather than being inferred from an empty set that a
user with no likes at all also produces.

**Queue.** Restores from `player_state.json`'s `tracks` records at **zero
requests** (`player.py:2907-2923`) — and that claim covers the record format
only. A legacy `track_ids` file takes the branch at `player.py:2925-3019`,
which offline restores nothing at all (§3): it stops on the first transport
failure, says so, leaves the file whole, and sets `_restore_abandoned` so the
session cannot overwrite it. Rows whose track fails `_playable` (§4e) are
dimmed and lose the `↓` marker; Enter on one still attempts, because of the
never-suppress-the-play-path rule, and toasts on failure. Auto-advance and `→`
do not attempt them — they skip, per §3 — so a queue that is half downloaded
plays its downloaded half rather than stopping at the first gap.

**Playlists.** The best-behaved screen today: `_load_playlists` paints the
cached list first and only sets a message when there was nothing cached, with
the comment `Offline with a cached list is a usable player, not an error`
(`player.py:6519-6521`). Unchanged except for the staleness line from §4(d)
and the failure colour. `_open_playlist` has the same shape
(`player.py:6575-6577`) and keeps it; its rows are `CachedTrack` shims that
become playable in step 2.

**Album.** `_open_album` has no cache tier, so offline it is a dead end. Today
it says `Failed to load album` in **`dim green`** (`player.py:6312`, rendered
at `:4122`) — the exact style `Playlist is empty` uses, information colour for
a failure. Offline it says `Offline — this album was never cached` in the
failure style. It does not gain a cache tier (§8).

**Artist page, all four tabs — reachable offline only when the link dropped
while the page was open.** That scoping sentence is new and it changes how much
of this paragraph matters. `_open_artist` has exactly two callers:
`_select_search_row`'s `item["type"] == "artist"` branch (`player.py:6288`) and
the similar-artist row on the artist page itself (`player.py:6467`). Both are
server-side results. From a cold offline start there is no third door — no key
opens an artist from a queue row, a downloads row or a My Music hit, and those
rows are `CachedTrack` shims whose artists are `_Named` objects carrying a name
and no id, so no such key could be built without one. **That gap is deliberate
and is listed in §8**; the alternative is storing artist ids in `track_record`
and adding a key, which is a browse feature rather than an offline one.

For the case that does arise — the page was open when the link died — the
machinery is already right: sections are recorded per `(artist id, section)`,
the presence of a record answers "has this tab been visited" so a held `Tab`
cannot fan out, and a failed section renders `bold red` with `[Enter] retry`
(`player.py:4178-4183`). Offline reuses it with an offline-specific string per
section, alongside the existing `ARTIST_SECTION_FAILED` strings
(`player.py:2052-2057`).

One change: an offline failure record must not be sticky the way a real failure
is. The promotion drops records that failed for being offline, so tabbing back
retries instead of showing a stale red screen for the rest of the session.
Everything else keeps the "presence of a record is the whole answer" invariant,
including the whole-dict replacement idiom at `player.py:6357-6373`, so the
paint thread never reads a half-updated map. **The tab on screen is excepted.**
The drop happens on whichever worker just promoted, while the main thread reads
the same field in `_build_artist_display` (`player.py:4173`). `_artist_record()`
then returns None and the render maps `record is None` to `Loading albums...`
in dim yellow (`player.py:4173-4176`): a screen claiming to load while nothing
is loading, since `_load_artist_section` only ever runs from navigation
(`player.py:5792`, `:6327`, `:6443`, `:6455`). The retry is gone too —
`_select_artist_row` forces a reload only when `record["state"] == "failed"`
(`player.py:6453-6455`). So the drop skips the visible tab and calls
`_load_artist_section(force=True)` for it instead, which writes a real
`loading` record and starts a real request, then `_wake()`s.

**Search.** The `Tab` cycle survives intact and costs nothing extra, because
one failed request already answers all four server-side scopes through the
reservoir's `stopped`/`message` (`player.py:6165-6167`). Four changes.

The message stops being `f"Search failed: {e}"` rendered in `dim green`
(`player.py:6165`, `:4045`) — 90 characters of `HTTPSConnectionPool(host=…)`
in the colour that means "nothing matched" — and becomes
`Offline — TIDAL search needs a connection` in the failure style.

Offline, the cycle **starts on My Music** rather than All. That is the whole
UX of offline search: the only scope that can answer is the one you land on,
and landing on four dead scopes first is four keystrokes of nothing.

The reservoir's failure memory defers to `self._offline` while it is set, so
retyping offline does not buy a fresh dead request per query. (Typing itself
never searches — `_apply_search_scope` is reached only from
`_cycle_search_filter` and `_do_search`, `player.py:6072`, `:6084` — so this is
about `Enter` and `Tab`, not about keystrokes.)

**And that memory gets an explicit clearing edge**, which the previous draft
gave to artwork, favorites and artist records and forgot here. Search is the
first thing anyone does when the network returns and the one screen with no
`[Enter] retry`, so a memory with no clearing edge means landing, typing the
song you have been thinking about for six hours, and being told you are offline
on a working connection. The promotion drops the reservoir and every view; and
because `_do_search` is itself one of the actions that *runs*
`_ensure_session_online()` first (§2), the ordinary path is that Enter promotes
and then searches, in one keystroke.

**Radio.** `[r]` needs `get_track_radio` and has no local answer. Offline it
toasts `Offline — radio needs a connection` rather than
`Radio unavailable — queue unchanged` (`player.py:3532-3533`): the same honest
shape with the cause named, and it still leaves the queue untouched. Rejected:
building a local "radio" from the download folder. That is shuffle wearing
radio's name, and `[r]` means TIDAL's mix from this song.

**Add-to-playlist `[y]`, and the three mutation paths.** Pre-empted offline
with a toast, rather than attempted. Note `_open_playlist_picker` currently
swallows its fetch failure entirely (`player.py:7836-7837`) and consults no
cache, so offline it displays `No playlists you can edit` — an assertion that
the user owns none. Even online, an honest empty state here needs the failure
distinguished from the answer. This screen, `_load_playlists` and
`_load_favorites` are the three that must read `self._offline` rather than
their own exception, because on an adopted-token session their failure is an
`AttributeError` off `session.user` (§2) and no transport classifier will ever
recognise it.

**Settings.** Works completely; the config is local and `SETTINGS_SPEC` needs
nothing from the network. Five changes: the logged-in-as row says `Offline`
(and no name — §2); `[u]` refuses before suspending the TUI, because
`_upgrade_to_pkce` (`player.py:2593-2604`) enters `_suspended_tui()` and calls
`_login_pkce`, which walks the user through `PKCE_PASTE_TRIES` paste attempts
(`player.py:2564-2591`) before the first network call; `[R]` refuses before
starting, since `_refetch_candidates` reads two indexes and is accurate offline
while the job it launches is entirely network; `[d]` opens a downloads screen
that now plays; and **two** destructive paths gain an offline-specific
confirmation, because both destroy the only copies the user can play.

`[x]` is the obvious one and it already has a confirmation to amend
(`_build_clear_cache_confirm`, `player.py:4968-4975`). `cache_budget_gb` is the
one the first draft missed, and it is worse for having no confirmation at all:
it is a `SETTINGS_SPEC` row, so every `←` press goes through `_set_setting` →
`_apply_setting`, which calls `self._cache.enforce_budget()` inline under the
comment "Lowering the budget evicts right now, not at some later write"
(`player.py:8058-8062`). `enforce_budget` unlinks fewest-plays-first
(`utils/cache.py:925-1014`), so a user offline who nudges the budget down one
gigabyte silently deletes songs he is currently able to play, one keypress at a
time. **Offline, lowering `cache_budget_gb` prompts, and the prompt says how
many playable songs the eviction will unlink.**

The previous draft stopped there, and neither half of that sentence has a
mechanism. Both are specified now, because the doc pointed at the wrong seam:

- **The count needs a dry run that does not exist.** `enforce_budget`
  (`utils/cache.py:925-1014`) measures and unlinks in the same pass and returns
  bytes freed. So: `MetadataCache.eviction_preview(budget_bytes) -> tuple[int,
  int]` (files, bytes), running the same `audio_value` ordering without
  unlinking. One function, in `utils/cache.py`, in step 4.
- **The hook point is one layer up from `_apply_setting`.** By the time
  `_apply_setting` runs, `_set_setting` has already written `self.config[key]`
  and is about to call `save_config` (`player.py:7958-7969`) — a confirmation
  there prompts about a change already persisted. The existing pending-confirm
  idiom intercepts in `_adjust_setting`, before `_set_setting`
  (`player.py:7982-7985`, `_disable_songs_pending`).
- **And `_adjust_setting` is not the only entry.** `_commit_setting_edit`
  (`player.py:7996-8007`) types a number straight into `_set_setting`, so a
  user can type `1`, press Enter, and evict with no prompt at all. So
  `self._budget_evict_pending` is set in **both**, when `self._offline and
  value < current`, answered in `_handle_key` next to `_disable_songs_pending`,
  with `_build_budget_evict_confirm` reading the preview — and the config write
  happens only after `y`.

What is deliberately *not* built: an offline-aware eviction policy. The audio
cache stays machine-owned, `should_cache` goes on refusing new admissions under
pressure (`utils/cache.py:793-822`), and being offline does not pin anything.
Pinning is the download folder's job — that is the whole distinction §4 is
built on — and a cache that changes its own eviction rule based on a transient
flag is a cache whose contents nobody can predict. The honest fix for "I want
these to survive" is `[D]`, and the confirmations above are what make the
difference visible at the moment it matters.

**Downloads screen.** Becomes the offline library, which reverses a documented
decision. Its docstring says it is "Deliberately not a player: Enter does
nothing here and there is no `[a] play all`. Playing a row means resolving a
track id through the session, and this screen's whole claim is that it makes
no request" (`player.py:4678-4685`). The reversal is legitimate for exactly
one reason: the stated premise stops being true in step 2. Once `_local_source`
runs before `_resolve_track`, playing a row on this screen costs nothing,
because every row on it by construction has a file. The screen's real claim —
browsing it makes no request — survives untouched; only Enter can ever spend
one, and only on a miss, which on this screen means a file that vanished
between the `stat` and the play.

**This moves to step 2**, with the swap that justifies it, rather than to step
5 where the previous draft had it. A row's `id` is already in `_downloads()`
(`player.py:4411-4423`, memoised), and `describe()` gives it a title, artist
and album, so a `CachedTrack` can be built with no index change at all; §4(b)'s
`"track"` records upgrade those rows later. Staging the only way to play a
download folder behind four other steps was the single worst consequence of the
previous ordering.

This needs a dated `DECISIONS.md` entry and the docstring rewritten in the
same commit.

---

## 6. Search offline

`_search_own_playlists` (`player.py:6206-6273`) is the one search surface that
already works with no network: it reads `self._cache.get_playlists()` and
`self._cache.iter_tracks()`, runs on the UI thread because a scan of a few
thousand rows is instant and happens on `Tab` or `Enter` rather than per
keystroke, and has correct distinct messages for "cache off" and "cache empty".
It is the model. Its limit is where it reads from.

**Coverage.** `iter_tracks` yields records from `playlist:{id}` keys only, and
those are written exclusively by `_open_playlist`'s fetch
(`player.py:6565-6566`). So the index holds tracks from playlists that have
been **opened**, and a downloaded track that never appeared in an opened
playlist is invisible to search — including, absurdly, one the user downloaded
five minutes ago from an artist page.

**The generalisation.** The scope searches the union of two sources:
`cache.iter_tracks()` and the download index's new `"track"` records (§4b),
deduped by track id with the download record winning, because it names a file
that exists. Nothing else joins the union: album pages and artist pages have
no store, and search results are deliberately not persisted (the reservoir is
per-session and in-memory, and staleness is the reason).

**The guard has to narrow with it.** As written today the function never gets
as far as reading anything: `if not self._cache.enabled:` returns at once with
`Playlist search needs the metadata cache — turn 'Cache playlists' on in
settings` (`player.py:6229-6234`). The two stores are independent —
`downloads.json` lives outside `MetadataCache` entirely and is not gated by
`cache_metadata`, whose `enabled` property is "whether the metadata index may
be read or written" and nothing more (`utils/cache.py:396-399`) — so a user
with the metadata cache off and five hundred downloaded tracks would get that
message and zero rows, offline, on the only search surface that can answer him.
So the early return narrows to the `iter_tracks()` half: the download-index
half always runs, and `cache.enabled` becomes a reason one source is missing
rather than a reason to answer nothing.

Which means three distinct empty states, and the rename invalidates the
strings for all three (`player.py:6269-6271`):

- Metadata cache off, downloads present, query matched none of them —
  `No results in your downloads · playlist search needs the metadata cache`.
  Two facts, because one of them is fixable in settings and the other is not.
- Both stores empty — `Nothing here yet — download a song, or open a playlist
  to index it`. Not today's `open Playlists once to index them`, which is now
  only half the answer and is, offline, advice the user cannot take.
- Both stores read, neither matched — `No results in your music`, the direct
  descendant of `No results in your playlists`.

**The name.** Because the source broadens, the label broadens: `My Playlists`
becomes **`My Music`**. One string in `SEARCH_FILTER_LABELS`
(`player.py:2085-2091`); the scope *key* stays `playlists`, which already
reads oddly for a documented reason — `tidal_playlists` took the obvious name
because the reservoir's categories are named the way `session.search()` names
them.

**Ranking.** Keep the existing four buckets in their existing order — title,
then artist, then album, then playlist name — for the reason already written
into the code: a playlist name is one string standing in for every track under
it, so last place keeps "type the playlist's name, get its tracks" working
without letting it flood anything. Then add one rule **above** all four:
**offline, a row that passes `_playable` (§4e) sorts before one that does
not.** Within each group the four buckets apply unchanged. The predicate is
the memo, not `_local_source`, because this runs on the UI thread.

The justification is narrow and should stay narrow: offline, a row you cannot
play is not a better answer than a row you can, whatever field matched.
Online, playability is not a ranking signal at all and the ordering is
untouched — a downloaded track is not a better search result on a good
connection, it is just one that starts faster.

Each row keeps its provenance line: the playlist it came from, or
`downloaded` for a row that exists only in the download index.

Rejected: a seventh scope for downloads. The scope row is already six wide,
`Tab`-cycling a seventh costs a keystroke on every search forever, and the
question a user asks offline is "what can I play", not "which store is it in"
— which is an ordering, not a filter.

---

## 7. Settings, keybindings, surfacing

**One `SETTINGS_SPEC` row and no new keybinding.**

```python
{
    "key": "offline_mode",
    "label": "Offline mode",
    "kind": "bool",
    "default": False,
    "desc": "Never touch the network. Plays only what is already on this disk.",
}
```

That is the whole surface. `utils/config.py:1-18` states the contract — a
future setting is one new row, and the table drives defaults, load-time
coercion and the page — and `DEFAULTS` is derived from `SETTINGS_SPEC`
(`config.py:163`), so an existing config missing the key reads the default.

**No key.** Not for scarcity: `b`, `e`, `f`, `g`, `i`, `j`, `w` and `z` are
each bound in zero handlers (grepped for `key == "x"` and `key in ("x"` across
`player.py`; `a`, `o`, `u` and `x` are taken in one or more modes). A key is
available. The argument against it is that this repo's action keys exist for
*actions* — `[x]` clear, `[o]` log out, `[u]` upgrade, `[R]` re-fetch, `[d]`
downloads, each commented in `_handle_settings_key` with why it is not a
`SETTINGS_SPEC` row — and "offline" is a persisted preference plus an observed
condition, neither of which is an action. A momentary "go offline now" key
would be a mode toggle for something the app already observes, and it would
have to disagree with the setting the moment the network returned.

Offline is *surfaced* everywhere without being *toggled* anywhere except the
settings page: a word on the player status line, the failure strings on every
list screen, and the pre-empting toasts.

**No `--offline` CLI flag.** `cli.py` has `--quality` and `--login-flow`, both
of which exist because they change a decision made *before* the TUI can offer
one. Offline does not: the state is detected at startup and the preference is
reachable from inside the app on the first keystroke. A flag would be a second
source of truth for a state the app can observe.

**Config version stays 5.** `CONFIG_VERSION = 5` (`config.py:27`) and
`_migrate` exists to rename or re-mean values, never to add them; unknown keys
already ride along on save so an older build cannot eat a newer one's
settings. This is a purely additive key with a new default, so no bump. The
corollary is a constraint on the rest of this design: `offline_mode` must not
change what `cache_songs` or `cache_metadata` *mean*, because that would be
the v5 quality-rename situation and would need a migration plus the
write-a-real-file-and-reload round-trip test that `ai/INCIDENTS.md` #4
demands.

---

## 8. What is not built, and why

**No offline queue of writes for replay on reconnect.** A like, a playlist
add, a removal — none is stored to be replayed later. Two reasons: replaying a
batch on reconnect is precisely the retry-storm shape `ai/WORKING-RULES.md:15`
bans, and a write queued for three days and fired against a session that may
have been revoked in the meantime is a write the user did not re-consent to.
Offline, these keys say what they cannot do.

**No background sync, no "download my library" job.** `[R]` already re-fetches
everything at the current tier, paced by `REFETCH_MIN_INTERVAL` and
single-threaded on the API side by construction. A sync is that job with a
scheduler attached, and a scheduler is a new timer.

**No album cache tier.** Offline album pages stay dead ends. Adding one
doubles the index key space to serve a screen the user reaches transiently on
the way to a track, and the tracks they actually keep are reachable from the
downloads screen and from search.

**No route to an artist page from a local row.** Both callers of `_open_artist`
are server-side results (`player.py:6288`, `:6467`), so from a cold offline
start the artist page is unreachable. Opening one would mean storing artist ids
in `track_record` and adding a key to the queue and downloads screens; that is
a browse feature, and offline is not the reason to build it.

**No resume of an interrupted download.** A `.part` killed by a transport
failure is discarded, as today; §3 makes the toast say so. Range-resume needs
a CDN that honours `Range` on a signed URL, a length check against the index,
and a rule for a partial fetched at a tier the setting has since left.

**No reachability probe, no heartbeat, no captive-portal detection.** §3. And
no interval-based promotion attempt either — §2 says why that is the same
thing wearing the promotion's name.

**No re-render of a cached `.art` at a new terminal size.** The pixels are the
render and the size is in the key; a new size needs the JPEG. Offline artwork
is the sizes already seen, stated as such.

**No folder-scan adoption of orphaned downloads or orphaned `.part` files in
the staged work.** Specified in §4(c), deferred; both need the same folder walk
and a tag reader that does not exist.

**No persisted display name.** §2. The offline settings row says `Offline` and
nothing else.

**No Windows work.** `ai/WORKING-RULES.md:126` — knowingly unsupported, the
input path is `termios`/`tty`.

**One thing that is changed, and needs Garrett's sign-off.** The cache's
below-tier rule. `_local_source` skips a cached copy stored below the current
quality setting so the track is re-fetched at the tier that was asked for
(`player.py:3155-3159` via `_tier_is_enough`, and locked in
`ai/DECISIONS.md:253-278` as the deliberate opposite of the download tier's
rule). Offline there is nothing to fall through to, so that rule refuses to
play a file that exists in order to fetch one that cannot be fetched.
**Offline, the below-tier cached copy is played, with an honest badge naming
what it really is** — `96k AAC · cached`. This does not touch the download
tier's rule at all, and it is consistent with the cache rule's own stated
justification, which is about *fetching* at the right quality.

Two things the previous draft left implicit and which make the amendment
shippable rather than aspirational. It is **step 2**, beside the other
`_local_source` work — not unscheduled, as it was, which would have shipped
§1's promise minus every below-tier cached track. And the badge is **new
code**, `_cached_badge`, specified in §5 — without it `_local_source` returns
`(cached, None)` and the player announces the quality *setting* over a 96k AAC
file, which is the exact lie `_local_source`'s own docstring says the download
badge exists to prevent. It is still an amendment to a locked decision, so it
is his.

---

## 9. Staging

Five steps. Each is independently shippable, each leaves ticli working, and
the first two are improvements even if offline mode is never finished. Steps 1
and 2 together deliver §1's promise for a download-folder library; steps 3 to 5
make it a feature.

### Step 1 — Stop failing silently, stop amplifying, stop hanging

No offline mode yet. Install the `requests.Session` subclass on
`self.session.request_session` carrying `API_TIMEOUT = (5, 30)` — the one seam
(§3), initially for the timeout only. Add
`_looks_offline(exc) = isinstance(exc, requests.exceptions.ConnectionError)`,
checked before `_looks_rate_limited` at the three of its four call sites that
hold a live exception (`player.py:963`, `:2953`, `:7761`; `:1063` keeps
string-only matching for the reason in §3's table), and anchor the `"429"` sign
in `RATE_LIMIT_SIGNS` so a URL cannot match it. Give `_play_track`'s worker a
toast instead of a bare `except Exception:` that only sets
`self._playing = False` (`player.py:3091-3093` — pressing Enter offline is
currently indistinguishable from pressing pause, and this is a standing
violation of the project's most-enforced rule), and give `_flush_seek`'s
`_play_track` fallback the same one. Make all **three** amplifiers stop on a
transport failure rather than stepping down and grinding on:
`_stream_at_best_tier`, `_PacedRun.run` (whose stop toast names the discarded
partial), and `_restore_state`'s legacy `track_ids` loop, whose `_fetch` gains
an offline stop flag beside `blocked` and whose stop sets `_restore_abandoned`
and leaves the file untouched.

*Files:* `player.py`.
*Tests* (new `tests/test_offline.py`): Enter on a track whose `get_stream`
raises `ConnectionError` produces a toast naming the network; the same for a
seek that falls through to `_play_track`; a MAX download against a raising
session makes **one** `get_stream` call, not four; a five-track paced batch
makes one, not five; a legacy state file of 20 `track_ids` against a session
whose `track()` raises `ConnectionError` makes exactly **one** call, returns
without pacing (`test_cache.py`'s per-call `LATENCY` sleep makes "finished
fast" assertable, §10), and leaves the 20 ids on disk after ten seconds of
autosave with a non-empty queue; the measured
`HTTPSConnectionPool(… /v1/tracks/154291836/playbackinfo …)` string classifies
as offline and **not** as rate-limited; `requests.exceptions.ReadTimeout`
classifies as **not** offline while `ConnectTimeout` classifies as offline; a
recording stub in `request_session` position sees a `timeout` on every call.
Each watched failing first.

### Step 2 — Play from disk before the network, and let the library play

Move `local, badge = self._local_source(real)` ahead of
`real = self._resolve_track(track)` in `_play_track._run`
(`player.py:3047` vs `:3067`), resolving only on a miss. `_local_source` reads
`getattr(track, "id", None)` and nothing else, which every `CachedTrack`
carries; the media title needs `real.name` and `real.artists`, both present on
the shim (`utils/cache.py:253-279`); `cache_key=real.id` is the same id. The
queue-copy swap at `player.py:3052-3059` moves into the miss branch with the
resolve it belongs to.

Four things ship with it because they are the same change:

- **§4(a)'s `cover`**, all three edits. Skipping the resolve on a hit means
  `self._current_track` stays a `CachedTrack`, and `artwork.cover_id_of` reads
  `track.album.cover` off a `_Named` that cannot carry one — so moving the
  resolve without this would remove album art from every restored, cached and
  downloaded row **online included**, and the artwork subsystem would swallow
  it in silence.
- **§5's `_cached_badge`** and §8's offline below-tier branch in
  `_local_source`, so a cached file is announced as itself rather than as the
  setting.
- **§4(e)'s `_cached_ids` / `_playable`**, since the render is about to start
  asking per row.
- **The downloads screen plays**: Enter and `[a] play all`, rows built as
  `CachedTrack` from `_downloads()` plus `describe()`, no index change needed.
  Plus the `DECISIONS.md` entry and the rewritten docstring.

This is the single highest-leverage step in the document, and after it a user
whose library is a download folder can play all of it with the network down.
It also saves one live request per cached row **online**.

*Files:* `player.py`, `utils/cache.py`, `utils/artwork.py` (one docstring),
`ai/DECISIONS.md`.
*Tests:* the survey's own probe, turned into a regression test — a
`CachedTrack` row, a session whose `track()` raises, a real file at
`downloads.path_for(id)`, asserting the backend was handed that exact path and
the network was touched zero times. Plus: after playing that same restored row
from disk, `artwork.cover_id_of(self._current_track)` still returns the cover
id; a record with a cover and **no** album name still returns it; a `.art` file
written in one "session" is found after a simulated restart; offline with MAX
selected, a HIGH-cached file plays and the rendered status line reads
`16/44.1 FLAC · cached`, not `24/192 FLAC`; a 200-row offline queue repaint
reads `downloads.json` **zero** times; playback started from the downloads
screen with every network door shut. The existing `_player_offline` helper
(`tests/test_downloads.py:1889`) is the pattern: it nails `_stream_description`,
`_stream_url` and `requests` shut with `pytest.fail` messages naming what
leaked. Note why this gap survived: every existing offline test feeds
`_play_track` live tidalapi-shaped objects with no `cached` attribute, which
`_resolve_track` hands straight back.

### Step 3 — Start offline, and be able to come back

`_login` returns `LOGIN_ONLINE` / `LOGIN_OFFLINE` / `LOGIN_NONE`;
`_adopt_tokens_offline` assigns the five token fields with no request; `run()`
replaces `if not self._login():` (`player.py:8910`) with the three-way gate in
§2 and sets `_session_adopted_offline`; `_upgrade_to_pkce` (`player.py:2604`)
checks `is LOGIN_ONLINE` rather than truthiness; the status line and the
settings row say `Offline`; no tokens means a refusal with a message, decided
by the login attempt rather than before it. `_login_device` and `_login_pkce`
return `LOGIN_OFFLINE` on a `_looks_offline` failure, and `_login_pkce` breaks
its paste loop on the first one instead of spending `PKCE_PASTE_TRIES` telling
the user to check his clipboard.

`_promote_session_online()`, `_ensure_session_online()`, the `_promoting` flag
and `PROMOTE_MIN_INTERVAL` land here, wired into the call sites §2 enumerates,
because a start that can never come back online is not a shippable step — and
because the promotion is a *precondition* of the first request, not a
consequence of one, a session that started offline can make no request at all
without it. `_note_network_ok()` / `_note_offline()` are added to the seam
installed in step 1. A dead session becomes the signed-out-still-running state,
which is emphatically **not** `_logout()`.

Two consequences to state on screen rather than discover: until the promotion
lands, the playlists list, the picker and the heart show the offline string;
and an offline start against a *legacy* state file restores no queue and, while
`_restore_abandoned` is set, saves none either — the file on disk survives the
session intact and this session's queue does not.

*Files:* `player.py`.
*Tests:* a `_FakeSession` whose `load_oauth_session` raises `ConnectionError`
— `tests/test_login.py:168-204` already swaps the whole session object, so this
is a small addition — asserting `_login` returns `LOGIN_OFFLINE` and that
`login_oauth` was never called (today it is: offline escalates into a real
device-auth attempt). A rejected-token fake still gets today's fresh-login
behaviour. No credentials refuses, and `run()` returns. A PKCE first run whose
`pkce_get_auth_token` raises `ConnectionError` calls `input()` **once**, not
three times. `_upgrade_to_pkce` on `LOGIN_OFFLINE` never calls
`_finish_login`. And the promotion, end to end against the fake described in
§10: adopt tokens with no request, restore the link, and prove the **next user
action results in real playback of a streamed track** — not merely that
`_offline` went False. A promotion that raises a 401 leaves `self.running`
True, `self._queue` unchanged, the audio process alive and `load_tokens()` still
answering. A promotion that raises `ConnectionError` changes nothing at all.

### Step 4 — Something true on every screen

The state threaded through the render: search styling, offline scope start and
the reservoir's clearing edge; artist section strings and their un-sticking on
the promotion (visible tab reloaded, the rest dropped); the album message; the
playlists staleness line and `MetadataCache.get_aged` with its two callers and
three call sites; the tri-state heart and its `_favorites_loaded` reload; the
auto-advance skip, the `→`/`←` skip and their one toast; the artwork
un-sticking of both `_artwork` and `_artwork_request`; the offline confirmations
on `[x]` and on lowering `cache_budget_gb`, the latter via
`MetadataCache.eviction_preview` and `_budget_evict_pending` set in **both**
`_adjust_setting` and `_commit_setting_edit`; and the pre-empting toasts on
`[l]`, `[r]`, `[y]`, `[R]`, `[u]`.

*Files:* `player.py`, `utils/cache.py`.
*Tests:* screen assertions in the style of `tests/test_display.py`, which
drives the real `Live` against `tests/vt.py`; a cache test that a 40-day-old
entry comes back from `get_aged` with its age, that `get()` still drops it, and
that it is still replaced by a live fetch online; a test that four `Tab`
presses offline cost zero requests; offline search after a promotion re-issues
exactly one request for the same query; a reconnect on a failed artist tab that
leaves the screen saying `Loading albums…` **with a request in flight**, not
without one; a queue of local, non-local, local played end to end offline with
one skip toast and no `get_stream` call, and the same queue walked with `→`
held, ending on the third track playing; typing `1` into `cache_budget_gb`
offline and pressing Enter prompts before anything is unlinked or saved.

### Step 5 — The download index becomes searchable

`"track"` records in the download index (§4b), upgrading the downloads rows
step 2 already plays; the search union, its narrowed `cache.enabled` guard, its
three empty states and the playability-first ordering; `My Playlists` → `My
Music`.

*Files:* `utils/downloads.py`, `utils/cache.py`, `player.py`, plus
**`CLAUDE.md`** — which documents the old scope name and the old semantics in
three places and would otherwise instruct every future agent from the wrong
map: the scope list in the Search section (`CLAUDE.md:125` and `:131`) and the
paragraph at `CLAUDE.md:183`, which claims `"My Playlists" is answered entirely
from cache.iter_tracks()` and stops being true the moment the download-index
half lands. That paragraph becomes the union of the two stores plus the offline
playability-first ordering.
*Tests:* an old-format index entry (no `"track"`) still lists and still plays,
falling back to `describe()`; `cache_metadata` off with two downloaded tracks
returns those two rows rather than the metadata-cache message; offline, a
downloaded match sorts above an un-downloaded one and online it does not.

---

## 10. Test plan

Everything here follows conventions already in the suite; the file is new and
so is one rail on the fake session.

**Faking the network.** A session is a `types.SimpleNamespace` or a small
class carrying only what the code touches — `audio_quality`, `is_pkce`,
`track(tid)`, `user.playlists()`, `playlist(pid)` — as in
`tests/test_cache.py:44-96` and `tests/test_downloads.py:57-96`. Offline is
faked by making those methods raise `requests.exceptions.ConnectionError`, not
by touching a socket. The CDN half goes through `patch_get(monkeypatch,
module, get)` (`tests/fakes.py`), which replaces **both** `requests.get` and
`requests.Session` because patching only the former fakes a function
production no longer calls — the `INCIDENTS.md` #2 shape. Point it at a
function that calls `pytest.fail` with a message naming what leaked.

**The fake must be able to see the deadlock.** This is new, and without it the
suite is a rubber stamp for the defect §2 corrects. A `SimpleNamespace` whose
`track(tid)` returns an object has no notion of `country_code`, so it succeeds
on exactly the calls production 400s on — step 3's headline test ("raises once,
then succeeds") would be green by construction and red forever in production.
So the fake session **carries `session_id` and `country_code`, and raises
`requests.HTTPError` with the real body
`{"status":400,"subStatus":1002,"userMessage":"countryCode parameter missing"}`
for any call made while `country_code is None`** — mirroring
`Requests.basic_request`'s param assembly (`tidalapi/request.py:64-78`). Its
`load_oauth_session` is what fills those fields, exactly as tidalapi's does.
Then the offline→online edge can be asserted end to end rather than by reading
a flag.

**What is asserted.** Not that a flag was set. The bar is
`ai/WORKING-RULES.md:134`: the path handed to the audio backend, the bytes at
that path compared to the bytes that should be there, and a request count of
zero. Where a real transfer must be proven end to end, the suite's loopback
`_Server` (`tests/test_downloads.py:99-144`) already exists.

**Rails first.** If any part of this writes a new file at startup — it does
not, and §2's rejection of a persisted display name is part of why — the
`conftest.py` fixture lands in the same commit, before the test. That rule was
written because the instance lock appeared in the owner's real
`~/.config/ticli` on the first full-suite run; `never_the_real_state_dir`
(`tests/conftest.py:31-35`) is the rail that came out of it, and every test in
step 3 runs behind it because they all call `run()`.

**Exception fixtures.** Four, all real rather than invented. The transport one
is the measured `HTTPSConnectionPool(host=…, port=443): Max retries exceeded
with url: /v1/tracks/154291836/playbackinfo (Caused by NameResolutionError(…))`
— it must classify as offline and must **not** classify as rate-limited, which
is the regression test for anchoring `"429"`. A `ReadTimeout` and a
`ConnectTimeout`, asserted to classify not-offline and offline respectively.
And the rate-limit one is whatever `RATE_LIMIT_SIGNS` was written against,
unchanged, so the anchoring cannot quietly turn off the stricter rule it sits
next to.

**Config.** Assert `offline_mode` is in `SETTINGS_SPEC` and therefore in
`DEFAULTS`; assert an existing v5 config without the key loads with the
default; do it by **writing a real `config.json` and reloading**, never by
calling `_migrate` in isolation — `INCIDENTS.md` #4 was a migration that was
dead code and passed its own unit test.

**Timing and pacing.** `test_cache.py`'s fake session sleeps a `LATENCY` per
call on purpose, so a test can assert that a batch offline finishes *fast*
rather than grinding through 50 paced attempts.

**Watch every one fail first** (`ai/WORKING-RULES.md:161`). For step 2 in
particular the failing state is trivially reachable — the survey already
observed `audio.plays == []` with the file on disk — so there is no excuse for
committing a green-from-birth test here.

---

## 11. Risks, and how this could be worse than nothing

**The timeout is the widest blast radius in the design.** `API_TIMEOUT` on
`session.request_session` affects every request the app makes, online
included, and a read timeout that is too short turns a slow-but-working link
into a broken one. Thirty seconds is chosen against the largest real response
and is roughly three times any observed page fetch, but it is a number, and the
honest position is that it is the one change here most likely to need adjusting
after real use. It ships in step 1 precisely so it gets that use before
anything depends on it. What it does **not** do any more is latch: a
`ReadTimeout` is a live server answering slowly and `_looks_offline` excludes
it by construction (§3), which is the correction to a contradiction the
previous draft carried between §3 and this section.

**A latched `_offline` on a working link is the worst failure mode**, and the
previous draft's three mitigations included one that could not fire and one
that could not finish. The corrected set:

1. The play path is never suppressed unless the user chose offline — and on an
   adopted session that path now *starts* with the promotion, so it is capable
   of succeeding rather than merely of being attempted.
2. The leaving transition is a precondition of the first request rather than a
   reward for one, so it cannot be starved by a session that is structurally
   unable to make a successful request (§2's `countryCode` deadlock).
3. The transition actually finishes, because `_promote_session_online()`
   fetches `session_id`, `country_code` and `session.user` in one `GET
   /v1/sessions` — the one v1 call a token-only session can make.

The tempting weakenings, named so they are recognisable: "let's not attempt the
stream when we know we're offline", "clearing the flag is enough", and "we can
promote after something succeeds".

**The residual: an all-local session that is never asked anything stays
offline.** §2 states it plainly rather than mitigating it with a poll. Nothing
on screen is false while it lasts, and one deliberate action ends it. If real
use shows this is annoying, the honest escalation is a visible affordance — a
`[Enter] check connection` on the offline status line — not a timer.

**Narrowing `RATE_LIMIT_SIGNS` touches the most-enforced rule in the project.**
Anchoring `"429"` so a URL cannot match it is a *correctness* fix — today a
track id spells a rate limit into an offline error — but the direction of the
change is toward matching less, and the rule it guards ("never retry, ever")
is the one whose violation cost the owner an edge block (`ai/INCIDENTS.md` #1).
So the anchored matcher keeps a test for every sign it used to catch, on the
real strings, and the offline fixture is asserted to fail the rate-limit
classifier rather than merely to pass the offline one. If those two ever have
to be traded off, the rate limiter wins: a false rate limit stops making
requests, and a false offline keeps making them.

**Skipping rather than stopping.** Offline auto-advance and `→` skip
un-downloaded rows, which means a queue can play a different set of tracks than
the one on screen. That is a real cost and it is paid deliberately: the
alternative is silence three tracks in. The one-toast-per-run cap is the part
most likely to be wrong in practice — a queue that is mostly un-downloaded says
"skipped 41" and moves on, where the honest answer might be to stop and offer
`[D]`.

**The offline session's own queue is discarded.** `_restore_abandoned`
protects the legacy file on disk by refusing every full save for the session,
which means the queue built offline does not survive quit. That is the right
trade for a fifty-track file against a four-track session, and it is the wrong
one for a user who spends a whole flight building a queue. It is the decision
in this document most likely to be reversed after use.

**Clearing the cache offline destroys the only copies you have.** A new
hazard, created by this feature making those copies matter. Handled by a
different confirmation string on `[x]`, and — the sharper edge — by a real
prompt on `cache_budget_gb`, which today evicts per keypress with no
confirmation at all (`player.py:8058-8062` → `enforce_budget`,
`utils/cache.py:925-1014`): a destructive action wearing a number's clothes.
§5 specifies the preview function, both entry points and the ordering against
the config write, because the previous draft named the hazard and left the
mechanism undefined at a hook point that runs *after* the value is persisted.
What remains unmitigated by design: the cache is still evictable, still sized
by a budget the user set, and still admits nothing under pressure that does not
beat the cheapest resident (`should_cache`, `utils/cache.py:793-822`). "These
songs must survive" is answered by `[D]`.

**A stale list mistaken for a current one.** §4(d) trades a hard 30-day drop
for a label, and now also changes the *online* first paint from nothing to a
stale list. If the label is ever dropped from the render — a narrow terminal, a
mini mode, a refactor — the feature has silently reintroduced exactly the thing
the constant was protecting against. The age line is not decoration and should
be tested as screen state, not as a returned string.

**Trusting an unvalidated token.** Bounded deliberately: it plays local files
and nothing claims "logged in". The residual risk is a user who was
deliberately signed out on another device and whose ticli goes on playing
downloaded files for a week. That is the same risk as any offline music player,
and it is Garrett's to accept.

**Reversing the downloads screen's decision.** The screen's zero-request claim
must remain true for *browsing*. If Enter on that screen ever grows a resolve
— a metadata refresh, a like check, an artwork fetch — the reversal will have
been the thin end of exactly what the original docstring was guarding.

**Scope.** Steps 1 and 2 deliver the promise in §1 for a download-folder
library: no more silent failures, no more four-requests-per-outage, artwork
that survives a restart, and every downloaded and cached track playable with
the network down. Steps 3 through 5 are what let the app *start* that way and
say true things while it does. If the work is going to stop somewhere, stopping
after step 2 leaves ticli materially better and nothing half-built; stopping
after step 3 leaves it starting offline into screens that lie about being
empty, so step 4 must not be skipped if step 3 ships. The previous draft named
step 4 as the one that must not be dropped while staging Enter-plays into step
5, which meant its recommended stopping point shipped a library nobody could
play; that ordering is fixed above.

---

## 12. Review findings that did not survive checking

Recorded because a document that lists only the corrections it accepted teaches
the next reviewer nothing about where this kind of review goes wrong.

**"The promotion should be attempted on any track change and any list-screen
entry, floored at 60s."** The *problem* it was raised against is real and is
corrected in §2 — the previous draft had no reachable exit at all. The
proposed trigger is not taken. A track change on an all-local queue is caused
by the audio backend finishing a file, not by the user, so on a plane that is
one request a minute for the whole flight against `api.tidal.com` — a
reachability probe with the promotion's name on it, failing all three of §3's
grounds for rejecting probes. §2 keeps the trigger set to actions the user
performed and §11 accepts the residual explicitly.

**"`_search_own_playlists` is re-run per keystroke, over every record
`iter_tracks()` yields."** False. Typing calls `_reset_search_results()` and
nothing else (`player.py:8482-8485`); `_apply_search_scope` — the only caller
of `_search_own_playlists` (`player.py:6112`) — is reached from
`_cycle_search_filter` (`:6072`) and `_do_search` (`:6084`) alone, and
`_apply_search_scope`'s own docstring says "typing never searches". The scan
runs on `Tab` and on `Enter`. The *rest* of that finding is correct and acted
on: the queue's per-row playability question genuinely has no cheap predicate,
which is §4(e). The likely source of the error is this document's own sentence
about the reservoir's failure memory being "cleared by `_reset_search_results`
on every keystroke" — true, and not the same thing as re-running the search;
§5 now says which.

**"Keep the partial file on a download interrupted by the network, and resume
it with a `Range` request."** Not taken. The finding is right that the discard
is silent (`player.py:1449-1455`, `:7311`, `:7671-7683`) and §3 now says so in
the toast. Resume is a feature of its own — it needs the CDN to honour `Range`
on a signed URL, a length check against the index, and a rule for a partial
fetched at a tier the setting has since left — and none of that is about
playing offline. §8 lists it as not built.

**"Add a table of every `self.session.` call site with its two columns."** The
problem is real — the previous draft's passive voice hid the state machine —
but the fix is worse than the alternative. Thirty-four rows of table in a
design document is thirty-four things to keep in sync with a 9000-line file,
and a table is not enforceable. §3 uses the single `request_session` seam
instead, which the design was already installing for `API_TIMEOUT`, giving a
touched-call-site count of zero. The one enumeration that *is* kept is the four
`_looks_rate_limited` sites, because there are four of them and one of them
cannot take the rule.

**"§8's below-tier amendment should be a new step."** Not taken as a step; it
is folded into step 2. It is a change to `_local_source`, and step 2 is the
`_local_source` step — a sixth step containing one branch and one badge builder
would be a step nobody would ship separately. The finding's substance (it was
scheduled nowhere and its badge did not exist) is fully accepted; only the
placement differs.
