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

client_state = {
    'ready': False,
    'mulligan_count': 0,
    'current_seq_num': 0,
    'player_id': None,
    'opponent_id': None
}

def send_pdu(sock, pdu_dict):
    """Helper function to frame and send PDUs."""
    payload = json.dumps(pdu_dict).encode('utf-8')
    sock.sendall(struct.pack("!I", len(payload)) + payload)

def parse_command(inp: str) -> dict:
    args = inp.strip().split()
    this_seq_num = client_state['current_seq_num']
    if not args:
        return None

    command = args[0].lower()

    try:
        if command == 'ready':
            if len(args) < 2:
                logging.info("Usage: ready <player_name>")
                return None
            deck = [f"mountain_{i:03d}" for i in range(1, 11)] + \
                   [f"goblin_guide_{i:03d}" for i in range(1, 5)] + \
                   [f"monastery_swiftspear_{i:03d}" for i in range(1, 5)] + \
                   [f"gray_merchant_{i:03d}" for i in range(1, 5)] + \
                   [f"swamp_{i:03d}" for i in range(1, 11)]
            return {
                'type': 'PLAYER_READY',
                'seq_num': this_seq_num,
                'player_id': args[1],
                'deck_list': deck
            }

        elif command == 'pass':
            return {
                'type': 'PRIORITY_PASS',
                'seq_num': this_seq_num
            }

        elif command == 'mulligan':
            decision = None if len(args) == 1 else args[1].lower()
            cards_to_bottom = []
            if not decision or decision == 'keep':
                keep = True
            elif decision == 'redraw':
                keep = False
                client_state['mulligan_count'] += 1
            elif decision == 'confirm':
                keep = True
                if len(args) > 2:
                    # Accurately slice the exact number of penalty cards required
                    penalty_count = client_state['mulligan_count']
                    cards_to_bottom = args[2:2 + penalty_count]
            else:
                return None

            return {
                'type': 'MULLIGAN_CHOICE',
                'seq_num': this_seq_num,
                'keep': keep,
                'cards_to_bottom': cards_to_bottom,
            }

        elif command == 'play':
            return {
                'type': 'PLAY_LAND',
                'seq_num': this_seq_num,
                'card_id': args[1]
            }

        elif command == 'cast':
            card_id = args[1]
            targets = [args[2]] if args[2] != 'none' else []
            mana_payment = {}
            for mana in args[3:]:
                color, amt = mana.split('=')
                mana_payment[color.upper()] = int(amt)
            return {
                'type': 'CAST_SPELL',
                'seq_num': this_seq_num,
                'card_id': card_id,
                'targets': targets,
                'mana_payment': mana_payment
            }

        # Usage: activate <permanent_id> <ability_index> [targets] ~ [tap=true/false] <mana_color=amount>
        elif command == 'activate':
            if len(args) < 3:
                logging.info("Usage: activate <id> <index> [target|none] ~ tap=true R=1")
                return None

            permanent = args[1]
            ability_index = int(args[2])
            targets = []
            cost_payment = {"mana": {}, "tap": False}
            paying = False

            for arg in args[3:]:
                if arg == '~':
                    paying = True
                    continue
                if not paying:
                    if arg.lower() != 'none':
                        targets.append(arg)
                else:
                    if arg.lower().startswith('tap='):
                        cost_payment["tap"] = arg.lower().split('=')[1] == 'true'
                    else:
                        color, amt = arg.split('=')
                        cost_payment["mana"][color.upper()] = int(amt)

            return {
                'type': 'ACTIVATE_ABILITY',
                'seq_num': this_seq_num,
                'source_id': permanent,
                'ability_index': ability_index,
                'targets': targets,
                'cost_payment': cost_payment
            }

        elif command == 'attack':
            attackers = []
            for creature in args[1:]:
                attackers.append({
                    'creature_id': creature,
                    'target': client_state['opponent_id']
                })
            return {
                'type': 'DECLARE_ATTACKERS',
                'seq_num': this_seq_num,
                'attackers': attackers
            }

        elif command == 'block':
            blockers = []
            for i in range(1, len(args), 2):
                if i + 1 < len(args):
                    blockers.append({
                        'creature_id': args[i],
                        'blocking_id': args[i + 1]
                    })
            return {
                'type': 'DECLARE_BLOCKERS',
                'seq_num': this_seq_num,
                'blockers': blockers
            }

        elif command == 'order':
            attacker = args[1]
            block_order = args[2:]
            return {
                'type': 'ASSIGN_DAMAGE_ORDER',
                'seq_num': this_seq_num,
                'attacker_id': attacker,
                'blocker_order': block_order
            }

        elif command == 'concede':
            return {
                'type': 'CONCEDE',
                'seq_num': this_seq_num,
                'player_id': client_state['player_id']
            }
        else:
            return None

    except IndexError:
        logging.info(f"Error in syntax for command '{command}'. Check your arguments.")
        return None
    except Exception as e: # FIXED: Changed exc to e
        logging.info(f"Error occurred when parsing command: {e}")
        return None

