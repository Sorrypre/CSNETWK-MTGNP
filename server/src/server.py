import sys
import os
import threading
import socket
import json
from pydantic import ValidationError
from schemas import (
    Error, Ping, Pong, PDUType, CastSpell, PlayLand, Discard, 
    TriggerOrderResponse, GameOver, DeclareAttackers, DeclareBlockers, AssignDamageOrder
)
from framer import read_framed_message, send_framed_message
from game_state import GameState
from game_engine import GameEngine
from lobby import *
import logging

# Track two levels up from client.py to find the project root
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, ROOT)

from shared.util.logger_util import setup_app_logging

setup_app_logging(__file__)

PORT, MAX_CLIENTS = 4444, 2
active_connections = []
connections_lock = threading.Lock()
game_lock = threading.Lock() # Lock for synchronizing access to game state
game_state = GameState() # Initialize global game state instance
game_engine = GameEngine() # Initialize global game engine instance
priority_timer = {} # Track priority timers for each player

def process_engine_result(result, conn):
    """
    Helper function to process and route PDUs returned by GameEngine methods.
    """
    if isinstance(result, Error):
        send_framed_message(conn, result.model_dump_json().encode('utf-8'))
        return

    if not result:
        return

    for pdu in result:
        payload_bytes = pdu.model_dump_json().encode('utf-8')

        # Broadcast turn/phase transitions, combat results, stack events, and game overs
        if pdu.type in [
            PDUType.STACK_PUSH, 
            PDUType.STACK_RESOLVE, 
            PDUType.PHASE_TRANSITION, 
            PDUType.COMBAT_DAMAGE_RESULT,
            PDUType.GAME_OVER,
            PDUType.GAME_STATE_UPDATE
        ]:
            for client_conn in game_state.player_sockets.values():
                send_framed_message(client_conn, payload_bytes)

            if pdu.type == PDUType.GAME_OVER:
                game_state.reset_game_state()
                broadcast_game_state(game_state)

        #PERSONLIZED GAME_STATE_UPDATE sends a unique view to each player
        elif pdu.type == PDUType.GAME_STATE_UPDATE:
            for p_id, client_conn in game_state.player_sockets.items():
                #Masks the opponent's hand
                personalized_state = game_state.to_in_game_state(viewer_id=p_id)

                # Swap the generic state out for the personalized one
                pdu.state = personalized_state

                personalized_bytes = pdu.model_dump_json().encode('utf-8')
                send_framed_message(client_conn, personalized_bytes)

        # PRIORITY_GRANT sends ONLY to the specific priority holder
        elif pdu.type == PDUType.PRIORITY_GRANT:
            active_conn = game_state.player_sockets.get(pdu.player_id)
            if active_conn:
                send_framed_message(active_conn, payload_bytes)

                # Start a 60 second timer for the player to respond
                timer = threading.Timer(60.0, priority_timeout, args=[pdu.player_id])
                priority_timer[pdu.player_id] = timer
                timer.start()

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
            pdu = GameOver(
                type=PDUType.GAME_OVER,
                seq_num=game_state.get_next_seq_num(),
                winner_id=winner_id,
                loser_id=p_id,
                reason="DISCONNECT"
            )
            try: send_framed_message(winner_conn, pdu.model_dump_json().encode('utf-8'))
            except Exception: pass

        # Reset the game state after a player disconnects
        game_state.reset_game_state()
        broadcast_game_state(game_state)
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
                send_error_response(conn, seq_num=0, code="INVALID_JSON", message=str(e))
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

            with game_lock:
                validate_sequence = game_engine.validate_action(msg_type, seq_num, game_state)
                if validate_sequence:
                    stale_error, new_grant = validate_sequence
                    send_framed_message(conn, stale_error.model_dump_json().encode('utf-8'))
                    send_framed_message(conn, new_grant.model_dump_json().encode('utf-8'))
                    continue # discard illegal actions

                # Cancel any existing priority timer for this player
                player_id = game_state.socket_to_player.get(conn)
                if player_id in priority_timer:
                    priority_timer[player_id].cancel()

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
                                "ILLEGAL_ACTION",
                                str(ve))

                    case PDUType.PLAYER_READY:
                        handle_player_ready(conn, message, game_state)

                    case PDUType.MULLIGAN_CHOICE:
                        handle_mulligan_choice(conn, message, game_state)

                        if game_state.phase == "IN_GAME" and game_state.current_step == "UNTAP":
                            #Advances to UPKEEP and generates the PRIORITY_GRANT
                            result = game_engine.advance_phase(game_state)
                            process_engine_result(result, conn)

                    case PDUType.PRIORITY_PASS:
                        if game_state.phase != "IN_GAME":
                            send_error_response(conn, seq_num, "WRONG_PHASE", "Game has not started yet.")
                            continue

                        result = game_engine.handle_priority_pass(game_state)
                        process_engine_result(result, conn)

                    case PDUType.DECLARE_ATTACKERS:
                        if game_state.phase != "IN_GAME":
                            send_error_response(conn, seq_num, "WRONG_PHASE", "The players are not ingame.")
                            continue
                        try:
                            attack_pdu = DeclareAttackers(**message)
                        except ValidationError as ve:
                            send_error_response(conn, seq_num, "ILLEGAL_ACTION", str(ve))
                            continue
                        
                        player_id = game_state.socket_to_player.get(conn)
                        result = game_engine.handle_declare_attackers(player_id, attack_pdu, game_state)
                        process_engine_result(result, conn)

                    case PDUType.DECLARE_BLOCKERS:
                        if game_state.phase != "IN_GAME":
                            send_error_response(conn, seq_num, "WRONG_PHASE", "The players are not ingame.")
                            continue
                        try:
                            block_pdu = DeclareBlockers(**message)
                        except ValidationError as ve:
                            send_error_response(conn, seq_num, "ILLEGAL_ACTION", str(ve))
                            continue

                        player_id = game_state.socket_to_player.get(conn)
                        result = game_engine.handle_declare_blockers(player_id, block_pdu, game_state)
                        process_engine_result(result, conn)

                    case PDUType.ASSIGN_DAMAGE_ORDER:
                        if game_state.phase != "IN_GAME":
                            send_error_response(conn, seq_num, "WRONG_PHASE", "The players are not ingame.")
                            continue
                        try:
                            damage_order_pdu = AssignDamageOrder(**message)
                        except ValidationError as ve:
                            send_error_response(conn, seq_num, "ILLEGAL_ACTION", str(ve))
                            continue

                        player_id = game_state.socket_to_player.get(conn)
                        result = game_engine.handle_assign_damage_order(player_id, damage_order_pdu, game_state)
                        process_engine_result(result, conn)

                    case PDUType.CAST_SPELL:
                        if game_state.phase != "IN_GAME":
                            send_error_response(conn, seq_num, "WRONG_PHASE", "Game has not started yet.")
                            continue
                        player_id = game_state.socket_to_player.get(conn)

                        try:
                            spell_pdu = CastSpell(**message)
                        except ValidationError as ve:
                            send_error_response(conn, seq_num, "ILLEGAL_ACTION", str(ve))
                            continue

                        #Sent for processing
                        result = game_engine.handle_cast_spell(player_id, spell_pdu, game_state)
                        process_engine_result(result, conn)

                    case PDUType.PLAY_LAND:
                        if game_state.phase != "IN_GAME":
                            send_error_response(conn, seq_num, "WRONG_PHASE", "The players are not ingame.")
                            continue
                        try:
                            land_pdu = PlayLand(**message)
                        except ValidationError as ve:
                            send_error_response(conn, seq_num, "ILLEGAL_ACTION", str(ve))
                            continue
                        player_id = game_state.socket_to_player.get(conn)
                        result = game_engine.play_land(player_id, land_pdu, game_state)
                        process_engine_result(result, conn)

                    case PDUType.DISCARD:
                        if game_state.phase != "IN_GAME":
                            send_error_response(conn, seq_num, "WRONG_PHASE", "The players are not ingame.")
                            continue
                        try:
                            discard_pdu = Discard(**message)
                        except ValidationError as ve:
                            send_error_response(conn, seq_num, "ILLEGAL_ACTION", str(ve))
                            continue
                        player_id = game_state.socket_to_player.get(conn)
                        result = game_engine.cleanup_discard(player_id, discard_pdu, game_state)
                        process_engine_result(result, conn)

                    case PDUType.TRIGGER_ORDER_RESPONSE:
                        if game_state.phase != "IN_GAME":
                            send_error_response(conn, seq_num, "WRONG_PHASE", "The players are not ingame.")
                            continue
                        try:
                            trigger_pdu = TriggerOrderResponse(**message)
                        except ValidationError as ve:
                            send_error_response(conn, seq_num, "ILLEGAL_ACTION", str(ve))
                            continue
                        player_id = game_state.socket_to_player.get(conn)
                        result = game_engine.trigger_order_response(player_id, trigger_pdu, game_state)
                        process_engine_result(result, conn)

                    case PDUType.CONCEDE:
                        #Get the conceding player's ID
                        player_id = game_state.socket_to_player.get(conn)

                        #Validate that the player is in the game state
                        if player_id not in game_state.players:
                            continue

                        players = list(game_state.players.keys())
                        winner_id = players[1] if player_id == players[0] else players[0]

                        game_over_pdu = GameOver(
                            type=PDUType.GAME_OVER,
                            seq_num=game_state.get_next_seq_num(),
                            winner_id=winner_id,
                            loser_id=player_id,
                            reason="CONCEDE"
                        )

                        payload_bytes = game_over_pdu.model_dump_json().encode('utf-8')
                        for client_conn in game_state.player_sockets.values():
                            send_framed_message(client_conn, payload_bytes)

                        #Reset the game state after a player concedes
                        game_state.reset_game_state()

                        #Broadcast the updated game state to all players in the lobby
                        broadcast_game_state(game_state)
                    case _:
                        send_error_response(
                            conn,
                            seq_num,
                            "UNKNOWN_TYPE",
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

def priority_timeout(timed_out_player_id: str):
    """"
    Runs when a player fails to respond within priority timelimit.
    """
    with game_lock:
        #Checks if the player is still in the game and has priority
        if game_state.phase != "IN_GAME":
            return

        logging.warning(f"[TIMEOUT] Player {timed_out_player_id} has failed to respond in time.")
        priority_timer.pop(timed_out_player_id, None)

        players = list(game_state.players.keys())
        winner = players[1] if timed_out_player_id == players[0] else players[0]

        game_over_pdu = GameOver(
            type=PDUType.GAME_OVER,
            seq_num=game_state.get_next_seq_num(),
            winner_id=winner,
            loser_id=timed_out_player_id,
            reason="DISCONNECT"
        )

        payload = game_over_pdu.model_dump_json().encode('utf-8')
        for client_conn in game_state.player_sockets.values():
            send_framed_message(client_conn, payload)

        #Reset the game state after a player timeouts
        game_state.reset_game_state()

        #Broadcast the updated game state to all players in the lobby
        broadcast_game_state(game_state)
        
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
                        code="ILLEGAL_ACTION",
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