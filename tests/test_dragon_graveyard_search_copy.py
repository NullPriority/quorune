"""Native search, tracked public Dragon model, same-source copy and ordered shuffle."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
import test_next_personal_turn_protection as next_turn
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.dragon_graveyard_search_copy import (
    DRAGON_GRAVEYARD_COPY_SEARCH_CAPABILITY, dragon_graveyard_copy_search_effect,
    dragon_graveyard_copy_search_runtime_dependencies,
)
from scripts.build_test_database import build_fixture_database


SCION = "Scion of the Ur-Dragon"
DRAGON = "Shivan Dragon"
PROFILE = "commander_review"


class DragonGraveyardSearchCopyTests(InheritedNativeFixture, unittest.TestCase):
    until_following_turn = next_turn.NextPersonalTurnProtectionTests.until_following_turn

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        root = Path(cls.temporary.name)
        fixtures = ROOT / "tests" / "fixtures"
        support = {
            "bound-effect-program-cards.json": {"Generic Bound Plains"},
            "targeted-library-exile-cards.json": {"Isamaru, Hound of Konda", "Grizzly Bears"},
            "inherited-rule-composition-cards.json": {DRAGON},
            "activation-paid-creature-subtype-cards.json": {"Universal Automaton", "Rest in Peace"},
            "creature-subtype-set-results-cards.json": {"Nameless Inversion"},
            "committed-milled-creature-tokens-cards.json": {"Leyline of the Void", "Tormod's Crypt"},
            "ability-label-composition-cards.json": {"Unsummon"},
        }
        paths = []
        for filename, names in support.items():
            payload = json.loads((fixtures / filename).read_text(encoding="utf-8"))
            payload["cards"] = [row for row in payload["cards"] if row["name"] in names]
            if {row["name"] for row in payload["cards"]} != names:
                raise AssertionError((filename, names, payload["cards"]))
            payload["rulings"] = []
            path = root / filename
            path.write_text(json.dumps(payload), encoding="utf-8")
            paths.append(path)
        database = root / "dragon-graveyard-copy.sqlite3"
        build_fixture_database([*paths, fixtures / "dragon-graveyard-search-self-copy-cards.json"], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Dragon search", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_exact_activation_requires_its_original_search_copy_and_shuffle(self):
        record = self.db.lookup(SCION, fuzzy=False)
        options = {"capability_registry": self.registry, "capability_profile": PROFILE}
        ir = compile_oracle_card(record, **options)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        node, = [row for row in ir.faces[0].nodes if DRAGON_GRAVEYARD_COPY_SEARCH_CAPABILITY in row.capability_dependencies]
        self.assertEqual((dragon_graveyard_copy_search_effect(),), tuple(node.effects))
        self.assertIsNone(node.target_schema)
        for old, new in (("Dragon permanent", "Dragon creature"), ("into your graveyard", "into your hand"),
                         ("until end of turn", "for the rest of the game"), (" Then shuffle.", "")):
            with self.subTest(operand=old):
                changed = replace(record, oracle_text=record.oracle_text.replace(old, new, 1))
                self.assertNotEqual("exact", compile_oracle_card(changed, **options).status)
        game = self.session(822417100)
        source = self.add(game.engine, SCION)
        self.assert_closed(game, source)
        program, = [row for row in game.engine.semantics.programs_for_oracle(source.oracle_id)
                    if DRAGON_GRAVEYARD_COPY_SEARCH_CAPABILITY in row.capability_dependencies]
        with self.assertRaises(ValueError):
            dragon_graveyard_copy_search_runtime_dependencies(replace(program, active_zone="graveyard"))
        deps = tuple(dep for dep in program.capability_dependencies if dep != DRAGON_GRAVEYARD_COPY_SEARCH_CAPABILITY)
        mutated = replace(program, capability_dependencies=deps,
                          capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
        self.assertFalse(bind_semantic_program_runtime(mutated, capability_registry=self.registry, profile=PROFILE)["strict"])

    def put_on_top(self, game, name, ref, seat="A"):
        card = self.add(game.engine, name, seat=seat, zone="library", ref=ref)
        card.known_to = []
        card.revealed_to = []
        return card

    def reject_choice(self, game, seat, refs):
        before = authoritative_state_hash(game.state)
        result = game.act("pilot:" + seat, {"action_id": "choose", "search_cards": refs})
        self.assertFalse(result.ok, result.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def activate(self, game, source, seat="A", targets=()):
        self.priority(game, seat)
        actions = game.packet("pilot:" + seat, full=True)["decision"]["ctx"]["legal"]["actions"]
        offered = [row["id"] for row in actions if row["id"].startswith(f"activate:{source.ref}:")]
        self.assertEqual(1, len(offered), (source.printed_name, [row["id"] for row in actions]))
        action, = offered
        result = game.act("pilot:" + seat, {"action_id": action, "targets": list(targets), "pay": "auto"})
        self.assertTrue(result.ok, result.summary)

    def search(self, game, source):
        self.activate(game, source)
        decision = self.resolve(game)
        self.assertEqual(("semantic.search", ["A"]), (decision.kind, decision.actors))
        return game.packet("pilot:A", full=True)["decision"]["ctx"]

    def choose(self, game, *refs):
        result = game.act("pilot:A", {"action_id": "choose", "search_cards": list(refs)})
        self.assertTrue(result.ok, result.summary)

    def copied_name(self, game, card):
        return game.engine._effective_card_data(game.state.cards[card.object_id])["name"]

    def test_paid_private_search_copies_the_selected_model_before_its_single_shuffle_and_replays(self):
        game = self.session(822417101)
        source = self.add(game.engine, SCION)
        model = self.put_on_top(game, DRAGON, "scion-selected-dragon")
        foreign = self.put_on_top(game, DRAGON, "scion-foreign-dragon", seat="B")
        instant = self.put_on_top(game, "Nameless Inversion", "scion-kindred-instant")
        changeling = self.put_on_top(game, "Universal Automaton", "scion-permanent-changeling")
        grave_exile = self.add(game.engine, "Tormod's Crypt", seat="B")
        self.prepare(game, mana={"C": 2})
        self.checkpoint(game)
        context = self.search(game, source)
        self.assertEqual({model.ref, changeling.ref}, {row["id"] for row in context["search_cards"]})
        for principal in ("pilot:B", "pilot:C", "pilot:D", "spectator"):
            packet = json.dumps(game.packet(principal, full=True))
            self.assertNotIn(model.ref, packet)
            self.assertNotIn(changeling.ref, packet)
        self.assertEqual(0, game.state.players["A"].mana_pool["C"])
        self.reject_choice(game, "A", [instant.ref])
        self.reject_choice(game, "A", [foreign.ref])
        self.reject_choice(game, "B", [model.ref])
        game = self.replay(game, load=True)
        original_shuffle = game.engine.shuffle_library
        ordered = []

        def shuffle(seat, *args, **kwargs):
            ordered.append((seat, self.copied_name(game, source), game.state.cards[model.object_id].zone))
            return original_shuffle(seat, *args, **kwargs)

        with patch.object(game.engine, "shuffle_library", side_effect=shuffle):
            self.choose(game, model.ref)
            self.assertIsNone(self.resolve(game))
        self.assertEqual([("A", DRAGON, "graveyard")], ordered)
        self.assertEqual(source.logical_object_id, game.state.cards[source.object_id].logical_object_id)
        self.assertFalse(any("Search your library" in ability.effect_text
                             for ability in game.engine._activated_abilities(game.state.cards[source.object_id])))
        self.activate(game, grave_exile, seat="B", targets=("A",))
        self.assertIsNone(self.resolve(game))
        self.assertEqual("exile", game.state.cards[model.object_id].zone)
        self.assertEqual(DRAGON, self.copied_name(game, source))
        self.until_following_turn(game, game.state.turn_sequence, "B")
        self.assertEqual(SCION, self.copied_name(game, source))
        self.replay(game, load=True)

    def test_competing_replacements_pause_and_replay_before_copying_the_face_up_exile_model(self):
        game = self.session(822417102)
        source = self.add(game.engine, SCION)
        model = self.put_on_top(game, "Universal Automaton", "scion-interrupted-model")
        self.add(game.engine, "Rest in Peace", seat="B")
        self.add(game.engine, "Leyline of the Void", seat="C")
        self.prepare(game, mana={"C": 2})
        self.checkpoint(game)
        self.search(game, source)
        with patch.object(game.engine, "shuffle_library", wraps=game.engine.shuffle_library) as shuffle:
            self.choose(game, model.ref)
            self.assertEqual([], shuffle.call_args_list)
        pending = game.state.pending_decision
        self.assertEqual(("replacement.order", ["A"]), (pending.kind, pending.actors))
        self.assertEqual(("library", SCION), (game.state.cards[model.object_id].zone, self.copied_name(game, source)))
        game = self.replay(game, load=True)
        option = game.packet("pilot:A", full=True)["decision"]["ctx"]["options"][0]["id"]
        with patch.object(game.engine, "shuffle_library", wraps=game.engine.shuffle_library) as shuffle:
            result = game.act("pilot:A", {"action_id": "choose", "replacement": option})
            self.assertTrue(result.ok, result.summary)
            self.assertIsNone(self.resolve(game))
            self.assertEqual(["A"], [call.args[0] for call in shuffle.call_args_list])
        self.assertEqual(("exile", False, "Universal Automaton"),
                         (game.state.cards[model.object_id].zone, game.state.cards[model.object_id].face_down,
                          self.copied_name(game, source)))
        self.replay(game, load=True)

    def test_failed_to_find_or_departed_source_preserves_the_search_and_shuffle(self):
        for departed in (False, True):
            with self.subTest(departed=departed):
                game = self.session(822417103 + departed)
                source = self.add(game.engine, SCION)
                model = self.put_on_top(game, DRAGON, "scion-no-copy-model")
                bounce = self.add(game.engine, "Unsummon", seat="B", zone="hand")
                self.prepare(game, mana={"C": 2})
                game.state.players["B"].mana_pool["U"] = 1
                self.checkpoint(game)
                self.activate(game, source)
                if departed:
                    self.cast_for(game, "B", bounce, [source.ref])
                self.assertEqual("semantic.search", self.resolve(game).kind)
                with patch.object(game.engine, "shuffle_library", wraps=game.engine.shuffle_library) as shuffle:
                    self.choose(game, *([model.ref] if departed else []))
                    self.assertIsNone(self.resolve(game))
                    self.assertEqual(["A"], [call.args[0] for call in shuffle.call_args_list])
                self.assertEqual("graveyard" if departed else "library", game.state.cards[model.object_id].zone)
                self.assertEqual("hand" if departed else "battlefield", game.state.cards[source.object_id].zone)
                self.assertEqual(SCION, self.copied_name(game, source))
                self.replay(game, load=True)
