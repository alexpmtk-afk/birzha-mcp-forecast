"""Immutable observation reports in a separate SQLite journal."""
import hashlib
import json
import sqlite3
from pathlib import Path
from birzha.application.level_reaction import canonical_bytes, build_level_reaction_report


class ReactionCollisionError(ValueError):
    pass


class LevelReactionJournal:
    def __init__(self, path: str | Path):
        self.connection = sqlite3.connect(str(path))
        self.connection.execute("CREATE TABLE IF NOT EXISTS reaction_reports (report_id TEXT PRIMARY KEY, payload BLOB NOT NULL, sha256 TEXT NOT NULL)")
        self.connection.execute("CREATE TABLE IF NOT EXISTS reaction_forecast_anchors (forecast_id TEXT PRIMARY KEY, sha256 TEXT NOT NULL)")
        self.connection.commit()

    def observe(self, forecast_payload, series, *, observation_at):
        report = build_level_reaction_report(forecast_payload,series,observation_at=observation_at)
        payload = canonical_bytes(report)
        digest = hashlib.sha256(payload).hexdigest()
        with self.connection:
            self.connection.execute("INSERT OR IGNORE INTO reaction_forecast_anchors VALUES (?,?)",(report["forecast_id"],report["forecast_sha256"]))
            anchor = self.connection.execute("SELECT sha256 FROM reaction_forecast_anchors WHERE forecast_id=?",(report["forecast_id"],)).fetchone()
            if anchor != (report["forecast_sha256"],):
                raise ReactionCollisionError("frozen forecast identity has conflicting source content")
            self.connection.execute("INSERT OR IGNORE INTO reaction_reports VALUES (?,?,?)",(report["report_id"],payload,digest))
            stored = self.connection.execute("SELECT payload,sha256 FROM reaction_reports WHERE report_id=?",(report["report_id"],)).fetchone()
            if stored != (payload,digest):
                raise ReactionCollisionError("immutable reaction identity has conflicting content")
        return report

    def get(self, report_id):
        row = self.connection.execute("SELECT payload,sha256 FROM reaction_reports WHERE report_id=?",(report_id,)).fetchone()
        if row is None:
            return None
        payload,digest = row
        if hashlib.sha256(payload).hexdigest() != digest:
            raise ReactionCollisionError("stored reaction content hash is invalid")
        report = json.loads(payload)
        body = {k:v for k,v in report.items() if k != "report_id"}
        expected = "reaction_" + hashlib.sha256(canonical_bytes(body)).hexdigest()
        if report.get("report_id") != report_id or report_id != expected:
            raise ReactionCollisionError("stored reaction identity is invalid")
        anchor = self.connection.execute("SELECT sha256 FROM reaction_forecast_anchors WHERE forecast_id=?",(report["forecast_id"],)).fetchone()
        if anchor != (report.get("forecast_sha256"),):
            raise ReactionCollisionError("stored forecast anchor is inconsistent")
        return report

    def close(self):
        self.connection.close()
