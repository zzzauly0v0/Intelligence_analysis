#!/usr/bin/env python3
"""
One summarization client for every model, chosen at run time.

    from summarizer import Summarizer, MODELS
    s = Summarizer()                    # DEFAULT_MODEL, or pass a MODELS key
    model, text = s.summarize_competitor(body)

Adding a model means adding ONE entry to the MODELS table below — no new class.
There are only two wire protocols in play:

  * "bedrock"  — AWS Bedrock converse() (Claude on Bedrock)
  * "openai"   — the OpenAI chat-completions protocol, which every self-hosted
                 server we use also speaks (Unsloth Studio, vLLM, Ollama, LM
                 Studio…) as well as the OpenAI/DeepSeek/Moonshot APIs

so the class carries one _call_<protocol> method per protocol and the table says
which one a model uses. The PROMPTS live here too (see prompts.py) and are
identical across models on purpose: switching models must change only the model,
never the instructions, or two runs aren't comparable.
"""

import os
import re
import time

from dotenv import load_dotenv

from prompts import COMPETITOR_PROMPT, REGULATORY_PROMPT

load_dotenv()


# --------------------------------------------------------------------------- #
# The model table — the ONLY place to touch when adding/switching a model.
# --------------------------------------------------------------------------- #
# Keys are the names you pass to --model / SUMMARIZER_MODEL.
#   protocol     "bedrock" | "openai"
#   model_id     the id sent on the wire. "" means "ask the server what it has
#                loaded" (openai protocol only) — handy for a local server whose
#                loaded model changes.
#   env_*        which .env variable holds the endpoint/key (openai protocol)
#   max_prompt_tokens
#                context guard. Input over this is truncated BEFORE sending,
#                because a too-long prompt is REJECTED (HTTP 400), which would
#                cost the whole summary. None = no guard needed (Bedrock's window
#                is far larger than any body we scrape).
#   note         shown by --list-models
MODELS = {
    "sonnet": {
        "protocol": "bedrock",
        # Cross-region INFERENCE PROFILE id. The "us." prefix is required — the
        # bare model id is not invocable with on-demand throughput. Use "eu."/
        # "apac." if your Bedrock region differs.
        "model_id": "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
        "max_prompt_tokens": None,
        "note": "Claude Sonnet 4.5 on AWS Bedrock（质量最好，按量付费，约 5-8s/条）",
    },
    "haiku": {
        "protocol": "bedrock",
        "model_id": "us.anthropic.claude-haiku-4-5-20251001-v1:0",
        "max_prompt_tokens": None,
        "note": "Claude Haiku 4.5 on Bedrock（更快更便宜，质量略降）",
    },
    "unsloth": {
        "protocol": "openai",
        # Blank -> _loaded_model() asks /v1/models which one is loaded, so a swap
        # on the server needs no code or .env change.
        "model_id": "",
        "env_base_url": "UNSLOTH_BASE_URL",
        "env_api_key": "UNSLOTH_API_KEY",
        "env_model_id": "UNSLOTH_MODEL_ID",
        "default_base_url": "http://192.168.105.133:8888/v1",
        # The server's real window is 16384 tokens — the `context_length` field of
        # /v1/models, NOT the misleadingly-named `max_context_length: 4096`
        # (probed 2026-08-05: a 10,961-token prompt succeeds, 16,414 is rejected
        # with context_length_exceeded). 15000 leaves room for the chat template.
        "max_prompt_tokens": 15000,
        "note": "自建 Unsloth Studio（gemma-4-E4B，本地免费，约 15-22s/条）",
    },
}

# Back to Sonnet on Bedrock (2026-08-14, by request). The self-hosted model was
# the default for a week because it costs nothing per article, but its output
# needed watching: the same body would sometimes come back as 正文内容不完整 on
# one run and summarize fine on the next (see BAIL_NUDGE below), and that jitter
# is expensive to babysit on a 30-site daily digest. Sonnet costs per article and
# needs AWS_BEARER_TOKEN_BEDROCK to be valid.
# `--model unsloth` still switches back for a single run.
#
# This lives in code, not .env, on purpose: .env isn't committed, so a default
# that only exists there silently changes when the code moves to another machine.
DEFAULT_MODEL = "sonnet"

