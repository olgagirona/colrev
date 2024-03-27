#! /usr/bin/env python3
"""CoLRev status stats."""
from __future__ import annotations

import typing
from dataclasses import dataclass
from pathlib import Path

import colrev.process.operation
from colrev.constants import Fields
from colrev.constants import Filepaths
from colrev.constants import OperationsType
from colrev.constants import RecordState
from colrev.loader.bib import BIBLoader
from colrev.process.model import ProcessModel


@dataclass
class StatusStats:
    """Data class for status statistics"""

    # pylint: disable=too-many-instance-attributes
    atomic_steps: int
    nr_curated_records: int
    currently: StatusStatsCurrently
    overall: StatusStatsOverall
    completed_atomic_steps: int
    completeness_condition: bool

    def __init__(
        self,
        *,
        review_manager: colrev.review_manager.ReviewManager,
        records: dict,
    ) -> None:
        self.review_manager = review_manager
        self.records = records

        self.status_list = [x[Fields.STATUS] for x in self.records.values()]
        self.screening_criteria = [
            x[Fields.SCREENING_CRITERIA]
            for x in self.records.values()
            if x.get(Fields.SCREENING_CRITERIA, "") not in ["", "NA"]
        ]

        self.md_duplicates_removed = 0
        for item in self.records.values():
            self.md_duplicates_removed += (
                len([o for o in item[Fields.ORIGIN] if not o.startswith("md_")]) - 1
            )

        origin_list = [x[Fields.ORIGIN] for x in self.records.values()]
        self.nr_origins = 0
        for origin in origin_list:
            self.nr_origins += len([o for o in origin if not o.startswith("md_")])

        criteria = list(review_manager.settings.screen.criteria.keys())
        self.screening_statistics = {crit: 0 for crit in criteria}
        for screening_case in self.screening_criteria:
            for criterion in screening_case.split(";"):
                criterion_name, decision = criterion.split("=")
                if decision == "out":
                    self.screening_statistics[criterion_name] += 1

        self.currently = StatusStatsCurrently(status_stats=self)
        self.overall = StatusStatsOverall(status_stats=self)

        self.completed_atomic_steps = 0
        self.nr_incomplete = 0

        self._overall_stats_backward_calculation()

        self.currently.non_processed = (
            self.currently.md_imported
            + self.currently.md_retrieved
            + self.currently.md_needs_manual_preparation
            + self.currently.md_prepared
        )

        self.currently.md_retrieved = max(
            self.overall.md_retrieved - self.nr_origins, 0
        )

        self.completeness_condition = (
            (0 == self.nr_incomplete)
            and (0 == self.currently.md_retrieved)
            and self.overall.md_retrieved > 0
        )

        self.currently.exclusion = self.screening_statistics

        self.overall.rev_screen = self.overall.pdf_prepared

        self.overall.rev_prescreen = self.overall.md_processed
        self.currently.pdf_needs_retrieval = self.currently.rev_prescreen_included

        self.nr_curated_records = len(
            [
                r
                for r in self.records.values()
                if colrev.record.record.Record(r).masterdata_is_curated()
            ]
        )

        if review_manager.settings.is_curated_masterdata_repo():
            self.nr_curated_records = self.overall.md_processed

        self.atomic_steps = (
            # initially, all records have to pass 8 operations
            8 * self.overall.md_retrieved
            # for removed duplicates, 5 operations are no longer needed
            - 5 * self.currently.md_duplicates_removed
            # for rev_prescreen_excluded, 4 operations are no longer needed
            - 4 * self.currently.rev_prescreen_excluded
            - 3 * self.currently.pdf_not_available
            - self.currently.rev_excluded
        )

        self.perc_curated = 0
        denominator = (
            self.overall.md_processed
            + self.currently.md_prepared
            + self.currently.md_needs_manual_preparation
            + self.currently.md_imported
        )

        if denominator > 0:
            self.perc_curated = int((self.nr_curated_records / (denominator)) * 100)

    def _overall_stats_backward_calculation(self) -> None:
        """Calculate the state_x overall stats (based on backward calculation)"""
        # self.review_manager.logger.debug(
        #     "Set overall colrev_status statistics (going backwards)"
        # )
        visited_states = []
        current_state = RecordState.rev_synthesized  # start with the last
        atomic_step_number = 0
        while True:
            # self.review_manager.logger.debug(
            #     "current_state: %s with %s",
            #     current_state,
            #     getattr(self.overall, str(current_state)),
            # )
            if RecordState.md_prepared == current_state:
                overall_md_prepared = (
                    getattr(self.overall, str(current_state))
                    + self.md_duplicates_removed
                )
                getattr(self.overall, str(current_state), overall_md_prepared)

            states_to_consider = [current_state]
            predecessors: list[dict[str, typing.Any]] = [
                {
                    "trigger": "init",
                    "source": RecordState.md_imported,
                    "dest": RecordState.md_imported,
                }
            ]
            # Go backward through the process model
            predecessor = None
            while predecessors:
                predecessors = [
                    t
                    for t in ProcessModel.transitions
                    if t["source"] in states_to_consider
                    and t["dest"] not in visited_states
                ]
                for predecessor in predecessors:
                    # self.review_manager.logger.debug(
                    #     " add %s from %s (predecessor transition: %s)",
                    #     getattr(self.overall, str(predecessor["dest"])),
                    #     str(predecessor["dest"]),
                    #     predecessor["trigger"],
                    # )
                    setattr(
                        self.overall,
                        str(current_state),
                        (
                            getattr(self.overall, str(current_state))
                            + getattr(self.overall, str(predecessor["dest"]))
                        ),
                    )
                    visited_states.append(predecessor["dest"])
                    if predecessor["dest"] not in states_to_consider:
                        states_to_consider.append(predecessor["dest"])
                if len(predecessors) > 0:
                    if predecessors[0]["trigger"] != "init":
                        # ignore _man versions to avoid double-counting:
                        if (
                            predecessors[0]["trigger"]
                            not in OperationsType.get_manual_extra_operations()
                        ):
                            self.completed_atomic_steps += getattr(
                                self.overall, str(predecessor["dest"])
                            )
                        # Note : load is not a predecessor so we need to
                        # correct for a missing step (same number like prep)
                        if predecessors[0]["trigger"] == "prep":
                            self.completed_atomic_steps += getattr(
                                self.overall, str(predecessor["dest"])
                            )

            atomic_step_number += 1
            # Note : the following does not consider multiple parallel steps.
            for trans_for_completeness in [
                t for t in ProcessModel.transitions if current_state == t["dest"]
            ]:
                self.nr_incomplete += getattr(
                    self.currently, str(trans_for_completeness["source"])
                )

            t_list = [t for t in ProcessModel.transitions if current_state == t["dest"]]
            transition: dict = t_list.pop()
            if current_state == RecordState.md_imported:
                break
            current_state = transition["source"]  # go a step back
            self.currently.non_completed += getattr(self.currently, str(current_state))

    def get_active_metadata_operation_info(self) -> str:
        """Get active metadata operation info (convenience function for status printing)"""
        infos = []
        if self.currently.md_retrieved > 0:
            infos.append(f"{self.currently.md_retrieved} to load")
        if self.currently.md_imported > 0:
            infos.append(f"{self.currently.md_imported} to prepare")
        if self.currently.md_needs_manual_preparation > 0:
            infos.append(
                f"{self.currently.md_needs_manual_preparation} to prepare manually"
            )
        if self.currently.md_prepared > 0:
            infos.append(f"{self.currently.md_prepared} to deduplicate")
        return ", ".join(infos)

    def get_active_pdf_operation_info(self) -> str:
        """Get active PDF operation info (convenience function for status printing)"""
        infos = []
        if self.currently.rev_prescreen_included > 0:
            infos.append(f"{self.currently.rev_prescreen_included} to retrieve")
        if self.currently.pdf_needs_manual_retrieval > 0:
            infos.append(
                f"{self.currently.pdf_needs_manual_retrieval} to retrieve manually"
            )
        if self.currently.pdf_imported > 0:
            infos.append(f"{self.currently.pdf_imported} to prepare")
        if self.currently.pdf_needs_manual_preparation > 0:
            infos.append(
                f"{self.currently.pdf_needs_manual_preparation} to prepare manually"
            )
        return ", ".join(infos)

    def get_transitioned_records(
        self, current_origin_states_dict: dict
    ) -> list[typing.Dict]:
        """Get the transitioned records"""

        committed_origin_states_dict = (
            self.review_manager.dataset.get_committed_origin_state_dict()
        )
        transitioned_records = []
        for (
            committed_origin,
            committed_colrev_status,
        ) in committed_origin_states_dict.items():
            transitioned_record = {
                "origin": committed_origin,
                "source": committed_colrev_status,
                "dest": current_origin_states_dict.get(
                    committed_origin, "no_source_state"
                ),
            }

            operations_type = [
                x["trigger"]
                for x in ProcessModel.transitions
                if x["source"] == transitioned_record["source"]
                and x["dest"] == transitioned_record["dest"]
            ]
            if (
                len(operations_type) == 0
                and transitioned_record["source"] != transitioned_record["dest"]
            ):
                transitioned_record["operations_type"] = "invalid_transition"

            if len(operations_type) > 0:
                transitioned_record["operations_type"] = operations_type[0]
                transitioned_records.append(transitioned_record)

        return transitioned_records

    def get_priority_operations(self, *, current_origin_states_dict: dict) -> list:
        """Get the priority operations"""

        # get "earliest" states (going backward)
        earliest_state = []
        search_states = [RecordState.rev_synthesized]
        while True:
            if any(
                search_state in current_origin_states_dict.values()
                for search_state in search_states
            ):
                earliest_state = [
                    search_state
                    for search_state in search_states
                    if search_state in current_origin_states_dict.values()
                ]
            search_states = [
                x["source"]  # type: ignore
                for x in ProcessModel.transitions
                if x["dest"] in search_states
            ]
            if [] == search_states:
                break
        # print(f'earliest_state: {earliest_state}')

        # next: get the priority transition for the earliest states
        priority_transitions = [
            x["trigger"]
            for x in ProcessModel.transitions
            if x["source"] in earliest_state
        ]

        priority_operations = list(set(priority_transitions))

        self.review_manager.logger.debug(f"priority_operations: {priority_operations}")
        return priority_operations

    def get_active_operations(self, *, current_origin_states_dict: dict) -> list:
        """Get the active processing functions"""

        active_operations: typing.List[str] = []
        for state in set(current_origin_states_dict.values()):
            valid_transitions = ProcessModel.get_valid_transitions(state=state)
            active_operations.extend(valid_transitions)

        self.review_manager.logger.debug(f"active_operations: {set(active_operations)}")
        return active_operations

    def get_operation_in_progress(self, *, transitioned_records: list) -> list:
        """Get the operation currently in progress"""

        in_progress_operation = list(
            {x["operations_type"] for x in transitioned_records}
        )
        self.review_manager.logger.debug(
            f"in_progress_operation: {in_progress_operation}"
        )
        return in_progress_operation


