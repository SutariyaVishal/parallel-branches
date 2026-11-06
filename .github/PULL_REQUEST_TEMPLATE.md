## What and why

<!-- One or two sentences. Link the issue if there is one. -->

## Checklist

- [ ] `python3 tests/validate_skill.py` and `python3 -m unittest discover tests/scripts` pass
- [ ] `ruff check .` passes
- [ ] New behaviour has a test, and a test that exercises it for real where it starts processes
- [ ] `SKILL.md` and `references/` updated if behaviour an agent relies on changed
- [ ] `CHANGELOG.md` has a line
- [ ] No new runtime dependencies (the scripts are standard library only, Python 3.9+)
- [ ] Anything destructive (killing, deleting, force) is recorded in tests, never executed machine-wide
