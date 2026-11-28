"""Public sacrifice eligibility follows current type/color and player control."""
from dataclasses import replace
import unittest

from quorune.compiler.affected_player_sacrifice_templates import (
    fixed_affected_player_sacrifice_effect_template,
    fixed_affected_player_sacrifice_predicate_is_closed,
)
from quorune.object_query import ObjectQueryResult, query_objects
from quorune.rules.affected_player_sacrifice_capability_shapes import (
    fixed_affected_player_sacrifice_node_capabilities,
)


class FixedPublicSacrificePredicateModelTests(unittest.TestCase):
    def row(self, ref, *, types=('land',), subtypes=(), supertypes=(), colors=(),
            owner='A', controller='A'):
        return ObjectQueryResult(object_id=ref, ref=ref, printed_name='Public eligible object',
            owner=owner, controller=controller, zone='battlefield', types=types,
            subtypes=subtypes, supertypes=supertypes, colors=colors)

    def template(self, text):
        result = fixed_affected_player_sacrifice_effect_template(text)
        self.assertIsNotNone(result, text)
        self.assertTrue(fixed_affected_player_sacrifice_node_capabilities(
            effects=result.effects, target_schema=result.target_schema, mechanic_ids=result.mechanics))
        return result

    def test_basic_land_sacrifice_uses_subtype_not_basic_or_snow_supertype(self):
        template = self.template('Sacrifice a Mountain.')
        query = replace(template.predicate, controller='A')
        basic = self.row('basic', subtypes=('mountain',), supertypes=('basic',))
        dual = self.row('dual', subtypes=('forest', 'mountain'), owner='B')
        snow = self.row('snow', subtypes=('mountain',), supertypes=('basic', 'snow'))
        forest = self.row('forest', subtypes=('forest',))
        foreign = self.row('foreign', subtypes=('mountain',), controller='B')
        self.assertEqual(('basic', 'dual', 'snow'),
            tuple(r.ref for r in query_objects((basic, dual, snow, forest, foreign), query)))
        self.assertEqual((), query_objects((replace(dual, subtypes=('forest',)),), query))
        for subtype in ('Plains', 'Island', 'Swamp', 'Forest'):
            with self.subTest(subtype=subtype):
                self.assertEqual((subtype.lower(),), self.template('Sacrifice a '+subtype+'.').predicate.subtypes_all)

    def test_powerstone_sacrifice_requires_current_artifact_subtype(self):
        template = self.template('Sacrifice a Powerstone.')
        query = replace(template.predicate, controller='A')
        stone = self.row('stone', types=('artifact',), subtypes=('powerstone',), owner='B')
        ordinary = self.row('ordinary', types=('artifact',))
        wrong_type = self.row('wrong-type', types=('creature',), subtypes=('powerstone',))
        foreign = replace(stone, ref='foreign', object_id='foreign', controller='B')
        self.assertEqual((stone,), query_objects((stone, ordinary, wrong_type, foreign), query))

    def test_colored_permanent_uses_current_colors_and_includes_multicolor_once(self):
        query = replace(self.template("Sacrifice a permanent that's one or more colors.").predicate,
                        controller='A')
        green = self.row('green', types=('land', 'creature'), colors=('G',), owner='B')
        multi = self.row('multi', types=('artifact', 'creature'), colors=('W', 'U'))
        colorless = self.row('colorless', types=('creature',))
        foreign = replace(green, ref='foreign', object_id='foreign', controller='B')
        self.assertEqual((green, multi), query_objects((green, multi, colorless, foreign), query))
        self.assertEqual((), query_objects((replace(green, colors=()),), query))

    def test_fixed_plural_quantities_preserve_the_existing_apnap_count(self):
        for phrase in ('Mountains', 'Powerstones', 'permanents that are one or more colors'):
            with self.subTest(phrase=phrase):
                template = self.template('Each player sacrifices two '+phrase+'.')
                self.assertEqual(2, template.count)
                self.assertEqual('all', template.effects[0]['players'])
                self.assertIsNone(template.target_schema)

    def test_unowned_predicate_tails_and_source_relative_exclusions_stay_outside_this_family(self):
        other = self.template('Sacrifice another Powerstone.')
        self.assertTrue(other.exclude_source)
        self.assertEqual(('powerstone',), other.predicate.subtypes_all)
        for text in ('Sacrifice a Mountain you owned last turn.',
                'Sacrifice a Powerstone in your hand.', 'Sacrifice a colored card.',
                "Sacrifice a permanent that's exactly two colors.",
                'Sacrifice a permanent that used to be green.'):
            with self.subTest(text=text):
                self.assertIsNone(fixed_affected_player_sacrifice_effect_template(text))
        value = self.template('Sacrifice a Mountain.').predicate.to_dict()
        for field, replacement in (('zones', ['hand']), ('owner', 'B'), ('exclude_ref', '$source')):
            with self.subTest(field=field):
                self.assertFalse(fixed_affected_player_sacrifice_predicate_is_closed({**value, field:replacement}))


