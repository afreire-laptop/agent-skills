Title: prune deletes recent tiles when the local timezone is ahead of UTC

On a host with `TZ=Asia/Tokyo` (UTC+9), `tilecache prune --older-than 6h` deletes every tile, including tiles written seconds earlier. It should keep tiles newer than 6 hours. It wiped our production cache twice this week.

## Reproduce

tilecache 2.4.1 from PyPI, Python 3.11.6, Ubuntu 22.04:

```sh
export TZ=Asia/Tokyo
tilecache seed --zoom 3 ./cache
tilecache prune --older-than 6h ./cache
ls ./cache    # empty; expected: the 85 tiles that seed wrote
```

tilecache 2.3.0 keeps all 85 tiles. `git bisect` points at 9f3e2a1 ("use pendulum for duration parsing").

## Cause

`tilecache/prune.py:88` builds the cutoff from `datetime.now()`, which is naive local time, then labels it UTC with `.replace(tzinfo=timezone.utc)` before comparing it with each file's `st_mtime`. On a UTC+9 host the cutoff lands 9 hours in the future; hosts behind UTC delete less than they should. A likely fix is `datetime.now(timezone.utc)`.

Workaround: run `prune` with `TZ=UTC`.
