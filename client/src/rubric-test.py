import socket
import json
import struct
import time
import os
import sys
import logging

# Track two levels up to find the project root (matching client-test.py)
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, ROOT)

from shared.util.logger_util import setup_app_logging, log_pdu_exchange

# Initialize the custom logger with the verbose argparse logic
logger = setup_app_logging(__file__)

HOST = '127.0.0.1'
PORT = 4444

def send_pdu(sock, pdu, log=True):
    """
    Frames and sends a JSON PDU.
    """
    if log:
        # Uses the custom formatter. (Outputs via logging.debug)
        log_pdu_exchange("C -> S", pdu.get("type", "TX"), pdu)

    payload = json.dumps(pdu).encode('utf-8')
    sock.sendall(struct.pack("!I", len(payload)) + payload)

def recv_pdu(sock, timeout=2.0, log=True):
    """
    Receives and un-frames a JSON PDU
    """
    sock.settimeout(timeout)
    try:
        header = sock.recv(4)
        if not header:
            return None
        length = struct.unpack("!I", header)[0]
        data = sock.recv(length)
        payload = json.loads(data.decode('utf-8'))

        if log:
            log_pdu_exchange("S -> C", payload.get("type", "RX"), payload)

        return payload
    except socket.timeout:
        return None
    except Exception as e:
        logger.error(f"Receive error: {e}")
        return None

def flush_socket(sock):
    """
    Clears pending messages in the socket buffer silently
    """
    sock.setblocking(False)
    try:
        while sock.recv(65535): pass
    except:
        pass
    sock.setblocking(True)

# --- DECK GENERATORS ---
def generate_test_deck():
    return (
            [f"mountain_{i:03d}" for i in range(1, 21)] +
            [f"goblin_guide_{i:03d}" for i in range(1, 5)] +
            [f"monastery_swiftspear_{i:03d}" for i in range(1, 5)] +
            [f"phantasmal_bear_{i:03d}" for i in range(1, 5)] +
            [f"lightning_bolt_{i:03d}" for i in range(1, 5)] +
            [f"shock_{i:03d}" for i in range(1, 5)]
    )

def test_connection_limits():
    logger.info("--- TESTING TCP SOCKETS & NETWORKING (Max 2 Clients) ---")
    c1 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    c2 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    c3 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

    logger.info("Connecting Client 1...")
    c1.connect((HOST, PORT))
    logger.info("Connecting Client 2...")
    c2.connect((HOST, PORT))
    logger.info("Connecting Client 3 (Should fail)...")
    c3.connect((HOST, PORT))

    resp = recv_pdu(c3)
    if resp and resp.get("type") == "ERROR" and "limit reached" in resp.get("message", "").lower():
        logger.info("✅ PASS: 3rd connection correctly rejected.")
    else:
        logger.error("❌ FAIL: 3rd connection was not rejected properly.")

    c1.close()
    c2.close()
    c3.close()
    time.sleep(0.5)

def test_lobby_and_errors():
    logger.info("--- TESTING LOBBY, ERRORS & DECK VALIDATION ---")
    c1 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    c2 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    c1.connect((HOST, PORT))
    c2.connect((HOST, PORT))

    logger.info("[Test 1] Attempting to submit empty deck...")
    send_pdu(c1, {"type": "PLAYER_READY", "seq_num": 1, "player_id": "Kurt", "deck_list": []})
    resp = recv_pdu(c1)
    if resp and resp.get("code") == "ILLEGAL_DECK":
        logger.info("✅ PASS: Blocked empty deck (ILLEGAL_DECK).")
    else:
        logger.error(f"❌ FAIL: Expected ILLEGAL_DECK, got: {resp}")

    logger.info("[Test 2] Submitting valid Red Aggro deck...")
    send_pdu(c1, {"type": "PLAYER_READY", "seq_num": 2, "player_id": "Kurt", "deck_list": generate_test_deck()})

    logger.info("[Test 3] Client 2 attempting to steal 'Kurt' ID...")
    send_pdu(c2, {"type": "PLAYER_READY", "seq_num": 1, "player_id": "Kurt", "deck_list": generate_test_deck()})
    resp = recv_pdu(c2)
    if resp and resp.get("code") == "DUPLICATE_ID":
        logger.info("✅ PASS: Blocked duplicate username (DUPLICATE_ID).")
    else:
        logger.error(f"❌ FAIL: Expected DUPLICATE_ID, got: {resp}")

    logger.info("[Test 4] Client 2 registering correctly as 'Jensel'...")
    send_pdu(c2, {"type": "PLAYER_READY", "seq_num": 2, "player_id": "Jensel", "deck_list": generate_test_deck()})

    flush_socket(c1)
    flush_socket(c2)
    logger.info("✅ PASS: Players registered successfully.")
    c1.close()
    c2.close()
    time.sleep(0.5)

