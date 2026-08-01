import argparse
import logging
from pathlib import Path

def setup_app_logging(current_File):
    parser = argparse.ArgumentParser(prog="Verbose", description=f"Used for logging PDUs sent and received. Without Verbose Argument it will display logging.INFO only. Run 'python {Path(current_File).name} --verbose' to activate the verbose mode. Use -v for shortcut")
    parser.add_argument("-v", "--verbose", action="store_true", help="Reveals the logging.debug() functions")

    # prevents crashing if there are other flags
    args, _ = parser.parse_known_args()

    log_level = logging.DEBUG if args.verbose else logging.INFO

    logging.basicConfig(level=log_level, format="%(asctime)s - %(levelname)s - %(message)s")

    return logging.getLogger(__name__)
