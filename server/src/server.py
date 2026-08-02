import sys
import threading
import socket
import json
from pydantic import ValidationError
from schemas import Error, Ping, Pong
from framer import read_framed_message, send_framed_message
from game_state import GameState
from lobby import handle_player_ready, handle_mulligan_choice

PORT = 4444
MAX_CLIENTS = 2
active_connections = []
connections_lock = threading.Lock()

# Initialize global game state instance
game_state = GameState()

def send_error(conn, seq_num, code, message, rejected_action=None):
    """Utility helper to send structured Error PDUs back to the client."""
    err_pdu = Error(
        type="ERROR",
        seq_num=seq_num,
        code=code,
        message=message,
        rejected_action=rejected_action
    )
    send_framed_message(conn, err_pdu.model_dump_json().encode('utf-8'))

def receive(conn, addr):
    print(f'Client connected from {addr}')
    try:
        while True:
            # 1. Read byte-framed message
            try:
                raw_payload = read_framed_message(conn)
            except ValueError as e:
                send_error(conn, seq_num=0, code="PAYLOAD_TOO_LARGE", message=str(e))
                break
            except ConnectionError:
                break

            # 2. Parse JSON string
            try:
                message = json.loads(raw_payload.decode('utf-8'))
            except json.JSONDecodeError:
                send_error(conn, seq_num=0, code="INVALID_JSON", message="Payload is not valid JSON")
                continue

            msg_type = message.get("type")
            seq_num = message.get("seq_num", 0)

            # 3. Route actions
            if msg_type == "PING":
                try:
                    ping_pdu = Ping(**message)
                    pong_pdu = Pong(type="PONG", seq_num=ping_pdu.seq_num, timestamp=ping_pdu.timestamp)
                    send_framed_message(conn, pong_pdu.model_dump_json().encode('utf-8'))
                except ValidationError as ve:
                    send_error(conn, seq_num=seq_num, code="MALFORMED_PING", message=str(ve))
                continue

            elif msg_type == "PLAYER_READY":
                handle_player_ready(conn, message, game_state)

            elif msg_type == "MULLIGAN_CHOICE":
                handle_mulligan_choice(conn, message, game_state)

            else:
                # Unhandled or invalid message types
                print(f'Received from {addr}: {message}')

    finally:
        with connections_lock:
            if conn in active_connections:
                active_connections.remove(conn)
            
            # Clean up game state mappings if player disconnects
            p_id = game_state.socket_to_player.pop(conn, None)
            if p_id:
                game_state.player_sockets.pop(p_id, None)
                game_state.players.pop(p_id, None)
                print(f"[LOBBY] Player '{p_id}' removed due to disconnection.")

        conn.close()
        print(f'Connection closed for {addr}')

PORT = 4444
listening = False
server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

try:
    server_socket.bind(('', PORT))
    listening = True
    print(f'Server started at localhost on port {PORT}')
except socket.error:
    print('Unable to start server')

try:
    server_socket.listen()
    while listening:
        conn, addr = server_socket.accept()
        
        with connections_lock:
            if len(active_connections) >= MAX_CLIENTS:
                print(f'Rejected extra connection from {addr}')
                send_error(conn, seq_num=0, code="SERVER_FULL", message="Server active client limit reached")
                conn.close()
                continue

            active_connections.append(conn)

        thread = threading.Thread(target=receive, args=(conn, addr))
        thread.start()
except KeyboardInterrupt:
    print('Server stopped via Ctrl+C')
    server_socket.close()
    sys.exit()