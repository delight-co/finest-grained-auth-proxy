import argparse
import logging

from aiohttp import web

from fgap.core.config import load_config
from fgap.core.masking import MaskingFormatter, collect_secrets
from fgap.core.router import create_app

logger = logging.getLogger(__name__)

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def setup_logging(secrets: set[str]) -> None:
    """Configure logging with secret masking."""
    handler = logging.StreamHandler()
    handler.setFormatter(MaskingFormatter(LOG_FORMAT, secrets))
    logging.root.addHandler(handler)
    logging.root.setLevel(logging.INFO)


def main() -> int:
    parser = argparse.ArgumentParser(description="fgap - multi-CLI auth proxy")
    parser.add_argument("--config", required=True, help="Path to config file (JSON5)")
    parser.add_argument("--port", type=int, help="Port override (default: from config or 8766)")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind (default: 0.0.0.0)")
    args = parser.parse_args()

    config = load_config(args.config)
    setup_logging(collect_secrets(config))

    port = args.port or config.get("port", 8766)

    app = create_app(config)
    logger.info("Starting fgap on %s:%d", args.host, port)
    web.run_app(app, host=args.host, port=port, print=None)
    return 0
