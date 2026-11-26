from __future__ import annotations

from pathlib import Path
from dataclasses import replace
import json
import tempfile
import unittest
from unittest import mock
from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.capabilities import CapabilityRegistry
from quorune.card_programs.adapters import compile_best_available_card_program
from quorune.card_programs import bind_card_program_runtime
from quorune.semantics import SemanticRegistry
from quorune.record import authoritative_state_hash
from quorune.compiler.token_copy_templates import _fixed_exception
from scripts.build_test_database import build_fixture_database
import test_bound_effect_programs as actions
import test_self_public_zone_casting as zone_actions

FIXTURE=ROOT/'tests/fixtures/fixed-copy-type-exception-cards.json'


class FixedCopyTypeExceptionCompilerTests(unittest.TestCase):
    def test_fixed_copy_type_exceptions_consume_full_type_words(self):
        spec=_fixed_exception("it's a Synth artifact creature in addition to its other types")
        self.assertEqual(('artifact','creature'),spec.add_card_types)
        self.assertEqual(('synth',),spec.add_subtypes)
        spec=_fixed_exception("it's a Ninja in addition to its other creature types")
        self.assertEqual(('ninja',),spec.add_subtypes)
        for text in ("it's a Synth unknown creature in addition to its other types",
            "it's a Synth artifact creature in addition to its other creature types",
            "it's a Synth Synth artifact creature in addition to its other types",
            "it's a Ninja in addition to its other types. Draw a card."):
            with self.subTest(text=text):self.assertIsNone(_fixed_exception(text))


class FixedCopyTypeExceptionRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=actions.BoundEffectProgramRuntimeTests.session
    add=actions.BoundEffectProgramRuntimeTests.add
    checkpoint=actions.BoundEffectProgramRuntimeTests.checkpoint
    resolve=actions.BoundEffectProgramRuntimeTests.resolve
    replay=actions.BoundEffectProgramRuntimeTests.replay
    prepare=zone_actions.SelfPublicZoneRuntimeTests.prepare
    cast=zone_actions.SelfPublicZoneRuntimeTests.cast
    assert_closed=zone_actions.SelfPublicZoneRuntimeTests.assert_closed

    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();path=Path(cls.temp.name)/'copy-type.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',FIXTURE],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Copy types',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temp.cleanup()

    def choose_copy(self,session,target):
        decision=self.resolve(session);self.assertEqual('replacement.order',decision.kind)
        session=self.replay(session,load=True)
        option=next(row for row in session.packet('pilot:A',full=True)['decision']['ctx']['options']
            if target.ref in row['id'] and not row['id'].startswith('decline:'))
        before=authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:C',{'action_id':'choose','replacement':option['id']}).ok)
        self.assertEqual(before,authoritative_state_hash(session.state))
        result=session.act('pilot:A',{'action_id':'choose','replacement':option['id']})
        self.assertTrue(result.ok,result.summary);self.resolve(session)
        return session

    def test_actual_synth_exception_is_copiable_and_survives_ability_removal(self):
        session=self.session(70709101);engine=session.engine
        target=self.add(engine,'Generic Bound Body',seat='B')
        source=self.add(engine,'Synth Infiltrator',zone='hand')
        clone=self.add(engine,'Clone',zone='hand')
        humble=self.add(engine,'Humble',zone='hand')
        private=self.add(engine,'Generic Bound Growth',seat='C',zone='hand',ref='copy-type-private')
        self.assert_closed(session,source)
        self.prepare(session,mana={'C':3,'U':2});self.checkpoint(session)
        self.cast(session,'A',source);session=self.choose_copy(session,target)
        current=session.state.cards[source.object_id]
        data=session.engine._effective_card_data(current)
        self.assertEqual({'artifact','creature'},session.engine._type_parts(data['type_line'])[0])
        self.assertIn('synth',session.engine._type_parts(data['type_line'])[1])
        self.assertNotIn('improvise',{keyword.casefold() for keyword in data['keywords']})
        self.replay(session,load=True)
        self.prepare(session,mana={'C':3,'U':1});self.checkpoint(session)
        self.cast(session,'A',clone);session=self.choose_copy(session,current)
        copied=session.state.cards[clone.object_id]
        data=session.engine._effective_card_data(copied)
        self.assertEqual({'artifact','creature'},session.engine._type_parts(data['type_line'])[0])
        self.assertIn('synth',session.engine._type_parts(data['type_line'])[1])
        for viewer in 'ABD':self.assertNotIn(private.ref,json.dumps(session.packet('pilot:'+viewer,full=True)))
        self.replay(session,load=True)
        self.prepare(session,mana={'C':1,'W':1});self.checkpoint(session)
        self.cast(session,'A',humble,[copied.ref]);self.resolve(session)
        data=session.engine._effective_card_data(copied)
        self.assertEqual({'artifact','creature'},session.engine._type_parts(data['type_line'])[0])
        self.assertIn('synth',session.engine._type_parts(data['type_line'])[1])
        self.replay(session,load=True)

    def test_original_type_exception_carriers_require_whole_card_admission(self):
        for name in ('Synth Infiltrator',"Sakashima's Student"):
            with self.subTest(name=name):
                card=self.db.lookup(name)
                p=compile_best_available_card_program(self.db,card,semantic_registry=SemanticRegistry(),capability_registry=self.registry,capability_profile='commander_review')
                b=bind_card_program_runtime(p,capability_registry=self.registry,profile='commander_review')
                self.assertTrue(b['strict_capability_ready'],b['blockers'])
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        changed=json.loads(json.dumps(raw));next(r for r in changed['capabilities'] if r['id']=='zone.entry.fixed_copy').update(status='blocked',blockers=['copy owner omitted'])
        blocked=CapabilityRegistry(changed)
        card=self.db.lookup('Synth Infiltrator');p=compile_best_available_card_program(self.db,card,semantic_registry=SemanticRegistry(),capability_registry=blocked,capability_profile='commander_review')
        binding=bind_card_program_runtime(p,capability_registry=blocked,profile='commander_review')
        self.assertFalse(binding['strict_capability_ready'])

    def test_copied_type_omission_is_killed_by_actual_synth(self):
        import quorune.semantic_runtime.entry_copy as owner
        original=owner.apply_copy_exception
        def omitted(values,spec):return original(values,replace(spec,add_card_types=(),add_subtypes=()))
        with mock.patch.object(owner,'apply_copy_exception',side_effect=omitted):
            with self.assertRaises(AssertionError):self.test_actual_synth_exception_is_copiable_and_survives_ability_removal()


class NamedAdditiveArtifactCopyTests(unittest.TestCase):
    def test_compiler_consumes_the_named_additive_copy_lifecycle(self):
        from quorune.compiler.token_copy_templates import token_copy_recipe_template
        from quorune.token_copy_recipes import NAMED_ADDITIVE_COPY_VERSION, TokenCopyRecipeSpec

        body = (
            "Create a token that's a copy of target noncreature artifact you control, "
            "except its name is The Inventor's Replica and it's a 5/3 Golem artifact creature "
            "in addition to its other types. It gains haste until end of turn. "
            "Sacrifice it at the beginning of the next end step."
        )
        result = token_copy_recipe_template(body, source_name="Generic Inventor")
        recipe = TokenCopyRecipeSpec.from_dict(result.effect["copy_spec"])
        self.assertEqual(NAMED_ADDITIVE_COPY_VERSION, recipe.schema_version)
        self.assertEqual(("The Inventor's Replica", 5, 3),
                         (recipe.exception.name, recipe.exception.power, recipe.exception.toughness))
        self.assertEqual(("artifact", "creature"), recipe.exception.add_card_types)
        self.assertEqual(("golem",), recipe.exception.add_subtypes)
        self.assertEqual(("sacrifice_next_end_step", ("Haste",), "until_end_of_turn"),
                         (recipe.cleanup, recipe.temporary_keywords, recipe.keyword_duration))
        for variant in (
            body.replace("noncreature artifact", "creature"),
            body.replace("you control", "an opponent controls"),
            body.replace("until end of turn", "this combat"),
            body.replace("of the next end step", "of your next end step"),
            body + " Draw a card.",
        ):
            with self.subTest(variant=variant):
                self.assertIsNone(token_copy_recipe_template(variant, source_name="Generic Inventor"))


