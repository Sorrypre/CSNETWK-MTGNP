import socket
import struct
import json
import threading
import time

import logging
import os
import sys

# Track two levels up from client.py to find the project root
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, ROOT)

from shared.util.logger_util import setup_app_logging

setup_app_logging(__file__)

def recv_loop(sock):
    while True:
        try:
            header = sock.recv(4)
            if not header:
                logging.info("\n[SYSTEM] Server closed connection.")
                break
            length = struct.unpack("!I", header)[0]
            msg = json.loads(sock.recv(length).decode('utf-8'))

            # Optionally filter out PONGs to keep your terminal clean during manual testing
            if msg.get("type") != "PONG":
                logging.info(f"\n[SERVER]: {json.dumps(msg, indent=2)}\n> ")
        except Exception as e:
            logging.info(f"\n[SYSTEM] Connection lost: {e}")
            break

def ping_loop(sock):
    """Automates the PING heartbeat required by MTGNP specifications."""
    seq = 1
    while True:
        time.sleep(5) # Send every 5 seconds to beat the server's 10-second timeout
        ping_pdu = {
            "type": "PING",
            "seq_num": seq,
            "timestamp": int(time.time() * 1000)
        }
        try:
            payload = json.dumps(ping_pdu).encode('utf-8')
            sock.sendall(struct.pack("!I", len(payload)) + payload)
            seq += 1
        except Exception:
            break # Stop thread if socket closes

s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.connect(('127.0.0.1', 4444))

# Start background threads for receiving and pinging
threading.Thread(target=recv_loop, args=(s,), daemon=True).start()
threading.Thread(target=ping_loop, args=(s,), daemon=True).start()

while True:
    try:
        user_input = input("> ")
        if user_input.strip():
            msg_dict = json.loads(user_input)
            payload = json.dumps(msg_dict).encode('utf-8')
            s.sendall(struct.pack("!I", len(payload)) + payload)
    except json.JSONDecodeError:
        logging.info("Invalid JSON format. Please try again.")
    except Exception as e:
        logging.info(f"Error sending data: {e}")
        break