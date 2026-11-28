"""Native and compiler witnesses for linked color-cast library exiles."""

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.adapters import compile_best_available_card_program
from quorune.carddb import CardDatabase, CardRecord
from quorune.compiler import linked_color_cast_exile as compiler
from quorune.deck import DeckDefinition, DeckEntry
from quorune.linked_ability_exile_state import card_has_current_exile_link
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.rules.evidence import validate_capability_evidence_index
from quorune.rules.evidence_storage import read_evidence_document
from quorune.rules.linked_color_cast_exile import (
    EXILE_NAME_CAST_CAPABILITY,
    LINKED_COLOR_TOP_EXILE,
    TOP_EXILE_CAPABILITY,
    LinkedColorCastExile,
    exile_name_cast_binding,
    validate_linked_color_cast_card_group,
)
from scripts.build_test_database import build_fixture_database
from scripts.update_capability_evidence import build_index, discover_tests


SOURCE = "Generic Archive Steward, Ledger Keeper"
BLUE = "Generic Archive Review"
BLACK = "Generic Archive Toll"
DUAL = "Generic Archive Notation"
FACE_A = "Generic Archive Increase"
FACE_B = "Generic Archive Pause"
SPLIT = FACE_A + " // " + FACE_B
PROFILE = "commander_review"
FIXTURE = "linked-color-cast-exile-cards.json"
TEXT = (
    "Whenever you cast a blue spell, exile the top card of target player's library.\n"
    "Whenever you cast a black spell, exile the top card of target player's library.\n"
    "Your opponents can't cast spells with the same name as a card exiled with this creature."
)


def compiler_record(text):
    return CardRecord(
        oracle_id="fixture:linked-color-cast-exile",
        name=SOURCE,
        mana_cost="{2}{U}{B}",
        mana_value=4,
        type_line="Legendary Creature — Human Wizard",
        oracle_text=text,
        power="2", toughness="3", loyalty=None, defense=None,
        colors=("U", "B"), color_identity=("U", "B"), keywords=(),
        produced_mana=(), layout="normal", released_at="2026-01-01",
        legalities={"commander": "legal"}, faces=(), raw={},
    )


class LinkedColorCastExileCompilerTests(unittest.TestCase):
    def compile(self, text, registry):
        return compile_oracle_card(compiler_record(text), capability_registry=registry, capability_profile=PROFILE)

    def test_linked_group_requires_two_exact_colors_and_original_reader(self):
        registry = load_default_capability_registry()
        verdict = self.compile(TEXT, registry)
        self.assertEqual("exact", verdict.status, verdict.material_residuals)
        blue, black, reader = verdict.faces[0].nodes
        specs = [LinkedColorCastExile.from_effect(node.effects[0]) for node in (blue, black)]
        self.assertEqual(["U", "B"], [spec.color for spec in specs])
        self.assertEqual({specs[0].binding_id}, {specs[1].binding_id, exile_name_cast_binding(reader.handlers[0])})
        for unexpected in (
            TEXT.replace("a black spell", "a blue spell"),
            TEXT.replace("Whenever you cast a black spell", "Whenever an opponent casts a black spell"),
            TEXT.replace("target player's", "target opponent's"),
            TEXT.replace("this creature.", "a different creature."),
            "\n".join(TEXT.splitlines()[1:]),
        ):
            with self.subTest(unexpected=unexpected):
                self.assertNotEqual("exact", self.compile(unexpected, registry).status)

    def test_withdrawn_authority_blocks_linked_group_but_keeps_independent_cast_rules(self):
        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        known = set(discover_tests(ROOT).values())
        for capability in (TOP_EXILE_CAPABILITY, EXILE_NAME_CAST_CAPABILITY):
            with self.subTest(capability=capability):
                modified = deepcopy(raw)
                entry = next(row for row in modified["capabilities"] if row["id"] == capability)
                entry.update(status="blocked", blockers=["Tested authority withdrawal"])
                registry = CapabilityRegistry(modified)
                evidence = build_index(registry_value=modified, declaration_source=declarations, discovered_test_ids=known)
                registry.mark_evidence_verified(validate_capability_evidence_index(evidence, registry=registry))
                self.assertNotEqual("exact", self.compile(TEXT, registry).status)
                old_rule = self.compile("Your opponents can't cast spells during your turn.", registry)
                self.assertEqual("exact", old_rule.status, old_rule.material_residuals)

    def test_linked_group_rejects_either_omitted_lowering(self):
        registry = load_default_capability_registry()
        self.assertEqual("exact", self.compile(TEXT, registry).status)
        for hook in ("_producer_spec", "_reader_names_original_source"):
            with self.subTest(hook=hook), patch.object(compiler, hook, return_value=None):
                self.assertNotEqual("exact", self.compile(TEXT, registry).status)


class LinkedColorCastExileNativeTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        fixture_dir = ROOT / "tests" / "fixtures"
        path = Path(cls.temporary.name) / "linked-exile.sqlite3"
        build_fixture_database([fixture_dir / "bound-effect-program-cards.json", fixture_dir / FIXTURE], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Generic Bound Commander"
        cls.deck = DeckDefinition("Archive", [DeckEntry(commander, 1, "commander"), DeckEntry("Generic Bound Plains", 30)], [commander])

    def begin(self, game, source, *, mana):
        self.assert_closed(game, source)
        program = compile_best_available_card_program(
            self.db, self.db.lookup(source.printed_name), semantic_registry=game.engine.semantics,
            capability_registry=self.registry, capability_profile=PROFILE,
        )
        validate_linked_color_cast_card_group(program.abilities)
        linked = [index for index, ability in enumerate(program.abilities)
                  if {TOP_EXILE_CAPABILITY, EXILE_NAME_CAST_CAPABILITY}.intersection(ability.capability_dependencies)]
        self.assertEqual(3, len(linked))
        for index in linked:
            with self.assertRaises(ValueError):
                validate_linked_color_cast_card_group(program.abilities[:index] + program.abilities[index + 1:])
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game, mana=mana)
        self.checkpoint(game)

    def submit(self, game, seat, command):
        result = game.act("pilot:" + seat, command)
        self.assertTrue(result.ok, result.summary)

    def reject(self, game, seat, command):
        before = authoritative_state_hash(game.state)
        result = game.act("pilot:" + seat, command)
        self.assertFalse(result.ok, result.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def cast_offers(self, game, seat, card):
        self.priority(game, seat)
        live = game.state.cards[card.object_id]
        rows = game.packet("pilot:" + seat, full=True)["decision"]["ctx"]["legal"]["actions"]
        return [row for row in rows if row.get("card") == live.ref and row["id"].startswith("cast:")]

    def current_color(self, game, semantic_key):
        return game.engine.semantics.get(semantic_key).effects[0][LINKED_COLOR_TOP_EXILE]["color"]

    def choose_library_owner(self, game, seat):
        pending = game.state.pending_decision
        self.assertEqual(("semantic.target", ["A"]), (pending.kind, pending.actors))
        packet = game.packet("pilot:A", full=True)["decision"]
        self.assertEqual(set("ABCD"), set(packet["ctx"]["target_schema"]["legal_refs"]))
        command = {"action_id": "choose", "targets": [seat]}
        self.reject(game, "C", command)
        self.reject(game, "A", {"action_id": "choose", "targets": ["no-such-player"]})
        if seat != "C":
            self.assertIsNone(game.packet("pilot:C", full=True)["decision"])
        self.submit(game, "A", command)

    def assert_visible_only_after_exile(self, game, *, hidden, revealed=None):
        for seat in "ABCD":
            packet = json.dumps(game.packet("pilot:" + seat, full=True))
            self.assertNotIn(game.state.cards[hidden.object_id].ref, packet)
            self.assertNotIn(hidden.object_id, packet)
            if revealed is not None:
                self.assertIn(game.state.cards[revealed.object_id].ref, packet)

    def test_dual_color_orders_top_exiles_and_prohibits_both_split_faces(self):
        game = self.session(70985101)
        source = self.add(game.engine, SOURCE)
        dual = self.add(game.engine, DUAL, zone="hand")
        review = self.add(game.engine, BLUE, seat="B", zone="library", ref="archive-hidden-review")
        split = self.add(game.engine, SPLIT, seat="B", zone="library", ref="archive-hidden-split")
        for hidden in (review, split):
            hidden.known_to = []
            hidden.revealed_to = []
        leave = self.add(game.engine, "Generic Archive Recover", zone="hand")
        matching = {seat: self.add(game.engine, SPLIT, seat=seat, zone="hand", ref=f"archive-split-{seat}")
                    for seat in "ABCD"}
        unrelated = {seat: self.add(game.engine, DUAL, seat=seat, zone="hand", ref=f"archive-unlinked-{seat}")
                     for seat in "BCD"}
        for seat in "BCD":
            game.state.players[seat].mana_pool.update({"U": 1, "B": 1})
        self.begin(game, source, mana={"U": 2, "B": 2, "W": 1})
        self.cast(game, "A", dual)
        self.assertEqual(("trigger.order", ["A"]),
                         (game.state.pending_decision.kind, game.state.pending_decision.actors))
        group, = game.state.pending_trigger_batches[0].groups
        by_color = {self.current_color(game, item.source_ability_id): item.ref for item in group.items}
        self.assertEqual({"U", "B"}, set(by_color))
        order = {"action_id": "order", "triggers": [by_color["B"], by_color["U"]]}
        self.reject(game, "B", order)
        self.submit(game, "A", order)
        self.assert_visible_only_after_exile(game, hidden=split)
        game = self.replay(game, load=True)
        self.choose_library_owner(game, "B")
        self.choose_library_owner(game, "B")
        self.assertEqual(order["triggers"], [item.ref for item in game.state.stack[-2:]])
        self.resolve_top(game)
        self.assertEqual(("exile", False), (game.state.cards[split.object_id].zone, game.state.cards[split.object_id].face_down))
        self.assert_visible_only_after_exile(game, hidden=review, revealed=split)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("exile", game.state.cards[review.object_id].zone)

        own = self.cast_offers(game, "A", matching["A"])
        self.assertEqual({FACE_A, FACE_B}, {row["face"] for row in own})
        front = next(row for row in own if row["face"] == FACE_A)
        self.submit(game, "A", {"action_id": front["id"], "pay": "auto"})
        self.choose_library_owner(game, "A")
        self.assertIsNone(self.resolve(game))
        for seat in "BCD":
            self.assertTrue(self.cast_offers(game, seat, unrelated[seat]))
            self.assertEqual([], self.cast_offers(game, seat, matching[seat]))
            live = game.state.cards[matching[seat].object_id]
            for face in (FACE_A, FACE_B):
                self.reject(game, seat, {"a": "cast", "card": live.ref, "face": face, "pay": "auto"})
        self.cast(game, "D", unrelated["D"])
        self.assertEqual(0, self.trigger_count(game, source))
        self.assertIsNone(self.resolve(game))

        self.cast(game, "A", leave, [game.state.cards[split.object_id].ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", game.state.cards[split.object_id].zone)
        offers = self.cast_offers(game, "B", matching["B"])
        self.assertEqual({FACE_A, FACE_B}, {row["face"] for row in offers})
        self.submit(game, "B", {"a": "cast", "card": matching["B"].ref, "face": FACE_A, "pay": "auto"})
        self.assertIsNone(self.resolve(game))
        self.replay(game)

    def test_pending_trigger_keeps_old_incarnation_and_only_new_links_prohibit(self):
        game = self.session(70985102)
        source = self.add(game.engine, SOURCE)
        blue = self.add(game.engine, BLUE, zone="hand", ref="archive-controller-blue")
        black = self.add(game.engine, BLACK, zone="hand", ref="archive-controller-black")
        blink = self.add(game.engine, "Generic Archive Return", zone="hand")
        next_top = self.add(game.engine, BLACK, seat="B", zone="library", ref="archive-new-restriction")
        first_top = self.add(game.engine, BLUE, seat="B", zone="library", ref="archive-old-restriction")
        old_name = self.add(game.engine, BLUE, seat="B", zone="hand", ref="archive-match-old")
        new_name = self.add(game.engine, BLACK, seat="B", zone="hand", ref="archive-match-new")
        game.state.players["B"].mana_pool.update({"U": 1, "B": 1})
        self.begin(game, source, mana={"U": 1, "B": 1, "W": 1})
        old = game.state.cards[source.object_id].logical_object_id
        self.cast(game, "A", blue)
        self.choose_library_owner(game, "B")
        trigger = game.state.stack[-1]
        self.assertEqual(("U", old),
                         (self.current_color(game, trigger.semantic_key), trigger.context["source_logical_object_id"]))
        binding = game.engine.semantics.get(trigger.semantic_key).effects[0][LINKED_COLOR_TOP_EXILE]["binding_id"]
        self.cast(game, "A", blink, [source.ref])
        self.resolve_top(game)
        current = game.state.cards[source.object_id]
        self.assertEqual("battlefield", current.zone)
        self.assertNotEqual(old, current.logical_object_id)
        new = current.logical_object_id
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        departed = game.state.cards[first_top.object_id]
        self.assertTrue(card_has_current_exile_link(departed, source_incarnation=old, ability_pair=binding))
        self.assertFalse(card_has_current_exile_link(departed, source_incarnation=new, ability_pair=binding))

        self.cast(game, "A", black)
        self.choose_library_owner(game, "B")
        trigger = game.state.stack[-1]
        self.assertEqual(("B", new),
                         (self.current_color(game, trigger.semantic_key), trigger.context["source_logical_object_id"]))
        self.assertIsNone(self.resolve(game))
        self.assertTrue(card_has_current_exile_link(game.state.cards[next_top.object_id],
                                                  source_incarnation=new, ability_pair=binding))
        self.assertTrue(self.cast_offers(game, "B", old_name))
        self.assertEqual([], self.cast_offers(game, "B", new_name))
        self.cast(game, "B", old_name)
        self.assertIsNone(self.resolve(game))
        self.replay(game)
