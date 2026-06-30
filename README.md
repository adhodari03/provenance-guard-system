# Provenance Guard

A multi-signal AI content attribution API that classifies submitted text as human-authored, AI-generated, or uncertain — and surfaces that verdict to readers through empathetic, plain-language transparency labels.

---

## Why This System Exists

AI detection is an asymmetric risk problem. A false negative — missing AI-generated content — is an inconvenience. A false positive — wrongly accusing a human creator of using AI — can damage their reputation, invalidate their work, and break their trust in a platform permanently.

Most single-signal detectors treat this as a symmetric binary classification problem. They do not. Provenance Guard is built around the principle that **we must be much more afraid of false positives than false negatives.** Every architectural decision — the dual-signal pipeline, the Human Bias Multiplier, the three-tier label system, the appeals workflow — flows from that single constraint.

---

## Detection Signals

Provenance Guard uses two independent signals that measure fundamentally different properties of text. The independence is the point: when both signals agree, we have genuine confidence. When they disagree, we surface uncertainty rather than forcing a verdict.

### Signal 1 — Semantic Evaluator (Groq / Llama-3)

**What it measures:** Phrasing predictability, transitional idioms, and logical coherence. AI models favour highly predictable transitions ("Furthermore," "It is worth noting that," "In conclusion") and an overly uniform, helpful semantic tone. The model evaluates the text and returns a single `ai_probability` float between `0.0` and `1.0`.

**Why we chose it:** A large language model is uniquely positioned to recognise the output patterns of other large language models — it has internalised what "sounds AI-generated" from its training data. No hand-crafted rule set can replicate that coverage. We send the submitted text to `llama-3.3-70b-versatile` via the Groq API with `temperature=0.0` (deterministic) and `max_tokens=64` (no prose, machine-readable JSON only).

**What it misses:** Any human writing that uses formal, predictable phrasing by design — academic papers, legal briefs, grant proposals, structured poetry. These texts share surface-level patterns with AI output and will score high on this signal even when written entirely by a human. The signal also has no awareness of structural variance; a text can be semantically uniform but stylistically erratic, and this signal will only see the former.

### Signal 2 — Structural Evaluator (Pure Python Stylometrics)

**What it measures:** Two orthogonal sub-metrics computed entirely locally with no external libraries:

- **Burstiness** — the standard deviation of sentence lengths in words. Human writers naturally alternate between short, punchy sentences and long, elaborative ones. AI models regress to a mean sentence length, producing low variance.
- **Lexical Diversity** — the ratio of unique words to total words (Type-Token Ratio). Human creative writing uses richer, more idiosyncratic vocabulary. AI favours high-frequency, safe words.

Both sub-metrics are normalised and combined into a single float: `0.0` = high human variance, `1.0` = uniform/AI.

**Why we chose it:** This signal is entirely local — no API call, no cost, no added latency. More importantly, it captures *structural* authorship fingerprints that are orthogonal to the *semantic* patterns Signal 1 measures. A text can sound formal and AI-like (high Signal 1) while still being written with erratic sentence lengths and rich vocabulary (low Signal 2). The disagreement between signals is itself meaningful information.

**What it misses:** Short texts — anything under two sentences or roughly 50 words — do not provide enough data for variance to be statistically meaningful. The signal returns a neutral `0.5` in these cases, effectively abstaining. It also cannot distinguish *deliberate* structural uniformity (a haiku, a legal clause) from *AI-induced* uniformity; both look identical to a standard deviation calculation.

### Why Two Signals Is Better Than One

A single LLM-based detector has one catastrophic failure mode: it can be confidently wrong. Formal human writing — academic papers, legal briefs, structured poetry — routinely triggers high semantic AI-probability scores because it shares surface-level patterns with AI output. Without a structural check, there is no circuit-breaker.

The Structural Evaluator acts as that circuit-breaker. It cannot be fooled by phrasing choices; it only sees the mathematical shape of the text. When a human writer's structural variance is strong enough, the system overrides the LLM's verdict regardless of the semantic score.

### What We Would Change for Production

