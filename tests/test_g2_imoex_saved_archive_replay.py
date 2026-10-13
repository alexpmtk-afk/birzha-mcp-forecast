import hashlib
import pytest

import scripts.g2_imoex_saved_archive_replay as script


def test_corrupted_archive_fails_before_duckdb_open(tmp_path, monkeypatch):
    data = tmp_path/"archive.duckdb"
    data.write_bytes(b"not an archive")
    monkeypatch.setattr(script.duckdb, "connect",
                        lambda *a, **k: pytest.fail("bad SHA reached DB"))
    with pytest.raises(ValueError, match="differs"):
        next(script._source(data, "0"*64))
    assert data.read_bytes() == b"not an archive"


def test_research_source_only_opens_read_only(tmp_path, monkeypatch):
    data = tmp_path/"archive.duckdb"
    data.write_bytes(b"fake")
    received=[]
    class FakeConnection:
        def execute(self, statement):
            assert statement.startswith("SELECT value FROM research_intraday_meta")
            return self
        def fetchone(self):
            return (script.ARCHIVE_MARKER,)
        def close(self):
            received.append("closed")
    def connector(filename, *, read_only=False):
        assert filename == str(data)
        assert read_only is True
        return FakeConnection()
    monkeypatch.setattr(script.duckdb,"connect",connector)
    g=script._source(data,hashlib.sha256(data.read_bytes()).hexdigest())
    cx=next(g)
    assert cx is not None
    with pytest.raises(StopIteration):
        next(g)
    assert received==["closed"]
    assert data.read_bytes()==b"fake"


def test_replay_cutoff_rejects_old_period_before_network():
    class NetworkForbidden:
        def resolve(self,*args,**kwargs):
            pytest.fail("forbidden date reached provider")
    for day in ("2024-12-31","2027-01-01","2025-04-01"):
        with pytest.raises(ValueError):
            script.frozen_replay(None,NetworkForbidden(),day,"0"*64)
