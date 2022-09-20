#! /usr/bin/env python
"""Deduplication based on active learning (dedupe-io)"""
from __future__ import annotations

import os
import sqlite3
import statistics
import typing
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import dedupe as dedupe_io
import pandas as pd
import psutil
import zope.interface
from dacite import from_dict
from dedupe._typing import RecordDictPair as TrainingExample
from dedupe._typing import TrainingData
from dedupe.core import unique

import colrev.env.package_manager
import colrev.exceptions as colrev_exceptions
import colrev.ops.built_in.dedupe.utils
import colrev.ops.built_in.pdf_prep.metadata_valiation
import colrev.record
import colrev.ui_cli.cli_colors as colors

if TYPE_CHECKING:
    import colrev.ops.dedupe

# pylint: disable=too-few-public-methods


@zope.interface.implementer(colrev.env.package_manager.DedupePackageInterface)
class ActiveLearningDedupeTraining:
    """Active learning: training phase (minimum sample size of 50 required)"""

    settings_class = colrev.env.package_manager.DefaultSettings

    deduper: dedupe_io.Deduper

    # Code based on
    # https://github.com/dedupeio/dedupe-examples/blob/master/csv_example/csv_example.py

    def __init__(
        self,
        *,
        dedupe_operation: colrev.ops.dedupe.Dedupe,  # pylint: disable=unused-argument
        settings: dict,
    ):
        self.settings = from_dict(data_class=self.settings_class, data=settings)

    def __setup_active_learning_dedupe(
        self,
        *,
        dedupe_operation: colrev.ops.dedupe.Dedupe,
        retrain: bool,
        in_memory: bool,
    ) -> None:
        """Prepare data for active learning setup"""
        # pylint: disable=import-outside-toplevel
        import random
        import logging

        logging.getLogger("opensearch").setLevel(logging.ERROR)
        logging.getLogger("dedupe.training").setLevel(logging.WARNING)
        logging.getLogger("dedupe.api").setLevel(logging.WARNING)

        if retrain:
            # Note : removing the training_file would be to start from scratch...
            # self.training_file.unlink(missing_ok=True)
            dedupe_operation.settings_file.unlink(missing_ok=True)

        dedupe_operation.review_manager.logger.info("Importing data ...")

        # Possible extension: in the read_data, we may want to append the colrev_status
        # to use Gazetteer (dedupe_io) if applicable (no duplicates in pos-md_processed)

        data_d = dedupe_operation.read_data()

        # to address memory issues, we select a sample from data_d
        # and feed it to prepare_training:
        # https://github.com/dedupeio/dedupe/issues/920

        if not in_memory:
            # Note: we have to make sure that when we sample for training,
            # the not-in-memory mode is used for duplicate clustering
            # otherwise, non-sampled duplicates will not be identified
            max_training_sample_size = min(3000, len(list(data_d.keys())))
            dedupe_operation.review_manager.logger.info(
                f"Selecting a random sample of {max_training_sample_size}"
                " to avoid memory problems"
            )
            # TODO : consider similar proportions of post-md_processed/md_prepared?
            keys = random.sample(list(data_d.keys()), max_training_sample_size)
            data_d = {key: data_d[key] for key in keys}

        dedupe_operation.review_manager.logger.debug(
            dedupe_operation.review_manager.p_printer.pformat(data_d)
        )

        # def title_corpus():
        #     for record in data_d.values():
        #         yield record["title"]

        # def container_corpus():
        #     for record in data_d.values():
        #         yield record["container_title"]

        # def author_corpus():
        #     for record in data_d.values():
        #         yield record["author"]

        # Training

        # TODO : creating a corpus from all fields may create memory issues...

        # Define the fields dedupe will pay attention to
        fields = [
            {
                "field": "author",
                "type": "String",
                # "corpus": author_corpus(),k
                "has missing": True,
                "crf": True,
            },
            {
                "field": "title",
                "type": "String",
                #  "corpus": title_corpus()
                "crf": True,
            },
            {
                "field": "container_title",
                "variable name": "container_title",
                "type": "ShortString",
                # "corpus": container_corpus(),
                "crf": True,
            },
            {"field": "year", "variable name": "year", "type": "DateTime"},
            {
                "field": "volume",
                "variable name": "volume",
                "type": "ShortString",
                "has missing": True,
            },
            {
                "field": "number",
                "variable name": "number",
                "type": "ShortString",
                "has missing": True,
            },
            {
                "field": "pages",
                "type": "ShortString",
                "has missing": True,
                "crf": True,
            },
            {
                "type": "Interaction",
                "interaction variables": [
                    "container_title",
                    "year",
                    "volume",
                    "number",
                ],
            },
        ]
        # Interactions:
        # https://docs.dedupe.io/en/latest/Variable-definition.html

        # Create a new deduper object and pass our data model to it.
        self.deduper = dedupe_io.Dedupe(fields)

        # If we have training data saved from a previous run of dedupe,
        # look for it and load it in.
        # __Note:__ if you want to train from scratch, delete the training_file

        if len(data_d) < 50:
            raise colrev_exceptions.DedupeError(
                "Sample size too small for active learning. "
                "Use simple_dedupe instead:\n"
                f"{colors.ORANGE}  colrev settings -m 'dedupe.scripts="
                f'[{{"endpoint":"simple_dedupe"}}]\'{colors.END}'
            )

        if dedupe_operation.training_file.is_file():
            dedupe_operation.review_manager.logger.info(
                "Reading pre-labeled training data from "
                f"{dedupe_operation.training_file.name} "
                "and preparing data"
            )
            with open(dedupe_operation.training_file, "rb") as file:
                self.deduper.prepare_training(data_d, file)
        else:
            self.deduper.prepare_training(data_d)

        # TODO  input('del data_d - check memory')
        del data_d

        dedupe_operation.review_manager.logger.info(
            "Reading and preparation completed."
        )

    def __apply_active_learning(
        self,
        *,
        dedupe_operation: colrev.ops.dedupe.Dedupe,
        results: list,
        saved_args: dict,
    ) -> None:

        dedupe_operation.apply_merges(results=results, complete_dedupe=False)

        # Using the examples we just labeled, train the deduper and learn
        # blocking predicates
        self.deduper.train(recall=0.9, index_predicates=True)
        # print(self.deduper.data_model._field_comparators)
        # print(self.deduper.predicates)

        # When finished, save our training to disk
        with open(dedupe_operation.training_file, "w", encoding="utf-8") as train_file:
            self.deduper.write_training(train_file)
        dedupe_operation.review_manager.dataset.add_changes(
            path=dedupe_operation.training_file
        )

        # Save our weights and predicates to disk.  If the settings file
        # exists, we will skip all the training and learning next time we run
        # this file.
        with open(dedupe_operation.settings_file, "wb") as sett_file:
            self.deduper.write_settings(sett_file)

        dedupe_operation.review_manager.create_commit(
            msg="Labeling of duplicates (active learning)",
            manual_author=True,
            script_call="colrev dedupe",
            saved_args=saved_args,
        )
        # self.cleanup_training()

    def __adapted_console_label(
        self,
        *,
        dedupe_operation: colrev.ops.dedupe.Dedupe,
        manual: bool,
        saved_args: dict,
        max_associations_to_check: int = 1000,
    ) -> None:
        """
        Train a matcher instance (Dedupe, RecordLink, or Gazetteer) from the cli.
        Example

        .. code:: python

        > deduper = dedupe.Dedupe(variables)
        > deduper.prepare_training(data)
        > dedupe.console_label(deduper)
        """

        # pylint: disable=too-many-branches
        # pylint: disable=too-many-statements
        # pylint: disable=too-many-locals

        dedupe_operation.review_manager.logger.info(
            "Note: duplicate associations available in the LocalIndex "
            "are applied automatically."
        )
        dedupe_operation.review_manager.logger.info("Press Enter to start.")
        input()

        local_index = dedupe_operation.review_manager.get_local_index()
        finished, use_previous = False, False

        keys = unique(
            field.field for field in self.deduper.data_model.primary_variables
        )

        buffer_len = 1  # Max number of previous operations
        examples_buffer: list[
            tuple[TrainingExample, typing.Literal["match", "distinct", "uncertain"]]
        ] = []
        uncertain_pairs: list[TrainingExample] = []

        manual_dedupe_decision_list = []

        while not finished:

            if use_previous:
                record_pair, _ = examples_buffer.pop(0)
                use_previous = False
            else:
                try:
                    if not uncertain_pairs:
                        uncertain_pairs = self.deduper.uncertain_pairs()

                    record_pair = uncertain_pairs.pop()
                except IndexError:
                    break

            n_match = len(self.deduper.training_pairs["match"]) + sum(
                label == "match" for _, label in examples_buffer
            )
            n_distinct = len(self.deduper.training_pairs["distinct"]) + sum(
                label == "distinct" for _, label in examples_buffer
            )
            if (n_match + n_distinct) > max_associations_to_check:
                finished = True

            user_input = "u"
            if (
                record_pair[0]["colrev_id"] == record_pair[1]["colrev_id"]
                # if any of the colrev_ids NA,
                # we don't know whether we have a duplicate.
                and "NA" != record_pair[0]["colrev_id"]
                and "NA" != record_pair[1]["colrev_id"]
            ):
                user_input = "y"
            else:
                # Check local_index for duplicate information
                index_dupe_info = local_index.is_duplicate(
                    record1_colrev_id=record_pair[0]["colrev_id"].split(";"),
                    record2_colrev_id=record_pair[1]["colrev_id"].split(";"),
                )

                user_input = (
                    colrev.ops.built_in.dedupe.utils.console_duplicate_instance_label(
                        record_pair,
                        keys,
                        manual,
                        index_dupe_info,
                        n_match,
                        n_distinct,
                        examples_buffer,
                    )
                )

            if user_input == "y":
                manual_dedupe_decision_list.append(
                    {
                        "ID1": record_pair[0]["ID"],
                        "ID2": record_pair[1]["ID"],
                        "decision": "duplicate",
                    }
                )
                examples_buffer.insert(0, (record_pair, "match"))
                msg = (
                    f"Marked as duplicate: {record_pair[0]['ID']} - "
                    + f"{record_pair[1]['ID']}"
                )
                dedupe_operation.review_manager.report_logger.info(msg)

            elif user_input == "n":
                if not manual:
                    # Ensure that non-dupes do not exceed 3x dupes
                    # (for balanced training data)
                    if n_distinct > n_match * 3:
                        examples_buffer.insert(0, (record_pair, "uncertain"))
                        continue

                manual_dedupe_decision_list.append(
                    {
                        "ID1": record_pair[0]["ID"],
                        "ID2": record_pair[1]["ID"],
                        "decision": "no_duplicate",
                    }
                )
                examples_buffer.insert(0, (record_pair, "distinct"))
                msg = (
                    f"Marked as non-duplicate: {record_pair[0]['ID']}"
                    + f" - {record_pair[1]['ID']}"
                )
                dedupe_operation.review_manager.report_logger.info(msg)

            elif user_input == "u":
                examples_buffer.insert(0, (record_pair, "uncertain"))
            elif user_input == "f":
                os.system("cls" if os.name == "nt" else "clear")
                print("Finished labeling")
                finished = True
            elif user_input == "p":
                use_previous = True
                uncertain_pairs.append(record_pair)

            if len(examples_buffer) > buffer_len:
                record_pair, label = examples_buffer.pop()
                if label in {"distinct", "match"}:
                    examples: TrainingData = {"distinct": [], "match": []}
                    examples[label].append(record_pair)
                    self.deduper.mark_pairs(examples)

        for record_pair, label in examples_buffer:
            if label in ["distinct", "match"]:
                examples = {"distinct": [], "match": []}
                examples[label].append(record_pair)
                self.deduper.mark_pairs(examples)

        # Note : for debugging:
        # import csv
        # keys = manual_dedupe_decision_list[0].keys()
        # with open("manual_dedupe_decision_list.csv", "w", newline="") as output_file:
        #     dict_writer = csv.DictWriter(output_file, keys)
        #     dict_writer.writeheader()
        #     dict_writer.writerows(manual_dedupe_decision_list)

        # Apply and commit
        self.__apply_active_learning(
            dedupe_operation=dedupe_operation,
            results=manual_dedupe_decision_list,
            saved_args=saved_args,
        )

    def run_dedupe(self, dedupe_operation: colrev.ops.dedupe.Dedupe) -> None:

        saved_args: dict = {}
        in_memory = True

        self.__setup_active_learning_dedupe(
            dedupe_operation=dedupe_operation, retrain=False, in_memory=in_memory
        )

        dedupe_io.console_label = self.__adapted_console_label
        dedupe_io.console_label(
            dedupe_operation=dedupe_operation, manual=True, saved_args=saved_args
        )


