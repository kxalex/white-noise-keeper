from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import replace
from pathlib import Path

from .cast import PyChromecastClient
from .config import AppConfig, load_config
from .keeper import WhiteNoiseKeeper
from .state import StateStore


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Keep Google Nest white noise playback alive.")
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("/etc/white-noise-keeper/config.toml"),
        help="Path to TOML config file.",
    )
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--once", action="store_true", help="Run one keeper loop and exit.")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging.")
    parser.add_argument(
        "--state-path",
        type=Path,
        help="Override runtime state path. Useful for local integration tests.",
    )
    args = parser.parse_args(argv)

    configure_logging(args.debug)
    try:
        config = load_config(args.config)
        if args.state_path is not None:
            config = replace(
                config,
                monitor=replace(config.monitor, state_path=args.state_path),
            )
        keeper = build_keeper(config)
        if args.once:
            result = keeper.run_once()
            logging.getLogger(__name__).info(result.message)
            return 0 if result.healthy else 1

        keeper.run_forever()
        return 0
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        logging.getLogger(__name__).error("%s", exc)
        return 1


def build_keeper(config: AppConfig) -> WhiteNoiseKeeper:
    return WhiteNoiseKeeper(
        config=config,
        cast_client=PyChromecastClient(config.cast),
        state_store=StateStore(config.monitor.state_path),
    )


def configure_logging(debug: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )


if __name__ == "__main__":
    raise SystemExit(main())