- **N-gram tie-breaker (Signal 3):** A local lexicon check for overused AI-marker words ("delve," "tapestry," "testament," "it's worth noting") would serve as a low-cost tiebreaker when Signals 1 and 2 disagree. Planned as a stretch feature.
- **Async processing:** At scale, the Groq API call introduces latency on the submission path. In production this would move to an async queue (e.g. Celery + Redis): the `/submit` endpoint returns a `202 Accepted` with a `job_id` immediately, and the client polls for the result.
- **Model versioning:** AI writing styles drift as models improve. The Groq prompt and scoring thresholds would need periodic recalibration against labelled ground-truth data.

---

## Confidence Scoring and the Human Bias Multiplier

### How Scores Are Calculated

The two signal scores are combined using a weighted average:

```
base_score = (semantic_score × 0.55) + (structural_score × 0.45)
```

Signal 1 carries slightly more weight because LLM-based semantic evaluation is a stronger generalised signal. But it does not get the final word.

### The Human Bias Multiplier

If the Structural Evaluator detects strong human variance — a `structural_score < 0.30` — the Groq score is penalised by a `0.5×` multiplier before the weighted average is computed:

```python
if structural_score < 0.30:
    effective_groq = groq_score * 0.5
else:
    effective_groq = groq_score

final_score = (effective_groq × 0.55) + (structural_score × 0.45)
```

**Why such an aggressive penalty?** Because we accept a higher false-negative rate (occasionally missing AI content) in exchange for a lower false-positive rate (wrongly flagging a human creator). The `0.5×` multiplier ensures that when a human's structural fingerprint is unmistakably present, the LLM cannot unilaterally push the score into the accusatory zone. The cost of a false positive — a damaged reputation, a removed piece, a broken relationship with a creator — is categorically higher than the cost of a false negative.

### Score Thresholds and Labels

| Score Range | Classification | Label Displayed |
|---|---|---|
| `0.00 – 0.49` | Likely Human | "Authentic Work: Our systems indicate this content features the natural variance and structure of human creativity." |
| `0.50 – 0.84` | Uncertain | "Mixed Signals: This work contains structural patterns common to both human writing and AI assistance. We prioritize creator trust and assume human authorship." |
| `0.85 – 1.00` | Likely AI | "AI-Generated: Strong multi-signal indicators suggest this work was primarily generated using AI tools." |

A score of `0.51` and a score of `0.95` both trigger transparency labels — but meaningfully different ones. The `0.51` label explicitly states that creator trust is assumed. The `0.95` label surfaces the AI verdict clearly. A binary classifier would collapse both into the same output.

### Real Test Examples

#### Example 1 — Clearly Human

**Text submitted:**
> "ok so i finally tried that new ramen place downtown and honestly? underwhelming. the broth was fine but they put WAY too much sodium in it and i was thirsty for like three hours after. my friend got the spicy version and said it was better. probably won't go back unless someone drags me there"

| Signal | Score |
|---|---|
| Semantic (Groq) | `0.23` |
| Structural (Stylometrics) | `0.40` |
| **Final Confidence** | **`0.3045`** |
| **Verdict** | **Human** |

**Why this score:** Groq correctly identified the informal, unpredictable phrasing as human (`0.23`). The stylometrics registered moderate sentence variance and non-repetitive vocabulary (`0.40`). Both signals agreed, and the final score landed comfortably in the "Likely Human" zone. No veto needed — the signals were already aligned.

---

#### Example 2 — Clearly AI

**Text submitted:**
> "Furthermore, it is worth noting that leveraging cutting-edge machine learning algorithms offers unprecedented opportunities for innovation across diverse domains. In conclusion, these transformative solutions empower organizations to achieve their strategic objectives."

| Signal | Score |
|---|---|
| Semantic (Groq) | `0.92` |
| Structural (Stylometrics) | `0.40` |
| **Final Confidence** | **`0.686`** |
| **Verdict** | **Uncertain** |

