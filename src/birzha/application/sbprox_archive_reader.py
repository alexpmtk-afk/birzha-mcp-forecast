"""Bounded read-only access to staged SBProX research coverage and profiles."""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import timedelta
from decimal import Decimal

from birzha.application.sbprox_moex_comparison import moscow
from birzha.providers.sbprox_ri import SERIES


def profile_stats(volumes, fraction=Decimal("0.70")):
    if not volumes or min(volumes.values()) < 0 or sum(volumes.values()) <= 0:
        raise ValueError("positive profile required")
    if not 0 < fraction <= 1:
        raise ValueError("value area fraction must be in (0, 1]")
    prices = sorted(volumes)
    poc = max(prices, key=volumes.__getitem__)
    left = right = prices.index(poc)
    volume = volumes[poc]
    target = sum(volumes.values()) * fraction
    while volume < target:
        if left and (right == len(prices) - 1 or volumes[prices[left - 1]] >= volumes[prices[right + 1]]):
            left -= 1
            volume += volumes[prices[left]]
        else:
            right += 1
            volume += volumes[prices[right]]
    hvn, lvn = [], []
    for i, price in enumerate(prices[1:-1], 1):
        value, a, b = volumes[price], volumes[prices[i - 1]], volumes[prices[i + 1]]
        if value > max(a, b):
            hvn.append(price)
        if value < min(a, b):
            lvn.append(price)
    return dict(poc=poc, val=prices[left], vah=prices[right], hvn=hvn, lvn=lvn,
                rule="OBSERVED_LEVELS_70PCT_ADJACENCY_STRICT_EXTREMA_V1")


class SBProXResearchReader:
    """Adapter for a separately materialized generation. No MCP tools registered here."""

    def __init__(self, generation):
        from birzha.application.sbprox_archive import SCHEMA, _check_manifest, _load

        self.generation = generation.resolve(strict=True)
        self.manifest = _load(self.generation / "manifest.json")[0]
        if self.manifest["schema"] != SCHEMA:
            raise ValueError("unknown SBProX schema")
        _check_manifest(self.generation, self.manifest)

    @staticmethod
    def _window(secid, start, end):
        a, b = moscow(start), moscow(end)
        if (secid not in SERIES.values() or a.year != 2026 or b.year != 2026
                or not a < b or b - a > timedelta(days=31)):
            raise ValueError("exact RI SECID and a bounded 2026 window (up to 31 days) required")
        return a, b

    def _read(self, name, secid, start, end, fields):
        import duckdb

        paths = [str(self.generation / rel) for rel in self.manifest["checksums"]
                 if rel.startswith(f"NORMALIZED/{name}_{secid}_")
                 and start.strftime("%Y-%m") <= rel[-15:-8] <= end.strftime("%Y-%m")]
        if not paths:
            return []
        with duckdb.connect() as db:
            return db.execute(f"""SELECT {fields} FROM read_parquet(?)
                WHERE minute_start::TIMESTAMPTZ >= ? AND minute_start::TIMESTAMPTZ < ?
                ORDER BY minute_start""", [paths, start, end]).fetchall()

    def coverage(self, secid, start, end):
        a, b = self._window(secid, start, end)
        rows = self._read("M1_BARS", secid, a, b, "minute_start, volume, source_sha256, session_quality")
        return dict(secid=secid, provider="SBPROX", minutes=len(rows), volume=sum(r[1] for r in rows),
                    first=rows[0][0] if rows else None, last=rows[-1][0] if rows else None,
                    sources=sorted({r[2] for r in rows}), session_quality=dict(Counter(r[3] for r in rows)),
                    generation=self.manifest["generation"], quality="OBSERVED_RESEARCH_ONLY",
                    available_at="UNKNOWN")

    def features(self, secid, start, end, as_of, *, include_footprint=False,
                 tick_size=None, imbalance_ratio=None):
        a, b = self._window(secid, start, end)
        # End is exclusive. Require full scheduled minutes and never query a partially known minute.
        limit = min(b, moscow(as_of)).replace(second=0, microsecond=0)
        if limit <= a:
            return dict(secid=secid, quality="UNAVAILABLE", available_at="UNKNOWN", minutes=0)
        if (include_footprint or tick_size is not None or imbalance_ratio is not None) and limit - a > timedelta(days=1):
            raise ValueError("footprint and imbalance requests are limited to one day")
        bars = self._read("M1_BARS", secid, a, limit, "minute_start, open, close, volume, ask, bid, delta")
        levels = self._read("PRICE_LEVELS", secid, a, limit, "minute_start, price, volume, ask, bid, delta")
        if not bars:
            return dict(secid=secid, quality="UNAVAILABLE", available_at="UNKNOWN", minutes=0)
        by_price = defaultdict(lambda: [0, 0, 0, 0])
        footprint = defaultdict(dict)
        for minute, price, volume, ask, bid, delta in levels:
            sums = by_price[price]
            for i, x in enumerate((volume, ask, bid, delta)):
                sums[i] += x
            if tick_size is not None:
                footprint[minute][price] = (ask, bid)
        imbalances = []
        if tick_size is not None or imbalance_ratio is not None:
            if not isinstance(tick_size, int) or tick_size <= 0 or not imbalance_ratio or imbalance_ratio <= 1:
                raise ValueError("imbalance needs a confirmed positive tick and ratio greater than one")
            for minute, prices in footprint.items():
                for price, (ask, bid) in sorted(prices.items()):
                    below = prices.get(price - tick_size)
                    above = prices.get(price + tick_size)
                    if below and below[1] > 0 and ask >= imbalance_ratio * below[1]:
                        imbalances.append([minute, price, "ASK"])
                    if above and above[0] > 0 and bid >= imbalance_ratio * above[0]:
                        imbalances.append([minute, price, "BID"])
        profile = {p: v[0] for p, v in by_price.items()}
        return dict(
            **self.coverage(secid, a.isoformat(), limit.isoformat()),
            profile=profile_stats(profile) if sum(profile.values()) else None,
            volume_at_price={p: by_price[p] for p in sorted(by_price)},
            footprint=[dict(minute_start=m, price=p, volume=v, ask=a, bid=b, delta=d)
                       for m, p, v, a, b, d in sorted(levels)] if include_footprint else None,
            diagonal_imbalances=imbalances if tick_size is not None else None,
            observed_through=limit.isoformat(), ask=sum(r[4] for r in bars), bid=sum(r[5] for r in bars),
            observed_delta=sum(r[6] for r in bars), price_result=bars[-1][2] - bars[0][1],
            window_reset_cumulative_delta=sum(r[6] for r in bars),
        )

    def profile_migration(self, secid, previous_start, previous_end, start, end, as_of):
        """Compare two completed, non-overlapping research windows for the same contract."""
        prior = self._window(secid, previous_start, previous_end)
        current = self._window(secid, start, end)
        if prior[1] > current[0] or current[1] > moscow(as_of):
            raise ValueError("ordered, non-overlapping windows completed by as_of required")
        before = self.features(secid, previous_start, previous_end, as_of)
        after = self.features(secid, start, end, as_of)
        old, new = before.get("profile"), after.get("profile")
        return dict(secid=secid, available_at="UNKNOWN",
                    quality="OBSERVED_RESEARCH_ONLY" if old and new else "UNAVAILABLE",
                    shift={k: new[k] - old[k] for k in ("poc", "val", "vah")} if old and new else None)
