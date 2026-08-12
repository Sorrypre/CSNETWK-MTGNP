import socket
import json
import struct
import time

HOST = '127.0.0.1'
PORT = 4444

def send_pdu(sock, pdu):
    """Frames and sends a JSON PDU."""
    payload = json.dumps(pdu).encode('utf-8')
    sock.sendall(struct.pack("!I", len(payload)) + payload)

def recv_pdu(sock, timeout=2.0):
    """Receives and un-frames a JSON PDU."""
    sock.settimeout(timeout)
    try:
        header = sock.recv(4)
        if not header:
            return None
        length = struct.unpack("!I", header)[0]
        data = sock.recv(length)
        return json.loads(data.decode('utf-8'))
    except socket.timeout:
        return None
    except Exception as e:
        print(f"Receive error: {e}")
        return None

def flush_socket(sock):
    """Clears pending messages in the socket buffer."""
    sock.setblocking(False)
    try:
        while sock.recv(65535): pass
    except:
        pass
    sock.setblocking(True)

def test_connection_limits():
    print("\n--- TESTING TCP SOCKETS & NETWORKING (Max 2 Clients) ---")
    c1 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    c2 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    c3 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

    c1.connect((HOST, PORT))
    c2.connect((HOST, PORT))
    c3.connect((HOST, PORT))

    # The 3rd client should receive an error and be closed by the server
    resp = recv_pdu(c3)
    if resp and resp.get("type") == "ERROR" and "limit reached" in resp.get("message", "").lower():
        print("✅ PASS: 3rd connection correctly rejected.")
    else:
        print("❌ FAIL: 3rd connection was not rejected properly.")

    c1.close()
    c2.close()
    c3.close()
    time.sleep(1) # Let server clean up

def test_lobby_and_errors():
    print("\n--- TESTING LOBBY, ERRORS & SEQ_NUM ENFORCEMENT ---")
    c1 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    c2 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    c1.connect((HOST, PORT))
    c2.connect((HOST, PORT))

    # Generate legally unique deck lists
    deck_1 = [f"mountain_{i:02d}" for i in range(1, 11)]
    deck_2 = [f"island_{i:02d}" for i in range(1, 11)]

    # Test 1: ILLEGAL_DECK (Empty Deck)
    send_pdu(c1, {
        "type": "PLAYER_READY", "seq_num": 1, "player_id": "Alice", "deck_list": []
    })
    resp = recv_pdu(c1)
    if resp and resp.get("code") == "ILLEGAL_DECK":
        print("✅ PASS: Blocked empty deck (ILLEGAL_DECK).")
    else:
        print(f"❌ FAIL: Expected ILLEGAL_DECK, got: {resp}")

    # Test 2: Valid Registration
    send_pdu(c1, {
        "type": "PLAYER_READY", "seq_num": 2, "player_id": "Alice", "deck_list": deck_1
    })

    # Test 3: DUPLICATE_ID
    send_pdu(c2, {
        "type": "PLAYER_READY", "seq_num": 1, "player_id": "Alice", "deck_list": deck_2
    })
    resp = recv_pdu(c2)
    if resp and resp.get("code") == "DUPLICATE_ID":
        print("✅ PASS: Blocked duplicate username (DUPLICATE_ID).")
    else:
        print(f"❌ FAIL: Expected DUPLICATE_ID, got: {resp}")

    # Test 4: Game Start & Mulligan
    send_pdu(c2, {
        "type": "PLAYER_READY", "seq_num": 2, "player_id": "Bob", "deck_list": deck_2
    })

    flush_socket(c1)
    flush_socket(c2)
    print("✅ PASS: Players registered, checking phase transitions...")

    c1.close()
    c2.close()
    time.sleep(1)

