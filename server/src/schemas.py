from pydantic import BaseModel, Field
from typing import Literal, List, Dict, Any, Optional, Union
# strenumhttps://docs.python.org/3/library/enum.html
from enum import StrEnum

# Centralized PDU Types
class PDUType(StrEnum):
    LOBBY = "LOBBY"
    PLAYER_READY = "PLAYER_READY"
    GAME_STATE_UPDATE = "GAME_STATE_UPDATE"
    MULLIGAN_CHOICE = "MULLIGAN_CHOICE"
    PHASE_TRANSITION = "PHASE_TRANSITION"
    PRIORITY_GRANT = "PRIORITY_GRANT"
    PRIORITY_PASS = "PRIORITY_PASS"
    CAST_SPELL = "CAST_SPELL"
    ACTIVATE_ABILITY = "ACTIVATE_ABILITY"
    STACK_PUSH = "STACK_PUSH"
    TRIGGER_ORDER = "TRIGGER_ORDER"
    TRIGGER_ORDER_RESPONSE = "TRIGGER_ORDER_RESPONSE"
    TRIGGER_CHOICE = "TRIGGER_CHOICE"
    TRIGGER_CHOICE_RESPONSE = "TRIGGER_CHOICE_RESPONSE"
    STACK_RESOLVE = "STACK_RESOLVE"
    DECLARE_ATTACKERS = "DECLARE_ATTACKERS"
    DECLARE_BLOCKERS = "DECLARE_BLOCKERS"
    ASSIGN_DAMAGE_ORDER = "ASSIGN_DAMAGE_ORDER"
    COMBAT_DAMAGE_RESULT = "COMBAT_DAMAGE_RESULT"
    PLAY_LAND = "PLAY_LAND"
    DISCARD = "DISCARD"
    CONCEDE = "CONCEDE"
    GAME_OVER = "GAME_OVER"
    ERROR = "ERROR"
    PING = "PING"
    PONG = "PONG"
#COMPONENTS
class LobbyState(BaseModel):
    phase: PDUType.LOBBY
    players_ready: int
    waiting_for: List[str]

class BlockerAssignment(BaseModel):
    creature_id: str
    blocking_id: str

class AttackerAssignment(BaseModel):
    creature_id: str
    target: str


#PROTOCOL DATA UNITS (PDUs)
class PlayerReady(BaseModel):
    type: PDUType.PLAYER_READY
    seq_num: int
    player_id: str = Field(min_length=1)
    deck_list: List[str] = Field(min_length=1, max_length=50)

class GameStateUpdate(BaseModel):
    type: PDUType.GAME_STATE_UPDATE
    seq_num: int
    state: Union[LobbyState, Dict[str, Any]]

class MulliganChoice(BaseModel):
    type: PDUType.MULLIGAN_CHOICE
    seq_num: int
    keep: bool
    cards_to_bottom: List[str]

class PhaseTransition(BaseModel):
    type: PDUType.PHASE_TRANSITION
    seq_num: int
    from_phase: str
    to_phase: str
    active_player: str
    turn: int

class PriorityGrant(BaseModel):
    type: PDUType.PRIORITY_GRANT
    seq_num: int
    player_id: str = Field(min_length=1)
    time_limit_ms: int

class PriorityPass(BaseModel):
    type: PDUType.PRIORITY_PASS
    seq_num: int

class CastSpell(BaseModel):
    type: PDUType.CAST_SPELL
    seq_num: int
    card_id: str
    targets: List[str]
    mana_payment: Dict[str, int]

class ActivateAbility(BaseModel):
    type: PDUType.ACTIVATE_ABILITY
    seq_num: int
    source_id: str
    ability_index: int
    targets: List[str]
    cost_payment: Dict[str, Any]

class StackPush(BaseModel):
    type: PDUType.STACK_PUSH
    seq_num: int
    stack_item_id: str
    item_type: Literal["SPELL", "ABILITY", "TRIGGER_ABILITY"]
    source: str
    targets: List[str]
    controller: str

class TriggerOrder(BaseModel):
    type: PDUType.TRIGGER_ORDER
    seq_num: int
    player_id: str
    trigger_ids: List[str]

class TriggerOrderResponse(BaseModel):
    type: PDUType.TRIGGER_ORDER_RESPONSE
    seq_num: int
    ordered_trigger_ids: List[str]

class TriggerChoice(BaseModel):
    type: PDUType.TRIGGER_CHOICE
    seq_num: int
    trigger_id: str
    source_id: str
    effect_summary: str
    legal_targets: List[str]
    requires_target: bool

class TriggerChoiceResponse(BaseModel):
    type: PDUType.TRIGGER_CHOICE_RESPONSE
    seq_num: int
    trigger_id: str
    accept: bool
    chosen_target: Optional[str] = None

class StackResolve(BaseModel):
    type: PDUType.STACK_RESOLVE
    seq_num: int
    stack_item_id: str
    result: Literal["RESOLVED", "FIZZLE"]
    state_changes: List[Dict[str, Any]]

class DeclareAttackers(BaseModel):
    type: PDUType.DECLARE_ATTACKERS
    seq_num: int
    attackers: List[AttackerAssignment]

class DeclareBlockers(BaseModel):
    type: PDUType.DECLARE_BLOCKERS
    seq_num: int
    blockers: List[BlockerAssignment]

class AssignDamageOrder(BaseModel):
    type: PDUType.ASSIGN_DAMAGE_ORDER
    seq_num: int
    attacker_id: str
    blocker_order: List[str]

class CombatDamageResult(BaseModel):
    type: PDUType.COMBAT_DAMAGE_RESULT
    seq_num: int
    damage_events: List[Dict[str, Any]]
    life_totals: Dict[str, int]
    creatures_died: List[str]

class PlayLand(BaseModel):
    type: PDUType.PLAY_LAND
    seq_num: int
    card_id: str

class Discard(BaseModel):
    type: PDUType.DISCARD
    seq_num: int
    card_ids: List[str] = Field(min_length=1, max_length=50)

class Concede(BaseModel):
    type: PDUType.CONCEDE
    seq_num: int
    player_id: str

class GameOver(BaseModel):
    type: PDUType.GAME_OVER
    seq_num: int
    winner_id: str
    loser_id: str
    reason: Literal["LIFE_ZERO", "DECK_EMPTY", "CONCEDE", "DISCONNECT"]

class Error(BaseModel):
    type: PDUType.ERROR
    seq_num: int
    code: str
    message: str = Field(min_length=1)
    rejected_action: Optional[Dict[str, Any]] = None

#PING PONG PDUs

class Ping(BaseModel):
    type: PDUType.PING
    seq_num: int
    timestamp: int

class Pong(BaseModel):
    type: PDUType.PONG
    seq_num: int
    timestamp: int