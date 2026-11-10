"""Tenglish talking script for a news story: the "తెలుగు" button under each AI update.

He records himself on the phone in Telugu, so this writes what he says: Telugu in English letters mixed with the
English tech words people really use (Tenglish), split into short clips, 30-80 seconds, with a strong hook.
The draft is reviewed by a separate critic pass and rewritten until it passes (at most MAX_ROUNDS reviews).
"""

import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

WPS = 2.3                # Tenglish words per second when he talks naturally
MIN_SEC, MAX_SEC = 30, 80
MIN_WORDS, MAX_WORDS = round(MIN_SEC * WPS), round(MAX_SEC * WPS)   # 69-184
MIN_ROUNDS = 2           # always at least one rewrite: the first draft is never the final script
MAX_ROUNDS = 4           # critic reviews (each failed review is followed by a rewrite)
PASS_SCORE = 9

STYLE = """How he talks (Tenglish):
- Telugu written in English letters, mixed with English words exactly like a young Hyderabad tech creator speaks:
  "Guys, OpenAI ippudu oka kotha model release chesindi, and idi chala fast." Tech words, product names and numbers
  stay in English (model, launch, free, update, coding, agent, 31%). Never Telugu script, never pure English lines.
- Spoken, casual and warm: "asalu", "chudandi", "ento telusa?", "simple ga cheppalante", "idi chala important",
  "mana daily life lo". Short sentences that are easy to say in one breath. No formal or bookish Telugu
  (no "pratyekamaina", "vishleshana", "prakatinchindi"), no literal word-for-word translation from English.
- He talks TO the viewer (meeru / mana), like explaining to a friend, with energy but no shouting or hype words."""

WRITE_PROMPT = """You write short-form video scripts (Instagram Reels / YouTube Shorts) for Ashok (@ashok_a.i), a
Telugu AI/tech creator who records himself on the phone's front camera.

The story (use ONLY these facts; never invent numbers, names or dates):
Title: {title}
Summary: {summary}
Key points: {points}
Why it matters: {why}
Source: {source}

{style}

Structure:
- hook: the first 2-3 seconds, max 14 words. It must stop a scrolling viewer: lead with the most surprising
  concrete fact, a bold number, a "meeku telusa?" curiosity gap, or a direct "if you use X, idi miss avvakandi"
  line. 100% true to the facts. Never "Hello guys welcome to my channel".
- clips: 4-8 more clips after the hook, each 1-2 short sentences (about 6-12 seconds), in this flow: what happened
  -> how it works / what is new (simple example) -> why it matters to the viewer (Indian viewer, students,
  developers, creators) -> one sharp takeaway or opinion -> a short natural call to action as the last clip
  (comment / follow, max 12 words, tied to the topic).
- Every clip adds something new: the clip after the hook never repeats the hook's fact, no line says the same
  thing twice.
- Total spoken length {min_sec}-{max_sec} seconds: {min_words}-{max_words} words in all (hook + clips). Aim for
  about {target} words unless the story is very thin.

Return ONLY JSON: {{"title": "<short English title>", "hook": "...", "clips": ["...", "..."]}}"""

REVIEW_PROMPT = """You are a strict editor of Telugu short-form video scripts. Review this Tenglish script for the
creator Ashok (@ashok_a.i), who will say it to his phone camera.

The facts it must stay true to:
Title: {title}
Summary: {summary}
Key points: {points}

{style}

The script ({words} words, about {seconds} seconds spoken; it must be {min_sec}-{max_sec} seconds,
{min_words}-{max_words} words):
HOOK: {hook}
{clips}

Be harsh: 10 = publish-ready as is; most first drafts deserve 6-8. Read every line aloud in your head as he
would say it. Score 1-10 each:
- hook: would a scrolling Telugu viewer stop in the first 2 seconds? concrete, curious, true, max 14 words
- natural: sounds like a real Hyderabad creator talking (Tenglish mix, casual), not translated or bookish;
  every line easy to say in one breath
- engaging: keeps attention clip to clip, simple example, clear "why should I care"; any repeated fact (e.g. the
  clip after the hook restating the hook), filler or generic hype line ("future of tech") costs points
- accurate: every claim matches the facts above, nothing invented
- flow: each clip leads to the next; the ending lands with a takeaway and a short, natural call to action
List concrete fixes (quote the weak line and say what to write instead). passed = true only if every score is
>= {pass_score} and the length is inside the range.
Return ONLY JSON: {{"scores": {{"hook": 0, "natural": 0, "engaging": 0, "accurate": 0, "flow": 0}},
"passed": false, "fixes": ["..."]}}"""

REWRITE_PROMPT = """Rewrite this Tenglish video script for Ashok (@ashok_a.i) using the editor's fixes. Keep what
already works; fix every point. Use ONLY these facts:
Title: {title}
Summary: {summary}
Key points: {points}
Why it matters: {why}

{style}

Current script ({words} words; it must be {min_words}-{max_words} words in all, {min_sec}-{max_sec} seconds):
HOOK: {hook}
{clips}

Editor's fixes:
{fixes}

Return ONLY JSON: {{"title": "<short English title>", "hook": "...", "clips": ["...", "..."]}}"""


