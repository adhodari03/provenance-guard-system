# Provenance Guard: Project Planning & Architecture

## 1. Detection Signals

To prevent catastrophic false positives, we use a multi-signal pipeline that measures genuinely independent properties of the text.

### Signal 1: Semantic Evaluator (Groq / Llama-3 API)

- **What it measures:** Phrasing predictability, transitional idioms, and logical coherence. AI models favor highly predictable transitions ("Furthermore," "In conclusion,") and overly helpful, uniform semantic tones.
- **Why this signal:** A large language model is uniquely positioned to recognize the output patterns of other large language models — it has internalized what "sounds AI-generated" from training data.
- **Output:** A float between `0.0` (entirely human) and `1.0` (entirely AI).
- **Implementation:** A structured prompt is sent to `llama-3.3-70b-versatile` via the Groq API, instructing the model to evaluate the submitted text and return only a JSON object containing a single `score` field. No prose — machine-readable output only.

### Signal 2: Structural Evaluator (Python Stylometrics)

- **What it measures:** Two orthogonal sub-metrics:
  - **Burstiness** — the standard deviation of sentence lengths. Human writers naturally alternate between short punchy sentences and long elaborative ones. AI models regress to a mean sentence length, producing low variance.
  - **Lexical Diversity** — the ratio of unique words to total words (Type-Token Ratio). Human creative writing uses richer, more idiosyncratic vocabulary. AI favors high-frequency, safe words.
- **Why this signal:** This signal is entirely local — no API call, no cost, no latency. It captures *structural* authorship fingerprints that are orthogonal to the *semantic* patterns Signal 1 measures. The two signals can disagree, which is valuable information.
- **Output:** A float between `0.0` (high variance / human) and `1.0` (low variance / AI), computed as: `1 - normalize(burstiness + lexical_diversity)`.

### Combining the Signals: Human Bias Multiplier

We take the weighted average of both signals and apply a structural veto:

```
base_score = (signal_1 * 0.55) + (signal_2 * 0.45)
```

Signal 1 carries slightly more weight because LLM-based semantic evaluation is a stronger generalized signal than heuristics alone.

**Human Bias Veto:** If the Structural Evaluator returns a score `< 0.3` (strong human variance detected), the final score is hard-capped at `0.49`, regardless of what the Semantic Evaluator returns. This prevents the LLM from unilaterally condemning a human writer whose text happens to use formal transitions.

```
if signal_2 < 0.3:
    final_score = min(base_score, 0.49)
else:
    final_score = base_score
```

**Rationale:** We accept a higher false-negative rate (missing AI content) in exchange for a lower false-positive rate (wrongly accusing human creators). The cost of a false positive — damaging a human creator's reputation — is higher than the cost of a false negative.

---

## 2. Uncertainty Representation

Uncertainty is a feature, not a bug. Perfect AI detection does not exist, so our scoring must gracefully handle ambiguity.

**What 0.60 means:** A score of 0.60 means the text contains robotic structural elements or predictable phrasing, but exhibits enough human variance that we cannot comfortably accuse the author of using AI. We default to trusting the creator.

**What 0.30 means:** The text shows strong stylometric markers of human authorship — erratic sentence length, rich vocabulary. Even if the LLM finds the phrasing predictable (e.g., structured poetry), the structural veto caps the score below the accusatory threshold.

### Thresholds

| Score Range  | Classification          | Default Creator Stance |
|---|---|---|
| `0.00 – 0.49` | Likely Human            | Trust assumed          |
| `0.50 – 0.84` | Uncertain (Mixed Signals) | Trust assumed        |
| `0.85 – 1.00` | Likely AI               | Flag for transparency  |

---

## 3. Transparency Label Design

User-facing labels must communicate verdicts empathetically, honestly, and without accusing creators of dishonesty.

### Label Variants (Exact Text)

**High-Confidence Human (`0.00 – 0.49`):**
> "Authentic Work: Our systems indicate this content features the natural variance and structure of human creativity."

**Uncertain / Mixed Signals (`0.50 – 0.84`):**
> "Mixed Signals: This work contains structural patterns common to both human writing and AI assistance. We prioritize creator trust and assume human authorship."

**High-Confidence AI (`0.85 – 1.00`):**
> "AI-Generated: Strong multi-signal indicators suggest this work was primarily generated using AI tools."

### Design Principles

