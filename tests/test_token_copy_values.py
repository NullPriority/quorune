"""Copy-value semantics preserve historical absence and exact record hashes."""

from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from inherited_composition_fixture import InheritedNativeFixture
from quorune.model import CardInstance,GameConfig,GameState
from quorune.record_state_provenance import format_state_versions,validate_state_versions
from quorune.token_copy_value_model import TOKEN_COPY_VALUE_VERSION_FIELD as FIELD,token_copy_value_version


class TokenCopyValueVersionTests(unittest.TestCase):
    def state(self):
        return GameState(game_id='copy-value-version',config=GameConfig(),players={},cards={},
            deck_names={},commander_oracle_ids={},turn_order=[],current_turn=None,last_normal_turn_player=None)

    def test_new_state_and_manifest_pin_one_and_historical_absence_stays_absent(self):
        state=self.state();payload=state.to_dict()
        self.assertEqual(1,payload[FIELD]);self.assertEqual(1,format_state_versions(state)[FIELD])
        validate_state_versions({'format':format_state_versions(state)},state)
        payload.pop(FIELD);legacy=GameState.from_dict(payload)
        self.assertIsNone(legacy.token_copy_value_version)
        self.assertNotIn(FIELD,legacy.to_dict());self.assertNotIn(FIELD,format_state_versions(legacy))
        validate_state_versions({'format':format_state_versions(legacy)},legacy)
        self.assertEqual(payload,legacy.to_dict())

    def test_unknown_boolean_and_disagreeing_versions_reject(self):
        for bad in (True,False,0,2,'1',1.0):
            with self.subTest(value=bad),self.assertRaises(ValueError):token_copy_value_version(bad)
            with self.subTest(state=bad),self.assertRaises(ValueError):GameState.from_dict({**self.state().to_dict(),FIELD:bad})
        state=self.state();manifest={'format':format_state_versions(state)}
        for bad in (False,2,0):
            changed=deepcopy(manifest);changed['format'][FIELD]=bad
            with self.subTest(manifest=bad),self.assertRaisesRegex(ValueError,'Token copy value provenance'):
                validate_state_versions(changed,state)
        changed=deepcopy(manifest);changed['format'].pop(FIELD)
        with self.assertRaisesRegex(ValueError,'Token copy value provenance'):validate_state_versions(changed,state)

    def test_annotation_mode_selects_current_copy_values_or_original_legacy_definition(self):
        from quorune.defined_token_faces import defined_token_characteristic_values
        card=CardInstance(object_id='token',ref='T1',oracle_id='custom-token:one',printed_name='One',
            owner='A',controller='A',zone='battlefield',is_token=True,
            annotations={'token_characteristics':{'name':'Original','activated_abilities':[]},
                         'copy_overrides':{'name':'Copied','activated_abilities':[{'copied':True}]}})
        self.assertEqual('Original',defined_token_characteristic_values(card)['name'])
        card.annotations[FIELD]=1
        self.assertEqual('Copied',defined_token_characteristic_values(card)['name'])
        card.annotations[FIELD]=True
        with self.assertRaises(ValueError):defined_token_characteristic_values(card)

    def test_snapshot_and_explicit_copy_names_keep_the_selected_version(self):
        from types import SimpleNamespace
        from quorune.token_creation_identity import token_copy_snapshot,_copied_token_identity
        card=CardInstance(object_id='source',ref='S',oracle_id='custom-token:source',printed_name='Physical label',
            owner='A',controller='A',zone='battlefield',is_token=True)
        for mode,expected in ((None,'Physical label'),(1,'Current copied name')):
            host=SimpleNamespace(state=SimpleNamespace(token_copy_value_version=mode),
                _copyable_characteristics=lambda source:{'name':'Current copied name','type_line':'Artifact'})
            snapshot=token_copy_snapshot(host,card)
            self.assertEqual(expected,snapshot['printed_name'])
            identity=_copied_token_identity(host,'A',copy_of=card.ref,name='',characteristics={},ref='T1',copy_snapshot=snapshot)
            self.assertEqual(expected,identity[2])

    def test_prior_pinned_runtime_is_rejected_before_checkpoint_or_commands(self):
        import json
        from unittest.mock import patch
        from common import ROOT
        from quorune.record import ENGINE_VERSION,replay_record,semantics_fingerprint
        from quorune.record_trust import runtime_trust_provenance
        from quorune.semantics import SemanticRegistry
        recipe=json.loads((ROOT/'tests/fixtures/historical-token-copy-provenance-recipe.json').read_text(encoding='utf8'))
        semantics=SemanticRegistry();current=runtime_trust_provenance()
        historical=deepcopy(current);historical.update(recipe['runtime_trust_fingerprints'])
        self.assertNotEqual(current,historical)
        self.assertEqual(ENGINE_VERSION,recipe['engine_version'])
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)
            (path/'manifest.json').write_text(json.dumps({'schema_version':3,'engine_version':ENGINE_VERSION,
                'semantics_fingerprint':semantics_fingerprint(semantics),'runtime_trust':historical}),encoding='utf8')
            with patch('quorune.record_trust.SemanticRegistry',return_value=semantics),patch('quorune.record.read_initial_checkpoint') as checkpoint:
                with self.assertRaisesRegex(ValueError,recipe['current_pinned_replay_disposition']):
                    replay_record(path,None,verify=True)
                checkpoint.assert_not_called()


