"""Rebuild derived SQL and NoSQL stores from the compact pinned public sources."""
from pathlib import Path
import argparse
import json
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from twin import data, semantic

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--force', action='store_true', help='Replace derived SQLite data after source hash verification.')
    args = parser.parse_args()
    print(json.dumps({'data': data.initialise(force=args.force), 'semantic': semantic.initialise(force=args.force)}, indent=2))
