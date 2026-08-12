from pydantic import ValidationError
from typing import cast
from schemas import *
from framer import send_framed_message
from game_state import PlayerState, GameState
from shared.util.logger_util import log_pdu_exchange

import json
import logging

def send_error_response(conn, seq_num: int, code: str, message: str, rejected_action=None):
    err_pdu = Error(
        type=PDUType.ERROR,
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
    if game_state.phase == "LOBBY":
        state_payload = {
            "phase": "LOBBY",
            "players_ready": len(game_state.players),
            "waiting_for": ["WAITING_FOR_PLAYERS"] if len(game_state.players) < 2 else []
        }
        pdu = GameStateUpdate(
            type=PDUType.GAME_STATE_UPDATE,
            seq_num=game_state.get_next_seq_num(),
            state=state_payload
        )

        log_pdu_exchange("S -> ALL", "lobby GAME_STATE_UPDATE", pdu.model_dump())
        payload = pdu.model_dump_json().encode('utf-8')

        for conn in list(game_state.player_sockets.values()):
            try:
                send_framed_message(conn, payload)
            except Exception: pass
    else:
        # Personalized state generator
        seq_num = game_state.get_next_seq_num()
        for p_id, conn in list(game_state.player_sockets.items()):
            pdu = GameStateUpdate(
                type=PDUType.GAME_STATE_UPDATE,
                seq_num=seq_num,
                state=game_state.to_in_game_state(viewer_id=p_id)
            )

            pdu_dict = pdu.model_dump()
            log_pdu_exchange(f"S -> {p_id}", "personalized GAME_STATE_UPDATE", pdu_dict)

            # Sync the expected sequence number specifically for Mulligan redraw validations
            if game_state.phase == "MULLIGAN":
                game_state.mulligan_expected_seq_nums[p_id] = seq_num

            try:
                send_framed_message(conn, json.dumps(pdu_dict).encode('utf-8'))
            except Exception: pass

def handle_player_ready(conn, payload: dict, game_state: GameState) -> bool:
    """
    Validates registration and deck limits.
    Returns True if game setup is triggered.
    """
    seq_num = payload.get("seq_num", 0)
    deck = payload.get("deck_list", [])

    if not isinstance(deck, list) or not (1 <= len(deck) <= 50):
        return send_error_response(
            conn,
            seq_num,
            "ILLEGAL_DECK",
            "Decks must contain between 1 and 50 card IDs.",
            payload
        )

    for card_id in deck:
        if deck.count(card_id) > 1:
            return send_error_response(
                conn,
                seq_num,
                'ILLEGAL_DECK',
                f'There are multiple instances of {card_id} in player\'s deck.',
                payload
            )
        split = str(card_id).rsplit('_', 1)
        if len(split) != 2:
            return send_error_response(
                conn,
                seq_num,
                'ILLEGAL_DECK',
                f'{split[0]} has no copy number (_0XX) attached.',
                payload
            )
        base_id = split[0]
        identifier = split[1]
        if len(identifier) != 3:
            return send_error_response(
                conn,
                seq_num,
                'ILLEGAL_DECK',
                f'Unable to recognize card {base_id}_{identifier}',
                payload
            )
        card = game_state.catalog.get(base_id, None)
        if card:
            info = cast(dict[str, Any], card)
            copy_number = int(split[1])
            copies = int(info.get('copies_in_set', 0))
            # Must follow _0XX convention
            if not 1 <= copy_number <= copies:
                return send_error_response(
                    conn,
                    seq_num,
                    'ILLEGAL_DECK',
                    f'Copy #{copy_number} of {base_id} is not present in the catalog.',
                    payload
                )
        if base_id == card_id or base_id not in game_state.catalog:
            return send_error_response(
                conn,
                seq_num,
                'ILLEGAL_DECK',
                f'Unable to recognize card {base_id}',
                payload
            )

    try:
        pdu = PlayerReady(**payload) # auto checks deck length
    except ValidationError as ve:
        return send_error_response(
            conn,
            payload.get("seq_num", 0),
            "ILLEGAL_ACTION",
            str(ve),
            rejected_action=payload
        )

    if conn in game_state.socket_to_player:
        existing_player_id = game_state.socket_to_player[conn]

        if game_state.phase == "LOBBY":
            # Prevent changing to an ID that is currently used by the OTHER player
            if pdu.player_id in game_state.players and pdu.player_id != existing_player_id:
                return send_error_response(
                    conn, pdu.seq_num, "DUPLICATE_ID",
                    f"Player ID '{pdu.player_id}' is already taken.", rejected_action=payload
                )

            # Overwrite existing player's deck and ID
            game_state.players.pop(existing_player_id)
            new_player = PlayerState(pdu.player_id, pdu.deck_list, game_state.catalog)
            game_state.players[pdu.player_id] = new_player
            game_state.socket_to_player[conn] = pdu.player_id

            if pdu.player_id != existing_player_id:
                game_state.player_sockets.pop(existing_player_id)
            game_state.player_sockets[pdu.player_id] = conn

            logging.info(f"[LOBBY] Player updated to '{pdu.player_id}' with deck size {len(pdu.deck_list)}")
            broadcast_game_state(game_state)
            return False
        else:
            return send_error_response(
                conn, seq_num, "ILLEGAL_ACTION",
                "Cannot send PLAYER_READY after game has started.", payload
            )

    # Check for duplicate ID on fresh connections
    if pdu.player_id in game_state.players:
        return send_error_response(
            conn,
            pdu.seq_num,
            "DUPLICATE_ID",
            f"Player ID '{pdu.player_id}' is already taken.",
            rejected_action=payload
        )

    # Register player and socket
    new_player = PlayerState(pdu.player_id, pdu.deck_list, game_state.catalog)
    game_state.players[pdu.player_id] = new_player
    game_state.socket_to_player[conn] = pdu.player_id
    game_state.player_sockets[pdu.player_id] = conn

    logging.info(f"[LOBBY] Registered player '{pdu.player_id}' with deck size {len(pdu.deck_list)}")

    # Check if both players are ready to initialize match
    if len(game_state.players) == 2:
        game_state.initialize_game()

        logging.info(f"[GAME] Both players ready. Active player chosen: '{game_state.active_player}'. Status -> MULLIGAN")
        broadcast_game_state(game_state)
        return True
    else:
        broadcast_game_state(game_state)
        return False

def handle_mulligan_choice(conn, payload: dict, game_state):
    """Processes hand keep or mulligan choices."""
    player_id = game_state.socket_to_player.get(conn)
    seq_num = payload.get("seq_num", 0)

    if not player_id or game_state.phase != "MULLIGAN":
        return send_error_response(
            conn,
            seq_num,
            "ILLEGAL_ACTION",
            "Mulligan choice sent outside mulligan phase.",
            rejected_action=payload
        )

    # Enforce MULLIGAN_CHOICE sequence numbers dynamically
    expected_seq = game_state.mulligan_expected_seq_nums.get(player_id)
    if seq_num != expected_seq:
        return send_error_response(
            conn,
            game_state.get_next_seq_num(),
            "STALE_ACTION",
            f"Priority token mismatch. Expected {expected_seq}, got {seq_num}.",
            rejected_action=payload
        )

    try:
        pdu = MulliganChoice(**payload)
    except ValidationError as ve:
        return send_error_response(
            conn,
            seq_num,
            "ILLEGAL_ACTION",
            str(ve),
            rejected_action=payload
        )

    player = game_state.players[player_id]

    if player.has_kept_hand:
        return send_error_response(
            conn,
            pdu.seq_num,
            "ILLEGAL_ACTION",
            "Player has already kept their hand.",
            payload
        )

    if pdu.keep:
        # Validate cards_to_bottom count matches mulligan_count
        if len(pdu.cards_to_bottom) != player.mulligan_count or not all(c in player.hand for c in pdu.cards_to_bottom):
            return send_error_response(
                conn,
                pdu.seq_num,
                "ILLEGAL_ACTION",
                f"Expected {player.mulligan_count} cards to bottom, got {len(pdu.cards_to_bottom)}",
                rejected_action=payload
            )

        # Remove cards put on bottom from hand to library
        for c_id in pdu.cards_to_bottom:
            if c_id in player.hand:
                player.hand.remove(c_id)
                player.library.append(c_id)
        player.has_kept_hand = True

        logging.info(f"[MULLIGAN] Player '{player_id}' kept their hand.")
    else:
        if pdu.cards_to_bottom:
            return send_error_response(
                conn,
                pdu.seq_num,
                "ILLEGAL_ACTION",
                "cards_to_bottom must be empty when mulliganing.",
                payload
            )

        # Reshuffle hand into library and redraw 7
        player.mulligan_count += 1
        player.reset_hand_to_library()
        player.draw_cards(7)
        logging.info(f"[MULLIGAN] Player '{player_id}' mulliganed (Count: {player.mulligan_count}). Redrew 7 cards.")

        # Send the new hand ONLY to the redrawing player
        seq_num_new = game_state.get_next_seq_num()

        # Update the tracked seq num for the subsequent check
        game_state.mulligan_expected_seq_nums[player_id] = seq_num_new

        pdu = GameStateUpdate(
            type=PDUType.GAME_STATE_UPDATE,
            seq_num=seq_num_new,
            state=game_state.to_in_game_state(viewer_id=player_id)
        )
        pdu_dict = pdu.model_dump()
        log_pdu_exchange(f"S -> {player_id}", "personalized GAME_STATE_UPDATE", pdu_dict)
        send_framed_message(conn, json.dumps(pdu_dict).encode('utf-8'))

    # Check if all mulligans resolved
    if game_state.is_all_mulligans_resolved():
        game_state.start_main_game()
        game_state.phase = "IN_GAME"
        logging.info("[GAME] All mulligans resolved. Match status -> IN_GAME")
        return None
    return None