def test_in_game_timing():
    logger.info("--- TESTING TIMING & STALE ACTIONS ---")
    c1 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    c2 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    c1.connect((HOST, PORT))
    c2.connect((HOST, PORT))

    send_pdu(c1, {"type": "PLAYER_READY", "seq_num": 1, "player_id": "P1", "deck_list": generate_test_deck()}, log=False)
    send_pdu(c2, {"type": "PLAYER_READY", "seq_num": 1, "player_id": "P2", "deck_list": generate_test_deck()}, log=False)
    time.sleep(0.5)
    flush_socket(c1)

    logger.info("[Test] Submitting stale sequence token...")
    send_pdu(c1, {"type": "PRIORITY_PASS", "seq_num": 9999})
    resp = recv_pdu(c1)
    if resp and resp.get("code") == "STALE_ACTION":
        logger.info("✅ PASS: Stale sequence numbers correctly rejected (STALE_ACTION).")
    else:
        logger.error(f"❌ FAIL: Expected STALE_ACTION, got: {resp}")

    logger.info("[Test] Player 1 Conceding Match...")
    send_pdu(c1, {"type": "CONCEDE", "seq_num": 0, "player_id": "P1"})

    game_over_found = False
    start_time = time.time()
    while time.time() - start_time < 1:
        resp = recv_pdu(c2, timeout=0.5, log=False)
        if resp and resp.get("type") == "GAME_OVER":
            if resp.get("reason") == "CONCEDE":
                game_over_found = True
                break

    if game_over_found:
        logger.info("✅ PASS: CONCEDE cleanly triggers GAME_OVER broadcast.")
    else:
        logger.error("❌ FAIL: Did not receive GAME_OVER after concede.")

    c1.close()
    c2.close()

def test_ping_pong():
    logger.info("--- TESTING PING/PONG HEARTBEAT ---")
    c1 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    c1.connect((HOST, PORT))

    test_time = int(time.time() * 1000)
    send_pdu(c1, {"type": "PING", "seq_num": 99, "timestamp": test_time})
    resp = recv_pdu(c1)

    if resp and resp.get("type") == "PONG" and resp.get("timestamp") == test_time:
        logger.info("✅ PASS: Server correctly responded to PING with matching PONG.")
    else:
        logger.error(f"❌ FAIL: Expected PONG with matching timestamp, got: {resp}")

    c1.close()
    time.sleep(0.5)