**Why this score — and why "Uncertain" instead of "AI":** Groq correctly flagged the text as highly AI-like (`0.92`) — the phrasing is textbook AI output. However, the text is only two sentences long. The Structural Evaluator could not gather enough data to produce a meaningful variance measurement and returned a neutral `0.40`. Because Signal 2 did not cross the `< 0.30` veto threshold, the Human Bias Multiplier did not fire — but the structural score still pulled the weighted average down from `0.92` to `0.686`, landing in "Uncertain" rather than "AI."

**This is the system behaving correctly.** A two-sentence sample is genuinely ambiguous — even a human writing a single formal sentence would score similarly. We surface that uncertainty rather than condemning the creator based on insufficient structural evidence. With a longer sample, the structural signal would carry more weight and the score would shift accordingly.

---

## Transparency Labels

Labels are mapped from the final confidence score and returned in every `/submit` response under the `label` key. They are written to address the *work*, never the *creator* — we never say "you used AI."

**High-Confidence Human (`0.00 – 0.49`):**
> "Authentic Work: Our systems indicate this content features the natural variance and structure of human creativity."

**Uncertain / Mixed Signals (`0.50 – 0.84`):**
> "Mixed Signals: This work contains structural patterns common to both human writing and AI assistance. We prioritize creator trust and assume human authorship."

**High-Confidence AI (`0.85 – 1.00`):**
> "AI-Generated: Strong multi-signal indicators suggest this work was primarily generated using AI tools."

The uncertain label explicitly states our tie-breaking policy — creator trust is assumed — so the platform's stance is transparent to any reader who sees it. The AI label says "primarily generated" to acknowledge that AI-assisted editing is a spectrum, not a binary.

---

## Appeals Workflow

Creators who believe their content has been misclassified can submit an appeal via `POST /appeal`. The endpoint accepts a JSON body containing the `content_id` of the original submission and a `creator_reasoning` field where the creator explains their writing process or provides context.

```bash
POST /appeal
Content-Type: application/json

{
  "content_id": "fe1cd1eb-6ad1-48ff-bc35-7de41049f36c",
  "creator_reasoning": "I wrote this myself as part of a formal grant proposal. The structured phrasing is required by the submission guidelines, not AI-generated."
}
```

When an appeal is received the system:

1. Looks up the `content_id` in the `audit_log` table and returns `404` if it does not exist.
2. Updates the row's `status` from `classified` to `under_review`.
3. Saves the full `creator_reasoning` text to the `creator_reasoning` column on that same row.
4. Returns a `202 Accepted` response confirming the appeal is logged.

No automated re-classification occurs. The record enters a human review queue where an admin can see the original signal breakdown (semantic score, structural score, final confidence) alongside the creator's written justification.

The rate limit on `/appeal` is **3 requests per hour** — appeals are deliberate, high-intent actions and this prevents flooding the review queue to suppress an AI label.

---

## Rate Limiting

Rate limiting is implemented using Flask-Limiter with in-memory storage. Limits are applied per IP address.

| Endpoint | Limit | Window | Reasoning |
|---|---|---|---|
| `POST /submit` | 10 requests | 1 minute | Expensive endpoint (Groq API call + DB write); stays well below Groq's free-tier ceiling of ~30 RPM |
| `POST /appeal` | 3 requests | 1 hour | Deliberate action; prevents flooding the appeal queue to suppress AI labels |
| `GET /log` | 30 requests | 1 minute | Read-only, no external calls; generous limit for admin/debug use |

The 10 requests/minute limit on `/submit` is generous enough for any human user testing the API while blocking automated scraping loops. When the limit is exceeded the server returns `429 Too Many Requests`.

**Verified terminal output — 11th request triggers 429:**

