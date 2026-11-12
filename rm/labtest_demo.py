import json, traceback
from . import demo
out = {}
try: out = demo.check()
except Exception: out["error"] = traceback.format_exc()[-800:]
print(json.dumps(out, indent=1)); json.dump(out, open("labtest.json", "w"))