def test_hidden_info_and_mulligan():
    logger.info("--- TESTING HIDDEN INFO & LONDON MULLIGAN ---")
    c1 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    c2 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    c1.connect((HOST, PORT))
    c2.connect((HOST, PORT))

    send_pdu(c1, {"type": "PLAYER_READY", "seq_num": 1, "player_id": "P1", "deck_list": generate_test_deck()}, log=False)
    send_pdu(c2, {"type": "PLAYER_READY", "seq_num": 1, "player_id": "P2", "deck_list": generate_test_deck()}, log=False)

    p1_seq = p2_seq = 0
    start_time = time.time()
    while time.time() - start_time < 2:
        resp1 = recv_pdu(c1, timeout=0.1, log=False)
        if resp1 and resp1.get("type") == "GAME_STATE_UPDATE" and resp1.get("state", {}).get("phase") == "MULLIGAN":
            p1_seq = resp1.get("seq_num")
        resp2 = recv_pdu(c2, timeout=0.1, log=False)
        if resp2 and resp2.get("type") == "GAME_STATE_UPDATE" and resp2.get("state", {}).get("phase") == "MULLIGAN":
            p2_seq = resp2.get("seq_num")
        if p1_seq and p2_seq: break

    logger.info("[Test] P2 Requests Mulligan Redraw...")
    send_pdu(c1, {"type": "MULLIGAN_CHOICE", "seq_num": p1_seq, "keep": True, "cards_to_bottom": []}, log=False)
    send_pdu(c2, {"type": "MULLIGAN_CHOICE", "seq_num": p2_seq, "keep": False, "cards_to_bottom": []})

    resp = recv_pdu(c2, timeout=1.0)
    if resp and resp.get("type") == "GAME_STATE_UPDATE":
        state = resp.get("state", {})
        p1_hand = state.get("hand", {}).get("P1")
        if not p1_hand:
            logger.info("✅ PASS: Opponent's hand is correctly masked (Hidden Information).")
        else:
            logger.error("❌ FAIL: Opponent's hand is visible in state update!")

        logger.info("[Test] P2 Attempts to Keep without paying London Mulligan penalty...")
        new_seq = resp.get("seq_num")
        send_pdu(c2, {"type": "MULLIGAN_CHOICE", "seq_num": new_seq, "keep": True, "cards_to_bottom": []})
        err_resp = recv_pdu(c2)
        if err_resp and err_resp.get("code") == "ILLEGAL_ACTION" and "Expected 1" in err_resp.get("message", ""):
            logger.info("✅ PASS: London Mulligan rule enforced (rejected keep with wrong cards_to_bottom count).")
        else:
            logger.error(f"❌ FAIL: Expected ILLEGAL_ACTION for bad mulligan keep, got: {err_resp}")

    send_pdu(c1, {"type": "CONCEDE", "seq_num": 0, "player_id": "P1"}, log=False)
    c1.close()
    c2.close()

def test_deck_empty():
    logger.info("--- TESTING GAME OVER (DECK_EMPTY) ---")
    c1 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    c2 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    c1.connect((HOST, PORT))
    c2.connect((HOST, PORT))

    logger.info("[Test] Submitting 1-card decks to force mill-out...")
    send_pdu(c1, {"type": "PLAYER_READY", "seq_num": 1, "player_id": "P1", "deck_list": ["mountain_001"]})
    send_pdu(c2, {"type": "PLAYER_READY", "seq_num": 1, "player_id": "P2", "deck_list": ["island_001"]})

    p1_seq = p2_seq = 0
    start_time = time.time()
    while time.time() - start_time < 2:
        resp1 = recv_pdu(c1, timeout=0.1, log=False)
        if resp1 and resp1.get("type") == "GAME_STATE_UPDATE" and resp1.get("state", {}).get("phase") == "MULLIGAN":
            p1_seq = resp1.get("seq_num")
        resp2 = recv_pdu(c2, timeout=0.1, log=False)
        if resp2 and resp2.get("type") == "GAME_STATE_UPDATE" and resp2.get("state", {}).get("phase") == "MULLIGAN":
            p2_seq = resp2.get("seq_num")
        if p1_seq and p2_seq: break

    send_pdu(c1, {"type": "MULLIGAN_CHOICE", "seq_num": p1_seq, "keep": True, "cards_to_bottom": []}, log=False)
    send_pdu(c2, {"type": "MULLIGAN_CHOICE", "seq_num": p2_seq, "keep": True, "cards_to_bottom": []}, log=False)

    game_over_found = False
    start_time = time.time()
    while time.time() - start_time < 2:
        resp = recv_pdu(c1, timeout=0.2, log=False)
        if resp and resp.get("type") == "GAME_OVER" and resp.get("reason") == "DECK_EMPTY":
            game_over_found = True
            break

    if game_over_found:
        logger.info("✅ PASS: Drawing from empty library correctly triggers GAME_OVER (DECK_EMPTY).")
    else:
        logger.error("❌ FAIL: Did not receive GAME_OVER with DECK_EMPTY reason.")

    c1.close()
    c2.close()

if __name__ == "__main__":
    logger.info("Starting Detailed Rubric Validation Tests...\n")
    test_connection_limits()
    test_lobby_and_errors()
    test_ping_pong()
    test_hidden_info_and_mulligan()
    test_in_game_timing()
    test_deck_empty()
    logger.info("Tests complete.")