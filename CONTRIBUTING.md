# Contributing

Thanks for helping. This project is small on purpose: one script, no runtime dependencies, Python 3.9+.

## Set up and test

```
python3 tests/validate_skill.py                 # skill + manifest rules
python3 -m unittest discover tests/scripts      # unit and CLI tests (about 30 s)
ruff check .                                    # lint (pip install ruff)
```

The tests start real dev servers (`python3 -m http.server`) and, if Chrome or Chromium is installed, a headless browser. They clean up after themselves. Never put machine-wide commands such as `pkill` in a test that executes them.

## Adding a stack

1. Add a `detect_<name>` function to `skills/parallel-branches/scripts/stacks.py` and list it in `detect_all`. Return `name`, `family`, `cmd`, `env`, `confidence` and `note`. Use `high` confidence only when the port is set by a flag or variable you have verified the tool honors. Otherwise use `low`.
2. If the stack needs an install step, extend `plan_install`.
3. Add detection and install tests to `tests/scripts/test_stacks.py`.
4. Add a row to the table in `skills/parallel-branches/references/stacks.md`. Say whether you ran it or took the port mechanism from documentation.

## Evals

`tests/evals/scenarios.json` describes what a good agent does, and `tests/evals/graders.py` checks it with deterministic rules. `tests/scripts/test_evals.py` runs a scripted agent through every scenario, so scenarios and graders stay consistent without calling a model.

To run the evals against real models (this spends money and lets the model run shell commands in a throwaway repo):

```
python3 tests/evals/run_evals.py --dry-run                      # show what would run
python3 tests/evals/run_evals.py --yes --models haiku,sonnet    # run
python3 tests/evals/run_evals.py --yes --baseline               # same prompts, no skill
```

A new behavior in `SKILL.md` should come with a scenario that fails without it.

## Pull requests

- Keep changes focused, and update `references/` and `SKILL.md` if behavior changes.
- Add a line to `CHANGELOG.md`.
- Do not add runtime dependencies. If you need one, open an issue first.
