# Magic: The Gathering Multiplayer Network Protocol
This machine problem requires you to implement a networked two-player card game system following the Magic: The Gathering Multiplayer Network Protocol (MTGNP) v1.0, as defined in RFC 0001 (CSNETWK). Magic: The Gathering is one of the most rules-dense games ever designed. This is intentional: the protocol reflects exactly the kind of layered, stateful, and specification-heavy system you will encounter when working with real-world network protocols. Successfully implementing MTGNP is not about memorizing card game rules; it is about demonstrating that you can read a formal specification and build something that faithfully follows it.

## Game Scope
This document specifies a simplified subset of the full MTG rules. Specifically, the following limitations apply to MTGNP 1.0:
* Exactly two players per game.
* Decks of between 1 and 50 cards each, drawn from a fixed, pre-defined card set. Both players may use different deck sizes.
* No replacement effects.
* No planeswalker permanents.
* No match structure (no best-of-three). After GAME_OVER, both players may immediately start a new game on the same TCP connection by sending fresh PLAYER_READY PDUs.

## Features
- **TCP Connection & Framing Manager** - The protocol operates exclusively over TCP. The framer reads exactly a 4-byte, big-endian unsigned integer to determine the length of the incoming JSON payload before parsing.
- **Authoritative State Engine** - The server is the absolute single source of truth. It must maintain the state of all zones (library, hand, battlefield, graveyard, stack), life totals, and current phases. Structuring this state logic requires strict entity management, similar to designing robust table schemas for administrative DBMS records.
- **Thin Client Renderer** - Clients must never compute game outcomes. The client's only jobs are to maintain a local rendering of the "Visible State", accept updates from the server, and send user actions.
- **Priority & Sequence Controller** - Priority windows allow players to act at nearly every point in a turn. The server must generate a monotonically increasing seq_num for every Priority Grant, and the client must echo this exact number back in their action PDUs to prevent stale actions.
- **Stack LIFO Resolver** - The server implements a Last-In, First-Out (LIFO) data structure for the stack. It must evaluate state-based actions (like creature death from 0 toughness) before granting priority, and resolve spells top-down only when both players pass priority consecutively.
- **Combat Sub-State Machine** - The combat phase is highly regulated, requiring distinct transitions for declaring attackers, declaring blockers, ordering damage for multi-blocked attackers, and calculating simultaneous combat damage.

## Requirements

## Project Structure
```
CSNETWK-MTGNP/
├── .vscode/
│   └── settings.json                 # Workspace & editor settings
│
├── client/
│   └── src/                          # 1. Thin Client Application
│       ├── client-combat.py          # Outdated version of client.py
│       ├── client-test.py            # Testing area of client.py
│       ├── client.py                 # Final client entry point (player)
│       └── rubric-test.py            # Rubric compliance validation testing
│
├── server/
│   └── src/                          # 2. Server & Game Engine
│       ├── server.py                 # Server entry point (TCP socket listener)
│       ├── game_engine.py            # Core rules execution, combat, & stack mechanics
│       ├── game_state.py             # Board state, player data, & CardInstance definitions
│       ├── lobby.py                  # Room management & player matchmaking
│       ├── framer.py                 # Message stream framing & PDU length prefixing
│       └── schemas.py                # JSON payload schema validation
│
├── shared/                           # 3. Shared Resources & Data
│   ├── cards_catalog.json            # Out-of-band static card catalog database
│   ├── data/                         # Shared data storage directory
│   └── util/                         # Common utility modules
│       └── logger_util.py            # Structured logging helper
│
├── .gitignore                        # Git file tracking exclusion rules
├── pyproject.toml                    # Python project packaging & metadata setup
└── README.md                         # Project documentation and setup instructions
```
## Instructions
### Prerequisites
* **Python:** `3.10` or higher
### Installation & Setup
1. (Optional) If you are downloading this from repository, clone it first:
```bash
git clone https://github.com/Sorrypre/CSNETWK-MTGNP.git
cd CSNETWK-MTGNP
```
2. Install the package dependencies:
```bash
pip install -e .
```
### Running the Game
A complete game session requires **1 Server** instance and **2 Client** instances running concurrently.
* Terminal 1 (Server):
```bash
python server/src/server.py
```
* Terminal 2 (Player 1 Client):
```bash
python client/src/client.py
```
* Terminal 3 (Player 2 Client):
```bash
python client/src/client.py
```
### Running Verbose
To print all PDUs sent and received in both client and server-side, use the command-line flag stated below:
* For the `server.py `
```bash
python server/src/server.py --verbose
```
* For the `client.py `
```bash
python client/src/client.py --verbose
```
* You can also use the short `-v`
* You can also use `-h` for more information
## Members
**Member 1** - Joramm Dela Torre  
**Member 2** - Jensel Espada  
**Member 3** - Kurt Laguerta  
**Member 4** - VL Kirsten (Kei) Saguin  
## Work Distribution Matrix
A detailed report of tasks implemented by each member

