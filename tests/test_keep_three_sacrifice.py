"The original keep-three instructions resolve through one simultaneous sacrifice."
from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.errors import GameRuleError
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.replacement.immutable import thaw_value
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.rules.evidence import validate_capability_evidence_index
from quorune.rules.evidence_storage import read_evidence_document
from quorune.rules.keep_three_sacrifice import KEEP_THREE_SACRIFICE_CAPABILITY, KEEP_THREE_SACRIFICE_FIELD
from quorune.zone_trigger_events import ZoneTransitionKind
from scripts.build_test_database import build_fixture_database
from scripts.update_capability_evidence import build_index, discover_tests
from test_controller_creature_keyword_absence import register_card


class KeepThreeSacrificeTests(InheritedNativeFixture, unittest.TestCase):
    firebombers = "Keldon Firebombers"
    purification = "Razia's Purification"
    profile = "commander_review"

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "keep-three.sqlite3"
        fixtures = ROOT / "tests/fixtures"
        build_fixture_database([
            fixtures / "bound-effect-program-cards.json",
            fixtures / "event-player-discard-result-cards.json",
            fixtures / "keep-three-sacrifice-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        ruler = "Generic Bound Commander"
        cls.deck = DeckDefinition("Keep three permanents",
            [DeckEntry(ruler, 1, "commander"), DeckEntry("Generic Bound Plains", 30)], [ruler])

    def lands(self, game, seat, count):
        return [self.add(game.engine, "Island", seat=seat, ref=f"LAND-{seat}-{i}") for i in range(count)]

    def choice(self, game, seat, options, prior):
        self.assertEqual(("choice.apnap", [seat]),
                         (game.state.pending_decision.kind, game.state.pending_decision.actors))
        ctx = game.packet("pilot:" + seat, full=True)["decision"]["ctx"]
        self.assertEqual(3, ctx["count"])
        self.assertEqual({card.ref for card in options}, set(ctx["options"]))
        self.assertEqual({player: [card.ref for card in cards] for player, cards in prior.items()},
                         ctx["prior_public_choices"])
        for other in set("ABCD") - {seat}:
            self.assertIsNone(game.packet("pilot:" + other, full=True)["decision"])
        return ctx

    def choose(self, game, seat, cards):
        return game.act("pilot:" + seat, {"action_id": "choose", "cards": [card.ref for card in cards]})

    def enter_firebombers(self, game, source, seat):
        self.cast(game, seat, source)
        self.resolve_top(game)
        trigger, = game.state.stack
        self.assertEqual(("triggered_ability", source.object_id, [], "permanent.enter"),
                         (trigger.kind, trigger.source_object_id, trigger.targets, trigger.context["event"]))

    def places(self, game, cards, zone):
        self.assertTrue(all(game.state.cards[card.object_id].zone == zone for card in cards))

    def test_lands_are_kept_in_turn_order_before_one_simultaneous_sacrifice(self):
        game = self.session(701214001)
        source = self.add(game.engine, self.firebombers, seat="C", zone="hand", ref="ACTUAL-FIREBOMBERS")
        a, b, c, d = (self.lands(game, player, count) for player, count in zip("ABCD", (5, 2, 5, 3)))
        borrowed = c[-1]
        borrowed.owner = "B"
        creature = self.add(game.engine, "Grizzly Bears", seat="C", ref="NONLAND")
        hidden = self.add(game.engine, "Grizzly Bears", seat="B", zone="hand", ref="PRIVATE-HAND")
        self.assert_closed(game, source)
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game, actor="C", active="C", mana={"C": 3, "R": 2})
        self.checkpoint(game)
        self.enter_firebombers(game, source, "C")

        self.assertIsNotNone(self.resolve(game))
        self.assertNotIn(hidden.ref, json.dumps(self.choice(game, "C", c, {})))
        self.assertIn("lands", self.choice(game, "C", c, {})["prompt"])
        game = self.replay(game, load=True)
        before = authoritative_state_hash(game.state)
        for seat, chosen in (("A", c[:3]), ("C", c[:2]), ("C", [c[0], c[0], c[1]]),
                             ("C", [c[0], c[1], creature]), ("C", [c[0], c[1], a[0]])):
            self.assertFalse(self.choose(game, seat, chosen).ok)
            self.assertEqual(before, authoritative_state_hash(game.state))
        result = self.choose(game, "C", c[:3])
        self.assertTrue(result.ok, result.summary)
        self.places(game, a + b + c + d, "battlefield")
        self.choice(game, "A", a, {"C": c[:3], "D": d})
        game = self.replay(game, load=True)

        before = authoritative_state_hash(game.state)
        move = game.engine.move_objects_simultaneously_intent

        def reject_after_movement(intent):
            move(intent)
            raise GameRuleError("Reject this transaction after the sacrifice movement.")

        with patch.object(game.engine, "move_objects_simultaneously_intent", side_effect=reject_after_movement):
            self.assertFalse(self.choose(game, "A", a[:3]).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        with patch.object(game.engine, "move_objects_simultaneously_intent", wraps=move) as movement:
            result = self.choose(game, "A", a[:3])
            self.assertTrue(result.ok, result.summary)
        batch, = [call.args[0] for call in movement.call_args_list
                  if call.args[0].transition_kind is ZoneTransitionKind.SACRIFICE]
        self.assertEqual(tuple(card.ref for card in c[3:] + a[3:]), batch.object_refs)
        self.places(game, c[3:] + a[3:], "graveyard")
        self.places(game, a[:3] + b + c[:3] + d + [creature, source], "battlefield")
        self.assertIn(borrowed.object_id, game.state.players["B"].zones["graveyard"])
        self.assertEqual([], game.state.stack)
        self.replay(game, load=True)

    def test_permanent_choices_preserve_real_graveyard_replacements(self):
        game = self.session(701214002)
        source = self.add(game.engine, self.purification, zone="hand", ref="ACTUAL-PURIFICATION")
        a_lands = self.lands(game, "A", 3)
        c_lands = self.lands(game, "C", 3)
        replacement = self.add(game.engine, "Rest in Peace", ref="REAL-GRAVEYARD-REPLACEMENT")
        creature = self.add(game.engine, "Grizzly Bears", seat="C", ref="SACRIFICED-CREATURE")
        self.assert_closed(game, source)
        self.assert_closed(game, replacement)
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game, mana={"C": 4, "R": 1, "W": 1})
        self.checkpoint(game)
        self.cast(game, "A", source)
        self.assertIsNotNone(self.resolve(game))
        ctx = self.choice(game, "A", a_lands + [replacement], {})
        self.assertIn("permanents", ctx["prompt"])
        kept = a_lands[:2] + [replacement]
        result = self.choose(game, "A", kept)
        self.assertTrue(result.ok, result.summary)
        self.choice(game, "C", c_lands + [creature], {"A": kept, "B": []})
        self.places(game, [a_lands[-1], creature], "battlefield")
        game = self.replay(game, load=True)
        result = self.choose(game, "C", c_lands)
        self.assertTrue(result.ok, result.summary)
        self.places(game, [a_lands[-1], creature, source], "exile")
        self.places(game, kept + c_lands, "battlefield")
        self.assertEqual([], game.state.stack)
        self.replay(game, load=True)

    def test_a_short_or_exact_set_finishes_without_requesting_a_choice(self):
        game = self.session(701214003)
        source = self.add(game.engine, self.firebombers, zone="hand", ref="ACTUAL-FIREBOMBERS")
        a, b, c, d = (self.lands(game, player, count) for player, count in zip("ABCD", (3, 0, 1, 2)))
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game, mana={"C": 3, "R": 2})
        self.checkpoint(game)
        self.enter_firebombers(game, source, "A")
        self.assertIsNone(self.resolve(game))
        self.places(game, a + b + c + d + [source], "battlefield")
        self.assertEqual([], game.state.stack)
        self.replay(game, load=True)

    def test_both_original_programs_reject_scope_changes_and_missing_authority(self):
        registry_path = ROOT / "quorune/rules/capability-registry.json"
        value = json.loads(registry_path.read_bytes())
        owner = next(row for row in value["capabilities"] if row["id"] == KEEP_THREE_SACRIFICE_CAPABILITY)
        owner.update(status="blocked", blockers=["Withdraw keep-three choice authority for this test."])
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        registry = CapabilityRegistry(value)
        index = build_index(registry_value=value, declaration_source=declarations,
                            discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        for name, template, module in (
            (self.firebombers, "keep_three_land_entry_template", "keep_three_sacrifice_templates"),
            (self.purification, "keep_three_permanents_spell_template", "resolution_effect_templates"),
        ):
            with self.subTest(card=name):
                record = self.db.lookup(name, fuzzy=False)
                catalogue, semantics = register_card(self.db, self.registry, name)
                self.assertTrue(catalogue["strict_capability_ready"], catalogue["blockers"])
                program, = (p for p in semantics.programs_for_oracle(record.oracle_id)
                            if KEEP_THREE_SACRIFICE_CAPABILITY in p.capability_dependencies)
                effect, = thaw_value(program.effects)
                self.assertIsNone(program.target_schema)
                for mutation in ({KEEP_THREE_SACRIFICE_FIELD: True}, {"count": 2}, {"players": "opponents"},
                                 {"then": "exile"}, {"predicate": {**effect["predicate"], "types_all": ["battle"]}}):
                    mutated = replace(program, effects=[{**effect, **mutation}], coverage=())
                    result = bind_semantic_program_runtime(mutated, capability_registry=self.registry,
                                                           profile=self.profile)
                    self.assertFalse(result["strict"], result)
                admitted = compile_oracle_card(record, capability_registry=registry, capability_profile=self.profile)
                self.assertNotEqual("exact", admitted.status)
                with patch(f"quorune.compiler.{module}.{template}", return_value=None):
                    omitted = compile_oracle_card(record, capability_registry=self.registry,
                                                  capability_profile=self.profile)
                    self.assertNotEqual("exact", omitted.status)
