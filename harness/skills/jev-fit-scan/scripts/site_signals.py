"""The signal patterns and the ledger group titles of scan_sites.py: one regex list per signal.

Signals:
  llm_call           model SDK imports, call shapes, HTTP endpoints
  structured_output  the call asks the model for a schema (closed-set output likely)
  prompt_verdict     prompt wording that asks for yes/no, a pick, or a grade
                     (only in files that also touch a model, or prompt files)
  rewrite_filter     prompt wording that asks a model to rewrite, drop or keep parts of a text
                     that exists already (same gate as prompt_verdict)
  null_answer        prompt wording that tells the model to answer "nothing" for most items (same gate)
  cost_cut           a sample, a cap or a "too expensive" note beside a model call (same gate)
  checkpoint         an agent loop that asks a model whether to go on or stop (same gate)
  rerank             rerankers and cross-encoders
  heuristic_verdict  thresholds, fuzzy matching, review queues, "undecided" bands, catch-all buckets,
                     code that calls itself a proxy for a judgment
  guard              moderation, guardrails, prompt-injection filters
  model_router       code or config that picks which model, or no model, serves a request
                     (kept only when the file names two or more model tiers)
  tool_guard         code that refuses an agent's tool call: a hook that answers deny or ask, or exits 2
                     (kept only when the file reads a tool call)
  agent_cli          a script or a CI step that runs a coding agent or an AI review action
  usage_cost         token and cost logging (where spend can be MEASURED)
  jev_present        the repo already calls TypeSafe
"""
import re


class Sig:
    """One regex. ci=True means case-insensitive, done by lowering both sides:
    re.IGNORECASE on these long alternations dominated the run time."""

    def __init__(self, pattern, ci=False):
        self.ci = ci
        self.rx = re.compile(pattern.lower() if ci else pattern)

    def search(self, text, low):
        return self.rx.search(low if self.ci else text)


# Code that says it only approximates a judgment ("deterministic proxies", "a crude proxy by design"): the
# judgment itself is the unruled band. Word-list names (BANNED, IDIOMS) stay out: 5 of 7 hits in three repos
# were allowlists or redaction lists, where an exact match is the rule.
PROXY_WORDS = r"(cheap|crude|rough|deterministic|heuristic|keyword) prox(y|ies)"
# A model router names two or more model tiers in one file. Claude ids reduce to their family word, so
# "claude-haiku-4-5" and "haiku" count once. scan_sites.py drops a router hit in a file with fewer than two.
MODEL_FAMILY = re.compile(r"\b(haiku|sonnet|opus|fable|jev)\b|\bgpt-\d[\w.-]*|\bo[1-9](?:-mini|-pro)?\b|"
                          r"\bgemini-[\w.-]+|\b(?:llama|qwen|deepseek)-[\w.-]+", re.IGNORECASE)
CALL_SHAPE = (r"chat\.completions\.(create|parse)|responses\.(create|parse)|messages\.create|generate_content|"
              r"generateContent|generateText|generateObject|streamText|streamObject|\bacompletion\(|"
              r"\blitellm\.completion\(|InvokeModel|bedrock-runtime|\.converse\(")
