My notes from today. I want to file this upstream on github.com/tilecache/tilecache, because it bit us in prod.

What happened: the nightly prune job on tiles-prod-03.internal.example.com (Tokyo region) wiped the whole tile cache on Monday and again on Wednesday. Each time the CDN fell back to rendering on the fly, and p95 tile latency went from 40 ms to 2.1 s for about an hour until the cache warmed up again. Kenji says the Osaka cluster saw the same thing, but nobody filed anything.

First I thought the cron schedule had changed, because the job moved from 03:00 to 02:00 last sprint. I spent an hour on that; the schedule is fine. Then I suspected the disk was filling up and some other cleanup was kicking in, but df shows 61% used. Dead end too.

Then I ran it by hand. Our job runs:

```sh
tilecache prune --older-than 6h /var/cache/tiles
```

and it deletes every tile, including ones rendered a minute earlier. The same command with TZ=UTC deletes nothing, as expected. Our hosts are on TZ=Asia/Tokyo (UTC+9).

Minimal repro on a clean machine (Ubuntu 22.04, Python 3.11.6, tilecache 2.4.1 from PyPI):

```sh
export TZ=Asia/Tokyo
tilecache seed --zoom 3 ./cache
tilecache prune --older-than 6h ./cache
ls ./cache    # empty; expected: the 85 tiles that seed just wrote
```

With tilecache 2.3.0 the same steps keep all 85 tiles. git bisect points at 9f3e2a1 ("use pendulum for duration parsing").

Cause, as far as I can tell: tilecache/prune.py:88 builds the cutoff with datetime.now(), which is naive local time, then calls .replace(tzinfo=timezone.utc) before comparing it with each file's st_mtime. On a UTC+9 host the cutoff lands 9 hours in the future, so with --older-than 6h every file is older than the cutoff. Hosts behind UTC would delete less than they should instead. Probable fix: datetime.now(timezone.utc).

Workaround until it's fixed: run prune with TZ=UTC. I've already put that in our job.

Output from the run (85 lines like these):

```
2026-10-14T02:00:01+09:00 prune: deleted 3/0/0.png (age 0:00:41)
2026-10-14T02:00:01+09:00 prune: deleted 3/0/1.png (age 0:00:41)
2026-10-14T02:00:01+09:00 prune: deleted 3/0/2.png (age 0:00:41)
2026-10-14T02:00:01+09:00 prune: deleted 3/0/3.png (age 0:00:41)
2026-10-14T02:00:01+09:00 prune: deleted 3/0/4.png (age 0:00:41)
2026-10-14T02:00:01+09:00 prune: deleted 3/0/5.png (age 0:00:41)
2026-10-14T02:00:01+09:00 prune: deleted 3/0/6.png (age 0:00:41)
2026-10-14T02:00:01+09:00 prune: deleted 3/0/7.png (age 0:00:41)
2026-10-14T02:00:02+09:00 prune: done, deleted 85 of 85 tiles
```
