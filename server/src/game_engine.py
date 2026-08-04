from typing import List
from pydantic import BaseModel
from schemas import *
from game_state import GameState
import logging
from enum import StrEnum

#Centralized Game Phases
class InGamePhase(StrEnum):
    UNTAP = "UNTAP"
    UPKEEP = "UPKEEP"
    DRAW = "DRAW"
    PRE_COMBAT_MAIN = "PRECOMBAT_MAIN"
    BEGIN_COMBAT = "BEGIN_COMBAT"
    DECLARE_ATTACKERS = "DECLARE_ATTACKERS"
    DECLARE_BLOCKERS = "DECLARE_BLOCKERS"
    COMBAT_DAMAGE = "COMBAT_DAMAGE"
    END_OF_COMBAT = "END_OF_COMBAT"
    POST_COMBAT_MAIN = "POSTCOMBAT_MAIN"
    END_STEP = "END_STEP"
    CLEANUP = "CLEANUP"

class GameEngine:
    def validate_action(self, pdu_type: str, client_seq_num: int, game_state: GameState) -> tuple[Error, PriorityGrant] | None:
        """
        Sequence number enforcer.
        Returns Error PDU if client sequence number is stale, otherwise returns None.
        """
        logging.debug(f"[ENGINE RECEIVE] Validating PDU Type: {pdu_type} with seq_num: {client_seq_num}")

        if pdu_type in [PDUType.PING, PDUType.CONCEDE, PDUType.PLAYER_READY, PDUType.MULLIGAN_CHOICE]:
            return None # These actions are exempt from sequence number validation

        # Compare client with global game state
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
            logging.debug(f"[ENGINE SEND] Stale action detected. Generated PDUs: ERROR, PRIORITY_GRANT")
            return error_pdu, grant_pdu

        return None

    def handle_priority_pass(self, game_state: GameState) -> List[BaseModel]:
        """
        Processes a valid PRIORITY_PASS action.
        Returns a list of PDUs to be routed by the server.
        """
        logging.debug(f"[ENGINE RECEIVE] Processing PRIORITY_PASS from {game_state.priority_player}")

        game_state.passes_in_a_row += 1

        if game_state.passes_in_a_row < 2:
            # Swap priority holder
            players = list(game_state.players.keys())
            other_player = players[1] if game_state.priority_player == players[0] else players[0]
            game_state.priority_player = other_player

            grant_pdu = PriorityGrant(
                type=PDUType.PRIORITY_GRANT,
                seq_num=game_state.get_next_seq_num(),
                player_id=game_state.priority_player,
                time_limit_ms=60000
            )
            logging.debug(f"[ENGINE SEND] Priority swapped. Generated PDU: PRIORITY_GRANT for {game_state.priority_player}")
            return [grant_pdu]

        # Both players passed in a row, resolve the stack or advance the phase
        else:
            game_state.passes_in_a_row = 0
            if not game_state.stack:
                logging.debug("[ENGINE STATE] Stack empty. Triggering phase advancement.")
                return self.advance_phase(game_state)
            else:
                logging.debug("[ENGINE STATE] Stack populated. Triggering stack resolution.")
                return self.resolve_stack(game_state)

    def advance_phase(self, game_state: GameState) -> List[BaseModel]:
        """
        Advances the game phase to the next phase in the cycle
        """
        phase_order = [
            InGamePhase.UNTAP, InGamePhase.UPKEEP, InGamePhase.DRAW,
            InGamePhase.PRE_COMBAT_MAIN, InGamePhase.BEGIN_COMBAT,
            InGamePhase.DECLARE_ATTACKERS, InGamePhase.DECLARE_BLOCKERS,
            InGamePhase.COMBAT_DAMAGE, InGamePhase.END_OF_COMBAT,
            InGamePhase.POST_COMBAT_MAIN, InGamePhase.END_STEP,
            InGamePhase.CLEANUP
        ]

        current_phase_index = phase_order.index(game_state.current_step)

        #If on CLEANUP phase, advance turn and reset to UNTAP
        if current_phase_index == len(phase_order) - 1:
            next_step = InGamePhase.UNTAP
            game_state.turn_number += 1
            players = list(game_state.players.keys())
            game_state.active_player = players[1] if game_state.active_player == players[0] else players[0] #Switch active player
        else:
            next_step = phase_order[current_phase_index + 1]

        old_step = game_state.current_step
        game_state.current_step = next_step
        logging.info(f"Phase advanced from {old_step} to {next_step}.")

        transition_pdu = PhaseTransition(
            type=PDUType.PHASE_TRANSITION,
            seq_num=game_state.get_next_seq_num(),
            from_phase=old_step,
            to_phase=next_step,
            active_player=game_state.active_player,
            turn=game_state.turn_number
        )

        if next_step in [InGamePhase.UNTAP, InGamePhase.CLEANUP]:
            #No priority granted during these phases
            game_state.priority_player = None
            logging.debug(f"[ENGINE SEND] Phase transition (No Priority). Generated PDU: PHASE_TRANSITION ({next_step})")
            return [transition_pdu]
        else:
            game_state.priority_player = game_state.active_player
            grant_pdu = PriorityGrant(
                type=PDUType.PRIORITY_GRANT,
                seq_num=game_state.get_next_seq_num(),
                player_id=game_state.priority_player,
                time_limit_ms=60000
            )
            logging.debug(f"[ENGINE SEND] Phase transition. Generated PDUs: PHASE_TRANSITION ({next_step}), PRIORITY_GRANT")
            return [transition_pdu, grant_pdu]

    def resolve_stack(self, game_state: GameState):
        """
        Pop the top item, applies effects, and re-grants priority
        """
        resolved_item = game_state.stack.pop()

        resolved_pdu = StackResolve(
            type=PDUType.STACK_RESOLVE,
            seq_num=game_state.get_next_seq_num(),
            stack_item_id=resolved_item["stack_item_id"],
            result="RESOLVED",
            state_changes=[]
        )

        # After resolving, grant priority to the active player
        game_state.priority_player = game_state.active_player

        grant_pdu = PriorityGrant(
            type=PDUType.PRIORITY_GRANT,
            seq_num=game_state.get_next_seq_num(),
            player_id=game_state.priority_player,
            time_limit_ms=60000
        )

        logging.info(f"Stack item {resolved_item['stack_item_id']} resolved.")
        logging.debug(f"[ENGINE SEND] Stack item resolved. Generated PDUs: STACK_RESOLVE, PRIORITY_GRANT")

        return [resolved_pdu, grant_pdu]

    def cast_spell(self, player_id: str, spell_pdu: CastSpell, game_state: GameState) -> List[BaseModel] | Error:
        """
        Pushes a cast spell onto the stack and re-grants priority to the caster.
        """

        # Validate that the player has the priority
        if game_state.priority_player != player_id:
            error_pdu = Error(
                type=PDUType.ERROR,
                seq_num=game_state.get_next_seq_num(),
                code="NOT_YOUR_PRIORITY",
                message=f"Player {player_id} does not have priority to cast a spell.",
                rejected_action=spell_pdu.model_dump()
            )
            logging.warning(f"[ENGINE ERROR] Player {player_id} attempted to cast a spell without priority.")
            return error_pdu

        # TODO: di pa impl ang cards.json, pero i think need ng check dito if
        # the cost in spell_pdu matches the data from cards.json to avoid exploiting

        player = game_state.players[player_id]
        land_taps = []
        for land_id, mana_cost in spell_pdu.mana_payment.items():
            untapped_lands = []
            for card in player.battlefield:
                if card['card_id'] == land_id and not card.get('tapped', False):
                    untapped_lands.append(card)
            if len(untapped_lands) < mana_cost:
                error_pdu = Error(
                    type=PDUType.ERROR,
                    seq_num=game_state.get_next_seq_num(),
                    code="INSUFFICIENT_MANA",
                    message=f"Player {player_id} has insufficient mana to cast the spell."
                )
                return error_pdu
            land_taps.extend(untapped_lands[:mana_cost])
        for land in land_taps:
            land['tapped'] = True
        
        #Reset passes since an action was taken
        game_state.passes_in_a_row = 0

        stack_item_id = f"stack_{game_state.get_next_seq_num()}"

        # Add the spell to the stack
        stack_item = {
            "stack_item_id": stack_item_id,
            "item_type": "SPELL",
            "source": spell_pdu.card_id,
            "targets": spell_pdu.targets,
            "controller": player_id
        }
        game_state.stack.append(stack_item)

        logging.info(f"Player {player_id} cast {spell_pdu.card_id}. Added to stack.")
        logging.debug(f"[ENGINE STATE] Stack size is now {len(game_state.stack)}.")

        # Create StackPush PDU to notify clients of the new stack item
        push_pdu = StackPush(
            type=PDUType.STACK_PUSH,
            seq_num=game_state.get_next_seq_num(),
            stack_item_id=stack_item_id,
            item_type="SPELL",
            source=spell_pdu.card_id,
            targets=spell_pdu.targets,
            controller=player_id
        )

        #Priority is re-granted to the player who cast the spell
        grant_pdu = PriorityGrant(
            type=PDUType.PRIORITY_GRANT,
            seq_num=game_state.get_next_seq_num(),
            player_id=player_id,
            time_limit_ms=60000
        )
        return [push_pdu, grant_pdu]

    def play_land(self, player_id: str, land_pdu: PlayLand, game_state: GameState):
        if game_state.priority_player != player_id or game_state.active_player != player_id:
            return Error(
                type=PDUType.ERROR,
                seq_num=game_state.get_next_seq_num(),
                code="NOT_YOUR_TURN",
                message="Land can only be played during your turn or when you have priority.",
                rejected_action=land_pdu.model_dump()
            )
        if game_state.current_step not in [InGamePhase.PRE_COMBAT_MAIN, InGamePhase.POST_COMBAT_MAIN]:
            return Error(
                type=PDUType.ERROR,
                seq_num=game_state.get_next_seq_num(),
                code="WRONG_PHASE",
                message="Lands can only be played during the Main Phase.",
                rejected_action=land_pdu.model_dump()
            )
        player = game_state.players[player_id]
        if player.lands_played_this_turn >= 1:
            return Error(
                type=PDUType.ERROR,
                seq_num=game_state.get_next_seq_num(),
                code="LAND_LIMIT_REACHED",
                message="Land already played for this turn.",
                rejected_action=land_pdu.model_dump()
            )
        if land_pdu.card_id not in player.hand:
            return Error(
                type=PDUType.ERROR,
                seq_num=game_state.get_next_seq_num(),
                code="LAND_NOT_IN_HAND",
                message=f"{land_pdu.card_id} is not in player {player_id}'s hand.",
                rejected_action=land_pdu.model_dump()
            )
        player.hand.remove(land_pdu.card_id)
        player.battlefield.append({ 'card_id': land_pdu.card_id, 'tapped': False })
        player.lands_played_this_turn += 1
        game_state.passes_in_a_row = 0
        logging.info(f'Player {player_id} plays land: {land_pdu.card_id}')
        grant_pdu = PriorityGrant(
            type=PDUType.PRIORITY_GRANT,
            seq_num=game_state.get_next_seq_num(),
            player_id=player_id,
            time_limit_ms=60000
        )
        return [grant_pdu]