@zope.interface.implementer(colrev.env.package_manager.DedupePackageInterface)
class ActiveLearningDedupeAutomated:
    """Applies trained (active learning) model"""

    @dataclass
    class ActiveLearningSettings:
        name: str
        merge_threshold: float = 0.8
        partition_threshold: float = 0.5

        _details = {
            "merge_threshold": {"tooltip": "Threshold for merging record pairs"},
            "partition_threshold": {"tooltip": "Threshold for partitioning"},
        }

    settings_class = ActiveLearningSettings

    def __init__(
        self,
        *,
        dedupe_operation: colrev.ops.dedupe.Dedupe,  # pylint: disable=unused-argument
        settings: dict,
    ):

        self.settings = from_dict(data_class=self.settings_class, data=settings)

        assert self.settings.merge_threshold >= 0.0
        assert self.settings.merge_threshold <= 1.0
        assert self.settings.partition_threshold >= 0.0
        assert self.settings.partition_threshold <= 1.0

    def __get_duplicates_from_clusters(
        self, *, dedupe_operation: colrev.ops.dedupe.Dedupe, clustered_dupes: list
    ) -> list[dict]:
        dedupe_operation.review_manager.report_logger.info(
            f"set merge_threshold: {self.settings.merge_threshold}"
        )
        dedupe_operation.review_manager.logger.info(
            f"set merge_threshold: {self.settings.merge_threshold}"
        )
        results = []
        dedupe_decision_list = []
        for cluster_id, (records, scores) in enumerate(clustered_dupes):
            dedupe_decision_list.append(
                {
                    "cluster_id": cluster_id,
                    "records": list(records),
                    "score": statistics.mean(list(scores)),
                }
            )

        for dedupe_decision in dedupe_decision_list:

            if len(dedupe_decision["records"]) == 0:
                continue

            if dedupe_decision["score"] < self.settings.merge_threshold:
                continue

            orig_rec = dedupe_decision["records"].pop()
            if 0 == len(dedupe_decision["records"]):
                results.append(
                    {
                        "ID1": orig_rec,
                        "decision": "no_duplicate",
                    }
                )
                continue

            for dupe_rec in dedupe_decision["records"]:

                orig_propagated = dedupe_operation.review_manager.dataset.propagated_id(
                    record_id=orig_rec
                )
                dupe_propagated = dedupe_operation.review_manager.dataset.propagated_id(
                    record_id=dupe_rec
                )

                if not orig_propagated and not dupe_propagated:

                    # Use the record['ID'] without appended letters if possible
                    # Set orig_propagated=True if record_a_ID should be kept
                    if orig_rec[-1:].isnumeric() and not dupe_rec[-1:].isnumeric():
                        orig_propagated = True
                    else:
                        dupe_propagated = True
                        # This arbitrarily uses record_b_ID
                        # if none of the IDs has a letter appended.

                    if orig_propagated and dupe_propagated:
                        # both_IDs_propagated
                        dedupe_operation.review_manager.logger.error(
                            f"Both IDs propagated: {orig_rec}, {dupe_rec}"
                        )
                        continue

                    if orig_propagated:
                        results.append(
                            {
                                "ID1": orig_rec,
                                "ID2": dupe_rec,
                                "decision": "duplicate",
                                "score": dedupe_decision["score"],
                            }
                        )

                    else:
                        results.append(
                            {
                                "ID1": dupe_rec,
                                "ID2": orig_rec,
                                "decision": "duplicate",
                                "score": dedupe_decision["score"],
                            }
                        )
        return results

    def __highlight_cells(self, *, input_df):
        dataframe = input_df.copy()
        dataframe["cluster_id"] = dataframe["cluster_id"].astype(str)
        dataframe.loc[:, dataframe.columns != "cluster_id"] = "background-color: white"

        # http://www.excelsupersite.com/what-are-the-56-colorindex-colors-in-excel/
        available_colors = [
            "#FFFFFF",
            "#FFCC99",
            "#FFFFCC",
            "#CCFFCC",
            "#FFFF99",
            "#99CCFF",
            "#FF99CC",
        ]
        cur_color_index = -1
        cur_cluster = ""

        prev_row = {}
        for i, row in dataframe.iterrows():
            if row["cluster_id"] != cur_cluster:
                cur_color_index += 1
                cur_cluster = row["cluster_id"]
            # dataframe.at[i, 'cluster_id'] = ( # only the cluster_id column
            dataframe.at[i, :] = (
                "background-color: "
                + available_colors[cur_color_index % len(available_colors)]
            )

        for i, row in input_df.iterrows():
            if i in [0, 1]:
                continue
            if len(prev_row) != 0:
                for j, val in row.items():
                    # changes in these fields should not be marked
                    if j in ["error", "confidence_score", "ID"]:
                        continue
                    # do not mark changes between different clusters
                    if j == "cluster_id" and prev_row["cluster_id"] != val:
                        break
                    if val != prev_row[j]:
                        dataframe.at[i, j] = dataframe.at[i, j] + "; font-weight: bold"

            prev_row = row

        return dataframe

    def __export_duplicates_excel(
        self, *, dedupe_operation: colrev.ops.dedupe.Dedupe, collected_duplicates: list
    ) -> None:
        if len(collected_duplicates) == 0:
            print("No duplicates found")
            return

        duplicates_df = pd.DataFrame.from_records(collected_duplicates)
        duplicates_df.fillna("", inplace=True)
        duplicates_df["distinct_str"] = (
            duplicates_df["author"]
            + duplicates_df["title"]
            + duplicates_df["year"]
            + duplicates_df["container_title"]
            + duplicates_df["volume"]
            + duplicates_df["number"]
            + duplicates_df["pages"]
        )
        # Only export bibliographically distict cases
        duplicates_df = duplicates_df.groupby("distinct_str").filter(
            lambda x: len(x) == 1
        )
        duplicates_df.drop(columns=["distinct_str"], inplace=True)

        duplicates_df = duplicates_df[
            [
                "error",
                "confidence_score",
                "cluster_id",
                "ID",
                "author",
                "title",
                "year",
                "container_title",
                "volume",
                "number",
                "pages",
            ]
        ]

        duplicates_df = duplicates_df.groupby("cluster_id").filter(lambda x: len(x) > 1)
        duplicates_df = duplicates_df.sort_values(
            ["confidence_score", "cluster_id"], ascending=(True, False)
        )
        duplicates_df["confidence_score"] = duplicates_df["confidence_score"].round(4)
        # to adjust column widths in ExcelWriter:
        # http://pandas-docs.github.io/pandas-docs-travis/user_guide/style.html
        duplicates_df = duplicates_df.style.apply(self.__highlight_cells, axis=None)
        duplicates_df.to_excel(dedupe_operation.dupe_file, index=False)

    def __export_non_duplicates_excel(
        self,
        *,
        dedupe_operation: colrev.ops.dedupe.Dedupe,
        collected_non_duplicates: list,
    ) -> None:
        if len(collected_non_duplicates) == 0:
            print("No duplicates.")
            return

        non_duplicates_df = pd.DataFrame.from_records(collected_non_duplicates)
        # To develop in jupyter:
        # non_duplicates_df.to_csv(output_file, index=False)
        # non_duplicates_df = pd.read_csv("duplicates_for_validation.csv")
        non_duplicates_df = non_duplicates_df[
            [
                "error",
                "cluster_id",
                "confidence_score",
                "ID",
                "author",
                "title",
                "year",
                "container_title",
                "volume",
                "number",
                "pages",
            ]
        ]
        non_duplicates_df = non_duplicates_df.groupby("cluster_id").filter(
            lambda x: len(x) > 1
        )
        non_duplicates_df = non_duplicates_df.sort_values(
            ["confidence_score", "cluster_id"], ascending=(True, False)
        )
        non_duplicates_df["confidence_score"] = non_duplicates_df[
            "confidence_score"
        ].round(4)
        # to adjust column widths in ExcelWriter:
        # http://pandas-docs.github.io/pandas-docs-travis/user_guide/style.html
        non_duplicates_df = non_duplicates_df.style.apply(
            self.__highlight_cells, axis=None
        )
        non_duplicates_df.to_excel(dedupe_operation.non_dupe_file_xlsx, index=False)

    def __get_collected_dupes_non_dupes_from_clusters(
        self, *, clustered_dupes: list, data_d: dict
    ) -> dict:

        # pylint: disable=too-many-locals

        cluster_membership = {}
        # cluster_membership:
        # {'FrolovaFrolovKayurovEtAl2021': {'Cluster ID': 352, 'confidence_score': 1.0},
        #  'BhaskaraBawa2021': {'Cluster ID': 353, 'confidence_score': 1.0}}
        for cluster_id, (records, scores) in enumerate(clustered_dupes):
            for record_id, score in zip(records, scores):

                cluster_membership[record_id] = {
                    "cluster_id": cluster_id,
                    "confidence_score": score,
                }

        results: typing.Dict[str, list] = {
            "collected_duplicates": [],
            "collected_non_duplicates": [],
        }
        for cluster_id, vals in data_d.items():
            vals.update(error="")
            if cluster_id in cluster_membership:
                cur_cluster_membership = cluster_membership[cluster_id]
                vals.update(cur_cluster_membership)
                if (
                    cur_cluster_membership["confidence_score"]
                    > self.settings.merge_threshold
                ):
                    results["collected_duplicates"].append(vals)
                else:
                    results["collected_non_duplicates"].append(vals)

        # Set confidence scores to average of group
        for cluster_nr in {d["cluster_id"] for d in results["collected_duplicates"]}:
            avg_confidence = statistics.mean(
                [
                    d["confidence_score"]
                    for d in results["collected_duplicates"]
                    if d["cluster_id"] == cluster_nr
                ]
            )
            for collected_duplicate in results["collected_duplicates"]:
                if collected_duplicate["cluster_id"] == cluster_nr:
                    collected_duplicate["confidence_score"] = avg_confidence
        for cluster_nr in {
            d["cluster_id"] for d in results["collected_non_duplicates"]
        }:
            avg_confidence = statistics.mean(
                [
                    d["confidence_score"]
                    for d in results["collected_non_duplicates"]
                    if d["cluster_id"] == cluster_nr
                ]
            )
            for collected_non_duplicate in results["collected_non_duplicates"]:
                if collected_non_duplicate["cluster_id"] == cluster_nr:
                    collected_non_duplicate["confidence_score"] = avg_confidence

        return results

    def __export_validation_excel(
        self,
        *,
        dedupe_operation: colrev.ops.dedupe.Dedupe,
        clustered_dupes: list,
        data_d: dict,
    ) -> None:

        results = self.__get_collected_dupes_non_dupes_from_clusters(
            clustered_dupes=clustered_dupes, data_d=data_d
        )

        self.__export_duplicates_excel(
            dedupe_operation=dedupe_operation,
            collected_duplicates=results["collected_duplicates"],
        )

        self.__export_non_duplicates_excel(
            dedupe_operation=dedupe_operation,
            collected_non_duplicates=results["collected_non_duplicates"],
        )

    def __cluster_duplicates(
        self, *, dedupe_operation: colrev.ops.dedupe.Dedupe, data_d: dict
    ) -> list:

        # pylint: disable=too-many-locals

        dedupe_operation.review_manager.logger.info("Clustering duplicates...")
        dedupe_operation.review_manager.logger.info(
            f"Number of records (before): {len(data_d.items())}"
        )

        # Setting in-memory mode depending on system RAM

        record_state_list = (
            dedupe_operation.review_manager.dataset.get_record_state_list()
        )
        sample_size = len(record_state_list)

        ram = psutil.virtual_memory().total
        in_memory = sample_size * 5000000 < ram

        with open(dedupe_operation.settings_file, "rb") as sett_file:
            deduper = dedupe_io.StaticDedupe(sett_file, num_cores=4)

        # `partition` will return sets of records that dedupe
        # believes are all referring to the same entity.

        if in_memory:
            dedupe_operation.review_manager.report_logger.info(
                f"set partition_threshold: {self.settings.partition_threshold}"
            )

            clustered_dupes = deduper.partition(
                data_d, self.settings.partition_threshold
            )

            # from dedupe.core import BlockingError
            # except BlockingError:

            #     dedupe_operation.review_manager.logger.info(
            #         "No duplicates found (please check carefully)"
            #     )
            #     dedupe_operation.apply_merges(results=[], complete_dedupe=True)
            #     dedupe_operation.review_manager.create_commit(
            #         msg="Merge duplicate records (no duplicates detected)",
            #         script_call="colrev dedupe",
            #         saved_args=saved_args,
            #     )
            #     dedupe_operation.review_manager.logger.info(
            #         "If there are errors, it could be necessary to remove the "
            #         ".records_dedupe_training.json to train a fresh dedupe model."
            #     )

            #     pass
            # except KeyboardInterrupt:
            #     print("KeyboardInterrupt")
            #     pass

        else:

            for field in deduper.fingerprinter.index_fields:
                field_data = (r[field] for r in data_d.values() if field in r)
                deduper.fingerprinter.index(field_data, field)

            full_data = ((r["ID"], r) for r in data_d.values())

            # pylint: disable=not-callable
            # fingerprinter is callable according to
            # https://github.com/dedupeio/dedupe/blob/
            # b9d8f111bcd5ffd177659f79f57354d9a9318359/dedupe/blocking.py
            b_data = deduper.fingerprinter(full_data)

            # use sqlite: light-weight, file-based
            # https://docs.python.org/3/library/sqlite3.html
            # https://dedupeio.github.io/dedupe-examples/docs/pgsql_big_dedupe_example.html

            dedupe_db = Path("dedupe.db")
            dedupe_db.unlink(missing_ok=True)
            con = sqlite3.connect(str(dedupe_db))

            cur = con.cursor()

            cur.execute("""DROP TABLE IF EXISTS blocking_map""")
            cur.execute("""CREATE TABLE blocking_map (block_key text, ID INTEGER)""")
            cur.executemany("""INSERT into blocking_map values (?, ?)""", b_data)

            records_data = {r["ID"]: r for r in data_d.values()}

            def record_pairs(result_set):

                for row in result_set:
                    id_a, id_b = row
                    record_a = (id_a, records_data[id_a])
                    record_b = (id_b, records_data[id_b])

                    yield record_a, record_b

            cur.execute(
                """select DISTINCT l.ID as east, r.ID as west
                        from blocking_map as l
                        INNER JOIN blocking_map as r
                        using (block_key)
                        where east != west"""
            )

            clustered_dupes = list(
                deduper.cluster(
                    deduper.score(record_pairs(cur.fetchall())), threshold=0.5
                )
            )

            # import csv
            # clusterin_results_csv = Path("clusterin_results.csv")
            # clusterin_results_csv.unlink(missing_ok=True)
            # with open(clusterin_results_csv, "w") as out:
            #     csv_out = csv.writer(out)
            #     csv_out.writerow(["ID1", "ID2", "conf"])
            #     for row in list(cluster_ids(clustered_dupes)):
            #         if row[0] != row[1]:  # only focus on non-identical IDs
            #             csv_out.writerow(row)

            con.commit()
            con.close()
            dedupe_db.unlink(missing_ok=True)

        dedupe_operation.review_manager.report_logger.info(
            f"Number of duplicate sets {len(clustered_dupes)}"
        )
        return clustered_dupes

    def run_dedupe(self, dedupe_operation: colrev.ops.dedupe.Dedupe) -> None:
        """Cluster potential duplicates, merge, and export validation spreadsheets"""

        saved_args: dict = {}
        saved_args.update(merge_threshold=str(self.settings.merge_threshold))
        saved_args.update(partition_threshold=str(self.settings.partition_threshold))

        data_d = dedupe_operation.read_data()

        clustered_dupes = self.__cluster_duplicates(
            dedupe_operation=dedupe_operation, data_d=data_d
        )

        results = self.__get_duplicates_from_clusters(
            dedupe_operation=dedupe_operation, clustered_dupes=clustered_dupes
        )

        dedupe_operation.apply_merges(results=results, complete_dedupe=True)

        self.__export_validation_excel(
            dedupe_operation=dedupe_operation,
            clustered_dupes=clustered_dupes,
            data_d=data_d,
        )

        dedupe_operation.review_manager.create_commit(
            msg="Merge duplicate records (based on active-learning clusters)",
            script_call="colrev dedupe",
            saved_args=saved_args,
        )

        dedupe_operation.review_manager.logger.info(
            "Successfully completed the deduplication. Please check the "
            "duplicates_to_validate.xlsx and non_duplicates_to_validate.xlsx for "
            'potential errors.\nTo fix them, mark them in the "error" column and '
            "run\n  colrev dedupe --fix_errors\n\n"
        )

        if Path("same_source_merges.txt").is_file():
            dedupe_operation.review_manager.logger.info(
                "Detected and prevented same-source merges. Please check potential"
                "duplicates in same_source_merges.txt"
            )

        info = dedupe_operation.get_info()
        if len(info["same_source_merges"]) > 0:
            dedupe_operation.review_manager.logger.info(
                f"\n{colors.ORANGE}Same source merges to check:{colors.END}"
                "\n- ".join(info["same_source_merges"]) + "\n"
            )
        else:
            dedupe_operation.review_manager.logger.info(
                "\nNo same-origin merges detected."
            )


if __name__ == "__main__":
    pass