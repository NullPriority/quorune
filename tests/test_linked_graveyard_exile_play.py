"""Native linked-opponent redirection, own-turn play and the typed Ward price."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from test_controller_creature_keyword_absence import register_card
from test_qualified_zone_event_queries import query_record
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.linked_ability_exile_model import LINKED_ABILITY_EXILE_ANNOTATION
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.linked_graveyard_play import REPLACEMENT_CAPABILITY, PLAY_CAPABILITY
from scripts.build_test_database import build_fixture_database

CARD = "Valgavoth, Terror Eater"
PROFILE = "commander_review"
WARD_CAPABILITY = "trigger.keyword.ward.three_nonland_permanents"


class LinkedGraveyardExilePlayTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "linked-graveyard-play.sqlite3"
        fixtures = ROOT / "tests/fixtures"
        build_fixture_database([fixtures / name for name in (
            "bound-effect-program-cards.json", "linked-graveyard-exile-play-cards.json",
            "state-qualified-zone-cards.json", "cast-target-copy-cards.json",
            "graveyard-exile-replacement-cards.json",
        )], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Generic Bound Commander"
        cls.deck = DeckDefinition("Linked exile play", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], [commander])

    def test_compiler_pairs_exact_replacement_and_life_permission_with_distinct_ward(self):
        printed = self.db.lookup(CARD, fuzzy=False)
        text = printed.oracle_text.replace("exiled with Valgavoth", "exiled with Generic Query Observer")
        record = replace(query_record(text), mana_cost="{6}{B}{B}{B}", type_line=printed.type_line, keywords=printed.keywords)
        result = compile_oracle_card(record, capability_registry=self.registry, capability_profile=PROFILE)
        self.assertEqual("exact", result.status, result.material_residuals)
        flying, ward, producer, reader = result.faces[0].nodes
        self.assertEqual(tuple(text.splitlines()), tuple(text[n.span.start:n.span.end] for n in (flying, ward, producer, reader)))
        self.assertIn(WARD_CAPABILITY, ward.capability_dependencies)
        self.assertIn(REPLACEMENT_CAPABILITY, producer.capability_dependencies)
        self.assertIn(PLAY_CAPABILITY, reader.capability_dependencies)
        self.assertEqual(producer.handlers[0]["ability_pair"], reader.handlers[0]["ability_pair"])

        for old, new in (("three nonland", "three land"), ("you didn't control", "you controlled"),
                         ("exiled with Generic Query Observer", "exiled with Another Source"),
                         ("During your turn", "During each opponent's turn"),
                         ("rather than pay its mana cost", "in addition to paying its mana cost")):
            with self.subTest(new=new):
                changed = replace(record, oracle_text=text.replace(old, new))
                self.assertNotEqual("exact", compile_oracle_card(
                    changed, capability_registry=self.registry, capability_profile=PROFILE).status)

        binding, semantics = register_card(self.db, self.registry, CARD)
        self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
        programs = semantics.programs_for_oracle(printed.oracle_id)
        for capability in (WARD_CAPABILITY, REPLACEMENT_CAPABILITY, PLAY_CAPABILITY):
            with self.subTest(capability=capability):
                program = next(p for p in programs if capability in p.capability_dependencies)
                dependencies = [item for item in program.capability_dependencies if item != capability]
                closure = self.registry.closure(dependencies, profile=PROFILE).to_dict() if dependencies else None
                changed = replace(program, capability_dependencies=dependencies, capability_closure=closure)
                self.assertFalse(bind_semantic_program_runtime(
                    changed, capability_registry=self.registry, profile=PROFILE)["strict"])

    def start(self, game, *cards, mana=None, active="A"):
        for card in cards:
            self.assert_closed(game, card)
        self.prepare(game, mana=mana or {}, active=active)
        self.checkpoint(game)

    def card(self, game, card):
        return game.state.cards[card.object_id]

    def assert_link(self, game, card, source):
        current = self.card(game, card)
        self.assertEqual("exile", current.zone)
        link, = current.annotations[LINKED_ABILITY_EXILE_ANNOTATION]
        self.assertEqual(self.card(game, source).logical_object_id, link["source"]["logical_object_id"])
        self.assertEqual(current.logical_object_id, link["exiled"]["logical_object_id"])

    def test_foreign_spell_and_permanent_are_linked_by_actual_old_control_then_cast_for_life(self):
        game = self.session(60721401)
        source = self.add(game.engine, CARD)
        stolen = self.add(game.engine, "Grizzly Bears", seat="C", ref="controlled-by-a")
        other = self.add(game.engine, "Grizzly Bears", seat="B", ref="controlled-by-b")
        removal = self.add(game.engine, "Murder", seat="B", zone="hand")
        game.engine.change_control(stolen.object_id, "A", reason="Set up the prior-control witness.")
        game.state.players["B"].mana_pool.update({"C": 1, "B": 2})
        self.start(game, source, removal, other)
        self.cast_for(game, "B", removal, [stolen.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", self.card(game, stolen).zone)
        self.assertIn(stolen.object_id, game.state.players["C"].zones["graveyard"])
        self.assert_link(game, removal, source)
        game = self.replay(game, load=True)

        self.priority(game, "A")
        before = authoritative_state_hash(game.state)
        action = self.action(game, "A", self.card(game, removal))
        refused = game.act("pilot:A", {"action_id": action["id"], "targets": [other.ref], "cost_option": "printed"})
        self.assertFalse(refused.ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        self.cast_for(game, "A", removal, [other.ref])
        self.assertEqual(37, game.state.players["A"].life)
        self.assertIsNone(self.resolve(game))
        self.assert_link(game, other, source)
        self.assertEqual("graveyard", self.card(game, removal).zone)
        self.assertIn(removal.object_id, game.state.players["B"].zones["graveyard"])
        self.cast_for(game, "A", other)
        self.assertEqual(35, game.state.players["A"].life)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(("battlefield", "A", "B"), (
            self.card(game, other).zone, self.card(game, other).controller, self.card(game, other).owner,
        ))
        self.replay(game, load=True)

    def test_milled_land_plays_normally_and_a_new_source_does_not_reuse_a_link(self):
        game = self.session(60721402)
        source = self.add(game.engine, CARD)
        flicker = self.add(game.engine, "Flicker of Fate", zone="hand")
        mill = self.add(game.engine, "Thought Scour", zone="hand")
        unused = self.add(game.engine, "Grizzly Bears", seat="B", zone="library")
        land = self.add(game.engine, "Generic Bound Plains", seat="B", zone="library", ref="milled-land")
        self.start(game, source, mill, flicker, mana={"W": 1, "C": 1, "U": 1})
        self.cast_for(game, "A", mill, ["B"])
        self.assertIsNone(self.resolve(game))
        self.assert_link(game, land, source)
        self.assert_link(game, unused, source)
        self.priority(game, "A")
        packet = game.packet("pilot:A", full=True)
        land_offer, = [row for row in packet["decision"]["ctx"]["legal"]["actions"]
                       if row.get("action") == "play_land" and row.get("card") == land.ref]
        result = game.act("pilot:A", {"action_id": land_offer["id"]})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(("battlefield", "A", 40), (
            self.card(game, land).zone, self.card(game, land).controller, game.state.players["A"].life,
        ))
        original = self.card(game, source).logical_object_id
        self.cast_for(game, "A", flicker, [source.ref])
        self.assertIsNone(self.resolve(game))
        self.assertNotEqual(original, self.card(game, source).logical_object_id)
        self.priority(game, "A")
        rejected = game.act("pilot:A", {"action": "cast", "card": unused.ref, "from": "exile", "pay": "auto"})
        self.assertFalse(rejected.ok)
        self.assertEqual("exile", self.card(game, unused).zone)
        self.replay(game, load=True)

    def test_ward_requires_three_distinct_current_nonlands_and_stages_no_partial_payment(self):
        game = self.session(70221031)
        source = self.add(game.engine, CARD)
        removal = self.add(game.engine, "Murder", seat="B", zone="hand")
        payers = tuple(self.add(game.engine, "Grizzly Bears", seat="B", ref=f"ward-bears-{i}") for i in range(3))
        land = self.add(game.engine, "Generic Bound Plains", seat="B")
        game.state.players["B"].mana_pool.update({"C": 1, "B": 2})
        self.start(game, source, removal)
        self.cast_for(game, "B", removal, [source.ref])
        pending = self.resolve(game)
        self.assertEqual(("semantic.choice", ["B"]), (pending.kind, pending.actors))
        decision = game.packet("pilot:B", full=True)["decision"]
        offered, = [row for row in decision["ctx"]["legal_actions"] if row["id"] == "choose"]
        self.assertEqual({card.ref for card in payers}, set(offered["choice_schema"]["legal_refs"]))
        before = authoritative_state_hash(game.state)
        for invalid in ([payers[0].ref] * 3, [payers[0].ref, payers[1].ref, land.ref],
                        [payers[0].ref, payers[1].ref]):
            with self.subTest(invalid=invalid):
                response = game.act("pilot:B", {"action_id": "choose", "cards": invalid})
                self.assertFalse(response.ok)
                self.assertEqual(before, authoritative_state_hash(game.state))
        game = self.replay(game, load=True)
        response = game.act("pilot:B", {"action_id": "choose", "cards": [card.ref for card in payers]})
        self.assertTrue(response.ok, response.summary)
        for card in payers:
            self.assert_link(game, card, source)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", self.card(game, source).zone)
        self.assertEqual("graveyard", self.card(game, removal).zone)
        self.replay(game, load=True)

    def test_only_the_selected_replacement_links_an_exile_and_grants_no_off_turn_cast(self):
        game = self.session(60721404)
        source = self.add(game.engine, CARD)
        peace = self.add(game.engine, "Rest in Peace", seat="D")
        bear = self.add(game.engine, "Grizzly Bears", seat="B")
        removal = self.add(game.engine, "Murder", seat="B", zone="hand")
        game.state.players["B"].mana_pool.update({"C": 1, "B": 2})
        self.start(game, source, peace, removal, active="D")
        self.cast_for(game, "B", removal, [bear.ref])

        first = self.resolve(game)
        self.assertEqual(("replacement.order", ["B"]), (first.kind, first.actors))
        game = self.replay(game, load=True)
        options = game.packet("pilot:B", full=True)["decision"]["ctx"]["options"]
        self.assertEqual(2, len(options))
        replacement, = [row["id"] for row in options if f":{peace.ref}:" in row["id"]]
        receipt = game.act("pilot:B", {"action_id": "choose", "replacement": replacement})
        self.assertTrue(receipt.ok, receipt.summary)
        self.assertEqual("exile", self.card(game, bear).zone)
        self.assertNotIn(LINKED_ABILITY_EXILE_ANNOTATION, self.card(game, bear).annotations)

        second = self.resolve(game)
        self.assertEqual(("replacement.order", ["B"]), (second.kind, second.actors))
        options = game.packet("pilot:B", full=True)["decision"]["ctx"]["options"]
        replacement, = [row["id"] for row in options if f":{source.ref}:" in row["id"]]
        receipt = game.act("pilot:B", {"action_id": "choose", "replacement": replacement})
        self.assertTrue(receipt.ok, receipt.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("D", game.state.active_player)
        self.assert_link(game, removal, source)
        self.priority(game, "A")
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        self.assertFalse(any(row.get("card") == removal.ref for row in actions))
        before = authoritative_state_hash(game.state)
        rejected = game.act("pilot:A", {"action": "cast", "card": removal.ref, "from": "exile", "pay": "auto"})
        self.assertFalse(rejected.ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        self.replay(game, load=True)