class TokenCopyValueNativeTests(unittest.TestCase):
    register_fixture_cards_on_add=True

    @classmethod
    def setUpClass(cls):
        from common import ROOT
        from quorune.carddb import CardDatabase
        from quorune.deck import DeckDefinition,DeckEntry
        from quorune.rules.capabilities import load_default_capability_registry
        from scripts.build_test_database import build_fixture_database
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'copy-values.sqlite3'
        build_fixture_database([ROOT / "tests/fixtures" / name for name in (
            "bound-effect-program-cards.json", "token-copy-recipe-cards.json",
            "copied-aura-token-entry-cards.json",
        )], path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Copy-value witness',[DeckEntry('Generic Bound Commander',1,'commander'),
            DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()

    def test_actual_populate_uses_selected_mode_and_round_trips_pending_record(self):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as Helpers
        for index,mode in enumerate((1,None)):
            with self.subTest(mode=mode):
                session=Helpers.session(self,919010+index);engine=session.engine
                engine.state.token_copy_value_version=mode
                ref=engine.create_token('A',name='Original token',characteristics={
                    'type_line':'Token Creature — Elf','power':'2','toughness':'2','colors':['G']})[0]
                original=engine._resolve_object('A',ref,zones={'battlefield'})
                original.annotations['until_end_of_turn']={'compatibility_witness':True}
                source=Helpers.add(self,engine,'Generic Recipe Populate',zone='hand')
                action=Helpers.ready(self,session,source,{'W':1});Helpers.checkpoint(self,session)
                accepted=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'})
                self.assertTrue(accepted.ok,accepted.summary);Helpers.resolve(self,session)
                session=Helpers.replay(self,session,load=True);engine=session.engine
                self.assertEqual(mode,engine.state.token_copy_value_version)
                accepted=session.act('pilot:A',{'action_id':'choose','card':ref})
                self.assertTrue(accepted.ok,accepted.summary);Helpers.resolve(self,session)
                copied=next(card for card in engine.state.cards.values() if card.is_token
                    and card.zone=='battlefield' and card.object_id!=original.object_id)
                if mode==1:
                    self.assertEqual(1,copied.annotations[FIELD])
                    self.assertNotIn('until_end_of_turn',copied.annotations)
                    self.assertNotIn('token_characteristics',copied.annotations)
                else:
                    self.assertNotIn(FIELD,copied.annotations)
                    self.assertIn('until_end_of_turn',copied.annotations)
                    self.assertIn('token_characteristics',copied.annotations)
                    expected=deepcopy(engine.state.cards[original.object_id].annotations)
                    expected['copied_from']=original.object_id
                    expected['copy_overrides']={**expected['copy_overrides'],'name':'Original token'}
                    self.assertEqual(expected,copied.annotations)
                self.assertNotIn(FIELD,engine._copyable_characteristics(copied))
                Helpers.replay(self,session,load=True)

    def test_actual_aura_token_copies_choose_independent_attachments_before_entry(self):
        from dataclasses import replace
        import json

        from quorune.card_programs import bind_card_program_runtime
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.engine import TURN_STEPS
        from quorune.record import authoritative_state_hash
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as Helpers
        from test_self_public_zone_casting import SelfPublicZoneRuntimeTests

        session = Helpers.session(self, 743301)
        engine = session.engine
        replication = Helpers.add(self, engine, "Extravagant Replication")
        aura = Helpers.add(self, engine, "Mind Control", zone="hand")
        original = Helpers.add(self, engine, "Generic Bound Body", seat="B", ref="original-host")
        another = Helpers.add(self, engine, "Generic Bound Body", seat="B", ref="second-copy-host")
        shroud = Helpers.add(self, engine, "Elvish Lookout", seat="C")
        doubler = Helpers.add(self, engine, "Parallel Lives")
        private = session.state.cards[session.state.players["D"].zones["hand"][0]]
        engine.move_card(aura.object_id, "battlefield", aura_target_ref=original.ref)
        self.assertEqual("A", session.state.cards[original.object_id].controller)
        for card in (replication, aura, shroud, doubler):
            record = self.db.by_oracle_id(card.oracle_id)
            program = compile_best_available_card_program(
                self.db, record, semantic_registry=engine.semantics, capability_registry=self.registry,
                capability_profile="commander_review",
            )
            bound = bind_card_program_runtime(program, capability_registry=self.registry, profile="commander_review")
            self.assertTrue(bound["strict_capability_ready"], (card.printed_name, bound["blockers"]))

        SelfPublicZoneRuntimeTests.prepare(self, session, actor="D", active="D")
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.phase, engine.state.step = "ending", "end_step"
        engine.state.phase_index = TURN_STEPS.index(("ending", "end_step"))
        engine.state.last_normal_turn_player = "D"
        if engine.state.current_turn is not None:
            engine.state.current_turn = replace(engine.state.current_turn, player="D")
        engine._grant_priority("D")
        engine.pump()
        Helpers.checkpoint(self, session)
        for _ in range(4):
            principal, = session.pending_principals()
            result = session.act(principal, {"action_id": "pass"})
            self.assertTrue(result.ok, result.summary)
        self.assertEqual("semantic.target", session.state.pending_decision.kind)
        target = session.act("pilot:A", {"action_id": "choose", "targets": [aura.ref]})
        self.assertTrue(target.ok, target.summary)
        self.assertEqual([aura.ref], session.state.stack[-1].targets)
        for _ in range(4):
            principal, = session.pending_principals()
            result = session.act(principal, {"action_id": "pass"})
            self.assertTrue(result.ok, result.summary)

        decision = session.state.pending_decision
        self.assertEqual("aura.entry", decision.kind)
        self.assertEqual(["A"], decision.actors)
        self.assertEqual([aura.ref], session.state.stack[-1].targets)
        self.assertEqual([], [card.ref for card in session.state.cards.values() if card.is_token])
        packet = session.packet("pilot:A", full=True)
        self.assertNotIn(private.ref, json.dumps(packet))
        self.assertIsNone(session.packet("pilot:B", full=True)["decision"])
        self.assertIn(shroud.ref, packet["decision"]["ctx"]["target_schema"]["legal_refs"])

        before = authoritative_state_hash(session.state)
        for seat, ref in (("B", another.ref), ("A", replication.ref)):
            rejected = session.act(f"pilot:{seat}", {"action_id": "choose", "aura_target": ref})
            self.assertFalse(rejected.ok)
            self.assertEqual(before, authoritative_state_hash(session.state))
        first = session.act("pilot:A", {"action_id": "choose", "aura_target": shroud.ref})
        self.assertTrue(first.ok, first.summary)
        self.assertEqual("aura.entry", session.state.pending_decision.kind)
        self.assertEqual([], [card.ref for card in session.state.cards.values() if card.is_token])
        session = Helpers.replay(self, session, load=True)

        second = session.act("pilot:A", {"action_id": "choose", "aura_target": another.ref})
        self.assertTrue(second.ok, second.summary)
        self.assertFalse(session.state.stack)
        copies = [card for card in session.state.cards.values() if card.is_token and card.zone == "battlefield"]
        self.assertEqual(2, len(copies))
        self.assertEqual({shroud.object_id, another.object_id}, {card.attached_to for card in copies})
        self.assertEqual({"Mind Control"}, {session.engine.display_name(card.object_id) for card in copies})
        self.assertEqual(original.object_id, session.state.cards[aura.object_id].attached_to)
        for host in (original, shroud, another):
            self.assertEqual("A", session.state.cards[host.object_id].controller)
        self.assertEqual("A", session.state.priority_player)
        Helpers.replay(self, session, load=True)



class TokenCopyBatchNativeTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import json

        from common import ROOT
        from quorune.carddb import CardDatabase
        from quorune.deck import DeckDefinition, DeckEntry
        from quorune.rules.capabilities import load_default_capability_registry
        from scripts.build_test_database import build_fixture_database

        cls.temporary = tempfile.TemporaryDirectory()
        folder = Path(cls.temporary.name)
        fixtures = ROOT / "tests" / "fixtures"
        path = folder / "copy-batch.sqlite3"
        support = {
            "creature-subtype-addition-cards.json": "Isamaru, Hound of Konda",
            "permanent-control-exchange-cards.json": "Avarice Totem",
            "activation-restriction-material-cards.json": "Memnite",
            "kicked-same-target-replacement-cards.json": "Reverberate",
        }
        cards = []
        for filename, name in support.items():
            data = json.loads((fixtures / filename).read_bytes())
            selected, = (row for row in data["cards"] if row["name"] == name)
            cards.append(selected)
        legend = folder / "copy-batch-models-and-controls.json"
        legend.write_text(json.dumps({"schema_version": 1, "cards": cards}), encoding="utf-8")
        build_fixture_database([
            fixtures / "bound-effect-program-cards.json", fixtures / "token-copy-recipe-cards.json",
            fixtures / "copied-aura-token-entry-cards.json", legend,
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Current copy batches", [
            DeckEntry("Generic Bound Commander", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])

    def _tokens(self, game, *, controller="A"):
        return [card for card in game.state.cards.values()
                if card.is_token and card.zone == "battlefield" and card.controller == controller]

    def _creations(self, game, cursor):
        return [event for event in game.state.events[cursor:] if event.code == "token.create"]

    def test_complete_originals_require_one_typed_model_scope_and_batch_dependency(self):
        from dataclasses import replace
        from unittest.mock import patch

        from quorune.card_programs.binding import bind_semantic_program_runtime
        from quorune.rules.capabilities import capability_dependencies_for_node
        from quorune.token_copy_recipes import TOKEN_COPY_BATCH_CAPABILITY, TokenCopyBatchKind, TokenCopyBatchSpec
        from test_controller_creature_keyword_absence import register_card

        originals = (
            ("Clone Legion", TokenCopyBatchKind.TARGET_PLAYER_CREATURES),
            ("Second Harvest", TokenCopyBatchKind.CONTROLLER_TOKENS),
            ("Multiversal Incursion", TokenCopyBatchKind.CONTROLLER_NONTOKEN_CREATURES),
        )
        for name, kind in originals:
            with self.subTest(name=name):
                spec = TokenCopyBatchSpec(kind)
                report, semantics = register_card(self.db, self.registry, name)
                self.assertTrue(report["strict_capability_ready"], report["blockers"])
                self.assertEqual("capability_closed", report["trust_basis"])
                program, = semantics.programs_for_oracle(self.db.lookup(name).oracle_id)
                self.assertEqual((spec.to_effect(),), tuple(program.effects))
                self.assertEqual(spec.target_schema, program.target_schema)
                self.assertTrue(set(spec.capability_dependencies) <= set(program.capability_dependencies))
                effect = spec.to_effect()
                for invalid in (
                    {**effect, "copy_batch": {**effect["copy_batch"], "schema_version": True}},
                    {**effect, "copy_batch": {**effect["copy_batch"], "kind": "target_player_tokens"}},
                    {**effect, "tokens": []},
                    {**effect, "controller": "$target.0"},
                ):
                    bound = bind_semantic_program_runtime(
                        replace(program, effects=(invalid,)), capability_registry=self.registry, profile="commander_review",
                    )
                    self.assertFalse(bound["strict"], bound)
                wrong_schema = None if spec.targets_player else TokenCopyBatchSpec(
                    TokenCopyBatchKind.TARGET_PLAYER_CREATURES,
                ).target_schema
                self.assertNotIn(TOKEN_COPY_BATCH_CAPABILITY, capability_dependencies_for_node(
                    effects=program.effects, target_schema=wrong_schema, mechanic_ids=program.coverage,
                ))
        with patch("quorune.compiler.token_copy_templates._token_copy_batch_template", return_value=None):
            for name, _ in originals:
                report, _ = register_card(self.db, self.registry, name)
                self.assertFalse(report["strict_capability_ready"], report)

    def test_actual_clone_legion_copies_current_models_in_one_replacement_aware_batch(self):
        from collections import Counter
        import json

        game = self.session(7072101)
        spell = self.add(game.engine, "Clone Legion", zone="hand")
        doubler = self.add(game.engine, "Parallel Lives")
        bear = self.add(game.engine, "Grizzly Bears", seat="B", ref="legion-bear")
        self.add(game.engine, "Young Wolf", seat="B", ref="legion-wolf")
        departed = self.add(game.engine, "Grizzly Bears", seat="B", ref="legion-departing")
        self.add(game.engine, "Generic Bound Body", seat="C", ref="legion-wrong-controller")
        bounce = self.add(game.engine, "Unsummon", seat="B", zone="hand")
        private = self.add(game.engine, "Generic Bound Growth", seat="D", zone="hand", ref="legion-private")
        bear.tapped = True
        bear.counters["+1/+1"] = 2
        game.state.config.semantic_policy = "trusted_only"
        for card in (spell, doubler, bounce):
            self.assert_closed(game, card)
        game.state.players["B"].mana_pool.update({"U": 1})
        self.prepare(game, mana={"U": 2, "C": 7})
        self.checkpoint(game)
        self.cast(game, "A", spell, ["B"])
        self.cast(game, "B", bounce, [departed.ref])
        game = self.replay(game, load=True)
        cursor = len(game.state.events)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("hand", game.state.cards[departed.object_id].zone)
        copies = self._tokens(game)
        self.assertEqual(Counter({"Grizzly Bears": 2, "Young Wolf": 2}), Counter(
            game.engine._effective_card_data(card)["name"] for card in copies
        ))
        self.assertTrue(all(card.owner == "A" and not card.tapped and not card.counters for card in copies))
        created, = self._creations(game, cursor)
        self.assertEqual((2, 2), (created.details["base_quantity"], created.details["replacement_count"]))
        self.assertCountEqual([card.ref for card in copies], created.details["objects"])
        self.assertNotIn(private.ref, json.dumps(game.packet("pilot:A", full=True)))
        self.replay(game, load=True)

    def test_clone_legion_can_target_a_player_with_an_empty_cohort(self):
        from quorune.record import authoritative_state_hash

        game = self.session(7072102)
        spell = self.add(game.engine, "Clone Legion", zone="hand")
        self.add(game.engine, "Grizzly Bears", seat="C", ref="legion-untargeted-creature")
        game.state.config.semantic_policy = "trusted_only"
        self.assert_closed(game, spell)
        self.prepare(game, mana={"U": 2, "C": 7})
        self.checkpoint(game)
        rows = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        action, = (row for row in rows if row["id"] == f"cast:{spell.ref}")
        before = authoritative_state_hash(game.state)
        for targets in ([], ["B", "C"], ["legion-untargeted-creature"]):
            denied = game.act("pilot:A", {"action_id": action["id"], "targets": targets, "pay": "auto"})
            self.assertFalse(denied.ok)
            self.assertEqual(before, authoritative_state_hash(game.state))
        self.cast(game, "A", spell, ["B"])
        self.assertIsNone(self.resolve(game))
        self.assertFalse(self._tokens(game))
        self.assertFalse(self._creations(game, 0))
        self.assertEqual("graveyard", game.state.cards[spell.object_id].zone)
        self.replay(game, load=True)

    def test_actual_harvest_copies_its_treasure_and_aura_in_one_resumable_batch(self):
        import json

        from quorune.record import authoritative_state_hash

        game = self.session(7072103)
        spell = self.add(game.engine, "Second Harvest", zone="hand")
        aura = self.add(game.engine, "Mind Control", zone="hand")
        original_host = self.add(game.engine, "Grizzly Bears", seat="B", ref="harvest-original-host")
        token_host = self.add(game.engine, "Grizzly Bears", seat="C", ref="harvest-token-host")
        new_host = self.add(game.engine, "Young Wolf", seat="D", ref="harvest-new-host")
        private = self.add(game.engine, "Generic Bound Growth", seat="D", zone="hand", ref="harvest-private")
        game.engine.move_card(aura.object_id, "battlefield", aura_target_ref=original_host.ref)
        game.engine.create_token("A", name="", copy_of=aura.ref, aura_target_ref=token_host.ref)
        game.engine.create_token("A", name="Treasure", characteristics={"type_line": "Token Artifact — Treasure"})
        game.engine.create_token("B", name="Food", characteristics={"type_line": "Token Artifact — Food"})
        game.state.config.semantic_policy = "trusted_only"
        self.assert_closed(game, spell)
        self.prepare(game, mana={"G": 2, "C": 2})
        self.checkpoint(game)
        previous = {card.object_id for card in self._tokens(game)}
        self.assertEqual(2, len(previous))
        cursor = len(game.state.events)
        self.cast(game, "A", spell)
        pending = self.resolve(game)
        self.assertEqual(("aura.entry", ["A"]), (pending.kind, pending.actors))
        self.assertEqual(previous, {card.object_id for card in self._tokens(game)})
        self.assertFalse(self._creations(game, cursor))
        self.assertEqual([], game.state.stack[-1].targets)
        self.assertNotIn(private.ref, json.dumps(game.packet("pilot:A", full=True)))
        self.assertIsNone(game.packet("pilot:B", full=True)["decision"])
        game = self.replay(game, load=True)
        cursor = len(game.state.events)
        before = authoritative_state_hash(game.state)
        choose = {"action_id": "choose", "aura_target": new_host.ref}
        self.assertFalse(game.act("pilot:B", choose).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        result = game.act("pilot:A", choose)
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(game))
        copies = [card for card in self._tokens(game) if card.object_id not in previous]
        self.assertCountEqual(["Mind Control", "Treasure"], [game.engine.display_name(c.object_id) for c in copies])
        attached, = (card for card in copies if card.attached_to is not None)
        self.assertEqual(new_host.object_id, attached.attached_to)
        self.assertEqual("A", game.state.cards[new_host.object_id].controller)
        created, = self._creations(game, cursor)
        self.assertEqual(2, created.details["base_quantity"])
        self.assertCountEqual([card.ref for card in copies], created.details["objects"])
        self.assertEqual(1, len(self._tokens(game, controller="B")))
        self.replay(game, load=True)

    def test_actual_incursion_copies_nontoken_creatures_with_a_copiable_nonlegendary_exception(self):
        game = self.session(7072104)
        spell = self.add(game.engine, "Multiversal Incursion", zone="hand")
        legend = self.add(game.engine, "Isamaru, Hound of Konda", ref="incursion-legend")
        self.add(game.engine, "Grizzly Bears", ref="incursion-bear")
        self.add(game.engine, "Young Wolf", seat="B", ref="incursion-foreign")
        game.engine.create_token("A", name="Excluded Wolf",
                                 characteristics={"type_line": "Token Creature — Wolf", "power": "2", "toughness": "2"})
        game.state.config.semantic_policy = "trusted_only"
        self.assert_closed(game, spell)
        self.prepare(game, mana={"U": 2, "C": 5})
        self.checkpoint(game)
        previous = {card.object_id for card in self._tokens(game)}
        cursor = len(game.state.events)
        self.cast(game, "A", spell)
        self.assertIsNone(self.resolve(game))
        copies = [card for card in self._tokens(game) if card.object_id not in previous]
        self.assertCountEqual(["Isamaru, Hound of Konda", "Grizzly Bears"], [
            game.engine._effective_card_data(card)["name"] for card in copies
        ])
        copied_legend, = (card for card in copies if game.engine.display_name(card.object_id) == legend.printed_name)
        self.assertNotIn("Legendary", game.engine._copyable_characteristics(copied_legend)["type_line"])
        self.assertIn("Legendary", game.engine._copyable_characteristics(game.state.cards[legend.object_id])["type_line"])
        self.assertEqual("battlefield", game.state.cards[legend.object_id].zone)
        created, = self._creations(game, cursor)
        self.assertEqual(2, created.details["base_quantity"])
        self.assertCountEqual([card.ref for card in copies], created.details["objects"])
        self.replay(game, load=True)

    def test_verified_batch_withdrawal_keeps_ordinary_single_model_copies(self):
        import json

        from common import ROOT
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from quorune.token_copy_recipes import TOKEN_COPY_BATCH_CAPABILITY
        from scripts.update_capability_evidence import build_index, discover_tests
        from test_controller_creature_keyword_absence import register_card

        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        next(row for row in raw["capabilities"] if row["id"] == TOKEN_COPY_BATCH_CAPABILITY).update(
            status="blocked", blockers=["Scoped current copy batch withdrawal"],
        )
        registry = CapabilityRegistry(raw)
        index = build_index(registry_value=raw, declaration_source=declarations,
                            discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        closure = registry.closure((TOKEN_COPY_BATCH_CAPABILITY,), profile="commander_review")
        self.assertIn(f"status:{TOKEN_COPY_BATCH_CAPABILITY}:blocked", closure.blockers)
        self.assertFalse(any("unverified" in blocker for blocker in closure.blockers))
        for name in ("Clone Legion", "Second Harvest", "Multiversal Incursion"):
            report, _ = register_card(self.db, registry, name)
            self.assertFalse(report["strict_capability_ready"], report)
        control, _ = register_card(self.db, registry, "Extravagant Replication")
        self.assertTrue(control["strict_capability_ready"], control["blockers"])


    def test_complete_elminster_preserves_announced_opponent_roles(self):
        from dataclasses import replace
        from unittest.mock import patch

        from quorune.card_programs.binding import bind_semantic_program_runtime
        from quorune.token_copy_recipes import ANNOUNCED_OPPONENT_COPY_BATCH_CAPABILITY, TokenCopyBatchKind, TokenCopyBatchSpec
        from test_controller_creature_keyword_absence import register_card

        name = "Elminster's Simulacrum"
        original = self.db.lookup(name, fuzzy=False)
        report, semantics = register_card(self.db, self.registry, name)
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        self.assertEqual("capability_closed", report["trust_basis"])
        program, = semantics.programs_for_oracle(original.oracle_id)
        spec = TokenCopyBatchSpec(TokenCopyBatchKind.ANNOUNCED_OPPONENT_CREATURES, schema_version=2)
        self.assertEqual(spec.target_schema, program.target_schema)
        self.assertEqual((spec.to_effect(),), tuple(program.effects))
        self.assertIn(ANNOUNCED_OPPONENT_COPY_BATCH_CAPABILITY, program.capability_dependencies)
        for schema in (None, {**program.target_schema, "controller_relation": "any"},
                       {**program.target_schema, "retain_controller": False},
                       {key: value for key, value in program.target_schema.items()
                        if key not in {"distinct_controllers", "retain_controller"}}):
            with self.subTest(schema=schema):
                changed = replace(program, target_schema=schema)
                bound = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile="commander_review")
                self.assertFalse(bound["strict"], bound)
        payload = spec.to_effect()
        malformed = {**payload, "copy_batch": {**payload["copy_batch"], "schema_version": 1}}
        wrong = replace(program, effects=[malformed])
        self.assertFalse(bind_semantic_program_runtime(
            wrong, capability_registry=self.registry, profile="commander_review",
        )["strict"])
        with patch("quorune.compiler.token_copy_templates._token_copy_batch_template", return_value=None):
            denied, _ = register_card(self.db, self.registry, name)
            self.assertFalse(denied["strict_capability_ready"], denied)

    def test_actual_elminster_creates_one_batch_from_one_creature_per_selected_opponent(self):
        from collections import Counter
        import json

        from quorune.record import authoritative_state_hash

        game = self.session(60121101)
        spell = self.add(game.engine, "Elminster's Simulacrum", zone="hand")
        own = self.add(game.engine, "Grizzly Bears", ref="elminster-own")
        bear = self.add(game.engine, "Grizzly Bears", seat="B", ref="elminster-bear")
        other_b = self.add(game.engine, "Young Wolf", seat="B", ref="elminster-second-b")
        wolf = self.add(game.engine, "Young Wolf", seat="C", ref="elminster-wolf")
        self.add(game.engine, "Generic Bound Body", seat="D", ref="elminster-unselected")
        private = self.add(game.engine, "Generic Bound Growth", seat="D", zone="hand", ref="elminster-private")
        doubler = self.add(game.engine, "Parallel Lives")
        bear.tapped = True
        bear.counters["+1/+1"] = 2
        game.state.config.semantic_policy = "trusted_only"
        self.assert_closed(game, spell)
        self.assert_closed(game, doubler)
        self.prepare(game, mana={"C": 4, "U": 2})
        self.checkpoint(game)
        before = authoritative_state_hash(game.state)
        for targets in ([bear.ref, other_b.ref], [own.ref], [private.ref]):
            denied = game.act("pilot:A", {"action_id": f"cast:{spell.ref}", "targets": targets, "pay": "auto"})
            self.assertFalse(denied.ok)
            self.assertEqual(before, authoritative_state_hash(game.state))
        self.cast(game, "A", spell, [bear.ref, wolf.ref])
        game = self.replay(game, load=True)
        cursor = len(game.state.events)
        self.assertIsNone(self.resolve(game))
        copies = self._tokens(game)
        self.assertEqual(Counter({"Grizzly Bears": 2, "Young Wolf": 2}), Counter(
            game.engine._effective_card_data(card)["name"] for card in copies
        ))
        self.assertTrue(all(card.owner == "A" and not card.tapped and not card.counters for card in copies))
        self.assertEqual(1, len({card.zone_timestamp for card in copies}))
        batch, = self._creations(game, cursor)
        self.assertEqual((2, 2), (batch.details["base_quantity"], batch.details["replacement_count"]))
        self.assertCountEqual([card.ref for card in copies], batch.details["objects"])
        self.assertNotIn(private.ref, json.dumps(game.packet("pilot:A", full=True)))
        self.replay(game, load=True)

    def test_actual_elminster_omits_changed_controller_and_fizzles_after_total_loss(self):
        for case in ("controller", "total"):
            with self.subTest(case=case):
                game = self.session(60121102 + len(case))
                spell = self.add(game.engine, "Elminster's Simulacrum", zone="hand")
                selected = self.add(game.engine, "Grizzly Bears", seat="B", ref="elminster-invalidated")
                other = self.add(game.engine, "Young Wolf", seat="C", ref="elminster-surviving")
                if case == "controller":
                    response = self.add(game.engine, "Avarice Totem", seat="C")
                    game.state.players["C"].mana_pool.update({"C": 5})
                else:
                    response = self.add(game.engine, "Unsummon", seat="B", zone="hand")
                    game.state.players["B"].mana_pool.update({"U": 1})
                game.state.config.semantic_policy = "trusted_only"
                for card in (spell, response):
                    self.assert_closed(game, card)
                self.prepare(game, mana={"C": 4, "U": 2})
                self.checkpoint(game)
                self.cast(game, "A", spell, [selected.ref, other.ref] if case == "controller" else [selected.ref])
                if case == "controller":
                    self.priority(game, "C")
                    rows = game.packet("pilot:C", full=True)["decision"]["ctx"]["legal"]["actions"]
                    action, = (row for row in rows if row["id"].startswith(f"activate:{response.ref}:"))
                    accepted = game.act("pilot:C", {"action_id": action["id"], "targets": [selected.ref], "pay": "auto"})
                    self.assertTrue(accepted.ok, accepted.summary)
                else:
                    self.cast(game, "B", response, [selected.ref])
                self.resolve_top(game)
                game = self.replay(game, load=True)
                cursor = len(game.state.events)
                self.assertIsNone(self.resolve(game))
                copies = self._tokens(game)
                if case == "controller":
                    token, = copies
                    self.assertEqual("Young Wolf", game.engine._effective_card_data(token)["name"])
                    self.assertEqual("C", game.state.cards[selected.object_id].controller)
                    batch, = self._creations(game, cursor)
                    self.assertEqual([token.ref], batch.details["objects"])
                else:
                    self.assertFalse(copies)
                    self.assertFalse(self._creations(game, cursor))
                    self.assertEqual("hand", game.state.cards[selected.object_id].zone)
                self.assertEqual("graveyard", game.state.cards[spell.object_id].zone)
                self.replay(game, load=True)

    def test_actual_elminster_can_cast_with_zero_models(self):
        game = self.session(60121103)
        spell = self.add(game.engine, "Elminster's Simulacrum", zone="hand")
        game.state.config.semantic_policy = "trusted_only"
        self.assert_closed(game, spell)
        self.prepare(game, mana={"C": 4, "U": 2})
        self.checkpoint(game)
        self.cast(game, "A", spell, [])
        self.assertEqual([], game.state.stack[-1].targets)
        cursor = len(game.state.events)
        self.assertIsNone(self.resolve(game))
        self.assertFalse(self._tokens(game))
        self.assertFalse(self._creations(game, cursor))
        self.assertEqual(0, sum(game.state.players["A"].mana_pool.values()))
        self.assertEqual("graveyard", game.state.cards[spell.object_id].zone)
        self.replay(game, load=True)

    def test_verified_announced_batch_withdrawal_preserves_current_player_creature_batches(self):
        import json

        from common import ROOT
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from quorune.token_copy_recipes import ANNOUNCED_OPPONENT_COPY_BATCH_CAPABILITY
        from scripts.update_capability_evidence import build_index, discover_tests
        from test_controller_creature_keyword_absence import register_card

        capability = ANNOUNCED_OPPONENT_COPY_BATCH_CAPABILITY
        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        next(row for row in raw["capabilities"] if row["id"] == capability).update(
            status="blocked", blockers=["Scoped announced creature copy withdrawal"],
        )
        registry = CapabilityRegistry(raw)
        index = build_index(registry_value=raw, declaration_source=declarations,
                            discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        closure = registry.closure((capability,), profile="commander_review")
        self.assertIn(f"status:{capability}:blocked", closure.blockers)
        self.assertFalse(any("unverified" in blocker for blocker in closure.blockers))
        rejected, _ = register_card(self.db, registry, "Elminster's Simulacrum")
        self.assertFalse(rejected["strict_capability_ready"], rejected)
        retained, _ = register_card(self.db, registry, "Clone Legion")
        self.assertTrue(retained["strict_capability_ready"], retained["blockers"])


    def test_complete_march_keeps_both_targeted_and_overloaded_copy_results(self):
        from unittest.mock import patch

        from quorune.overload_model import OVERLOAD_CAPABILITY
        from quorune.token_copy_recipes import TokenCopyBatchKind, TokenCopyBatchSpec, TokenCopyRecipeSpec
        from test_controller_creature_keyword_absence import register_card

        name = "March of Progress"
        record = self.db.lookup(name, fuzzy=False)
        binding, semantics = register_card(self.db, self.registry, name)
        self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
        self.assertEqual("capability_closed", binding["trust_basis"])
        programs = {program.effects[0]["op"]: program for program in semantics.programs_for_oracle(record.oracle_id)}
        self.assertEqual({"create_token", "create_token_batch"}, set(programs))
        normal = programs["create_token"]
        overloaded = programs["create_token_batch"]
        self.assertEqual(
            {"zones": ["battlefield"], "categories": ["permanent"], "count": 1,
             "types_all": ["artifact", "creature"], "controller_relation": "you"},
            normal.target_schema,
        )
        self.assertIsNone(overloaded.target_schema)
        self.assertEqual(TokenCopyRecipeSpec("target").to_dict(), normal.effects[0]["copy_spec"])
        self.assertEqual(TokenCopyBatchSpec(TokenCopyBatchKind.CONTROLLER_ARTIFACT_CREATURES).to_effect(),
                         overloaded.effects[0])
        for program in programs.values():
            self.assertIn(OVERLOAD_CAPABILITY, program.capability_dependencies)
        with patch("quorune.compiler.token_copy_templates._token_copy_batch_template", return_value=None):
            withheld, _ = register_card(self.db, self.registry, name)
            self.assertFalse(withheld["strict_capability_ready"], withheld)

    def test_actual_march_normal_targets_one_and_overload_copies_current_controlled_artifact_creatures(self):
        from quorune.record import authoritative_state_hash

        for overloaded in (False, True):
            with self.subTest(overloaded=overloaded):
                game = self.session(70296101 + int(overloaded))
                spell = self.add(game.engine, "March of Progress", zone="hand")
                model = self.add(game.engine, "Memnite", ref="march-ordinary")
                shrouded = self.add(game.engine, "Inkwell Leviathan", ref="march-shrouded")
                noncreature = self.add(game.engine, "Avarice Totem", ref="march-artifact")
                self.add(game.engine, "Grizzly Bears", ref="march-creature")
                foreign = self.add(game.engine, "Memnite", seat="B", ref="march-opponent")
                model.tapped = True
                model.counters["+1/+1"] = 2
                game.state.config.semantic_policy = "trusted_only"
                self.assert_closed(game, spell)
                self.assert_closed(game, shrouded)
                self.assertIn("shroud", game.engine._combat_keywords(shrouded))
                self.prepare(game, mana={"U": 1, "C": 6})
                self.checkpoint(game)
                actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
                action, = (row for row in actions if row["id"] == f"cast:{spell.ref}")
                self.assertEqual({"normal", "overload"}, {row["id"] for row in action["cost_options"]})
                command = {"action_id": action["id"], "cost_option": "overload" if overloaded else "normal",
                           "pay": "auto"}
                before = authoritative_state_hash(game.state)
                bad = [model.ref] if overloaded else [shrouded.ref, foreign.ref, noncreature.ref]
                for ref in bad:
                    denied = game.act("pilot:A", {**command, "targets": [ref]})
                    self.assertFalse(denied.ok)
                    self.assertEqual(before, authoritative_state_hash(game.state))
                accepted = game.act("pilot:A", {**command, "targets": [] if overloaded else [model.ref]})
                self.assertTrue(accepted.ok, accepted.summary)
                self.assertEqual([] if overloaded else [model.ref], game.state.stack[-1].targets)
                self.assertEqual(overloaded, game.state.stack[-1].semantic_key.endswith(":overload"))
                self.assertEqual("battlefield", game.state.cards[shrouded.object_id].zone)
                self.assertEqual(0 if overloaded else 4, sum(game.state.players["A"].mana_pool.values()))
                game = self.replay(game, load=True)
                cursor = len(game.state.events)
                self.assertIsNone(self.resolve(game))
                copies = self._tokens(game)
                self.assertCountEqual(["Memnite", "Inkwell Leviathan"] if overloaded else ["Memnite"], [
                    game.engine._effective_card_data(card)["name"] for card in copies
                ])
                self.assertTrue(all(not card.tapped and not card.counters and card.owner == "A" for card in copies))
                creation, = self._creations(game, cursor)
                self.assertCountEqual([card.ref for card in copies], creation.details["objects"])
                self.assertEqual(1, len({card.zone_timestamp for card in copies}))
                self.replay(game, load=True)

    def test_real_copy_of_overloaded_march_uses_its_own_controller_and_keeps_the_mode(self):
        game = self.session(70296103)
        spell = self.add(game.engine, "March of Progress", zone="hand")
        copier = self.add(game.engine, "Reverberate", seat="B", zone="hand")
        self.add(game.engine, "Inkwell Leviathan", ref="march-original-model")
        self.add(game.engine, "Memnite", seat="B", ref="march-spell-copy-model")
        game.state.config.semantic_policy = "trusted_only"
        for card in (spell, copier):
            self.assert_closed(game, card)
        game.state.players["B"].mana_pool.update({"R": 2})
        self.prepare(game, mana={"C": 6, "U": 1})
        self.checkpoint(game)
        accepted = game.act("pilot:A", {
            "action_id": f"cast:{spell.ref}", "cost_option": "overload", "targets": [], "pay": "auto",
        })
        self.assertTrue(accepted.ok, accepted.summary)
        original = game.state.stack[-1]
        self.cast(game, "B", copier, [original.ref])
        self.resolve_top(game)
        copied, = (item for item in game.state.stack if item.kind == "spell_copy")
        self.assertEqual(("B", [], original.semantic_key), (copied.controller, copied.targets, copied.semantic_key))
        self.assertTrue(copied.semantic_key.endswith(":overload"))
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        for seat, name in (("A", "Inkwell Leviathan"), ("B", "Memnite")):
            model, = self._tokens(game, controller=seat)
            self.assertEqual(seat, model.owner)
            self.assertEqual(name, game.engine._effective_card_data(model)["name"])
            self.assertEqual(0, sum(game.state.players[seat].mana_pool.values()))
        self.assertEqual(2, len(game.engine._current_turn_history("spell_cast")))
        self.replay(game, load=True)

    def test_verified_copy_batch_withdrawal_keeps_march_unresolved_as_a_whole(self):
        import json

        from common import ROOT
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from quorune.token_copy_recipes import TOKEN_COPY_BATCH_CAPABILITY
        from scripts.update_capability_evidence import build_index, discover_tests
        from test_controller_creature_keyword_absence import register_card

        capability = TOKEN_COPY_BATCH_CAPABILITY
        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        next(row for row in raw["capabilities"] if row["id"] == capability).update(
            status="blocked", blockers=["Scoped overloaded copy batch withdrawal"],
        )
        registry = CapabilityRegistry(raw)
        index = build_index(registry_value=raw, declaration_source=declarations,
                            discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        closure = registry.closure((capability,), profile="commander_review")
        self.assertIn(f"status:{capability}:blocked", closure.blockers)
        self.assertFalse(any("unverified" in blocker for blocker in closure.blockers))
        rejected, _ = register_card(self.db, registry, "March of Progress")
        self.assertFalse(rejected["strict_capability_ready"], rejected)
        retained, _ = register_card(self.db, registry, "Extravagant Replication")
        self.assertTrue(retained["strict_capability_ready"], retained["blockers"])


RENEWED = "Renewed Solidarity"


class ChosenEntryTokenCopyNativeTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from common import ROOT
        from quorune.carddb import CardDatabase
        from quorune.deck import DeckDefinition, DeckEntry
        from quorune.rules.capabilities import load_default_capability_registry
        from scripts.build_test_database import build_fixture_database

        cls.temporary = tempfile.TemporaryDirectory()
        folder = Path(cls.temporary.name)
        directory = ROOT / "tests/fixtures"
        build_fixture_database([
            directory / name for name in ("token-copy-recipe-cards.json",
                "bound-effect-program-cards.json", "activation-paid-creature-subtype-cards.json",
                "life-gain-counted-result-cards.json", "additional-token-definition-cards.json",
                "cast-creature-targets.json")
        ], folder / "chosen-entry-token-copy.sqlite3")
        cls.db = CardDatabase(folder / "chosen-entry-token-copy.sqlite3")
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Chosen-type token copies", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def test_renewed_exact_same_face_entry_end_step_and_batch(self):
        from dataclasses import replace
        from unittest.mock import patch

        from quorune.oracle_ir import compile_oracle_card
        from quorune.token_copy_recipes import (
            CHOSEN_ENTRY_TOKEN_COPY_CAPABILITY, TokenCopyBatchSpec, uses_chosen_entry_token_copy,
        )
        from test_controller_creature_keyword_absence import register_card

        record = self.db.lookup(RENEWED, fuzzy=False)
        kw = {"capability_registry": self.registry, "capability_profile": "commander_review"}
        ir = compile_oracle_card(record, **kw)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        node, = [node for node in ir.faces[0].nodes
                 if any(uses_chosen_entry_token_copy(effect) for effect in node.effects)]
        self.assertIsNone(node.target_schema)
        self.assertIn(CHOSEN_ENTRY_TOKEN_COPY_CAPABILITY, node.capability_dependencies)
        spec = TokenCopyBatchSpec.from_effect(node.effects[0])
        query = spec.model_query("B", chosen_type="soldier")
        self.assertEqual(("B", True, ("soldier",), ()), (query.controller, query.token, query.subtypes_all, query.types_all))
        self.assertTrue(query.state_predicate.entered_this_turn)
        closure, _ = register_card(self.db, self.registry, RENEWED)
        self.assertTrue(closure["strict_capability_ready"], closure["blockers"])
        for oracle in (
            "\n".join(record.oracle_text.splitlines()[1:]),
            record.oracle_text.replace("your end step", "each end step"),
            record.oracle_text.replace("that entered this turn", "that attacked this turn"),
            record.oracle_text.replace("token you control of the chosen type", "creature you control of the chosen type"),
        ):
            changed = compile_oracle_card(replace(record, oracle_text=oracle), **kw)
            self.assertTrue(changed.material_residuals, oracle)
            self.assertFalse(any(node.exact and any(uses_chosen_entry_token_copy(effect) for effect in node.effects)
                                 for node in changed.faces[0].nodes), oracle)
        with patch("quorune.compiler.token_copy_templates.chosen_entry_token_copy_trigger_template", return_value=None):
            omitted = compile_oracle_card(record, **kw)
        self.assertTrue(omitted.material_residuals)

    def tokens(self, game, seat="A"):
        return {card.object_id: card for card in game.state.cards.values()
                if card.zone == "battlefield" and card.controller == seat and card.is_token}

    def test_renewed_copies_current_eligible_models_after_source_leaves(self):
        from collections import Counter
        from quorune.record import authoritative_state_hash

        game = self.session(70704001)
        source = self.add(game.engine, RENEWED, zone="hand")
        elite = self.add(game.engine, "Elite Vanguard")
        harvest = self.add(game.engine, "Second Harvest", zone="hand")
        growth = self.add(game.engine, "Giant Growth", zone="hand")
        exit_source = self.add(game.engine, "Disenchant", seat="B", zone="hand")
        exit_token = self.add(game.engine, "Unsummon", seat="B", zone="hand")
        for card in (source, elite, harvest, growth, exit_source, exit_token):
            self.assert_closed(game, card)

        game.engine.create_token("A", name="", copy_of=elite.ref)
        for name, subtype in (("Old Soldier", "Soldier"), ("Second Old Soldier", "Soldier"), ("Old Goblin", "Goblin")):
            game.engine.create_token("A", name=name, characteristics={
                "type_line": "Token Creature \u2014 " + subtype, "power": "1", "toughness": "1",
            })
        old_ids = set(self.tokens(game))
        self.assertEqual(4, len(old_ids))
        from quorune.turn_history import reset_turn_history

        game.state.turn_sequence += 1
        game.state.turn_history = reset_turn_history(game.state.turn_history, game.state.turn_sequence)
        self.prepare(game, mana={"W": 4, "C": 20, "G": 8})
        for index, name in enumerate(("Plains", "Plains", "Island")):
            self.add(game.engine, name, seat="B", ref=f"renewed-opponent-mana-{index}")
        game.engine.create_token("B", name="Foreign Soldier", quantity=2, characteristics={
            "type_line": "Token Creature — Soldier", "power": "1", "toughness": "1",
        })
        self.checkpoint(game)
        self.cast(game, "A", source)
        pending = self.resolve(game)
        self.assertEqual(("replacement.order", ["A"]), (pending.kind, pending.actors))
        game = self.replay(game, load=True)
        options = game.packet("pilot:A", full=True)["decision"]["ctx"]["options"]
        option, = [row["id"] for row in options if row["id"].endswith(":soldier")]
        command = {"action_id": "choose", "replacement": option}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:B", command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        accepted = game.act("pilot:A", command)
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual("soldier", game.state.cards[source.object_id].annotations["chosen_creature_type"])

        self.cast(game, "A", harvest)
        self.resolve_top(game)
        new_models = {game.engine.display_name(object_id): card for object_id, card in self.tokens(game).items()
                      if object_id not in old_ids}
        self.assertEqual({"Elite Vanguard", "Old Soldier", "Second Old Soldier", "Old Goblin"}, set(new_models))
        copied_elite = new_models["Elite Vanguard"]
        dropped = new_models["Old Soldier"]
        self.assertEqual(3, game.engine._numeric_stat(copied_elite.object_id, "power"))
        self.cast(game, "A", growth, [copied_elite.ref])
        self.resolve_top(game)
        self.assertEqual(6, game.engine._numeric_stat(copied_elite.object_id, "power"))

        self.until(game, lambda s: self.trigger_count(s, source) == 1)
        self.assertEqual("end_step", game.state.step)
        self.assertEqual(2, len(self.tokens(game, "B")))
        self.cast_for(game, "B", exit_token, [dropped.ref])
        self.resolve_top(game)
        self.assertNotIn(dropped.object_id, self.tokens(game))
        self.cast_for(game, "B", exit_source, [source.ref])
        self.resolve_top(game)
        self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
        self.assertEqual(5, game.engine._numeric_stat(copied_elite.object_id, "power"))
        self.assertEqual(1, self.trigger_count(game, source))

        game = self.replay(game, load=True)
        previous = set(self.tokens(game))
        cursor = len(game.state.events)
        self.assertIsNone(self.resolve(game))
        created = {object_id: card for object_id, card in self.tokens(game).items() if object_id not in previous}
        self.assertEqual(Counter({"Elite Vanguard": 1, "Second Old Soldier": 1}),
                         Counter(game.engine.display_name(object_id) for object_id in created))
        powers = {game.engine.display_name(object_id): game.engine._numeric_stat(object_id, "power")
                  for object_id in created}
        self.assertEqual({"Elite Vanguard": 2, "Second Old Soldier": 1}, powers)
        event, = [row for row in game.state.events[cursor:] if row.code == "token.create"]
        self.assertEqual(2, event.details["base_quantity"])
        self.assertCountEqual([card.ref for card in created.values()], event.details["objects"])
        self.assertEqual(2, len(self.tokens(game, "B")))
        self.replay(game, load=True)
