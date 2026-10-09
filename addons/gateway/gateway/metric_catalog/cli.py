from __future__ import annotations

import argparse
import json
import sys

from gateway.ha_client import HAClient

from . import store
from .runner import config_from_env, run_once
from .vm_match import VMClient


def main(argv: list[str] | None = None) -> int:
    cfg = config_from_env()
    p = argparse.ArgumentParser(prog="python -m gateway.metric_catalog")
    p.add_argument("--db", default=cfg["db_path"])
    sub = p.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run", help="collect entities + VM series and store a new catalog run")
    run.add_argument("--out-dir", default=cfg["out_dir"])
    run.add_argument("--lookback-days", type=int, default=cfg["lookback_days"])
    run.add_argument("--git-commit", action="store_true")
    s = sub.add_parser("search", help="full-text search the latest catalog")
    s.add_argument("query")
    s.add_argument("--limit", type=int, default=25)
    sub.add_parser("changes", help="diff the latest run against the previous one")
    e = sub.add_parser("export", help="write JSONL + CSV from the latest run")
    e.add_argument("out_dir")
    args = p.parse_args(argv)

    if args.cmd == "run":
        result = run_once(HAClient.from_env(), VMClient(cfg["vm_url"], username=cfg["vm_username"], password=cfg["vm_password"]), args.db, args.out_dir,
                          args.lookback_days, args.git_commit)
        print(json.dumps(result))
        return 0
    conn = store.connect(args.db)
    if args.cmd == "search":
        for ent in store.search(conn, args.query, args.limit):
            metrics = ", ".join(sorted({x["metric"] for x in ent["series"]})) or "(no VM series)"
            print(f'{ent["entity_id"]:45} {ent["friendly_name"] or ent["name"]:30} {metrics}')
    elif args.cmd == "changes":
        print(json.dumps(store.diff_runs(conn), indent=2))
    elif args.cmd == "export":
        print("\n".join(str(x) for x in store.export(conn, args.out_dir)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
