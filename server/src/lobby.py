from pydantic import ValidationError
from schemas import PlayerReady, MulliganChoice, GameStateUpdate, Error
from framer import send_framed_message
from game_state import PlayerState

def send_error_response(conn, seq_num: int, code: str, message: str, rejected_action=None):
    err_pdu = Error(
        type="ERROR",
        seq_num=seq_num,
        code=code,
        message=message,
        rejected_action=rejected_action
    )
    send_framed_message(conn, err_pdu.model_dump_json().encode('utf-8'))

def broadcast_game_state(game_state):
    """
    Utility to broadcast GAME_STATE_UPDATE to all registered 
    player connections when a game state changes.
    """
    pdu = GameStateUpdate(
        type="GAME_STATE_UPDATE",
        seq_num=game_state.get_next_seq_num(),
        # if LOBBY, use default states
        # if MULLIGAN or IN_GAME, use p_id dict, containing
        # game summary of players
        state=game_state.get_lobby_state_dict() if game_state.phase == "LOBBY" else {
            "phase": game_state.phase,
            "active_player": game_state.active_player,
            "players": {
                p_id: {
                    "life": p.life,
                    "hand_size": len(p.hand),
                    "library_size": len(p.library),
                    "has_kept": p.has_kept_hand
                } for p_id, p in game_state.players.items()
            }
        }
    )
    payload = pdu.model_dump_json().encode('utf-8')
    # Sends to all clients (all sockets)
    for conn in game_state.player_sockets.values():
        send_framed_message(conn, payload)

def handle_player_ready(conn, payload: dict, game_state) -> bool:
    """
    Validates registration and deck limits. 
    Returns True if game setup is triggered.
    """
    try:
        pdu = PlayerReady(**payload) # auto checks deck length
    except ValidationError as ve:
        send_error_response(conn, payload.get("seq_num", 0), "INVALID_PDU", str(ve), rejected_action=payload)
        return False

    # Check for duplicate ID
    if pdu.player_id in game_state.players:
        send_error_response(conn, pdu.seq_num, "DUPLICATE_ID", f"Player ID '{pdu.player_id}' is already taken.", rejected_action=payload)
        return False

    # Register player and socket
    new_player = PlayerState(pdu.player_id, pdu.deck_list)
    game_state.players[pdu.player_id] = new_player
    game_state.socket_to_player[conn] = pdu.player_id
    game_state.player_sockets[pdu.player_id] = conn

    print(f"[LOBBY] Registered player '{pdu.player_id}' with deck size {len(pdu.deck_list)}")

    # Check if both players are ready to initialize match
    if len(game_state.players) == 2:
        game_state.initialize_game()
        print(f"[GAME] Both players ready. Active player chosen: '{game_state.active_player}'. Status -> MULLIGAN")
        broadcast_game_state(game_state)
        return True
    else:
        broadcast_game_state(game_state)
        return False

def handle_mulligan_choice(conn, payload: dict, game_state):
    """Processes hand keep or mulligan choices."""
    player_id = game_state.socket_to_player.get(conn)
    if not player_id or game_state.phase != "MULLIGAN":
        send_error_response(conn, payload.get("seq_num", 0), "ILLEGAL_ACTION", "Mulligan choice sent outside mulligan phase.", rejected_action=payload)
        return

    try:
        pdu = MulliganChoice(**payload)
    except ValidationError as ve:
        send_error_response(conn, payload.get("seq_num", 0), "INVALID_PDU", str(ve), rejected_action=payload)
        return

    player = game_state.players[player_id]

    if pdu.keep:
        # Validate cards_to_bottom count matches mulligan_count
        if len(pdu.cards_to_bottom) != player.mulligan_count:
            send_error_response(
                conn, pdu.seq_num, "INVALID_MULLIGAN_BOTTOM",
                f"Expected {player.mulligan_count} cards to bottom, got {len(pdu.cards_to_bottom)}",
                rejected_action=payload
            )
            return

        # Remove cards put on bottom from hand to library
        for c_id in pdu.cards_to_bottom:
            if c_id in player.hand:
                player.hand.remove(c_id)
                player.library.append(c_id)

        player.has_kept_hand = True
        print(f"[MULLIGAN] Player '{player_id}' kept their hand.")
    else:
        # Reshuffle hand into library and redraw 7
        player.mulligan_count += 1
        player.reset_hand_to_library()
        player.draw_cards(7)
        print(f"[MULLIGAN] Player '{player_id}' mulliganed (Count: {player.mulligan_count}). Redrew 7 cards.")

    # Check if all mulligans resolved
    if game_state.is_all_mulligans_resolved():
        game_state.phase = "IN_GAME"
        print("[GAME] All mulligans resolved. Match status -> IN_GAME")

    broadcast_game_state(game_state)