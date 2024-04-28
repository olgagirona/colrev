#! /usr/bin/env python
"""CoLRev prep operation: Prepare record metadata."""
from __future__ import annotations

import inspect
import logging
import multiprocessing as mp
import random
import time
import typing
from copy import deepcopy
from datetime import datetime
from datetime import timedelta
from multiprocessing import Lock
from multiprocessing import Value
from multiprocessing.pool import ThreadPool as Pool
from pathlib import Path

from requests.exceptions import ConnectionError as requests_ConnectionError
from requests.exceptions import ReadTimeout

import colrev.env.utils
import colrev.exceptions as colrev_exceptions
import colrev.process.operation
import colrev.record.record_prep
from colrev.constants import Colors
from colrev.constants import DefectCodes
from colrev.constants import EndpointType
from colrev.constants import Fields
from colrev.constants import FieldSet
from colrev.constants import OperationsType
from colrev.constants import RecordState
from colrev.writer.write_utils import to_string
from colrev.writer.write_utils import write_file

# pylint: disable=too-many-lines

# logging.getLogger("urllib3").setLevel(logging.ERROR)
logging.getLogger("requests_cache").setLevel(logging.ERROR)

PREP_COUNTER = Value("i", 0)


class PreparationBreak(Exception):
    """Event interrupting the preparation."""


# pylint: disable=duplicate-code
FIELDS_TO_KEEP = FieldSet.STANDARDIZED_FIELD_KEYS + [
    Fields.DBLP_KEY,
    Fields.SEMANTIC_SCHOLAR_ID,
    Fields.WEB_OF_SCIENCE_ID,
    Fields.EDITION,
]