# Estimating prompt length in CHARS would be wrong by 3x across languages
# (probed on the Unsloth tokenizer: 1.81 chars/token for Chinese, 5.99 for
# English). We use the Chinese density so the estimate never UNDER-counts and
# the guard errs toward truncating early rather than getting a 400.
CHARS_PER_TOKEN = 1.8

FAILURE_TEXT = "摘要生成失败, 请点击链接查看文章。"

# The prompts let the model bail out with this phrase when a body is genuinely
# unusable (nav-only, error page). Small models over-use it: a press release
# whose first half is a line-per-metric list (Kerry's half-year results — real
# revenue/EBITDA/EPS figures, one per line) reads as "fragments" to them, so they
# bail on a perfectly summarizable article. Verified 2026-08-05: the same body
# summarized fine on three re-runs, i.e. it's sampling jitter, not a bad body.
BAIL_MARKER = "正文内容不完整"
# Above this many chars, a bail-out is not credible: a body this long has a
# topic and facts in it whatever the layout. Kerry's was 2550 chars; a genuinely
# broken body (nav crumbs only) is a few hundred at most.
BAIL_RETRY_MIN_CHARS = 800
BAIL_NUDGE = (
    "\n\n注意：以上正文是完整的，只是排版为逐行短句/数字列表。"
    "请不要回答“正文内容不完整”，务必按三段格式正常总结。\n"
)


def resolve_model_name(name=None):
    """Pick the model: explicit arg > SUMMARIZER_MODEL in .env > DEFAULT_MODEL.

    Raises ValueError on an unknown name, listing the valid ones — a typo must
    fail loudly rather than silently fall back to a different model than the one
    the operator asked for.
    """
    chosen = (name or os.getenv("SUMMARIZER_MODEL", "") or DEFAULT_MODEL).strip().lower()
    if chosen not in MODELS:
        raise ValueError(f"未知模型 {chosen!r}；可用: {', '.join(MODELS)}")
    return chosen


