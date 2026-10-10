"""Append-only holding facts, separate from immutable forecasts and reactions."""
import hashlib
import json
import sqlite3
from birzha.application.level_holding import build_level_holding_report
from birzha.application.level_reaction import canonical_bytes
from birzha.storage.level_reaction import ReactionCollisionError


class LevelHoldingJournal:
    def __init__(self,path):
        self.connection=sqlite3.connect(str(path))
        self.connection.execute("CREATE TABLE IF NOT EXISTS holding_forecast_anchors (forecast_id TEXT PRIMARY KEY, sha256 TEXT NOT NULL)")
        self.connection.execute("CREATE TABLE IF NOT EXISTS holding_reports (report_id TEXT PRIMARY KEY, payload BLOB NOT NULL, sha256 TEXT NOT NULL)")
        self.connection.commit()

    def observe(self,forecast_payload,series,*,observation_at,schedule_bytes=None):
        report=build_level_holding_report(forecast_payload,series,observation_at=observation_at,schedule_bytes=schedule_bytes)
        payload=canonical_bytes(report); digest=hashlib.sha256(payload).hexdigest()
        with self.connection:
            self.connection.execute("INSERT OR IGNORE INTO holding_forecast_anchors VALUES (?,?)",(report["forecast_id"],report["forecast_sha256"]))
            anchor=self.connection.execute("SELECT sha256 FROM holding_forecast_anchors WHERE forecast_id=?",(report["forecast_id"],)).fetchone()
            if anchor!=(report["forecast_sha256"],):
                raise ReactionCollisionError("frozen forecast content changed")
            self.connection.execute("INSERT OR IGNORE INTO holding_reports VALUES (?,?,?)",(report["report_id"],payload,digest))
            saved=self.connection.execute("SELECT payload,sha256 FROM holding_reports WHERE report_id=?",(report["report_id"],)).fetchone()
            if saved!=(payload,digest):
                raise ReactionCollisionError("holding report collision")
        return report

    def get(self,report_id):
        saved=self.connection.execute("SELECT payload,sha256 FROM holding_reports WHERE report_id=?",(report_id,)).fetchone()
        if saved is None:return None
        payload,digest=saved
        if hashlib.sha256(payload).hexdigest()!=digest:
            raise ReactionCollisionError("holding report hash invalid")
        report=json.loads(payload)
        body={k:v for k,v in report.items() if k!="report_id"}
        expected="holding_"+hashlib.sha256(canonical_bytes(body)).hexdigest()
        anchor=self.connection.execute("SELECT sha256 FROM holding_forecast_anchors WHERE forecast_id=?",(report["forecast_id"],)).fetchone()
        if report.get("report_id")!=report_id or report_id!=expected or anchor!=(report.get("forecast_sha256"),):
            raise ReactionCollisionError("holding report identity invalid")
        return report

    def close(self):self.connection.close()