```
127.0.0.1 - - [30/Jun/2026 15:14:07] "POST /submit HTTP/1.1" 200 -
127.0.0.1 - - [30/Jun/2026 15:14:09] "POST /submit HTTP/1.1" 200 -
127.0.0.1 - - [30/Jun/2026 15:14:11] "POST /submit HTTP/1.1" 200 -
127.0.0.1 - - [30/Jun/2026 15:14:12] "POST /submit HTTP/1.1" 200 -
127.0.0.1 - - [30/Jun/2026 15:14:14] "POST /submit HTTP/1.1" 200 -
127.0.0.1 - - [30/Jun/2026 15:14:17] "POST /submit HTTP/1.1" 200 -
127.0.0.1 - - [30/Jun/2026 15:14:18] "POST /submit HTTP/1.1" 200 -
127.0.0.1 - - [30/Jun/2026 15:14:20] "POST /submit HTTP/1.1" 200 -
127.0.0.1 - - [30/Jun/2026 15:14:21] "POST /submit HTTP/1.1" 200 -
127.0.0.1 - - [30/Jun/2026 15:14:22] "POST /submit HTTP/1.1" 200 -
127.0.0.1 - - [30/Jun/2026 15:14:23] "POST /submit HTTP/1.1" 429 -
127.0.0.1 - - [30/Jun/2026 15:14:26] "POST /submit HTTP/1.1" 429 -
127.0.0.1 - - [30/Jun/2026 15:14:44] "POST /submit HTTP/1.1" 429 -
```

---

## Audit Log

Every attribution decision is persisted to a local SQLite database (`provenance.db`) in the `audit_log` table. Each row captures the full signal breakdown — both individual scores and the combined confidence — alongside the submission metadata and current status.

**Schema:**

| Column | Type | Description |
|---|---|---|
| `content_id` | TEXT (UUID) | Primary key, returned to the caller |
| `creator_id` | TEXT | Provided by the caller in the request body |
| `timestamp` | TEXT | UTC ISO 8601 timestamp of the decision |
| `attribution` | TEXT | `human`, `uncertain`, or `ai` |
| `confidence` | REAL | Final synthesized score after Human Bias Multiplier |
| `llm_score` | REAL | Raw Signal 1 output from Groq |
| `stylo_score` | REAL | Raw Signal 2 output from stylometrics |
| `status` | TEXT | `classified` or `under_review` |
| `creator_reasoning` | TEXT | Populated when an appeal is submitted, null otherwise |

**Live log output (`GET /log`) — includes a classified entry, a human entry, and an appealed entry:**

```json
{
    "entries": [
        {
            "attribution": "uncertain",
            "confidence": 0.686,
            "content_id": "fe1cd1eb-6ad1-48ff-bc35-7de41049f36c",
            "creator_id": "user_ai",
            "creator_reasoning": "I wrote this myself as part of a formal grant proposal. The structured phrasing is required by the submission guidelines, not AI-generated.",
            "llm_score": 0.92,
            "status": "under_review",
            "stylo_score": 0.4,
            "timestamp": "2026-06-30T19:21:05.837436+00:00"
        },
        {
            "attribution": "human",
            "confidence": 0.3507,
            "content_id": "c301556f-b5b8-4713-8a0a-bce46d69a0b6",
            "creator_id": "user_human",
            "creator_reasoning": null,
            "llm_score": 0.23,
            "status": "classified",
            "stylo_score": 0.4983,
            "timestamp": "2026-06-30T19:20:58.219467+00:00"
        },
        {
            "attribution": "uncertain",
            "confidence": 0.7035,
            "content_id": "c013b349-c251-4248-abd0-11aaad09ce50",
            "creator_id": "user_1",
            "creator_reasoning": null,
            "llm_score": 0.87,
            "status": "classified",
            "stylo_score": 0.5,
            "timestamp": "2026-06-30T19:14:22.888292+00:00"
        }
    ]
}
```

The log endpoint returns the 10 most recent entries ordered by timestamp descending. The first entry above shows a completed appeal — `status: "under_review"` with the creator's full reasoning saved. The second shows a clean human classification. The third is a short-text submission where `stylo_score: 0.5` reflects the neutral fallback for texts under 50 words.

---

## Known Limitations

These limitations are documented honestly based on observed behaviour during testing, not hypothetical edge cases.