import tempfile
from pathlib import Path
from unittest.mock import patch
from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
import test_recipient_control as seat_actions
from quorune.carddb import CardDatabase
from quorune.card_programs.adapters import compile_best_available_card_program
from quorune.card_programs.binding import bind_card_program_runtime
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card, register_generated_programs
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantics import SemanticRegistry

from scripts.build_test_database import build_fixture_database

ORIGINALS = ('Alpine Guide', 'Horned Stoneseeker', "Ugin's Construct")


class FixedPublicSacrificePredicateNativeTests(InheritedNativeFixture, unittest.TestCase):
    reject = seat_actions.RecipientControlNativeTests.reject
    finish = seat_actions.RecipientControlNativeTests.finish

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(); path = Path(cls.temp.name)/'sacrifice-predicates.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/fixed-public-sacrifice-predicates-cards.json'], path)
        cls.db = CardDatabase(path); cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Public sacrifice predicates', [
            DeckEntry('Generic Bound Commander', 1, 'commander'),
            DeckEntry('Generic Bound Plains', 20)], ['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close(); cls.temp.cleanup()

    def options(self, s):
        self.assertEqual('choice.apnap', s.state.pending_decision.kind)
        return set(s.packet('pilot:A', full=True)['decision']['ctx']['options'])

    def sacrifice(self, s, card):
        command = {'action_id': 'choose', 'cards': [card.ref]}
        self.reject(s, 'B', command)
        s = self.replay(s, load=True)
        result = s.act('pilot:A', command); self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(s)); return s

    def test_three_complete_originals_have_normal_program_admission(self):
        semantics = SemanticRegistry(); cards = [self.db.lookup(name) for name in ORIGINALS]
        register_generated_programs(self.db, semantics, cards, trust_level='provisional',
            capability_registry=self.registry, capability_profile='commander_review',
            promote_exact_runtime_handlers=True, promote_exact_trigger_programs=True,
            promote_exact_effect_programs=True, promote_exact_capability_declarations=True)
        for card in cards:
            with self.subTest(name=card.name):
                ir = compile_oracle_card(card, capability_registry=self.registry,
                                         capability_profile='commander_review')
                self.assertEqual('exact', ir.status, ir.material_residuals)
                p = compile_best_available_card_program(self.db, card, semantic_registry=semantics,
                    capability_registry=self.registry, capability_profile='commander_review')
                b = bind_card_program_runtime(p, capability_registry=self.registry, profile='commander_review')
                self.assertTrue(b['strict_capability_ready'], b['blockers'])
                self.assertEqual('capability_closed', b['trust_basis'])

    def test_alpine_search_attack_and_departure_offer_current_controlled_mountains(self):
        s = self.session(701310); source = self.add(s.engine, 'Alpine Guide', zone='hand')
        flash = self.add(s.engine, 'Fervor'); self.assert_closed(s, flash)
        snow = self.add(s.engine, 'Snow-Covered Mountain', zone='library')
        search_dual = self.add(s.engine, 'Taiga', zone='library', ref='search-nonbasic-mountain')
        search_bad = self.add(s.engine, 'Forest', zone='library', ref='search-forest')
        borrowed = self.add(s.engine, 'Taiga', ref='borrowed-mountain'); borrowed.owner = 'B'
        forest = self.add(s.engine, 'Forest', ref='battlefield-forest')
        foreign = self.add(s.engine, 'Mountain', seat='B'); foreign.owner = 'A'
        bounce = self.add(s.engine, 'Boomerang', zone='hand')
        for index in range(2):self.add(s.engine, 'Island', ref='bounce-blue-'+str(index))
        private = self.add(s.engine, 'Counterspell', seat='C', zone='hand')
        for card in (source, bounce): self.assert_closed(s, card)
        self.prepare(s, mana={'C': 6, 'R': 2, 'U': 2}); self.checkpoint(s)
        self.cast(s, 'A', source); self.assertEqual('semantic.choice', self.resolve(s).kind)
        result = s.act('pilot:A', {'action_id':'choose', 'choice':'apply'})
        self.assertTrue(result.ok, result.summary); self.assertEqual('semantic.search', s.state.pending_decision.kind)
        schema = s.packet('pilot:A', full=True)['decision']['ctx']['legal_actions'][0]['choice_schema']
        self.assertEqual({snow.ref, search_dual.ref}, set(schema['legal_refs']))
        self.reject(s, 'A', {'action_id':'choose', 'search_cards':[search_bad.ref]})
        result = s.act('pilot:A', {'action_id':'choose', 'search_cards':[snow.ref]})
        self.assertTrue(result.ok, result.summary); self.assertIsNone(self.resolve(s))
        self.assertEqual('battlefield', s.state.cards[snow.object_id].zone)
        self.assertTrue(s.state.cards[snow.object_id].tapped)
        self.until(s, lambda game:game.state.pending_decision is not None
                   and game.state.pending_decision.kind=='combat.attackers')
        self.reject(s, 'A', {'action_id':'attack', 'attackers':[]})
        self.attack(s, [(source, 'B')])
        self.cast(s, 'A', bounce, [source.ref]); self.resolve_top(s)
        self.assertEqual('choice.apnap', self.resolve(s).kind)
        self.assertEqual({borrowed.ref, snow.ref}, self.options(s))
        for card in (forest, foreign):
            self.reject(s, 'A', {'action_id':'choose', 'cards':[card.ref]})
        s = self.sacrifice(s, borrowed)
        self.assertIn(borrowed.object_id, s.state.players['B'].zones['graveyard'])
        self.assertEqual('battlefield', s.state.cards[snow.object_id].zone)
        self.assertEqual('hand', s.state.cards[source.object_id].zone); self.finish(s, private)

    def test_stoneseeker_token_menace_and_departure_allow_a_nontoken_powerstone(self):
        s = self.session(701320); source = self.add(s.engine, 'Horned Stoneseeker', zone='hand')
        haste = self.add(s.engine, 'Fervor'); self.assert_closed(s, haste)
        nontoken = self.add(s.engine, 'The Mightstone and Weakstone')
        wrong = self.add(s.engine, 'Powerstone Shard')
        blocker = self.add(s.engine, 'Grizzly Bears', seat='B')
        bounce = self.add(s.engine, 'Boomerang', zone='hand')
        for index in range(2):self.add(s.engine, 'Island', ref='bounce-blue-'+str(index))
        private = self.add(s.engine, 'Counterspell', seat='C', zone='hand')
        for card in (source, bounce): self.assert_closed(s, card)
        self.prepare(s, mana={'C': 6, 'R': 1, 'U': 2}); self.checkpoint(s)
        self.cast(s, 'A', source); self.assertIsNone(self.resolve(s))
        tokens = [card for card in s.state.cards.values() if card.is_token and card.zone=='battlefield'
                  and 'powerstone' in s.engine._effective_type_parts(card)[1]]
        token, = tokens; self.assertTrue(token.tapped)
        self.assertIn('menace', s.engine._combat_keywords(s.state.cards[source.object_id]))
        self.attack(s, [(source, 'B')])
        self.until(s, lambda game:game.state.pending_decision is not None
                   and game.state.pending_decision.kind=='combat.blockers')
        self.reject(s, 'B', {'action_id':'block', 'blocks':{blocker.ref:source.ref}})
        result = s.act('pilot:B', {'action_id':'block', 'blocks':{}}); self.assertTrue(result.ok, result.summary)
        self.cast(s, 'A', bounce, [source.ref]); self.resolve_top(s)
        self.assertEqual('choice.apnap', self.resolve(s).kind)
        self.assertEqual({token.ref, nontoken.ref}, self.options(s))
        self.reject(s, 'A', {'action_id':'choose', 'cards':[wrong.ref]})
        s = self.sacrifice(s, nontoken)
        self.assertEqual('graveyard', s.state.cards[nontoken.object_id].zone)
        self.assertEqual('battlefield', s.state.cards[token.object_id].zone); self.finish(s, private)

    def test_construct_uses_current_color_not_mana_cost_identity_or_ownership(self):
        s = self.session(701330); source = self.add(s.engine, "Ugin's Construct", zone='hand')
        green = self.add(s.engine, 'Dryad Arbor'); green.owner = 'B'
        other = self.add(s.engine, 'Grizzly Bears')
        devoid = self.add(s.engine, 'Benthic Infiltrator')
        artifact = self.add(s.engine, 'Ornithopter')
        land = self.add(s.engine, 'Generic Bound Plains', ref='color-identity-not-color')
        foreign = self.add(s.engine, 'Grizzly Bears', seat='B', ref='owned-not-controlled'); foreign.owner = 'A'
        private = self.add(s.engine, 'Counterspell', seat='C', zone='hand'); self.assert_closed(s, source)
        self.prepare(s, mana={'C': 6}); self.checkpoint(s); self.cast(s, 'A', source)
        self.assertEqual('choice.apnap', self.resolve(s).kind)
        self.assertEqual({green.ref, other.ref}, self.options(s))
        for card in (devoid, artifact, land, foreign, source):
            self.reject(s, 'A', {'action_id':'choose', 'cards':[card.ref]})
        s = self.sacrifice(s, green)
        self.assertIn(green.object_id, s.state.players['B'].zones['graveyard'])
        self.assertEqual('battlefield', s.state.cards[other.object_id].zone)
        self.assertEqual('battlefield', s.state.cards[source.object_id].zone); self.finish(s, private)

    def test_construct_with_only_colorless_permanents_finishes_without_choice(self):
        s = self.session(701340); source = self.add(s.engine, "Ugin's Construct", zone='hand')
        controls = [self.add(s.engine, name) for name in ('Benthic Infiltrator', 'Ornithopter')]
        private = self.add(s.engine, 'Counterspell', seat='C', zone='hand'); self.assert_closed(s, source)
        self.prepare(s, mana={'C': 4}); self.checkpoint(s); self.cast(s, 'A', source)
        self.assertIsNone(self.resolve(s))
        for card in [source, *controls]:self.assertEqual('battlefield', s.state.cards[card.object_id].zone)
        self.finish(s, private)

    def test_missing_current_color_filter_is_killed_by_the_original_action_witness(self):
        method = 'test_construct_uses_current_color_not_mana_cost_identity_or_ownership'
        witness = type(self)(method)
        import quorune.selection.apnap as owner
        original = owner.object_matches_query
        def omit(row, spec):
            return original(row, replace(spec, colors_any=())) if spec.colors_any else original(row, spec)
        with patch.object(owner, 'object_matches_query', side_effect=omit):
            with self.assertRaises(AssertionError):getattr(witness, method)()


