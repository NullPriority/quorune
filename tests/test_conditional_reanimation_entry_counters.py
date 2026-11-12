"""Pinned cast and entry boundaries for conditional reanimation counters."""

from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from test_granted_skulk import record
import test_fixed_copy_type_exceptions as copy_cases
from quorune.carddb import CardDatabase
from quorune.compiler.cast_cost_modifier_templates import _public_condition_metric
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.reanimation_entry_counters import (
    CONDITIONAL_REANIMATION_COUNTER_CAPABILITY,
    validate_reanimation_entry_effect,
)
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.rules.casting import public_conditions
from quorune.rules.casting.public_conditions import (
    ATTACKING_CONTROLLER_SELF_COST_CAPABILITY,
    self_public_condition_dependencies,
)
from quorune.rules.evidence import validate_capability_evidence_index
from quorune.rules.evidence_storage import read_evidence_document
from quorune.self_cast_reductions import CastReductionMetricKind, cast_reduction_multiplier
from scripts.build_test_database import build_fixture_database
from scripts.update_capability_evidence import build_index, discover_tests


RECOMMISSION = (
    "Return target artifact or creature card with mana value 3 or less from your graveyard to the battlefield. "
    "If a creature enters this way, it enters with an additional +1/+1 counter on it."
)
HEROIC_RETURN = (
    "This spell costs {2} less to cast if a creature is attacking you.\n"
    "Return target creature card from your graveyard to the battlefield. "
    "If a Hero enters this way, it enters with two additional +1/+1 counters on it."
)
LEGACY_RETURN = "Return target creature card from your graveyard to the battlefield with a +1/+1 counter on it."


class ConditionalEntryCounterCompilerTests(unittest.TestCase):
    def test_printed_qualification_and_runtime_shape_are_both_required(self):
        registry = load_default_capability_registry()

        def witness():
            ir = compile_oracle_card(record(RECOMMISSION, "Sorcery"), capability_registry=registry)
            self.assertEqual("exact", ir.status, ir.material_residuals)
            node, = ir.faces[0].nodes
            self.assertIn(CONDITIONAL_REANIMATION_COUNTER_CAPABILITY, node.capability_dependencies)
            effect, = node.effects
            self.assertEqual(6, effect["schema_version"])
            self.assertEqual(
                {"counter_name": "+1/+1", "amount": 1, "required_entry_type": "creature"},
                dict(effect["entry_counter"]),
            )
            validate_reanimation_entry_effect(effect)
            for condition in (None, True, "Creature", "planeswalker"):
                changed = {**effect, "entry_counter": {**effect["entry_counter"], "required_entry_type": condition}}
                with self.subTest(condition=condition), self.assertRaises(ValueError):
                    validate_reanimation_entry_effect(changed)
            with self.assertRaises(ValueError):
                validate_reanimation_entry_effect({**effect, "schema_version": 3})

        witness()
        from quorune.compiler import reanimation_entry_counter_templates as owner

        original = owner._entry_counter_result

        def omit_condition(compiled, name, amount, *, required_type=None):
            return original(compiled, name, amount)

        with patch.object(owner, "_entry_counter_result", side_effect=omit_condition), self.assertRaises(AssertionError):
            witness()
        for unsupported in (
            RECOMMISSION.replace("a creature enters", "a planeswalker enters"),
            RECOMMISSION.replace("enters this way", "would enter next turn"),
            RECOMMISSION + " Draw two or three cards.",
        ):
            with self.subTest(unsupported=unsupported):
                self.assertNotEqual("exact", compile_oracle_card(record(unsupported, "Sorcery"), capability_registry=registry).status)

    def test_withdrawing_either_new_owner_preserves_the_legacy_return(self):
        original = json.loads((ROOT / "quorune/rules/capability-registry.json").read_text(encoding="utf-8"))
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        known = set(discover_tests(ROOT).values())
        for owner, text, kind in (
            (CONDITIONAL_REANIMATION_COUNTER_CAPABILITY, RECOMMISSION, "Sorcery"),
            (ATTACKING_CONTROLLER_SELF_COST_CAPABILITY, HEROIC_RETURN, "Instant"),
        ):
            with self.subTest(owner=owner):
                raw = deepcopy(original)
                next(row for row in raw["capabilities"] if row["id"] == owner).update(
                    status="blocked", blockers=["Typed conditional authority withdrawn"],
                )
                registry = CapabilityRegistry(raw)
                index = build_index(registry_value=raw, declaration_source=declarations, discovered_test_ids=known)
                registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
                self.assertNotEqual("exact", compile_oracle_card(record(text, kind), capability_registry=registry).status)
                self.assertEqual("exact", compile_oracle_card(record(LEGACY_RETURN, "Sorcery"), capability_registry=registry).status)

    def test_mutating_the_direct_defender_check_breaks_the_current_combat_fact(self):
        metric = _public_condition_metric("if a creature is attacking you")
        self.assertIs(metric.kind, CastReductionMetricKind.CREATURE_ATTACKING_CONTROLLER)
        self.assertIn(ATTACKING_CONTROLLER_SELF_COST_CAPABILITY, self_public_condition_dependencies(metric.to_dict()))
        attacker = SimpleNamespace(object_id="attacker", zone="battlefield", phased_out=False)
        state = SimpleNamespace(cards={attacker.object_id: attacker}, combat=SimpleNamespace(attackers={}))
        host = SimpleNamespace(
            state=state,
            _effective_card_data=lambda card, **kwargs: {"type_line": "Creature"},
            _type_parts=lambda type_line: ({type_line.casefold()}, set(), set()),
        )

        def witness():
            for defender, expected in (("B", 1), ("C", 0), ("B-walker", 0)):
                state.combat.attackers[attacker.object_id] = defender
                self.assertEqual(expected, cast_reduction_multiplier(host, "B", metric))
            state.combat.attackers.clear()
            self.assertEqual(0, cast_reduction_multiplier(host, "B", metric))

        witness()
        with patch.object(public_conditions, "current_creature_attacking_controller", return_value=True):
            with self.assertRaises(AssertionError):
                witness()


