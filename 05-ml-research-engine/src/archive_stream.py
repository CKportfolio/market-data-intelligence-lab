from __future__ import annotations
import io
import json
import tarfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator


@dataclass(frozen=True)
class ArchiveInfo:
    path: Path
    start_ms: int
    end_ms: int
    rows: int | None
    batch_id: str | None
    schema: str | None
    member_count: int
    format: str = "collector_v1"


def _json_member_names(tf: tarfile.TarFile) -> list[str]:
    return [m.name for m in tf.getmembers() if m.isfile() and m.name.lower().endswith('.jsonl')]


def read_manifest(path: Path) -> dict:
    """Read only manifest.json from tar.gz. Nothing is extracted to disk."""
    with tarfile.open(path, 'r:gz') as tf:
        members = tf.getmembers()
        m = next((x for x in members if x.isfile() and x.name.lower().endswith('manifest.json')), None)
        if not m:
            raise RuntimeError(f"Brak manifest.json w {path}")
        f = tf.extractfile(m)
        if f is None:
            raise RuntimeError(f"Nie moge odczytac manifest.json w {path}")
        return json.loads(f.read().decode('utf-8'))


def inspect_archive(path: Path) -> ArchiveInfo:
    manifest = read_manifest(path)
    start = manifest.get('startMs', manifest.get('start_ms'))
    end = manifest.get('endMs', manifest.get('end_ms'))
    rows = manifest.get('rows', manifest.get('record_count'))
    schema = manifest.get('schema')
    batch_id = manifest.get('batchId', manifest.get('batch_id'))
    with tarfile.open(path, 'r:gz') as tf:
        names = _json_member_names(tf)
    fmt = 'collector_v1' if any(n.endswith('market.jsonl') for n in names) else ('dense_truth_v1' if any(Path(n).name.startswith('records_') for n in names) else 'unknown')
    if start is None or end is None:
        # Dense truth recorder manifest uses ISO/range metadata in some builds; if missing,
        # infer cheaply from first/last row only when necessary.
        first, last = None, None
        for row in iter_archive_rows(path, allow_dense=True):
            ts = record_timestamp(row)
            if ts is None:
                continue
            first = ts if first is None else min(first, ts)
            last = ts if last is None else max(last, ts)
        start, end = first, last
    if start is None or end is None:
        raise RuntimeError(f"Nie udalo sie ustalic czasu archiwum {path}")
    return ArchiveInfo(path=path.resolve(), start_ms=int(start), end_ms=int(end), rows=int(rows) if rows is not None else None,
                       batch_id=str(batch_id) if batch_id else None, schema=str(schema) if schema else None,
                       member_count=len(names), format=fmt)


def discover_archives(base: Path) -> list[ArchiveInfo]:
    base = base.expanduser().resolve()
    if base.is_file() and base.name.endswith('.tar.gz'):
        paths = [base]
    else:
        candidates = []
        for p in [base, base / 'archives', base / 'raw' / 'archives', base / 'data' / 'raw' / 'archives']:
            if p.is_dir():
                candidates.extend(p.glob('*.tar.gz'))
        # fallback recursive, but keep it bounded to tar.gz only
        if not candidates and base.is_dir():
            candidates = list(base.rglob('*.tar.gz'))
        paths = sorted(set(x.resolve() for x in candidates))
    if not paths:
        return []
    infos = [inspect_archive(p) for p in paths]
    infos.sort(key=lambda x: (x.start_ms, x.end_ms, str(x.path)))
    return infos


def record_timestamp(row: dict) -> int | None:
    for k in ('tsRecordMs','tsTradeMs','tsSampleMs','tsLiquidationMs','exch_ts_ms','recv_wall_ms','tsExchangeMs','tsMatchMs'):
        v = row.get(k)
        try:
            v = int(v)
            if v > 0:
                return v
        except (TypeError, ValueError):
            pass
    return None


