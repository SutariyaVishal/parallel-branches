"""A hook command for tests: records how it was run. Usage: hookprobe.py OUTFILE LABEL [ARG...]

Appends one JSON line to OUTFILE with the label, the arguments, the working directory and the PB_*
environment. An argument that starts with http:// is also fetched, and the status (or error) is
recorded, which shows whether the server was up when the hook ran.
"""

import json
import os
import sys
import urllib.request

out, label, args = sys.argv[1], sys.argv[2], sys.argv[3:]
fetched = {}
for arg in args:
    if arg.startswith("http://"):
        try:
            with urllib.request.urlopen(arg, timeout=2) as resp:
                fetched[arg] = resp.status
        except OSError as exc:
            fetched[arg] = "error: %s" % exc
record = {"label": label, "args": args, "cwd": os.path.realpath(os.getcwd()), "fetched": fetched,
          "env": {k: v for k, v in os.environ.items() if k.startswith("PB_")}}
with open(out, "a") as fh:
    fh.write(json.dumps(record) + "\n")
