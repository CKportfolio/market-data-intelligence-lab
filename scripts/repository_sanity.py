from pathlib import Path
import re, sys

ROOT = Path(__file__).resolve().parents[1]
forbidden_dirs = {"node_modules", ".venv", "__pycache__"}
for p in ROOT.rglob("*"):
    if p.is_dir() and p.name in forbidden_dirs:
        raise SystemExit(f"Forbidden runtime directory committed: {p.relative_to(ROOT)}")

text_ext={'.md','.txt','.json','.yaml','.yml','.js','.mjs','.cjs','.py','.sh','.cmd','.bat'}
patterns=[
    re.compile(r"C:\\Users\\", re.I),
    re.compile(r'(?:api[_-]?key|secret|password)\s*[:=]\s*["\']?[A-Za-z0-9_\-]{24,}', re.I),
]
for p in ROOT.rglob('*'):
    if not p.is_file() or p.suffix.lower() not in text_ext: continue
    # published public artifacts deliberately contain placeholder, not local path
    s=p.read_text(encoding='utf-8',errors='ignore')
    for rx in patterns:
        if rx.search(s):
            raise SystemExit(f"Potential private/local value in {p.relative_to(ROOT)}: {rx.pattern}")
print('Repository sanity: PASS')
