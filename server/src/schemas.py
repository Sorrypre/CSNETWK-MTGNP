from pydantic import BaseModel, Field
from typing import Literal, List, Dict, Any, Optional, Union

#COMPONENTS
class LobbyState(BaseModel):
    phase: Literal["LOBBY"]
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
    type: Literal["PLAYER_READY"]
    seq_num: int
    player_id: str = Field(min_length=1)
    deck_list: List[str] = Field(min_length=1, max_length=50)

class GameStateUpdate(BaseModel):
    type: Literal["GAME_STATE_UPDATE"]
    seq_num: int
    state: Union[LobbyState, Dict[str, Any]]

class MulliganChoice(BaseModel):
    type: Literal["MULLIGAN_CHOICE"]
    seq_num: int
    keep: bool
    cards_to_bottom: List[str]

class PhaseTransition(BaseModel):
    type: Literal["PHASE_TRANSITION"]
    seq_num: int
    from_phase: str
    to_phase: str
    active_player: str
    turn: int

class PriorityGrant(BaseModel):
    type: Literal["PRIORITY_GRANT"]
    seq_num: int
    player_id: str = Field(min_length=1)
    time_limit_ms: int

class PriorityPass(BaseModel):
    type: Literal["PRIORITY_PASS"]
    seq_num: int

class CastSpell(BaseModel):
    type: Literal["CAST_SPELL"]
    seq_num: int
    card_id: str
    targets: List[str]
    mana_payment: Dict[str, int]

class ActivateAbility(BaseModel):
    type: Literal["ACTIVATE_ABILITY"]
    seq_num: int
    source_id: str
    ability_index: int
    targets: List[str]
    cost_payment: Dict[str, Any]

class StackPush(BaseModel):
    type: Literal["STACK_PUSH"]
    seq_num: int
    stack_item_id: str
    item_type: Literal["SPELL", "ABILITY", "TRIGGER_ABILITY"]
    source: str
    targets: List[str]
    controller: str

class TriggerOrder(BaseModel):
    type: Literal["TRIGGER_ORDER"]
    seq_num: int
    player_id: str
    trigger_ids: List[str]

class TriggerOrderResponse(BaseModel):
    type: Literal["TRIGGER_ORDER_RESPONSE"]
    seq_num: int
    ordered_trigger_ids: List[str]

class TriggerChoice(BaseModel):
    type: Literal["TRIGGER_CHOICE"]
    seq_num: int
    trigger_id: str
    source_id: str
    effect_summary: str
    legal_targets: List[str]
    requires_target: bool

class TriggerChoiceResponse(BaseModel):
    type: Literal["TRIGGER_CHOICE_RESPONSE"]
    seq_num: int
    trigger_id: str
    accept: bool
    chosen_target: Optional[str] = None

class StackResolve(BaseModel):
    type: Literal["STACK_RESOLVE"]
    seq_num: int
    stack_item_id: str
    result: Literal["RESOLVED", "FIZZLE"]
    state_changes: List[Dict[str, Any]]

class DeclareAttackers(BaseModel):
    type: Literal["DECLARE_ATTACKERS"]
    seq_num: int
    attackers: List[AttackerAssignment]

class DeclareBlockers(BaseModel):
    type: Literal["DECLARE_BLOCKERS"]
    seq_num: int
    blockers: List[BlockerAssignment]

class AssignDamageOrder(BaseModel):
    type: Literal["ASSIGN_DAMAGE_ORDER"]
    seq_num: int
    attacker_id: str
    blocker_order: List[str]

class CombatDamageResult(BaseModel):
    type: Literal["COMBAT_DAMAGE_RESULT"]
    seq_num: int
    damage_events: List[Dict[str, Any]]
    life_totals: Dict[str, int]
    creatures_died: List[str]

class PlayLand(BaseModel):
    type: Literal["PLAY_LAND"]
    seq_num: int
    card_id: str

class Discard(BaseModel):
    type: Literal["DISCARD"]
    seq_num: int
    card_ids: List[str] = Field(min_length=1, max_length=50)

class Concede(BaseModel):
    type: Literal["CONCEDE"]
    seq_num: int
    player_id: str

class GameOver(BaseModel):
    type: Literal["GAME_OVER"]
    seq_num: int
    winner_id: str
    loser_id: str
    reason: Literal["LIFE_ZERO", "DECK_EMPTY", "CONCEDE", "DISCONNECT"]

class Error(BaseModel):
    type: Literal["ERROR"]
    seq_num: int
    code: str
    message: str = Field(min_length=1)
    rejected_action: Optional[Dict[str, Any]] = None

#PING PONG PDUs

class Ping(BaseModel):
    type: Literal["PING"]
    seq_num: int
    timestamp: int

class Pong(BaseModel):
    type: Literal["PONG"]
    seq_num: int
    timestamp: int