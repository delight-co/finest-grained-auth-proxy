import argparse
import logging
import os
import sys

from aiohttp import web

from fgap.core.config import load_config
from fgap.core.masking import MaskingFormatter, collect_secrets
from fgap.core.router import create_app

logger = logging.getLogger(__name__)

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def setup_logging(secrets: set[str], *, logfile: str | None = None) -> None:
    """Configure logging with secret masking."""
    if logfile:
        handler = logging.FileHandler(logfile)
    else:
        handler = logging.StreamHandler()
    handler.setFormatter(MaskingFormatter(LOG_FORMAT, secrets))
    logging.root.addHandler(handler)
    logging.root.setLevel(logging.INFO)


def _daemonize() -> None:
    """Fork into the background (Unix double-fork)."""
    if os.fork() > 0:
        sys.exit(0)
    os.setsid()
    if os.fork() > 0:
        sys.exit(0)
    # Redirect stdio to /dev/null
    devnull = os.open(os.devnull, os.O_RDWR)
    os.dup2(devnull, 0)
    os.dup2(devnull, 1)
    os.dup2(devnull, 2)
    os.close(devnull)


def main() -> int:
    parser = argparse.ArgumentParser(description="fgap - multi-CLI auth proxy")
    parser.add_argument("--config", required=True, help="Path to config file (JSON5)")
    parser.add_argument("--port", type=int, help="Port override (default: from config or 8766)")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind (default: 0.0.0.0)")
    parser.add_argument("--daemon", action="store_true", help="Run in the background")
    parser.add_argument("--pidfile", help="Write PID to this file")
    parser.add_argument("--logfile", help="Write logs to this file (required with --daemon)")
    args = parser.parse_args()

    if args.daemon and not args.logfile:
        print("Error: --logfile is required with --daemon", file=sys.stderr)
        return 1

    config = load_config(args.config)

    if args.daemon:
        _daemonize()

    setup_logging(collect_secrets(config), logfile=args.logfile)

    if args.pidfile:
        with open(args.pidfile, "w") as f:
            f.write(str(os.getpid()))

    port = args.port or config.get("port", 8766)

    app = create_app(config)
    logger.info("Starting fgap on %s:%d", args.host, port)
    web.run_app(app, host=args.host, port=port, print=None)
    return 0
