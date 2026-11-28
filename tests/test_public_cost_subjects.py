from __future__ import annotations

"""CR 601.2f/i: spell ordinals count committed casts, with closed type unions."""

import json
from pathlib import Path
import tempfile
import unittest

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_granted_skulk import record
import test_bound_effect_programs as actions
import test_self_public_zone_casting as zone_actions


class PublicCostSubjectCompilerTests(unittest.TestCase):
    def test_creature_affinity_grant_closes_one_controller_spell_type_union(self):
        from quorune.cast_cost_modifiers import CastCostAffectedController, PublicCastCostModifierV2Spec
        from quorune.compiler.cast_cost_modifier_templates import static_fixed_spell_cost_reduction_handler
        from quorune.self_cast_reductions import CastReductionMetric, CastReductionQueryScope

        text = "Instant and sorcery spells you cast have affinity for creatures."
        template, descriptor, _dependencies = static_fixed_spell_cost_reduction_handler(text)
        self.assertEqual("public-fixed-spell-cost-modifier-v1", template)
        modifier = PublicCastCostModifierV2Spec.from_dict(descriptor["modifier"])
        self.assertEqual(CastCostAffectedController.SOURCE_CONTROLLER, modifier.affected_controller)
        predicate, = modifier.predicates_any
        self.assertEqual(("instant", "sorcery"), predicate.types_any)
        self.assertEqual((("GENERIC", -1),), modifier.mana_adjustment)
        query, = CastReductionMetric.from_dict(modifier.multiplier).queries
        self.assertEqual(CastReductionQueryScope.CONTROLLER_ZONE, query.scope)
        self.assertEqual(("battlefield",), query.query.zones)
        self.assertEqual(("creature",), query.query.types_all)

        for old in ("Artifact creature spells you cast have affinity for artifacts.",
                    "Spells you cast have affinity for artifacts."):
            _, descriptor, _dependencies = static_fixed_spell_cost_reduction_handler(old)
            modifier = PublicCastCostModifierV2Spec.from_dict(descriptor["modifier"])
            query, = CastReductionMetric.from_dict(modifier.multiplier).queries
            self.assertEqual(("artifact",), query.query.types_all)
        for unsupported in (
            text.replace("you cast", "your opponents cast"),
            text.replace("for creatures", "for artifacts"),
            text.replace("Instant and sorcery spells", "Creature spells"),
            text + " (Discard a card.)",
        ):
            with self.subTest(unsupported=unsupported):
                self.assertIsNone(static_fixed_spell_cost_reduction_handler(unsupported))

    def test_unqualified_ordinals_and_type_disjunctions_keep_their_exact_predicates(self):
        registry = load_default_capability_registry()
        cases = (
            ('The second spell you cast each turn costs {1} less to cast.', 'second', ()),
            ('Dualcast — The second spell you cast each turn costs {2} less to cast.', 'second', ()),
            ('The first instant or sorcery spell you cast each turn costs {3} less to cast.', 'first', ('instant', 'sorcery')),
            ('Instant or sorcery spells you cast cost {1} less to cast.', 'any', ('instant', 'sorcery')),
        )
        for text, ordinal, types in cases:
            with self.subTest(text=text):
                ir = compile_oracle_card(record(text, 'Artifact'), capability_registry=registry,
                    capability_profile='commander_review')
                self.assertEqual('exact', ir.status, ir.material_residuals)
                descriptor = ir.faces[0].nodes[0].handlers[0]
                modifier = descriptor['modifier']
                self.assertEqual(ordinal, modifier['ordinal'])
                self.assertEqual(list(types), modifier['predicates_any'][0]['types_any'])
                self.assertEqual([], modifier['predicates_any'][0]['types_all'])

    def test_relative_color_union_is_one_discount_and_open_subjects_remain_residual(self):
        registry = load_default_capability_registry()
        text = "Each spell you cast that's red or green costs {1} less to cast."
        ir = compile_oracle_card(record(text, 'Creature — Goblin'), capability_registry=registry,
            capability_profile='commander_review')
        self.assertEqual('exact', ir.status, ir.material_residuals)
        modifier = ir.faces[0].nodes[0].handlers[0]['modifier']
        self.assertEqual(['G', 'R'], modifier['predicates_any'][0]['colors_any'])
        self.assertEqual({'GENERIC': -1}, modifier['mana_adjustment'])
        self.assertEqual(1, len(modifier['predicates_any']))
        for text in ('The third spell you cast each turn costs {1} less to cast.',
                     'The first kicked spell you cast each turn costs {1} less to cast.',
                     'The first red spell you cast each turn costs {1} less to cast.',
                     "Each spell you cast that's red or chosen costs {1} less to cast.",
                     'Instant or creature with flying spells you cast cost {1} less to cast.'):
            with self.subTest(text=text):
                ir = compile_oracle_card(record(text, 'Artifact'), capability_registry=registry,
                    capability_profile='commander_review')
                self.assertNotEqual('exact', ir.status)
                self.assertTrue(ir.material_residuals)


class PublicCostSubjectRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = actions.BoundEffectProgramRuntimeTests.session
    add = actions.BoundEffectProgramRuntimeTests.add
    checkpoint = actions.BoundEffectProgramRuntimeTests.checkpoint
    resolve = actions.BoundEffectProgramRuntimeTests.resolve
    replay = actions.BoundEffectProgramRuntimeTests.replay
    prepare = zone_actions.SelfPublicZoneRuntimeTests.prepare
    cast = zone_actions.SelfPublicZoneRuntimeTests.cast
    assert_closed = zone_actions.SelfPublicZoneRuntimeTests.assert_closed

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name).resolve() / 'public-cost-subjects.sqlite3'
        build_fixture_database([
            ROOT / 'tests/fixtures/bound-effect-program-cards.json',
            ROOT / 'tests/fixtures/public-cost-subject-cards.json',
            ROOT / 'tests/fixtures/legend-rule-exemption-cards.json',
            ROOT / 'tests/fixtures/cast-target-copy-cards.json',
        ], path)
        cls.db = CardDatabase(path); cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Public cost subject witness', [DeckEntry('Generic Bound Commander', 1, 'commander'),
            DeckEntry('Generic Bound Plains', 30)], ['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close(); cls.temporary.cleanup()

    def requirements(self, session, card):
        current = session.state.cards[card.object_id]
        action = next(row for row in session.packet('pilot:' + current.owner, full=True)['decision']['ctx']['legal']['actions']
            if row['id'] == 'cast:' + current.ref)
        return next(option['requirements'] for option in action['cost_options'] if option['id'] == 'normal')

    def test_cast_price_collects_public_cost_adjustments_once(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        from quorune.rules.casting import static_modifiers

        host = object()
        card = SimpleNamespace(oracle_id="price-fixture")
        option = {"requirements": {"GENERIC": 3}}
        adjustments = (
            SimpleNamespace(generic_adjustment=2, generic_reduction=0),
            SimpleNamespace(generic_adjustment=-1, generic_reduction=1),
        )
        with (
            patch.object(static_modifiers, "active_fixed_spell_cost_reductions", return_value=adjustments) as collect,
            patch.object(static_modifiers, "compiled_self_spell_cost_reduction_specs", return_value=()),
            patch("quorune.rules.casting.declared_cost_reductions.apply_declared_cost_reductions"),
        ):
            static_modifiers.apply_cast_static_reductions(host, "A", card, option, program=None)
        collect.assert_called_once_with(host, "A", card, cast_type_line=None, face_down_method=None)
        self.assertEqual({"GENERIC": 4}, option["requirements"])
        self.assertEqual([{"kind": "fixed_query", "count": 2}], option["cost_increases"])
        self.assertEqual([{"kind": "fixed_query", "count": 1}], option["cost_reductions"])

    def test_actual_witherbloom_grants_stack_and_follow_live_providers(self):
        from quorune.rules.casting.static_modifiers import fixed_generic_reduction

        game = self.session(399015)
        first = self.add(game.engine, "Witherbloom, the Balancer", ref="affinity-provider-one")
        second = self.add(game.engine, "Witherbloom, the Balancer", ref="affinity-provider-two")
        gallery = self.add(game.engine, "Mirror Gallery")
        self.add(game.engine, "Grizzly Bears", seat="B", ref="unrelated-opponent-creature")
        sorcery = self.add(game.engine, "Generic Ordinal Sorcery", zone="hand", ref="affinity-discounted-spell")
        later = self.add(game.engine, "Generic Ordinal Sorcery", zone="hand", ref="affinity-later-spell")
        foreign = self.add(game.engine, "Generic Ordinal Sorcery", seat="B", zone="hand")
        artifact = self.add(game.engine, "Generic Ordinal Colorless", zone="hand")
        phase = self.add(game.engine, "Reality Ripple", zone="hand")
        bounce = self.add(game.engine, "Unsummon", zone="hand")
        private = self.add(game.engine, "Generic Bound Growth", seat="D", zone="hand")
        for card in (first, second, gallery, sorcery, phase, bounce):
            self.assert_closed(game, card)
        self.prepare(game, mana={"U": 3})
        self.checkpoint(game)

        # Mirror Gallery keeps both legendary providers in play.
        self.assertEqual(4, fixed_generic_reduction(game.engine, "A", sorcery))
        self.assertEqual(0, fixed_generic_reduction(game.engine, "B", foreign))
        self.assertEqual(0, fixed_generic_reduction(game.engine, "A", artifact))
        self.assertEqual(4, fixed_generic_reduction(game.engine, "A", artifact, cast_type_line="Instant"))
        self.assertEqual(0, fixed_generic_reduction(game.engine, "A", sorcery, cast_type_line="Creature"))
        self.assertEqual(0, self.requirements(game, sorcery)["GENERIC"])
        self.assertEqual(1, self.requirements(game, sorcery)["U"])
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:B", {"action_id": "cast:" + sorcery.ref, "pay": "auto"}).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))

        self.cast(game, "A", sorcery)
        self.resolve(game)
        self.assertEqual(2, game.state.players["A"].mana_pool["U"])
        self.assertEqual(41, game.state.players["A"].life)
        self.cast(game, "A", phase, [second.ref])
        self.resolve(game)
        self.assertTrue(game.state.cards[second.object_id].phased_out)
        self.assertEqual(1, fixed_generic_reduction(game.engine, "A", later))
        game = self.replay(game, load=True)
        self.assertEqual(1, fixed_generic_reduction(game.engine, "A", later))

        self.cast(game, "A", bounce, [first.ref])
        self.resolve(game)
        self.assertEqual("hand", game.state.cards[first.object_id].zone)
        self.assertEqual(0, fixed_generic_reduction(game.engine, "A", later))
        self.assertEqual(0, sum(game.state.players["A"].mana_pool.values()))
        for seat in "ABC":
            self.assertNotIn(private.ref, json.dumps(game.packet("pilot:" + seat, full=True)))
        self.replay(game, load=True)

    def test_actual_bell_ringer_counts_its_own_cast_and_only_discounts_the_second_spell(self):
        session = self.session(399001); engine = session.engine
        source = self.add(engine, 'Highspire Bell-Ringer', zone='hand', ref='original-second-spell-reducer')
        second = self.add(engine, 'Generic Ordinal Colorless', zone='hand', ref='second-any-spell')
        third = self.add(engine, 'Generic Ordinal Sorcery', zone='hand', ref='third-any-spell')
        private = self.add(engine, 'Generic Bound Growth', seat='D', zone='hand', ref='ordinal-private')
        self.assert_closed(session, source); self.prepare(session, mana={'C': 8, 'U': 3}); self.checkpoint(session)
        before = authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:B', {'action_id': 'cast:' + source.ref, 'pay': 'auto'}).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        self.cast(session, 'A', source); self.resolve(session)
        self.assertEqual(1, self.requirements(session, second)['GENERIC'])
        self.cast(session, 'A', second)
        session = self.replay(session, load=True); self.resolve(session)
        self.assertEqual(4, self.requirements(session, third)['GENERIC'])
        self.cast(session, 'A', third); self.resolve(session)
        self.assertEqual(1, session.state.players['A'].mana_pool['C'])
        self.assertEqual(1, session.state.players['A'].mana_pool['U'])
        for viewer in 'ABC':
            self.assertNotIn(private.ref, json.dumps(session.packet('pilot:' + viewer, full=True)))
        self.replay(session, load=True)

    def test_actual_melek_counts_only_the_casters_instant_or_sorcery_history(self):
        session = self.session(399002); engine = session.engine
        source = self.add(engine, 'Melek, Reforged Researcher', zone='hand', ref='original-typed-first-reducer')
        self.add(engine, 'Generic Ordinal Sorcery', zone='graveyard', ref='melek-starting-sorcery')
        self.add(engine, 'Generic Ordinal Instant', zone='graveyard', ref='melek-starting-instant')
        first = self.add(engine, 'Generic Ordinal Instant', zone='hand', ref='first-typed-spell')
        second = self.add(engine, 'Generic Ordinal Sorcery', zone='hand', ref='second-typed-spell')
        foreign = self.add(engine, 'Generic Ordinal Zero Instant', seat='B', zone='hand', ref='foreign-cast-not-counted')
        self.assert_closed(session, source); self.prepare(session, mana={'C': 12, 'U': 8, 'R': 1}); self.checkpoint(session)
        self.assertEqual(3, self.requirements(session, source)['GENERIC'])
        self.assertEqual(1, self.requirements(session, source)['U'])
        self.assertEqual(1, self.requirements(session, source)['R'])
        self.cast(session, 'A', source); self.resolve(session)
        self.assertEqual(16, sum(session.state.players['A'].mana_pool.values()))
        self.cast(session, 'B', foreign); self.resolve(session)
        self.assertEqual(1, self.requirements(session, first)['GENERIC'])
        self.cast(session, 'A', first); self.resolve(session)
        self.assertEqual(14, sum(session.state.players['A'].mana_pool.values()))
        self.assertEqual(4, self.requirements(session, second)['GENERIC'])
        self.cast(session, 'A', second)
        session = self.replay(session, load=True); self.resolve(session)
        self.assertEqual(9, sum(session.state.players['A'].mana_pool.values()))
        self.assertEqual(0, session.state.players['A'].mana_pool['R'])
        self.assertEqual(42, session.state.players['A'].life)
        self.assertEqual(41, session.state.players['B'].life)
        self.replay(session, load=True)

    def test_actual_anarchomancer_applies_one_discount_to_a_multicolor_spell(self):
        session = self.session(399003); engine = session.engine
        source = self.add(engine, 'Goblin Anarchomancer', zone='hand', ref='original-color-union-reducer')
        both = self.add(engine, 'Generic Ordinal Red Green', zone='hand', ref='both-colors-one-discount')
        neither = self.add(engine, 'Generic Ordinal Sorcery', zone='hand', ref='blue-spell-no-discount')
        self.assert_closed(session, source); self.prepare(session, mana={'C': 12, 'U': 3, 'R': 4, 'G': 4}); self.checkpoint(session)
        self.cast(session, 'A', source); self.resolve(session)
        self.assertEqual(1, self.requirements(session, both)['GENERIC'])
        self.assertEqual(4, self.requirements(session, neither)['GENERIC'])
        self.cast(session, 'A', both); self.resolve(session)
        self.assertEqual(11, session.state.players['A'].mana_pool['C'])
        self.assertEqual(2, session.state.players['A'].mana_pool['R'])
        self.assertEqual(2, session.state.players['A'].mana_pool['G'])
        self.cast(session, 'A', neither)
        session = self.replay(session, load=True); self.resolve(session)
        self.assertEqual(7, session.state.players['A'].mana_pool['C'])
        self.assertEqual(42, session.state.players['A'].life)
        self.replay(session, load=True)
