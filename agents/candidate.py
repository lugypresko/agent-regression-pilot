"""EXAMPLE candidate: two deliberate defects (D1 refund->technical, D2 down->low)."""
import json, sys
RULES=[(("invoice","charge","billing"),"billing"),                 # D1: refund removed
       (("password","login","crash","error","bug","export"),"technical"),
       (("cancel","plan","upgrade","seat"),"account")]
def route(t):
    low=t.lower()
    for ws,q in RULES:
        if any(w in low for w in ws): return q
    return "technical"
p=json.load(sys.stdin); t=f"{p['input'].get('subject','')} {p['input'].get('body','')}".lower()
prio="low" if "down" in t else ("high" if "urgent" in t else "low")       # D2
print(json.dumps({"queue":route(t),"priority":prio}))
