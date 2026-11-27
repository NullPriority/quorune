from __future__ import annotations

from contextlib import closing
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from common import DB_PATH, ROOT
from quorune import CardDatabase
from quorune.card_programs.binding import bind_card_program_runtime
from quorune.card_programs.trust import FORMAT_PROFILE_CAPABILITIES
from quorune.record import database_fingerprint, replay_record
from server import ServerSettings, create_app
from server.app import COOKIE_NAME, CSRF_COOKIE_NAME, _websocket_origin_allowed


class ServerApplicationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        static_dir = root / "browser"
        static_dir.mkdir()
        (static_dir / "index.html").write_text(
            "<!doctype html><title>Quorune test</title>",
            encoding="utf-8",
        )
        card_db = root / "cards.sqlite3"
        shutil.copy2(DB_PATH, card_db)
        self.settings = ServerSettings(
            card_db=card_db,
            database=root / "server.sqlite3",
            game_root=root / "games",
            card_snapshot_dir=root / "card-snapshots",
            static_dir=static_dir,
        )
        self.client = TestClient(create_app(self.settings))
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.temp.cleanup()

    def restart_client(self) -> None:
        self.client.__exit__(None, None, None)
        self.client = TestClient(create_app(self.settings))
        self.client.__enter__()

    def guest(self, name: str) -> tuple[str, str]:
        response = self.client.post(
            "/api/v1/guests", json={"display_name": name}
        )
        self.assertEqual(201, response.status_code, response.text)
        return response.json()["access_token"], response.json()["csrf_token"]

    @staticmethod
    def auth(token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}

    def set_existing_cookie(self, name: str, value: str) -> None:
        cookies = [cookie for cookie in self.client.cookies.jar if cookie.name == name]
        self.assertEqual(1, len(cookies), "Expected one server-issued cookie scope")
        cookie = cookies[0]
        self.client.cookies.set(name, value, domain=cookie.domain, path=cookie.path)

    def test_deck_upload_accepts_printed_copy_limits_and_rejects_the_next_copy(self):
        player, _ = self.guest("Copy Limit Pilot")
        created = self.client.post("/api/v1/rooms", headers=self.auth(player), json={"seed": 9035})
        self.assertEqual(201, created.status_code, created.text)
        room = created.json()["room"]["room_id"]
        cases = (
            ("Persistent Petitioners", 2, "Island", "Azami, Lady of Scrolls", 200),
            ("Seven Dwarves", 7, "Mountain", "Krenko, Tin Street Kingpin", 200),
            ("Seven Dwarves", 8, "Mountain", "Krenko, Tin Street Kingpin", 409),
            ("Nazgûl", 9, "Swamp", "Drana, Kalastria Bloodchief", 200),
            ("Nazgûl", 10, "Swamp", "Drana, Kalastria Bloodchief", 409),
        )
        for name, copies, land, commander, status in cases:
            with self.subTest(card=name, copies=copies):
                response = self.client.put(f"/api/v1/rooms/{room}/deck", headers=self.auth(player), json={
                    "name": f"Printed copy limit for {name}", "commander": commander,
                    "decklist": f"Commander:\n1 {commander}\nMainboard:\n{copies} {name}\n{99 - copies} {land}\n",
                })
                self.assertEqual(status, response.status_code, response.text)
                if status == 200:
                    self.assertEqual("legal", response.json()["format_legality"]["status"])
                else:
                    self.assertIn("printed limit", response.text)

    def test_deck_upload_distinguishes_card_rules_from_match_rules(self):
        player, _ = self.guest("Rules Readiness Pilot")
        response = self.client.post("/api/v1/rooms", headers=self.auth(player), json={})
        self.assertEqual(201, response.status_code, response.text)
        room = response.json()["room"]["room_id"]
        deck = {
            "name": "Rules readiness",
            "commander": "Krenko, Tin Street Kingpin",
            "decklist": "Commander:\n1 Krenko, Tin Street Kingpin\nMainboard:\n99 Mountain\n",
        }

        def submit():
            with patch.dict(FORMAT_PROFILE_CAPABILITIES, {"commander_review": ()}):
                response = self.client.put(
                    f"/api/v1/rooms/{room}/deck", headers=self.auth(player), json=deck
                )
            self.assertEqual(200, response.status_code, response.text)
            return response.json()["preflight"]

        ready = submit()
        self.assertEqual(100, ready["fully_playable_cards"])
        self.assertTrue(ready["card_rules_ready"])
        self.assertFalse(ready["trusted_only_ready"])

        commander = self.client.app.state.runtime.card_db.lookup(deck["commander"])

        def missing_commander_binding(program, **kwargs):
            binding = bind_card_program_runtime(program, **kwargs)
            if program.oracle_id == commander.oracle_id:
                binding["strict_capability_ready"] = False
            return binding

        with patch("quorune.preflight.bind_card_program_runtime", side_effect=missing_commander_binding):
            blocked = submit()
        self.assertFalse(blocked["card_rules_ready"])
        self.assertFalse(blocked["trusted_only_ready"])

    def test_websocket_origin_accepts_exact_same_origin_and_rejects_foreign(self):
        configured = ("http://127.0.0.1:5173",)
        self.assertTrue(
            _websocket_origin_allowed(
                "http://127.0.0.1:8000",
                "127.0.0.1:8000",
                configured,
            )
        )
        self.assertTrue(
            _websocket_origin_allowed(
                "http://127.0.0.1:5173",
                "127.0.0.1:8000",
                configured,
            )
        )
        self.assertFalse(
            _websocket_origin_allowed(
                "https://attacker.example",
                "127.0.0.1:8000",
                configured,
            )
        )

    def test_submitted_deck_readiness_separates_snapshot_drift_and_private_card_reasons(self):
        from unittest.mock import patch

        host, _ = self.guest("Deck owner")
        guest, _ = self.guest("Second seat")
        created = self.client.post("/api/v1/rooms", headers=self.auth(host), json={"player_count": 2})
        self.assertEqual(201, created.status_code, created.text)
        room = created.json()["room"]["room_id"]
        invite = created.json()["invite_code"]
        joined = self.client.post("/api/v1/rooms/join", headers=self.auth(guest), json={"invite_code": invite, "seat": "B"})
        self.assertEqual(200, joined.status_code, joined.text)

        assessment = {
            "trusted_only_ready": False,
            "source_hash_drift_cards": ["Ornithopter"],
            "cards": [
                {"name": "Ornithopter", "status": "partial", "source_hash_match": False, "unresolved": ["cast_cost"]},
                {"name": "Viscera Seer", "status": "partial", "unresolved": ["activated_ability:ab1"]},
            ],
        }
        with patch("server.deck_readiness.semantic_preflight", return_value=assessment):
            submitted = self.client.put(f"/api/v1/rooms/{room}/deck", headers=self.auth(host), json={
                "name": "Saved deck", "commander": "Yargle and Multani",
                "decklist": "1 Viscera Seer\n1 Ornithopter\n97 Swamp",
            })
        self.assertEqual(200, submitted.status_code, submitted.text)
        self.assertEqual("legal", submitted.json()["format_legality"]["status"])
        readiness = submitted.json()["preflight"]
        self.assertFalse(readiness["trusted_only_ready"])
        self.assertTrue(readiness["data_integrity_issue"])
        self.assertEqual(readiness["affected_cards"], [
            {"name": "Ornithopter", "reasons": ["The verified rules use different card data"]},
            {"name": "Viscera Seer", "reasons": ["Activated ability needs additional rules support"]},
        ])

        self.restart_client()
        owned = self.client.get(f"/api/v1/rooms/{room}", headers=self.auth(host)).json()["room"]["seats"][0]
        public = self.client.get(f"/api/v1/rooms/{room}", headers=self.auth(guest)).json()["room"]["seats"][0]
        self.assertEqual(readiness, owned["deck"]["preflight"])
        self.assertFalse(public["ready"])
        self.assertNotIn("preflight", public["deck"])

    def test_rulings_conflict_saves_a_private_draft_and_blocks_admission(self):
        from unittest.mock import AsyncMock, patch

        from quorune.card_programs.adapters import card_program_from_semantic_programs
        from quorune.semantics import SemanticProgram

        host, _ = self.guest("Deck owner")
        guest, _ = self.guest("Other player")
        created = self.client.post("/api/v1/rooms", headers=self.auth(host), json={"player_count": 2}).json()
        room_id = created["room"]["room_id"]
        room_path = f"/api/v1/rooms/{room_id}"
        joined = self.client.post("/api/v1/rooms/join", headers=self.auth(guest), json={"invite_code": created["invite_code"], "seat": "B"})
        self.assertEqual(200, joined.status_code, joined.text)
        deck = {"name": "Rules check witness", "commander": "Yargle and Multani", "decklist": "99 Swamp"}
        with patch("server.app.CardImageCache.prefetch", new_callable=AsyncMock):
            ready = self.client.put(f"{room_path}/deck", headers=self.auth(guest), json=deck)
        self.assertEqual(200, ready.status_code, ready.text)
        with CardDatabase(self.settings.card_db) as card_db:
            oracle_id = card_db.lookup("Swamp", fuzzy=False).oracle_id
        reviewed = SemanticProgram(key="swamp-reviewed", label="Reviewed fixture", oracle_id=oracle_id, ability_id="ability:reviewed",
            provenance={"authored_by": "fixture review", "source_rulings_hash": "1" * 64})
        generated = SemanticProgram(key="swamp-generated", label="Generated fixture", oracle_id=oracle_id, ability_id="ability:generated",
            provenance={"template_id": "fixture template", "source_rulings_hash": "2" * 64})

        def conflict(*_args, **_kwargs):
            return card_program_from_semantic_programs([reviewed, generated])

        with patch("server.deck_readiness.semantic_preflight", side_effect=conflict), patch(
            "server.app.CardImageCache.prefetch", new_callable=AsyncMock,
        ) as prefetch:
            draft = self.client.put(f"{room_path}/deck", headers=self.auth(host), json=deck)
        prefetch.assert_not_called()
        self.assertEqual(200, draft.status_code, draft.text)
        receipt = draft.json()
        self.assertEqual("legal", receipt["format_legality"]["status"])
        self.assertTrue(receipt["preflight"]["data_integrity_issue"])
        blocker, = receipt["preflight"]["data_integrity_blockers"]
        self.assertEqual("rulings_source_mismatch", blocker["kind"])
        self.assertEqual("Swamp", blocker["card"])
        self.assertEqual(oracle_id, blocker["oracle_id"])
        self.assertTrue(blocker["diagnostic_id"].startswith("card-data-"))
        self.assertEqual({"1" * 64, "2" * 64}, {program["rulings_hash"] for program in blocker["programs"]})

        owned = self.client.get(room_path, headers=self.auth(host)).json()["room"]["seats"][0]
        other = self.client.get(room_path, headers=self.auth(guest)).json()["room"]["seats"][0]
        self.assertFalse(owned["ready"])
        self.assertEqual([blocker], owned["deck"]["preflight"]["data_integrity_blockers"])
        self.assertNotIn("preflight", other["deck"])
        deck_id = receipt["deck"]["deck_id"]
        saved = self.client.get(f"/api/v1/me/decks/{deck_id}", headers=self.auth(host))
        self.assertEqual(200, saved.status_code, saved.text)
        self.assertIn("Swamp", {entry["name"] for entry in saved.json()["deck"]["entries"]})
        self.assertEqual(409, self.client.post(f"{room_path}/start", headers=self.auth(host)).status_code)

    def test_deck_review_compares_all_mismatches_and_pins_reviewed_match(self):
        from unittest.mock import AsyncMock, patch

        with CardDatabase(self.settings.card_db) as database:
            earlier_hash = database_fingerprint(database)["metadata_hash"]
            swat = database.lookup("Deflecting Swat", fuzzy=False).oracle_id
            rollick = database.lookup("Deadly Rollick", fuzzy=False).oracle_id
        reviewed_dir = self.settings.card_snapshot_dir / "reviewed"
        reviewed_dir.mkdir(parents=True)
        earlier = reviewed_dir / f"{earlier_hash}.sqlite3"
        self.client.__exit__(None, None, None)
        shutil.copy2(self.settings.card_db, earlier)
        with closing(sqlite3.connect(self.settings.card_db)) as connection, connection:
            connection.execute(
                "INSERT INTO rulings(oracle_id, published_at, source, comment) VALUES (?, ?, ?, ?)",
                (swat, "2026-11-20", "wotc", "A new ruling in the test snapshot."),
            )
            # The all-ASCII variant is a reviewed equivalent. Changing one
            # ruling gives this fixture a distinct formatting-only fingerprint.
            changed = connection.execute(
                """
                UPDATE rulings SET comment = REPLACE(comment, ?, ?)
                WHERE rowid = (
                    SELECT rowid FROM rulings
                    WHERE oracle_id = ? AND INSTR(comment, ?) > 0
                    ORDER BY published_at, source, comment LIMIT 1
                )
                """,
                ("’", "'", rollick, "’"),
            ).rowcount
            self.assertEqual(1, changed)
            connection.execute(
                "INSERT OR REPLACE INTO metadata(key, value) VALUES (?, ?)",
                ("scryfall_rulings_updated_at", "2026-11-20T12:00:00Z"),
            )
        self.client = TestClient(create_app(self.settings))
        self.client.__enter__()

        host, _ = self.guest("Host")
        other, _ = self.guest("Guest")
        created = self.client.post(
            "/api/v1/rooms", headers=self.auth(host), json={"player_count": 2},
        ).json()
        room_path = f"/api/v1/rooms/{created['room']['room_id']}"
        joined = self.client.post(
            "/api/v1/rooms/join", headers=self.auth(other), json={"invite_code": created["invite_code"], "seat": "B"},
        )
        self.assertEqual(200, joined.status_code, joined.text)
        with patch("server.app.CardImageCache.prefetch", new_callable=AsyncMock):
            waiting = self.client.put(f"{room_path}/deck", headers=self.auth(host), json={
                "name": "Source review", "commander": "Mishra, Eminent One",
                "decklist": "1 Deflecting Swat\n1 Deadly Rollick\n49 Mountain\n48 Swamp",
            })
            ready = self.client.put(f"{room_path}/deck", headers=self.auth(other), json={
                "name": "Other deck", "commander": "Yargle and Multani", "decklist": "99 Swamp",
            })
        self.assertEqual(200, waiting.status_code, waiting.text)
        self.assertEqual(200, ready.status_code, ready.text)
        blockers = {
            item["card"]: item["comparison"]["changes"]
            for item in waiting.json()["preflight"]["data_integrity_blockers"]
        }
        self.assertEqual({"Deadly Rollick", "Deflecting Swat"}, set(blockers))
        self.assertEqual([{
            "kind": "added",
            "after": {"text": "A new ruling in the test snapshot.", "source": "wotc", "published_at": "2026-11-20"},
        }], blockers["Deflecting Swat"])
        self.assertEqual({"formatting"}, {change["kind"] for change in blockers["Deadly Rollick"]})
        visible = self.client.get(room_path, headers=self.auth(other)).json()["room"]["seats"][0]
        self.assertFalse(visible["ready"])
        self.assertNotIn("preflight", visible["deck"])

        path = f"{room_path}/start"
        override = {"use_reviewed_card_data": True}
        self.assertEqual(409, self.client.post(path, headers=self.auth(host)).status_code)
        self.assertEqual(403, self.client.post(path, headers=self.auth(other), json=override).status_code)
        unavailable = earlier.with_suffix(".unavailable")
        self.client.__exit__(None, None, None)
        earlier.rename(unavailable)
        self.client = TestClient(create_app(self.settings))
        self.client.__enter__()
        missing = self.client.post(path, headers=self.auth(host), json=override)
        self.assertEqual(409, missing.status_code, missing.text)
        self.assertIn("No retained card data passed", missing.text)
        unavailable.rename(earlier)
        started = self.client.post(path, headers=self.auth(host), json=override)
        self.assertEqual(200, started.status_code, started.text)
        game_id = started.json()["game_id"]
        record_dir = self.settings.game_root / game_id
        manifest = json.loads((record_dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(earlier_hash, manifest["scryfall"]["metadata_hash"])
        with CardDatabase(self.settings.card_db) as active:
            self.assertNotEqual(earlier_hash, database_fingerprint(active)["metadata_hash"])

        self.restart_client()
        resumed = self.client.get(f"/api/v1/games/{game_id}/state?full=true", headers=self.auth(other))
        self.assertEqual(200, resumed.status_code, resumed.text)
        self.assertEqual(game_id, resumed.json()["packet"]["state"]["game"]["id"])
        with CardDatabase(earlier) as database:
            self.assertTrue(replay_record(record_dir, database, verify=True)["ok"])

    def test_browser_tabs_keep_distinct_guests_in_one_cookie_jar(self):
        tab_a = "a" * 32
        tab_b = "b" * 32
        alice = self.client.post(
            "/api/v1/guests",
            headers={"X-Commander-Tab": tab_a},
            json={"display_name": "Alice tab"},
        )
        unregistered_tab = self.client.get(
            "/api/v1/me", headers={"X-Commander-Tab": tab_b}
        )
        bob = self.client.post(
            "/api/v1/guests",
            headers={"X-Commander-Tab": tab_b},
            json={"display_name": "Bob tab"},
        )
        self.assertEqual(201, alice.status_code, alice.text)
        self.assertNotIn("access_token", alice.json())
        self.assertEqual(401, unregistered_tab.status_code, unregistered_tab.text)
        self.assertEqual(201, bob.status_code, bob.text)

        restored_a = self.client.get(
            "/api/v1/me", headers={"X-Commander-Tab": tab_a}
        )
        restored_b = self.client.get(
            "/api/v1/me", headers={"X-Commander-Tab": tab_b}
        )
        cookie_fallback = self.client.get("/api/v1/me")

        self.assertEqual("Alice tab", restored_a.json()["guest"]["display_name"])
        self.assertEqual("Bob tab", restored_b.json()["guest"]["display_name"])
        self.assertEqual("Bob tab", cookie_fallback.json()["guest"]["display_name"])

    def test_two_player_room_starts_as_commander_duel(self):
        from unittest.mock import patch

        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import _VALIDATION_REGISTRY
        from quorune.runtime import GameManager

        alice, _ = self.guest("Duel A")
        bob, _ = self.guest("Duel B")
        carol, _ = self.guest("Duel C")
        created = self.client.post(
            "/api/v1/rooms",
            headers=self.auth(alice),
            json={"seed": 20260730, "player_count": 2},
        )
        self.assertEqual(201, created.status_code, created.text)
        room = created.json()["room"]
        invite = created.json()["invite_code"]
        self.assertEqual(2, room["seat_count"])
        self.assertEqual("commander_duel", room["format_profile"])
        self.assertEqual(["A", "B"], [seat["seat"] for seat in room["seats"]])
        invalid_seat = self.client.post(
            "/api/v1/rooms/join",
            headers=self.auth(carol),
            json={"invite_code": invite, "seat": "C"},
        )
        self.assertEqual(404, invalid_seat.status_code, invalid_seat.text)
        joined = self.client.post(
            "/api/v1/rooms/join",
            headers=self.auth(bob),
            json={"invite_code": invite, "seat": "B"},
        )
        self.assertEqual(200, joined.status_code, joined.text)
        for token, name, commander, fixture in (
            (alice, "Duel A", "Zimone and Dina", "zimone-and-dina.txt"),
            (bob, "Duel B", "Mishra, Eminent One", "mishra-eminent-one.txt"),
        ):
            uploaded = self.client.put(
                f"/api/v1/rooms/{room['room_id']}/deck",
                headers=self.auth(token),
                json={
                    "name": name,
                    "commander": commander,
                    "decklist": (ROOT / "examples" / fixture).read_text(encoding="utf-8"),
                },
            )
            self.assertEqual(200, uploaded.status_code, uploaded.text)
        add = GameManager.add

        async def checked_add(manager, *args, **kwargs):
            self.assertIsNone(_VALIDATION_REGISTRY.get())
            return await add(manager, *args, **kwargs)

        with (
            patch.object(CapabilityRegistry, "from_path", wraps=CapabilityRegistry.from_path) as loader,
            patch.object(GameManager, "add", checked_add),
        ):
            started = self.client.post(
                f"/api/v1/rooms/{room['room_id']}/start",
                headers=self.auth(alice),
            )
        self.assertEqual(1, loader.call_count)
        self.assertEqual(200, started.status_code, started.text)
        self.assertEqual("commander_duel", started.json()["profile"])
        game_id = started.json()["game_id"]
        summary = self.client.get(
            f"/api/v1/games/{game_id}", headers=self.auth(alice)
        ).json()["game"]
        packet = self.client.get(
            f"/api/v1/games/{game_id}/state?full=true",
            headers=self.auth(alice),
        ).json()["packet"]
        progress = self.client.get(
            f"/api/v1/games/{game_id}/progress",
            headers=self.auth(alice),
        )
        outsider_progress = self.client.get(
            f"/api/v1/games/{game_id}/progress",
            headers=self.auth(carol),
        )
        self.assertEqual("commander_duel", summary["format_profile"])
        self.assertEqual({"A", "B"}, set(packet["state"]["players"]))
        self.assertEqual(200, progress.status_code, progress.text)
        self.assertEqual(0, progress.json()["queue_depth"])
        self.assertFalse(progress.json()["persistence"]["pending"])
        self.assertEqual(403, outsider_progress.status_code)
        record_dir = self.settings.game_root / game_id
        checkpoint = json.loads(
            (record_dir / "checkpoint.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            "trusted_only", checkpoint["state"]["config"]["semantic_policy"]
        )
        self.assertFalse(
            checkpoint["state"]["config"]["auto_pass_empty_priority"]
        )
        self.assertTrue(
            checkpoint["state"]["config"]["manual_active_main_phase"]
        )
        self.assertEqual("debug", checkpoint["state"]["config"]["trace_level"])
        semantics = json.loads(
            (record_dir / "semantics.json").read_text(encoding="utf-8")
        )
        semantic_keys = set(semantics.get("programs", {}))
        self.assertIn(
            "256b8c23-589e-429d-9e6e-433d55079eb4:trigger:enter",
            semantic_keys,
        )
        self.assertIn(
            "ea5103f5-27e0-4eb1-902c-7f34652d6bf3:spell:front",
            semantic_keys,
        )

    def test_owner_removes_a_player_and_replaces_an_unstarted_room(self):
        alice, _ = self.guest("Room owner")
        bob, _ = self.guest("Removable player")
        created = self.client.post(
            "/api/v1/rooms",
            headers=self.auth(alice),
            json={"seed": 1, "player_count": 4},
        )
        room_id = created.json()["room"]["room_id"]
        old_invite = created.json()["invite_code"]
        joined = self.client.post(
            "/api/v1/rooms/join",
            headers=self.auth(bob),
            json={"invite_code": old_invite, "seat": "B"},
        )
        self.assertEqual(200, joined.status_code, joined.text)
        removed = self.client.delete(
            f"/api/v1/rooms/{room_id}/seats/B",
            headers=self.auth(alice),
        )
        self.assertEqual(200, removed.status_code, removed.text)
        self.assertIsNone(removed.json()["room"]["seats"][1]["guest_id"])
        expelled = self.client.get(
            f"/api/v1/rooms/{room_id}", headers=self.auth(bob)
        )
        self.assertEqual(403, expelled.status_code, expelled.text)

        replacement = self.client.post(
            f"/api/v1/rooms/{room_id}/replace",
            headers=self.auth(alice),
            json={"seed": 2, "player_count": 2},
        )
        self.assertEqual(200, replacement.status_code, replacement.text)
        next_room = replacement.json()["room"]
        self.assertNotEqual(room_id, next_room["room_id"])
        self.assertEqual(2, next_room["seat_count"])
        self.assertEqual("commander_duel", next_room["format_profile"])
        stale = self.client.post(
            "/api/v1/rooms/join",
            headers=self.auth(bob),
            json={"invite_code": old_invite, "seat": "B"},
        )
        self.assertEqual(404, stale.status_code, stale.text)

    def test_system_status_exposes_local_data_readiness_without_paths(self):
        response = self.client.get("/api/v1/system")
        self.assertEqual(200, response.status_code, response.text)
        payload = response.json()
        self.assertEqual("ready", payload["server"])
        self.assertTrue(payload["card_data"]["ready"])
        self.assertGreater(payload["card_data"]["database"]["cards"], 0)
        self.assertEqual("local_on_demand_cache", payload["images"]["mode"])
        self.assertTrue(payload["browser"]["served_by_server"])
        self.assertNotIn(str(self.settings.card_db), response.text)
        browser = self.client.get("/")
        self.assertEqual(200, browser.status_code, browser.text)
        self.assertIn("Quorune test", browser.text)
        disabled = self.client.post("/api/v1/system/refresh")
        self.assertEqual(409, disabled.status_code, disabled.text)

    def test_future_preview_cards_require_exact_legality_confirmation(self):
        connection = sqlite3.connect(self.settings.card_db)
        try:
            raw = connection.execute(
                "SELECT legalities_json FROM cards WHERE name = 'Sol Ring'"
            ).fetchone()[0]
            legalities = json.loads(raw)
            legalities["commander"] = "not_legal"
            connection.execute(
                "UPDATE cards SET legalities_json = ?, released_at = ? WHERE name = 'Sol Ring'",
                (json.dumps(legalities, sort_keys=True), "2999-01-01"),
            )
            connection.commit()
        finally:
            connection.close()

        alice, _ = self.guest("Preview Player")
        bob, _ = self.guest("Public Observer")
        created = self.client.post(
            "/api/v1/rooms",
            headers=self.auth(alice),
            json={"seed": 42},
        )
        room_id = created.json()["room"]["room_id"]
        invite = created.json()["invite_code"]
        joined = self.client.post(
            "/api/v1/rooms/join",
            headers=self.auth(bob),
            json={"invite_code": invite, "seat": "B"},
        )
        self.assertEqual(200, joined.status_code, joined.text)
        body = {
            "name": "Preview Mishra",
            "commander": "Mishra, Eminent One",
            "decklist": (ROOT / "examples" / "mishra-eminent-one.txt").read_text(
                encoding="utf-8"
            ),
        }

        warning = self.client.put(
            f"/api/v1/rooms/{room_id}/deck",
            headers=self.auth(alice),
            json=body,
        )

        self.assertEqual(409, warning.status_code, warning.text)
        detail = warning.json()["detail"]
        self.assertEqual("legality_confirmation_required", detail["code"])
        self.assertEqual("Sol Ring", detail["issues"][0]["card"])
        self.assertEqual("2999-01-01", detail["issues"][0]["released_at"])
        self.assertTrue(detail["issues"][0]["confirmable"])
        before = self.client.get(
            f"/api/v1/rooms/{room_id}", headers=self.auth(alice)
        ).json()["room"]
        self.assertFalse(before["seats"][0]["ready"])

        confirmed = self.client.put(
            f"/api/v1/rooms/{room_id}/deck",
            headers=self.auth(alice),
            json={**body, "legality_confirmation": detail["confirmation"]},
        )

        self.assertEqual(200, confirmed.status_code, confirmed.text)
        self.assertEqual(
            "preview_override_confirmed",
            confirmed.json()["format_legality"]["status"],
        )
        owner_room = self.client.get(
            f"/api/v1/rooms/{room_id}", headers=self.auth(alice)
        ).json()["room"]
        owner_deck = owner_room["seats"][0]["deck"]
        self.assertTrue(owner_room["seats"][0]["ready"])
        self.assertEqual(1, owner_deck["format_legality"]["issue_count"])
        self.assertEqual("Sol Ring", owner_deck["format_legality"]["issues"][0]["card"])
        observer_room = self.client.get(
            f"/api/v1/rooms/{room_id}", headers=self.auth(bob)
        ).json()["room"]
        observer_deck = observer_room["seats"][0]["deck"]
        self.assertEqual(1, observer_deck["format_legality"]["issue_count"])
        self.assertEqual([], observer_deck["format_legality"]["issues"])

        cleared = self.client.delete(
            f"/api/v1/rooms/{room_id}/deck",
            headers=self.auth(alice),
        )
        self.assertEqual(200, cleared.status_code, cleared.text)
        own_seat = cleared.json()["room"]["seats"][0]
        self.assertFalse(own_seat["ready"])
        self.assertIsNone(own_seat["deck"])

    def test_persisted_game_reopens_with_its_retained_card_snapshot(self):
        game_id, tokens, _ = self.create_ready_game()
        with CardDatabase(self.settings.card_db) as current:
            old_hash = str(database_fingerprint(current)["metadata_hash"])
        self.client.__exit__(None, None, None)
        self.settings.card_snapshot_dir.mkdir(parents=True)
        snapshot = self.settings.card_snapshot_dir / f"{old_hash}.sqlite3"
        self.settings.card_db.replace(snapshot)
        shutil.copy2(snapshot, self.settings.card_db)
        connection = sqlite3.connect(self.settings.card_db)
        try:
            connection.execute(
                "INSERT OR REPLACE INTO metadata(key, value) VALUES (?, ?)",
                ("scryfall_oracle_updated_at", "a-newer-snapshot"),
            )
            connection.commit()
        finally:
            connection.close()
        self.client = TestClient(create_app(self.settings))
        self.client.__enter__()

        response = self.client.get(
            f"/api/v1/games/{game_id}/state?full=true",
            headers=self.auth(tokens[0]),
        )

        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual(game_id, response.json()["packet"]["state"]["game"]["id"])

    def create_ready_game(
        self, player_count: int = 4
    ) -> tuple[str, list[str], str]:
        seats = "ABCD"[:player_count]
        tokens = [self.guest(f"Player {seat}")[0] for seat in seats]
        response = self.client.post(
            "/api/v1/rooms",
            headers=self.auth(tokens[0]),
            json={"seed": 20260730, "player_count": player_count},
        )
        self.assertEqual(201, response.status_code, response.text)
        room_id = response.json()["room"]["room_id"]
        invite = response.json()["invite_code"]
        self.last_ready_invite = invite
        for index, seat in enumerate(seats[1:], 1):
            response = self.client.post(
                "/api/v1/rooms/join",
                headers=self.auth(tokens[index]),
                json={"invite_code": invite, "seat": seat},
            )
            self.assertEqual(200, response.status_code, response.text)
        for index, seat in enumerate(seats):
            zimone = seat in "AC"
            path = ROOT / "examples" / (
                "zimone-and-dina.txt"
                if zimone
                else "mishra-eminent-one.txt"
            )
            response = self.client.put(
                f"/api/v1/rooms/{room_id}/deck",
                headers=self.auth(tokens[index]),
                json={
                    "name": f"Deck {seat}",
                    "commander": (
                        "Zimone and Dina" if zimone else "Mishra, Eminent One"
                    ),
                    "decklist": path.read_text(encoding="utf-8"),
                },
            )
            self.assertEqual(200, response.status_code, response.text)
            preflight = response.json()["preflight"]
            # This helper tests room/game transport. Draft mechanic contracts
            # may conservatively warn and pause when encountered; duplicated
            # browser fixtures are not semantic or matchup evidence.
            self.assertEqual(
                100,
                sum(
                    int(preflight[key] or 0)
                    for key in (
                        "fully_playable_cards",
                        "partial_cards",
                        "unresolved_cards",
                    )
                ),
            )
        response = self.client.post(
            f"/api/v1/rooms/{room_id}/start",
            headers=self.auth(tokens[0]),
        )
        self.assertEqual(200, response.status_code, response.text)
        return response.json()["game_id"], tokens, room_id

    def test_lost_durable_ack_reloads_same_move_without_a_second_commit(self):
        game_id, tokens, _ = self.create_ready_game(player_count=2)
        base = f"/api/v1/games/{game_id}"
        auth = self.auth(tokens[0])
        packet = self.client.get(f"{base}/state?full=true", headers=auth).json()["packet"]
        decision = packet["decision"]
        command = {
            "protocol_version": "3.0",
            "game_id": game_id,
            "command_id": "lost-durable-ack",
            "decision_id": decision["id"],
            "action_id": "keep",
            "capability": decision["cap"],
            "expected_view_revision": packet["view_revision"],
            "choices": {},
        }
        runtime = self.client.app.state.runtime
        failed_actor = runtime.manager.get(game_id)
        save = runtime.persistence.save

        def save_without_ack(service):
            save(service)
            raise OSError("Injected interruption after the durable commit")

        with patch.object(runtime.persistence, "save", side_effect=save_without_ack):
            failed = self.client.post(f"{base}/commands", headers=auth, json=command)
        self.assertEqual(503, failed.status_code, failed.text)
        self.assertEqual("game_recovery_required", failed.json()["detail"]["code"])
        self.assertTrue(failed_actor.requires_recovery)

        recovered = self.client.get(f"{base}/state?full=true", headers=self.auth(tokens[1]))
        self.assertEqual(200, recovered.status_code, recovered.text)
        self.assertEqual(packet["view_revision"] + 1, recovered.json()["packet"]["view_revision"])
        self.assertIsNot(failed_actor, runtime.manager.get(game_id))

        retry = self.client.post(f"{base}/commands", headers=auth, json=command)
        self.assertEqual(200, retry.status_code, retry.text)
        self.assertTrue(retry.json()["receipt"]["replayed"])
        self.assertEqual(1, recovered.json()["game"]["commands"])

    def test_command_progress_shows_only_the_requesting_pilots_timing(self):
        game_id, tokens, _room = self.create_ready_game(player_count=2)
        base = f"/api/v1/games/{game_id}"
        spectator, _ = self.guest("Timing spectator")
        watched = self.client.post("/api/v1/rooms/watch", headers=self.auth(spectator),
            json={"invite_code": self.last_ready_invite})
        self.assertEqual(200, watched.status_code, watched.text)
        packet = self.client.get(f"{base}/state?full=true", headers=self.auth(tokens[0])).json()["packet"]
        decision = packet["decision"]
        interaction_id = "timing-test-a-keep"
        reply = self.client.post(f"{base}/commands", headers=self.auth(tokens[0]), json={
            "protocol_version": "3.0", "game_id": game_id, "command_id": interaction_id,
            "decision_id": decision["id"], "action_id": "keep", "capability": decision["cap"],
            "expected_view_revision": packet["view_revision"], "choices": {},
        })
        self.assertEqual(200, reply.status_code, reply.text)
        self.assertTrue(reply.json()["receipt"]["ok"], reply.text)
        revision = reply.json()["receipt"]["state_revision"]
        yours = self.client.get(f"{base}/progress", headers=self.auth(tokens[0])).json()["last_command"]
        self.assertEqual(interaction_id, yours["interaction_id"])
        self.assertEqual(revision, yours["revision"])
        for field in ("queue_ms", "execution_ms", "persistence_ms", "actor_ms"):
            self.assertGreaterEqual(yours[field], 0)
        for token in (tokens[1], spectator):
            progress = self.client.get(f"{base}/progress", headers=self.auth(token))
            self.assertEqual(200, progress.status_code, progress.text)
            self.assertIsNone(progress.json()["last_command"])
            self.assertNotIn(interaction_id, progress.text)
        after = self.client.get(f"{base}/state?full=true", headers=self.auth(tokens[1])).json()["packet"]
        self.assertEqual(revision, after["view_revision"])

    def test_spectator_projection_public_log_and_restart_are_read_only(self):
        game_id, tokens, room_id = self.create_ready_game()
        spectator, _ = self.guest("Table watcher")
        outsider, _ = self.guest("Uninvited watcher")

        joined = self.client.post(
            "/api/v1/rooms/watch",
            headers=self.auth(spectator),
            json={"invite_code": self.last_ready_invite},
        )
        self.assertEqual(200, joined.status_code, joined.text)
        watched_room = joined.json()["room"]
        self.assertEqual(room_id, watched_room["room_id"])
        self.assertEqual(game_id, watched_room["game_id"])
        self.assertTrue(watched_room["spectator"])
        self.assertFalse(any(seat["mine"] for seat in watched_room["seats"]))

        projection = self.client.get(
            f"/api/v1/games/{game_id}/state?full=true",
            headers=self.auth(spectator),
        )
        self.assertEqual(200, projection.status_code, projection.text)
        packet = projection.json()["packet"]
        self.assertEqual("spectator", packet["principal"])
        self.assertIsNone(packet["decision"])
        self.assertNotIn('"cap"', json.dumps(packet, sort_keys=True))
        for player in packet["state"]["players"].values():
            self.assertNotIn("hand", player)
            self.assertNotIn("known_hand", player)
            self.assertNotIn("known_top", player)

        summary = projection.json()["game"]
        self.assertTrue(summary["spectator"])
        self.assertEqual("spectator", summary["role"])
        self.assertIsNone(summary["seat"])
        self.assertFalse(summary["owner"])
        self.assertFalse(summary["can_stop"])
        self.assertFalse(summary["can_resume"])

        denied_outsider = self.client.get(
            f"/api/v1/games/{game_id}/events",
            headers=self.auth(outsider),
        )
        self.assertEqual(403, denied_outsider.status_code)

        first_page = self.client.get(
            f"/api/v1/games/{game_id}/events?after=0&limit=1",
            headers=self.auth(spectator),
        )
        self.assertEqual(200, first_page.status_code, first_page.text)
        first_payload = first_page.json()
        self.assertEqual(1, len(first_payload["events"]))
        self.assertTrue(first_payload["has_more"])
        self.assertGreater(first_payload["next_after"], 0)
        self.assertEqual(
            {"id", "code", "actor", "summary", "importance"},
            set(first_payload["events"][0]),
        )

        public_events: list[dict] = []
        after = 0
        while True:
            page = self.client.get(
                f"/api/v1/games/{game_id}/events?after={after}&limit=2",
                headers=self.auth(spectator),
            )
            self.assertEqual(200, page.status_code, page.text)
            payload = page.json()
            public_events.extend(payload["events"])
            self.assertGreaterEqual(payload["next_after"], after)
            if not payload["has_more"]:
                break
            self.assertGreater(payload["next_after"], after)
            after = payload["next_after"]
        self.assertTrue(public_events)
        self.assertEqual(
            sorted({event["id"] for event in public_events}),
            [event["id"] for event in public_events],
        )
        self.assertTrue(any(event["code"] == "game.created" for event in public_events))
        self.assertFalse(
            any(event["code"] in {"card.draw.private", "library.look"} for event in public_events)
        )

        seat_packet = self.client.get(
            f"/api/v1/games/{game_id}/state?full=true",
            headers=self.auth(tokens[0]),
        ).json()["packet"]
        stolen = {
            "protocol_version": "3.0",
            "game_id": game_id,
            "command_id": "spectator-stolen-command",
            "decision_id": seat_packet["decision"]["id"],
            "action_id": "keep",
            "capability": seat_packet["decision"]["cap"],
            "expected_view_revision": seat_packet["view_revision"],
            "choices": {},
        }
        denied_command = self.client.post(
            f"/api/v1/games/{game_id}/commands",
            headers=self.auth(spectator),
            json=stolen,
        )
        self.assertEqual(403, denied_command.status_code, denied_command.text)

        self.set_existing_cookie(COOKIE_NAME, spectator)
        with self.client.websocket_connect(
            f"/api/v1/games/{game_id}/stream"
        ) as websocket:
            initial = websocket.receive_json()
            self.assertEqual("spectator", initial["packet"]["principal"])
            self.assertIsNone(initial["packet"]["decision"])

            accepted = self.client.post(
                f"/api/v1/games/{game_id}/commands",
                headers=self.auth(tokens[0]),
                json=stolen | {"command_id": "seat-a-public-update"},
            )
            self.assertEqual(200, accepted.status_code, accepted.text)
            self.assertTrue(accepted.json()["receipt"]["ok"])
            update = websocket.receive_json()
            self.assertEqual("spectator", update["packet"]["principal"])
            self.assertIsNone(update["packet"]["decision"])

        for index in range(1, 4):
            seat_packet = self.client.get(
                f"/api/v1/games/{game_id}/state?full=true",
                headers=self.auth(tokens[index]),
            ).json()["packet"]
            keep = self.client.post(
                f"/api/v1/games/{game_id}/commands",
                headers=self.auth(tokens[index]),
                json={
                    "protocol_version": "3.0",
                    "game_id": game_id,
                    "command_id": f"seat-{'ABCD'[index]}-public-log-keep",
                    "decision_id": seat_packet["decision"]["id"],
                    "action_id": "keep",
                    "capability": seat_packet["decision"]["cap"],
                    "expected_view_revision": seat_packet["view_revision"],
                    "choices": {},
                },
            )
            self.assertEqual(200, keep.status_code, keep.text)
            self.assertTrue(keep.json()["receipt"]["ok"])

        low_importance_public_event = False
        for pass_sequence in range(20):
            current_log = self.client.get(
                f"/api/v1/games/{game_id}/events?after=0&limit=200",
                headers=self.auth(spectator),
            ).json()["events"]
            if any(event["importance"] == 0 for event in current_log):
                low_importance_public_event = True
                break
            active_index, active_packet = next(
                (index, packet)
                for index, token in enumerate(tokens)
                for packet in [
                    self.client.get(
                        f"/api/v1/games/{game_id}/state?full=true",
                        headers=self.auth(token),
                    ).json()["packet"]
                ]
                if packet["decision"] is not None
            )
            pass_action = next(
                action
                for action in active_packet["decision"]["legal_actions"]
                if action["id"] == "pass"
            )
            passed = self.client.post(
                f"/api/v1/games/{game_id}/commands",
                headers=self.auth(tokens[active_index]),
                json={
                    "protocol_version": "3.0",
                    "game_id": game_id,
                    "command_id": (
                        f"seat-{'ABCD'[active_index]}-public-log-pass-"
                        f"{pass_sequence}"
                    ),
                    "decision_id": active_packet["decision"]["id"],
                    "action_id": pass_action["id"],
                    "capability": active_packet["decision"]["cap"],
                    "expected_view_revision": active_packet["view_revision"],
                    "choices": {},
                },
            )
            self.assertEqual(200, passed.status_code, passed.text)
            self.assertTrue(passed.json()["receipt"]["ok"])
        self.assertTrue(
            low_importance_public_event,
            "the scenario must create a low-importance public event",
        )

        self.restart_client()
        recovered = self.client.get(
            f"/api/v1/games/{game_id}/state?full=true",
            headers=self.auth(spectator),
        )
        self.assertEqual(200, recovered.status_code, recovered.text)
        self.assertEqual("spectator", recovered.json()["packet"]["principal"])
        recovered_log = self.client.get(
            f"/api/v1/games/{game_id}/events?after=0&limit=200",
            headers=self.auth(spectator),
        )
        self.assertEqual(200, recovered_log.status_code, recovered_log.text)
        self.assertGreaterEqual(
            len(recovered_log.json()["events"]), len(public_events)
        )
        self.assertTrue(
            any(
                event["importance"] == 0
                for event in recovered_log.json()["events"]
            ),
            "the complete public log must retain low-importance public events",
        )

        left = self.client.delete(
            f"/api/v1/rooms/{room_id}/membership",
            headers=self.auth(spectator),
        )
        self.assertEqual(200, left.status_code, left.text)
        denied_after_leave = self.client.get(
            f"/api/v1/games/{game_id}", headers=self.auth(spectator)
        )
        self.assertEqual(403, denied_after_leave.status_code)

    def test_guest_csrf_room_and_atomic_seat_claims(self):
        blank = self.client.post(
            "/api/v1/guests", json={"display_name": "   "}
        )
        self.assertEqual(422, blank.status_code, blank.text)

        alice, csrf = self.guest("Alice")
        bob, _ = self.guest("Bob")
        carol, _ = self.guest("Carol")

        denied = self.client.post("/api/v1/rooms", json={"seed": 1})
        self.assertEqual(403, denied.status_code)
        self.set_existing_cookie(COOKIE_NAME, alice)
        self.set_existing_cookie(CSRF_COOKIE_NAME, csrf)
        created = self.client.post(
            "/api/v1/rooms",
            headers={"X-CSRF-Token": csrf},
            json={"seed": 1},
        )
        self.assertEqual(201, created.status_code, created.text)
        room = created.json()["room"]
        invite = created.json()["invite_code"]
        self.assertTrue(room["seats"][0]["mine"])
        self.assertEqual("A", room["seats"][0]["seat"])
        unsafe_source = self.client.put(
            f"/api/v1/rooms/{room['room_id']}/deck",
            headers=self.auth(alice),
            json={
                "name": "Unsafe",
                "source_url": "https://example.invalid/deck",
            },
        )
        self.assertEqual(422, unsafe_source.status_code)
        self.assertIn("public Moxfield", unsafe_source.text)

        joined = self.client.post(
            "/api/v1/rooms/join",
            headers=self.auth(bob),
            json={"invite_code": invite, "seat": "B"},
        )
        self.assertEqual(200, joined.status_code, joined.text)

        denied_rotation = self.client.post(
            f"/api/v1/rooms/{room['room_id']}/invite",
            headers=self.auth(bob),
            json={},
        )
        self.assertEqual(403, denied_rotation.status_code, denied_rotation.text)
        rotated = self.client.post(
            f"/api/v1/rooms/{room['room_id']}/invite",
            headers=self.auth(alice),
            json={},
        )
        self.assertEqual(200, rotated.status_code, rotated.text)
        replacement = rotated.json()["invite_code"]
        self.assertNotEqual(invite, replacement)

        stale_invite = self.client.post(
            "/api/v1/rooms/join",
            headers=self.auth(carol),
            json={"invite_code": invite, "seat": "C"},
        )
        self.assertEqual(404, stale_invite.status_code, stale_invite.text)
        replacement_join = self.client.post(
            "/api/v1/rooms/join",
            headers=self.auth(carol),
            json={"invite_code": replacement, "seat": "C"},
        )
        self.assertEqual(200, replacement_join.status_code, replacement_join.text)
        conflict = self.client.post(
            "/api/v1/rooms/join",
            headers=self.auth(carol),
            json={"invite_code": replacement, "seat": "B"},
        )
        self.assertEqual(409, conflict.status_code)

        dave, _ = self.guest("Dave")
        outsider = self.client.get(
            f"/api/v1/rooms/{room['room_id']}", headers=self.auth(dave)
        )
        self.assertEqual(403, outsider.status_code)
        me = self.client.get("/api/v1/me", headers=self.auth(alice))
        self.assertEqual("Alice", me.json()["guest"]["display_name"])

    def test_four_seat_projection_websocket_reconnect_and_exact_replay(self):
        game_id, tokens, _ = self.create_ready_game()
        packets = []
        for index, seat in enumerate("ABCD"):
            response = self.client.get(
                f"/api/v1/games/{game_id}/state?full=true",
                headers=self.auth(tokens[index]),
            )
            self.assertEqual(200, response.status_code, response.text)
            packet = response.json()["packet"]
            self.assertEqual(f"pilot:{seat}", packet["principal"])
            self.assertEqual(game_id, packet["state"]["game"]["id"])
            packets.append(packet)

        a_players = packets[0]["state"]["players"]
        self.assertIn("hand", a_players["A"])
        self.assertNotIn("hand", a_players["B"])
        self.assertNotIn("hand", a_players["C"])
        self.assertNotIn("hand", a_players["D"])

        self.set_existing_cookie(COOKIE_NAME, tokens[0])
        with self.client.websocket_connect(
            f"/api/v1/games/{game_id}/stream",
            headers={"origin": "http://testserver"},
        ) as websocket_a:
            initial_a = websocket_a.receive_json()
            self.assertEqual("full", initial_a["packet"]["mode"])
            self.set_existing_cookie(COOKIE_NAME, tokens[1])
            with self.client.websocket_connect(
                f"/api/v1/games/{game_id}/stream"
            ) as websocket_b:
                initial_b = websocket_b.receive_json()
                self.assertEqual("pilot:B", initial_b["packet"]["principal"])
                packet = initial_a["packet"]
                envelope = {
                    "protocol_version": "3.0",
                    "game_id": game_id,
                    "command_id": "browser-A-0001",
                    "decision_id": packet["decision"]["id"],
                    "action_id": "keep",
                    "capability": packet["decision"]["cap"],
                    "expected_view_revision": packet["view_revision"],
                    "choices": {},
                }
                response = self.client.post(
                    f"/api/v1/games/{game_id}/commands",
                    headers=self.auth(tokens[0]),
                    json=envelope,
                )
                self.assertEqual(200, response.status_code, response.text)
                self.assertTrue(response.json()["receipt"]["ok"])
                self.assertEqual(
                    "pilot:A", websocket_a.receive_json()["packet"]["principal"]
                )
                update_b = websocket_b.receive_json()["packet"]
                self.assertEqual("pilot:B", update_b["principal"])

                duplicate = self.client.post(
                    f"/api/v1/games/{game_id}/commands",
                    headers=self.auth(tokens[0]),
                    json=envelope,
                )
                self.assertTrue(duplicate.json()["receipt"]["replayed"])

                b_decision = update_b["decision"]
                stolen = {
                    **envelope,
                    "command_id": "browser-A-stolen",
                    "decision_id": b_decision["id"],
                    "capability": b_decision["cap"],
                    "expected_view_revision": update_b["view_revision"],
                }
                rejected = self.client.post(
                    f"/api/v1/games/{game_id}/commands",
                    headers=self.auth(tokens[0]),
                    json=stolen,
                )
                self.assertEqual(
                    "unauthorized_capability",
                    rejected.json()["receipt"]["code"],
                )

        self.set_existing_cookie(COOKIE_NAME, tokens[0])
        with self.client.websocket_connect(
            f"/api/v1/games/{game_id}/stream"
        ) as reconnected:
            packet = reconnected.receive_json()["packet"]
            self.assertEqual("full", packet["mode"])
            self.assertEqual(1, packet["view_revision"])

        bad = {**envelope, "principal": "pilot:B"}
        response = self.client.post(
            f"/api/v1/games/{game_id}/commands",
            headers=self.auth(tokens[0]),
            json=bad,
        )
        self.assertEqual(422, response.status_code)

        record_dir = self.settings.game_root / game_id
        commands = [
            json.loads(line)
            for line in (record_dir / "commands.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        self.assertEqual(1, len(commands))
        self.assertEqual("browser-A-0001", commands[0]["client_command_id"])
        capability = envelope["capability"]
        for path in (
            record_dir / "commands.jsonl",
            record_dir / "checkpoint.json",
            self.settings.database,
        ):
            self.assertNotIn(capability.encode(), path.read_bytes(), path.name)

        database = CardDatabase(DB_PATH)
        try:
            replay = replay_record(record_dir, database, verify=True)
        finally:
            database.close()
        self.assertTrue(replay["ok"])

    def test_stale_game_websocket_stops_reconnecting_with_terminal_message(self):
        game_id, _, _ = self.create_ready_game()
        outsider, _ = self.guest("Stale game tab")
        self.set_existing_cookie(COOKIE_NAME, outsider)

        with self.client.websocket_connect(
            f"/api/v1/games/{game_id}/stream"
        ) as websocket:
            message = websocket.receive_json()

        self.assertEqual("terminal", message["type"])
        self.assertEqual("game_access_lost", message["code"])
        self.assertIn("Return to the lobby", message["message"])

    def test_persisted_arbiter_decision_becomes_browser_rules_pause(self):
        game_id, tokens, _ = self.create_ready_game()
        runtime = self.client.app.state.runtime
        service = runtime.manager.get(game_id).service
        service.session.engine.permissions.invalidate_current()
        service.session.state.pending_decision = None
        service.session.engine.permissions.issue(
            kind="arbiter.resolve",
            role="arbiter",
            actors=["arbiter"],
            allowed_actions=["resolve", "register_and_resolve"],
            payload_by_actor={
                "arbiter": {
                    "label": "Legacy unresolved permanent",
                    "stack": "S1",
                }
            },
        )
        service.session.record_status = "in_progress"
        service.session.pause_reason = None
        # Save through the raw record adapter to model a record written by a
        # pre-boundary browser build. The next server process must recognize
        # that no authenticated browser principal can answer this decision.
        original_card_db = service.session.card_db
        test_thread_card_db = CardDatabase(self.settings.card_db)
        try:
            service.session.card_db = test_thread_card_db
            service.session.engine.card_db = test_thread_card_db
            runtime.records.save(service)
        finally:
            service.session.card_db = original_card_db
            service.session.engine.card_db = original_card_db
            test_thread_card_db.close()
        runtime.store.update_game_state(
            game_id,
            service.session.state.revision,
            "active",
        )

        self.restart_client()

        response = self.client.get(
            f"/api/v1/games/{game_id}/state?full=true",
            headers=self.auth(tokens[0]),
        )
        self.assertEqual(200, response.status_code, response.text)
        payload = response.json()
        self.assertEqual("paused", payload["game"]["status"])
        self.assertEqual(
            "browser_rules_boundary",
            payload["game"]["pause_reason"]["kind"],
        )
        self.assertIn(
            "no player is passing priority",
            payload["game"]["pause_reason"]["label"],
        )
        self.assertIsNone(payload["packet"]["decision"])
        self.assertFalse(payload["game"]["can_resume"])
        persisted = json.loads(
            (self.settings.game_root / game_id / "manifest.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual("paused", persisted["status"])
        self.assertEqual(
            "browser_rules_boundary",
            persisted["pause_reason"]["kind"],
        )

    def test_server_restart_recovers_decision_idempotency_and_replay(self):
        game_id, tokens, _ = self.create_ready_game()
        response = self.client.get(
            f"/api/v1/games/{game_id}/state?full=true",
            headers=self.auth(tokens[0]),
        )
        self.assertEqual(200, response.status_code, response.text)
        packet_a = response.json()["packet"]
        envelope_a = {
            "protocol_version": "3.0",
            "game_id": game_id,
            "command_id": "restart-A-0001",
            "decision_id": packet_a["decision"]["id"],
            "action_id": "keep",
            "capability": packet_a["decision"]["cap"],
            "expected_view_revision": packet_a["view_revision"],
            "choices": {},
        }
        accepted_a = self.client.post(
            f"/api/v1/games/{game_id}/commands",
            headers=self.auth(tokens[0]),
            json=envelope_a,
        )
        self.assertEqual(200, accepted_a.status_code, accepted_a.text)
        self.assertTrue(accepted_a.json()["receipt"]["ok"])
        revision_after_a = accepted_a.json()["receipt"]["state_revision"]

        before_restart = self.client.get(
            f"/api/v1/games/{game_id}/state?full=true",
            headers=self.auth(tokens[1]),
        )
        self.assertEqual(200, before_restart.status_code, before_restart.text)
        packet_b_before = before_restart.json()["packet"]
        self.assertIsNotNone(packet_b_before["decision"])

        self.restart_client()

        recovered = self.client.get(
            f"/api/v1/games/{game_id}/state?full=true",
            headers=self.auth(tokens[1]),
        )
        self.assertEqual(200, recovered.status_code, recovered.text)
        packet_b = recovered.json()["packet"]
        self.assertEqual("pilot:B", packet_b["principal"])
        self.assertEqual(revision_after_a, packet_b["view_revision"])
        self.assertEqual(
            packet_b_before["decision"]["id"],
            packet_b["decision"]["id"],
        )
        self.assertNotEqual(
            packet_b_before["decision"]["cap"],
            packet_b["decision"]["cap"],
        )

        duplicate = self.client.post(
            f"/api/v1/games/{game_id}/commands",
            headers=self.auth(tokens[0]),
            json=envelope_a,
        )
        self.assertEqual(200, duplicate.status_code, duplicate.text)
        self.assertTrue(duplicate.json()["receipt"]["ok"])
        self.assertTrue(duplicate.json()["receipt"]["replayed"])

        envelope_b = {
            "protocol_version": "3.0",
            "game_id": game_id,
            "command_id": "restart-B-0001",
            "decision_id": packet_b["decision"]["id"],
            "action_id": "keep",
            "capability": packet_b["decision"]["cap"],
            "expected_view_revision": packet_b["view_revision"],
            "choices": {},
        }
        accepted_b = self.client.post(
            f"/api/v1/games/{game_id}/commands",
            headers=self.auth(tokens[1]),
            json=envelope_b,
        )
        self.assertEqual(200, accepted_b.status_code, accepted_b.text)
        self.assertTrue(accepted_b.json()["receipt"]["ok"])

        database = CardDatabase(DB_PATH)
        try:
            replay = replay_record(
                self.settings.game_root / game_id,
                database,
                verify=True,
            )
        finally:
            database.close()
        self.assertTrue(replay["ok"])

    def test_completed_concession_survives_restart_and_exact_replay(self):
        game_id, tokens, _ = self.create_ready_game(player_count=2)
        for index in range(2):
            projected = self.client.get(
                f"/api/v1/games/{game_id}/state?full=true",
                headers=self.auth(tokens[index]),
            )
            self.assertEqual(200, projected.status_code, projected.text)
            packet = projected.json()["packet"]
            accepted = self.client.post(
                f"/api/v1/games/{game_id}/commands",
                headers=self.auth(tokens[index]),
                json={
                    "protocol_version": "3.0",
                    "game_id": game_id,
                    "command_id": f"terminal-keep-{index}",
                    "decision_id": packet["decision"]["id"],
                    "action_id": "keep",
                    "capability": packet["decision"]["cap"],
                    "expected_view_revision": packet["view_revision"],
                    "choices": {},
                },
            )
            self.assertEqual(200, accepted.status_code, accepted.text)
            self.assertTrue(accepted.json()["receipt"]["ok"])

        active = self.client.get(
            f"/api/v1/games/{game_id}/state?full=true",
            headers=self.auth(tokens[0]),
        )
        packet = active.json()["packet"]
        concession = self.client.post(
            f"/api/v1/games/{game_id}/commands",
            headers=self.auth(tokens[0]),
            json={
                "protocol_version": "3.0",
                "game_id": game_id,
                "command_id": "terminal-concede-A",
                "decision_id": packet["decision"]["id"],
                "action_id": "concede",
                "capability": packet["decision"]["cap"],
                "expected_view_revision": packet["view_revision"],
                "choices": {"confirm_concede": True},
            },
        )
        self.assertEqual(200, concession.status_code, concession.text)
        self.assertTrue(concession.json()["receipt"]["ok"])

        self.restart_client()

        recovered = self.client.get(
            f"/api/v1/games/{game_id}/state?full=true",
            headers=self.auth(tokens[1]),
        )
        self.assertEqual(200, recovered.status_code, recovered.text)
        payload = recovered.json()
        self.assertEqual("complete", payload["game"]["status"])
        self.assertTrue(payload["game"]["game_over"])
        self.assertEqual("B", payload["game"]["winner"])
        self.assertFalse(payload["game"]["draw"])
        self.assertIsNone(payload["packet"]["decision"])
        self.assertEqual(0, payload["packet"]["state"]["players"]["A"]["in"])

        with CardDatabase(DB_PATH) as database:
            replay = replay_record(
                self.settings.game_root / game_id,
                database,
                verify=True,
            )
        self.assertTrue(replay["ok"])

        next_table = f"/api/v1/games/{game_id}/rematch"
        too_early = self.client.post(next_table, headers=self.auth(tokens[1]))
        self.assertEqual(409, too_early.status_code, too_early.text)
        opened = self.client.post(next_table, headers=self.auth(tokens[0]))
        self.assertEqual(200, opened.status_code, opened.text)
        room = opened.json()["room"]
        self.assertNotEqual(payload["game"]["room_id"], room["room_id"])
        self.assertIsNone(room["game_id"])
        self.assertTrue(all(seat["ready"] for seat in room["seats"]))
        for token in tokens[:2]:
            reused = self.client.post(next_table, headers=self.auth(token))
            self.assertEqual(room["room_id"], reused.json()["room"]["room_id"])

        own = self.client.get("/api/v1/me/decks", headers=self.auth(tokens[0])).json()["decks"]
        opposing = self.client.get("/api/v1/me/decks", headers=self.auth(tokens[1])).json()["decks"]
        self.assertTrue(own)
        self.assertTrue(opposing)
        self.assertTrue({deck["id"] for deck in own}.isdisjoint(deck["id"] for deck in opposing))
        private = self.client.get(
            f"/api/v1/me/decks/{opposing[0]['id']}", headers=self.auth(tokens[0]),
        )
        self.assertEqual(404, private.status_code, private.text)

        room_id = room["room_id"]
        unready = self.client.delete(f"/api/v1/rooms/{room_id}/deck", headers=self.auth(tokens[0]))
        self.assertEqual(200, unready.status_code, unready.text)
        reused = self.client.put(
            f"/api/v1/rooms/{room_id}/deck", headers=self.auth(tokens[0]),
            json={"name": "Rematch list", "saved_deck_id": own[0]["id"]},
        )
        self.assertEqual(200, reused.status_code, reused.text)

        removed = self.client.delete(f"/api/v1/rooms/{room_id}/seats/B", headers=self.auth(tokens[0]))
        self.assertEqual(200, removed.status_code, removed.text)
        newcomer, _ = self.guest("Next table guest")
        joined = self.client.post(
            "/api/v1/rooms/join", headers=self.auth(newcomer),
            json={"invite_code": opened.json()["invite_code"], "seat": "B"},
        )
        self.assertEqual(200, joined.status_code, joined.text)
        historical = self.client.get(f"/api/v1/games/{game_id}/state", headers=self.auth(tokens[1]))
        self.assertEqual(200, historical.status_code, historical.text)
        inaccessible = self.client.get(f"/api/v1/games/{game_id}/state", headers=self.auth(newcomer))
        self.assertEqual(403, inaccessible.status_code, inaccessible.text)

    def test_owner_stop_resume_inspection_and_restart_are_durable(self):
        game_id, tokens, _ = self.create_ready_game()
        owner_inspection = self.client.get(
            f"/api/v1/games/{game_id}", headers=self.auth(tokens[0])
        )
        self.assertEqual(200, owner_inspection.status_code, owner_inspection.text)
        owner_game = owner_inspection.json()["game"]
        self.assertEqual("active", owner_game["status"])
        self.assertTrue(owner_game["owner"])
        self.assertTrue(owner_game["can_stop"])
        self.assertNotIn("record_path", owner_game)
        self.assertNotIn("hand", json.dumps(owner_game).casefold())

        member_inspection = self.client.get(
            f"/api/v1/games/{game_id}", headers=self.auth(tokens[1])
        )
        self.assertEqual(200, member_inspection.status_code)
        self.assertFalse(member_inspection.json()["game"]["owner"])
        self.assertFalse(member_inspection.json()["game"]["can_stop"])

        forbidden = self.client.post(
            f"/api/v1/games/{game_id}/stop",
            headers=self.auth(tokens[1]),
            json={"reason": "Seat B cannot stop the match"},
        )
        self.assertEqual(403, forbidden.status_code, forbidden.text)

        packet_response = self.client.get(
            f"/api/v1/games/{game_id}/state?full=true",
            headers=self.auth(tokens[0]),
        )
        self.assertEqual(200, packet_response.status_code)
        packet = packet_response.json()["packet"]
        paused_envelope = {
            "protocol_version": "3.0",
            "game_id": game_id,
            "command_id": "paused-A-0001",
            "decision_id": packet["decision"]["id"],
            "action_id": "keep",
            "capability": packet["decision"]["cap"],
            "expected_view_revision": packet["view_revision"],
            "choices": {},
        }
        stopped = self.client.post(
            f"/api/v1/games/{game_id}/stop",
            headers=self.auth(tokens[0]),
            json={"reason": "Table break"},
        )
        self.assertEqual(200, stopped.status_code, stopped.text)
        stopped_game = stopped.json()["game"]
        self.assertEqual("paused", stopped_game["status"])
        self.assertTrue(stopped_game["can_resume"])
        self.assertEqual("Table break", stopped_game["pause_reason"]["label"])
        self.assertEqual(
            {"kind", "label"}, set(stopped_game["pause_reason"])
        )
        repeated_stop = self.client.post(
            f"/api/v1/games/{game_id}/stop",
            headers=self.auth(tokens[0]),
            json={"reason": "A retry must not replace the first reason"},
        )
        self.assertEqual(200, repeated_stop.status_code, repeated_stop.text)
        self.assertEqual(
            "Table break",
            repeated_stop.json()["game"]["pause_reason"]["label"],
        )

        rejected = self.client.post(
            f"/api/v1/games/{game_id}/commands",
            headers=self.auth(tokens[0]),
            json=paused_envelope,
        )
        self.assertEqual(200, rejected.status_code, rejected.text)
        self.assertFalse(rejected.json()["receipt"]["ok"])
        self.assertEqual("game_paused", rejected.json()["receipt"]["code"])
        manifest_path = self.settings.game_root / game_id / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual("paused", manifest["status"])
        self.assertEqual("administrative_stop", manifest["pause_reason"]["kind"])

        # Simulate a crash after the Game Record commit but before the
        # denormalized SQLite status update. The record remains authoritative.
        connection = sqlite3.connect(self.settings.database)
        try:
            connection.execute(
                "UPDATE games SET status = 'active' WHERE game_id = ?",
                (game_id,),
            )
            connection.commit()
        finally:
            connection.close()

        self.restart_client()
        recovered = self.client.get(
            f"/api/v1/games/{game_id}", headers=self.auth(tokens[1])
        )
        self.assertEqual(200, recovered.status_code, recovered.text)
        self.assertEqual("paused", recovered.json()["game"]["status"])
        connection = sqlite3.connect(self.settings.database)
        try:
            status = connection.execute(
                "SELECT status FROM games WHERE game_id = ?", (game_id,)
            ).fetchone()[0]
        finally:
            connection.close()
        self.assertEqual("paused", status)
        forbidden_resume = self.client.post(
            f"/api/v1/games/{game_id}/resume",
            headers=self.auth(tokens[1]),
            json={},
        )
        self.assertEqual(403, forbidden_resume.status_code)

        resumed = self.client.post(
            f"/api/v1/games/{game_id}/resume",
            headers=self.auth(tokens[0]),
            json={},
        )
        self.assertEqual(200, resumed.status_code, resumed.text)
        self.assertEqual("active", resumed.json()["game"]["status"])
        self.assertFalse(resumed.json()["game"]["can_resume"])
        repeated_resume = self.client.post(
            f"/api/v1/games/{game_id}/resume",
            headers=self.auth(tokens[0]),
            json={},
        )
        self.assertEqual(200, repeated_resume.status_code, repeated_resume.text)
        self.assertEqual("active", repeated_resume.json()["game"]["status"])
        malformed_resume = self.client.post(
            f"/api/v1/games/{game_id}/resume",
            headers=self.auth(tokens[0]),
            json={"seat": "B"},
        )
        self.assertEqual(422, malformed_resume.status_code)

        replayed_rejection = self.client.post(
            f"/api/v1/games/{game_id}/commands",
            headers=self.auth(tokens[0]),
            json=paused_envelope,
        )
        self.assertEqual(200, replayed_rejection.status_code)
        self.assertEqual(
            "game_paused", replayed_rejection.json()["receipt"]["code"]
        )
        self.assertTrue(replayed_rejection.json()["receipt"]["replayed"])

        fresh = self.client.get(
            f"/api/v1/games/{game_id}/state?full=true",
            headers=self.auth(tokens[0]),
        ).json()["packet"]
        accepted = self.client.post(
            f"/api/v1/games/{game_id}/commands",
            headers=self.auth(tokens[0]),
            json={
                "protocol_version": "3.0",
                "game_id": game_id,
                "command_id": "resumed-A-0002",
                "decision_id": fresh["decision"]["id"],
                "action_id": "keep",
                "capability": fresh["decision"]["cap"],
                "expected_view_revision": fresh["view_revision"],
                "choices": {},
            },
        )
        self.assertEqual(200, accepted.status_code, accepted.text)
        self.assertTrue(accepted.json()["receipt"]["ok"])

        database = CardDatabase(DB_PATH)
        try:
            replay = replay_record(
                self.settings.game_root / game_id,
                database,
                verify=True,
            )
        finally:
            database.close()
        self.assertTrue(replay["ok"])


if __name__ == "__main__":
    unittest.main()
