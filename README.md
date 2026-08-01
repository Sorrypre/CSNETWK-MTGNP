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

## Requirements

## Project Structure

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