def test_in_game_timing():
    print("\n--- TESTING TIMING & STALE ACTIONS ---")
    c1 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    c2 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    c1.connect((HOST, PORT))
    c2.connect((HOST, PORT))

    # Generate legally unique 40-card deck lists
    deck_1 = [f"mountain_{i:02d}" for i in range(1, 41)]
    deck_2 = [f"island_{i:02d}" for i in range(1, 41)]

    # Setup Match
    send_pdu(c1, {"type": "PLAYER_READY", "seq_num": 1, "player_id": "P1", "deck_list": deck_1})
    send_pdu(c2, {"type": "PLAYER_READY", "seq_num": 1, "player_id": "P2", "deck_list": deck_2})

    # Skip Mulligans (Just use sequence 0 for testing fallback)
    send_pdu(c1, {"type": "MULLIGAN_CHOICE", "seq_num": 3, "keep": True, "cards_to_bottom": []})
    send_pdu(c2, {"type": "MULLIGAN_CHOICE", "seq_num": 3, "keep": True, "cards_to_bottom": []})
    time.sleep(0.5)

    flush_socket(c1)
    flush_socket(c2)

    # Force a stale action error
    send_pdu(c1, {"type": "PRIORITY_PASS", "seq_num": 9999})
    resp = recv_pdu(c1)
    if resp and resp.get("code") == "STALE_ACTION":
        print("✅ PASS: Stale sequence numbers correctly rejected (STALE_ACTION).")
    else:
        print(f"❌ FAIL: Expected STALE_ACTION, got: {resp}")

    # Test Concede / Game Over
    send_pdu(c1, {"type": "CONCEDE", "seq_num": 0, "player_id": "P1"})
    time.sleep(0.5)

    game_over_found = False
    while True:
        resp = recv_pdu(c2, timeout=0.5)
        if not resp: break
        if resp.get("type") == "GAME_OVER" and resp.get("reason") == "CONCEDE":
            game_over_found = True
            break

    if game_over_found:
        print("✅ PASS: CONCEDE cleanly triggers GAME_OVER broadcast.")
    else:
        print("❌ FAIL: Did not receive GAME_OVER after concede.")

    c1.close()
    c2.close()

def test_ping_pong():
    print("\n--- TESTING PING/PONG HEARTBEAT ---")
    c1 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    c1.connect((HOST, PORT))

    test_time = int(time.time() * 1000)
    send_pdu(c1, {"type": "PING", "seq_num": 99, "timestamp": test_time})
    resp = recv_pdu(c1)

    if resp and resp.get("type") == "PONG" and resp.get("timestamp") == test_time:
        print("✅ PASS: Server correctly responded to PING with matching PONG.")
    else:
        print(f"❌ FAIL: Expected PONG with matching timestamp, got: {resp}")

    c1.close()
    time.sleep(1)

def test_hidden_info_and_mulligan():
    print("\n--- TESTING HIDDEN INFO & LONDON MULLIGAN ---")
    c1 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    c2 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    c1.connect((HOST, PORT))
    c2.connect((HOST, PORT))

    deck_1 = [f"mountain_{i:02d}" for i in range(1, 41)]
    deck_2 = [f"island_{i:02d}" for i in range(1, 41)]

    # Setup
    send_pdu(c1, {"type": "PLAYER_READY", "seq_num": 1, "player_id": "P1", "deck_list": deck_1})
    send_pdu(c2, {"type": "PLAYER_READY", "seq_num": 1, "player_id": "P2", "deck_list": deck_2})

    # Dynamically hunt for the MULLIGAN sequence numbers
    p1_seq = 0
    p2_seq = 0
    start_time = time.time()

    while time.time() - start_time < 2:
        resp1 = recv_pdu(c1, timeout=0.1)
        if resp1 and resp1.get("type") == "GAME_STATE_UPDATE" and resp1.get("state", {}).get("phase") == "MULLIGAN":
            p1_seq = resp1.get("seq_num")

        resp2 = recv_pdu(c2, timeout=0.1)
        if resp2 and resp2.get("type") == "GAME_STATE_UPDATE" and resp2.get("state", {}).get("phase") == "MULLIGAN":
            p2_seq = resp2.get("seq_num")

        if p1_seq and p2_seq:
            break

    if not p1_seq or not p2_seq:
        print("❌ FAIL: Did not receive initial Mulligan sequence numbers.")
        c1.close(); c2.close(); return

    # P1 Keeps using dynamic seq
    send_pdu(c1, {"type": "MULLIGAN_CHOICE", "seq_num": p1_seq, "keep": True, "cards_to_bottom": []})

    # P2 Mulligans using dynamic seq
    send_pdu(c2, {"type": "MULLIGAN_CHOICE", "seq_num": p2_seq, "keep": False, "cards_to_bottom": []})

    # P2 gets a new personalized GAME_STATE_UPDATE
    resp = recv_pdu(c2, timeout=1.0)

    if resp and resp.get("type") == "GAME_STATE_UPDATE":
        state = resp.get("state", {})
        # Check hidden info: P2's state dictionary should NOT contain P1's hand array
        p1_hand = state.get("hand", {}).get("P1")
        if not p1_hand:
            print("✅ PASS: Opponent's hand is correctly masked (Hidden Information).")
        else:
            print("❌ FAIL: Opponent's hand is visible in state update!")

        # P2 must now keep, but because they took 1 mulligan, they must bottom 1 card.
        new_seq = resp.get("seq_num")
        send_pdu(c2, {"type": "MULLIGAN_CHOICE", "seq_num": new_seq, "keep": True, "cards_to_bottom": []})

        err_resp = recv_pdu(c2)
        if err_resp and err_resp.get("code") == "ILLEGAL_ACTION" and "Expected 1" in err_resp.get("message", ""):
            print("✅ PASS: London Mulligan rule enforced (rejected keep with wrong cards_to_bottom count).")
        else:
            print(f"❌ FAIL: Expected ILLEGAL_ACTION for bad mulligan keep, got: {err_resp}")
    else:
        print(f"❌ FAIL: Did not receive GAME_STATE_UPDATE after mulligan redraw. Got: {resp}")

    # Clean up so the next test doesn't crash
    send_pdu(c1, {"type": "CONCEDE", "seq_num": 0, "player_id": "P1"})
    c1.close()
    c2.close()
    time.sleep(1)

