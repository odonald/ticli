# ticli — agent entry points

Two different jobs land an agent here. Pick yours; each pointer's target is
complete, so nothing else needs reading first.

**Using ticli** — searching TIDAL, resolving songs, managing playlists, or
checking the player on the user's behalf: run `ticli agent docs`. It is the
whole contract — every verb with its request cost and JSON shape, the
enforced rate rules, and the workflows. Do not import ticli's internals or
call TIDAL's API directly; the surface exists because that path got the
owner's IP blocked.

**Working on ticli's code**: read `ai/README.md` first — it is short and
routes you to the working rules (several are hard, learned expensively),
the history, and where to write your own findings back. Updating `ai/` is
part of any code change, in the same commit.
