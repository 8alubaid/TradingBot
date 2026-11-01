"""Local SQLite persistence + optional GCS mirroring for trades and lessons-learned files."""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core.config import settings
from core.models import TradeRecord

logger = logging.getLogger("storage")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
    id TEXT PRIMARY KEY,
    timestamp TEXT NOT NULL,
    symbol TEXT NOT NULL,
    signal_action TEXT NOT NULL,
    decision_action TEXT NOT NULL,
    qty REAL,
    notional_usd REAL,
    price REAL,
    order_id TEXT,
    status TEXT NOT NULL,
    rationale TEXT,
    pnl_usd REAL,
    closed_at TEXT
);
CREATE TABLE IF NOT EXISTS indicator_state (
    symbol TEXT PRIMARY KEY,
    regime TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


def _db_path() -> Path:
    path = Path(settings.database_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path())
    conn.executescript(_SCHEMA)
    return conn


def save_trade(record: TradeRecord) -> None:
    conn = _connect()
    try:
        conn.execute(
            """INSERT OR REPLACE INTO trades
               (id, timestamp, symbol, signal_action, decision_action, qty, notional_usd,
                price, order_id, status, rationale, pnl_usd, closed_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                record.id,
                record.timestamp.isoformat(),
                record.symbol,
                record.signal_action.value,
                record.decision_action.value,
                record.qty,
                record.notional_usd,
                record.price,
                record.order_id,
                record.status.value,
                record.rationale,
                record.pnl_usd,
                record.closed_at.isoformat() if record.closed_at else None,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    _mirror_trade_to_gcs(record)


def get_recent_trades(
    limit: int = 50,
    symbol: str | None = None,
    status: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> list[dict]:
    """date_from/date_to are 'YYYY-MM-DD' (UTC), inclusive."""
    clauses = []
    params: list = []
    if symbol:
        clauses.append("symbol = ?")
        params.append(symbol)
    if status:
        clauses.append("status = ?")
        params.append(status)
    if date_from:
        clauses.append("substr(timestamp, 1, 10) >= ?")
        params.append(date_from)
    if date_to:
        clauses.append("substr(timestamp, 1, 10) <= ?")
        params.append(date_to)

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(limit)

    conn = _connect()
    try:
        cur = conn.execute(f"SELECT * FROM trades {where} ORDER BY timestamp DESC LIMIT ?", params)
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    finally:
        conn.close()


def get_trade(trade_id: str) -> dict | None:
    conn = _connect()
    try:
        cur = conn.execute("SELECT * FROM trades WHERE id = ?", (trade_id,))
        cols = [c[0] for c in cur.description]
        row = cur.fetchone()
        return dict(zip(cols, row)) if row else None
    finally:
        conn.close()


def get_indicator_regime(symbol: str) -> str | None:
    """Last-known trend regime ('bull'/'bear') for a symbol, persisted across
    restarts so the autonomous scanner (core/scanner.py) doesn't mistake a
    fresh cold start for a fresh cross."""
    conn = _connect()
    try:
        cur = conn.execute("SELECT regime FROM indicator_state WHERE symbol = ?", (symbol,))
        row = cur.fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def set_indicator_regime(symbol: str, regime: str) -> None:
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO indicator_state (symbol, regime, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(symbol) DO UPDATE SET regime = excluded.regime, updated_at = excluded.updated_at",
            (symbol, regime, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
    finally:
        conn.close()


def get_todays_realized_pnl() -> float:
    """Sum of pnl_usd for positions closed today (UTC). Used by the risk manager's
    daily loss kill-switch. Relies on pipeline.py recording an approximate pnl_usd
    (the position's unrealized P/L at the moment of close) -- see README known
    limitations for why this is an approximation, not exact fill-level accounting.
    """
    conn = _connect()
    try:
        today = datetime.now(timezone.utc).date().isoformat()
        cur = conn.execute(
            "SELECT COALESCE(SUM(pnl_usd), 0) FROM trades "
            "WHERE status = 'closed' AND substr(closed_at, 1, 10) = ?",
            (today,),
        )
        return float(cur.fetchone()[0] or 0.0)
    finally:
        conn.close()


def get_health_stats(hours: int = 24) -> dict:
    """Decision outcome breakdown over a trailing window -- feeds the dashboard's
    system health panel and is the kind of thing you'd otherwise build a Cloud
    Monitoring custom metric for."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    conn = _connect()
    try:
        cur = conn.execute(
            "SELECT status, COUNT(*) FROM trades WHERE timestamp >= ? GROUP BY status", (cutoff,)
        )
        counts = dict(cur.fetchall())
    finally:
        conn.close()

    total = sum(counts.values())

    def pct(n: int) -> float:
        return round(100 * n / total, 1) if total else 0.0

    executed = counts.get("filled", 0) + counts.get("closed", 0)
    return {
        "hours": hours,
        "total": total,
        "counts": counts,
        "executed_pct": pct(executed),
        "held_pct": pct(counts.get("held", 0)),
        "rejected_pct": pct(counts.get("rejected", 0)),
        "error_pct": pct(counts.get("error", 0)),
    }


def get_performance_summary() -> dict:
    conn = _connect()
    try:
        closed_count, total_pnl, wins = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(pnl_usd), 0), "
            "COALESCE(SUM(CASE WHEN pnl_usd >= 0 THEN 1 ELSE 0 END), 0) "
            "FROM trades WHERE status = 'closed'"
        ).fetchone()

        today = datetime.now(timezone.utc).date().isoformat()
        trades_today = conn.execute(
            "SELECT COUNT(*) FROM trades WHERE substr(timestamp, 1, 10) = ?", (today,)
        ).fetchone()[0]
        trades_all_time = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
    finally:
        conn.close()

    return {
        "total_realized_pnl": float(total_pnl or 0.0),
        "closed_count": closed_count or 0,
        "win_rate_pct": round(100 * wins / closed_count, 1) if closed_count else None,
        "trades_today": trades_today,
        "trades_all_time": trades_all_time,
    }


def save_lesson(record_id: str, markdown: str) -> Path:
    lessons_dir = Path(settings.local_lessons_dir)
    lessons_dir.mkdir(parents=True, exist_ok=True)
    path = lessons_dir / f"{record_id}.md"
    path.write_text(markdown, encoding="utf-8")
    _mirror_lesson_to_gcs(path)
    return path


def recent_lessons_summary(limit: int = 5, snippet_chars: int = 400) -> str:
    lessons_dir = Path(settings.local_lessons_dir)
    if not lessons_dir.exists():
        return ""
    files = sorted(lessons_dir.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]
    chunks = []
    for f in files:
        text = f.read_text(encoding="utf-8").strip()
        chunks.append(f"--- {f.stem} ---\n{text[:snippet_chars]}")
    return "\n\n".join(chunks)


def get_lesson_markdown(trade_id: str) -> str | None:
    path = Path(settings.local_lessons_dir) / f"{trade_id}.md"
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8")


def parse_frontmatter(text: str) -> dict[str, str]:
    """Our lesson files' frontmatter is always simple flat key: value pairs --
    no need for a full YAML parser."""
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    meta = {}
    for line in text[3:end].strip().splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            meta[k.strip()] = v.strip()
    return meta


def strip_frontmatter(text: str) -> str:
    if not text.startswith("---"):
        return text
    end = text.find("\n---", 3)
    return text[end + 4 :].lstrip("\n") if end != -1 else text


def list_all_lessons() -> list[dict]:
    """Every lesson journal entry, independent of the trades table -- for the
    standalone lessons archive page."""
    lessons_dir = Path(settings.local_lessons_dir)
    if not lessons_dir.exists():
        return []

    entries = []
    for f in sorted(lessons_dir.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True):
        text = f.read_text(encoding="utf-8")
        meta = parse_frontmatter(text)
        body = strip_frontmatter(text).strip().replace("\n", " ")
        entries.append(
            {
                "trade_id": f.stem,
                "date": meta.get("date", ""),
                "symbol": meta.get("symbol", ""),
                "decision": meta.get("decision", ""),
                "status": meta.get("status", ""),
                "snippet": body[:220],
            }
        )
    return entries


def _gcs_bucket():
    if not settings.gcs_bucket_name:
        return None
    try:
        from google.cloud import storage as gcs_storage

        client = gcs_storage.Client(project=settings.gcp_project_id or None)
        return client.bucket(settings.gcs_bucket_name)
    except Exception:
        logger.exception("Failed to initialize GCS client; continuing with local-only storage")
        return None


def _mirror_trade_to_gcs(record: TradeRecord) -> None:
    bucket = _gcs_bucket()
    if not bucket:
        return
    try:
        blob = bucket.blob(f"trades/{record.id}.json")
        blob.upload_from_string(record.model_dump_json(indent=2), content_type="application/json")
    except Exception:
        logger.exception("Failed to upload trade %s to GCS", record.id)


def _mirror_lesson_to_gcs(path: Path) -> None:
    bucket = _gcs_bucket()
    if not bucket:
        return
    try:
        blob = bucket.blob(f"lessons/{path.name}")
        blob.upload_from_filename(str(path))
    except Exception:
        logger.exception("Failed to upload lesson %s to GCS", path.name)