class NamedAdditiveArtifactCopyNativeTests(unittest.TestCase):
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
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        folder = Path(cls.temporary.name)
        selections = {
            "scryfall-exact-lists.json": {
                "Mishra, Eminent One", "Ichor Wellspring", "Sundial of the Infinite", "Darksteel Citadel", "Island",
            },
            "fixed-created-token-haste-cards.json": {"Cackling Counterpart"},
            "bound-effect-program-cards.json": {
                "Generic Bound Commander", "Generic Bound Plains", "Grizzly Bears", "Unsummon", "Disenchant",
            },
        }
        selected_fixtures = []
        for name, wanted in selections.items():
            data = json.loads((ROOT / "tests" / "fixtures" / name).read_bytes())
            data["cards"] = [row for row in data["cards"] if row["name"] in wanted]
            assert {row["name"] for row in data["cards"]} == wanted
            ids = {row["oracle_id"] for row in data["cards"]}
            data["rulings"] = [row for row in data.get("rulings", ()) if row["oracle_id"] in ids]
            fixture = folder / name
            fixture.write_text(json.dumps(data), encoding="utf-8")
            selected_fixtures.append(fixture)
        path = folder / "named-additive-copy.sqlite3"
        build_fixture_database(selected_fixtures, path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        ruler = "Generic Bound Commander"
        cls.deck = DeckDefinition("Named artifact copies", [
            DeckEntry(ruler, 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], [ruler])

    def start(self, game, source):
        for object_id in game.state.players["A"].zones["hand"][:3]:
            game.engine.move_card(object_id, "library", log=False)
        self.assert_closed(game, source)
        programs = game.engine.semantics.programs_for_oracle(source.oracle_id)
        trigger, = [program for program in programs if program.event == "step.begin"]
        self.assertEqual("create_token", trigger.effects[0]["op"])
        self.assertEqual(9, trigger.effects[0]["copy_spec"]["schema_version"])
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game, actor="A", active="A")
        self.checkpoint(game)
        return trigger

    def act_ok(self, game, seat, command):
        outcome = game.act("pilot:" + seat, command)
        self.assertTrue(outcome.ok, outcome.summary)

    def until(self, game, predicate):
        commands = {
            "priority": {"action_id": "pass"},
            "combat.attackers": {"action_id": "attack", "attackers": {}},
            "combat.blockers": {"action_id": "block", "blocks": {}},
        }
        for _ in range(90):
            if predicate(game):
                return
            pending = game.state.pending_decision
            self.assertIsNotNone(pending)
            self.assertIn(pending.kind, commands)
            result = game.act(game.pending_principals()[0], commands[pending.kind])
            self.assertTrue(result.ok, result.summary)
        self.fail("Did not reach the expected decision")

    def resolve_top(self, game):
        ref = game.state.stack[-1].ref
        self.until(game, lambda match: all(item.ref != ref for item in match.state.stack))

    def reach_target(self, game, source):
        self.until(game, lambda match: match.state.pending_decision.kind == "semantic.target")
        self.assertEqual("beginning_combat", game.state.step)
        self.assertEqual(["pilot:A"], game.pending_principals())
        item, = game.state.stack
        self.assertEqual(source.object_id, item.source_object_id)
        return item

    def assert_target_rejection(self, game, seat, targets):
        previous = authoritative_state_hash(game.state)
        rejected = game.act("pilot:" + seat, {"action_id": "choose", "targets": targets})
        self.assertFalse(rejected.ok, rejected.summary)
        self.assertEqual(previous, authoritative_state_hash(game.state))

    def warforms(self, game):
        return [card for card in game.state.cards.values() if card.is_token and card.zone == "battlefield"
                and game.engine.display_name(card.object_id) == "Mishra's Warform"]

    def test_actual_named_copy_after_source_departure_and_next_players_end_step(self):
        from quorune.haste import has_effective_haste

        game = self.session(72416001)
        source = self.add(game.engine, "Mishra, Eminent One")
        model = self.add(game.engine, "Ichor Wellspring")
        model.tapped = True
        model.counters["charge"] = 1
        other_artifact = self.add(game.engine, "Ichor Wellspring", seat="B", ref="foreign-artifact")
        creature = self.add(game.engine, "Grizzly Bears")
        sundial = self.add(game.engine, "Sundial of the Infinite")
        bounce = self.add(game.engine, "Unsummon", seat="B", zone="hand")
        copy = self.add(game.engine, "Cackling Counterpart", zone="hand")
        self.add(game.engine, "Island", seat="B", ref="bounce-island")
        for index in range(3):
            self.add(game.engine, "Island", ref=f"copy-island-{index}")
        self.add(game.engine, "Generic Bound Plains", ref="sundial-payment")
        self.start(game, source)
        item = self.reach_target(game, source)
        self.assertEqual(source.logical_object_id, item.context["source_logical_object_id"])
        for seat, targets in (("A", []), ("A", [other_artifact.ref]), ("A", [creature.ref]), ("C", [model.ref])):
            self.assert_target_rejection(game, seat, targets)
        self.assertIsNone(game.packet("pilot:D", full=True)["decision"])
        game = self.replay(game, load=True)
        self.act_ok(game, "A", {"action_id": "choose", "targets": [model.ref]})
        self.cast(game, "B", bounce, [source.ref])
        self.resolve_top(game)
        self.assertEqual("hand", game.state.cards[source.object_id].zone)
        self.assertEqual(item.ref, game.state.stack[-1].ref)
        hand_count = len(game.state.players["A"].zones["hand"])
        self.assertIsNone(self.resolve(game))
        self.assertEqual(hand_count + 1, len(game.state.players["A"].zones["hand"]))
        warform, = self.warforms(game)
        values = game.engine._copyable_characteristics(warform)
        self.assertEqual(("Mishra's Warform", "{2}", 2, "4", "4"),
                         (values["name"], values["mana_cost"], values["mana_value"], values["power"], values["toughness"]))
        self.assertEqual(({"artifact", "creature"}, {"construct"}), game.engine._type_parts(values["type_line"])[:2])
        self.assertFalse(warform.tapped)
        self.assertFalse(warform.counters)
        self.assertTrue(has_effective_haste(game.engine, warform))
        self.assertNotIn("Haste", values["keywords"])
        delayed, = game.state.delayed_triggers
        self.assertEqual({"phase": "ending", "step": "end_step"}, delayed.condition)
        self.assertEqual(("A", source.object_id, source.logical_object_id, (warform.object_id,)),
                         (delayed.controller, delayed.source_object_id, delayed.source_logical_object_id,
                          tuple(delayed.referred_object_ids)))
        game = self.replay(game, load=True)
        self.cast(game, "A", copy, [warform.ref])
        self.assertIsNone(self.resolve(game))
        duplicate, = [token for token in self.warforms(game) if token.object_id != warform.object_id]
        snapshot = game.engine._copyable_characteristics(duplicate)
        self.assertEqual((values["name"], values["type_line"], 2),
                         (snapshot["name"], snapshot["type_line"], snapshot["mana_value"]))
        self.assertFalse(has_effective_haste(game.engine, duplicate))
        self.until(game, lambda match: match.state.pending_decision.kind == "combat.attackers")
        self.act_ok(game, "A", {"action_id": "attack", "attackers": {warform.ref: "B"}})
        self.until(game, lambda match: match.state.pending_decision.kind == "priority"
                   and match.state.priority_player == "A")
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        sundial_action, = [row["id"] for row in actions if row["id"].startswith("activate:" + sundial.ref + ":")]
        self.act_ok(game, "A", {"action_id": sundial_action, "pay": "auto"})
        self.resolve_top(game)
        self.assertEqual("B", game.state.active_player)
        self.assertFalse(has_effective_haste(game.engine, game.state.cards[warform.object_id]))
        self.assertEqual("battlefield", game.state.cards[warform.object_id].zone)
        self.assertEqual(1, len(game.state.delayed_triggers))
        self.until(game, lambda match: match.state.active_player == "B" and match.state.step == "end_step")
        self.assertIsNone(self.resolve(game))
        self.assertEqual("outside", game.state.cards[warform.object_id].zone)
        self.assertEqual("battlefield", game.state.cards[duplicate.object_id].zone)
        self.assertEqual("battlefield", game.state.cards[model.object_id].zone)
        self.assertFalse(any(trigger.active for trigger in game.state.delayed_triggers))
        self.replay(game, load=True)

    def test_actual_named_copy_loses_its_only_original_model(self):
        game = self.session(72416002)
        source = self.add(game.engine, "Mishra, Eminent One")
        model = self.add(game.engine, "Ichor Wellspring")
        destroy = self.add(game.engine, "Disenchant", seat="B", zone="hand")
        self.add(game.engine, "Generic Bound Plains", seat="B", ref="artifact-payment-1")
        self.add(game.engine, "Generic Bound Plains", seat="B", ref="artifact-payment-2")
        self.start(game, source)
        self.reach_target(game, source)
        self.act_ok(game, "A", {"action_id": "choose", "targets": [model.ref]})
        self.cast(game, "B", destroy, [model.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", game.state.cards[model.object_id].zone)
        self.assertFalse(self.warforms(game))
        self.assertFalse(game.state.delayed_triggers)
        self.replay(game, load=True)

    def test_actual_named_copy_keeps_models_other_card_types(self):
        game = self.session(72416003)
        source = self.add(game.engine, "Mishra, Eminent One")
        model = self.add(game.engine, "Darksteel Citadel")
        self.start(game, source)
        self.reach_target(game, source)
        self.act_ok(game, "A", {"action_id": "choose", "targets": [model.ref]})
        self.assertIsNone(self.resolve(game))
        token, = self.warforms(game)
        values = game.engine._copyable_characteristics(token)
        types, subtypes, _ = game.engine._type_parts(values["type_line"])
        self.assertEqual({"artifact", "creature", "land"}, types)
        self.assertEqual({"construct"}, subtypes)
        self.assertIn("Indestructible", values["keywords"])

    def test_omitting_the_additive_copy_types_changes_the_actual_result(self):
        from quorune import token_copy_runtime

        apply_copy_exception = token_copy_runtime.apply_copy_exception

        def without_types(characteristics, spec):
            return apply_copy_exception(characteristics, replace(spec, add_card_types=(), add_subtypes=()))

        with mock.patch.object(token_copy_runtime, "apply_copy_exception", side_effect=without_types):
            with self.assertRaisesRegex(AssertionError, "creature"):
                self.test_actual_named_copy_keeps_models_other_card_types()

    def test_fresh_admission_only_supersedes_the_exact_reviewed_descriptor(self):
        from quorune.card_programs.trigger_view_equivalence import shadowed_reviewed_trigger_view_keys

        game = self.session(72416004)
        source = self.add(game.engine, "Mishra, Eminent One")
        current = self.start(game, source)
        record = self.db.lookup(source.printed_name, fuzzy=False)
        old, = [program for program in SemanticRegistry().programs_for_oracle(record.oracle_id)
                if program.event == "step.begin"]
        self.assertEqual("create_modified_token_copy", old.effects[0]["op"])

        def shadowed(prior):
            return shadowed_reviewed_trigger_view_keys(
                record, (current,), (prior,), capability_registry=self.registry, capability_profile="commander_review",
            )

        self.assertEqual({old.key}, shadowed(old))
        self.assertEqual(set(), shadowed(replace(old, effects=({**old.effects[0], "sacrifice_on_controller_end_step": False},))))
        self.assertEqual(set(), shadowed(replace(old, target_schema={**old.target_schema, "creature": True})))
        self.assertEqual(set(), shadowed(replace(old, provenance={**old.provenance, "source_rulings_hash": "other"})))