- Labels address the *work*, not the *creator*. We never say "you used AI."
- The uncertain label explicitly states our tie-breaking policy (creator trust) so the platform's stance is transparent.
- The AI label says "primarily generated" — acknowledging that AI-assisted editing is a spectrum, not a binary.

---

## 4. Appeals Workflow

Mistakes will happen. Creators must have an immediate, transparent path to contest a classification.

### Who Can Appeal

The creator who originally submitted the content (identified by `creator_id` on the submission record).

### Information Captured

A text field (`appeal_reason`) where the creator explains their writing process or provides context. Example: *"This is heavily structured code documentation, which is why it looks uniform. I wrote every word."*

### System Actions on Appeal

1. The `submissions` table record for that `submission_id` is updated: `status → UNDER_REVIEW`.
2. A new row is written to the `audit_log` table with:
   - `event_type: APPEAL_SUBMITTED`
   - `submission_id`
   - `creator_id`
   - `appeal_reason` (full text)
   - `timestamp`
3. The API returns a `202 Accepted` response confirming the appeal is logged.
4. *(Front-end note, out of scope for this project):* When `status = UNDER_REVIEW`, the transparency label is suppressed and replaced with "This content is under human review."

### Human Reviewer View

When an admin queries the appeal queue, each record exposes:
- Original submitted text
- Signal 1 score (Semantic Evaluator)
- Signal 2 score (Structural Evaluator)
- Final synthesized score
- Transparency label that was displayed
- Creator's written justification
- Timestamp of original submission and appeal

---

## 5. Anticipated Edge Cases

We proactively identify where our signals have known blind spots.

### Edge Case 1: Structured Poetry (Haiku, Sonnet, Villanelle)

**Problem:** A strictly formatted poem requires constrained line length, repetitive structure, and limited vocabulary by design. The Structural Evaluator will misread this as AI behavior (low variance, low lexical diversity).

**Mitigation:** The Human Bias Veto partially helps if the poet's word choices are idiosyncratic. A fully rhymed, formally constrained poem may still land in the Uncertain band (0.50–0.84), which defaults to creator trust. This is an acceptable outcome.

### Edge Case 2: Technical / Legal Documentation

**Problem:** API documentation and legal agreements rely on repetitive, formal transitions and constrained vocabulary. Signal 1 will flag the predictable phrasing; Signal 2 will flag the uniform sentence structure. Both signals agree: AI. But the creator may have written every word.

**Mitigation:** The `appeal_reason` field is the correct escape valve here. A creator can provide context. This is also a good candidate for the Provenance Certificate stretch feature.

### Edge Case 3: Very Short Submissions

**Problem:** A 10-word submission does not have enough text for the Structural Evaluator to produce statistically meaningful variance measurements.

**Mitigation:** If the submission is fewer than 50 words, set `signal_2 = 0.5` (neutral) and return a response noting that the structural analysis is inconclusive. The final score will then rely primarily on Signal 1.

---

## 6. Stretch Features Plan

To be implemented after the core system is stable and all required features are verified. Each stretch feature must have its own planning update in this file before implementation begins.

### Stretch Feature 1: Ensemble Detection (3rd Signal — N-gram Frequency Analysis)

**What it adds:** A third signal that checks for overuse of known AI-favored vocabulary: words and phrases like *"delve," "tapestry," "testament," "it's worth noting," "in the realm of."* This is fast, fully local, and serves as a lexical fingerprint check that is orthogonal to both the semantic and structural signals.

**Lexicon:** A hardcoded Python list of ~40 high-frequency AI marker words/phrases, versioned in the codebase so it can be updated as language trends shift.

**Scoring function:** For each marker found in the text, add its weight to a running total. Normalize by text length (per 100 words) and then sigmoid-compress to `[0.0, 1.0]`. This prevents very long texts from artificially inflating the score.

```python
raw = sum(weight for marker, weight in LEXICON if marker in text_lower) / (word_count / 100)
signal_3 = 1 / (1 + exp(-raw + 2))   # sigmoid centered at density=2
```

**How it integrates:** The n-gram score becomes a third input to the confidence synthesizer. Weights are updated to:
```
base_score = (signal_1 * 0.45) + (signal_2 * 0.35) + (signal_3 * 0.20)
```
Signal 3 carries the least weight because vocabulary trends are culturally shifting — the "AI word" list of 2026 may be mainstream by 2029.

**New API response field:** The `signals` object in the JSON response gains a `ngram_score` field alongside `semantic_score` and `structural_score`.

