import os
import sys
import json
import logging
import argparse
from pathlib import Path
from src.pipeline import index_pedoman, check_document


def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def main():
    setup_logging()

    parser = argparse.ArgumentParser(
        description="AI Document Checker - Compliance verification tool."
    )
    subparsers = parser.add_subparsers(dest="command", help="Available subcommands")

    # Command: index-pedoman
    index_parser = subparsers.add_parser(
        "index-pedoman", help="Index reference guideline PDFs into ChromaDB."
    )
    index_parser.add_argument(
        "--rules-dir",
        type=str,
        default="./rules",
        help="Path to directory containing reference guideline PDFs (default: ./rules)",
    )

    # Command: check
    check_parser = subparsers.add_parser(
        "check", help="Check document compliance against reference guidelines."
    )
    check_parser.add_argument(
        "--input",
        type=str,
        required=True,
        help="Path to input PDF file to check",
    )
    check_parser.add_argument(
        "--output",
        type=str,
        default="./output/hasil.json",
        help="Path where JSON report will be saved (default: ./output/hasil.json)",
    )
    check_parser.add_argument(
        "--refs",
        type=str,
        nargs="+",
        default=None,
        help="Optional list of reference guideline file names to restrict the check "
        "to (default: all indexed guidelines). Example: --refs \"UU 12 Tahun 2011.pdf\"",
    )

    # Command: serve
    serve_parser = subparsers.add_parser(
        "serve", help="Start the FastAPI Web UI dashboard server."
    )
    serve_parser.add_argument(
        "--host",
        type=str,
        default=os.environ.get("HOST", "0.0.0.0"),
        help="Host interface to bind (default: 0.0.0.0, or $HOST if set)",
    )
    serve_parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("PORT", 8000)),
        help="Port number to listen on (default: 8000, or $PORT if set)",
    )

    args = parser.parse_args()

    if args.command == "index-pedoman":
        print(f"[*] Starting indexing of reference guidelines from: {args.rules_dir}")
        count = index_pedoman(args.rules_dir)
        print(f"[OK] Indexing finished successfully. Total {count} chunk(s) indexed.")

    elif args.command == "check":
        print(f"[*] Checking document: {args.input}")
        if args.refs:
            print(f"[*] Menggunakan referensi terpilih: {', '.join(args.refs)}")

        def _print_progress(info):
            pct = int(info["completed"] * 100 / info["total"]) if info["total"] else 100
            print(f"    [progress] {info['completed']}/{info['total']} blok ({pct}%)")

        report = check_document(
            args.input, selected_references=args.refs, progress_callback=_print_progress
        )

        # Output JSON saving
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        json_data = report.model_dump_json(indent=2)
        output_path.write_text(json_data, encoding="utf-8")

        print(f"[OK] Check report saved to: {output_path.resolve()}")
        print("\nSummary:")
        for k, v in report.summary.items():
            print(f"  - {k}: {v}")

    elif args.command == "serve":
        import uvicorn
        # Render (and most PaaS) set RENDER=true in their build/runtime env - use
        # that to auto-disable the dev-only file watcher/auto-reload in production,
        # without needing a separate flag for local vs. deployed runs.
        is_production = bool(os.environ.get("RENDER"))
        print(f"[*] Starting AI Document Checker Web Server on http://{args.host}:{args.port}")
        print("[*] Tekan CTRL+C untuk menghentikan server.")
        try:
            uvicorn.run(
                "src.web_app:app",
                host=args.host,
                port=args.port,
                reload=not is_production,
            )
        except KeyboardInterrupt:
            print("\n[!] Server dihentikan oleh pengguna (CTRL+C).")
            sys.exit(0)

    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[!] Program dihentikan.")
        sys.exit(0)
