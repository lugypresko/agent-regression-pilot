import json, sys
json.load(sys.stdin)
print(json.dumps({"queue": "technical", "priority": "high"}))
