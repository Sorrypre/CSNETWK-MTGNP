from schemas import *
from game_state import GameState
import logging

class GameEngine:
    def validate_action(self, pdu_type: str, client_seq_num: int, game_state: GameState) -> tuple[Error, PriorityGrant] | None:
        """
        Sequence number enforcer.
        Returns Error PDU if client sequence number is stale, otherwise returns None.
        """
        if pdu_type in [PDUType.PING, PDUType.CONCEDE, PDUType.PLAYER_READY, PDUType.MULLIGAN_CHOICE]:
            return None # These actions are exempt from sequence number validation

        #Compare client with global game state
        if client_seq_num != game_state.seq_num:
            error_pdu = Error(
                type=PDUType.ERROR,
                seq_num=game_state.get_next_seq_num(),
                code="STALE_ACTION",
                message=f"Priority token mismatch. Expected {game_state.seq_num - 1}, got {client_seq_num}.",
                rejected_action=None
            )

            grant_pdu = PriorityGrant(
                type=PDUType.PRIORITY_GRANT,
                seq_num=game_state.get_next_seq_num(),
                player_id=game_state.priority_player,
                time_limit_ms=60000
            )
            return error_pdu, grant_pdu
        return None

    def handle_priority_pass(self, game_state: GameState) -> PriorityGrant | None:
        """"
        Processes a valid PRIORITY_PASS action.
        Returns a PriorityGrant PDU if priority swaps, or None if phase advances/stack resolves.
        """
        logging.info(f"Player {game_state.priority_player} passed priority.")

        game_state.passes_in_a_row += 1

        if game_state.passes_in_a_row < 2:
            #Swap priority holder
            players = list(game_state.players.keys())
            other_player = players[1] if game_state.priority_player == players[0] else players[0]
            game_state.priority_player = other_player

            return PriorityGrant(
                type=PDUType.PRIORITY_GRANT,
                seq_num=game_state.get_next_seq_num(),
                player_id=game_state.priority_player,
                time_limit_ms=60000
            )
        #Both players passed in a row, resolve the stack or advance the phase
        else:
            game_state.passes_in_a_row = 0
            if not game_state.stack:
                #ADVANCE PHASE
                pass
            else:
                #RESOLVE STACK
                pass
        return None