**Verification:** Submit a text densely packed with AI-marker words. Confirm `signal_3 ≥ 0.70`. Submit a text with zero markers. Confirm `signal_3 ≤ 0.15`.

---

### Stretch Feature 2: Provenance Certificate ("Verified Human" Credential)

**What it is:** A creator can earn a `VERIFIED_HUMAN` badge on a specific submission by completing a supplementary verification step. The badge is stored in the database and returned in all future reads of that submission.

**Verification step — Challenge Response:** When a creator requests a certificate for a submission, the system returns a short, specific question about the content that only the author would be able to answer (generated by a second Groq call on the submitted text). The creator must answer it in a follow-up request. This is not cryptographic proof — it is a lightweight signal of authorial familiarity that raises the bar for misuse.

**Endpoints:**

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/v1/certificate/request` | Initiates challenge; returns a `challenge_id` and a question |
| `POST` | `/api/v1/certificate/verify` | Submits the creator's answer; issues the certificate on pass |
| `GET`  | `/api/v1/submission/{id}`    | Returns `"provenance_certificate": true/false` in the response |

**Challenge generation prompt (Groq):**
> "Read this text. Generate one specific factual question about a detail, word choice, or structural decision that only the original author would know. Return only the question, no preamble."

**Answer evaluation:** The creator's answer is sent back to Groq alongside the original text for judgment:
> "Given this text and this question: [Q], does this answer [A] demonstrate familiarity with the source material? Reply with only YES or NO."

**Database changes:**

New `certificates` table:

| Column | Type | Notes |
|---|---|---|
| `cert_id` | TEXT (UUID) | Primary key |
| `submission_id` | TEXT | Foreign key → submissions |
| `creator_id` | TEXT | Must match original submitter |
| `challenge_id` | TEXT (UUID) | Links request to verify step |
| `challenge_question` | TEXT | Groq-generated question |
| `creator_answer` | TEXT | Creator's submitted answer |
| `status` | TEXT | `PENDING`, `ISSUED`, `FAILED` |
| `issued_at` | TEXT | ISO 8601, null until issued |

**Display:** When `provenance_certificate = true`, the transparency label gains a suffix line:
> "Provenance Verified: The creator has completed an authorship challenge for this work."

**Audit log:** Both the `CERTIFICATE_REQUESTED` and `CERTIFICATE_ISSUED` (or `CERTIFICATE_FAILED`) events are written to the `audit_log` table.

**Verification:** Submit text, request a certificate, answer the challenge correctly → confirm `status = ISSUED` in DB and `provenance_certificate: true` in submission response. Answer incorrectly → confirm `status = FAILED` and no certificate on the submission.

---

### Stretch Feature 3: Analytics Dashboard (`GET /api/v1/metrics`)

**What it returns:**

```json
{
  "total_submissions": 142,
  "label_distribution": {
    "likely_human": 68,
    "uncertain": 51,
    "likely_ai": 23
  },
  "appeal_rate_pct": 5.6,
  "under_review_count": 11,
  "under_review_rate_pct": 7.7,
  "certificate_issuance_rate_pct": 12.0
}
```

**Metrics and their meaning:**

| Metric | Why it matters |
|---|---|
| `label_distribution` | Shows whether the system is over-indexing on any verdict — a healthy system should not skew heavily toward one bucket |
| `appeal_rate_pct` | Measures creator trust: high appeal rate signals the thresholds need recalibration |
| `under_review_rate_pct` | Proxy for false-positive rate — if it spikes, human review burden is rising |
| `certificate_issuance_rate_pct` | Third metric of choice: shows adoption of the Provenance Certificate feature relative to total submissions |

**Implementation:** All values are computed with a single SQL query over the `submissions` and `certificates` tables. No caching needed at this scale.

**Rate limiting:** `GET /api/v1/metrics` — 30 requests per minute (read-only, admin use).

**Verification:** Seed the DB with known data (e.g., 10 submissions: 4 human, 3 uncertain, 3 AI; 1 appeal). Confirm the endpoint returns the exact expected counts.

---

### Stretch Feature 4: Multi-Modal Support (Image Description Analysis)

**What it adds:** The pipeline is extended to accept a second content type — a structured image description (alt-text or caption metadata) — in addition to plain text. This is distinct from analyzing the image itself; we analyze the *text description* of the image.

**Why image descriptions:** AI-generated images are increasingly accompanied by AI-generated alt-text or captions. These descriptions share the same stylometric and semantic fingerprints as other AI text, but they are structurally shorter and more formulaic, which means our existing thresholds need content-type awareness.

**New endpoint:**

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/v1/analyze/image-description` | Accepts `image_url` (optional), `description` (required), `creator_id` |

