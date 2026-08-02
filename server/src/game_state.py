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
        self.life: int = 20
        self.mulligan_count: int = 0
        self.has_kept_hand: bool = False

    def draw_cards(self, count: int) -> List[str]:
        """
        draw_cards - removes cards from library and appends to hand
        """
        drawn = []
        for _ in range(count):
            if self.library:
                drawn.append(self.library.pop(0))
        self.hand.extend(drawn)
        return drawn

    def shuffle_library(self):
        random.shuffle(self.library)

    def reset_hand_to_library(self):
        """
        Moves all hand cards to library and reshuffles
        """
        self.library.extend(self.hand)
        self.hand.clear()
        self.shuffle_library()

class GameState:
    """
    Maintains the global game state including the current phase, 
    socket and player mappings (client-server connections).
    """

    def __init__(self):
        self.phase: str = "LOBBY"  # Options: LOBBY, MULLIGAN, IN_GAME
        self.players: Dict[str, PlayerState] = {}
        self.socket_to_player: Dict[Any, str] = {}
        self.player_sockets: Dict[str, Any] = {}
        self.active_player: Optional[str] = None
        self.seq_num: int = 1
        
    def get_next_seq_num(self) -> int:
        """
        Generates an incrementing sequence number for every 
        message or state change issued by the server.

        Used to keep track of turn updates and grant requests.
        """

        current = self.seq_num
        self.seq_num += 1
        return current

    def get_lobby_state_dict(self) -> Dict[str, Any]:
        """
        Builds a dictionary containing the current lobby 
        setup state so it can be sent inside a 
        GAME_STATE_UPDATE PDU.
        """
        ready_count = len(self.players)
        waiting_for = []
        if ready_count < 2:
            waiting_for.append("WAITING_FOR_PLAYERS")
            
        return {
            "phase": "LOBBY",
            "players_ready": ready_count,
            "waiting_for": waiting_for
        }

    def initialize_game(self):
        """
        Initializes life totals to 20, shuffles libraries, 
        draws 7 cards, and picks active player.
        """
        player_ids = list(self.players.keys())
        for p_id in player_ids:
            p_state = self.players[p_id]
            p_state.life = 20 # starting life points
            p_state.shuffle_library()
            p_state.draw_cards(7) # 7 cards each player

        # Coin flip for active player
        self.active_player = random.choice(player_ids)
        self.phase = "MULLIGAN"

    def is_all_mulligans_resolved(self) -> bool:
        return all(p.has_kept_hand for p in self.players.values())