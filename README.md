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
├── shared/                       # 1. Independent JSON Set-Up & Card Catalog
│   ├── schemas/                  # JSON schemas for PDU validation (e.g., CAST_SPELL)
│   ├── types/                    # Shared data types/interfaces
│   └── data/
│       └── cards.json            # The static out-of-band card catalog
│
├── server/                       # 2. Independent Server Modules      
│   ├── src/
│   │   ├── network/              # The TCP Connection (Listens on port 4444, Framer)
│   │   ├── middleware/           # Sequence & Priority Validator
│   │   ├── controllers/          # MVC Controllers (Routes PDUs to the Engine)
│   │   ├── engine/               # Game States & Calculations (Core rules, Stack LIFO)
│   │   └── server.py              # Server entry point
│   └── tests/                    # Automated tests for game state logic
│
├── client/                       # 3. Independent Thin Client UI
│   ├── src/
│   │   ├── components/           # UI rendering (Battlefield, Hand, Stack)
│   │   ├── network/              # Client-side TCP wrapper to send/receive PDUs
│   │   ├── context/              # React state to hold the GAME_STATE_UPDATE
│   │   └── client.py             # Client entry point
│   └── public/
│
├── .gitignore
└── README.md
```
## Instructions
### How to Build
### How to Run
### Enabling Verbose Mode

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
| TCP Server: connection handling, framing, dispatch | - | - | - | - | - | 
| Game lifecycle: LOBBY, GAME_SETUP, MULLIGAN logic | - | - | - | - | - | 
| Turn & phase engine (all phases/steps, transitions) | - | - | - | - | - | 
| Priority & Stack logic, spell/ability resolution | - | - | - | - | - | 
| Combat system (attackers, blockers, damage) | - | - | - | - | - | 
| Client implementation & state rendering | - | - | - | - | - | 
| PDU serialization/deserialization (all 25 PDU types) | - | - | - | - | - | 
| Error handling, PING/PONG heartbeat, disconnect logic| - | - | - | - | - | 
| Verbose mode (client + server PDU logging, toggle on/off) | - | - | - | - | - | 
| Testing & interoperability | - | - | - | - | - |
| README / documentation / AI disclosure |  - | - | - | - | - | 


## AI Usage
> **Policy Reminder:** AI tools are permitted as learning aids. All AI-assisted code must be fully tested, verified, and understood by all team members. Blind copying, untested code, or sharing outputs across groups is prohibited.

| Tool Name | Feature / Purpose | Specific Scope / Modules | Description of Assistance |
| :--- | :--- | :--- | :--- |
| *e.g., ChatGPT (GPT-4o)* | *Debugging / Syntax* | `src/network/socket_handler.py` | *Helped resolve non-blocking socket handling edge cases.* |
## Known Limitation or Deviations from the RFC