class Summarizer:
    """Turns an article body into a Chinese summary with the chosen model.

    Same (model_id, text) return contract whatever the model is, so callers
    never branch on the backend. On failure it returns FAILURE_TEXT rather than
    raising: one unsummarizable article must not abort a 20-site digest.
    """

    MAX_ATTEMPTS = 3
    TIMEOUT = 300          # seconds; a cold local model can be slow to first token
    MAX_OUTPUT_TOKENS = 800   # Bedrock only; the local server's limit is set on
                              # the deployment side, so we don't send one there.

    def __init__(self, name=None, verbose=True):
        self.name = resolve_model_name(name)
        self.spec = MODELS[self.name]
        self.protocol = self.spec["protocol"]
        # A model id set in .env wins over the table, so pointing at a different
        # checkpoint (or a differently-named one on the local server) needs no
        # code edit. Blank/absent -> the table's id.
        env_var = self.spec.get("env_model_id")
        self.model_id = ((os.getenv(env_var, "").strip() if env_var else "")
                         or self.spec["model_id"])
        self.max_prompt_tokens = self.spec.get("max_prompt_tokens")
        self._connect()
        if verbose:
            print(f"🤖 摘要模型: {self.name} · {self.model_id} "
                  f"({self.protocol})")

    # ---------------------------------------------------------------- connect
    def _connect(self):
        if self.protocol == "bedrock":
            import boto3
            from botocore.config import Config
            # boto3/botocore (>= ~1.39) picks up AWS_BEARER_TOKEN_BEDROCK for the
            # bedrock-runtime service automatically; load_dotenv put it in the
            # environment. Without it, standard AWS credentials are used
            # (~/.aws/credentials, IAM role, AWS_ACCESS_KEY_ID/SECRET).
            if not os.getenv("AWS_BEARER_TOKEN_BEDROCK"):
                print("  ⚠️  AWS_BEARER_TOKEN_BEDROCK 未设置，改用标准 AWS 凭证")
            # Explicit read/connect timeouts + retries. Without these, a single
            # converse() that the server never answers hangs the WHOLE run
            # indefinitely (botocore's default read timeout is long and one dead
            # request stalls the sequential summary loop) — seen mid-run at
            # ~98/221. read_timeout bounds each call; MAX_ATTEMPTS in _run then
            # retries the timed-out one instead of blocking forever.
            boto_cfg = Config(
                region_name=os.getenv("AWS_REGION", "us-east-1"),
                connect_timeout=15,
                read_timeout=120,   # a healthy haiku summary returns in seconds;
                                    # this is only the ceiling for a hung request
                retries={"max_attempts": 2, "mode": "standard"},
            )
            self.client = boto3.client(
                "bedrock-runtime",
                region_name=os.getenv("AWS_REGION", "us-east-1"),
                config=boto_cfg)

        elif self.protocol == "openai":
            from openai import OpenAI
            key_var = self.spec["env_api_key"]
            api_key = os.getenv(key_var)
            if not api_key:
                raise RuntimeError(f"{key_var} 未在 .env 中设置")
            base_url = (os.getenv(self.spec["env_base_url"], "").strip()
                        or self.spec["default_base_url"])
            self.client = OpenAI(base_url=base_url, api_key=api_key)
            if not self.model_id:
                self.model_id = self._loaded_model()

        else:
            raise ValueError(f"未知协议 {self.protocol!r}")

    def _loaded_model(self):
        """Ask an OpenAI-protocol server which model it currently has loaded."""
        try:
            models = list(self.client.models.list().data)
        except Exception as e:
            raise RuntimeError(f"连不上模型服务: {e}") from e
        if not models:
            raise RuntimeError("模型服务未报告任何可用模型")
        for m in models:
            if (getattr(m, "model_extra", None) or {}).get("loaded"):
                return m.id
        return models[0].id

    # -------------------------------------------------------------- summarize
    def summarize_competitor(self, text):
        """Competitor/industry news (often English) -> structured Chinese brief."""
        if not text or not text.strip():
            return None, "内容过短，无法生成摘要"
        return self._run_checked(COMPETITOR_PROMPT, text)

    def summarize_regulatory(self, text):
        """Government/standards notices -> same 3-section structure, RA framing."""
        if not text or not text.strip():
            return None, "内容过短，无法生成摘要"
        return self._run_checked(REGULATORY_PROMPT, text)

    def _run_checked(self, prompt_prefix, body):
        """Summarize, and challenge an implausible "body is incomplete" bail-out.

        The bail-out exists for genuinely unusable bodies, but a small model also
        reaches for it when a real article is laid out as one-fact-per-line
        (financial results, spec tables). Re-asking once with an explicit "the
        body IS complete" nudge recovers those; a body that really is broken
        bails again and we keep that answer. Only re-asked for bodies long enough
        that "incomplete" isn't credible, so a truly empty page costs no extra
        call.
        """
        model, text = self._run(prompt_prefix + body)
        if BAIL_MARKER in (text or "") and len(body) >= BAIL_RETRY_MIN_CHARS:
            print(f"  ⚠️  模型称正文不完整，但正文有 {len(body)} 字 — 重问一次")
            model2, text2 = self._run(prompt_prefix + body + BAIL_NUDGE)
            if text2 and BAIL_MARKER not in text2:
                return model2, text2
            print("  ↳ 重问后仍判定不完整，保留该结论")
        return model, text

    def _run(self, prompt):
        """Send one prompt, with retries. Returns (model_id, summary_text)."""
        prompt = self._fit_context(prompt)
        call = getattr(self, f"_call_{self.protocol}")
        for attempt in range(1, self.MAX_ATTEMPTS + 1):
            try:
                model, text, stop_reason = call(prompt)
                if text:
                    return model, text
                # Empty answer with a length stop = the output cap was consumed
                # (a thinking model can spend it all on reasoning). Retrying the
                # identical request can't change a server-side cap.
                if stop_reason == "length":
                    print("  ⚠️  返回为空且触发输出长度上限"
                          "（本地模型请在部署侧调大输出上限）")
                    break
                print(f"  ⚠️  模型未返回可见内容 (stop_reason={stop_reason})")
            except Exception as e:
                if "context_length_exceeded" in str(e):
                    print(f"  ❌ prompt 超出上下文窗口被拒，不重试: {e}")
                    break
                if attempt < self.MAX_ATTEMPTS:
                    wait = attempt * 5
                    print(f"  ⚠️  第 {attempt}/{self.MAX_ATTEMPTS} 次失败: {e} "
                          f"— {wait}s 后重试")
                    time.sleep(wait)
                    continue
                print(f"  ❌ 第 {attempt}/{self.MAX_ATTEMPTS} 次失败: {e} — 放弃")
        return None, FAILURE_TEXT

    def _fit_context(self, prompt):
        """Truncate an over-long prompt instead of letting the server reject it.

        Over-limit input comes back as HTTP 400, never silently truncated, so a
        body that doesn't fit would lose the whole summary. Bodies are already
        capped at 8000 chars by fetch_article, so in practice this never fires —
        it's the guard for when that cap is raised.
        """
        if not self.max_prompt_tokens:
            return prompt
        limit = int(self.max_prompt_tokens * CHARS_PER_TOKEN)
        if len(prompt) <= limit:
            return prompt
        print(f"  ⚠️  prompt {len(prompt)} 字超出 {self.name} 的 ~{limit} 字安全预算，截断")
        return prompt[:limit] + "\n\n（正文超长，以上为截断内容）"

    # ------------------------------------------------------- wire protocols
    def _call_bedrock(self, prompt):
        resp = self.client.converse(
            modelId=self.model_id,
            messages=[{"role": "user", "content": [{"text": prompt}]}],
            inferenceConfig={"maxTokens": self.MAX_OUTPUT_TOKENS, "temperature": 0.3},
        )
        text = resp["output"]["message"]["content"][0]["text"].strip()
        return self.model_id, text, resp.get("stopReason")

    def _call_openai(self, prompt):
        # No max_tokens on purpose: for the self-hosted server the output limit is
        # a deployment-side setting, and a cap that's too small is exactly what
        # makes a thinking model return an empty answer.
        resp = self.client.chat.completions.create(
            model=self.model_id,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            timeout=self.TIMEOUT,
        )
        choice = resp.choices[0]
        # Thinking models put their chain of thought in a non-standard
        # `reasoning_content` field (which we ignore) — but some builds inline it
        # in the content as <think>…</think>, so strip that either way.
        text = re.sub(r"<(think|thinking)\b[^>]*>.*?</\1>", "",
                      (choice.message.content or ""), flags=re.S | re.I).strip()
        return (resp.model or self.model_id), text, choice.finish_reason


def format_model_table():
    """Human-readable model list for --list-models / --help."""
    lines = ["可用模型（--model NAME 或 .env 里的 SUMMARIZER_MODEL）:"]
    for key, spec in MODELS.items():
        mark = " (默认)" if key == DEFAULT_MODEL else ""
        lines.append(f"  {key:<10}{spec['note']}{mark}")
    return "\n".join(lines)


if __name__ == "__main__":
    # Smoke test:  ./nhc/bin/python src/summarizer.py [model]
    import sys
    print(format_model_table())
    name = sys.argv[1] if len(sys.argv) > 1 else None
    s = Summarizer(name)
    demo = ("Cargill today announced a $50 million investment to expand its "
            "stevia production capacity in Nebraska, doubling output of its "
            "EverSweet sweetener by 2027 to meet growing demand for "
            "sugar-reduced beverages.")
    t0 = time.time()
    model, text = s.summarize_competitor(demo)
    print(f"\n--- {model} ({time.time() - t0:.1f}s) ---\n{text}")