ALL_COLORED_SACRIFICE = "Each player sacrifices all permanents they control that are one or more colors."


class AllColoredPermanentSacrificeNativeTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "all-colored-permanent-sacrifice.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures" / name for name in (
            "bound-effect-program-cards.json", "fixed-public-sacrifice-predicates-cards.json",
            "linked-hand-mana-cards.json", "temporary-target-interaction-cards.json",
            "graveyard-exile-replacement-cards.json",
        )], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Colored sacrifice witness", [
            DeckEntry("Generic Bound Commander", 1, "commander"), DeckEntry("Generic Bound Plains", 25),
        ], ["Generic Bound Commander"])

    def cast_dust(self, game):
        source = self.add(game.engine, "All Is Dust", zone="hand", ref="dust-spell")
        self.assert_closed(game, source)
        self.prepare(game, mana={"C": 7})
        self.checkpoint(game)
        self.cast(game, "A", source)

    def order(self, game, owner, cards, *, private_ref=None):
        self.assertEqual("permanent.composition_order", game.state.pending_decision.kind)
        self.assertEqual(["pilot:" + owner], game.pending_principals())
        packet = game.packet("pilot:" + owner, full=True)["decision"]
        refs = [card.ref for card in cards]
        self.assertEqual(set(refs), {row["id"] for row in packet["ctx"]["cards"]})
        if private_ref is not None:
            self.assertNotIn(private_ref, str(packet))
        before = authoritative_state_hash(game.state)
        command = {"action_id": "order", "cards": refs}
        self.assertFalse(game.act("pilot:D", command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        self.assertFalse(game.act("pilot:" + owner, {**command, "cards": [refs[0]] * len(refs)}).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        result = game.act("pilot:" + owner, command)
        self.assertTrue(result.ok, result.summary)

    def test_all_is_dust_uses_live_colors_and_each_actual_owner_order(self):
        game = self.session(7012101)
        engine = game.engine
        permanent_a = self.add(engine, "Generic Bound Body", ref="dust-a-creature")
        enamel = self.add(engine, "Generic Bound Counter Doubler", ref="dust-a-enchantment")
        borrowed = self.add(engine, "Dryad Arbor", seat="B", ref="dust-borrowed-land")
        engine.change_control(borrowed.object_id, "A", reason="borrowed land fixture")
        permanent_b = self.add(engine, "Generic Bound Body", seat="B", ref="dust-b-creature")
        washed = self.add(engine, "Generic Bound Body", ref="dust-now-colorless")
        moonlace = self.add(engine, "Moonlace", seat="B", zone="hand", ref="dust-live-color-change")
        self.assert_closed(game, moonlace)
        game.state.players["B"].mana_pool["U"] = 1
        plain = self.add(engine, "Generic Bound Plains", seat="C", ref="dust-identity-only")
        devoid = self.add(engine, "Benthic Infiltrator", seat="C", ref="dust-devoid")
        phased = self.add(engine, "Generic Bound Body", seat="C", ref="dust-phased")
        phased.phased_out = True
        private = self.add(engine, "Counterspell", seat="C", zone="hand", ref="dust-private")
        token_ref, = engine.create_token("D", name="Colored shield token", characteristics={
            "type_line": "Creature", "colors": ["W", "U"], "power": "1", "toughness": "1",
            "keywords": ["Indestructible"],
        })
        token = engine._resolve_object("D", token_ref, zones={"battlefield"})
        self.assertIn("indestructible", {keyword.casefold() for keyword in engine._combat_keywords(token)})
        self.cast_dust(game)
        self.cast(game, "B", moonlace, [washed.ref])
        self.resolve_top(game)
        pending = self.resolve(game)
        self.assertIsNotNone(pending)
        self.assertEqual("permanent.composition_order", pending.kind)
        held = (permanent_a, enamel, permanent_b, borrowed, token)
        self.assertTrue(all(game.state.cards[card.object_id].zone == "battlefield" for card in held))
        self.assertFalse(game.engine._effective_card_data(game.state.cards[washed.object_id])["colors"])
        game = self.replay(game, load=True)
        self.order(game, "A", (enamel, permanent_a), private_ref=private.ref)
        self.assertTrue(all(game.state.cards[card.object_id].zone == "battlefield" for card in held))
        self.order(game, "B", (permanent_b, borrowed), private_ref=private.ref)
        self.assertIsNone(self.resolve(game))
        for owner, cards in (("A", (enamel, permanent_a)), ("B", (permanent_b, borrowed))):
            expected = [card.object_id for card in cards]
            ordered = [oid for oid in game.state.players[owner].zones["graveyard"] if oid in expected]
            self.assertEqual(expected, ordered)
        for card in (washed, plain, devoid, phased):
            self.assertEqual("battlefield", game.state.cards[card.object_id].zone)
        self.assertNotIn(token.object_id, game.state.players["D"].zones["battlefield"])
        for seat in "ABD":
            self.assertNotIn(private.ref, str(game.packet("pilot:" + seat, full=True)))
        self.replay(game, load=True)

    def test_all_is_dust_orders_only_actual_graveyard_arrivals_after_replacement(self):
        game = self.session(7012103)
        engine = game.engine
        replacement = self.add(engine, "Dauthi Voidwalker", seat="B", ref="dust-destination")
        self.assert_closed(game, replacement)
        arrived = self.add(engine, "Generic Bound Body", seat="B", ref="dust-normal")
        redirected = tuple(self.add(engine, "Generic Bound Body", ref=f"dust-redirection-{index}") for index in range(2))
        self.cast_dust(game)
        pending = self.resolve(game)
        self.assertIsNotNone(pending)
        self.assertEqual("permanent.composition_order", pending.kind)
        self.assertEqual(["pilot:B"], game.pending_principals())
        self.assertTrue(all(game.state.cards[card.object_id].zone == "battlefield" for card in redirected))
        game = self.replay(game, load=True)
        self.order(game, "B", (arrived, replacement))
        self.assertIsNone(self.resolve(game))
        for card in redirected:
            current = game.state.cards[card.object_id]
            self.assertEqual(("exile", 1), (current.zone, current.counters["void"]))
        self.assertEqual([arrived.object_id, replacement.object_id], game.state.players["B"].zones["graveyard"])
        self.replay(game, load=True)

    def test_all_is_dust_empty_color_set_finishes_without_a_choice(self):
        game = self.session(7012106)
        neutral = self.add(game.engine, "Ornithopter", ref="dust-uncolored-permanent")
        land = self.add(game.engine, "Generic Bound Plains", seat="B", ref="dust-uncolored-land")
        self.cast_dust(game)
        self.assertIsNone(self.resolve(game))
        self.assertTrue(all(game.state.cards[card.object_id].zone == "battlefield" for card in (neutral, land)))
        self.replay(game, load=True)

    def test_all_is_dust_closed_shape_and_runtime_dependency_withdrawal(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        from quorune.rules.colored_permanent_sacrifice import CAPABILITY
        from test_fixed_affected_player_sacrifices import synthetic_record

        original = synthetic_record(
            ALL_COLORED_SACRIFICE, name="All colored permanent fixture", type_line="Kindred Sorcery — Eldrazi",
            oracle_id_suffix=7012108,
        )
        ir = compile_oracle_card(original, capability_registry=self.registry)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        node, = ir.faces[0].nodes
        self.assertEqual((0, len(ALL_COLORED_SACRIFICE)), (node.span.start, node.span.end))
        self.assertIn(CAPABILITY, node.capability_dependencies)
        effect, = node.effects
        for patch_values in ({"all_colored_permanent_sacrifice": True}, {"players": "opponents"},
                             {"predicate": {**effect["predicate"], "colors_any": ["R"]}}, {"then": "destroy"}):
            self.assertEqual((), fixed_affected_player_sacrifice_node_capabilities(
                effects=({**effect, **patch_values},), mechanic_ids=node.mechanics, target_schema=None,
            ))
        for text in (
            ALL_COLORED_SACRIFICE.replace("Each player", "Each opponent"),
            ALL_COLORED_SACRIFICE.replace("one or more colors", "exactly one color"),
            ALL_COLORED_SACRIFICE.replace("sacrifices", "destroys"),
        ):
            modified = compile_oracle_card(replace(original, oracle_text=text), capability_registry=self.registry)
            self.assertNotIn(CAPABILITY, {cap for face in modified.faces for row in face.nodes for cap in row.capability_dependencies})
        with patch("quorune.compiler.affected_player_sacrifice_templates.AllColoredPermanentSacrificeTemplate", return_value=None):
            self.assertNotEqual("exact", compile_oracle_card(original, capability_registry=self.registry).status)
        game = self.session(7012108)
        spell = self.add(game.engine, "All Is Dust", zone="hand")
        program, = game.engine.semantics.programs_for_oracle(spell.oracle_id)
        self.assertTrue(game.engine.semantic_program_is_current_trusted(program))
        deps = tuple(value for value in program.capability_dependencies if value != CAPABILITY)
        mutant = replace(program, capability_dependencies=deps,
                         capability_closure=self.registry.closure(deps, profile="commander_review").to_dict())
        binding = bind_semantic_program_runtime(mutant, capability_registry=self.registry, profile="commander_review")
        self.assertFalse(binding["strict"])
        self.assertIn(f"capability:undeclared_runtime_dependency:{CAPABILITY}", binding["blockers"])
