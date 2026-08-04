import random
from typing import Dict, List, Optional, Any

class PlayerState:
    """
    Maintains the state of all zones (library, hand, battlefield, graveyard, stack/deck),
    total life points, mulligan count, how many cards in hand and if the player
    kept their hand.

    Helper functions are also present for the player to use:
    """

    def __init__(self, player_id: str, deck_list: List[str]):
        self.player_id: str = player_id
        self.raw_deck: List[str] = list(deck_list)
        self.library: List[str] = list(deck_list)
        self.hand: List[str] = []
        self.graveyard: List[str] = []
        self.battlefield: List[Dict[str, Any]] = []
        self.exile: List[str] = []
        self.lands_played_this_turn: int = 0
        self.life: int = 20
        self.mulligan_count: int = 0
        self.has_kept_hand: bool = False
        self.empty_deck_draw = False

    def draw_cards(self, count: int) -> List[str]:
        """
        draw_cards - removes cards from library and appends to hand
        """
        drawn = []
        for _ in range(min(count, len(self.library))):
            if self.library:
                drawn.append(self.library.pop(0))
            else:
                self.empty_deck_draw = True

        self.hand.extend(drawn)
        return drawn

    def reset_hand_to_library(self):
        """
        Moves all hand cards to library and reshuffles
        """
        self.library.extend(self.hand)
        self.hand.clear()
        random.shuffle(self.library)

class GameState:
    """
    Maintains the global game state including the current phase, 
    socket and player mappings (client-server connections).
    """

    def __init__(self):
        self.phase: str = "LOBBY"  # Options: LOBBY, MULLIGAN, IN_GAME, FINISHED
        self.players: Dict[str, PlayerState] = {}
        self.socket_to_player: Dict[Any, str] = {}
        self.player_sockets: Dict[str, Any] = {}
        self.active_player: Optional[str] = None
        self.seq_num: int = 1

        self.turn_number: int = 1
        self.current_turn_phase: str = "BEGINNING" # BEGINNING, MAIN_1, COMBAT, MAIN_2, END
        self.current_step: str = "UNTAP"           # UNTAP, DRAW, MAIN, DECLARE_ATTACKERS, etc.
        self.priority_player: Optional[str] = None
        self.passes_in_a_row: int = 0
        self.stack: List[Dict[str, Any]] = []      # Pending spells / abilities on the stack
        
    def get_next_seq_num(self) -> int:
        """
        Generates an incrementing sequence number for every 
        message or state change issued by the server.

        Used to keep track of turn updates and grant requests.
        """

        current = self.seq_num
        self.seq_num += 1
        return current

    def initialize_game(self):
        """
        Initializes life totals to 20, shuffles libraries, 
        draws 7 cards, and picks active player.
        """
        for p in self.players.values():
            p.life = 20 # starting life points
            random.shuffle(p.library)
            p.draw_cards(7) # 7 cards each player

        # Coin flip for active player
        self.active_player = random.choice(list(self.players.keys()))
        self.priority_player = self.active_player
        self.phase = "MULLIGAN"

    def is_all_mulligans_resolved(self) -> bool:
        return len(self.players) == 2 and all(p.has_kept_hand for p in self.players.values())

    def start_main_game(self):
        """Transitions state from Mulligan into Phase 3 execution."""
        self.phase = "IN_GAME"
        self.current_turn_phase = "BEGINNING"
        self.current_step = "UNTAP"
        self.priority_player = self.active_player

    def reset_game_state(self):
        """"
        Wipes out game state to prepare for a new game but retain socket connections.
        Moves back to LOBBY phase
        """
        self.phase = "LOBBY"
        self.players = {}
        self.stack = []
        self.current_step = None
        self.priority_player = None
        self.active_player = None
        self.turn_number = 0
        self.passes_in_a_row = 0

