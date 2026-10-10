---
title: "Generated protocol inventory"
status: "generated"
authoritative_source: "server FastAPI OpenAPI output and versioned schemas/*.json"
verified: "b470bd3a9041cf3d346fd5edd332931e6963dae3fbe2b5ad704701a84a403dc9"
audience: "client, server, and protocol contributors"
maintenance: "generated"
generated_source: "coverage/protocol-inventory.json"
generation_command: ".\.venv\Scripts\python.exe scripts\update_protocol_reference.py --write"
---

# Generated protocol inventory

Source fingerprint: `7346343d4a1dddb57b408c40c7d82596e47100b4d5e7b7f26cb1d83cb682c8cb`

## Current top-level state

- API title: `Quorune Server`
- API version: `0.9.0`
- HTTP operations: `24`
- WebSocket routes: `1`
- Versioned schemas: `17`

## Top blockers

- None detected by the inventory generator.

## HTTP operations

| Method | Path | Operation ID |
| --- | --- | --- |
| `GET` | `/api/v1/cards/{oracle_prefix}/image` | `card_image_api_v1_cards__oracle_prefix__image_get` |
| `GET` | `/api/v1/games/{game_id}` | `inspect_game_api_v1_games__game_id__get` |
| `POST` | `/api/v1/games/{game_id}/commands` | `submit_command_api_v1_games__game_id__commands_post` |
| `GET` | `/api/v1/games/{game_id}/events` | `public_game_events_api_v1_games__game_id__events_get` |
| `GET` | `/api/v1/games/{game_id}/progress` | `game_progress_api_v1_games__game_id__progress_get` |
| `POST` | `/api/v1/games/{game_id}/resume` | `resume_game_api_v1_games__game_id__resume_post` |
| `GET` | `/api/v1/games/{game_id}/state` | `game_state_api_v1_games__game_id__state_get` |
| `POST` | `/api/v1/games/{game_id}/stop` | `stop_game_api_v1_games__game_id__stop_post` |
| `POST` | `/api/v1/guests` | `create_guest_api_v1_guests_post` |
| `GET` | `/api/v1/health` | `health_api_v1_health_get` |
| `GET` | `/api/v1/me` | `me_api_v1_me_get` |
| `POST` | `/api/v1/rooms` | `create_room_api_v1_rooms_post` |
| `POST` | `/api/v1/rooms/join` | `join_room_api_v1_rooms_join_post` |
| `POST` | `/api/v1/rooms/watch` | `watch_room_api_v1_rooms_watch_post` |
| `GET` | `/api/v1/rooms/{room_id}` | `get_room_api_v1_rooms__room_id__get` |
| `DELETE` | `/api/v1/rooms/{room_id}/deck` | `clear_deck_api_v1_rooms__room_id__deck_delete` |
| `PUT` | `/api/v1/rooms/{room_id}/deck` | `upload_deck_api_v1_rooms__room_id__deck_put` |
| `POST` | `/api/v1/rooms/{room_id}/invite` | `rotate_room_invite_api_v1_rooms__room_id__invite_post` |
| `DELETE` | `/api/v1/rooms/{room_id}/membership` | `leave_room_api_v1_rooms__room_id__membership_delete` |
| `POST` | `/api/v1/rooms/{room_id}/replace` | `replace_room_api_v1_rooms__room_id__replace_post` |
| `DELETE` | `/api/v1/rooms/{room_id}/seats/{seat}` | `remove_room_seat_api_v1_rooms__room_id__seats__seat__delete` |
| `POST` | `/api/v1/rooms/{room_id}/start` | `start_game_api_v1_rooms__room_id__start_post` |
| `GET` | `/api/v1/system` | `system_status_api_v1_system_get` |
| `POST` | `/api/v1/system/refresh` | `refresh_system_api_v1_system_refresh_post` |

## WebSocket routes

- `/api/v1/games/{game_id}/stream`

Complete request, response, route, and schema summaries are in the
[machine-readable protocol inventory](../../coverage/protocol-inventory.json).
The versioned schema bodies remain authoritative in [`schemas/`](../../schemas/).

Exact generation command:

```powershell
.\.venv\Scripts\python.exe scripts\update_protocol_reference.py --write
```