def _clean(draft: Dict[str, Any]) -> Dict[str, Any]:
    hook = " ".join(str(draft.get("hook") or "").split())
    clips = [" ".join(str(c.get("text") if isinstance(c, dict) else c or "").split()) for c in draft.get("clips") or []]
    clips = [c for c in clips if c]
    if not hook and clips:
        hook = clips.pop(0)
    if not hook or not clips:
        raise ValueError("the script came back empty")
    return {"title": " ".join(str(draft.get("title") or "").split())[:80], "hook": hook, "clips": clips[:9]}


def _words(script: Dict[str, Any]) -> int:
    return len(script["hook"].split()) + sum(len(c.split()) for c in script["clips"])


def _facts(item: Any) -> Dict[str, str]:
    return {"title": item.title, "summary": item.summary,
            "points": "; ".join(getattr(item, "key_bullet_points", None) or []) or "-",
            "why": getattr(item, "why_it_matters", "") or "-",
            "source": getattr(item, "source_name", "") or getattr(item, "origin", "") or "-"}


def _numbered(script: Dict[str, Any]) -> str:
    return "\n".join(f"CLIP {i}: {c}" for i, c in enumerate(script["clips"], 1))


async def write_script(item: Any, llm: Any, model: str = None) -> Dict[str, Any]:
    """Draft -> critic review -> rewrite, until the critic passes it (or MAX_ROUNDS reviews). Every rewrite starts
    from the best version so far, and the best-reviewed version is returned (a rewrite can score lower):
    {"title", "hook", "clips", "words", "seconds", "rounds", "scores", "passed"}."""
    facts = _facts(item)
    limits = {"min_sec": MIN_SEC, "max_sec": MAX_SEC, "min_words": MIN_WORDS, "max_words": MAX_WORDS}
    script = _clean(await llm.generate_json(WRITE_PROMPT.format(**facts, **limits, style=STYLE,
                                                                target=round(55 * WPS)), model=model))
    review: Dict[str, Any] = {}
    best = None  # (rank, script, review, fixes)
    rounds = 0
    for rounds in range(1, MAX_ROUNDS + 1):
        words = _words(script)
        review = await llm.generate_json(REVIEW_PROMPT.format(
            **facts, **limits, style=STYLE, words=words, seconds=round(words / WPS), hook=script["hook"],
            clips=_numbered(script), pass_score=PASS_SCORE), model=model)
        scores = {k: int(v) for k, v in (review.get("scores") or {}).items() if str(v).strip().lstrip("-").isdigit()}
        review["scores"] = scores
        fixes = [str(f) for f in review.get("fixes") or [] if str(f).strip()]
        if not MIN_WORDS <= words <= MAX_WORDS:  # length is checked here too, not left to the critic
            fixes.insert(0, f"The script is {words} words (about {round(words / WPS)} s). Make it "
                            f"{'longer' if words < MIN_WORDS else 'shorter'}: {MIN_WORDS}-{MAX_WORDS} words in all.")
        review["passed"] = bool(scores) and min(scores.values()) >= PASS_SCORE and MIN_WORDS <= words <= MAX_WORDS
        logger.info(f"Telugu script review {rounds}: {scores} words={words} passed={review['passed']}")
        rank = (review["passed"], MIN_WORDS <= words <= MAX_WORDS, min(scores.values(), default=0),
                sum(scores.values()))
        if best is None or rank > best[0]:
            best = (rank, script, review, fixes)
        if (best[2]["passed"] and rounds >= MIN_ROUNDS) or rounds == MAX_ROUNDS:
            break
        _, base, _, base_fixes = best
        try:
            script = _clean(await llm.generate_json(REWRITE_PROMPT.format(
                **facts, **limits, style=STYLE, words=_words(base), hook=base["hook"], clips=_numbered(base),
                fixes="\n".join(f"- {f}" for f in base_fixes) or "- Make it more natural and engaging."), model=model))
        except Exception as e:  # keep the best draft so far
            logger.warning(f"Telugu script rewrite {rounds} failed: {e}")
            break
    _, script, review, _ = best
    words = _words(script)
    return {**script, "words": words, "seconds": round(words / WPS), "rounds": rounds,
            "scores": review.get("scores") or {}, "passed": bool(review.get("passed"))}


def script_markdown(s: Dict[str, Any], story_title: str) -> str:
    """The finished script as clips with times, ready to read out while recording."""
    from src.studio import _fmt
    lines = [f"**తెలుగు script · {s['title'] or story_title}** (about {s['seconds']}s, {len(s['clips']) + 1} clips)", ""]
    t = 0.0
    for i, text in enumerate([s["hook"]] + s["clips"], 1):
        d = len(text.split()) / WPS
        label = "Hook" if i == 1 else f"Clip {i}"
        lines += [f"**{label} ({_fmt(t)}-{_fmt(t + d)})**", f"Says: \"{text}\"", ""]
        t += d
    names = {"hook": "Hook", "natural": "Natural", "engaging": "Engaging", "accurate": "Accurate", "flow": "Flow"}
    score = " · ".join(f"{names.get(k, k)} {v}/10" for k, v in s["scores"].items())
    check = f"Reviewed {s['rounds']} time{'s' if s['rounds'] > 1 else ''}"
    check += " and passed" if s["passed"] else " (best version kept)"
    lines.append(f"::meta {check} · {score}" if score else f"::meta {check}")
    return "\n".join(lines)


def full_text(s: Dict[str, Any]) -> str:
    return "\n".join([s["hook"]] + s["clips"])

