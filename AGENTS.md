# Agent instructions

This repository contains one agent skill: `skills/parallel-branches/SKILL.md`. Read it for how to run several git branches side by side. The script it drives is `skills/parallel-branches/scripts/pb.py`.

When changing this repository:

- Run `python3 tests/validate_skill.py` and `python3 -m unittest discover tests/scripts` before finishing.
- Keep the scripts on Python 3.9 and the standard library only.
- Never execute machine-wide or destructive commands (`pkill`, `rm -rf`, `git clean`) from tests. Record them for grading instead.
