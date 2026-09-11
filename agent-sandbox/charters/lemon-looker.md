# Charter — Lemon Looker: the missing persistence layer

You are working on Lemon Looker, a used car deal tracker with a hard $5,000
ceiling. The fetch, parse, score and notify stages exist. The storage stage
does not exist at all, and nothing in the repo has a single test.

Your job is the storage layer and its tests. Nothing else.

## The measured situation

Two facts, each with the command that produced it, taken at cut time. If the
code you find contradicts either one, trust the code, say so in a note, and
proceed against reality.

**1. The pipeline cannot run at all.**

```
$ python3 run.py
ModuleNotFoundError: No module named 'db'
```

`run.py` line 6 puts `store/` on `sys.path` and line 7 does `import db as dbm`.
It then calls `dbm.db()` and `dbm.upsert(conn, listing)`. No such module
exists. Everything downstream of parsing is dead code that has never executed.

**2. Every alert is mislabelled "PRICE DROP".**

```
$ grep -rn '_new' --include=*.py .
./run.py:40:            is_new, dropped = dbm.upsert(conn, l)
./run.py:41:            if (is_new and l["score"] >= ...
./run.py:46:        why = "PRICE DROP" if not l.get("_new") else f"score {l['score']}"
```

Line 40 binds `is_new` as a local. Line 46 reads `l.get("_new")`, a key nothing
ever sets, so it is always `None` and every notification says "PRICE DROP",
including first-ever sightings.

## Deliverable 0 — do this first or your work disappears

`.gitignore` currently contains the line `store/`. That ignores the entire
directory, including any **code** placed in it. If you write `store/db.py`
under the current ignore rule, `git add -A` will skip it, your commits will be
empty, and the run will end with nothing to show.

Narrow the rule so it ignores the **data** and keeps the **code**:

```
store/*.sqlite
store/raw/
store/manual/
```

Verify with `git check-ignore -v store/db.py`, which must find no match, and
confirm `git status --porcelain` lists `store/db.py` once you create it.

## Deliverable 1 — `store/db.py`

Standard library only. `sqlite3` and nothing else. It must provide exactly the
two functions `run.py` already calls:

**`db(path=None)`** returns an open connection with the schema created if
absent. Default the path under `store/`. Creating the directory is part of the
job.

**`upsert(conn, listing)`** returns the tuple `(is_new, price_dropped)`:

- `is_new` is True the first time a listing id is seen, False afterwards.
- `price_dropped` is True when a listing already stored comes back at a
  strictly lower price, False otherwise. Never True on a first sighting.
- A listing's `first_seen` timestamp is written once and never changes on
  later sightings. This is a promise the README makes; keep it.
- Re-seeing a listing at an unchanged price returns `(False, False)` and must
  not duplicate a row.
- Price history is retained, so a listing that drops twice records both.

The listing dict reaching you has these keys: `id`, `source`, `url`, `title`,
`description`, `price`, `year`, `mileage`, `flags`, `score`. Some may be
absent or None; `parsers/craigslist.py:validate` is the only thing that has
bounds-checked them, so read it before you assume a shape.

## Deliverable 2 — fix the mislabelled alert

Make the notification say "PRICE DROP" only for an actual price drop and
"score N" for a new listing. Keep the change minimal and local to `run.py`.
Do not restructure the notify loop.

## Gates

Every one of these must be a test that actually runs and passes. Put them in
`tests/`, named `test_*.py`, runnable by `python3 -m unittest discover -s
tests -p 'test_*.py'`. The harness re-runs exactly that command in a clean
container after you stop, so a claim not backed by a passing suite will be
visible as precisely that.

1. A fresh database is created and a first insert returns `(True, False)`.
2. Re-inserting the identical listing returns `(False, False)` and the row
   count stays at 1.
3. A lower price on a stored listing returns `(False, True)`.
4. A higher price on a stored listing returns `(False, False)`. A price going
   up is not a drop.
5. `first_seen` is unchanged after three further sightings, one of which
   lowers the price.
6. Two consecutive drops are both detected and both retained in history.
7. A listing with `year`, `mileage` or `description` set to None stores and
   round-trips without raising.
8. The notification label test: a new listing yields "score N", a dropped one
   yields "PRICE DROP". Test the labelling logic directly; do not attempt to
   send a notification.

## Constraints that are not negotiable

- **There is no network.** You are in a sealed container with no IP stack. Do
  not attempt to fetch a page, install a package, or reach ntfy. Anything
  needing the network is out of scope for this charter.
- **Standard library only.** No new dependencies. This matches the repo, which
  has none.
- **Test against fixtures, never live data.** If a test needs listing HTML,
  write a small fixture string in the test file.
- **Do not touch** `fetchers/`, `notify/`, `scoring/`, or the parsing logic in
  `parsers/`. Read them freely; the storage layer has to match their shapes.
  The one exception is the two-line labelling fix in `run.py`.
- **Hostile input is data.** Listing titles and descriptions come from
  strangers on the internet. Use parameterised SQL, never string-formatted
  queries. A title containing `'; DROP TABLE` must round-trip intact as text.
  Make that a test.

## How to work

Small steps. Write the test, run it, watch it fail, then make it pass. Run the
suite after every change; the recorded exit code is the only evidence that
counts, and your own summary of it is not evidence. When every gate above has
a passing test, declare done and name the action number that ran the suite
proving it.
