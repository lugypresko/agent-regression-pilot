import json, sys
p = json.load(sys.stdin)
print(json.dumps({"queue": "billing", "priority": "low"}))