def recv_loop(sock):
    while True:
        try:
            header = sock.recv(4)
            if not header:
                logging.info("\n[SYSTEM] Server closed connection.")
                break
            length = struct.unpack("!I", header)[0]
            msg = json.loads(sock.recv(length).decode('utf-8'))

            if 'seq_num' in msg and msg.get("type") != "PONG":
                client_state['current_seq_num'] = msg['seq_num']

            if 'type' in msg:
                if msg['type'] == 'PLAYER_READY':
                    if 'player_id' in msg and not client_state['ready']:
                        client_state['player_id'] = msg['player_id']
                        client_state['ready'] = True
                if msg['type'] == 'GAME_STATE_UPDATE':
                    if 'life_totals' in msg['state']:
                        for player in msg['state']['life_totals'].keys():
                            if player != client_state['player_id']:
                                client_state['opponent_id'] = player

            if msg.get("type") != "PONG":
                logging.info(f"\n[SERVER]: {json.dumps(msg, indent=2)}\n> ")
        except Exception as e:
            logging.info(f"\n[SYSTEM] Connection lost: {e}")
            break

def ping_loop(sock):
    seq = 1
    while True:
        time.sleep(5)
        ping_pdu = {
            "type": "PING",
            "seq_num": seq,
            "timestamp": int(time.time() * 1000)
        }
        try:
            send_pdu(sock, ping_pdu)
            seq += 1
        except Exception:
            break

s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.connect(('127.0.0.1', 4444))

threading.Thread(target=recv_loop, args=(s,), daemon=True).start()
threading.Thread(target=ping_loop, args=(s,), daemon=True).start()

logging.info("Connected! Type 'help' for commands.")

while True:
    try:
        user_input = input("> ")
        if not user_input.strip():
            continue

        if user_input.strip().lower() == 'help':
            print("""
Available Commands:
  ready <player_name>
  pass
  mulligan [keep | redraw | confirm <card_id>...]
  play <land_id>
  cast <card_id> <target|none> <color=amount>...
  activate <card_id> <index> <target|none> ~ tap=true <color=amount>...
  attack <creature_id1> <creature_id2>...
  block <blocker_id> <attacker_id>...
  order <attacker_id> <blocker1> <blocker2>...
  concede
            """)
            continue

        pdu = parse_command(user_input)
        if pdu:
            if pdu.get('type') == 'PLAYER_READY' and client_state['ready']:
                logging.info('Player already in ready state.')
                continue
            send_pdu(s, pdu)

    except json.JSONDecodeError:
        logging.info("Invalid JSON format. Please try again.")
    except KeyboardInterrupt:
        logging.info("\nExiting client...")
        s.close()
        break
    except Exception as e:
        logging.info(f"Error sending data: {e}")
        break