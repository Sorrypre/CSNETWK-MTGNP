import socket
import struct
import json
import threading
import time

# --- CONFIGURATION ---
HOST = '127.0.0.1'
PORT = 4444
PLAYER_ID = input("Enter Player ID (e.g., Alice or Bob): ").strip()

# Globals for tracking state
auto_pass = False
last_seq = 0
current_phase = "LOBBY"
has_priority = False

def send_msg(sock, msg_dict):
    try:
        payload = json.dumps(msg_dict).encode('utf-8')
        sock.sendall(struct.pack("!I", len(payload)) + payload)
    except Exception as e:
        print(f"Error sending data: {e}")

def recv_loop(sock):
    global auto_pass, last_seq, current_phase, has_priority
    while True:
        try:
            header = sock.recv(4)
            if not header:
                print("\n[SYSTEM] Server closed connection.")
                break
            length = struct.unpack("!I", header)[0]
            msg = json.loads(sock.recv(length).decode('utf-8'))
            msg_type = msg.get("type")

            if "seq_num" in msg and msg_type != "PONG":
                last_seq = msg["seq_num"]

            if msg_type == "PHASE_TRANSITION":
                current_phase = msg.get("to_phase")
                print(f"\n[PHASE CHANGED] --> {current_phase}")

                # FIX 1: Only halt at PRECOMBAT_MAIN. Let it cruise through BEGIN_COMBAT!
                if current_phase == "PRECOMBAT_MAIN" and auto_pass:
                    auto_pass = False
                    print(f"\n[AUTO-PASS] Disabled. Reached {current_phase}!")

            if msg_type != "PONG":
                print(f"\n[SERVER]: {json.dumps(msg, indent=2)}\n> ", end="")

            if msg_type == "PRIORITY_GRANT":
                if msg.get("player_id") == PLAYER_ID:
                    has_priority = True
                    if auto_pass:
                        print(f"\n[AUTO-PASS] Passing priority (seq: {last_seq})...")
                        def delayed_pass(seq_to_send):
                            time.sleep(0.5)
                            send_msg(sock, {"type": "PRIORITY_PASS", "seq_num": seq_to_send})
                        threading.Thread(target=delayed_pass, args=(last_seq,), daemon=True).start()
                        has_priority = False
                else:
                    has_priority = False

        except Exception as e:
            print(f"\n[SYSTEM] Connection lost: {e}")
            break

def ping_loop(sock):
    seq = 1
    while True:
        time.sleep(5)
        ping_pdu = {"type": "PING", "seq_num": seq, "timestamp": int(time.time() * 1000)}
        send_msg(sock, ping_pdu)
        seq += 1

# --- CONNECT ---
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.connect((HOST, PORT))

threading.Thread(target=recv_loop, args=(s,), daemon=True).start()
threading.Thread(target=ping_loop, args=(s,), daemon=True).start()

print(f"\nConnected as {PLAYER_ID}! Type /help for a list of testing commands.")

# --- INPUT LOOP ---
while True:
    try:
        user_input = input("> ").strip()
        if not user_input:
            continue

        if user_input == "/help":
            print("""
Commands:
  /ready       - Sends a default 40-card deck to start the game
  /keep        - Sends a Mulligan Keep
  /autopass    - Toggles automatic PRIORITY_PASS responses on/off
  /land        - Plays a land (Usage: /land <land_id>)
  /cast        - Casts a spell using a land (Usage: /cast <spell_id>)
  /attack      - Declares an attacker (Usage: /attack <creature_id>)
  /block       - Declares a blocker (Usage: /block <blocker_id> <attacker_id>)
  /skip        - Skips mandatory combat phases with an empty declaration
            """)
        elif user_input == "/ready":
            deck = [f"mountain_{i:02d}" for i in range(1, 11)] + \
                   [f"goblin_guide_{i:02d}" for i in range(1, 11)] + \
                   [f"monastery_swiftspear_{i:02d}" for i in range(1, 11)] + \
                   [f"phantasmal_bear_{i:02d}" for i in range(1, 11)]
            send_msg(s, {"type": "PLAYER_READY", "seq_num": 1, "player_id": PLAYER_ID, "deck_list": deck})

        elif user_input == "/keep":
            send_msg(s, {"type": "MULLIGAN_CHOICE", "seq_num": last_seq, "keep": True, "cards_to_bottom": []})

        elif user_input == "/autopass":
            auto_pass = not auto_pass
            print(f"\n[SYSTEM] Auto-pass is now {'ON' if auto_pass else 'OFF'}")
            if auto_pass and has_priority:
                send_msg(s, {"type": "PRIORITY_PASS", "seq_num": last_seq})
                has_priority = False

        # --- Skip Macro for Empty Combat Declarations ---
        elif user_input == "/skip":
            if current_phase == "DECLARE_ATTACKERS":
                send_msg(s, {"type": "DECLARE_ATTACKERS", "seq_num": last_seq, "attackers": []})
            elif current_phase == "DECLARE_BLOCKERS":
                send_msg(s, {"type": "DECLARE_BLOCKERS", "seq_num": last_seq, "blockers": []})
            else:
                send_msg(s, {"type": "PRIORITY_PASS", "seq_num": last_seq})

        # --- MAIN PHASE MACROS ---
        elif user_input.startswith("/land"):
            parts = user_input.split()
            land_id = parts[1] if len(parts) > 1 else "mountain_01"
            send_msg(s, {
                "type": "PLAY_LAND", "seq_num": last_seq,
                "card_id": land_id
            })

        elif user_input.startswith("/cast"):
            parts = user_input.split()
            spell = parts[1] if len(parts) > 1 else "goblin_guide_01"
            mana_cost = {}
            if "goblin" in spell or "swiftspear" in spell:
                mana_cost = {"R": 1}
            elif "bear" in spell:
                mana_cost = {"U": 1}

            send_msg(s, {
                "type": "CAST_SPELL", "seq_num": last_seq,
                "card_id": spell, "targets": [],
                "mana_payment": mana_cost
            })

        # --- COMBAT MACROS ---
        elif user_input.startswith("/attack"):
            parts = user_input.split()
            attacker = parts[1] if len(parts) > 1 else "goblin_guide_01"
            opponent = "Bob" if PLAYER_ID == "Alice" else "Alice"
            send_msg(s, {
                "type": "DECLARE_ATTACKERS", "seq_num": last_seq,
                "attackers": [{"creature_id": attacker, "target": opponent}]
            })

        elif user_input.startswith("/block"):
            parts = user_input.split()
            blocker = parts[1] if len(parts) > 1 else "phantasmal_bear_01"
            attacker = parts[2] if len(parts) > 2 else "goblin_guide_01"
            send_msg(s, {
                "type": "DECLARE_BLOCKERS", "seq_num": last_seq,
                "blockers": [{"creature_id": blocker, "blocking_id": attacker}]
            })

        elif user_input.startswith("{"):
            send_msg(s, json.loads(user_input))
        else:
            print("Invalid command. Type /help or enter raw JSON.")

    except json.JSONDecodeError:
        print("Invalid JSON format. Please try again.")
    except KeyboardInterrupt:
        break
    except Exception as e:
        print(f"Error sending data: {e}")
        break