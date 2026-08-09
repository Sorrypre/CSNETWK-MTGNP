from schemas import *
from game_state import GameState, extract_base_id, CardInstance
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
    ASSIGN_DAMAGE_ORDER = "ASSIGN_DAMAGE_ORDER"
    FIRST_STRIKE_DAMAGE = "FIRST_STRIKE_DAMAGE"
    COMBAT_DAMAGE = "COMBAT_DAMAGE"
    END_OF_COMBAT = "END_OF_COMBAT"
    POST_COMBAT_MAIN = "POSTCOMBAT_MAIN"
    END_STEP = "END_STEP"
    CLEANUP = "CLEANUP"

class GameEngine:
    def validate_action(self, pdu_type: str, client_seq_num: int, game_state: GameState) -> List[BaseModel] | None:
        """
        Sequence number enforcer.
        Returns Error PDU if client sequence number is stale, otherwise returns None.
        """
        logging.debug(f"[ENGINE RECEIVE] Validating PDU Type: {pdu_type} with seq_num: {client_seq_num}")

        if pdu_type in [PDUType.PING, PDUType.CONCEDE, PDUType.PLAYER_READY, PDUType.MULLIGAN_CHOICE]:
            return None

        # Validate client sequence token against expected priority token
        if client_seq_num != game_state.expected_seq_num:
            error_pdu = Error(
                type=PDUType.ERROR,
                seq_num=game_state.get_next_seq_num(),
                code="STALE_ACTION",
                message=f"Priority token mismatch. Expected {game_state.expected_seq_num}, got {client_seq_num}.",
                rejected_action=None
            )

            pdu_list: List[BaseModel] = [error_pdu]

            if game_state.priority_player is not None:
                grant_pdu = PriorityGrant(
                    type=PDUType.PRIORITY_GRANT,
                    seq_num=game_state.expected_seq_num,
                    player_id=game_state.priority_player,
                    time_limit_ms=60000
                )
                pdu_list.append(grant_pdu)

            logging.debug("[ENGINE SEND] Stale action detected. Generated PDUs: ERROR, PRIORITY_GRANT")
            return pdu_list

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

            # Only generate the Priority Grant. Do not generate a GameStateUpdate on a simple pass.
            grant_seq = game_state.get_next_seq_num()
            game_state.expected_seq_num = grant_seq

            grant_pdu = PriorityGrant(
                type=PDUType.PRIORITY_GRANT,
                seq_num=grant_seq,
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

    def check_and_reap_dead_creatures(self, game_state: GameState) -> List[str]:
        """
        Identifies creatures with lethal damage or zero/negative toughness,
        removes them from the battlefield, moves them to the graveyard,
        and returns a list of destroyed card IDs.
        """
        destroyed_cards: List[str] = []

        for player_id, player_state in game_state.players.items():
            surviving_battlefield = []

            for perm in player_state.battlefield:
                # Support both object attributes and dict key lookups
                card_id = perm.id
                toughness = perm.toughness
                damage = perm.damage

                # Check if creature has taken lethal damage or has 0 or less toughness
                if toughness is not None and (toughness <= 0 or damage >= toughness):
                    if card_id:
                        destroyed_cards.append(card_id)
                        player_state.graveyard.append(card_id)
                        logging.info(f"Creature {card_id} destroyed by lethal damage/stats and moved to graveyard.")
                else:
                    surviving_battlefield.append(perm)

            player_state.battlefield = surviving_battlefield

        return destroyed_cards

    def resolve_combat_damage(self, game_state: GameState, is_first_strike: bool = False) -> CombatDamageResult:
        """
        Evaluates damage, mutates state, and returns a CombatDamageResult PDU.
        """
        players = list(game_state.players.keys())
        defending_player_id = players[1] if game_state.active_player == players[0] else players[0]

        active_state = game_state.players[game_state.active_player]
        defending_state = game_state.players[defending_player_id]

        damage_events = []  # To be populated with damage detail objects for PDU

        for attacker_id in game_state.attackers:
            attacker = active_state.get_battlefield_card(attacker_id)
            if not attacker:
                continue

            has_first_strike = getattr(attacker, 'first_strike', False)
            has_double_strike = getattr(attacker, 'double_strike', False)

            # In First Strike step: Skip creatures without First/Double Strike
            attacker_should_deal_damage = (
                    (is_first_strike and (has_first_strike or has_double_strike)) or
                    (not is_first_strike and (has_double_strike or not has_first_strike))
            )

            # Get ordered blockers for this attacker
            assigned_blocker_ids = game_state.damage_orders.get(
                attacker_id,
                [b_id for b_id, a_id in game_state.blockers.items() if a_id == attacker_id]
            )

            if not assigned_blocker_ids:
                # Unblocked: Damage dealt directly to player
                if attacker_should_deal_damage:
                    damage = attacker.power or 0
                    defending_state.life -= damage
                    damage_events.append({
                        "source": attacker_id,
                        "target": defending_player_id,
                        "amount": damage
                    })
            else:
                # Blocked: Process combat damage across ordered blockers
                if attacker_should_deal_damage:
                    remaining_power = max(0, attacker.power or 0)

                    for idx, b_id in enumerate(assigned_blocker_ids):
                        blocker = defending_state.get_battlefield_card(b_id)
                        if not blocker:
                            continue

                        needed_lethal = max(0, blocker.toughness - blocker.damage)
                        is_last_blocker = (idx == len(assigned_blocker_ids) - 1)
                        # Ensure damage is non-negative
                        dmg_to_blocker = remaining_power if is_last_blocker else min(remaining_power, needed_lethal)
                        dmg_to_blocker = max(0, dmg_to_blocker)

                        blocker.damage += dmg_to_blocker
                        remaining_power = max(0, remaining_power - dmg_to_blocker)

                        damage_events.append({
                            "source": attacker_id,
                            "target": b_id,
                            "amount": dmg_to_blocker
                        })

                for b_id in assigned_blocker_ids:
                    blocker = defending_state.get_battlefield_card(b_id)
                    if not blocker:
                        continue

                    blocker_has_fs = getattr(blocker, 'first_strike', False)
                    blocker_has_ds = getattr(blocker, 'double_strike', False)

                    blocker_should_deal_damage = (
                            (is_first_strike and (blocker_has_fs or blocker_has_ds)) or
                            (not is_first_strike and (blocker_has_ds or not blocker_has_fs))
                    )

                    if blocker_should_deal_damage:
                        blocker_dmg = blocker.power or 0
                        attacker.damage += blocker_dmg
                        damage_events.append({
                            "source": b_id,
                            "target": attacker_id,
                            "amount": blocker_dmg
                        })
        # Process lethal damage / creature deaths
        destroyed_cards = self.check_and_reap_dead_creatures(game_state)

        return CombatDamageResult(
            type=PDUType.COMBAT_DAMAGE_RESULT,
            seq_num=game_state.get_next_seq_num(),
            damage_events=damage_events,
            life_totals={p_id: p.life for p_id, p in game_state.players.items()},
            creatures_died=destroyed_cards
        )

    def requires_damage_ordering(self, game_state: GameState) -> bool:
        """
        Returns True if any attacker is blocked by 2 or more creatures.
        """
        for attacker_id in game_state.attackers:
            blocker_count = sum(1 for a_id in game_state.blockers.values() if a_id == attacker_id)
            if blocker_count > 1:
                return True
        return False

    def has_first_strike_creatures(self, game_state: GameState) -> bool:
        """
        Returns True if any active attacking or blocking creature has first or double strike.
        """
        active_player = game_state.players[game_state.active_player]
        defending_id = [p for p in game_state.players if p != game_state.active_player][0]
        defending_player = game_state.players[defending_id]

        # Check attackers
        for a_id in game_state.attackers:
            card = active_player.get_battlefield_card(a_id)
            if card and (getattr(card, 'first_strike', False) or getattr(card, 'double_strike', False)):
                return True

        # Check blockers
        for b_id in game_state.blockers.keys():
            card = defending_player.get_battlefield_card(b_id)
            if card and (getattr(card, 'first_strike', False) or getattr(card, 'double_strike', False)):
                return True

        return False

    def advance_phase(self, game_state: GameState) -> List[BaseModel]:
        phase_order = [
            InGamePhase.UNTAP, InGamePhase.UPKEEP, InGamePhase.DRAW,
            InGamePhase.PRE_COMBAT_MAIN, InGamePhase.BEGIN_COMBAT,
            InGamePhase.DECLARE_ATTACKERS, InGamePhase.DECLARE_BLOCKERS,
            InGamePhase.ASSIGN_DAMAGE_ORDER, InGamePhase.FIRST_STRIKE_DAMAGE,
            InGamePhase.COMBAT_DAMAGE, InGamePhase.END_OF_COMBAT,
            InGamePhase.POST_COMBAT_MAIN, InGamePhase.END_STEP,
            InGamePhase.CLEANUP
        ]

        current_phase_index = phase_order.index(game_state.current_step)

        # Loop to reset turn at CLEANUP
        if current_phase_index == len(phase_order) - 1:
            next_step = InGamePhase.UNTAP
            game_state.turn_number += 1
            players = list(game_state.players.keys())
            game_state.active_player = players[1] if game_state.active_player == players[0] else players[0]
        else:
            next_step = phase_order[current_phase_index + 1]

        # Skip ASSIGN_DAMAGE_ORDER if no attackers are multi-blocked
        if next_step == InGamePhase.ASSIGN_DAMAGE_ORDER and not self.requires_damage_ordering(game_state):
            game_state.current_step = next_step
            return self.advance_phase(game_state)

        # Skip FIRST_STRIKE_DAMAGE if no active creatures have first/double strike
        if next_step == InGamePhase.FIRST_STRIKE_DAMAGE and not self.has_first_strike_creatures(game_state):
            game_state.current_step = next_step
            return self.advance_phase(game_state)

        # Update step state
        old_step = game_state.current_step
        game_state.current_step = next_step
        logging.info(f"Phase advanced from {old_step} to {next_step}.")

        active_player_state = game_state.players[game_state.active_player]

        if next_step == InGamePhase.UNTAP:
            # Untap permanents and reset land plays
            active_player_state.lands_played_this_turn = 0
            for perm in active_player_state.battlefield:
                perm.tapped = False

        elif next_step == InGamePhase.DRAW:
            # Draw a card (but first player skips draw on turn 1)
            is_first_player_turn_1 = (game_state.turn_number == 1 and game_state.active_player == list(game_state.players.keys())[0])
            if not is_first_player_turn_1:
                active_player_state.draw_cards(1)

        transition_pdu = PhaseTransition(
            type=PDUType.PHASE_TRANSITION,
            seq_num=game_state.get_next_seq_num(),
            from_phase=old_step,
            to_phase=next_step,
            active_player=game_state.active_player,
            turn=game_state.turn_number
        )

        pdu_list: List[BaseModel] = [transition_pdu]

        if next_step == InGamePhase.FIRST_STRIKE_DAMAGE:
            damage_result_pdu = self.resolve_combat_damage(game_state, is_first_strike=True)
            pdu_list.append(damage_result_pdu)

        elif next_step == InGamePhase.COMBAT_DAMAGE:
            damage_result_pdu = self.resolve_combat_damage(game_state, is_first_strike=False)
            pdu_list.append(damage_result_pdu)

        elif next_step == InGamePhase.POST_COMBAT_MAIN:
            game_state.reset_combat_state()

        # PRIORITY & SBA PROCESSING
        combat_action_steps = [
            InGamePhase.DECLARE_ATTACKERS,
            InGamePhase.DECLARE_BLOCKERS,
            InGamePhase.ASSIGN_DAMAGE_ORDER
        ]

        # Combat declaration steps wait for client input directly after transition.
        if next_step in combat_action_steps:
            game_state.priority_player = None
            game_state.expected_seq_num = transition_pdu.seq_num # Sync to PHASE_TRANSITION seq_num
            return pdu_list

        # Generate state update for all other phases
        state_update_pdu = GameStateUpdate(
            type=PDUType.GAME_STATE_UPDATE,
            seq_num=game_state.get_next_seq_num(),
            state=game_state.to_in_game_state()
        )

        if next_step == InGamePhase.UNTAP:
            game_state.priority_player = None
            pdu_list.append(state_update_pdu)

            # Untap step has no priority, auto-advances to UPKEEP
            next_pdus = self.advance_phase(game_state)
            return pdu_list + next_pdus

        elif next_step == InGamePhase.CLEANUP:
            active_player = game_state.players[game_state.active_player]
            hand_diff = len(active_player.hand) - 7
            if hand_diff > 0:
                logging.info(f'Player {active_player.player_id} needs to discard {hand_diff} card(s).')
                # Sync expected seq_num for the DISCARD action
                game_state.expected_seq_num = state_update_pdu.seq_num
                pdu_list.append(state_update_pdu)
                return pdu_list
            else:
                # No discard needed. Clear damage/sickness now.
                for player in game_state.players.values():
                    for perm in player.battlefield:
                        perm.damage = 0
                        perm.summoning_sick = False

                # Append the newly cleared state BEFORE transitioning to the next turn
                cleared_state_update = GameStateUpdate(
                    type=PDUType.GAME_STATE_UPDATE,
                    seq_num=game_state.get_next_seq_num(),
                    state=game_state.to_in_game_state()
                )
                pdu_list.append(cleared_state_update)

                next_pdus = self.advance_phase(game_state)
                return pdu_list + next_pdus
        else:
            # All other steps grant priority to the active player
            game_state.priority_player = game_state.active_player

            grant_seq = game_state.get_next_seq_num()
            game_state.expected_seq_num = grant_seq

            grant_pdu = PriorityGrant(
                type=PDUType.PRIORITY_GRANT,
                seq_num=grant_seq,
                player_id=game_state.priority_player,
                time_limit_ms=60000
            )

            sba_results = self.check_state_based_action(game_state)
            if sba_results:
                return pdu_list + [state_update_pdu] + sba_results

            pdu_list.append(state_update_pdu)
            pdu_list.append(grant_pdu)
            return pdu_list

    def apply_spell_ability_effect(self, item: dict, game_state: GameState) -> List[dict]:
        """
        Lightweight effect parser to implement core catalog rules.
        Returns a list of state_change dictionaries.
        """
        changes = []
        targets = item.get("targets", [])
        base_id = extract_base_id(item["source"])

        # If it targets, process the primary target
        target_id = targets[0] if targets else None

        # Damage Effects
        if base_id in ["lightning_bolt", "shock", "lava_spike", "flame_slash", "searing_spear", "skullcrack", "rift_bolt", "incinerate", "prodigal_sorcerer", "rod_of_ruin"]:
            amount = 3
            if base_id in ["shock"]: amount = 2
            elif base_id in ["flame_slash"]: amount = 4
            elif base_id in ["prodigal_sorcerer", "rod_of_ruin"]: amount = 1

            if target_id in game_state.players:
                game_state.players[target_id].life -= amount
            else:
                for p in game_state.players.values():
                    card = p.get_battlefield_card(target_id)
                    if card:
                        card.damage += amount
            changes.append({"change_type": "DAMAGE", "target": target_id, "amount": amount})

        # Counter Spells
        elif base_id in ["counterspell", "cancel", "negate", "mana_leak"]:
            for i, stack_item in enumerate(game_state.stack):
                if stack_item["stack_item_id"] == target_id:
                    popped = game_state.stack.pop(i)
                    game_state.players[popped["controller"]].graveyard.append(popped["source"])
                    changes.append({"change_type": "COUNTER", "target": target_id})
                    break

        # Destroy / Exile Spells
        elif base_id in ["terror", "doom_blade", "swords_to_plowshares", "path_to_exile"]:
            for p in game_state.players.values():
                card = p.get_battlefield_card(target_id)
                if card:
                    card.damage += 999 # Marks for destruction in SBA
                    changes.append({"change_type": "DESTROY", "target": target_id})

        # Unsummon Bounces
        elif base_id == "unsummon":
            for p in game_state.players.values():
                card = p.get_battlefield_card(target_id)
                if card:
                    p.battlefield.remove(card)
                    p.hand.append(target_id)
                    changes.append({"change_type": "RETURN_TO_HAND", "target": target_id})

        # Stat Buffs
        elif base_id in ["giant_growth", "vines_of_vastwood"]:
            for p in game_state.players.values():
                card = p.get_battlefield_card(target_id)
                if card:
                    card.power = (card.power or 0) + 3
                    card.toughness = (card.toughness or 0) + 3
                    changes.append({"change_type": "BUFF", "target": target_id, "amount": "+3/+3"})

        # Life Gain
        elif base_id in ["healing_salve"]:
            if target_id in game_state.players:
                game_state.players[target_id].life += 3
                changes.append({"change_type": "LIFE_GAIN", "target": target_id, "amount": 3})

        # Apply the trigger ability effects
        if item.get("item_type") == "TRIGGER_ABILITY":
            if base_id == "monastery_swiftspear":
                for p in game_state.players.values():
                    card = p.get_battlefield_card(item["source"])
                    if card:
                        card.power = (card.power or 0) + 1
                        card.toughness = (card.toughness or 0) + 1
                        changes.append({"change_type": "BUFF", "target": item["source"], "amount": "+1/+1"})

            elif base_id == "phantasmal_bear":
                for p in game_state.players.values():
                    card = p.get_battlefield_card(item["source"])
                    if card:
                        card.damage += 999
                        changes.append({"change_type": "DESTROY", "target": item["source"]})

            elif base_id == "gray_merchant":
                controller = game_state.players[item["controller"]]
                devotion = 2 # Simplified devotion calculation for engine
                for p_id, p in game_state.players.items():
                    if p_id != item["controller"]:
                        p.life -= devotion
                controller.life += devotion
                changes.append({"change_type": "LIFE_DRAIN", "source": item["source"], "amount": devotion})

        return changes

    def resolve_stack(self, game_state: GameState):
        """
        Pop the top item, applies effects, and re-grants priority
        """
        if not game_state.stack:
            return []

        resolved_item = game_state.stack.pop()
        targets = resolved_item.get("targets", [])
        result_status = "RESOLVED"
        state_changes = []

        if len(targets) > 0:
            legal_targets = [t for t in targets if self.is_target_valid(t, game_state)]
            if len(legal_targets) == 0:
                result_status = "FIZZLE"
                logging.info(f"Stack item {resolved_item['stack_item_id']} fizzled due to no legal targets.")

        if result_status == "RESOLVED":
            controller_id = resolved_item["controller"]
            controller = game_state.players[controller_id]
            source_id = resolved_item["source"]
            base_id = extract_base_id(source_id)
            card_meta = game_state.catalog.get(base_id, {})
            card_type = card_meta.get("type", "")

            trigger_pdus = []
            if resolved_item["item_type"] == "SPELL":
                if any(t in card_type for t in ["Creature", "Artifact", "Enchantment"]):
                    # Enter the battlefield
                    card_obj = CardInstance(source_id, game_state.catalog)
                    controller.battlefield.append(card_obj)
                    state_changes.append({
                        "change_type": "PERMANENT_ENTERS",
                        "card_id": source_id,
                        "controller": controller_id
                    })
                    # Detect triggers for entering the battlefield
                    trigger_pdus.extend(self.detect_triggers(game_state, "ENTERS_BATTLEFIELD", {"card_id": source_id}))
                else:
                    controller.graveyard.append(source_id)
                    state_changes.extend(self.apply_spell_ability_effect(resolved_item, game_state))

            elif resolved_item["item_type"] == "ABILITY":
                state_changes.extend(self.apply_spell_ability_effect(resolved_item, game_state))

        resolved_pdu = StackResolve(
            type=PDUType.STACK_RESOLVE,
            seq_num=game_state.get_next_seq_num(),
            stack_item_id=resolved_item["stack_item_id"],
            result=Literal[result_status],
            state_changes=state_changes
        )

        # After resolving, grant priority to the active player
        game_state.priority_player = game_state.active_player

        state_update_pdu = GameStateUpdate(
            type=PDUType.GAME_STATE_UPDATE,
            seq_num=game_state.get_next_seq_num(),
            state=game_state.to_in_game_state()
        )

        grant_seq = game_state.get_next_seq_num()
        game_state.expected_seq_num = grant_seq

        grant_pdu = PriorityGrant(
            type=PDUType.PRIORITY_GRANT,
            seq_num=grant_seq,
            player_id=game_state.priority_player,
            time_limit_ms=60000
        )

        has_trigger_order = any(p.type == PDUType.TRIGGER_ORDER for p in trigger_pdus)

        sba_results = self.check_state_based_action(game_state)
        if sba_results:
            return [resolved_pdu] + trigger_pdus + [state_update_pdu] + sba_results

        if has_trigger_order:
            # Withhold priority grant until triggers are ordered
            return [resolved_pdu] + trigger_pdus + [state_update_pdu]

        logging.info(f"Stack item {resolved_item['stack_item_id']} resolved.")
        logging.debug(f"[ENGINE SEND] Stack item resolved. Generated PDUs: STACK_RESOLVE, PRIORITY_GRANT")

        return [resolved_pdu] + trigger_pdus + [state_update_pdu, grant_pdu]

    def handle_cast_spell(self, player_id: str, spell_pdu: CastSpell, game_state: GameState) -> List[BaseModel] | Error:
        """
        Pushes a cast spell onto the stack and re-grants priority to the caster.
        """
        # Validate that the player has the priority
        if game_state.priority_player != player_id:
            return Error(
                type=PDUType.ERROR,
                seq_num=game_state.get_next_seq_num(),
                code="NOT_YOUR_PRIORITY",
                message=f"Player {player_id} does not have priority to cast a spell.",
                rejected_action=spell_pdu.model_dump()
            )

        player = game_state.players[player_id]

        # Ensure the player has the card in hand
        if spell_pdu.card_id not in player.hand:
            return Error(
                type=PDUType.ERROR,
                seq_num=game_state.get_next_seq_num(),
                code="ILLEGAL_ACTION",
                message=f"Card '{spell_pdu.card_id}' is not in your hand.",
                rejected_action=spell_pdu.model_dump()
            )

        base_card_id = extract_base_id(spell_pdu.card_id)
        card_in_question = game_state.catalog.get(base_card_id)
        if not card_in_question:
            return Error(
                type=PDUType.ERROR,
                seq_num=game_state.get_next_seq_num(),
                code="ILLEGAL_ACTION",
                message=f"'{base_card_id}' is not found in the card catalog."
            )

        cmc = card_in_question.get("cmc", 0)
        card_type = card_in_question.get("type", "")

        # Validate specific color requirements first
        required_mana = card_in_question.get("mana_cost", {})
        provided_mana = spell_pdu.mana_payment

        if "Instant" not in card_type:
            if game_state.active_player != player_id:
                return Error(
                    type=PDUType.ERROR, seq_num=game_state.get_next_seq_num(),
                    code="ILLEGAL_ACTION", message="Non-instant spells can only be cast on your turn.",
                    rejected_action=spell_pdu.model_dump()
                )
            if game_state.current_step not in [InGamePhase.PRE_COMBAT_MAIN, InGamePhase.POST_COMBAT_MAIN]:
                return Error(
                    type=PDUType.ERROR, seq_num=game_state.get_next_seq_num(),
                    code="WRONG_PHASE", message="Non-instant spells can only be cast during the Main Phase.",
                    rejected_action=spell_pdu.model_dump()
                )
            if len(game_state.stack) > 0:
                return Error(
                    type=PDUType.ERROR, seq_num=game_state.get_next_seq_num(),
                    code="ILLEGAL_ACTION", message="Non-instant spells cannot be cast while the stack is not empty.",
                    rejected_action=spell_pdu.model_dump()
                )

        for color in ["W", "U", "B", "R", "G"]:
            req = required_mana.get(color, 0)
            prov = provided_mana.get(color, 0)
            if prov < req:
                return Error(
                    type=PDUType.ERROR,
                    seq_num=game_state.get_next_seq_num(),
                    code="INSUFFICIENT_MANA",
                    message=f"Spell requires {req} {color} mana, but only {prov} provided.",
                    rejected_action=spell_pdu.model_dump()
                )

        # Validate total CMC (to account for generic mana costs)
        total_mana = sum(provided_mana.values())
        if total_mana < cmc:
            return Error(
                type=PDUType.ERROR,
                seq_num=game_state.get_next_seq_num(),
                code="INSUFFICIENT_MANA",
                message=f"Spell needs {cmc} total mana, but PDU says {total_mana}."
            )

        if not player.pay_mana(spell_pdu.mana_payment):
            return Error(
                type=PDUType.ERROR,
                seq_num=game_state.get_next_seq_num(),
                code="INSUFFICIENT_MANA",
                message=f"Player {player_id} has insufficient mana to cast the spell."
            )

        # Remove from hand now that costs are paid
        player.hand.remove(spell_pdu.card_id)

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

        #Detect triggers for SPELL_CAST and BECOMES_TARGET events
        trigger_pdus = self.detect_triggers(game_state, "SPELL_CAST", {"controller": player_id, "card_type": card_type})
        for t in spell_pdu.targets:
            trigger_pdus.extend(self.detect_triggers(game_state, "BECOMES_TARGET", {"target_id": t}))

        state_update_pdu = GameStateUpdate(
            type=PDUType.GAME_STATE_UPDATE,
            seq_num=game_state.get_next_seq_num(),
            state=game_state.to_in_game_state()
        )

        grant_seq = game_state.get_next_seq_num()
        game_state.expected_seq_num = grant_seq

        #Priority is re-granted to the player who cast the spell
        grant_pdu = PriorityGrant(
            type=PDUType.PRIORITY_GRANT,
            seq_num=grant_seq,
            player_id=player_id,
            time_limit_ms=60000
        )

        sba_results = self.check_state_based_action(game_state)

        has_trigger_order = any(p.type == PDUType.TRIGGER_ORDER for p in trigger_pdus)

        if sba_results:
            return [push_pdu] + trigger_pdus + [state_update_pdu] + sba_results

        if has_trigger_order:
            return [push_pdu] + trigger_pdus + [state_update_pdu]

        return [push_pdu] + trigger_pdus + [state_update_pdu, grant_pdu]

    def handle_activate_ability(self, player_id: str, ability_pdu: ActivateAbility, game_state: GameState) -> List[BaseModel] | Error:
        if game_state.priority_player != player_id:
            return Error(
                type=PDUType.ERROR,
                seq_num=game_state.get_next_seq_num(),
                code="NOT_YOUR_PRIORITY",
                message=f"Player {player_id} does not have priority.",
                rejected_action=ability_pdu.model_dump()
            )

        player = game_state.players[player_id]
        source_card = player.get_battlefield_card(ability_pdu.source_id)
        if not source_card:
            return Error(
                type=PDUType.ERROR,
                seq_num=game_state.get_next_seq_num(),
                code="ILLEGAL_ACTION",
                message=f"Source {ability_pdu.source_id} is not on your battlefield.",
                rejected_action=ability_pdu.model_dump()
            )

        if ability_pdu.cost_payment.get("tap", False):
            if source_card.tapped or source_card.summoning_sick:
                return Error(
                    type=PDUType.ERROR,
                    seq_num=game_state.get_next_seq_num(),
                    code="ILLEGAL_ACTION",
                    message="Permanent is tapped or has summoning sickness.",
                    rejected_action=ability_pdu.model_dump()
                )
            source_card.tapped = True

        mana_cost = ability_pdu.cost_payment.get("mana", {})
        if mana_cost:
            if not player.pay_mana(mana_cost):
                return Error(
                    type=PDUType.ERROR,
                    seq_num=game_state.get_next_seq_num(),
                    code="INSUFFICIENT_MANA",
                    message="Insufficient mana to activate ability.",
                    rejected_action=ability_pdu.model_dump()
                )

        game_state.passes_in_a_row = 0
        stack_item_id = f"stack_{game_state.get_next_seq_num()}"

        stack_item = {
            "stack_item_id": stack_item_id,
            "item_type": "ABILITY",
            "source": ability_pdu.source_id,
            "targets": ability_pdu.targets,
            "controller": player_id,
            "ability_index": ability_pdu.ability_index
        }
        game_state.stack.append(stack_item)
        logging.info(f"Player {player_id} activated ability of {ability_pdu.source_id}.")

        push_pdu = StackPush(
            type=PDUType.STACK_PUSH,
            seq_num=game_state.get_next_seq_num(),
            stack_item_id=stack_item_id,
            item_type="ABILITY",
            source=ability_pdu.source_id,
            targets=ability_pdu.targets,
            controller=player_id
        )

        state_update_pdu = GameStateUpdate(
            type=PDUType.GAME_STATE_UPDATE,
            seq_num=game_state.get_next_seq_num(),
            state=game_state.to_in_game_state()
        )

        grant_seq = game_state.get_next_seq_num()
        game_state.expected_seq_num = grant_seq

        grant_pdu = PriorityGrant(
            type=PDUType.PRIORITY_GRANT,
            seq_num=grant_seq,
            player_id=player_id,
            time_limit_ms=60000
        )

        sba_results = self.check_state_based_action(game_state)
        if sba_results:
            return [push_pdu, state_update_pdu] + sba_results

        return [push_pdu, state_update_pdu, grant_pdu]

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
                # Access .id attribute on CardInstance directly
                if permanent.id == target_id:
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
                # Access CardInstance properties directly
                toughness = perm.toughness

                # If toughness is defined, check if the creature should die due to damage
                if toughness is not None:
                    damage = perm.damage
                    if toughness <= 0 or damage >= toughness:
                        # Creature dies, move to graveyard
                        player_state.graveyard.append(perm.id)
                        logging.debug(f"[ENGINE STATE] SBA: Creature {perm.id} died and moved to graveyard.")
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
                code="ILLEGAL_ACTION",
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
        if len(game_state.stack) > 0:
            return Error(
                type=PDUType.ERROR,
                seq_num=game_state.get_next_seq_num(),
                code="ILLEGAL_ACTION",
                message="Lands cannot be played while the stack is not empty.",
                rejected_action=land_pdu.model_dump()
            )
        player = game_state.players[player_id]
        if player.lands_played_this_turn >= 1:
            return Error(
                type=PDUType.ERROR,
                seq_num=game_state.get_next_seq_num(),
                code="ILLEGAL_ACTION",
                message="Land already played for this turn.",
                rejected_action=land_pdu.model_dump()
            )
        if land_pdu.card_id not in player.hand:
            return Error(
                type=PDUType.ERROR,
                seq_num=game_state.get_next_seq_num(),
                code="ILLEGAL_ACTION",
                message=f"{land_pdu.card_id} is not in player {player_id}'s hand.",
                rejected_action=land_pdu.model_dump()
            )

        # Move card to battlefield as a CardInstance object instead of adding a dict
        player.move_to_battlefield(land_pdu.card_id)
        player.lands_played_this_turn += 1
        game_state.passes_in_a_row = 0
        logging.info(f'Player {player_id} plays land: {land_pdu.card_id}')

        # game state update pdu
        state_update_pdu = GameStateUpdate(
            type=PDUType.GAME_STATE_UPDATE,
            seq_num=game_state.get_next_seq_num(),
            state=game_state.to_in_game_state() # Uses the new mapping method
        )

        grant_seq = game_state.get_next_seq_num()
        game_state.expected_seq_num = grant_seq  # Sync expected priority token

        grant_pdu = PriorityGrant(
            type=PDUType.PRIORITY_GRANT,
            seq_num=grant_seq,
            player_id=player_id,
            time_limit_ms=60000
        )
        return [state_update_pdu, grant_pdu]

    def trigger_order_response(self, player_id: str, response_pdu: TriggerOrderResponse, game_state: GameState):
        pending = game_state.pending_triggers.get(player_id, [])
        if not pending:
            return Error(
                type=PDUType.ERROR,
                seq_num=game_state.get_next_seq_num(),
                code="TRIGGER_ORDER_INVALID",
                message=f"Player {player_id}'s trigger stack is empty."
            )
        pending_ids = [ trigger["trigger_id"] for trigger in pending ]
        if set(response_pdu.ordered_trigger_ids) != set(pending_ids):
            return Error(
                type=PDUType.ERROR,
                seq_num=game_state.get_next_seq_num(),
                code="TRIGGER_ORDER_INVALID",
                message="Trigger IDs mismatch the current pending stack."
            )
        pdu_list: List[BaseModel] = []
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

        state_update_pdu = GameStateUpdate(
            type=PDUType.GAME_STATE_UPDATE,
            seq_num=game_state.get_next_seq_num(),
            state=game_state.to_in_game_state() # Uses the new mapping method
        )
        pdu_list.append(state_update_pdu)

        grant_seq = game_state.get_next_seq_num()
        game_state.expected_seq_num = grant_seq  # Sync expected priority token

        grant_pdu = PriorityGrant(
            type=PDUType.PRIORITY_GRANT,
            seq_num=grant_seq,
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
                code="ILLEGAL_ACTION",
                message="Only the active player can discard through this discard type."
            )
        player = game_state.players[player_id]
        hand_diff = len(player.hand) - 7
        if hand_diff <= 0:
            return Error(
                type=PDUType.ERROR,
                seq_num=game_state.get_next_seq_num(),
                code="ILLEGAL_ACTION",
                message="Cleanup discards only happen when there are more than 7 cards in a player's hand."
            )
        if len(discard_pdu.card_ids) != hand_diff:
            return Error(
                type=PDUType.ERROR,
                seq_num=game_state.get_next_seq_num(),
                code="ILLEGAL_ACTION",
                message=f"{hand_diff} card{'s' if hand_diff > 1 else ''} must be discarded by player {player_id}."
            )
        for card_id in discard_pdu.card_ids:
            if card_id not in player.hand:
                return Error(
                    type=PDUType.ERROR,
                    seq_num=game_state.get_next_seq_num(),
                    code="ILLEGAL_ACTION",
                    message=f"{card_id} was not found in player {player_id}'s hand during execution."
                )
        player.raw_discard(discard_pdu.card_ids)
        logging.info(f'Player {player_id} discarded a total of {hand_diff} card(s).')

        # Clear damage and summoning sickness
        for p in game_state.players.values():
            for perm in p.battlefield:
                perm.damage = 0
                perm.summoning_sick = False

        # Broadcast the post-discard, cleared state
        state_update_pdu = GameStateUpdate(
            type=PDUType.GAME_STATE_UPDATE,
            seq_num=game_state.get_next_seq_num(),
            state=game_state.to_in_game_state()
        )

        # Advance to UNTAP for the next player
        next_pdus = self.advance_phase(game_state)
        return [state_update_pdu] + next_pdus

    # Combat Phase Functions added to handle the logic required for that phased

    def handle_declare_attackers(self, player_id: str, attackers_pdu: DeclareAttackers, game_state: GameState) -> List[BaseModel] | Error:
        if game_state.current_step != InGamePhase.DECLARE_ATTACKERS:
            return Error(
                type=PDUType.ERROR,
                seq_num=game_state.get_next_seq_num(),
                code="WRONG_PHASE",
                message="Attackers can only be declared during the DECLARE_ATTACKERS step."
            )
        if game_state.active_player != player_id:
            return Error(
                type=PDUType.ERROR,
                seq_num=game_state.get_next_seq_num(),
                code="ILLEGAL_ACTION",
                message="Only the active player can declare attackers."
            )

        active_player_state = game_state.players[player_id]
        declared_ids = [a.creature_id for a in attackers_pdu.attackers]

        # If no attackers declared, skip combat steps directly to POSTCOMBAT_MAIN
        if not declared_ids:
            logging.info("No attackers declared. Skipping directly to END_OF_COMBAT.")
            game_state.current_step = InGamePhase.END_OF_COMBAT

            transition_pdu = PhaseTransition(
                type=PDUType.PHASE_TRANSITION,
                seq_num=game_state.get_next_seq_num(),
                from_phase=InGamePhase.DECLARE_ATTACKERS,
                to_phase=InGamePhase.END_OF_COMBAT,
                active_player=game_state.active_player,
                turn=game_state.turn_number
            )

            state_update_pdu = GameStateUpdate(
                type=PDUType.GAME_STATE_UPDATE,
                seq_num=game_state.get_next_seq_num(),
                state=game_state.to_in_game_state() # Uses the new mapping method
            )

            grant_seq = game_state.get_next_seq_num()
            game_state.expected_seq_num = grant_seq  # Sync expected priority token

            grant_pdu = PriorityGrant(
                type=PDUType.PRIORITY_GRANT,
                seq_num=grant_seq,
                player_id=game_state.active_player,
                time_limit_ms=60000
            )
            return [transition_pdu, state_update_pdu, grant_pdu]

        # Validate each declared attacker
        for card_id in declared_ids:
            card = active_player_state.get_battlefield_card(card_id)
            if not card:
                return Error(
                    type=PDUType.ERROR,
                    seq_num=game_state.get_next_seq_num(),
                    code="ILLEGAL_TARGET",
                    message=f"Creature {card_id} is not on the battlefield."
                )
            if card.tapped or card.summoning_sick:
                return Error(
                    type=PDUType.ERROR,
                    seq_num=game_state.get_next_seq_num(),
                    code="ILLEGAL_ACTION",
                    message=f"Creature {card_id} is tapped or has summoning sickness."
                )

        # Tap declared attackers and record state
        for card_id in declared_ids:
            card = active_player_state.get_battlefield_card(card_id)
            card.tapped = True
            game_state.attackers.append(card_id)

        # Detect triggered abilities for ATTACKS event
        trigger_pdus = []
        for card_id in declared_ids:
            trigger_pdus.extend(self.detect_triggers(game_state, "ATTACKS", {"attacker_id": card_id}))

        game_state.passes_in_a_row = 0
        state_update_pdu = GameStateUpdate(
            type=PDUType.GAME_STATE_UPDATE,
            seq_num=game_state.get_next_seq_num(),
            state=game_state.to_in_game_state() # Uses the new mapping method
        )

        grant_seq = game_state.get_next_seq_num()
        game_state.expected_seq_num = grant_seq  # Sync expected priority token

        grant_pdu = PriorityGrant(
            type=PDUType.PRIORITY_GRANT,
            seq_num=grant_seq,
            player_id=player_id,
            time_limit_ms=60000
        )

        has_trigger_order = any(p.type == PDUType.TRIGGER_ORDER for p in trigger_pdus)

        if has_trigger_order:
            return trigger_pdus + [state_update_pdu]

        return trigger_pdus + [state_update_pdu, grant_pdu]


    def handle_declare_blockers(self, player_id: str, blockers_pdu: DeclareBlockers, game_state: GameState) -> List[BaseModel] | Error:
        if game_state.current_step != InGamePhase.DECLARE_BLOCKERS:
            return Error(
                type=PDUType.ERROR,
                seq_num=game_state.get_next_seq_num(),
                code="WRONG_PHASE",
                message="Blockers can only be declared during the DECLARE_BLOCKERS step."
            )
        if game_state.active_player == player_id:
            return Error(
                type=PDUType.ERROR,
                seq_num=game_state.get_next_seq_num(),
                code="ILLEGAL_ACTION",
                message="Only the defending player can declare blockers."
            )

        defending_player_state = game_state.players[player_id]
        blocks = {b.creature_id: b.blocking_id for b in blockers_pdu.blockers}

        for blocker_id, attacker_id in blocks.items():
            blocker = defending_player_state.get_battlefield_card(blocker_id)
            if not blocker or blocker.tapped:
                return Error(
                    type=PDUType.ERROR,
                    seq_num=game_state.get_next_seq_num(),
                    code="ILLEGAL_ACTION",
                    message=f"Blocker {blocker_id} is invalid or tapped."
                )
            if attacker_id not in game_state.attackers:
                return Error(
                    type=PDUType.ERROR,
                    seq_num=game_state.get_next_seq_num(),
                    code="ILLEGAL_TARGET",
                    message=f"Attacker {attacker_id} was not declared as an attacker."
                )

        game_state.blockers = blocks
        game_state.passes_in_a_row = 0

        state_update_pdu = GameStateUpdate(
            type=PDUType.GAME_STATE_UPDATE,
            seq_num=game_state.get_next_seq_num(),
            state=game_state.to_in_game_state() # Uses the new mapping method
        )

        grant_seq = game_state.get_next_seq_num()
        game_state.expected_seq_num = grant_seq  # Sync expected priority token

        grant_pdu = PriorityGrant(
            type=PDUType.PRIORITY_GRANT,
            seq_num=grant_seq,
            player_id=game_state.active_player,
            time_limit_ms=60000
        )
        return [state_update_pdu, grant_pdu]

    def handle_assign_damage_order(self, player_id: str, pdu: AssignDamageOrder, game_state: GameState) -> List[BaseModel] | Error:
        if game_state.current_step != InGamePhase.ASSIGN_DAMAGE_ORDER:
            return Error(
                type=PDUType.ERROR,
                seq_num=game_state.get_next_seq_num(),
                code="WRONG_PHASE",
                message="Damage order can only be assigned during ASSIGN_DAMAGE_ORDER step."
            )
        if game_state.active_player != player_id:
            return Error(
                type=PDUType.ERROR,
                seq_num=game_state.get_next_seq_num(),
                code="ILLEGAL_ACTION",
                message="Only the active player can assign damage order."
            )

        attacker_id = pdu.attacker_id
        ordered_blocker_ids = pdu.blocker_order

        # Validate that the attacker was declared
        if attacker_id not in game_state.attackers:
            return Error(
                type=PDUType.ERROR,
                seq_num=game_state.get_next_seq_num(),
                code="ILLEGAL_TARGET",
                message=f"Attacker {attacker_id} does not exist in combat."
            )

        # Retrieve actual blockers assigned to this attacker
        actual_blockers = [b_id for b_id, a_id in game_state.blockers.items() if a_id == attacker_id]

        # Verify blocker list matches assigned blockers exactly
        if sorted(actual_blockers) != sorted(ordered_blocker_ids):
            return Error(
                type=PDUType.ERROR,
                seq_num=game_state.get_next_seq_num(),
                code="ILLEGAL_ACTION",
                message="Ordered blockers list does not match declared blockers for this attacker."
            )

        # Store damage assignment order
        game_state.damage_orders[attacker_id] = ordered_blocker_ids
        game_state.passes_in_a_row = 0

        multi_blocked_ids = [
            a_id for a_id in game_state.attackers
            if sum(1 for target_a in game_state.blockers.values() if target_a == a_id) > 1
        ]

        # Check if we have received an order for every multi-blocked attacker
        if all(a_id in game_state.damage_orders for a_id in multi_blocked_ids):
            # All orders received! Advance the sequence token and open the final priority window
            state_update_pdu = GameStateUpdate(
                type=PDUType.GAME_STATE_UPDATE,
                seq_num=game_state.get_next_seq_num(),
                state=game_state.to_in_game_state() # Uses the new mapping method
            )

            grant_seq = game_state.get_next_seq_num()
            game_state.expected_seq_num = grant_seq

            grant_pdu = PriorityGrant(
                type=PDUType.PRIORITY_GRANT,
                seq_num=grant_seq,
                player_id=player_id,
                time_limit_ms=60000
            )
            return [state_update_pdu, grant_pdu]

        # Still waiting on more orders.
        state_update_pdu = GameStateUpdate(
            type=PDUType.GAME_STATE_UPDATE,
            seq_num=game_state.get_next_seq_num(),
            state=game_state.to_in_game_state() # Uses the new mapping method
        )
        return [state_update_pdu]

    def detect_triggers(self, game_state: GameState, event_type: str, event_data: dict) -> List[BaseModel]:
        """"
        Detects triggered abilities in the battlefield and queues them for resolution.
        Returns a list of PDUs to send to clients.
        """
        pdu_list: List[BaseModel] = []
        new_triggers = {p: [] for p in game_state.players}

        for p_id, player in game_state.players.items():
            for perm in player.battlefield:
                base_id = extract_base_id(perm.id)

                # Enters Battlefield Triggers
                if event_type == "ENTERS_BATTLEFIELD" and event_data.get("card_id") == perm.id:
                    if base_id in ["gray_merchant", "gravedigger", "goblin_bushwhacker"]:
                        new_triggers[p_id].append({
                            "trigger_id": f"trg_{game_state.get_next_seq_num()}",
                            "source_id": perm.id
                        })
                # Attack  Triggers
                elif event_type == "ATTACKS" and event_data.get("attacker_id") == perm.id:
                    if base_id == "goblin_guide":
                        new_triggers[p_id].append({
                            "trigger_id": f"trg_{game_state.get_next_seq_num()}",
                            "source_id": perm.id
                        })
                # Spell Cast Triggers
                elif event_type == "SPELL_CAST" and event_data.get("controller") == p_id:
                    if base_id == "monastery_swiftspear" and "Creature" not in event_data.get("card_type", ""):
                        new_triggers[p_id].append({
                            "trigger_id": f"trg_{game_state.get_next_seq_num()}",
                            "source_id": perm.id
                        })
                # Becomes Target Triggers
                elif event_type == "BECOMES_TARGET" and event_data.get("target_id") == perm.id:
                    if base_id == "phantasmal_bear":
                        new_triggers[p_id].append({
                            "trigger_id": f"trg_{game_state.get_next_seq_num()}",
                            "source_id": perm.id
                        })
        # Process Triggers
        for p_id, triggers in new_triggers.items():
            if not triggers:
                continue

            if len(triggers) > 1:
                # Multiple simultaneous triggers
                game_state.pending_triggers[p_id].extend(triggers)
                order_pdu = TriggerOrder(
                    type=PDUType.TRIGGER_ORDER,
                    seq_num=game_state.get_next_seq_num(),
                    player_id=p_id,
                    trigger_ids=[t["trigger_id"] for t in triggers]
                )
                pdu_list.append(order_pdu)

            elif len(triggers) == 1:
                # Bypass ordering, push straight to stack
                trigger = triggers[0]
                stack_item_id = f"stack_{game_state.get_next_seq_num()}"
                stack_item = {
                    "stack_item_id": stack_item_id,
                    "item_type": "TRIGGER_ABILITY",
                    "source": trigger["source_id"],
                    "targets": [],
                    "controller": p_id
                }
                game_state.stack.append(stack_item)

                push_pdu = StackPush(
                    type=PDUType.STACK_PUSH,
                    seq_num=game_state.get_next_seq_num(),
                    stack_item_id=stack_item_id,
                    item_type="TRIGGER_ABILITY",
                    source=trigger["source_id"],
                    targets=[],
                    controller=p_id
                )
                pdu_list.append(push_pdu)
                logging.info(f"Trigger {trigger['trigger_id']} from {trigger['source_id']} pushed to stack.")

        return pdu_list
