"""EXAMPLE baseline: ticket router (illustrative data only)."""
import json, sys
RULES=[(("invoice","charge","refund","billing"),"billing"),
       (("password","login","crash","error","bug","export"),"technical"),
       (("cancel","plan","upgrade","seat"),"account")]
def route(t):
    low=t.lower()
    for ws,q in RULES:
        if any(w in low for w in ws): return q
    return "technical"
p=json.load(sys.stdin); t=f"{p['input'].get('subject','')} {p['input'].get('body','')}".lower()
print(json.dumps({"queue":route(t),"priority":"high" if ("urgent" in t or "down" in t) else "low"}))