**Short texts produce unreliable structural scores.** The Structural Evaluator (Signal 2) needs a minimum of two sentences and roughly 50 words to produce a statistically meaningful variance measurement. Submissions shorter than this threshold — a single sentence, a tweet, a short caption — automatically receive a neutral `stylo_score` of `0.5`. This means the final confidence score leans entirely on Groq's semantic judgment, which is itself less reliable on short samples. In practice, most short texts will land in the "Uncertain" band regardless of their actual origin. This is the honest outcome and is preferable to fabricating a structural verdict from insufficient data.

**Highly structured human writing will be falsely flagged.** Academic papers, legal briefs, grant proposals, and strictly formatted poetry (sonnets, haikus, villanelles) share two properties with AI output: uniform sentence length and constrained vocabulary. Both properties drive the structural score upward. A legal document written entirely by a human will often score `0.65–0.75` — landing in "Uncertain" — because Signal 2 cannot distinguish deliberate formal structure from AI uniformity. The appeals workflow is the designed escape valve for this case: creators of structured work can provide context explaining why their text looks the way it does. A production system would also benefit from a content-type flag allowing creators to declare "this is a legal document" before submission, adjusting the structural scoring accordingly.

**The system is not a ground-truth detector.** No AI content detection system is. Provenance Guard is a transparency tool — it surfaces what the signals say and communicates uncertainty honestly. It is not a moderation enforcement mechanism and should not be used as one.

---

## Spec Reflection

### Where the Spec Guided Implementation Well

Defining the uncertainty thresholds — and their exact label text — before writing a single line of detection code was the most important sequencing decision in this project. Because the thresholds (`0.49` and `0.85`) and the Human Bias Multiplier logic were committed to `planning.md` first, the implementation had an unambiguous target. There was never a temptation to tune the math to produce "cleaner" results, because the spec had already decided what the system should do when signals disagreed: default to creator trust, surface the uncertainty, and explain it in plain language. This prevented the most common failure mode of binary classifiers — collapsing genuine ambiguity into a forced verdict because the spec didn't account for the middle case.

### Where We Diverged from the Spec

The spec anticipated that clearly AI-written text would reliably score above `0.85` and trigger the "Likely AI" label. In testing, short AI samples (two to three sentences) consistently landed in the `0.65–0.75` range instead — "Uncertain" rather than "AI." The structural score, forced to neutral (`0.5`) on short texts, was pulling the weighted average down enough to keep the score out of the accusatory zone.

Rather than artificially inflate the math to match the spec's expectation, we embraced this as correct behaviour and updated the README analysis to explain it. A two-sentence sample is genuinely ambiguous — the structural signal has nothing to measure. The spec's threshold is right for full-length submissions; the neutral fallback is right for short ones. Diverging from the expected output in this case was the honest choice.

---

## AI Tool Usage

This section documents two specific instances where an AI coding assistant was used during implementation, including where its output required correction.

### Instance 1 — SQLite Schema Migration for Appeals

**What we asked for:** Update the `audit_log` table schema to add a `creator_reasoning` column (TEXT, nullable) to support the appeals workflow.

**What the AI produced:** A multi-step migration script using `ALTER TABLE` with existence checks, a backup-and-restore pattern, and several layers of error handling designed for a production database with live data. The script was technically correct but wildly over-engineered for a local development environment with a database that is intentionally dropped and recreated on every `init_db()` call.

**What we overrode:** We discarded the migration script entirely and added `DROP TABLE IF EXISTS audit_log` to `init_db()` before the `CREATE TABLE` statement. Since this is a test environment with no persistent data worth preserving, the right solution was a clean recreate — not a migration. The AI had optimised for the wrong constraint (data preservation) rather than the actual constraint (development simplicity).

### Instance 2 — Stylometrics ZeroDivisionError on Single-Sentence Input

**What we asked for:** Write pure Python functions for `burstiness()` (standard deviation of sentence lengths) and `lexical_diversity()` (Type-Token Ratio), then combine them into a single `analyze_stylometrics()` function.

**What the AI produced:** A correct implementation for multi-sentence texts that crashed with a `ZeroDivisionError` when the input contained only one sentence. The burstiness calculation computes a standard deviation across sentence lengths — with a single sentence, the list has one element, the mean equals that element, and the variance sum is `0.0`. The division to compute the final score then attempted to normalise over a zero range.

