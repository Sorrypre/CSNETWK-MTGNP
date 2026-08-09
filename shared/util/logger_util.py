import argparse
import logging
import json
from pathlib import Path

def setup_app_logging(current_File):
    parser = argparse.ArgumentParser(prog="Verbose", description=f"Used for logging PDUs sent and received. Without Verbose Argument it will display logging.INFO only. Run 'python {Path(current_File).name} --verbose' to activate the verbose mode. Use -v for shortcut")
    parser.add_argument("-v", "--verbose", action="store_true", help="Reveals the logging.debug() functions")

    # prevents crashing if there are other flags
    args, _ = parser.parse_known_args()

    log_level = logging.DEBUG if args.verbose else logging.INFO

    logging.basicConfig(level=log_level, format="%(asctime)s - %(levelname)s - %(message)s")

    return logging.getLogger(__name__)

def _format_empty_zones(d):
    """
    Formats empty dictionaries to empty lists for specific state zones to match specs.
    """
    if not isinstance(d, dict):
        return d

    formatted = {}
    for k, v in d.items():
        if isinstance(v, dict):
            # If it's a target key and it's empty, make it a list
            if k in ["waiting_for", "stack", "player_1", "player_2"] and not v:
                formatted[k] = []
            else:
                formatted[k] = _format_empty_zones(v)
        else:
            formatted[k] = v
    return formatted

def log_pdu_exchange(direction: str, context: str, pdu_dict: dict):
    """
    Formats and prints PDU exchanges to match the ExamplesCSNETWK_MP_MTGNP.pdf specification.
    e.g., C -> S (Player 1 passes)
    """
    display_dict = _format_empty_zones(pdu_dict.copy())

    # Use indent=2 to pretty-print the JSON payload
    formatted_json = json.dumps(display_dict, indent=2)

    # prefixes (time, level) that logging.info() would normally attach.
    print(f"{direction} ({context})\n{formatted_json}\n")