**Request body:**

```json
{
  "creator_id": "user_abc",
  "image_url": "https://example.com/image.jpg",
  "description": "A serene landscape featuring rolling hills bathed in golden light, with a solitary oak tree standing sentinel on the horizon."
}
```

**Pipeline adjustments for image descriptions:**

- **Short text handling:** Image descriptions are frequently under 50 words. Signal 2 (Structural Evaluator) is always set to neutral (`0.5`) for this content type — there is not enough text for variance to be meaningful.
- **Signal 1 prompt adjustment:** The Groq prompt is updated with content-type context: *"This is an image description/alt-text. Evaluate whether it was written by a human author or generated by an AI image captioning system."*
- **Signal 3 (if enabled):** Uses a separate, shorter lexicon tuned for AI image-description patterns (e.g., *"bathed in," "sentinel," "serene," "golden hues," "ethereal"*).
- **Thresholds unchanged:** The same `0.49 / 0.84` thresholds apply. The "Uncertain" label is expected to fire more often for short descriptions, which is acceptable — we surface the uncertainty rather than forcing a verdict.

**`content_type` field:** Both the `submissions` table and the API response gain a `content_type` field: `"text"` or `"image_description"`. The audit log records this alongside each decision.

**Verification:** Submit a known AI-generated image caption (e.g., from DALL-E's auto-caption). Confirm a valid response is returned with `content_type: "image_description"`. Submit a manually written photo caption. Confirm the score reflects the adjusted pipeline (Signal 2 neutral).

---

## 7. Rate Limiting

### Limits Chosen

| Endpoint         | Limit           | Window   |
|---|---|---|
| `POST /analyze`  | 10 requests     | 1 minute |
| `POST /appeal`   | 3 requests      | 1 hour   |
| `GET /log`       | 30 requests     | 1 minute |

### Reasoning

- **`/analyze` at 10/min:** This is the expensive endpoint (Groq API call + DB write). 10 requests per minute is generous for a single human user testing the system and prevents runaway automated scraping. If the Groq free tier has its own rate limits (~30 RPM for llama-3.3-70b), our limit keeps us comfortably below that ceiling.
- **`/appeal` at 3/hour:** An appeal is a deliberate, high-intent action. A rate limit here prevents abuse (e.g., flooding the appeal queue to suppress AI labels) and ensures each appeal is meaningful.
- **`/log` at 30/min:** Read-only, no external API calls. A generous limit for admin/debugging use.

---

## 8. Architecture

### Components

| Component | Tool | Role |
|---|---|---|
| API Framework | Flask | Routing, request handling |
| Rate Limiting | Flask-Limiter | Per-endpoint request throttling |
| Signal 1 | Groq API (`llama-3.3-70b-versatile`) | Semantic/phrasing evaluation |
| Signal 2 | Pure Python Stylometrics | Structural/variance evaluation |
| Signal 3 (stretch) | Python N-gram analysis | Lexical fingerprint check |
| Persistence | SQLite | Submissions + Audit log |
| Config | `.env` | API keys, secrets |

### Database Schema

**`submissions` table**

| Column | Type | Notes |
|---|---|---|
| `submission_id` | TEXT (UUID) | Primary key |
| `creator_id` | TEXT | Provided by caller |
| `content` | TEXT | Full submitted text |
| `signal_1_score` | REAL | Groq semantic score |
| `signal_2_score` | REAL | Stylometric score |
| `final_score` | REAL | Synthesized score |
| `label` | TEXT | Transparency label text |
| `status` | TEXT | `PENDING`, `DECIDED`, `UNDER_REVIEW` |
| `submitted_at` | TEXT | ISO 8601 timestamp |

**`audit_log` table**

| Column | Type | Notes |
|---|---|---|
| `log_id` | TEXT (UUID) | Primary key |
| `submission_id` | TEXT | Foreign key → submissions |
| `event_type` | TEXT | `SUBMISSION_ANALYZED`, `APPEAL_SUBMITTED` |
| `payload` | TEXT | JSON blob with event-specific fields |
| `timestamp` | TEXT | ISO 8601 timestamp |

### Flow Diagrams

**Submission Flow:**

```
[Client] ──(POST /analyze)──> [Flask API + Limiter]
                                        │
                              (Validate payload)
                                        │
                              (Split text to both signals)
                                ┌───────┴───────┐
                                ▼               ▼
                         [Groq LLM]   [Python Stylometrics]
                         (signal_1)      (signal_2)
                                └───────┬───────┘
                                        ▼
                             [Confidence Synthesizer]
                             (weighted avg + veto logic)
                                        │
                                        ▼
                             [Label Generator]
                             (float → label text)
                                        │
                           ┌────────────┴────────────┐
                           ▼                         ▼
                    [SQLite Write]           [JSON Response]
                  (submissions +          → client (score,
                   audit_log)               label, signals)
```

**Appeal Flow:**

```
[Creator] ──(POST /appeal/{submission_id})──> [Flask API]
                                                    │
                                      [Validate creator_id owns submission]
                                                    │
                                      [Update submissions: status=UNDER_REVIEW]
                                                    │
                                      [Write to audit_log: APPEAL_SUBMITTED]
                                                    │
[Creator] <──(202 Accepted + confirmation)──────────┘
```

### Architecture Narrative

During a submission, the text flows through the Flask API (guarded by rate limits) and is analyzed simultaneously by the semantic (Groq) and structural (Python) signals. The raw outputs are fed to the Confidence Synthesizer, which applies the weighted average and the Human Bias Veto. The resulting score is mapped to a transparency label, the full record is written to SQLite, and the response is returned to the caller.

During an appeal, the system validates that the appealing `creator_id` matches the original submission, sets the submission's status to `UNDER_REVIEW`, logs the creator's reasoning to the audit trail, and returns a `202 Accepted`. No automated re-classification occurs — the appeal enters a human review queue.

---

## 9. Milestone Checklist

| Milestone | Deliverable | Verification |
|---|---|---|
| M1 | Repo + `planning.md` | This document |
| M2 | `requirements.txt`, `.env.example`, DB init script | `python init_db.py` runs without error |
| M3 | `POST /analyze` endpoint + Signal 1 (Groq) | Submit ChatGPT text via Postman, get valid float |
| M4 | Signal 2 (Stylometrics) + Confidence Synthesizer | High-variance human text scores ≤ 0.49; structured AI text scores ≥ 0.85 |
| M5 | Label Generator + `POST /appeal` + Audit Log | All three label texts verified; DB shows `UNDER_REVIEW` after appeal; audit log has ≥ 3 entries |
| M6 | Rate limiting live + README finalized | 11th request to `/analyze` within 1 min returns `429` |
| S1 (stretch) | Signal 3 (N-gram) + updated synthesizer weights | N-gram score logged alongside signals 1 & 2 |
| S2 (stretch) | `GET /api/v1/metrics` | Returns correct counts from live DB |

---

## 10. AI Tool Usage Plan

This section documents exactly how AI assistance will be used during implementation, in accordance with project requirements.

### Milestone 3 — Submission Endpoint + Signal 1

- **Context provided to AI:** Architecture section (diagrams + narrative) + Signal 1 specification (Groq prompt design, expected output format).
- **Request:** Flask skeleton with `Flask-Limiter` on `POST /analyze`; the Python function to call Groq and extract a float score from the response JSON.
- **Verification:** Run Flask locally, submit a known ChatGPT paragraph, confirm a float in `[0.0, 1.0]` is returned within the Groq timeout.

### Milestone 4 — Signal 2 + Confidence Synthesizer

- **Context provided to AI:** Signal 2 specification (burstiness formula, lexical diversity formula) + Uncertainty Representation thresholds + existing M3 code.
- **Request:** Pure Python functions for `burstiness()` and `lexical_diversity()`; the `synthesize()` function implementing the weighted average and Human Bias Veto.
- **Verification:** Feed an erratic journal entry and a GPT-4 essay. Journal entry must score ≤ 0.49; essay must score ≥ 0.75.

### Milestone 5 — Production Layer

- **Context provided to AI:** Transparency Label Design (exact strings) + Appeals Workflow + DB schema + Architecture diagrams.
- **Request:** Label mapping function (float → label string); SQLite `init_db.py` script; audit logging functions; `POST /appeal` endpoint.
- **Verification:** Assert all three score thresholds return the exact label text specified in Section 3. Hit `/appeal`, open the `.db` file in DB Browser, confirm `status = UNDER_REVIEW` and a new `audit_log` row with `event_type = APPEAL_SUBMITTED`.
