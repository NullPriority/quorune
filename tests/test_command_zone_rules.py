from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from common import keep_all, load_assets, make_session
from quorune.engine import GameRuleError
from quorune.model import GameConfig, GameState
from quorune.projection import StateProjector
from quorune.record import (
    authoritative_state_hash,
    checkpoint_envelope,
    replay_record,
)
from quorune.session import CommanderSession


class CommandZoneRuleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db, cls.mishra, cls.zimone = load_assets()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def make_session(self, seed: int, *, players: int = 4):
        session = make_session(
            self.db,
            self.mishra,
            self.zimone,
            players=players,
            seed=seed,
        )
        keep_all(session)
        session.engine.permissions.invalidate_current()
        session.engine.state.pending_decision = None
        session.engine.state.priority_player = None
        return session

    @staticmethod
    def card(engine, owner: str, name: str):
        return next(
            card
            for card in engine.state.cards.values()
            if card.owner == owner
            and card.is_card_object
            and card.printed_name == name
        )

    def test_contract_traces_every_cr_408_rule(self):
        root = Path(__file__).resolve().parents[1]
        contract = json.loads(
            (
                root / "mechanics" / "contracts" / "command-zone.json"
            ).read_text(encoding="utf-8")
        )

        self.assertEqual(
            {"408", "408.1", "408.2", "408.3"},
            {
                rule_id
                for rule_id in contract["rule_references"]
                if str(rule_id).startswith("408")
            },
        )

    def test_command_zone_is_public_across_all_commander_seats(self):
        session = self.make_session(40801)
        engine = session.engine
        projector = StateProjector(self.db, engine.state)
        view = projector._snapshot("pilot:A")

        for seat in engine.seats:
            command = engine.state.players[seat].zones["command"]
            self.assertEqual(1, len(command))
            commander = engine.state.cards[command[0]]
            self.assertTrue(commander.is_card_object)
            self.assertTrue(commander.is_commander)
            self.assertEqual("command", commander.zone)
            self.assertEqual(seat, commander.owner)
            self.assertEqual(
                commander.printed_name,
                view["players"][seat]["cmd"][0]["n"],
            )

    def test_generic_emblem_is_a_persistent_noncard_command_object(self):
        session = self.make_session(40802, players=2)
        engine = session.engine
        ability = "At the beginning of combat, draw a card."
        ref = engine.create_emblem(
            "A",
            abilities=[ability],
            display_label="Rules witness emblem",
            semantic_key="test:rules-witness-emblem",
        )
        emblem = next(
            card
            for card in engine.state.cards.values()
            if card.ref == ref
        )

        self.assertEqual("emblem", emblem.object_kind)
        self.assertFalse(emblem.is_card_object)
        self.assertFalse(emblem.is_token)
        self.assertEqual("command", emblem.zone)
        self.assertEqual("A", emblem.owner)
        self.assertEqual("A", emblem.controller)
        self.assertIn(
            emblem.object_id,
            engine.state.players["A"].zones["command"],
        )
        characteristics = engine._effective_card_data(emblem)
        self.assertEqual("", characteristics["name"])
        self.assertEqual("", characteristics["mana_cost"])
        self.assertEqual("", characteristics["type_line"])
        self.assertEqual([], characteristics["colors"])
        self.assertEqual(ability, characteristics["oracle_text"])

        for principal in ("pilot:A", "pilot:B", "spectator"):
            command = StateProjector(
                self.db,
                engine.state,
            )._snapshot(principal)["players"]["A"]["cmd"]
            projected = next(
                item for item in command if item["id"] == ref
            )
            self.assertEqual("Rules witness emblem", projected["n"])
            self.assertEqual("emblem", projected["kind"])
            self.assertNotIn("cid", projected)

        restored = GameState.from_dict(engine.state.to_dict())
        restored_emblem = restored.cards[emblem.object_id]
        self.assertEqual("emblem", restored_emblem.object_kind)
        self.assertEqual(
            emblem.annotations,
            restored_emblem.annotations,
        )
        self.assertEqual(
            authoritative_state_hash(engine.state),
            authoritative_state_hash(restored),
        )

    def test_command_zone_objects_are_not_destroyable_permanents(self):
        session = self.make_session(40803, players=2)
        engine = session.engine
        ref = engine.create_emblem(
            "A",
            abilities=["Artifacts you control have hexproof."],
        )
        before = authoritative_state_hash(engine.state)

        with self.assertRaisesRegex(
            GameRuleError,
            "in requested zones",
        ):
            engine.apply_effect(
                {"op": "destroy", "card": ref},
                actor="B",
            )

        self.assertEqual(before, authoritative_state_hash(engine.state))
        emblem = next(
            card
            for card in engine.state.cards.values()
            if card.ref == ref
        )
        self.assertEqual("command", emblem.zone)

    def test_daretti_emblem_triggers_from_its_exact_command_object(self):
        session = self.make_session(40804, players=2)
        engine = session.engine
        artifact = self.card(engine, "A", "Sol Ring")
        engine.move_card(
            artifact.object_id,
            "battlefield",
            controller="A",
            log=False,
        )
        engine.apply_effect(
            {"op": "create_daretti_emblem"},
            actor="A",
        )
        emblem = next(
            card
            for card in engine.state.cards.values()
            if card.owner == "A" and card.object_kind == "emblem"
        )

        engine.move_card(
            artifact.object_id,
            "graveyard",
            reason="CR 408.2 emblem trigger witness",
            semantic_events=True,
        )
        self.assertFalse(engine._stabilize())
        triggers = [
            item
            for item in engine.state.stack
            if item.semantic_key == "builtin:daretti-emblem"
        ]

        self.assertEqual(1, len(triggers))
        self.assertEqual(emblem.object_id, triggers[0].source_object_id)
        from quorune.target_source import TARGETING_SOURCE_CONTEXT,TargetingSourceBinding,stack_targeting_source
        binding=TargetingSourceBinding.from_dict(triggers[0].context[TARGETING_SOURCE_CONTEXT])
        self.assertEqual((emblem.object_id,emblem.logical_object_id,'command','A','',()),
            (binding.object_id,binding.logical_object_id,binding.zone,binding.controller,binding.type_line,binding.colors))
        facts=stack_targeting_source(engine,triggers[0])
        self.assertEqual((frozenset(),frozenset(),'A'),(facts.card_types,facts.colors,facts.source_controller))

    def test_daretti_emblem_creation_exact_replays(self):
        session = self.make_session(40805, players=2)
        engine = session.engine
        daretti = self.card(engine, "A", "Daretti, Scrap Savant")
        engine.move_card(
            daretti.object_id,
            "battlefield",
            controller="A",
            log=False,
        )
        daretti.counters["loyalty"] = 10
        engine.state.active_player = "A"
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.state.priority_player = "A"
        engine._activate(
            "A",
            {"source": daretti.ref, "ability": "ab3"},
        )
        engine._issue_priority("A")
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()

        for _ in range(6):
            if any(
                card.object_kind == "emblem"
                for card in engine.state.cards.values()
            ):
                break
            principal = session.pending_principals()[0]
            result = session.act(principal, {"a": "pass"})
            self.assertTrue(result.ok, result.summary)

        self.assertTrue(
            any(
                card.object_kind == "emblem"
                for card in engine.state.cards.values()
            )
        )
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "command-zone-record"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)

        self.assertTrue(replay["ok"], replay)

    def test_noncommander_command_variants_remain_fail_closed(self):
        for profile in (
            "planechase",
            "vanguard",
            "archenemy",
            "conspiracy_draft",
        ):
            with self.subTest(profile=profile), self.assertRaisesRegex(
                ValueError,
                "Unsupported Commander format profile",
            ):
                CommanderSession.create(
                    self.db,
                    {"A": self.mishra, "B": self.zimone},
                    first_player="A",
                    config=GameConfig(profile=profile, seed=40806),
                )


if __name__ == "__main__":
    unittest.main()