def _normalize_dense(row: dict) -> dict | None:
    kind = row.get('kind')
    category = row.get('category')
    ts = record_timestamp(row)
    if ts is None:
        return None
    if kind == 'trade':
        return {
            '_channel': 'spot_trades' if category == 'spot' else 'perp_trades',
            'schema': 'trade-v1', 'market': 'spot' if category == 'spot' else 'linear',
            'symbol': row.get('symbol'), 'tsTradeMs': ts, 'tsRecordMs': ts,
            'side': row.get('side'), 'price': float(row.get('price') or 0), 'size': float(row.get('size') or 0),
            'tradeId': row.get('trade_id'), 'seq': row.get('seq'),
        }
    if kind in ('ob_snapshot','orderbook_snapshot'):
        bids = [[float(p), float(q)] for p,q in (row.get('bids') or [])]
        asks = [[float(p), float(q)] for p,q in (row.get('asks') or [])]
        if not bids or not asks:
            return None
        bb, ba = bids[0][0], asks[0][0]
        mid = (bb+ba)/2
        return {
            '_channel': 'spot_orderbook' if category == 'spot' else 'perp_orderbook',
            'schema': 'orderbook-snapshot-v1', 'market': 'spot' if category == 'spot' else 'linear',
            'symbol': row.get('symbol'), 'tsSampleMs': ts, 'tsRecordMs': ts,
            'bestBid': bb, 'bestAsk': ba, 'mid': mid, 'spreadBps': (ba-bb)/mid*10000 if mid else None,
            'bids': bids[:50], 'asks': asks[:50], 'updateId': row.get('update_id'), 'seq': row.get('seq'),
        }
    if kind == 'liquidation':
        return {
            '_channel': 'liquidations', 'tsLiquidationMs': ts, 'tsRecordMs': ts,
            'market': 'linear', 'symbol': row.get('symbol'), 'positionSide': row.get('side'),
            'size': float(row.get('size') or 0), 'bankruptcyPrice': float(row.get('price') or 0),
        }
    # Dense OB deltas are intentionally not reconstructed here. Dense truth archives are for
    # adaptive-sampling research. ML research should normally consume collector archives with L50 snapshots.
    return None


def iter_archive_rows(path: Path, allow_dense: bool = False) -> Iterator[dict]:
    """Stream jsonl members straight from .tar.gz; no extraction and no giant merge file."""
    # r|gz keeps tar processing streaming. Members are consumed sequentially.
    with tarfile.open(path, 'r|gz') as tf:
        for member in tf:
            if not member.isfile() or not member.name.lower().endswith('.jsonl'):
                continue
            base = Path(member.name).name
            is_market = base == 'market.jsonl'
            is_dense = base.startswith('records_')
            if not is_market and not (allow_dense and is_dense):
                continue
            f = tf.extractfile(member)
            if f is None:
                continue
            for raw in f:
                line = raw.decode('utf-8', errors='replace') if isinstance(raw, (bytes, bytearray)) else str(raw)
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if is_dense:
                    row = _normalize_dense(row)
                    if row is None:
                        continue
                yield row


def validate_archive_sequence(infos: list[ArchiveInfo]) -> dict:
    gaps, overlaps = [], []
    for a,b in zip(infos, infos[1:]):
        delta = b.start_ms - a.end_ms
        if delta > 5000:
            gaps.append({'after': str(a.path), 'before': str(b.path), 'gap_ms': int(delta)})
        elif delta < -5000:
            overlaps.append({'a': str(a.path), 'b': str(b.path), 'overlap_ms': int(-delta)})
    return {'archives': len(infos), 'gaps': gaps, 'overlaps': overlaps,
            'start_ms': infos[0].start_ms if infos else None, 'end_ms': infos[-1].end_ms if infos else None}

def detect_symbols(infos: list[ArchiveInfo], max_rows: int=20000) -> tuple[str|None,str|None]:
    spot=perp=None; seen=0
    for info in infos[:3]:
        if info.format!='collector_v1': continue
        for row in iter_archive_rows(info.path):
            seen+=1
            ch=row.get('_channel'); sym=row.get('symbol')
            if sym and ch in ('spot_trades','spot_orderbook','spot_orderbook_events'): spot=str(sym)
            if sym and ch in ('perp_trades','perp_orderbook','perp_orderbook_events','liquidations'): perp=str(sym)
            if spot and perp: return spot,perp
            if seen>=max_rows: return spot,perp
    return spot,perp
