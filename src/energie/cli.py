# Copyright (c) 2026 Taras Bulubas. All rights reserved.
# Use requires prior written permission; see LICENSE.

"""Command line interface: start the local web app, try it on invented data, load hand-checked
CSV files, sync the collector, or measure how well a Gemma model reads the images."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> None:
    if hasattr(sys.stdout, "reconfigure"):          # UTF-8 also in a Windows console
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(prog="energie", description="Local home energy analysis.")
    # 127.0.0.1 only: the app has no authentication and must not be reachable from the network
    parser.add_argument("--host", default="127.0.0.1", help="interface to bind (default 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8510, help="port (default 8510)")
    sub = parser.add_subparsers(dest="command")

    p_demo = sub.add_parser("demo", help="try the app on invented data in a temporary folder (your database is not used)")
    p_demo.add_argument("--port", type=int, default=8511, help="port (default 8511)")

    p_csv = sub.add_parser("load-csv", help="load meter_readings.csv / shelly_readings.csv from a folder")
    p_csv.add_argument("folder")

    sub.add_parser("sync", help="fetch new data from the collector once")

    p_eval = sub.add_parser("eval", help="compare Gemma's reading with hand-checked values")
    p_eval.add_argument("--truth", default="data", help="folder with the hand-checked CSV files (default data)")
    p_eval.add_argument("--model", help="Ollama model, e.g. gemma4:e4b (default ENERGIE_GEMMA_MODEL)")
    p_eval.add_argument("--only", choices=["meter", "shelly"])
    p_eval.add_argument("--single-pass", action="store_true", help="read meters without locating the counter first")
    p_eval.add_argument("--limit", type=int)
    args = parser.parse_args(argv)

    if args.command == "demo":
        from energie import demo
        demo.run(args.host, args.port)
        return
    if args.command == "load-csv":
        from energie import db
        db.init_db()
        con = db.get_db()
        print(db.load_csv_dir(con, Path(args.folder)))
        con.close()
        return
    if args.command == "sync":
        from energie import db, sync
        db.init_db()
        con = db.get_db()
        url = db.get_setting(con, "collector_url")
        if not url:
            sys.exit("No collector address: set it on the Settings tab or with ENERGIE_COLLECTOR_URL.")
        print(sync.sync(con, url))
        con.close()
        return
    if args.command == "eval":
        from energie import evaluate, gemma
        if args.model:
            gemma.MODEL = args.model
        evaluate.run(Path(args.truth), args.only, args.limit, two_pass=not args.single_pass)
        return

    import uvicorn

    from energie.app import app

    uvicorn.run(app, host=args.host, port=args.port)
