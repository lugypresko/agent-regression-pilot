import json, sys
json.load(sys.stdin)
print(json.dumps({"queue": "billing", "priority": "low"}))
sys.exit(1)
