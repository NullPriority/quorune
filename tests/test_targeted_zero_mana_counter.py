from copy import deepcopy
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
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.replacement.immutable import thaw_value
from quorune.rules.capabilities import load_default_capability_registry
from quorune.spell_mana_counts import MANA_SPENT_COUNTS_CAPABILITY
from quorune.stack_counter import NO_MANA_SPENT_COUNTER_FIELD, stack_spell_has_no_mana_spent
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


PROFILE = "commander_review"
NAME = "Nix"
COUNTER = "stack.counter.effect"
FIXTURES = ROOT / "tests" / "fixtures"


class TargetedZeroManaCounterTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        support = {
            "ability-label-composition-cards.json": {"Island", "Unsummon"},
            "ability-source-counter-cards.json": {"Counterspell", "Grizzly Bears", "Mind Stone", "Twincast"},
            "activation-result-views-cards.json": {"Ornithopter"},
            "bound-effect-program-cards.json": {"Generic Bound Plains"},
            "expend-cards.json": {"Frogmite"},
            "source-attack-alone-cards.json": {"Isamaru, Hound of Konda"},
        }
        parts = [FIXTURES / "targeted-zero-mana-counter-cards.json"]
        for filename, names in support.items():
            data = json.loads((FIXTURES / filename).read_bytes())
            data["cards"] = [row for row in data["cards"] if row["name"] in names]
            assert {row["name"] for row in data["cards"]} == names
            ids = {row["oracle_id"] for row in data["cards"]}
            data["rulings"] = [row for row in data.get("rulings", ()) if row["oracle_id"] in ids]
            piece = folder / filename
            piece.write_text(json.dumps(data), encoding="utf-8")
            parts.append(piece)
        database = folder / "targeted-zero-mana-counter.sqlite3"
        build_fixture_database(parts, database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Target spell actual mana", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def original_program(self):
        card = self.db.lookup(NAME, fuzzy=False)
        bound, semantics = register_card(self.db, self.registry, NAME)
        self.assertTrue(bound["strict_capability_ready"], bound["blockers"])
        program, = semantics.programs_for_oracle(card.oracle_id)
        return card, program

    def test_complete_original_preserves_any_spell_target_and_exact_resolution_test(self):
        card, program = self.original_program()
        ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        self.assertEqual(("resolve", "stack"), (program.event, program.active_zone))
        self.assertEqual({"zones": ["stack"], "categories": ["spell"], "source_exclusion": True, "count": 1},
                         thaw_value(program.target_schema))
        self.assertEqual([{"op": "counter_stack_target", "stack": "$target.0", NO_MANA_SPENT_COUNTER_FIELD: 1}],
                         thaw_value(program.effects))
        self.assertTrue({COUNTER, MANA_SPENT_COUNTS_CAPABILITY, "target.revalidate_resolution"}
                        <= set(program.capability_dependencies))

    def test_malformed_version_spell_resolution_and_target_scope_fail_closed(self):
        card, program = self.original_program()
        effect, = thaw_value(program.effects)
        schema = thaw_value(program.target_schema)
        for changes in (
            {"event": "activate", "coverage": ()},
            {"active_zone": "battlefield"},
            {"target_schema": None},
            {"target_schema": {**schema, "count": True}},
            {"target_schema": {**schema, "source_exclusion": 1}},
            {"target_schema": {**schema, "types_any": ["creature"]}},
            {"effects": [{**effect, NO_MANA_SPENT_COUNTER_FIELD: True}]},
            {"effects": [{**effect, NO_MANA_SPENT_COUNTER_FIELD: 1.0}]},
            {"effects": [{**effect, NO_MANA_SPENT_COUNTER_FIELD: 2}]},
            {"effects": [{**effect, "stack": "$context.stack"}]},
            {"effects": [effect, effect]},
        ):
            with self.subTest(changes=changes):
                bound = bind_semantic_program_runtime(replace(program, **changes),
                    capability_registry=self.registry, profile=PROFILE)
                self.assertFalse(bound["strict"], bound)
        for old, new in (("target spell", "target creature spell"),
                         ("no mana", "one mana"), ("to cast it", "this turn")):
            ir = compile_oracle_card(replace(card, oracle_text=card.oracle_text.replace(old, new)),
                capability_registry=self.registry, capability_profile=PROFILE)
            self.assertNotEqual("exact", ir.status, new)

    def _islands(self, game, seat, count):
        return [self.add(game.engine, "Island", seat=seat, ref=f"nix-{seat}-mana-{i}") for i in range(count)]

    def _spell(self, game, card):
        return next(item for item in game.state.stack if item.card_object_id == card.object_id)

    def _accept(self, game, seat, command):
        result = game.act("pilot:" + seat, command)
        self.assertTrue(result.ok, result.summary)

    def test_actual_paid_spell_is_offered_and_remains_after_nix_resolves(self):
        game = self.session(76156781)
        spell = self.add(game.engine, "Mind Stone", zone="hand")
        nix = self.add(game.engine, NAME, seat="B", zone="hand")
        island, = self._islands(game, "B", 1)
        self.assert_closed(game, spell)
        self.assert_closed(game, nix)
        self.prepare(game, mana={"C": 2})
        self.checkpoint(game)
        self.cast(game, "A", spell)
        original = self._spell(game, spell)
        self.assertEqual(2, sum(original.mana_spent_counts.amounts))
        self.assertFalse(stack_spell_has_no_mana_spent(game.state, replace(original, controller="C")))

        self.priority(game, "B")
        action, = (row for row in game.packet("pilot:B", full=True)["decision"]["ctx"]["legal"]["actions"]
                   if row["id"] == "cast:" + nix.ref)
        self.assertIn(original.ref, action["target_schema"]["legal_refs"])
        command = {"action_id": action["id"], "targets": [original.ref], "pay": "auto"}
        before = authoritative_state_hash(game.state)
        for seat, attempted in (("C", command), ("B", {**command, "targets": ["A"]})):
            self.assertFalse(game.act("pilot:" + seat, attempted).ok)
            self.assertEqual(before, authoritative_state_hash(game.state))
        self._accept(game, "B", command)
        self.assertTrue(game.state.cards[island.object_id].tapped)
        self.resolve_top(game)
        self.assertEqual("graveyard", game.state.cards[nix.object_id].zone)
        self.assertEqual(original.ref, self._spell(game, spell).ref)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("battlefield", game.state.cards[spell.object_id].zone)
        self.replay(game, load=True)

    def test_actual_affinity_zero_uses_the_original_incarnation_payment_receipt(self):
        game = self.session(76156782)
        frogmite = self.add(game.engine, "Frogmite", zone="hand")
        nix = self.add(game.engine, NAME, seat="B", zone="hand")
        self._islands(game, "B", 1)
        for index in range(4):
            self.add(game.engine, "Ornithopter", ref=f"nix-affinity-source-{index}")
        for card in (frogmite, nix):
            self.assert_closed(game, card)
        self.prepare(game)
        self.checkpoint(game)

        self.cast(game, "A", frogmite)
        original = self._spell(game, frogmite)
        receipt = original.mana_spent_counts
        self.assertEqual(0, sum(receipt.amounts))
        self.assertTrue(stack_spell_has_no_mana_spent(game.state, replace(original, controller="C")))
        self.assertFalse(stack_spell_has_no_mana_spent(game.state, replace(original, mana_spent_counts=None)))
        stale = replace(receipt, logical_object_id="departed-spell-incarnation")
        self.assertFalse(stack_spell_has_no_mana_spent(game.state, replace(original, mana_spent_counts=stale)))
        self.cast(game, "B", nix, [original.ref])
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", game.state.cards[frogmite.object_id].zone)
        self.assertEqual("graveyard", game.state.cards[nix.object_id].zone)
        self.replay(game, load=True)

    def test_actual_direct_copy_can_be_countered_even_when_the_original_cast_paid_mana(self):
        game = self.session(76156783)
        spell = self.add(game.engine, "Unsummon", zone="hand")
        twincast = self.add(game.engine, "Twincast", zone="hand", seat="B")
        nix = self.add(game.engine, NAME, zone="hand", seat="C")
        first = self.add(game.engine, "Grizzly Bears", seat="B", ref="nix-original-target")
        copied = self.add(game.engine, "Grizzly Bears", seat="D", ref="nix-copy-target")
        private = self.add(game.engine, "Counterspell", seat="D", zone="hand", ref="nix-private-counter")
        self._islands(game, "B", 2)
        self._islands(game, "C", 1)
        for card in (spell, twincast, nix):
            self.assert_closed(game, card)
        self.prepare(game, mana={"U": 1})
        self.checkpoint(game)

        self.cast(game, "A", spell, [first.ref])
        original = self._spell(game, spell)
        self.assertEqual(1, sum(original.mana_spent_counts.amounts))
        self.cast(game, "B", twincast, [original.ref])
        pending = self.resolve(game)
        self.assertEqual(("semantic.choice", ["B"]), (pending.kind, pending.actors))
        self._accept(game, "B", {"action_id": "choose", "targets": [copied.ref]})
        copy, = (item for item in game.state.stack if item.kind == "spell_copy")
        self.assertIsNotNone(copy.card_object_id)
        self.assertIsNone(copy.mana_spent_counts)
        self.assertTrue(stack_spell_has_no_mana_spent(game.state, copy))
        self.assertEqual([copied.ref], copy.targets)
        self.cast(game, "C", nix, [copy.ref])
        self.resolve_top(game)
        self.assertFalse(any(item.ref == copy.ref for item in game.state.stack))
        self.assertEqual(original.ref, self._spell(game, spell).ref)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("battlefield", game.state.cards[copied.object_id].zone)
        self.assertEqual("hand", game.state.cards[first.object_id].zone)
        for seat in "ABC":
            self.assertNotIn(private.ref, json.dumps(game.packet("pilot:" + seat, full=True)))
        self.replay(game, load=True)

    def test_actual_departed_target_does_not_redirect_the_counter_or_refund_cost(self):
        game = self.session(76156784)
        spell = self.add(game.engine, "Ornithopter", zone="hand")
        nix = self.add(game.engine, NAME, seat="B", zone="hand")
        counter = self.add(game.engine, "Counterspell", seat="C", zone="hand")
        island, = self._islands(game, "B", 1)
        self._islands(game, "C", 2)
        self.assert_closed(game, nix)
        self.prepare(game)
        self.checkpoint(game)
        self.cast(game, "A", spell)
        target = self._spell(game, spell)
        self.cast(game, "B", nix, [target.ref])
        self.cast(game, "C", counter, [target.ref])
        self.resolve_top(game)
        self.assertFalse(any(item.ref == target.ref for item in game.state.stack))
        self.assertIsNone(self.resolve(game))
        self.assertTrue(game.state.cards[island.object_id].tapped)
        self.assertEqual("graveyard", game.state.cards[nix.object_id].zone)
        self.assertEqual("graveyard", game.state.cards[spell.object_id].zone)
        self.replay(game, load=True)

    def test_omitting_payment_condition_lowering_leaves_only_the_original_residual(self):
        import quorune.compiler.resolution_effect_templates as owner

        from quorune.compiler.counter_templates import targeted_counter_effect_template
        original = targeted_counter_effect_template
        def omit_exact(text):
            template = original(text)
            return None if template is not None and template.only_if_no_mana_spent else template

        routes = owner._DIRECT_RESOLUTION_EFFECT_COMPILERS
        self.assertIn(original, routes)
        mutated = tuple(omit_exact if route is original else route for route in routes)
        with patch.object(owner, "_DIRECT_RESOLUTION_EFFECT_COMPILERS", mutated):
            bound, _ = register_card(self.db, self.registry, NAME)
            other, _ = register_card(self.db, self.registry, "Counterspell")
        self.assertFalse(bound["strict_capability_ready"], bound)
        self.assertTrue(other["strict_capability_ready"], other["blockers"])

    def test_verified_withdrawal_requires_actual_mana_receipt_authority(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        source = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        raw = deepcopy(source)
        next(row for row in raw["capabilities"] if row["id"] == MANA_SPENT_COUNTS_CAPABILITY).update(
            status="blocked", blockers=["Original spell mana receipt authority withdrawn"])
        registry = CapabilityRegistry(raw)
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        known = set(discover_tests(ROOT).values())
        index = build_index(registry_value=raw, declaration_source=declarations, discovered_test_ids=known)
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        closure = registry.closure((MANA_SPENT_COUNTS_CAPABILITY,), profile=PROFILE)
        self.assertIn("status:" + MANA_SPENT_COUNTS_CAPABILITY + ":blocked", closure.blockers)
        self.assertFalse(any("unverified" in reason for reason in closure.blockers))
        bound, _ = register_card(self.db, registry, NAME)
        other, _ = register_card(self.db, registry, "Counterspell")
        self.assertFalse(bound["strict_capability_ready"], bound)
        self.assertTrue(other["strict_capability_ready"], other["blockers"])
