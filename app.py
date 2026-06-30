import uuid
import sqlite3
from datetime import datetime, timezone
from flask import Flask, request, jsonify
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from signals import analyze_with_groq, analyze_stylometrics, calculate_final_confidence

DB_PATH = "provenance.db"

app = Flask(__name__)

limiter = Limiter(
    key_func=get_remote_address,
    app=app,
    default_limits=[],
    storage_uri="memory://",
)

LABELS = {
    "human": "Authentic Work: Our systems indicate this content features the natural variance and structure of human creativity.",
    "uncertain": "Mixed Signals: This work contains structural patterns common to both human writing and AI assistance. We prioritize creator trust and assume human authorship.",
    "ai": "AI-Generated: Strong multi-signal indicators suggest this work was primarily generated using AI tools.",
}


def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("DROP TABLE IF EXISTS audit_log")
        conn.execute("""
            CREATE TABLE audit_log (
                content_id  TEXT PRIMARY KEY,
                creator_id  TEXT NOT NULL,
                timestamp   TEXT NOT NULL,
                attribution TEXT NOT NULL,
                confidence  REAL NOT NULL,
                llm_score   REAL NOT NULL,
                stylo_score REAL NOT NULL,
                status      TEXT NOT NULL
            )
        """)
        conn.commit()


def log_decision(content_id, creator_id, attribution, confidence, llm_score, stylo_score):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            INSERT INTO audit_log
                (content_id, creator_id, timestamp, attribution, confidence, llm_score, stylo_score, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                content_id,
                creator_id,
                datetime.now(timezone.utc).isoformat(),
                attribution,
                confidence,
                llm_score,
                stylo_score,
                "classified",
            ),
        )
        conn.commit()


def score_to_result(score: float) -> tuple[str, str]:
    if score < 0.50:
        return "human", LABELS["human"]
    elif score < 0.85:
        return "uncertain", LABELS["uncertain"]
    else:
        return "ai", LABELS["ai"]


@app.route("/submit", methods=["POST"])
@limiter.limit("10 per minute")
def submit():
    body = request.get_json(silent=True)
    if not body or "text" not in body or "creator_id" not in body:
        return jsonify({"error": "Request body must include 'text' and 'creator_id'"}), 400

    text = body["text"]
    creator_id = body["creator_id"]
    content_id = str(uuid.uuid4())

    signal_1 = analyze_with_groq(text)
    signal_2 = analyze_stylometrics(text)
    final_score = calculate_final_confidence(signal_1, signal_2)
    attribution_result, label = score_to_result(final_score)

    log_decision(
        content_id=content_id,
        creator_id=creator_id,
        attribution=attribution_result,
        confidence=final_score,
        llm_score=signal_1,
        stylo_score=signal_2,
    )

    return jsonify({
        "content_id": content_id,
        "attribution_result": attribution_result,
        "confidence_score": final_score,
        "label": label,
        "signals": {
            "semantic_score": signal_1,
            "structural_score": signal_2,
        },
    }), 200


@app.route("/log", methods=["GET"])
@limiter.limit("30 per minute")
def get_log():
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM audit_log ORDER BY timestamp DESC LIMIT 10"
        ).fetchall()
    return jsonify({"entries": [dict(row) for row in rows]}), 200


if __name__ == "__main__":
    init_db()
    app.run(port=5001, debug=True)
