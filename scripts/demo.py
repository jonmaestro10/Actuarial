"""Serve the engine, and open the demonstration at /ui.

    python scripts/demo.py            # http://127.0.0.1:8000/ui
    python scripts/demo.py --port 9000 --host 0.0.0.0

One line of uvicorn with a sensible default, kept as a script because
``uvicorn engine.api:create_app --factory`` is the thing nobody remembers
and because it is worth saying, in the place someone starts the server, what
the page is: an evaluation surface for the API, not a product.

The counterpart is :mod:`scripts.api_demo`, which exercises the same
endpoints with no server and no browser and checks the numbers survive the
wire. This one is for looking.
"""

from __future__ import annotations

import argparse
import sys


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host", default="127.0.0.1",
                        help="interface to bind (default: loopback only)")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--workers", type=int, default=2,
                        help="run threads for concurrent projections")
    parser.add_argument("--no-ui", action="store_true",
                        help="serve the API without the page")
    args = parser.parse_args(argv)

    try:
        import uvicorn
    except ImportError:
        return "this needs the API extra: pip install -e '.[api]'"

    from engine.api import create_app

    app = create_app(max_workers=args.workers, ui=not args.no_ui)
    where = f"http://{args.host}:{args.port}"
    print(f"  API      {where}/docs")
    if not args.no_ui:
        print(f"  the page {where}/ui")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
