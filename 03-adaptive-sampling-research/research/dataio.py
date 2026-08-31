from __future__ import annotations
import csv, glob, json, tarfile
from pathlib import Path
from typing import Iterable, Iterator
import yaml


def load_yaml(path: str | Path):
    with open(path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)


def save_yaml(obj, path: str | Path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        yaml.safe_dump(obj, f, sort_keys=False, allow_unicode=True)


def archive_paths(pattern: str) -> list[str]:
    return sorted(glob.glob(pattern))


def archive_paths_from_list(list_path: str | Path) -> list[str]:
    p = Path(list_path)
    base = p.parent
    out: list[str] = []
    for raw in p.read_text(encoding='utf-8').splitlines():
        raw = raw.strip()
        if not raw or raw.startswith('#'):
            continue
        q = Path(raw)
        if not q.is_absolute():
            q = (base / q).resolve()
        out.append(str(q))
    return out


def stream_tar_jsonl(paths: Iterable[str]) -> Iterator[dict]:
    """Read recorder JSONL members directly from .tar.gz; nothing is extracted to disk."""
    for p in paths:
        with tarfile.open(p, mode='r|gz') as tf:
            for member in tf:
                if not member.isfile() or not member.name.endswith('.jsonl'):
                    continue
                fh = tf.extractfile(member)
                if fh is None:
                    continue
                for raw in fh:
                    raw = raw.strip()
                    if not raw:
                        continue
                    try:
                        yield json.loads(raw)
                    except json.JSONDecodeError:
                        continue


def write_csv(rows: Iterable[dict], path: str | Path, fieldnames: list[str] | None = None):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    it = iter(rows)
    try:
        first = next(it)
    except StopIteration:
        path.write_text('', encoding='utf-8')
        return
    names = fieldnames or list(first.keys())
    with path.open('w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=names, extrasaction='ignore')
        w.writeheader(); w.writerow(first)
        for row in it:
            w.writerow(row)
