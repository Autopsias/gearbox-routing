import json, sys
r = json.load(open(sys.argv[1]))
print(json.dumps({"pr": r.get("pr"), "input_tokens": r["usage"]["input_tokens"],
                  "output_tokens": r["usage"]["output_tokens"], "seconds": r["duration_ms"] / 1000,
                  "findings": r.get("findings", 0)}))
