from __future__ import annotations

"""CR205.1b and302.6: static land creatures retain land and control history."""

from pathlib import Path
import tempfile
import unittest
from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
import test_expanded_basic_type_setting as controls
from quorune.rules.evidence_storage import read_evidence_document


class StaticLandAnimationTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=controls.ExpandedBasicTypeSettingTests.session
    add=controls.ExpandedBasicTypeSettingTests.add
    prepare=controls.ExpandedBasicTypeSettingTests.prepare
    checkpoint=controls.ExpandedBasicTypeSettingTests.checkpoint
    resolve=controls.ExpandedBasicTypeSettingTests.resolve
    replay=controls.ExpandedBasicTypeSettingTests.replay
    binding=controls.ExpandedBasicTypeSettingTests.binding
    priority=controls.ExpandedBasicTypeSettingTests.priority
    cast=controls.ExpandedBasicTypeSettingTests.cast
    offered_cast=controls.ExpandedBasicTypeSettingTests.offered_cast
    private_replay=controls.ExpandedBasicTypeSettingTests.private_replay
    offered=controls.ExpandedBasicTypeSettingTests.offered
    advance=controls.ExpandedBasicTypeSettingTests.advance

    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();path=Path(cls.temp.name)/'static-land-animation.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/attached-protection-cards.json',
            ROOT/'tests/fixtures/static-land-animation-cards.json',ROOT/'tests/fixtures/fixed-basic-type-setting-cards.json',
            ROOT/'tests/fixtures/attached-occurrence-cards.json',ROOT/'tests/fixtures/expanded-basic-type-setting-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Static land animation',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temp.cleanup()

    def test_derived_animation_requires_its_independent_amount_and_target_authority(self):
        from dataclasses import replace
        from quorune.card_programs.binding import bind_semantic_program_runtime
        from quorune.oracle_ir import compile_oracle_card
        from quorune.resolution_characteristic_model import DERIVED_CHARACTERISTIC_CAPABILITY

        session = self.session(844001)
        for name in ("Karn's Touch", "Karn, Silver Golem", "Sydri, Galvanic Genius", "Toymaker", "Fractalize"):
            with self.subTest(card=name):
                source = self.add(session.engine, name, zone="hand")
                ir = compile_oracle_card(self.db.lookup(name), capability_registry=self.registry,
                                         capability_profile="commander_review")
                self.assertEqual("exact", ir.status, ir.material_residuals)
                binding = self.binding(session, source)
                self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
                program, = (entry for entry in session.engine.semantics.programs_for_oracle(source.oracle_id)
                            if DERIVED_CHARACTERISTIC_CAPABILITY in entry.capability_dependencies)
                self.assertEqual("trusted", program.trust_level)
                deps = tuple(cap for cap in program.capability_dependencies if cap != DERIVED_CHARACTERISTIC_CAPABILITY)
                omitted = replace(program, capability_dependencies=deps,
                                  capability_closure=self.registry.closure(deps, profile="commander_review").to_dict())
                wrong = replace(program, target_schema={**program.target_schema, "types_any": ["land"]})
                for changed in (omitted, wrong):
                    bound = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile="commander_review")
                    self.assertFalse(bound["strict"], bound)
                self.assertIn("capability:undeclared_runtime_dependency:" + DERIVED_CHARACTERISTIC_CAPABILITY,
                              bind_semantic_program_runtime(omitted, capability_registry=self.registry,
                                                            profile="commander_review")["blockers"])

    def test_actual_karns_touch_binds_mana_value_and_keeps_the_artifacts_abilities(self):
        from quorune.continuous_effect_state import expire_end_of_turn_continuous_effects
        from quorune.record import authoritative_state_hash

        session = self.session(844002)
        spell = self.add(session.engine, "Karn's Touch", zone="hand")
        artifact = self.add(session.engine, "Sol Ring", seat="B")
        creature = self.add(session.engine, "Memnite", seat="D")
        private = self.add(session.engine, "Generic Bound Growth", seat="C", zone="hand")
        artifact.acquired_control_turn_count = -1
        artifact.counters["+1/+1"] = 1
        self.prepare(session, mana={"U": 2})
        self.checkpoint(session)
        action, = (entry for entry in self.offered(session, "A") if entry["id"] == "cast:" + spell.ref)
        self.assertIn(artifact.ref, action["target_schema"]["legal_refs"])
        self.assertNotIn(creature.ref, action["target_schema"]["legal_refs"])
        command = {"action_id": action["id"], "targets": [artifact.ref], "pay": "auto"}
        before = authoritative_state_hash(session.state)
        self.assertFalse(session.act("pilot:C", command).ok)
        self.assertFalse(session.act("pilot:A", {**command, "targets": [creature.ref]}).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        result = session.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(session))
        current = session.state.cards[artifact.object_id]
        data = session.engine._effective_card_data(current)
        self.assertEqual({"artifact", "creature"}, session.engine._type_parts(data["type_line"])[0])
        self.assertEqual((2, 2), tuple(session.engine._numeric_stat(current.object_id, key)
                                      for key in ("power", "toughness")))
        setters = [operation.value for effect in session.state.continuous_effects
                   for operation in getattr(effect, "operations", ()) if operation.op == "set_power_toughness"]
        self.assertEqual([(1, 1)], setters)
        self.priority(session, "B")
        mana, = (entry for entry in self.offered(session, "B") if artifact.ref in entry["id"])
        result = session.act("pilot:B", {"action_id": mana["id"]})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(2, session.state.players["B"].mana_pool["C"])
        self.private_replay(session, private)
        expire_end_of_turn_continuous_effects(session.state)
        restored = session.engine._effective_card_data(current)
        self.assertEqual({"artifact"}, session.engine._type_parts(restored["type_line"])[0])

    def test_actual_karns_touch_zero_value_artifact_land_dies_to_toughness(self):
        session = self.session(844003)
        spell = self.add(session.engine, "Karn's Touch", zone="hand")
        land = self.add(session.engine, "Darksteel Citadel", seat="B")
        self.prepare(session, mana={"U": 2})
        self.checkpoint(session)
        self.offered_cast(session, spell, {"U": 2}, [land.ref])
        self.assertIsNone(self.resolve(session))
        self.assertEqual("graveyard", session.state.cards[land.object_id].zone)
        self.replay(session, load=True)

    def test_actual_animation_reads_the_copy_value_of_sculpting_steel(self):
        session = self.session(844007)
        spell = self.add(session.engine, "Karn's Touch", zone="hand")
        steel = self.add(session.engine, "Sculpting Steel", zone="hand")
        original = self.add(session.engine, "Sol Ring", seat="B")
        self.assertTrue(self.binding(session, steel)["strict_capability_ready"])
        self.prepare(session, mana={"C": 3, "U": 2})
        self.checkpoint(session)
        self.offered_cast(session, steel, {"C": 3})
        self.assertEqual("replacement.order", self.resolve(session).kind)
        options = session.packet("pilot:A", full=True)["decision"]["ctx"]["options"]
        selected, = (row for row in options if original.ref in row["id"] and not row["id"].startswith("decline:"))
        result = session.act("pilot:A", {"action_id": "choose", "replacement": selected["id"]})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(session))
        current = session.state.cards[steel.object_id]
        self.assertEqual(3, self.db.lookup("Sculpting Steel").mana_value)
        self.assertEqual(1, session.engine._effective_card_data(current)["mana_value"])
        self.offered_cast(session, spell, {"U": 2}, [current.ref])
        self.assertIsNone(self.resolve(session))
        self.assertEqual((1, 1), tuple(session.engine._numeric_stat(current.object_id, key)
                                      for key in ("power", "toughness")))
        self.replay(session, load=True)

    def test_actual_toymaker_pays_tap_and_discard_before_animating_a_foreign_artifact(self):
        from quorune.record import authoritative_state_hash

        session = self.session(844004)
        source = self.add(session.engine, "Toymaker")
        source.acquired_control_turn_count = -1
        discard = self.add(session.engine, "Generic Bound Growth", zone="hand", ref="toymaker-price")
        private = self.add(session.engine, "Generic Bound Growth", seat="C", zone="hand", ref="toymaker-private")
        artifact = self.add(session.engine, "Sol Ring", seat="D")
        creature = self.add(session.engine, "Memnite", seat="D")
        self.prepare(session, mana={"C": 1})
        self.checkpoint(session)
        offer, = (entry for entry in self.offered(session, "A") if entry["id"].startswith(f"activate:{source.ref}:"))
        self.assertNotIn(discard.ref, str(session.packet("pilot:D", full=True)))
        command = {"action_id": offer["id"], "targets": [artifact.ref], "cost_cards": [discard.ref], "pay": "auto"}
        before = authoritative_state_hash(session.state)
        self.assertFalse(session.act("pilot:A", {**command, "targets": [creature.ref]}).ok)
        self.assertFalse(session.act("pilot:A", {**command, "cost_cards": [private.ref]}).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        accepted = session.act("pilot:A", command)
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertTrue(session.state.cards[source.object_id].tapped)
        self.assertEqual("graveyard", session.state.cards[discard.object_id].zone)
        self.assertEqual("D", session.state.cards[artifact.object_id].controller)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(1, session.engine._numeric_stat(artifact.object_id, "power"))
        self.private_replay(session, private)

    def test_actual_sydri_offers_her_second_ability_after_animation(self):
        session = self.session(844008)
        sydri = self.add(session.engine, "Sydri, Galvanic Genius")
        artifact = self.add(session.engine, "Sol Ring", seat="B")
        self.prepare(session, mana={"U": 1, "W": 1, "B": 1})
        self.checkpoint(session)
        animation, = (entry for entry in self.offered(session, "A") if entry["id"].startswith(f"activate:{sydri.ref}:"))
        accepted = session.act("pilot:A", {"action_id": animation["id"], "targets": [artifact.ref], "pay": "auto"})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertIsNone(self.resolve(session))
        self.priority(session, "A")
        grant, = (entry for entry in self.offered(session, "A") if entry["id"].startswith(f"activate:{sydri.ref}:"))
        self.assertNotEqual(animation["id"], grant["id"])
        accepted = session.act("pilot:A", {"action_id": grant["id"], "targets": [artifact.ref], "pay": "auto"})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertIsNone(self.resolve(session))
        data = session.engine._effective_card_data(session.state.cards[artifact.object_id])
        self.assertTrue({"deathtouch", "lifelink"} <= {name.casefold() for name in data["keywords"]})
        self.replay(session, load=True)

    def test_actual_fractalize_sets_announced_x_plus_one_and_keeps_the_artifact_type(self):
        from quorune.record import authoritative_state_hash

        session = self.session(844005)
        spell = self.add(session.engine, "Fractalize", zone="hand")
        creature = self.add(session.engine, "Memnite", seat="B")
        creature.counters["+1/+1"] = 1
        artifact = self.add(session.engine, "Sol Ring", seat="D")
        self.prepare(session, mana={"U": 1, "C": 2})
        self.checkpoint(session)
        action, = (entry for entry in self.offered(session, "A") if entry["id"] == "cast:" + spell.ref)
        command = {"action_id": action["id"], "targets": [creature.ref], "x": 2, "pay": "auto"}
        before = authoritative_state_hash(session.state)
        self.assertFalse(session.act("pilot:A", {**command, "targets": [artifact.ref]}).ok)
        self.assertFalse(session.act("pilot:A", {**command, "x": 3}).ok)
        for malformed in (True, 1.5, "1"):
            rejected = session.act("pilot:A", {**command, "x": malformed})
            self.assertFalse(rejected.ok, rejected.summary)
        self.assertEqual(before, authoritative_state_hash(session.state))
        result = session.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(2, session.state.stack[-1].x_value)
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        current = session.state.cards[creature.object_id]
        data = session.engine._effective_card_data(current)
        self.assertEqual(({"artifact", "creature"}, {"fractal"}), session.engine._type_parts(data["type_line"])[:2])
        self.assertEqual({"U", "G"}, set(data["colors"]))
        self.assertEqual((4, 4), tuple(session.engine._numeric_stat(current.object_id, key)
                                      for key in ("power", "toughness")))
        self.replay(session, load=True)

    def test_derived_animation_checks_noncreature_status_again_after_a_response(self):
        session = self.session(844006)
        first = self.add(session.engine, "Karn's Touch", zone="hand", ref="first-animation")
        response = self.add(session.engine, "Karn's Touch", seat="B", zone="hand", ref="response-animation")
        artifact = self.add(session.engine, "Sol Ring", seat="D")
        session.state.players["B"].mana_pool["U"] = 2
        self.prepare(session, mana={"U": 2})
        self.checkpoint(session)
        self.offered_cast(session, first, {"U": 2}, [artifact.ref])
        self.priority(session, "B")
        action, = (entry for entry in self.offered(session, "B") if entry["id"] == "cast:" + response.ref)
        result = session.act("pilot:B", {"action_id": action["id"], "targets": [artifact.ref], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(session))
        effects = [effect for effect in session.state.continuous_effects
                   if getattr(effect, "sublayer", None) == "7b"]
        self.assertEqual(1, len(effects))
        self.assertEqual((1, 1), effects[0].operations[0].value)
        self.replay(session, load=True)

    def test_omitting_actual_mana_value_is_detected_by_native_animation(self):
        from unittest.mock import patch

        with patch("quorune.public_quantity_reductions.public_mana_value", return_value=5):
            with self.assertRaises(AssertionError):
                self.test_actual_karns_touch_binds_mana_value_and_keeps_the_artifacts_abilities()

    def test_complete_living_plane_and_natures_revolt_originals_register_before_actions(self):
        session=self.session(752100)
        for name in ('Living Plane',"Nature's Revolt"):
            source=self.add(session.engine,name,zone='hand')
            self.assertTrue(self.binding(session,source)['strict_capability_ready'],'Complete original '+name+' must admit')

    def test_complete_living_lands_and_kormus_bell_originals_register_before_actions(self):
        session=self.session(752101)
        for name in ('Living Lands','Kormus Bell'):
            source=self.add(session.engine,name,zone='hand')
            self.assertTrue(self.binding(session,source)['strict_capability_ready'],'Complete original '+name+' must admit')

    def test_actual_living_plane_animates_all_seats_retains_land_mana_and_restores(self):
        session=self.session(752102);source=self.add(session.engine,'Living Plane',zone='hand')
        land=self.add(session.engine,'Darksteel Citadel',seat='B');plain=self.add(session.engine,'Generic Bound Plains',seat='D',ref='other-seat-plain')
        destroy=self.add(session.engine,'Naturalize',zone='hand');private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        land.acquired_control_turn_count=-1;plain.acquired_control_turn_count=-1
        self.assertTrue(self.binding(session,source)['strict_capability_ready']);self.prepare(session,mana={'G':3,'C':3});self.checkpoint(session)
        self.offered_cast(session,source,{'G':2,'C':2});self.assertIsNone(self.resolve(session))
        for target in (land,plain):
            data=session.engine._effective_card_data(session.state.cards[target.object_id]);kinds,_,_=session.engine._type_parts(data['type_line'])
            self.assertTrue({'land','creature'}<=kinds);self.assertEqual((1,1),(session.engine._numeric_stat(target.object_id,'power'),session.engine._numeric_stat(target.object_id,'toughness')))
        self.assertIn('artifact',session.engine._type_parts(session.engine._effective_card_data(session.state.cards[land.object_id])['type_line'])[0])
        self.assertIn('indestructible',{x.casefold() for x in session.engine._effective_card_data(session.state.cards[land.object_id])['keywords']})
        self.priority(session,'B');mana=[a for a in self.offered(session,'B') if a.get('card')==land.ref or land.ref in a['id']]
        self.assertEqual(1,len(mana));result=session.act('pilot:B',{'action_id':mana[0]['id']});self.assertTrue(result.ok,result.summary)
        self.assertEqual(1,session.state.players['B'].mana_pool.get('C',0))
        self.offered_cast(session,destroy,{'G':1,'C':1},[source.ref]);self.assertIsNone(self.resolve(session))
        restored=session.engine._effective_card_data(session.state.cards[land.object_id]);self.assertEqual({'artifact','land'},session.engine._type_parts(restored['type_line'])[0])
        self.private_replay(session,private)

    def test_actual_natures_revolt_sets_two_two_and_counters_modify_after_base(self):
        session=self.session(752103);source=self.add(session.engine,"Nature's Revolt",zone='hand')
        land=self.add(session.engine,'Generic Bound Plains',ref='counter-land');land.counters['+1/+1']=1
        foreign=self.add(session.engine,'Darksteel Citadel',seat='B');private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.assertTrue(self.binding(session,source)['strict_capability_ready']);self.prepare(session,mana={'G':2,'C':3});self.checkpoint(session)
        self.offered_cast(session,source,{'G':2,'C':3});self.assertIsNone(self.resolve(session))
        self.assertEqual((3,3),(session.engine._numeric_stat(land.object_id,'power'),session.engine._numeric_stat(land.object_id,'toughness')))
        self.assertEqual((2,2),(session.engine._numeric_stat(foreign.object_id,'power'),session.engine._numeric_stat(foreign.object_id,'toughness')))
        self.assertEqual({'+1/+1':1},session.state.cards[land.object_id].counters);self.private_replay(session,private)

    def test_actual_kormus_bell_sets_swamps_black_and_leaves_other_lands_unchanged(self):
        session=self.session(752104);source=self.add(session.engine,'Kormus Bell',zone='hand')
        swamp=self.add(session.engine,'Swamp',seat='B');island=self.add(session.engine,'Snow-Covered Island',seat='D')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.assertTrue(self.binding(session,source)['strict_capability_ready']);self.prepare(session,mana={'C':4});self.checkpoint(session)
        self.offered_cast(session,source,{'C':4});self.assertIsNone(self.resolve(session))
        data=session.engine._effective_card_data(session.state.cards[swamp.object_id]);self.assertEqual({'B'},set(data['colors']))
        self.assertEqual(({'land','creature'},{'swamp'},{'basic'}),session.engine._type_parts(data['type_line']))
        self.assertEqual((1,1),(session.engine._numeric_stat(swamp.object_id,'power'),session.engine._numeric_stat(swamp.object_id,'toughness')))
        self.assertEqual({'land'},session.engine._type_parts(session.engine._effective_card_data(session.state.cards[island.object_id])['type_line'])[0]);self.private_replay(session,private)

    def test_actual_living_lands_selects_current_forests_and_preserves_mana(self):
        session=self.session(752105);source=self.add(session.engine,'Living Lands',zone='hand')
        forest=self.add(session.engine,'Forest',seat='B');island=self.add(session.engine,'Snow-Covered Island',seat='D')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.assertTrue(self.binding(session,source)['strict_capability_ready']);self.prepare(session,mana={'G':1,'C':3});self.checkpoint(session)
        self.offered_cast(session,source,{'G':1,'C':3});self.assertIsNone(self.resolve(session))
        self.assertEqual({'land','creature'},session.engine._type_parts(session.engine._effective_card_data(session.state.cards[forest.object_id])['type_line'])[0])
        self.assertEqual({'land'},session.engine._type_parts(session.engine._effective_card_data(session.state.cards[island.object_id])['type_line'])[0])
        self.assertEqual({'intrinsic_forest'},{a.ability_id for a in session.engine._activated_abilities(session.state.cards[forest.object_id])});self.private_replay(session,private)

    def test_old_and_new_animated_land_mana_actions_use_continuous_control_age(self):
        from quorune.record import authoritative_state_hash
        session=self.session(752106);source=self.add(session.engine,"Nature's Revolt",zone='hand')
        old=self.add(session.engine,'Generic Bound Plains',ref='old-animated-land');old.acquired_control_turn_count=-1
        fresh=self.add(session.engine,'Generic Bound Plains',zone='hand',ref='fresh-animated-land')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand');self.prepare(session,mana={'G':2,'C':3});self.checkpoint(session)
        self.offered_cast(session,source,{'G':2,'C':3});self.assertIsNone(self.resolve(session));self.priority(session,'A')
        actions=self.offered(session,'A');old_actions=[a for a in actions if a.get('card')==old.ref or old.ref in a['id']]
        self.assertEqual(1,len(old_actions));result=session.act('pilot:A',{'action_id':old_actions[0]['id']});self.assertTrue(result.ok,result.summary)
        self.priority(session,'A');plays=[a for a in self.offered(session,'A') if a['id'].startswith('play-land:') and fresh.ref in a['id']]
        self.assertEqual(1,len(plays));result=session.act('pilot:A',{'action_id':plays[0]['id']});self.assertTrue(result.ok,result.summary)
        self.assertEqual({'land','creature'},session.engine._type_parts(session.engine._effective_card_data(session.state.cards[fresh.object_id])['type_line'])[0])
        self.priority(session,'A');self.assertFalse(any(a.get('card')==fresh.ref or fresh.ref in a['id'] for a in self.offered(session,'A')))
        # The equivalent offered basic-land action ID must reject for the fresh
        # creature land through the same mana owner, without tapping it.
        command={'action_id':old_actions[0]['id'].replace(old.ref,fresh.ref)};before=authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:A',command).ok);self.assertEqual(before,authoritative_state_hash(session.state));self.assertFalse(session.state.cards[fresh.object_id].tapped)
        self.private_replay(session,private)

    def test_kormus_membership_tracks_urborg_departure_and_restores_non_swamp(self):
        session=self.session(752107);bell=self.add(session.engine,'Kormus Bell',zone='hand')
        urborg=self.add(session.engine,'Urborg, Tomb of Yawgmoth');island=self.add(session.engine,'Snow-Covered Island',seat='B')
        bounce=self.add(session.engine,'Boomerang',zone='hand');private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(session,mana={'C':4,'U':2});self.checkpoint(session);self.offered_cast(session,bell,{'C':4});self.assertIsNone(self.resolve(session))
        data=session.engine._effective_card_data(session.state.cards[island.object_id]);self.assertEqual({'island','swamp'},session.engine._type_parts(data['type_line'])[1])
        self.assertIn('creature',session.engine._type_parts(data['type_line'])[0]);self.assertEqual({'B'},set(data['colors']))
        self.offered_cast(session,bounce,{'U':2},[urborg.ref]);self.assertIsNone(self.resolve(session))
        data=session.engine._effective_card_data(session.state.cards[island.object_id]);self.assertEqual({'land'},session.engine._type_parts(data['type_line'])[0])
        self.assertEqual({'island'},session.engine._type_parts(data['type_line'])[1]);self.assertFalse(data['colors']);self.private_replay(session,private)

    def test_animation_descriptor_declares_type_layer_participation_and_closed_grammar(self):
        from quorune.compiler.static_land_animation import static_land_animation_handler
        from quorune.semantic_runtime.continuous_components import default_continuous_effect_component_registry
        from quorune.continuous_effect_model import Layer
        descriptor=static_land_animation_handler('All lands are 1/1 creatures that are still lands.')[1]
        self.assertEqual(Layer.TYPE,default_continuous_effect_component_registry().source_component_layer(descriptor))
        for text in ('All lands are X/X creatures that are still lands.','All lands are 1/1 creatures until end of turn. They are still lands.',
                'All lands are 1/1 creatures that are still lands. Draw a card.','All Deserts are 1/1 creatures that are still lands.'):
            self.assertIsNone(static_land_animation_handler(text),text)

    def test_living_plane_keeps_world_rule_when_a_new_original_resolves(self):
        session=self.session(752108);first=self.add(session.engine,'Living Plane',zone='hand',ref='first-world-animation')
        second=self.add(session.engine,'Living Plane',zone='hand',ref='new-world-animation');land=self.add(session.engine,'Generic Bound Plains')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand');self.prepare(session,mana={'G':4,'C':4});self.checkpoint(session)
        self.offered_cast(session,first,{'G':2,'C':2});self.assertIsNone(self.resolve(session))
        self.assertEqual('battlefield',session.state.cards[first.object_id].zone)
        self.offered_cast(session,second,{'G':2,'C':2});self.assertIsNone(self.resolve(session))
        self.assertEqual('graveyard',session.state.cards[first.object_id].zone);self.assertEqual('battlefield',session.state.cards[second.object_id].zone)
        self.assertIn('world',session.engine._type_parts(session.engine._effective_card_data(session.state.cards[second.object_id])['type_line'])[2])
        self.assertEqual((1,1),(session.engine._numeric_stat(land.object_id,'power'),session.engine._numeric_stat(land.object_id,'toughness')))
        self.private_replay(session,private)

    def test_song_removes_kormus_printed_animation_and_source_removal_restores_it(self):
        session=self.session(752109);bell=self.add(session.engine,'Kormus Bell',zone='hand');swamp=self.add(session.engine,'Swamp',seat='B')
        song=self.add(session.engine,'Song of the Dryads',zone='hand');destroy=self.add(session.engine,'Naturalize',zone='hand')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand');self.prepare(session,mana={'C':7,'G':2});self.checkpoint(session)
        self.offered_cast(session,bell,{'C':4});self.assertIsNone(self.resolve(session));self.assertIn('creature',session.engine._type_parts(session.engine._effective_card_data(session.state.cards[swamp.object_id])['type_line'])[0])
        self.offered_cast(session,song,{'G':1,'C':2},[bell.ref]);self.assertIsNone(self.resolve(session))
        self.assertEqual({'land'},session.engine._type_parts(session.engine._effective_card_data(session.state.cards[swamp.object_id])['type_line'])[0])
        self.offered_cast(session,destroy,{'G':1,'C':1},[song.ref]);self.assertIsNone(self.resolve(session))
        self.assertIn('creature',session.engine._type_parts(session.engine._effective_card_data(session.state.cards[swamp.object_id])['type_line'])[0]);self.private_replay(session,private)

    def test_animation_setting_owner_is_an_independent_runtime_minimum(self):
        from dataclasses import replace
        from quorune.card_programs.binding import bind_semantic_program_runtime
        for name in ('Living Plane',"Nature's Revolt",'Living Lands','Kormus Bell'):
            session=self.session(752110);source=self.add(session.engine,name,zone='hand')
            programs=[p for p in session.engine.semantics.programs_for_oracle(source.oracle_id)
                if any(d.get('handler_id')=='continuous.characteristics.fixed-public-setting.v1' for d in p.handlers)]
            self.assertEqual(1,len(programs));program=programs[0];cap='continuous.characteristics.fixed_public_setting'
            deps=tuple(c for c in program.capability_dependencies if c!=cap)
            stripped=replace(program,capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict() if deps else None)
            binding=bind_semantic_program_runtime(stripped,capability_registry=self.registry,profile='commander_review')
            self.assertFalse(binding['strict'],name);self.assertIn('capability:undeclared_runtime_dependency:'+cap,binding['blockers'])

    def test_verified_setting_withdrawal_blocks_all_four_complete_originals(self):
        import json
        from copy import deepcopy
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from scripts.update_capability_evidence import build_index,discover_tests
        from quorune.oracle_ir import compile_oracle_card
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'));changed=deepcopy(raw)
        cap='continuous.characteristics.fixed_public_setting';next(r for r in changed['capabilities'] if r['id']==cap).update(status='blocked',blockers=['withdrawn static land animation authority'])
        registry=CapabilityRegistry(changed);decl=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json')
        index=build_index(registry_value=changed,declaration_source=decl,discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
        for name in ('Living Plane',"Nature's Revolt",'Living Lands','Kormus Bell'):
            ir=compile_oracle_card(self.db.lookup(name),capability_registry=registry,capability_profile='commander_review')
            self.assertNotEqual('exact',ir.status,name);blockers=[b for r in ir.material_residuals for b in r.blockers]
            self.assertTrue(any('status:'+cap+':blocked' in b for b in blockers),(name,blockers));self.assertFalse(any('evidence_index:unverified' in b for b in blockers))

    def test_animation_query_and_descriptor_layers_reject_unsupported_shapes(self):
        from copy import deepcopy
        from quorune.compiler.static_land_animation import static_land_animation_handler
        from quorune.semantic_runtime.fixed_characteristic_settings import FixedCharacteristicSettingsHandler
        from quorune.object_predicate import ObjectQuerySpec
        from quorune.continuous_effect_model import Layer
        handler=FixedCharacteristicSettingsHandler();base=static_land_animation_handler('All Swamps are 1/1 black creatures that are still lands.')[1]
        for predicate in (ObjectQuerySpec(types_all=('creature',),subtypes_all=('swamp',)),
                ObjectQuerySpec(types_all=('land',),subtypes_all=('desert',)),ObjectQuerySpec(types_all=('land',),subtypes_all=('swamp',),known_to_actor=False)):
            changed=deepcopy(base);changed['target']['predicate']=predicate.to_dict()
            with self.assertRaises(ValueError):handler.validate(changed)
        changed=deepcopy(base);changed['schema_version']=True
        with self.assertRaises(ValueError):handler.descriptor_source_component_layer(changed)
        pure=deepcopy(base);pure['modifier']['type_operations']=[];pure['modifier']['color_operations']=[]
        self.assertEqual(Layer.ABILITY,handler.descriptor_source_component_layer(pure))

    def test_compiler_lowerer_and_first_layer_omissions_are_killed(self):
        from unittest.mock import patch
        from quorune.continuous_effect_model import Layer
        cases=(('quorune.compiler.static_land_animation.static_land_animation_handler',lambda *a:None,
                'test_actual_natures_revolt_sets_two_two_and_counters_modify_after_base'),
            ('quorune.semantic_runtime.fixed_characteristic_settings.FixedCharacteristicSettingsHandler.lower',lambda *a:(),
                'test_actual_natures_revolt_sets_two_two_and_counters_modify_after_base'),
            ('quorune.semantic_runtime.fixed_characteristic_settings.FixedCharacteristicSettingsHandler.descriptor_source_component_layer',lambda *a:Layer.ABILITY,
                'test_animation_descriptor_declares_type_layer_participation_and_closed_grammar'))
        for target,replacement,method in cases:
            with patch(target,replacement):
                with self.assertRaises(AssertionError):getattr(type(self)(method),method)()
