import json
import unittest
from dataclasses import replace
from unittest.mock import patch

from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.compiler.targeted_library_exile_templates import targeted_library_exile_spec
from quorune.private_opponent_library_cast_model import (
    has_private_opponent_library_mana_waiver, private_opponent_library_exile_viewer,
)
from quorune.record import authoritative_state_hash
from quorune.rules.targeted_library_exile import (
    PRIVATE_TARGET_LIBRARY_PLAY_CAPABILITY, targeted_library_exile_node_capabilities,
)


ORACLE = (
    "Search target opponent's library for a card and exile it face down. Then that player shuffles. "
    "You may play that card for as long as it remains exiled."
)


class PrivateTargetedLibraryPlayTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import tempfile
        from pathlib import Path
        from common import ROOT
        from quorune.carddb import CardDatabase
        from quorune.deck import DeckDefinition, DeckEntry
        from quorune.rules.capabilities import load_default_capability_registry
        from scripts.build_test_database import build_fixture_database

        cls.temporary = tempfile.TemporaryDirectory()
        root = Path(cls.temporary.name)
        fixtures = ROOT / "tests" / "fixtures"
        base = json.loads((fixtures / "bound-effect-program-cards.json").read_text(encoding="utf-8"))
        base["cards"] = [row for row in base["cards"] if row["name"] == "Generic Bound Plains"]
        for filename, name in (
            ("ability-source-counter-cards.json", "Shock"),
            ("activation-result-views-cards.json", "Mountain"),
        ):
            support = json.loads((fixtures / filename).read_text(encoding="utf-8"))
            card, = [row for row in support["cards"] if row["name"] == name]
            base["cards"].append(card)
        base["rulings"] = []
        path = root / "base.json"
        path.write_text(json.dumps(base), encoding="utf-8")
        database = root / "private-target-library.sqlite3"
        build_fixture_database(
            [path, fixtures / "targeted-library-exile-cards.json",
             fixtures / "target-opponent-private-library-play-cards.json"],
            database,
        )
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition(
            "Private targeted library play",
            [DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Generic Bound Plains", 30)],
            ["Isamaru, Hound of Konda"],
        )

    def test_original_requires_exact_opponent_private_play_authority(self):
        spec = targeted_library_exile_spec(ORACLE)
        self.assertTrue(spec.private_play)
        for old, new in (
            ("target opponent", "target player"), ("a card", "a creature card"),
            ("face down", "face up"), ("play", "cast"), ("it remains exiled", "end of turn"),
        ):
            with self.subTest(old=old):
                self.assertIsNone(targeted_library_exile_spec(ORACLE.replace(old, new)))
        self.assertFalse(targeted_library_exile_node_capabilities(
            effects=(spec.effect(),), target_schema={**spec.target_schema(), "player_relation": "any"},
        ))
        game = self.session(732155100)
        source = self.add(game.engine, "Praetor's Grasp", zone="hand")
        self.assert_closed(game, source)
        programs = game.engine.semantics.programs_for_oracle(source.oracle_id)
        program, = [p for p in programs if PRIVATE_TARGET_LIBRARY_PLAY_CAPABILITY in p.capability_dependencies]
        deps = tuple(dep for dep in program.capability_dependencies if dep != PRIVATE_TARGET_LIBRARY_PLAY_CAPABILITY)
        changed = replace(program, capability_dependencies=deps, coverage=(),
                          capability_closure=self.registry.closure(deps, profile="commander_review").to_dict())
        self.assertFalse(bind_semantic_program_runtime(
            changed, capability_registry=self.registry, profile="commander_review",
        )["strict"])

    def reject(self, game, seat, action):
        before = authoritative_state_hash(game.state)
        result = game.act("pilot:" + seat, action)
        self.assertFalse(result.ok, result.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def paid_search(self, game, selected_name, mana, *, seed):
        source = self.add(game.engine, "Praetor's Grasp", zone="hand")
        chosen = self.add(game.engine, selected_name, seat="B", zone="library", ref=f"chosen-{seed}")
        hidden = self.add(game.engine, "Grizzly Bears", seat="B", zone="library", ref=f"foreign-{seed}")
        own = self.add(game.engine, "Island", seat="A", zone="library", ref=f"own-{seed}")
        for card in (chosen, hidden, own):
            card.known_to = []
            card.revealed_to = []
        self.prepare(game, mana=mana)
        self.checkpoint(game)
        self.cast_for(game, "A", source, ["B"])
        self.assertEqual("semantic.search", self.resolve(game).kind)
        context = game.packet("pilot:A", full=True)["decision"]["ctx"]
        refs = {row["id"] for row in context["search_cards"]}
        self.assertTrue({chosen.ref, hidden.ref} <= refs)
        self.assertNotIn(own.ref, refs)
        for principal in ("pilot:B", "pilot:C", "pilot:D", "spectator"):
            packet = json.dumps(game.packet(principal, full=True))
            self.assertNotIn(chosen.ref, packet)
            self.assertNotIn(hidden.ref, packet)
        choose = {"action_id": "choose", "search_cards": [chosen.ref]}
        self.reject(game, "B", choose)
        self.reject(game, "A", {**choose, "search_cards": []})
        self.reject(game, "A", {"action_id": "choose", "cards": [chosen.ref]})
        self.reject(game, "A", {**choose, "search_cards": [own.ref]})
        game = self.replay(game, load=True)
        with patch.object(game.engine, "shuffle_library", wraps=game.engine.shuffle_library) as shuffle:
            result = game.act("pilot:A", choose)
            self.assertTrue(result.ok, result.summary)
            self.assertIsNone(self.resolve(game))
            self.assertEqual(["B"], [call.args[0] for call in shuffle.call_args_list])
        retained = game.state.cards[chosen.object_id]
        self.assertEqual(("B", "exile", True), (retained.owner, retained.zone, retained.face_down))
        self.assertEqual("A", private_opponent_library_exile_viewer(game.state, retained))
        self.assertEqual(["A"], retained.known_to)
        self.assertFalse(retained.revealed_to)
        self.assertFalse(has_private_opponent_library_mana_waiver(game.engine, actor="A", card=retained))
        self.assertIn(selected_name, json.dumps(game.packet("pilot:A", full=True)))
        for principal in ("pilot:B", "pilot:C", "pilot:D", "spectator"):
            self.assertNotIn(selected_name, json.dumps(game.packet(principal, full=True)))
        self.assertEqual("library", game.state.cards[hidden.object_id].zone)
        return self.replay(game, load=True), retained.object_id

    def test_paid_search_is_private_and_the_selected_spell_uses_its_normal_cost_then_expires(self):
        game = self.session(732155101)
        mountain = self.add(game.engine, "Mountain", zone="hand", ref="normal-red-mana-source")
        game, object_id = self.paid_search(game, "Shock", {"B": 2, "W": 2}, seed=101)
        card = game.state.cards[object_id]
        self.assertEqual(1, game.state.players["A"].mana_pool["W"])
        # The legacy opposing-top-library route would accept a white mana here.
        self.reject(game, "A", {"action_id": "cast:" + card.ref, "targets": ["B"], "pay": "auto"})
        play = game.act("pilot:A", {"action_id": "play-land:" + mountain.ref})
        self.assertTrue(play.ok, play.summary)
        initial_life = game.state.players["B"].life
        self.cast_for(game, "A", card, ["B"])
        self.assertIsNone(self.resolve(game))
        self.assertEqual(initial_life - 2, game.state.players["B"].life)
        self.assertTrue(game.state.cards[mountain.object_id].tapped)
        moved = game.state.cards[object_id]
        self.assertEqual(("B", "graveyard"), (moved.owner, moved.zone))
        self.assertIsNone(private_opponent_library_exile_viewer(game.state, moved))
        self.replay(game, load=True)

    def test_selected_land_uses_normal_land_play_and_changes_incarnation(self):
        game = self.session(732155102)
        game, object_id = self.paid_search(game, "Island", {"B": 2, "C": 1}, seed=102)
        card = game.state.cards[object_id]
        identity = card.logical_object_id
        result = game.act("pilot:A", {"action_id": "play-land:" + card.ref})
        self.assertTrue(result.ok, result.summary)
        moved = game.state.cards[object_id]
        self.assertEqual(("B", "A", "battlefield"), (moved.owner, moved.controller, moved.zone))
        self.assertNotEqual(identity, moved.logical_object_id)
        self.assertIsNone(private_opponent_library_exile_viewer(game.state, moved))
        self.replay(game, load=True)