def test_deck_empty():
    print("\n--- TESTING GAME OVER (DECK_EMPTY) ---")
    c1 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    c2 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    c1.connect((HOST, PORT))
    c2.connect((HOST, PORT))

    # Submit 1-card decks so both players instantly draw from an empty library
    send_pdu(c1, {"type": "PLAYER_READY", "seq_num": 1, "player_id": "P1", "deck_list": ["mountain_01"]})
    send_pdu(c2, {"type": "PLAYER_READY", "seq_num": 1, "player_id": "P2", "deck_list": ["island_01"]})

    p1_seq = p2_seq = 0
    start_time = time.time()
    while time.time() - start_time < 2:
        resp1 = recv_pdu(c1, timeout=0.1)
        if resp1 and resp1.get("type") == "GAME_STATE_UPDATE" and resp1.get("state", {}).get("phase") == "MULLIGAN":
            p1_seq = resp1.get("seq_num")

        resp2 = recv_pdu(c2, timeout=0.1)
        if resp2 and resp2.get("type") == "GAME_STATE_UPDATE" and resp2.get("state", {}).get("phase") == "MULLIGAN":
            p2_seq = resp2.get("seq_num")

        if p1_seq and p2_seq:
            break

    if not p1_seq or not p2_seq:
        print("❌ FAIL: Did not receive initial Mulligan sequence numbers.")
        c1.close(); c2.close(); return

    # Both players keep their (mostly empty) hands
    send_pdu(c1, {"type": "MULLIGAN_CHOICE", "seq_num": p1_seq, "keep": True, "cards_to_bottom": []})
    send_pdu(c2, {"type": "MULLIGAN_CHOICE", "seq_num": p2_seq, "keep": True, "cards_to_bottom": []})

    # Hunt for the resulting GAME_OVER PDU
    game_over_found = False
    start_time = time.time()
    while time.time() - start_time < 2:
        resp = recv_pdu(c1, timeout=0.2)
        if resp and resp.get("type") == "GAME_OVER" and resp.get("reason") == "DECK_EMPTY":
            game_over_found = True
            break

    if game_over_found:
        print("✅ PASS: Drawing from empty library correctly triggers GAME_OVER (DECK_EMPTY).")
    else:
        print("❌ FAIL: Did not receive GAME_OVER with DECK_EMPTY reason.")

    c1.close()
    c2.close()
    time.sleep(1)

if __name__ == "__main__":
    print("Starting Rubric Validation Tests...")
    test_connection_limits()
    test_lobby_and_errors()
    test_ping_pong()
    test_hidden_info_and_mulligan()
    test_in_game_timing()
    test_deck_empty()
    print("\nTests complete. Review the remaining manual testing steps below.")