@dataclass
class StatusStatsParent:
    """Parent class for StatusStatsCurrently and StatusStatsOverall"""

    # pylint: disable=too-many-instance-attributes
    # Note : StatusStatsCurrently and StatusStatsOverall start with the same frequencies
    def __init__(
        self,
        *,
        status_stats: StatusStats,
    ) -> None:
        self.status_stats = status_stats

        self.md_retrieved = self._get_freq(RecordState.md_retrieved)

        self.md_imported = self._get_freq(RecordState.md_imported)
        self.md_needs_manual_preparation = self._get_freq(
            RecordState.md_needs_manual_preparation
        )
        self.md_prepared = self._get_freq(RecordState.md_prepared)
        self.md_processed = self._get_freq(RecordState.md_processed)
        self.rev_prescreen_excluded = self._get_freq(RecordState.rev_prescreen_excluded)
        self.rev_prescreen_included = self._get_freq(RecordState.rev_prescreen_included)
        self.pdf_needs_manual_retrieval = self._get_freq(
            RecordState.pdf_needs_manual_retrieval
        )
        self.pdf_imported = self._get_freq(RecordState.pdf_imported)
        self.pdf_not_available = self._get_freq(RecordState.pdf_not_available)
        self.pdf_needs_manual_preparation = self._get_freq(
            RecordState.pdf_needs_manual_preparation
        )
        self.pdf_prepared = self._get_freq(RecordState.pdf_prepared)
        self.rev_excluded = self._get_freq(RecordState.rev_excluded)
        self.rev_included = self._get_freq(RecordState.rev_included)
        self.rev_synthesized = self._get_freq(RecordState.rev_synthesized)
        self.md_duplicates_removed = self.status_stats.md_duplicates_removed

    def _get_freq(self, colrev_status: RecordState) -> int:
        return len([x for x in self.status_stats.status_list if colrev_status == x])


