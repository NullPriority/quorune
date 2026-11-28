"""Characteristic memoization is a pure read frame, never a state revision cache."""

import unittest
import gc
import weakref
from types import SimpleNamespace
from unittest.mock import patch

from quorune.characteristic_read_frames import characteristic_read_frame, memoized_characteristic_read


class CharacteristicReadFrameTests(unittest.TestCase):
    def test_zone_replacement_subject_reads_current_data_only_without_a_supplied_snapshot(self):
        from quorune.characteristic_evaluation import type_parts
        from quorune.semantic_runtime.zone_replacement_inputs import zone_change_subject_characteristics

        live = {"type_line": "Artifact", "colors": []}
        supplied = {"type_line": "Creature", "colors": ["B"]}
        card = SimpleNamespace(object_id="entrant", zone="hand", owner="A")
        host = SimpleNamespace(_effective_card_data=lambda card: live, _type_parts=type_parts)
        with patch.object(host, "_effective_card_data", wraps=host._effective_card_data) as read:
            snapshot, _, card_types, _, _ = zone_change_subject_characteristics(
                host, card, "graveyard", {card.object_id: supplied}, {},
            )
            self.assertEqual({"creature"}, card_types)
            self.assertEqual(supplied, snapshot)
            self.assertIsNot(supplied, snapshot)
            empty, *_ = zone_change_subject_characteristics(
                host, card, "graveyard", {card.object_id: {}}, {},
            )
            self.assertEqual({}, empty)
            read.assert_not_called()

            current, _, card_types, _, _ = zone_change_subject_characteristics(host, card, "graveyard", {}, {})
            self.assertEqual({"artifact"}, card_types)
            self.assertEqual(live, current)
            read.assert_called_once_with(card)

    def test_each_mana_step_shares_validation_reads_before_its_own_mutation(self):
        from quorune.characteristic_evaluation import type_parts
        from quorune.mana_activation import complete_mana_plan_activations

        host = SimpleNamespace()
        card = SimpleNamespace(zone="battlefield", ref="source", object_id="source", tapped=False)
        host._resolve_object = lambda *args, **kwargs: card
        host._type_parts = type_parts
        source_types = ["Snow Land"]
        reads = []
        committed_snow_flags = []

        def compute():
            reads.append(source_types[0])
            return {"type_line": source_types[0]}

        def read_card(_card):
            return memoized_characteristic_read(host, ("fixture-mana-source", card.object_id), compute)

        def modes(*args):
            self.assertEqual(source_types[0], read_card(card)["type_line"])
            return ()

        def commit(_host, _seat, _card, _mode, _bundle, _restriction, snow_source, **_kwargs):
            committed_snow_flags.append(snow_source)
            source_types[0] = "Land"

        host._effective_card_data = read_card
        mode = SimpleNamespace(price=None)
        with (
            patch("quorune.mana_activation._mana_plan_modes", side_effect=modes),
            patch("quorune.mana_activation._selected_plan_mode", return_value=(mode, None)),
            patch("quorune.mana_activation._commit_plan_mode", side_effect=commit),
        ):
            step = {"source": card.ref, "bundle": {"C": 1}}
            complete_mana_plan_activations(host, "A", [step, step])

        self.assertEqual(["Snow Land", "Land"], reads)
        self.assertEqual([True, False], committed_snow_flags)

    def test_priority_catalog_reuses_only_its_current_pure_read_and_ends_before_mutation(self):
        import quorune.rules.action_catalog as catalog
        host=SimpleNamespace(state=SimpleNamespace());value={'power':1};calls=[]
        def read():calls.append(value['power']);return dict(value)
        def offers(*_args):
            first=memoized_characteristic_read(host,'current-characteristic',read)
            second=memoized_characteristic_read(host,'current-characteristic',read)
            self.assertEqual(first,second);return [],[],[]
        def abilities(*_args):offers();return [],[],[],[]
        with patch.object(catalog,'_cast_offers',side_effect=offers),patch.object(catalog,'_land_offers',return_value=([],[])),\
                patch.object(catalog,'_ability_offers',side_effect=abilities),patch.object(catalog,'_turn_face_up_offers',return_value=([],[])),\
                patch.object(catalog,'_suspend_offers',return_value=([],[])),patch.object(catalog,'_staged_cast_offers',return_value=([],[])),\
                patch.object(catalog,'priority_actions_with_mana_undo',return_value=[]),patch.object(catalog,'_concede_offer',return_value={'id':'concede'}):
            expected=catalog.build_priority_action_catalog.__wrapped__(host,'A');self.assertEqual([1,1,1,1],calls)
            calls.clear();self.assertEqual(expected,catalog.build_priority_action_catalog(host,'A'));self.assertEqual([1],calls)
            value['power']=4;catalog.build_priority_action_catalog(host,'A');self.assertEqual([1,4],calls)

    def test_oracle_program_reads_keep_order_and_observe_registry_changes(self):
        import json
        import tempfile
        from pathlib import Path
        from quorune.semantics import SemanticProgram, SemanticRegistry

        registry = SemanticRegistry(include_builtin_packs=False)
        host = SimpleNamespace(semantics=registry)
        oracle = "fixture-oracle"
        first = registry.put(SemanticProgram(key="z", label="First", oracle_id=oracle, ability_id="fixture:oracle-read-first"))
        second = registry.put(SemanticProgram(key="a", label="Second", oracle_id=oracle, ability_id="fixture:oracle-read-second"))

        class CountedPrograms(dict):
            scans = 0

            def values(self):
                self.scans += 1
                return super().values()

        programs = CountedPrograms(registry._programs)
        registry._programs = programs
        with characteristic_read_frame(host):
            result = registry.programs_for_oracle(oracle)
            self.assertEqual([first, second], result)
            result.clear()
            self.assertEqual([first, second], registry.programs_for_oracle(oracle, active_zone="stack"))
            self.assertEqual(1, programs.scans)
            first.event = "fixture_event"
            self.assertEqual([first], registry.programs_for_oracle(oracle, event="fixture_event"))
            self.assertEqual([], registry.programs_for_oracle([]))
            with characteristic_read_frame(object()):
                registry.remove(first.key)
            self.assertEqual([second], registry.programs_for_oracle(oracle))
            replacement = registry.put(SemanticProgram(key="a", label="Replacement", oracle_id=oracle, ability_id="fixture:oracle-read-second"))
            self.assertIs(replacement, registry.programs_for_oracle(oracle)[0])
            with tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / "programs.json"
                path.write_text(json.dumps({"schema_version": 3, "programs": [first.to_dict()]}), encoding="utf-8")
                registry.load_packs([path])
                self.assertEqual([replacement, first], registry.programs_for_oracle(oracle))
                registry.path = path
                path.write_text(json.dumps({"include_builtin_packs": False, "programs": {"z": first.to_dict()}}), encoding="utf-8")
                registry.load()
                self.assertEqual([first], registry.programs_for_oracle(oracle))
        loaded = registry.get("z")
        self.assertIsNotNone(loaded)
        loaded.oracle_id = "other-oracle"
        self.assertEqual([], registry.programs_for_oracle(oracle))
        self.assertEqual([loaded], registry.programs_for_oracle("other-oracle"))

    def test_card_rows_do_not_share_metadata_or_survive_connection_changes(self):
        import tempfile
        from pathlib import Path
        from common import ROOT
        from quorune.carddb import CardDatabase
        from scripts.build_test_database import build_fixture_database

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "cards.sqlite3"
            build_fixture_database([ROOT / "tests/fixtures/per-player-trigger-composition-cards.json"], path)
            with CardDatabase(path) as database:
                def read():
                    return database.by_oracle_id("00000000-0000-4000-8000-000000005414")

                queries = []
                def trace(sql):
                    if sql.startswith("SELECT * FROM cards WHERE oracle_id"):
                        queries.append(sql)

                database.connection.set_trace_callback(trace)
                host = SimpleNamespace(card_db=database)
                with characteristic_read_frame(host):
                    first = read()
                    first.raw["fixture_field"] = ["edited"]
                    first.legalities["fixture_format"] = "edited"
                    second = read()
                    self.assertEqual(1, len(queries))
                    self.assertIsNot(first, second)
                    self.assertNotIn("fixture_field", second.raw)
                    self.assertNotIn("fixture_format", second.legalities)
                    database.connection.execute(
                        "UPDATE cards SET name = ? WHERE oracle_id = ?",
                        ("Temporary name", second.oracle_id),
                    )
                    self.assertEqual("Temporary name", read().name)
                    database.connection.rollback()
                    self.assertEqual(second.name, read().name)
                    database.connection.execute(
                        "UPDATE cards SET name = ? WHERE oracle_id = ?",
                        ("Current name", second.oracle_id),
                    )
                    database.connection.commit()
                    self.assertEqual("Current name", read().name)
                    database.close()
                    database.connection.set_trace_callback(trace)
                    reads_before = len(queries)
                    self.assertEqual("Current name", read().name)
                    self.assertEqual(reads_before + 1, len(queries))
                reads_before = len(queries)
                read()
                read()
                self.assertEqual(reads_before + 2, len(queries))

    def test_prospective_identity_cannot_be_reused_inside_a_frame_and_is_released_afterward(self):
        class Prospective:
            pass
        host=object()
        with characteristic_read_frame(host):
            prospective=Prospective();reference=weakref.ref(prospective)
            key=('card',id(prospective))
            memoized_characteristic_read(host,key,lambda:{'controller':'A'},identity=prospective)
            del prospective;gc.collect()
            self.assertIsNotNone(reference())
            other=Prospective()
            self.assertNotEqual(key,('card',id(other)))
        gc.collect();self.assertIsNone(reference())

    def test_only_completed_nested_reads_share_results_and_return_independent_values(self):
        host=object();calls=[]
        def read():
            calls.append('read');return {'keywords':['flying']}
        with characteristic_read_frame(host):
            first=memoized_characteristic_read(host,('card',1),read)
            first['keywords'].append('haste')
            second=memoized_characteristic_read(host,('card',1),read)
            self.assertEqual(['flying'],second['keywords'])
            second['keywords'].clear()
            self.assertEqual(['flying'],memoized_characteristic_read(host,('card',1),read)['keywords'])
        self.assertEqual(['read'],calls)

    def test_top_level_reads_recompute_after_mutation_and_hosts_never_share_frames(self):
        first=object();second=object();state={'power':1};calls=[]
        def read():calls.append(state['power']);return dict(state)
        self.assertEqual(1,memoized_characteristic_read(first,'same',read)['power'])
        state['power']=4
        self.assertEqual(4,memoized_characteristic_read(first,'same',read)['power'])
        with characteristic_read_frame(first):
            self.assertEqual(4,memoized_characteristic_read(first,'same',read)['power'])
            with characteristic_read_frame(second):
                self.assertEqual(9,memoized_characteristic_read(second,'same',lambda:{'power':9})['power'])
            self.assertEqual(4,memoized_characteristic_read(first,'same',lambda:self.fail('Outer host frame lost'))['power'])
        self.assertEqual([1,4,4],calls)

    def test_failed_and_reentrant_reads_do_not_publish_provisional_values(self):
        host=object();calls=[]
        def outer():
            calls.append('outer')
            inner=memoized_characteristic_read(host,'recursive',lambda:{'value':'inner'})
            self.assertEqual('inner',inner['value']);return {'value':'complete'}
        with characteristic_read_frame(host):
            self.assertEqual('complete',memoized_characteristic_read(host,'recursive',outer)['value'])
            self.assertEqual('complete',memoized_characteristic_read(host,'recursive',lambda:self.fail('Completed value lost'))['value'])
            with self.assertRaisesRegex(ValueError,'failed'):
                memoized_characteristic_read(host,'failure',lambda:(_ for _ in ()).throw(ValueError('failed')))
            self.assertEqual({'value':'recovered'},memoized_characteristic_read(host,'failure',lambda:{'value':'recovered'}))
        self.assertEqual(['outer'],calls)
        self.assertEqual({'value':'new-root'},memoized_characteristic_read(host,'recursive',lambda:{'value':'new-root'}))

    def test_layer_flags_and_prospective_object_identities_are_independent_keys(self):
        host=object();live=object();prospective=object()
        with characteristic_read_frame(host):
            keys=(('card',id(live),False,False,5,True),('card',id(live),False,False,None,True),
                  ('card',id(live),True,False,5,True),('card',id(live),False,True,5,True),
                  ('card',id(live),False,False,5,False),('card',id(prospective),False,False,5,True))
            for index,key in enumerate(keys):self.assertEqual(index,memoized_characteristic_read(host,key,lambda value=index:value))
            for index,key in enumerate(keys):self.assertEqual(index,memoized_characteristic_read(host,key,lambda:self.fail('Distinct read key lost')))


if __name__=='__main__':unittest.main()
