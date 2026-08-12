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
    'mulligan_decided': False,
    'current_seq_num': 0,
    'player_id': None,
    'opponent_id': None
}

def parse_command(inp: str) -> dict:
    args = inp.strip().split()
    this_seq_num = client_state['current_seq_num']
    if not args:
        return None
    command = args[0].lower()
    try:
        # ready <player_name>
        if command == 'ready':
             deck = [f"mountain_{i:03d}" for i in range(1, 11)] + \
                [f"goblin_guide_{i:03d}" for i in range(1, 5)] + \
                [f"monastery_swiftspear_{i:03d}" for i in range(1, 5)] + \
                [f"phantasmal_bear_{i:03d}" for i in range(1, 5)]
             name = args[1]
             return {
                 'type': 'PLAYER_READY',
                 'seq_num': this_seq_num,
                 'player_id': name,
                 'deck_list': deck
             }
        # pass
        if command == 'pass':
            return {
                'type': 'PRIORITY_PASS',
                'seq_num': this_seq_num
            }
        # mulligan [keep (default) | take <card_id1> [<card_id2> ...]]
        elif command == 'mulligan':
            decision = None if len(args) == 1 else args[1].lower()
            cards_to_bottom = []
            if not decision or decision == 'keep':
                keep = True
            elif decision == 'take':
                keep = True if len(args) == 2 else False
                if len(args) != 2:
                    cards_to_bottom = args[2:]
            else:
                return None
            return {
                'type': 'MULLIGAN_CHOICE',
                'seq_num': this_seq_num,
                'keep': keep,
                'cards_to_bottom': cards_to_bottom
            } if not client_state['mulligan_decided'] else None
        # play <card_id>
        elif command == 'play':
            return {
                'type': 'PLAY_LAND',
                'seq_num': this_seq_num,
                'card_id': args[1]
            }
        # cast <card_id> <target> <mana_color1=amount1> [<mana_color2=amount2> ...]
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
        # activate <permanent_id> [targets] ~ [mana_payment]
        elif command == 'activate':
            permanent = args[1]
            targets = []
            mana_payment = {}
            paying = False
            for arg in args[2:]:
                if arg == '~':
                    paying = True
                    continue
                if not paying:
                    if arg.lower() != 'none':
                        targets.append(arg)
                else:
                    color, amt = arg.split('=')
                    mana_payment[color.upper()] = int(amt)
            return {
                'type': 'ACTIVATE_ABILITY',
                'seq_num': this_seq_num,
                'source_id': permanent,
                'targets': targets,
                'mana_payment': mana_payment
            }
        # attack <creature1> [<creature2> ...]
        elif command == 'attack':
            attackers = []
            for creature in args[2:]:
                attackers.append({
                    'creature_id': creature,
                    'target': client_state['opponent_id']
                })
            return {
                'type': 'DECLARE_ATTACKERS',
                'seq_num': this_seq_num,
                'attackers': attackers
            }
        # block <blocker1> <attacker1> [<blocker2> <attacker2> ...]
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
        # order <attacker_id> <blocker1> [<blocker2> ...]
        elif command == 'order':
            attacker = args[1]
            block_order = args[2:]
            return {
                'type': 'ASSIGN_DAMAGE_ORDER',
                'seq_num': this_seq_num,
                'attacker_id': attacker,
                'blocker_order': block_order
            }
        # concede
        elif command == 'concede':
            return {
                'type': 'CONCEDE',
                'seq_num': this_seq_num,
                'player_id': client_state['player_id']
            }
        else:
            return None
    except IndexError:
        logging.info(f"Error in syntax for command '{command}'")
        return None
    except Exception as exc:
        logging.info(f'Error occurred when parsing command: {e}')
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
            if 'seq_num' in msg:
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
                if msg['type'] == 'MULLIGAN_CHOICE':
                    client_state['mulligan_decided'] = True

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
            pdu = parse_command(user_input)
            if pdu:
                #msg_dict = json.loads(user_input)
                # Intercept on duplicate ready
                if 'type' in pdu and pdu['type'] == 'PLAYER_READY' and client_state['ready']:
                    logging.info('Player already in ready state.')
                    continue
                payload = json.dumps(pdu).encode('utf-8')
                s.sendall(struct.pack("!I", len(payload)) + payload)
    except json.JSONDecodeError:
        logging.info("Invalid JSON format. Please try again.")
    except Exception as e:
        logging.info(f"Error sending data: {e}")
        break