class ConditionalEntryCounterNativeTests(InheritedNativeFixture, unittest.TestCase):
    choose_copy = copy_cases.FixedCopyTypeExceptionRuntimeTests.choose_copy

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "conditional-entry.sqlite3"
        build_fixture_database([
            ROOT / "tests/fixtures/bound-effect-program-cards.json",
            ROOT / "tests/fixtures/conditional-reanimation-entry-counter-cards.json",
            ROOT / "tests/fixtures/combat-defending-player-cards.json",
            ROOT / "tests/fixtures/counter-replacement-cards.json",
            ROOT / "tests/fixtures/shared-creature-type-cards.json",
            ROOT / "tests/fixtures/source-label-characteristic-cards.json",
            ROOT / "tests/fixtures/static-land-animation-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition(
            "Conditional entry",
            [DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Generic Bound Plains", 30)],
            ["Isamaru, Hound of Konda"],
        )

    def test_actual_recommission_reads_the_prospective_layer_four_entry(self):
        for animate in (False, True):
            with self.subTest(animate=animate):
                session = self.session(614120610 + int(animate))
                spell = self.add(session.engine, "Recommission", zone="hand")
                target = self.add(session.engine, "Darksteel Citadel", zone="graveyard")
                self.add(session.engine, "Doubling Season")
                if animate:
                    self.add(session.engine, "Nature's Revolt")
                private = self.add(session.engine, "Twiddle", seat="D", zone="hand")
                self.assert_closed(session, spell)
                self.assertNotIn("creature", session.engine._type_parts(session.engine._effective_card_data(target)["type_line"])[0])
                self.prepare(session, mana={"C": 1, "W": 1})
                self.checkpoint(session)
                self.cast(session, "A", spell, [target.ref])
                session = self.replay(session, load=True)
                self.assertIsNone(self.resolve(session))
                current = session.state.cards[target.object_id]
                self.assertEqual("battlefield", current.zone)
                self.assertEqual(2 if animate else 0, current.counters.get("+1/+1", 0))
                for seat in "ABC":
                    self.assertNotIn(private.ref, json.dumps(session.packet(f"pilot:{seat}", full=True)))
                self.replay(session, load=True)

    def test_actual_heroic_return_prices_only_current_attacks_at_its_caster(self):
        for index, mode in enumerate(("direct", "other", "planeswalker", "removed")):
            with self.subTest(mode=mode):
                session = self.session(601205060 + index)
                spell = self.add(session.engine, "Heroic Return", seat="B", zone="hand")
                target = self.add(session.engine, "Universal Automaton", seat="B", zone="graveyard")
                attacker = self.add(session.engine, "Grizzly Bears")
                attacker.acquired_control_turn_count = -1
                defender = "B"
                if mode == "other":
                    defender = "C"
                if mode == "planeswalker":
                    walker = self.add(session.engine, "Ral, Caller of Storms", seat="B")
                    walker.counters["loyalty"] = 5
                    defender = walker.ref
                if mode == "removed":
                    removal = self.add(session.engine, "Unsummon", seat="C", zone="hand")
                    self.add(session.engine, "Island", seat="C")
                for island in range(3):
                    self.add(session.engine, "Island", seat="B", ref=f"caster-island-{island}")
                self.add(session.engine, "Generic Bound Plains", seat="B")
                private = self.add(session.engine, "Twiddle", seat="D", zone="hand")
                self.assert_closed(session, spell)
                self.prepare(session)
                self.checkpoint(session)
                self.attack(session, [(attacker, defender)])
                if mode == "removed":
                    self.cast(session, "C", removal, [attacker.ref])
                    self.resolve_top(session)
                    self.assertNotIn(attacker.object_id, session.state.combat.attackers)
                    self.assertTrue(session.state.combat.had_attacking_creature)
                self.priority(session, "B")
                before = authoritative_state_hash(session.state)
                command = {"action_id": f"cast:{spell.ref}", "targets": [target.ref], "pay": "auto"}
                result = session.act("pilot:B", command)
                self.assertEqual(mode == "direct", result.ok, result.summary)
                if mode == "direct":
                    self.assertIsNone(self.resolve(session))
                    current = session.state.cards[target.object_id]
                    self.assertEqual("battlefield", current.zone)
                    self.assertEqual(2, current.counters.get("+1/+1", 0))
                else:
                    self.assertEqual(before, authoritative_state_hash(session.state))
                for seat in "ABC":
                    self.assertNotIn(private.ref, json.dumps(session.packet(f"pilot:{seat}", full=True)))
                self.replay(session, load=True)

    def test_actual_heroic_return_rechecks_the_copied_entry_form(self):
        for index, (copy_name, expected) in enumerate((("Universal Automaton", 2), ("Grizzly Bears", 0))):
            with self.subTest(copy_name=copy_name):
                session = self.session(616100720 + index)
                spell = self.add(session.engine, "Heroic Return", zone="hand")
                clone = self.add(session.engine, "Clone", zone="graveyard")
                model = self.add(session.engine, copy_name, seat="B")
                self.assert_closed(session, spell)
                self.assert_closed(session, clone)
                self.prepare(session, mana={"C": 5, "W": 1})
                self.checkpoint(session)
                self.cast(session, "A", spell, [clone.ref])
                session = self.choose_copy(session, model)
                current = session.state.cards[clone.object_id]
                self.assertEqual("battlefield", current.zone)
                self.assertEqual(expected, current.counters.get("+1/+1", 0))
                self.replay(session, load=True)

    def test_actual_winter_soldier_uses_its_own_attack_and_relative_target(self):
        session = self.session(608206111)
        source = self.add(session.engine, "Winter Soldier, Reborn Avenger")
        source.acquired_control_turn_count = -1
        target = self.add(session.engine, "Universal Automaton", zone="graveyard")
        self.add(session.engine, "Clone", zone="graveyard")
        self.add(session.engine, "Memnite", seat="B", zone="graveyard")
        self.assert_closed(session, source)
        self.prepare(session)
        self.checkpoint(session)
        self.attack(session, [(source, "B")])
        decision = session.packet("pilot:A", full=True)["decision"]
        self.assertEqual("semantic.target", decision["kind"])
        self.assertEqual([target.ref], decision["ctx"]["target_schema"]["legal_refs"])
        self.target(session, "A", [target.ref])
        self.assertIsNone(self.resolve(session))
        current = session.state.cards[target.object_id]
        self.assertEqual("battlefield", current.zone)
        self.assertEqual(1, current.counters.get("+1/+1", 0))
        self.replay(session, load=True)
