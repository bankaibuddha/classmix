# Charter — synthetic scratch project

This is what the agent is told to do. It is the only instruction it gets,
and it is copied into the workspace as `.agent/CHARTER.md` where the loop
reads it.

This example is deliberately synthetic. Nothing real depends on the output,
so the first runs measure the sandbox and the model rather than risking work
you care about. Point `--charter` at a different file once you have watched a
run behave.

## Mission

Build a small, well-tested library for fair random team assignment, from
nothing, using only the Python standard library.

## What it must do

1. `assign(names, team_count)` splits a list of names into that many teams
   as evenly as possible. With 12 names and 3 teams: three teams of 4. With
   10 names and 3 teams: teams of 4, 3, 3.
2. Assignment is random, using `secrets` or `random.SystemRandom`, never a
   predictable default seed.
3. `assign_spread(members, team_count)` takes members that each carry a
   group label and spreads each group across teams as evenly as possible, so
   one group does not clump into one team.
4. Nothing is ever dropped or duplicated. Every input appears exactly once
   across the returned teams.

## Gates

Every one of these must be a test that actually runs and passes. The harness
re-runs your test suite in a fresh container after you stop, so a claim that
is not backed by a passing suite will be visible as exactly that.

- `python3 -m unittest discover -s tests` exits 0.
- A property test over at least 200 random inputs asserting no input is ever
  dropped or duplicated.
- A property test asserting team sizes never differ by more than 1.
- A test proving the group spread holds: with 12 members across 3 groups
  into 3 teams, no team holds more than `ceil(4/3) = 2` from any one group.
- A test proving two consecutive calls on the same input can differ, which
  is what shows the randomness is real rather than a fixed order.

## How to work

- Small steps. Write the test, run it, watch it fail, then make it pass.
- Run the suite after every change. The recorded exit code is the only
  evidence that counts; your own summary of it is not.
- Standard library only. There is no network, so there is nothing to install.
- When every gate above has a passing test, declare done and say which
  action number ran the suite that proves it.
