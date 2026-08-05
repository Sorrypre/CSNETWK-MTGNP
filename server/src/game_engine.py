from typing import List
from pydantic import BaseModel
from schemas import *
from game_state import GameState, PlayerState
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

        if next_step == InGamePhase.UNTAP:
            # not sure if meron pang need gawin sa untap phase,
            # pero for now same code lang muna sya as before ko inisplit
            # ung untap and cleanup conditions
            active_player = game_state.players[game_state.active_player]
            #No priority granted during these phases
            game_state.priority_player = None
            logging.debug(f"[ENGINE SEND] Phase transition (No Priority). Generated PDU: PHASE_TRANSITION ({next_step})")
            return [transition_pdu]
        elif next_step == InGamePhase.CLEANUP:
            active_player = game_state.players[game_state.active_player]
            hand_diff = len(active_player.hand) - 7
            if hand_diff > 0: # need magtapon
                logging.info(f'Player {active_player} needs to discard {hand_diff} card{'s' if hand_diff > 1 else ''}.')
                return [transition_pdu]
            else: # cleanup ok
                next_pdu = self.advance_phase(game_state)
                return [transition_pdu] + next_pdu
        else:
            game_state.priority_player = game_state.active_player
            grant_pdu = PriorityGrant(
                type=PDUType.PRIORITY_GRANT,
                seq_num=game_state.get_next_seq_num(),
                player_id=game_state.priority_player,
                time_limit_ms=60000
            )
            logging.debug(f"[ENGINE SEND] Phase transition. Generated PDUs: PHASE_TRANSITION ({next_step}), PRIORITY_GRANT")
            sba_results = self.check_state_based_action(game_state)
            if sba_results:
                return sba_results
            return [transition_pdu, grant_pdu]

    def resolve_stack(self, game_state: GameState):
        """
        Pop the top item, applies effects, and re-grants priority
        """
        # If the stack is empty, return an empty list
        if not game_state.stack:
            return []

        resolved_item = game_state.stack.pop()
        #Get the targets of the resolved item to check if they are still valid
        targets = resolved_item.get("targets", [])

        #Assume the spell resolves successfully unless we find no legal targets
        result_status = "RESOLVED"

        if len(targets) > 0:
            legal_targets = [t for t in targets if self.is_target_valid(t, game_state)]

            #No legal targets means the spell fizzles
            if len(legal_targets) == 0:
                result_status = "FIZZLE"
                logging.info(f"Stack item {resolved_item['stack_item_id']} fizzled due to no legal targets.")

        resolved_pdu = StackResolve(
            type=PDUType.STACK_RESOLVE,
            seq_num=game_state.get_next_seq_num(),
            stack_item_id=resolved_item["stack_item_id"],
            result=result_status,
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

        sba_results = self.check_state_based_action(game_state)
        if sba_results:
            return sba_results

        logging.info(f"Stack item {resolved_item['stack_item_id']} resolved.")
        logging.debug(f"[ENGINE SEND] Stack item resolved. Generated PDUs: STACK_RESOLVE, PRIORITY_GRANT")

        return [resolved_pdu, grant_pdu]

    def handle_cast_spell(self, player_id: str, spell_pdu: CastSpell, game_state: GameState) -> List[BaseModel] | Error:
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

        # Verify mana cost ng PDU via dun sa catalog to see if matching
        # Kase kung hindi ibig sabihin may mali smwr
        base_card_id = spell_pdu.card_id
        if '_' in base_card_id:
            base_card_id = base_card_id.rsplit("_", 1)[0]
        card_in_question = game_state.catalog.get(base_card_id)
        if not card_in_question:
            return Error(
                type=PDUType.ERROR,
                seq_num=game_state.get_next_seq_num(),
                code="UNKNOWN_CARD",
                message=f"'{base_card_id}' is not found in the card catalog."
            )
        cmc = card_in_question.get("cmc", 0)
        total_mana = sum(spell_pdu.mana_payment.values())
        if total_mana < cmc:
            return Error(
                type=PDUType.ERROR,
                seq_num=game_state.get_next_seq_num(),
                code="INSUFFICIENT_MANA",
                message=f"Spell needs {cmc} mana according to the card catalog, but PDU says {total_mana}."
            )
        player = game_state.players[player_id]
        if not player.pay_mana(spell_pdu.mana_payment):
            return Error(
                type=PDUType.ERROR,
                seq_num=game_state.get_next_seq_num(),
                code="INSUFFICIENT_MANA",
                message=f"Player {player_id} has insufficient mana to cast the spell."
            )
        
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

        sba_results = self.check_state_based_action(game_state)

        if sba_results:
            # If the game is over, broadcast the push/resolve, then the game over.
            return [push_pdu] + sba_results

        return [push_pdu, grant_pdu]

    def is_target_valid(self, target_id: str, game_state: GameState) -> bool:
        """
        Validates if a given target ID is valid in the current game state.
        """
        # Check if the target is a player
        if target_id in game_state.players:
            return True

        # Is the target permanent on the battlefield
        for player in game_state.players.values():
            for permanent in player.battlefield:
                #depends on how battlefield dict is structured
                if permanent.get("id") == target_id:
                    return True
        return False

    def check_state_based_action(self, game_state: GameState) -> List[BaseModel]:
        """
        Evaluates State-Based Actions (SBAs) and generates corresponding PDUs if any conditions are met.
        """
        generated_pdus = []

        players = list(game_state.players.keys())
        p1, p2 = players[0], players[1]

        #Check if either player has 0 or less life
        is_p1_dead = game_state.players[p1].life <= 0
        is_p2_dead = game_state.players[p2].life <= 0

        #Check if either player has drawn from an empty deck
        is_p1_deck_empty = game_state.players[p1].empty_deck_draw
        is_p2_deck_empty = game_state.players[p2].empty_deck_draw

        if is_p1_deck_empty or is_p2_deck_empty:
            if is_p1_deck_empty and is_p2_deck_empty:
                loser = game_state.active_player
                winner = p2 if loser == p1 else p1
            else:
                loser = p1 if is_p1_deck_empty else p2
                winner = p2 if is_p1_deck_empty else p1

            game_over_pdu = GameOver(
                type=PDUType.GAME_OVER,
                seq_num=game_state.get_next_seq_num(),
                winner_id=winner,
                loser_id=loser,
                reason="DECK_EMPTY"
            )
            logging.info(f"[ENGINE STATE] SBA Triggered: Player {loser} drew from an empty deck.")
            return [game_over_pdu]

        if is_p1_dead or is_p2_dead:
            #Rule 8.4 if both players die then Active player loses
            if is_p1_dead and is_p2_dead:
                loser = game_state.active_player
                winner = p2 if loser == p1 else p1
            else:
                loser = p1 if is_p1_dead else p2
                winner = p2 if is_p1_dead else p1

            game_over_pdu = GameOver(
                type=PDUType.GAME_OVER,
                seq_num=game_state.get_next_seq_num(),
                winner_id=winner,
                loser_id=loser,
                reason="LIFE_ZERO"
            )
            logging.info(f"[ENGINE STATE] SBA Triggered: Player {loser} has died.")
            return [game_over_pdu]

        # Check for dead creatures
        for player_id, player_state in game_state.players.items():
            surviving_permanents = []

            for perm in player_state.battlefield:
                #.get() safely in case non-creature permanents (like lands) are present
                toughness = perm.get("toughness")

                # If toughness is defined, check if the creature should die due to damage
                if toughness is not None:
                    damage = perm.get("damage", 0)
                    if toughness <= 0 or damage >= toughness:
                        # Creature dies, move to graveyard
                        player_state.graveyard.append(perm["id"])
                        logging.debug(f"[ENGINE STATE] SBA: Creature {perm['id']} died and moved to graveyard.")
                        continue # Skip adding it to surviving permanents

                surviving_permanents.append(perm)

            # Only include things that survived
            player_state.battlefield = surviving_permanents

        return generated_pdus

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

    def trigger_order_response(self, player_id: str, response_pdu: TriggerOrderResponse, game_state: GameState):
        pending = game_state.pending_triggers.get(player_id, [])
        if not pending:
            return Error(
                type=PDUType.ERROR,
                seq_num=game_state.get_next_seq_num(),
                code="NO_TRIGGER",
                message=f"Player {player_id}'s trigger stack is empty."
            )
        pending_ids = [ trigger["trigger_id"] for trigger in pending ]
        if set(response_pdu.ordered_trigger_ids) != set(pending_ids):
            return Error(
                type=PDUType.ERROR,
                seq_num=game_state.get_next_seq_num(),
                code="INVALID_TRIGGER",
                message="Trigger IDs mismatch the current pending stack."
            )
        pdu_list: List[StackPush | PriorityGrant] = []
        for trigger_id in response_pdu.ordered_trigger_ids:
            trigger_data = None
            for trigger in pending:
                if trigger["trigger_id"] == trigger_id:
                    trigger_data = trigger
                    break
            stack_item_id = f"stack_{game_state.get_next_seq_num()}"
            stack_item = {
                "stack_item_id": stack_item_id,
                "item_type": "TRIGGER_ABILITY",
                "source": trigger_data.get("source_id", "Unknown"),
                "targets": [],
                "controller": player_id
            }
            game_state.stack.append(stack_item)
            logging.debug(f"Trigger {trigger_id} pushed into the stack.")
            pdu_list.append(StackPush(
                type=PDUType.STACK_PUSH,
                seq_num=game_state.get_next_seq_num(),
                stack_item_id=stack_item_id,
                item_type="TRIGGER_ABILITY",
                source=trigger_data.get("source_id", "Unknown"),
                targets=[],
                controller=player_id
            ))
        game_state.pending_triggers[player_id] = []
        game_state.passes_in_a_row = 0
        grant_pdu = PriorityGrant(
            type=PDUType.PRIORITY_GRANT,
            seq_num=game_state.get_next_seq_num(),
            player_id=game_state.active_player,
            time_limit_ms=60000
        )
        pdu_list.append(grant_pdu)
        return pdu_list

    def cleanup_discard(self, player_id: str, discard_pdu: Discard, game_state: GameState):
        if game_state.current_step != InGamePhase.CLEANUP:
            return Error(
                type=PDUType.ERROR,
                seq_num=game_state.get_next_seq_num(),
                code="WRONG_PHASE",
                message="This discard type only happens during CLEANUP phase."
            )
        if game_state.active_player != player_id:
            return Error(
                type=PDUType.ERROR,
                seq_num=game_state.get_next_seq_num(),
                code="NOT_YOUR_TURN",
                message="Only the active player can discard through this discard type."
            )
        player = game_state.players[player_id]
        hand_diff = len(player.hand) - 7
        if hand_diff <= 0:
            return Error(
                type=PDUType.ERROR,
                seq_num=game_state.get_next_seq_num(),
                code="NO_CLEANUP_DISCARD",
                message="Cleanup discards only happen when there are more than 7 cards in a player's hand."
            )
        if len(discard_pdu.card_ids) != hand_diff:
            return Error(
                type=PDUType.ERROR,
                seq_num=game_state.get_next_seq_num(),
                code="DISCARD_COUNT_MISMATCH",
                message=f"{hand_diff} card{'s' if hand_diff > 1 else ''} must be discarded by player {player_id}."
            )
        for card_id in discard_pdu.card_ids:
            if card_id not in player.hand:
                return Error(
                    type=PDUType.ERROR,
                    seq_num=game_state.get_next_seq_num(),
                    code="DISCARD_NON_EXISTENT",
                    message=f"{card_id} was not found in player {player_id}'s hand during execution."
                )
        player.raw_discard(discard_pdu.card_ids)
        logging.info(f'Player {player_id} discarded a total of {hand_diff} card{'s' if hand_diff > 1 else ''}.')
        return self.advance_phase(game_state)