<!-- If you are going to put your contribution please just copy paste this check symbol  ✓ for consistency -->
| Task/Feature | Member 1 | Member 2 | Member 3 | Member 4 |
| --- | ---- | --- | --- | --- |
| TCP Server: connection handling, framing, dispatch | - | - | - | ✓ |
| Game lifecycle: LOBBY, GAME_SETUP, MULLIGAN logic | ✓ | - | ✓ | ✓ |
| Turn & phase engine (all phases/steps, transitions) | ✓ | ✓ | ✓ | - |
| Priority & Stack logic, spell/ability resolution | ✓ | - | ✓ | - |
| Combat system (attackers, blockers, damage) | - | ✓ | - | - |
| Client implementation & state rendering | ✓ | - | ✓ | - |
| PDU serialization/deserialization (all 25 PDU types) | ✓ | ✓ | ✓ | - |
| Error handling, PING/PONG heartbeat, disconnect logic| ✓ | ✓ | ✓ | ✓ |
| Verbose mode (client + server PDU logging, toggle on/off) | ✓ | ✓ | - | - |
| Testing & interoperability | ✓ | ✓ | ✓ | ✓ |
| README / documentation / AI disclosure |  ✓ | ✓ | ✓ | ✓ |


## AI Usage
> **Policy Reminder:** AI tools are permitted as learning aids. All AI-assisted code must be fully tested, verified, and understood by all team members. Blind copying, untested code, or sharing outputs across groups is prohibited.

| Tool Name | Feature / Purpose | Specific Scope / Modules | Description of Assistance |
| :--- | :--- | :--- | :--- |
| Gemini v3.6 Flash | *Understanding MTG Concepts based on RFC* | N/A | *Helped on digesting information from the given specifications for the project.* |
| Gemini v3.1 Pro Extended | *Implementation and Rechecking Program Logic* | `server/src/game_engine.py`, `server/src/game_state.py` | *Assisted on the mechanisms in order to implement features involved + rechecking of code logic* |
| Gemini v3.6 Thinking | *Parsing Google Sheets to JSON*  | `shared/cards_catalog.json` | *Helped in creating a python file that automatically transforms the table given in the google sheets into a JSON format.* |
| Gemini v3.6 Thinking | *Validation and Understanding of Battle Phase Logic* | `shared/src/server.py`, `shared/src/game_state.py`,  `shared/src/game_engine.py` | *Helped in implementing attacker and blocker logic in the battle phase.* |
| Gemini v3.6 Thinking | *Debugging Combat Phase*  | `shared/src/server.py`, `shared/src/game_state.py`,  `shared/src/game_engine.py` | *Helped identify some missing requirements in the RFC implementation and also the missing attributes inside the classes in the game_state.py. This also helped me identify why there is a non matching sequence num and grant sequence num.* |
| Gemini v3.6 Flash | *Syntax* | server/src/framer.py, server/src/game_state.py | *Helped polish syntax based on initial draft of states and implement features involved.* |
| Gemini v3.6 Flash | *Implementation and Rechecking Program Logic* | server/src/lobby.py | *Assisted on the python syntax of implementing pseudocode for the features involved + rechecking of code logic.* |

## Known Limitation or Deviations from the RFC
### Engine & Rules Deviations
* Exile Zone Mechanics: Spells such as swords_to_plowshares and path_to_exile apply 999 damage to destroy creatures via State-Based Actions rather than sending cards to a distinct Exile zone or resolving secondary spell logic (e.g., land searching, life conversion).
* Modal & Payment Prompts: healing_salve resolves directly to life gain without presenting a modal prompt, and mana_leak counters target spells immediately without giving the target player an option to pay {3} generic mana.
* Static Keyword Parsing: Keywords like trample, defender, hexproof, and vigilance present in cards_catalog.json are not extracted during CardInstance initialization.
* Unhandled Catalog Cards: Utility cards defined in cards_catalog.json—including dark_ritual, sol_ring, ponder, pacifism, millstone, and merfolk_looter—lack execution handlers in apply_spell_ability_effect.
* Simplified Priority System: Fast-effects and instant-speed responses operate on a simplified LIFO stack push/pop cycle rather than offering strict priority-passing windows across every step transition.
### Networking & Protocol (RFC) Limitations
* Connection Drop & Reconnect: Mid-game disconnections are not recoverable; if a socket closes, the server terminates the game session instead of restoring game_state.
* Out-of-Order PDU Handling: The framing layer processes incoming JSON payloads sequentially using stream buffer delimiters (framer.py); out-of-order or corrupt PDUs trigger an immediate error response rather than auto-retransmit requests.
* Concurrency & Timing Constraints: The lobby supports pair-based match initialization but does not support multi-room concurrent game execution or spectator mode.

