"""The one place that calls a model. Every call is logged to logs/llm-usage.jsonl."""
import json
import time

from openai import OpenAI

client = OpenAI()


def ask_model(site, prompt, model="gpt-5.6"):
    start = time.time()
    r = client.chat.completions.create(model=model, messages=[{"role": "user", "content": prompt}])
    with open("logs/llm-usage.jsonl", "a") as f:
        f.write(json.dumps({"site": site, "model": model, "input_tokens": r.usage.prompt_tokens,
                            "output_tokens": r.usage.completion_tokens, "seconds": round(time.time() - start, 1)}) + "\n")
    return r.choices[0].message.content