**What we fixed:** We added an explicit guard at the top of `analyze_stylometrics()`: if the split produces fewer than two sentences or the word list is empty, return `0.5` immediately. This matches the Edge Case 3 specification in `planning.md` (texts under 50 words return neutral) and prevents the crash entirely. The fix was one conditional — but it required understanding why the math broke, not just catching the exception.

---

## Stretch Feature: Ensemble Detection (Signal 3 — N-gram Analysis)

A third signal was added to the detection pipeline: a local lexicon check for known AI-favoured words and phrases. This runs entirely in Python with no external dependencies.

**What it measures:** The density of AI-marker vocabulary per 100 words. The lexicon contains ~35 weighted terms including single words ("delve," "tapestry," "transformative," "stakeholders") and multi-word phrases ("it is worth noting," "in the realm of," "drive innovation"). Each marker carries a weight between `0.8` and `1.5` based on how exclusively it is associated with AI output. The raw density is sigmoid-compressed to `[0.0, 1.0]`.

**How conflicts between signals are resolved:** With all three signals active, weights shift to:

```
final_score = (semantic × 0.45) + (structural × 0.35) + (ngram × 0.20)
```

Signal 3 carries the least weight because vocabulary trends shift over time — words that mark AI output in 2026 may be mainstream by 2029. The Human Bias Veto (structural score `< 0.30` → Groq penalised by `0.5×`) still applies before the weighted average is computed.

**Live demo — AI-dense text with all three signal scores:**

```json
{
    "attribution_result": "uncertain",
    "confidence_score": 0.7496,
    "content_id": "4b668a21-afe8-4767-be25-76c037877faf",
    "label": "Mixed Signals: This work contains structural patterns common to both human writing and AI assistance. We prioritize creator trust and assume human authorship.",
    "signals": {
        "ngram_score": 1.0,
        "semantic_score": 0.92,
        "structural_score": 0.3875
    }
}
```

`ngram_score: 1.0` — maximum marker density, text contained "furthermore," "it is worth noting," "cutting-edge," "transformative," "stakeholders," and "unprecedented" in two sentences. `semantic_score: 0.92` — Groq agreed. `structural_score: 0.3875` — just above the veto threshold, pulling the final score down to `0.7496` (Uncertain).

**Human text for comparison:**

```json
{
    "attribution_result": "human",
    "confidence_score": 0.3017,
    "signals": {
        "ngram_score": 0.1192,
        "semantic_score": 0.23,
        "structural_score": 0.4983
    }
}
```

`ngram_score: 0.1192` — near zero AI marker density on casual, conversational text. All three signals agree: human.

---

## Stretch Feature: Analytics Dashboard (`GET /api/v1/metrics`)

A read-only metrics endpoint that computes detection patterns and appeal rates from the live SQLite database.

**Endpoint:** `GET /api/v1/metrics` — rate limited to 30 requests per minute.

**Metrics returned:**

| Metric | Description |
|---|---|
| `total_submissions` | Total records in the audit log |
| `label_distribution` | Count of each verdict (`human`, `uncertain`, `ai`) |
| `under_review_count` | Submissions currently flagged by an appeal |
| `under_review_rate_pct` | Proxy for false-positive rate — if this spikes, thresholds need recalibration |
| `appeal_count` | Total appeals submitted |
| `appeal_rate_pct` | Measures creator trust — high rate signals the system is over-flagging |

**Live output:**

```json
{
    "appeal_count": 1,
    "appeal_rate_pct": 20.0,
    "label_distribution": {
        "human": 1,
        "uncertain": 4
    },
    "total_submissions": 5,
    "under_review_count": 1,
    "under_review_rate_pct": 20.0
}
```

The `appeal_rate_pct: 20.0` here reflects a small test dataset — in a real platform a sustained rate above 5% would indicate the thresholds need recalibration. The `label_distribution` shows the system is not over-indexing on "ai" verdicts, which matches the design intent (creator trust assumed in ambiguous cases).
