import sys
import threading
import socket
import json
from schemas import *
from pydantic import ValidationError
from schemas import Error, Ping, Pong, PDUType
from framer import read_framed_message, send_framed_message
from game_state import GameState
from game_engine import GameEngine
from lobby import *
import logging
import os

# Track two levels up from client.py to find the project root
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, ROOT)

from shared.util.logger_util import setup_app_logging

setup_app_logging(__file__)

PORT, MAX_CLIENTS = 4444, 2
active_connections = []
connections_lock = threading.Lock()
game_state = GameState() # Initialize global game state instance
game_engine = GameEngine() # Initialize global game engine instance

def handle_disconnect(conn, p_id):
    """
    Disconnects the client from the game
    """
    if not p_id: return
    game_state.player_sockets.pop(p_id, None)
    game_state.players.pop(p_id, None)

    if game_state.phase in ["MULLIGAN", "IN_GAME"] and game_state.players:
        winner_id = list(game_state.players.keys())[0]
        winner_conn = game_state.player_sockets.get(winner_id)
        if winner_conn:
            pdu = GameOver(seq_num=game_state.get_next_seq_num(), winner_id=winner_id, loser_id=p_id, reason="DISCONNECT")
            try: send_framed_message(winner_conn, pdu.model_dump_json().encode('utf-8'))
            except Exception: pass
        game_state.phase = "FINISHED"
    elif game_state.phase == "LOBBY":
        broadcast_game_state(game_state)

def receive(conn, addr):
    """
    Receives payload to inspect the big-endian prefix.
    If payload is >65535, catches ValueError and closes thread
    """
    conn.settimeout(10.0)
    
    print(f'Client connected from {addr}')
    try:
        while True:
            # 1. Read byte-framed message
            try:
                raw_payload = read_framed_message(conn)
            except TimeoutError:
                send_error_response(conn, 0, "TIMEOUT", "Connection dropped due to 10s inactivity limit.")
                break
            except ValueError as e:
                send_error_response(conn, seq_num=0, code="PAYLOAD_TOO_LARGE", message=str(e))
                break
            except ConnectionError:
                break

            # 2. Parse JSON string
            try:
                message = json.loads(raw_payload.decode('utf-8'))
                if not isinstance(message, dict): raise ValueError()
            except Exception:
                send_error_response(conn, seq_num=0, code="INVALID_JSON", message="Payload is not valid JSON")
                continue

            msg_type, seq_num = message.get("type"), message.get("seq_num", 0)

            validate_sequence = game_engine.validate_action(msg_type, seq_num, game_state)
            if validate_sequence:
                stale_error, new_grant = validate_sequence
                send_framed_message(conn, stale_error.model_dump_json().encode('utf-8'))
                send_framed_message(conn, new_grant.model_dump_json().encode('utf-8'))
                continue #discard illegal actions

            # 3. Route actions
            match msg_type:
                case PDUType.PING:
                    try:
                        ping = Ping(**message)
                        pong = Pong(seq_num=ping.seq_num, timestamp=ping.timestamp)
                        send_framed_message(conn, pong.model_dump_json().encode('utf-8'))
                    except ValidationError as ve:
                        send_error_response(
                            conn, 
                            seq_num, 
                            "MALFORMED_PING", 
                            str(ve))

                case PDUType.PLAYER_READY:
                    handle_player_ready(conn, message, game_state)

                case PDUType.MULLIGAN_CHOICE:
                    handle_mulligan_choice(conn, message, game_state)

                case PDUType.PRIORITY_PASS:
                    if game_state.phase != "IN_GAME":
                        send_error_response(conn, seq_num, "WRONG_PHASE", "Game has not started yet.")
                        continue

                    new_grant = game_engine.handle_priority_pass(game_state)

                    if new_grant:
                        active_conn = game_state.player_sockets.get(new_grant.player_id)
                        send_framed_message(active_conn, new_grant.model_dump_json().encode('utf-8'))

                case _:
                    send_error_response(
                        conn, 
                        seq_num, 
                        "UNRECOGNIZED_PDU", 
                        f"PDU '{msg_type}' unhandled.", 
                        rejected_action=message)

            print(f'Received from {addr}: {message}')
    finally:
        with connections_lock:
            if conn in active_connections:
                active_connections.remove(conn)
            
            # Clean up game state mappings if player disconnects
            p_id = game_state.socket_to_player.pop(conn, None)
            handle_disconnect(conn, p_id)

        try:
            conn.close()
            print(f'Connection closed for {addr}')
        except Exception: pass
        
def main():
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listening = False

    try:
        server_socket.bind(('', PORT))
        listening = True
        print(f'MTGNP Server started at localhost on port {PORT}')
    except socket.error:
        print('Unable to start server')

    try:
        server_socket.listen()
        while listening:
            conn, addr = server_socket.accept()

            with connections_lock:
                if len(active_connections) >= MAX_CLIENTS:
                    print(f'Rejected extra connection from {addr}')
                    send_error_response(
                        conn, 
                        seq_num=0, 
                        code="SERVER_FULL", 
                        message="Server active client limit reached"
                    )
                    conn.close()
                    continue
                active_connections.append(conn)
            thread = threading.Thread(target=receive, args=(conn, addr), daemon=True)
            thread.start()
    except KeyboardInterrupt:
        print('Server stopped via Ctrl+C')
        server_socket.close()
        sys.exit()

if __name__ == "__main__":
    main()