# pylint: disable=too-many-instance-attributes
class Prep(colrev.process.operation.Operation):
    """Prepare records (metadata)"""

    timeout = 30
    max_retries_on_error = 3
    pad: int = 0

    first_round: bool
    last_round: bool

    debug_mode: bool = False
    polish: bool = False

    prep_package_endpoints: dict[str, typing.Any]

    _cpu = 1
    _prep_commit_id = "HEAD"

    type = OperationsType.prep

    def __init__(
        self,
        *,
        review_manager: colrev.review_manager.ReviewManager,
        notify_state_transition_operation: bool = True,
    ) -> None:
        super().__init__(
            review_manager=review_manager,
            operations_type=self.type,
            notify_state_transition_operation=notify_state_transition_operation,
        )
        self.notify_state_transition_operation = notify_state_transition_operation

        self.fields_to_keep = (
            FIELDS_TO_KEEP + self.review_manager.settings.prep.fields_to_keep
        )

        self.quality_model = review_manager.get_qm()

        self._stats: typing.Dict[str, typing.List[timedelta]] = {}

        self.temp_prep_lock = Lock()
        self.current_temp_records = self.review_manager.get_path(
            Path(".colrev/cur_temp_recs.bib")
        )
        self.temp_records = self.review_manager.get_path(Path(".colrev/temp_recs.bib"))
        self.quality_model = review_manager.get_qm()

    def _add_stats(
        self, *, prep_round_package_endpoint: dict, start_time: datetime
    ) -> None:
        if prep_round_package_endpoint["endpoint"] not in self._stats:
            self._stats[prep_round_package_endpoint["endpoint"]] = [
                datetime.now() - start_time
            ]
        else:
            self._stats[prep_round_package_endpoint["endpoint"]].append(
                datetime.now() - start_time
            )

    def _print_stats(self) -> None:
        if self.review_manager.verbose_mode:
            print("Runtime statistics (averages)")
            averaged_list = [
                {
                    "script": script,
                    "average": sum(deltalist, timedelta(0)) / len(deltalist),
                }
                for script, deltalist in self._stats.items()
            ]
            for item in sorted(
                averaged_list,
                key=lambda k: k["average"],  # type: ignore
                reverse=True,
            ):
                average_time_str = (
                    f"{item['average'].seconds}."  # type: ignore
                    f"{item['average'].microseconds}"  # type: ignore
                )
                average_time = float(average_time_str)
                average_time = round(average_time, 2)
                average_time_str = f"{average_time:.2f}"
                print(
                    f"{item['script']} ".ljust(50, " ")
                    + ":"
                    + f"{average_time_str} s".rjust(10, " ")
                )
            print()

    def _print_diffs_for_debug(
        self,
        *,
        prior: colrev.record.record_prep.PrepRecord,
        preparation_record: colrev.record.record_prep.PrepRecord,
        prep_package_endpoint: colrev.package_manager.interfaces.PrepInterface,
    ) -> None:
        if not self.debug_mode:
            return

        diffs = prior.get_diff(preparation_record)
        if diffs:
            change_report = (
                f"{prep_package_endpoint}"
                f" on {preparation_record.data[Fields.ID]}"
                " changed:\n"
                f"{Colors.ORANGE}{self.review_manager.p_printer.pformat(diffs)}{Colors.END}\n"
            )

            self.review_manager.logger.info(change_report)
            self.review_manager.logger.info(
                "To correct errors in the endpoint,"
                " open an issue at "
                "https://github.com/CoLRev-Environment/colrev/issues"
            )
            self.review_manager.logger.info(
                "To correct potential errors at source,"
                f" {prep_package_endpoint.source_correction_hint}"
            )
            input("Press Enter to continue")
            print("\n")
        else:
            self.review_manager.logger.debug(
                f"{prep_package_endpoint}"
                f" on {preparation_record.data[Fields.ID]}"
                " changed: -"
            )
            print("\n")
            time.sleep(0.1)

    def _package_prep(
        self,
        prep_round_package_endpoint: dict,
        record: colrev.record.record_prep.PrepRecord,
        preparation_record: colrev.record.record_prep.PrepRecord,
    ) -> None:
        try:
            if (
                prep_round_package_endpoint["endpoint"].lower()
                not in self.prep_package_endpoints
            ):
                return
            endpoint = self.prep_package_endpoints[
                prep_round_package_endpoint["endpoint"].lower()
            ]

            if self.debug_mode:
                self.review_manager.logger.info(
                    f"{endpoint.settings.endpoint}(...) called"
                )

            prior = preparation_record.copy_prep_rec()

            start_time = datetime.now()
            preparation_record = endpoint.prepare(preparation_record)
            self._add_stats(
                start_time=start_time,
                prep_round_package_endpoint=prep_round_package_endpoint,
            )

            self._print_diffs_for_debug(
                prior=prior,
                preparation_record=preparation_record,
                prep_package_endpoint=endpoint,
            )

            if endpoint.always_apply_changes:
                record.update_by_record(preparation_record)

            if self._preparation_save_condition(preparation_record):
                record.update_by_record(preparation_record)

            if (
                self._preparation_break_condition(preparation_record)
                and not self.polish
            ):
                record.update_by_record(preparation_record)
                raise PreparationBreak
        except ReadTimeout:
            self._add_stats(
                start_time=start_time,
                prep_round_package_endpoint=prep_round_package_endpoint,
            )
            if self.review_manager.verbose_mode:
                self.review_manager.logger.error(
                    f" {Colors.RED}{record.data['ID']}".ljust(45)
                    + f"{endpoint.settings.endpoint}(...) timed out{Colors.END}{Colors.END}"
                )

        except colrev_exceptions.ServiceNotAvailableException as exc:
            if self.review_manager.force_mode:
                self._add_stats(
                    start_time=start_time,
                    prep_round_package_endpoint=prep_round_package_endpoint,
                )
                self.review_manager.logger.error(exc)
            else:
                raise exc

    def _print_post_package_prep_polish_info(
        self,
        *,
        record: colrev.record.record_prep.PrepRecord,
        prior_state: RecordState,
        progress: str,
    ) -> None:
        # records in post_md_prepared remain in that state (in polish mode)
        if (
            record.data[Fields.STATUS]
            in RecordState.get_post_x_states(state=RecordState.md_prepared)
            and prior_state != RecordState.md_needs_manual_preparation
        ) or (
            prior_state == RecordState.md_needs_manual_preparation
            and record.data[Fields.STATUS] == RecordState.md_needs_manual_preparation
        ):
            self.review_manager.logger.info(
                f" {record.data['ID']}".ljust(41) + f"{progress} - "
            )
        elif record.data[Fields.STATUS] == RecordState.rev_prescreen_excluded:
            self.review_manager.logger.info(
                f" {Colors.RED}{record.data['ID']}".ljust(46)
                + f"{progress}{prior_state} →  {record.data['colrev_status']}"
                + f"{Colors.END}"
            )
        elif record.data[Fields.STATUS] == RecordState.md_needs_manual_preparation:
            self.review_manager.logger.info(
                f" {Colors.ORANGE}{record.data['ID']}".ljust(46)
                + f"{progress}{prior_state} →  {record.data['colrev_status']}{Colors.END}"
            )
        elif record.data[Fields.STATUS] == RecordState.md_prepared:
            curation_addition = "   "
            if record.masterdata_is_curated():
                curation_addition = " ✔ "
            self.review_manager.logger.info(
                f" {Colors.GREEN}{record.data['ID']}".ljust(46)
                + f"{progress}{prior_state} →  "
                f"{record.data['colrev_status']}{Colors.END}{curation_addition}"
            )

        elif record.data[Fields.STATUS] == RecordState.md_needs_manual_preparation:
            self.review_manager.logger.info(
                f" {Colors.ORANGE}{record.data['ID']}".ljust(46)
                + f"{progress}{prior_state} →  {record.data['colrev_status']}{Colors.END}"
            )

    def _preparation_break_condition(
        self, record: colrev.record.record_prep.PrepRecord
    ) -> bool:
        """Check whether the break condition for the prep operation is given"""

        if DefectCodes.RECORD_NOT_IN_TOC in record.get_field_provenance_notes(
            Fields.JOURNAL
        ):
            return True
        if DefectCodes.RECORD_NOT_IN_TOC in record.get_field_provenance_notes(
            Fields.BOOKTITLE
        ):
            return True

        if record.data.get(Fields.STATUS, "NA") in [
            RecordState.rev_prescreen_excluded,
        ]:
            return True
        return False

    def _preparation_save_condition(
        self, record: colrev.record.record_prep.PrepRecord
    ) -> bool:
        """Check whether the save condition for the prep operation is given"""

        if record.data[Fields.STATUS] in [
            RecordState.rev_prescreen_excluded,
            RecordState.md_prepared,
        ]:
            return True

        if DefectCodes.RECORD_NOT_IN_TOC in record.get_field_provenance_notes(
            Fields.JOURNAL
        ):
            return True
        if DefectCodes.RECORD_NOT_IN_TOC in record.get_field_provenance_notes(
            Fields.BOOKTITLE
        ):
            return True

        return False

    def _status_to_prepare(self, record: colrev.record.record_prep.PrepRecord) -> bool:
        """Check whether the record needs to be prepared"""
        return record.data.get(Fields.STATUS, "NA") in [
            RecordState.md_needs_manual_preparation,
            RecordState.md_imported,
            RecordState.md_prepared,
        ]

    def _print_post_package_prep_info(
        self,
        record: colrev.record.record_prep.PrepRecord,
        item: dict,
        prior_state: RecordState,
    ) -> None:
        # pylint: disable=redefined-outer-name,invalid-name
        with PREP_COUNTER.get_lock():
            PREP_COUNTER.value += 1  # type: ignore
        progress = ""
        if item["nr_items"] > 100:
            progress = f"({PREP_COUNTER.value}/{item['nr_items']}) ".rjust(  # type: ignore
                12, " "
            )

        if self.polish:
            self._print_post_package_prep_polish_info(
                record=record, prior_state=prior_state, progress=progress
            )
        else:
            if self._preparation_break_condition(record):
                if RecordState.rev_prescreen_excluded == record.data[Fields.STATUS]:
                    if self.review_manager.verbose_mode:
                        self.review_manager.logger.info(
                            f" {Colors.RED}{record.data['ID']}".ljust(46)
                            + f"Detected: {record.data.get('prescreen_exclusion', 'NA')}"
                            + f"{Colors.END}"
                        )
                    target_state = RecordState.rev_prescreen_excluded
                    self.review_manager.logger.info(
                        f" {Colors.RED}{record.data['ID']}".ljust(46)
                        + f"{progress}{prior_state} →  {target_state}"
                        + f"{Colors.END}"
                    )
                else:
                    target_state = RecordState.md_needs_manual_preparation
                    self.review_manager.logger.info(
                        f" {Colors.ORANGE}{record.data['ID']}".ljust(46)
                        + f"{progress}{prior_state} →  {target_state}{Colors.END}"
                    )

            elif self._preparation_save_condition(record):
                curation_addition = "   "
                if record.masterdata_is_curated():
                    curation_addition = " ✔ "
                target_state = RecordState.md_prepared
                self.review_manager.logger.info(
                    f" {Colors.GREEN}{record.data['ID']}".ljust(46)
                    + f"{progress}{prior_state} →  {target_state}{Colors.END}{curation_addition}"
                )
            else:
                target_state = RecordState.md_needs_manual_preparation
                self.review_manager.logger.info(
                    f" {Colors.ORANGE}{record.data['ID']}".ljust(46)
                    + f"{progress}{prior_state} →  {target_state}{Colors.END}"
                )

    def _post_package_prep(
        self,
        record: colrev.record.record_prep.PrepRecord,
        preparation_record: colrev.record.record_prep.PrepRecord,
        item: dict,
        prior_state: RecordState,
    ) -> None:
        if self.last_round and not self.polish:
            if self._status_to_prepare(record):
                for key in list(record.data.keys()):
                    if key not in self.fields_to_keep:
                        record.remove_field(key=key)
                    elif record.data[key] in ["", "NA"]:
                        record.remove_field(key=key)
                record.update_by_record(preparation_record)

        # Note: run_quality_model sets to md_needs_manual_preparation
        record.run_quality_model(self.quality_model, set_prepared=not self.polish)

        if not self.review_manager.verbose_mode:
            self._print_post_package_prep_info(
                record=record, item=item, prior_state=prior_state
            )

    def _save_to_temp(self, record: colrev.record.record_prep.PrepRecord) -> None:
        rec_str = to_string(
            records_dict={record.data[Fields.ID]: record.get_data()},
            implementation="bib",
        )
        self.temp_prep_lock.acquire(timeout=120)
        self.current_temp_records.parent.mkdir(exist_ok=True)
        with open(self.current_temp_records, "a", encoding="utf-8") as cur_temp_rec:
            cur_temp_rec.write(rec_str)
        try:
            self.temp_prep_lock.release()
        except ValueError:
            pass

    def _complete_resumed_operation(self, prepared_records: list) -> None:
        if self.temp_records.is_file():
            temp_recs = colrev.loader.load_utils.load(
                filename=self.temp_records,
                logger=self.review_manager.logger,
            )
            prepared_records_ids = [x[Fields.ID] for x in prepared_records]
            for record in temp_recs.values():
                if record[Fields.ID] not in prepared_records_ids:
                    prepared_records.append(record)

        self.temp_records.unlink(missing_ok=True)
        self.current_temp_records.unlink(missing_ok=True)

    def _validate_record(
        self,
        *,
        record: colrev.record.record_prep.PrepRecord,
        prep_round_package_endpoint: str,
    ) -> None:
        if Fields.STATUS not in record.data:
            print(record.data)
            raise ValueError(
                f"Record {record.data['ID']} has no colrev_status"
                f" after {prep_round_package_endpoint}"
            )
        if not self.polish and record.data[Fields.STATUS] not in [
            RecordState.md_imported,
            RecordState.md_prepared,
            RecordState.md_needs_manual_preparation,
            RecordState.rev_prescreen_excluded,
        ]:
            print(record.data)
            raise ValueError(
                f"Record {record.data['ID']} has invalid status {record.data['colrev_status']}"
                f" after {prep_round_package_endpoint}"
            )
        if Fields.MD_PROV not in record.data:
            raise ValueError(
                f"Record {record.data['ID']} has no Fields.MD_PROV"
                f" after {prep_round_package_endpoint}"
            )
        if Fields.ID not in record.data:
            raise ValueError(
                f"Record {record.data['ID']} has no ID"
                f" after {prep_round_package_endpoint}"
            )
        if Fields.ENTRYTYPE not in record.data:
            raise ValueError(
                f"Record {record.data['ID']} has no ENTRYTYPE"
                f" after {prep_round_package_endpoint}"
            )

    # Note : no named arguments for multiprocessing
    def prepare(self, item: dict) -> dict:
        """Prepare a record (based on package_endpoints in the settings)"""

        # https://docs.python.org/3/library/concurrent.futures.html
        # #concurrent.futures.Executor.map
        # Exceptions are raised at the end/when results are retrieved from the iterator

        record: colrev.record.record_prep.PrepRecord = item["record"]

        if not self._status_to_prepare(record) and not self.polish:
            return record.get_data()

        if self.review_manager.verbose_mode:
            self.review_manager.logger.info(" prep " + record.data[Fields.ID])

        record.require_prov()
        # preparation_record changes with each endpoint and
        # eventually replaces record (if md_prepared or endpoint.always_apply_changes)
        preparation_record = record.copy_prep_rec()
        prior_state = record.data[Fields.STATUS]

        # Rerun quality model (in case there are manual prep changes)
        preparation_record.change_entrytype(
            new_entrytype=record.data[Fields.ENTRYTYPE], qm=self.quality_model
        )
        preparation_record.run_quality_model(
            self.quality_model, set_prepared=not self.polish
        )

        for prep_round_package_endpoint in deepcopy(
            item["prep_round_package_endpoints"]
        ):
            try:
                self._package_prep(
                    prep_round_package_endpoint,
                    record,
                    preparation_record,
                )
                self._validate_record(
                    record=record,
                    prep_round_package_endpoint=prep_round_package_endpoint,
                )
                # Note: ServiceNotAvailableException should be ignored
                # in the packages if review_manager.force_mode
            except PreparationBreak:
                break

        self._post_package_prep(
            record=record,
            preparation_record=preparation_record,
            item=item,
            prior_state=prior_state,
        )

        self._save_to_temp(record)

        return record.get_data()

    def set_ids(self) -> None:
        """Set IDs (regenerate). In force-mode, all IDs are regenerated and PDFs are renamed"""
        self.review_manager.logger.info("Set IDs")
        records = self.review_manager.dataset.load_records_dict()
        records = self.review_manager.dataset.set_ids()
        for record_dict in records.values():
            if Fields.FILE not in record_dict:
                continue

            if (
                str(Path(record_dict[Fields.FILE]).name)
                == f"{record_dict[Fields.ID]}.pdf"
            ):
                continue

            old_filename = record_dict[Fields.FILE]
            new_filename = Path(record_dict[Fields.FILE]).parent / Path(
                f"{record_dict[Fields.ID]}.pdf"
            )
            try:
                Path(record_dict[Fields.FILE]).rename(new_filename)
            except FileNotFoundError:
                print(f"rename error: {record_dict['file']}")
                continue
            record_dict[Fields.FILE] = str(new_filename)

            # simple heuristic:
            pdfs_origin_file = Path("data/search/pdfs.bib")
            if pdfs_origin_file.is_file():
                colrev.env.utils.inplace_change(
                    filename=pdfs_origin_file,
                    old_string=old_filename,
                    new_string=str(new_filename),
                )
                self.review_manager.dataset.add_changes(pdfs_origin_file)

        self.review_manager.dataset.save_records_dict(records)
        self.review_manager.dataset.create_commit(
            msg="Set IDs",
        )

    def setup_custom_script(self) -> None:
        """Setup a custom prep script"""

        filedata = colrev.env.utils.get_package_file_content(
            module="colrev.ops", filename=Path("custom_scripts/custom_prep_script.py")
        )
        if filedata:
            with open("custom_prep_script.py", "w", encoding="utf-8") as file:
                file.write(filedata.decode("utf-8"))

        self.review_manager.dataset.add_changes(Path("custom_prep_script.py"))

        prep_round = self.review_manager.settings.prep.prep_rounds[-1]
        prep_round.prep_package_endpoints.append({"endpoint": "custom_prep_script"})
        self.review_manager.save_settings()

    def _load_prep_data(self, *, polish: bool = False) -> dict:
        records_headers = self.review_manager.dataset.load_records_dict(
            header_only=True
        )
        record_header_list = list(records_headers.values())

        pad = (
            35
            if (0 == len(record_header_list))
            else min((max(len(x[Fields.ID]) for x in record_header_list) + 2), 35)
        )

        r_states_to_prepare = [
            RecordState.md_imported,
            RecordState.md_needs_manual_preparation,
        ]
        if polish:
            r_states_to_prepare = list(RecordState)

        items = list(
            self.review_manager.dataset.read_next_record(
                conditions=[{Fields.STATUS: s} for s in r_states_to_prepare]
            )
        )
        if polish and self.review_manager.in_ci_environment() and len(items) > 2000:
            items = random.choices(items, k=2000)  # nosec

        prep_data = {
            "nr_tasks": len(items),
            "PAD": pad,
            "items": list(items),
        }

        return prep_data

    def _get_preparation_data(
        self,
        *,
        prep_round: colrev.settings.PrepRound,
        debug_file: typing.Optional[Path] = None,
        debug_ids: str,
        polish: bool = False,
    ) -> list:
        if self.debug_mode:
            prepare_data = self._load_prep_data_for_debug(
                debug_ids=debug_ids, debug_file=debug_file
            )
            if prepare_data["nr_tasks"] == 0:
                print("ID not found in history.")
        else:
            prepare_data = self._load_prep_data(polish=polish)
        nr_items = len(prepare_data["items"])

        if self.current_temp_records.is_file():
            # combine and remove redundant records

            cur_temp_recs = colrev.loader.load_utils.load(
                filename=self.current_temp_records,
                logger=self.review_manager.logger,
            )

            temp_recs = {}
            if self.temp_records.is_file():
                temp_recs = colrev.loader.load_utils.load(
                    filename=self.temp_records,
                    logger=self.review_manager.logger,
                )

            combined_recs = {**temp_recs, **cur_temp_recs}
            self.temp_records.parent.mkdir(exist_ok=True)

            write_file(records_dict=combined_recs, filename=self.temp_records)

            self.current_temp_records.unlink()

        if self.temp_records.is_file():

            temp_recs = colrev.loader.load_utils.load(
                filename=self.temp_records,
                logger=self.review_manager.logger,
            )

            self.review_manager.logger.info("Continue with existing records")
            skipped_items = 0
            list_to_skip = []
            for item in prepare_data["items"]:
                if item[Fields.ID] not in temp_recs:
                    continue
                del temp_recs[item[Fields.ID]]
                list_to_skip.append(item[Fields.ID])
                skipped_items += 1
            self.review_manager.logger.info(
                f"{Colors.GREEN}Skipped {skipped_items} records{Colors.END}"
            )
            prepare_data["items"] = [
                x for x in prepare_data["items"] if x[Fields.ID] not in list_to_skip
            ]

            with PREP_COUNTER.get_lock():
                PREP_COUNTER.value += skipped_items  # type: ignore

        if self.debug_mode:
            input("Press Enter to continue")
            self.review_manager.logger.info(
                f"prepare_data: "
                f"{self.review_manager.p_printer.pformat(prepare_data)}"
            )
        self.pad = prepare_data["PAD"]
        items = prepare_data["items"]
        prep_data = []
        for item in items:
            prep_data.append(
                {
                    "record": colrev.record.record_prep.PrepRecord(item),
                    "nr_items": nr_items,
                    # Note : we cannot load endpoints here
                    # because pathos/multiprocessing
                    # does not support functions as parameters
                    "prep_round_package_endpoints": prep_round.prep_package_endpoints,
                    "prep_round": prep_round.name,
                }
            )
        return prep_data

    def _retrieve_records_from_history(
        self,
        *,
        original_records: list[dict],
        condition_state: RecordState,
    ) -> list:
        retrieved, prior_records = [], []
        for (
            prior_records_dict
        ) in self.review_manager.dataset.load_records_from_history():
            for prior_record in prior_records_dict.values():
                if prior_record.get(Fields.STATUS, "NA") != condition_state:
                    continue
                for original_record in original_records:
                    if any(
                        o in prior_record[Fields.ORIGIN]
                        for o in original_record[Fields.ORIGIN]
                    ):
                        prior_records.append(prior_record)
                        # only take the latest version (i.e., drop the record)
                        # Note: only append the first one if origins were in
                        # different records (after deduplication)
                        retrieved.append(original_record[Fields.ID])
                original_records = [
                    orec
                    for orec in original_records
                    if orec[Fields.ID] not in retrieved
                ]

        return prior_records

    def _load_prep_data_for_debug(
        self, *, debug_ids: str, debug_file: typing.Optional[Path] = None
    ) -> dict:
        if debug_file:
            records_dict = colrev.loader.load_utils.load(
                filename=debug_file,
                logger=self.review_manager.logger,
            )

            for record_dict in records_dict.values():
                if RecordState.md_imported != record_dict.get("state", ""):
                    self.review_manager.logger.info(
                        f"Setting colrev_status to md_imported {record_dict['ID']}"
                    )
                    record = colrev.record.record_prep.PrepRecord(record_dict)
                    record.set_status(RecordState.md_imported)
            debug_ids_list = list(records_dict.keys())
            debug_ids = ",".join(debug_ids_list)
            self.review_manager.logger.info("Imported record (retrieved from file)")

        else:
            records = []
            debug_ids_list = debug_ids.split(",")
            original_records = list(
                self.review_manager.dataset.read_next_record(
                    conditions=[{Fields.ID: ID} for ID in debug_ids_list]
                )
            )
            # self.review_manager.logger.info("Current record")
            # self.review_manager.p_printer.pprint(original_records)
            records = self._retrieve_records_from_history(
                original_records=original_records,
                condition_state=RecordState.md_imported,
            )

        if len(records) == 0:
            prep_data = {"nr_tasks": 0, "PAD": 0, "items": []}
        else:
            prep_data = {
                "nr_tasks": len(debug_ids_list),
                "PAD": len(debug_ids),
                "items": records,
            }
        return prep_data

    def _setup_prep_round(
        self, *, i: int, prep_round: colrev.settings.PrepRound
    ) -> None:
        # pylint: disable=redefined-outer-name,invalid-name,global-statement
        global PREP_COUNTER
        with PREP_COUNTER.get_lock():
            PREP_COUNTER = Value("i", 0)
            PREP_COUNTER.value = 0  # type: ignore

        self.first_round = bool(i == 0)

        self.last_round = bool(
            i == len(self.review_manager.settings.prep.prep_rounds) - 1
        )

        if self.debug_mode:
            print("\n\n")

        if len(self.review_manager.settings.prep.prep_rounds) > 1:
            self.review_manager.logger.info(f"Prepare ({prep_round.name})")

        package_manager = self.review_manager.get_package_manager()

        self.prep_package_endpoints: dict[str, typing.Any] = {}
        for prep_package_endpoint in prep_round.prep_package_endpoints:

            prep_class = package_manager.get_package_endpoint_class(
                package_type=EndpointType.prep,
                package_identifier=prep_package_endpoint["endpoint"],
            )
            self.prep_package_endpoints[prep_package_endpoint["endpoint"]] = prep_class(
                prep_operation=self, settings=prep_package_endpoint
            )

        non_available_endpoints = [
            x["endpoint"].lower()
            for x in prep_round.prep_package_endpoints
            if x["endpoint"].lower() not in self.prep_package_endpoints
        ]
        if non_available_endpoints:
            if self.review_manager.in_ci_environment():
                raise colrev_exceptions.ServiceNotAvailableException(
                    dep=f"colrev prep ({','.join(non_available_endpoints)})",
                    detailed_trace="prep not available in ci environment",
                )
            raise colrev_exceptions.ServiceNotAvailableException(
                dep="colrev prep", detailed_trace="prep not available"
            )

        for endpoint_name, endpoint in self.prep_package_endpoints.items():
            check_function = getattr(endpoint, "check_availability", None)
            if callable(check_function):
                self.review_manager.logger.debug(
                    f"Check availability of {endpoint_name}"
                )
                endpoint.check_availability(source_operation=self)  # type: ignore

    def _log_record_change_scores(
        self, *, preparation_data: list, prepared_records: list
    ) -> None:
        for previous_record_item in preparation_data:
            previous_record = previous_record_item["record"]
            prepared_record = [
                r
                for r in prepared_records
                if r[Fields.ID] == previous_record.data[Fields.ID]
            ][0]

            change = colrev.record.record_prep.PrepRecord.get_record_change_score(
                colrev.record.record_prep.PrepRecord(prepared_record),
                previous_record,
            )
            if change > 0.05:
                self.review_manager.report_logger.info(
                    f" {prepared_record[Fields.ID]} "
                    + f"Change score: {round(change, 2)}"
                )

    def _log_details(self, prepared_records: list) -> None:
        nr_curated_recs = len(
            [
                r
                for r in prepared_records
                if colrev.record.record_prep.PrepRecord(r).masterdata_is_curated()
            ]
        )

        self.review_manager.logger.info(
            "curated (✔)".ljust(29)
            + f"{Colors.GREEN}{nr_curated_recs}{Colors.END}".rjust(20, " ")
            + " records"
        )

        nr_recs = len(
            [
                record
                for record in prepared_records
                if record[Fields.STATUS] == RecordState.md_prepared
            ]
        )

        self.review_manager.logger.info(
            "md_prepared".ljust(29)
            + f"{Colors.GREEN}{nr_recs}{Colors.END}".rjust(20, " ")
            + " records"
        )

        nr_recs = len(
            [
                record
                for record in prepared_records
                if record[Fields.STATUS] == RecordState.md_needs_manual_preparation
            ]
        )
        if nr_recs > 0:
            self.review_manager.logger.info(
                "md_needs_manual_preparation".ljust(29)
                + f"{Colors.ORANGE}{nr_recs}{Colors.END}".rjust(20, " ")
                + f" records ({nr_recs/len(prepared_records):.2%})"
            )

        nr_recs = len(
            [
                record
                for record in prepared_records
                if record[Fields.STATUS] == RecordState.rev_prescreen_excluded
            ]
        )
        if nr_recs > 0:
            self.review_manager.logger.info(
                "rev_prescreen_excluded".ljust(29)
                + f"{Colors.RED}{nr_recs}{Colors.END}".rjust(20, " ")
                + " records"
            )

    def skip_prep(self) -> None:
        """Skip the preparation"""

        records = self.review_manager.dataset.load_records_dict()

        for record_dict in records.values():
            if RecordState.md_imported == record_dict[Fields.STATUS]:
                record = colrev.record.record_prep.PrepRecord(record_dict)
                record.set_status(RecordState.md_prepared)
        self.review_manager.dataset.save_records_dict(records)
        self.review_manager.dataset.create_commit(msg="Skip prep")

    def _initialize_prep(self, *, polish: bool, debug_ids: str, cpu: int) -> None:
        if not polish:
            self.review_manager.logger.info("Prep")
        else:
            self.review_manager.logger.info("Prep (polish mode)")
        self.review_manager.logger.info(
            "Prep completes and corrects record metadata based on APIs and preparation rules."
        )
        self.polish = polish
        if self.polish:
            self.review_manager.logger.info(
                "Polish mode: consider all records but prevent state transitions."
            )
        self.review_manager.logger.info(
            "See https://colrev.readthedocs.io/en/latest/manual/metadata_retrieval/prep.html"
        )

        if self.debug_mode:
            print("\n\n\n")
            self.review_manager.logger.info("Start debug prep\n")
            self.review_manager.logger.info(
                "The debugger will replay the preparation procedures"
                " step-by-step, allow you to identify potential errors, trace them to "
                "their colrev_origin and correct them."
            )
            input("\nPress Enter to continue")
            print("\n\n")

        if debug_ids != "NA":
            self.debug_mode = True

        self._cpu = cpu

        # Note: for unit testing, we use a simple loop (instead of parallel)
        # to ensure that the IDs of feed records don't change
        unit_testing = "test_prep" == inspect.stack()[1][3]
        if unit_testing or self.debug_mode:
            self._cpu = 1

    def _prep_packages_ram_heavy(self, prep_round: colrev.settings.PrepRound) -> bool:
        prep_pe_names = [r["endpoint"] for r in prep_round.prep_package_endpoints]
        ram_reavy = "colrev.exclude_languages" in prep_pe_names  # type: ignore
        self.review_manager.logger.info(
            "Info: The language detector requires RAM and may take longer"
        )
        return ram_reavy

    def _get_prep_pool(
        self, prep_round: colrev.settings.PrepRound
    ) -> mp.pool.ThreadPool:
        if self._prep_packages_ram_heavy(prep_round=prep_round):
            pool = Pool(mp.cpu_count() // 2)
        else:
            # Note : if we use too many CPUS,
            # a "too many open files" exception is thrown
            pool = Pool(self._cpu)
        self.review_manager.logger.info(
            "Info: ✔ = quality-assured by CoLRev community curators"
        )
        return pool

    def _create_prep_commit(
        self,
        *,
        previous_preparation_data: list,
        prepared_records: list,
        prep_round: colrev.settings.PrepRound,
    ) -> None:
        self._log_record_change_scores(
            preparation_data=previous_preparation_data,
            prepared_records=prepared_records,
        )

        if not self.debug_mode:
            self.review_manager.dataset.save_records_dict(
                {r[Fields.ID]: r for r in prepared_records}, partial=True
            )

            self._log_details(prepared_records)

            self.review_manager.dataset.create_commit(
                msg=f"Prepare records ({prep_round.name})",
            )
            self._prep_commit_id = (
                self.review_manager.dataset.get_repo().head.commit.hexsha
            )
            if not self.review_manager.high_level_operation:
                print()
        self.review_manager.reset_report_logger()

        self._print_stats()

    def _post_prep(self) -> None:
        if not self.review_manager.high_level_operation:
            print()

        self.review_manager.logger.info("To validate the changes, use")

        self.review_manager.logger.info(
            f"{Colors.ORANGE}colrev validate {self._prep_commit_id}{Colors.END}"
        )
        if not self.review_manager.high_level_operation:
            print()

        self.review_manager.logger.info(
            f"{Colors.GREEN}Completed prep operation{Colors.END}"
        )
        if self.review_manager.in_ci_environment():
            print("\n\n")

    # pylint: disable=too-many-arguments
    @colrev.process.operation.Operation.decorate()
    def main(
        self,
        *,
        keep_ids: bool = False,
        debug_ids: str = "NA",
        debug_file: typing.Optional[Path] = None,
        cpu: int = 4,
        polish: bool = False,
    ) -> None:
        """Preparation of records (main entrypoint)"""

        self._initialize_prep(polish=polish, debug_ids=debug_ids, cpu=cpu)

        try:
            for i, prep_round in enumerate(
                self.review_manager.settings.prep.prep_rounds
            ):
                self._setup_prep_round(i=i, prep_round=prep_round)

                preparation_data = self._get_preparation_data(
                    prep_round=prep_round,
                    debug_file=debug_file,
                    debug_ids=debug_ids,
                    polish=polish,
                )
                previous_preparation_data = deepcopy(preparation_data)

                if len(preparation_data) == 0 and not self.temp_records.is_file():
                    self.review_manager.logger.info("No records to prepare.")
                    print()
                    return

                if self._cpu == 1:
                    # Note: preparation_data is not turned into a list of records.
                    prepared_records = []
                    for item in preparation_data:
                        record = self.prepare(item)
                        prepared_records.append(record)
                else:
                    pool = self._get_prep_pool(prep_round)
                    prepared_records = pool.map(self.prepare, preparation_data)
                    pool.close()
                    pool.join()

                self._complete_resumed_operation(prepared_records)

                self._create_prep_commit(
                    previous_preparation_data=previous_preparation_data,
                    prepared_records=prepared_records,
                    prep_round=prep_round,
                )

        except requests_ConnectionError as exc:
            if "OSError(24, 'Too many open files" in str(exc):
                raise colrev_exceptions.ServiceNotAvailableException(
                    "Too many files opened (OSError, Errno24). "
                    "To use a smaller number of parallel processes, run colrev prep --cpu 1"
                ) from exc
            raise exc

        except OSError as exc:
            if 24 == exc.errno:
                raise colrev_exceptions.ServiceNotAvailableException(
                    "Too many files opened (OSError, Errno24). "
                    "To use a smaller number of parallel processes, run colrev prep --cpu 1"
                ) from exc
            raise exc

        if not keep_ids and not self.debug_mode and not self.polish:
            self.review_manager.logger.info("Set record IDs")
            self.review_manager.dataset.set_ids()
            self.review_manager.dataset.create_commit(msg="Set IDs")

        self._post_prep()