PATTERNS = {
    "llm_call": [
        Sig(r"(?m)^\s*(from|import)\s+(openai|anthropic|google\.(generativeai|genai)|vertexai|litellm|langchain\w*|"
            r"llama_index|cohere|groq|together|ollama|replicate|pydantic_ai|instructor|dspy|strands|"
            r"autogen|crewai|semantic_kernel|huggingface_hub|transformers|voyageai)\b"),
        Sig(r"""(from|require\()\s*['"](openai|@anthropic-ai/[\w-]+|@google/(generative-ai|genai)|ai|@ai-sdk/[\w-]+|"""
            r"""langchain|@langchain/[\w-]+|cohere-ai|groq-sdk|ollama|llamaindex)['"]"""),
        Sig(CALL_SHAPE),
        Sig(r"/v1/chat/completions|/v1/messages|/v1/responses|:generatecontent|/api/(generate|chat)\b|"
            r"openrouter\.ai|api\.(openai|anthropic|groq|together|deepseek|cohere)\.(com|ai)", ci=True),
    ],
    "structured_output": [
        Sig(r"response_format|response_model|response_schema|response_mime_type|json_schema|"
            r"with_structured_output|structured_output|tool_choice|output_type\s*=|result_type\s*=|"
            r"generateObject|zodResponseFormat|output_parser|PydanticOutputParser|JsonOutputParser"),
    ],
    "prompt_verdict": [
        Sig(r"yes or no|yes/no|true or false|answer (only )?with|respond (only )?with|reply (only )?with|"
            r"one of the following|exactly one of|choose (one|the best|from)|select (one|the best|from)|"
            r"\bclassify\b|\bcategori[sz]e\b|on a scale|rate (it|this|the|each)|score (from|between|each)|"
            r"from [01] to \d+|is (this|the) \w+ (a|an|the)\b|does (this|the) [\w ]{1,40} "
            r"(contain|mention|support|answer|match)|same (entity|person|company|product)|\bduplicates?\b|"
            r"contradict|relevan(t|ce) to|\bsufficient\b|grounded|faithful|hallucinat|supported by|"
            r"\bverdict\b|\bjudge\b|pass or fail", ci=True),
    ],
    "rewrite_filter": [
        Sig(r"rewrite (the|this|each|your)|remove (any|all|every|unsupported)|delete (any|all|every)|"
            r"drop (any|all|every|unsupported)|(→|->) ?(keep|drop)\b|keep or drop|do not add (any )?(new )?information|"
            r"return only the (rewritten|corrected|cleaned|verified)|strip (out )?(any|all)|"
            r"unsupported (claims?|sentences?)", ci=True),
    ],
    # No "return" verb in null_answer: it matched the Python statement `return None` in 43 files of one repo.
    "null_answer": [
        Sig(r"otherwise,? (reply|respond|say|output|answer)\b|(reply|respond|answer|output) (with )?(only )?['\"`]?(none|null|no_?op|skip|nothing)\b|"
            r"return (an )?empty (list|array|string)|if (nothing|none of|there (is|are) no)\b.{0,60}(reply|respond|say|output|answer|return)|"
            r"no (action|alert) (is )?(needed|required|warranted)", ci=True),
    ],
    "cost_cut": [
        Sig(r"random\.sample|\.sample\(|sample_(rate|size|n)\s*[:=]|max_(items|docs|posts|rows|records|tickets|files)\s*[:=]|"
            r"too (expensive|costly|slow)|to (save|cut|limit) (cost|tokens|money|spend)|costs? too much", ci=True),
    ],
    "checkpoint": [
        Sig(r"max_(steps|iterations|iters|turns|rounds)\s*[:=]|should_(continue|stop)|\bis_(done|complete|finished)\b|stop_condition|"
            r"task (is )?(complete|done|finished)", ci=True),
    ],
    "rerank": [Sig(r"re-?rank|cross.?encoder|bge-reranker|colbert|ms-marco", ci=True)],
    "heuristic_verdict": [
        Sig(r"rapidfuzz|fuzzywuzzy|thefuzz|jellyfish|jaro|levenshtein|difflib|sequencematcher|"
            r"string-similarity|fuse\.js|fuzzyset|cosine_similarity|cosine_distance|"
            r"threshold\w*\s*[:=]|_(min|max)_score\s*[:=]|min_score\s*[:=]|\bcut_?off\s*[:=]|"
            r"needs?_review|manual_review|human_review|review_queue|review_candidates?|\"review\"|'review'|"
            r"\bundecided\b|\buncertain\b|\bambiguous\b|low_confidence|fallback_to_(llm|human|manual)|"
            r"keyword_(match|classif)|regex_classif|\bheuristic|uncategori[sz]ed|"
            r"return ['\"](other|misc|miscellaneous)['\"]|" + PROXY_WORDS, ci=True),
    ],
    "guard": [
        Sig(r"moderations?\.create|\bmoderation\b|guardrails?|prompt.?injection|jailbreak|llama.?guard|"
            r"nemo.?guardrails|perspective(api)?|toxicity|content.?filter|\bpii\b", ci=True),
    ],
    "usage_cost": [
        Sig(r"input_tokens|prompt_tokens|completion_tokens|output_tokens|usage_metadata|total_tokens|"
            r"token_usage|estimated_cost|cost_usd|usd_per|price_per|per_million|per_mtok|tiktoken|"
            r"langfuse|helicone|langsmith|openllmetry|cost_tracker", ci=True),
    ],
    # Code or config that picks which model, or no model, serves a request: a Choice over tiers. A probe
    # `MODEL_BY_TIER` map with `pick_model()` gave 0 ledger rows before this signal existed.
    "model_router": [
        Sig(r"(?m)\b(pick|select|choose|route|resolve)_?(the_?)?model\b|\bmodel_?(router|routing|selector)\b|"
            r"\bmodels?_(by|for|per)_(tier|class|task|complexity|difficulty|level|intent)|"
            r"\b(tier|complexity|difficulty|task_?class|intent)\w*\s*[:=].{0,80}\b(haiku|sonnet|opus|gpt-|gemini-|mini|flash)|"
            r"\b(strong|weak|cheap|small|fast|big)_model\b|routellm|notdiamond|openrouter/auto|"
            r"^\s*(model_?)?(tiers?|routes|routing|task_?class(es)?)\s*:\s*$", ci=True),  # a YAML tier map
    ],
    # Code that refuses an agent's tool call from a pattern list. No model is in it, so no other signal sees it:
    # a shell guard in a hook gave 0 ledger rows before this signal.
    "tool_guard": [
        Sig(r"""permissionDecision["']?\s*[:=]\s*["'](deny|ask)|["']permission["']\s*:\s*["'](deny|ask)|"""
            r"""\bexit\(2\)|\bexit 2\b|\breturn 2\b|\bblock["']?\s*:\s*true\b"""),
    ],
    "agent_cli": [Sig(r"claude\s+(-p|--print)\b|claude-code-(base-)?action|codex\s+exec\b|gemini\s+(-p|--prompt)\b|"
                      r"coderabbitai|\baider\s+--|copilot[- ]code[- ]review|cursor-agent|opencode\s+run|\bllm\s+-m\b", ci=True)],
    "jev_present": [Sig(r"typesafe\.ai|typesafe[_-]sdk|@typesafe-ai|typesafeclient|/v1/systemone|"
                        r"\bjev-(latest|preview|\d)", ci=True)],
}
# `exit 2` alone is any script. scan_sites.py keeps a tool_guard hit only in a file that reads a tool call.
# ponytail: word match on the payload names of Claude Code, Codex, Cursor and PI hooks; add a name when a harness needs one
# The event name (PreToolUse) is not in the list: two eval runners that only name the event came through on it.
TOOL_PAYLOAD = re.compile(r"tool_input|toolInput|permissionDecision|\btool_call\b|before_tool")
MODEL_CATS = ("llm_call", "structured_output", "wrapper_call", "agent_cli")
# These three match code idioms, not only prompt prose. A schema word alone does not open their gate:
# FastAPI's `response_model=` let 3 files with no model into the ledger of one repo.
CODE_WORDING_CATS = ("null_answer", "cost_cut", "checkpoint")
CALL_CATS = ("llm_call", "wrapper_call", "agent_cli")
WORDING_CATS = ("prompt_verdict", "rewrite_filter", "null_answer", "cost_cut", "checkpoint")  # ordinary prose far from a model
# What each ledger group means, in the order the report prints them.
GROUP_TITLES = {"cost_cuts": "a sample, a cap or a cost note beside a model call: work that cost cut short",
                "screens": "a model reads every item and is told to answer 'nothing' for most of them",
                "checkpoints": "an agent loop asks a model whether to go on or stop",
                "rewrites": "a model rewrites, drops or keeps parts of a text that exists already",
                "routers": "code or config picks which model, or no model, serves a request: one Choice over tiers",
                "model_sites": "a model is called AND the file holds verdict wording or an output schema",
                "prompt_files": "verdict wording in a prompt file; the model call is in another file",
                "bands": "a heuristic score with a review / undecided / manual band, a catch-all bucket, or a self-declared proxy for a judgment",
                "rerank_top": "rerankers (top 5 files)", "guard_top": "moderation and guards (top 5 files)",
                "tool_guards": "code refuses an agent's tool call (a command, a file write) from a pattern list, with no model",
                "dev_loop": "the development loop: a model or an agent rules in CI, a hook, a skill or a review step",
                "host_choices": "the host agent (Claude Code, Codex) picks one skill, agent or command file from this folder per prompt"}
