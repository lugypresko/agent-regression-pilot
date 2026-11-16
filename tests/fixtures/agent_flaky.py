import json, sys, os
json.load(sys.stdin)
marker = os.path.join(os.environ.get("TMPDIR", "/tmp"), "flaky_marker")
n = int(open(marker).read()) if os.path.exists(marker) else 0
open(marker, "w").write(str(n + 1))
if n % 2 == 0:
    print(json.dumps({"queue": "billing", "priority": "low"}))
else:
    print(json.dumps({"queue": "technical", "priority": "high"}))