@dataclass
class StatusStatsCurrently(StatusStatsParent):
    """The current status statistics"""

    # pylint: disable=too-many-instance-attributes
    md_retrieved: int
    md_imported: int
    md_prepared: int
    md_needs_manual_preparation: int
    md_duplicates_removed: int
    md_processed: int
    non_processed: int
    rev_prescreen_excluded: int
    rev_prescreen_included: int
    pdf_needs_retrieval: int
    pdf_needs_manual_retrieval: int
    pdf_not_available: int
    pdf_imported: int
    pdf_needs_manual_preparation: int
    pdf_prepared: int
    rev_excluded: int
    rev_included: int
    rev_synthesized: int
    non_completed: int
    exclusion: dict

    def __init__(
        self,
        *,
        status_stats: StatusStats,
    ) -> None:
        self.exclusion: typing.Dict[str, int] = {}
        self.non_completed = 0
        self.non_processed = 0
        super().__init__(status_stats=status_stats)
        self.pdf_needs_retrieval = self.rev_prescreen_included


@dataclass
class StatusStatsOverall(StatusStatsParent):
    """The overall-status statistics (records currently/previously in each state)"""

    # pylint: disable=too-many-instance-attributes
    md_retrieved: int
    md_imported: int
    md_needs_manual_preparation: int
    md_prepared: int
    md_processed: int
    rev_prescreen: int
    rev_prescreen_excluded: int
    rev_prescreen_included: int
    pdf_needs_manual_retrieval: int
    pdf_imported: int
    pdf_not_available: int
    pdf_needs_manual_preparation: int
    pdf_prepared: int
    rev_excluded: int
    rev_included: int
    rev_screen: int
    rev_synthesized: int

    def __init__(
        self,
        *,
        status_stats: StatusStats,
    ) -> None:
        self.rev_screen = 0
        self.rev_prescreen = 0
        super().__init__(status_stats=status_stats)
        search_dir = self.status_stats.review_manager.get_path(Filepaths.SEARCH_DIR)
        self.md_retrieved = self._get_nr_search(search_dir=search_dir)

    def _get_nr_search(self, *, search_dir: Path) -> int:
        if not search_dir.is_dir():
            return 0
        bib_files = search_dir.glob("*.bib")
        number_search = 0
        for search_file in bib_files:
            # Note : skip md-prep sources
            if str(search_file.name).startswith("md_"):
                continue

            # TODO : incomplete (only covers bib files?!)
            bib_loader = BIBLoader(
                filename=search_file,
                logger=self.status_stats.review_manager.logger,
                unique_id_field="ID",
            )

            number_search += bib_loader.get_nr_in_bib()

        return number_search
