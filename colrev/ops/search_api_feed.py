#! /usr/bin/env python
"""CoLRev search feed: store and update origin records and update main records."""
from __future__ import annotations

import json
import time
from copy import deepcopy
from random import randint

import colrev.exceptions as colrev_exceptions
import colrev.loader.load_utils
from colrev.constants import Colors
from colrev.constants import DefectCodes
from colrev.constants import ENTRYTYPES
from colrev.constants import Fields
from colrev.constants import FieldSet
from colrev.constants import FieldValues
from colrev.writer.write_utils import to_string
from colrev.writer.write_utils import write_file


# Keep in mind the need for lock-mechanisms, e.g., in concurrent prep operations
class SearchAPIFeed:
    """A feed managing results from API searches"""

    # pylint: disable=too-many-instance-attributes
    # pylint: disable=too-many-arguments

    _nr_added: int = 0
    _nr_changed: int = 0

    def __init__(
        self,
        *,
        review_manager: colrev.review_manager.ReviewManager,
        source_identifier: str,
        search_source: colrev.settings.SearchSource,
        update_only: bool,
        update_time_variant_fields: bool = True,
        prep_mode: bool = False,
    ):
        self.source = search_source
        self.feed_file = search_source.filename

        # Note: the source_identifier identifies records in the search feed.
        # This could be a doi or link or database-specific ID (like WOS accession numbers)
        # The source_identifier can be stored in the main records.bib (it does not have to)
        # The record source_identifier (feed-specific) is used in search
        # or other operations (like prep)
        # In search operations, records are added/updated based on available_ids
        # (which maps source_identifiers to IDs used to generate the colrev_origin)
        # In other operations, records are linked through colrev_origins,
        # i.e., there is no need to store the source_identifier in the main records (redundantly)
        self.source_identifier = source_identifier

        # Note: corresponds to rerun (in search.main() and run_search())
        self.update_only = update_only
        self.update_time_variant_fields = update_time_variant_fields
        self.review_manager = review_manager
        self.origin_prefix = self.source.get_origin_prefix()

        self._load_feed()

        self.prep_mode = prep_mode
        if not prep_mode:
            self.records = self.review_manager.dataset.load_records_dict()

    def _load_feed(self) -> None:
        if not self.feed_file.is_file():
            self._available_ids = {}
            self._max_id = 1
            self.feed_records = {}
            return
        self.feed_records = colrev.loader.load_utils.loads(
            load_string=self.feed_file.read_text(encoding="utf8"),
            implementation="bib",
            logger=self.review_manager.logger,
        )
        self._available_ids = {
            x[self.source_identifier]: x[Fields.ID]
            for x in self.feed_records.values()
            if self.source_identifier in x
        }
        self._max_id = (
            max(
                [
                    int(x[Fields.ID])
                    for x in self.feed_records.values()
                    if x[Fields.ID].isdigit()
                ]
                + [1]
            )
            + 1
        )

    def _get_prev_feed_record(
        self, retrieved_record: colrev.record.Record
    ) -> colrev.record.Record:
        """Get the previous record dict version"""
        self._set_id(retrieved_record)

        prev_feed_record_dict = {}
        if retrieved_record.data[Fields.ID] in self.feed_records:
            prev_feed_record_dict = deepcopy(
                self.feed_records[retrieved_record.data[Fields.ID]]
            )
        return colrev.record.Record(data=prev_feed_record_dict)

    def _set_id(self, record: colrev.record.Record) -> None:
        """Set incremental record ID
        If self.source_identifier is in record_dict, it is updated, otherwise added as a new record.
        """

        if self.source_identifier not in record.data:
            raise colrev_exceptions.NotFeedIdentifiableException()

        if record.data[self.source_identifier] in self._available_ids:
            record.data[Fields.ID] = self._available_ids[
                record.data[self.source_identifier]
            ]
        else:
            record.data[Fields.ID] = str(self._max_id).rjust(6, "0")

    def _add_record_to_feed(
        self, record: colrev.record.Record, prev_feed_record: colrev.record.Record
    ) -> bool:
        """Add a record to the feed and set its colrev_origin"""

        self._set_id(record)

        feed_record_dict = record.data.copy()
        added_new = True
        if feed_record_dict[self.source_identifier] in self._available_ids:
            added_new = False
        else:
            self._max_id += 1
            self._nr_added += 1

        for provenance_key in FieldSet.PROVENANCE_KEYS:
            if provenance_key in feed_record_dict:
                del feed_record_dict[provenance_key]
        frid = feed_record_dict[Fields.ID]
        self._available_ids[feed_record_dict[self.source_identifier]] = frid

        self._update_record_forthcoming(
            record=record, prev_feed_record=prev_feed_record
        )

        if self.update_only:
            # ignore time_variant_fields
            # (otherwise, fields in recent records would be more up-to-date)
            for key in FieldSet.TIME_VARIANT_FIELDS:
                if frid in self.feed_records:
                    if key in self.feed_records[frid]:
                        feed_record_dict[key] = self.feed_records[frid][key]
                    else:
                        if key in feed_record_dict:
                            del feed_record_dict[key]

        self.feed_records[frid] = feed_record_dict
        return added_new

    def _have_changed(
        self, record_a: colrev.record.Record, record_b: colrev.record.Record
    ) -> bool:

        def tostr_and_load(record: colrev.record.Record) -> dict:
            record_dict = deepcopy(record.data)
            bibtex_str = to_string(
                records_dict={record_dict[Fields.ID]: record_dict}, implementation="bib"
            )
            record_dict = list(
                colrev.loader.load_utils.loads(
                    load_string=bibtex_str,
                    implementation="bib",
                    logger=self.review_manager.logger,
                ).values()
            )[0]
            return record_dict

        # To ignore changes introduced by saving/loading the feed-records,
        # we parse and load them in the following.
        record_a_dict = tostr_and_load(record_a)
        record_b_dict = tostr_and_load(record_b)

        # Note : record_a can have more keys (that's ok)
        changed = False
        for key, value in record_b_dict.items():
            if key in FieldSet.PROVENANCE_KEYS + [Fields.ID, Fields.CURATION_ID]:
                continue
            if key not in record_a_dict:
                return True
            if record_a_dict[key] != value:
                return True
        return changed

    def _update_record_retract(
        self, *, record: colrev.record.Record, main_record: colrev.record.Record
    ) -> bool:
        if record.is_retracted():
            self.review_manager.logger.info(
                f"{Colors.GREEN}Found paper retract: "
                f"{main_record.data['ID']}{Colors.END}"
            )
            main_record.prescreen_exclude(
                reason=FieldValues.RETRACTED, print_warning=True
            )
            main_record.remove_field(key="warning")
            return True
        return False

    def _update_record_forthcoming(
        self, *, record: colrev.record.Record, prev_feed_record: colrev.record.Record
    ) -> None:

        if self._forthcoming_published(record=record, prev_record=prev_feed_record):
            self.review_manager.logger.info(
                f"{Colors.GREEN}Update published forthcoming paper: "
                f"{record.data['ID']}{Colors.END}"
            )
            prev_feed_record.data[Fields.YEAR] = record.data[Fields.YEAR]

    def _missing_ignored_field(
        self, main_record: colrev.record.Record, key: str
    ) -> bool:
        source = main_record.get_masterdata_provenance_source(key)
        notes = main_record.get_masterdata_provenance_notes(key)
        if (
            source == "colrev_curation.masterdata_restrictions"
            and f"IGNORE:{DefectCodes.MISSING}" in notes
        ):
            return True
        return False

    def _update_record_fields(
        self,
        *,
        record: colrev.record.Record,
        main_record: colrev.record.Record,
        prev_feed_record: colrev.record.Record,
        origin: str,
    ) -> None:
        for key, value in record.data.items():
            if (
                not self.update_time_variant_fields
                and key in FieldSet.TIME_VARIANT_FIELDS
            ):
                continue

            if key in FieldSet.PROVENANCE_KEYS + [Fields.ID, Fields.CURATION_ID]:
                continue

            if key not in main_record.data:
                if self._missing_ignored_field(main_record, key):
                    continue

                main_record.update_field(
                    key=key,
                    value=value,
                    source=origin,
                    keep_source_if_equal=True,
                    append_edit=False,
                )
            else:
                if self.source.get_origin_prefix() != "md_curated.bib":
                    if prev_feed_record.data.get(key, "NA") != main_record.data.get(
                        key, "OTHER"
                    ):
                        continue
                if value.replace(" - ", ": ") == main_record.data[key].replace(
                    " - ", ": "
                ):
                    continue
                if (
                    key == Fields.URL
                    and "dblp.org" in value
                    and key in main_record.data
                ):
                    continue
                main_record.update_field(
                    key=key,
                    value=value,
                    source=origin,
                    keep_source_if_equal=True,
                    append_edit=False,
                )

    def _forthcoming_published(
        self, *, record: colrev.record.Record, prev_record: colrev.record.Record
    ) -> bool:
        if record.data[Fields.ENTRYTYPE] != ENTRYTYPES.ARTICLE:
            return False
        if Fields.YEAR not in record.data or Fields.YEAR not in prev_record.data:
            return False

        # Option 1: Forthcoming paper published if year is assigned
        if (
            "forthcoming" == prev_record.data[Fields.YEAR]
            and "forthcoming" != record.data[Fields.YEAR]
        ):
            return True

        # Option 2: Forthcoming paper published if volume and number are assigned
        # i.e., no longer UNKNOWN
        if (
            record.data.get(Fields.VOLUME, FieldValues.UNKNOWN) != FieldValues.UNKNOWN
            and prev_record.data.get(Fields.VOLUME, FieldValues.UNKNOWN)
            == FieldValues.UNKNOWN
            and record.data.get(Fields.NUMBER, FieldValues.UNKNOWN)
            != FieldValues.UNKNOWN
            and prev_record.data.get(Fields.VOLUME, FieldValues.UNKNOWN)
            == FieldValues.UNKNOWN
        ):
            return True
        return False

    def _get_main_record(
        self, retrieved_record: colrev.record.Record
    ) -> colrev.record.Record:
        main_record_dict: dict = {}
        for record_dict in self.records.values():
            if retrieved_record.data[Fields.ORIGIN][0] in record_dict[Fields.ORIGIN]:
                main_record_dict = record_dict
                break

        if main_record_dict == {}:
            raise colrev_exceptions.RecordNotFoundException(
                f"Could not find/update {retrieved_record.data['ID']}"
            )
        return colrev.record.Record(data=main_record_dict)

    def _update_record(
        self,
        *,
        retrieved_record: colrev.record.Record,
        prev_feed_record: colrev.record.Record,
    ) -> bool:
        """Convenience function to update existing records (main data/records.bib)"""

        # Original record
        colrev_origin = f"{self.origin_prefix}/{retrieved_record.data['ID']}"
        retrieved_record.data[Fields.ORIGIN] = [colrev_origin]
        retrieved_record.add_provenance_all(source=colrev_origin)

        main_record = self._get_main_record(retrieved_record)
        # TBD: in curated masterdata repositories?

        retrieved_record.prefix_non_standardized_field_keys(prefix=self.source.endpoint)
        changed = self._update_record_retract(
            record=retrieved_record, main_record=main_record
        )

        if (
            main_record.masterdata_is_curated()
            and "md_curated.bib" != self.source.get_origin_prefix()
        ):
            return False

        similarity_score = colrev.record.Record.get_record_similarity(
            record_a=retrieved_record,
            record_b=prev_feed_record,
        )

        dict_diff = retrieved_record.get_diff(other_record=prev_feed_record)
        self._update_record_fields(
            record=retrieved_record,
            main_record=main_record,
            prev_feed_record=prev_feed_record,
            origin=colrev_origin,
        )
        if self._have_changed(
            main_record, prev_feed_record
        ) or self._have_changed(  # Note : not (yet) in the main records but changed
            retrieved_record, prev_feed_record
        ):
            changed = True
            self._nr_changed += 1
            if self._forthcoming_published(
                record=retrieved_record, prev_record=prev_feed_record
            ):
                pass  # (notified when updating feed record)
            elif similarity_score > 0.98:
                self.review_manager.logger.info(f" check/update {colrev_origin}")
            else:
                self.review_manager.logger.info(
                    f" {Colors.RED} check/update {colrev_origin} leads to substantial changes "
                    f"({similarity_score}) in {main_record.data['ID']}:{Colors.END}"
                )
                self.review_manager.logger.info(
                    self.review_manager.p_printer.pformat(
                        [x for x in dict_diff if "change" == x[0]]
                    )
                )

        return changed

    def _print_post_run_search_infos(self) -> None:
        """Print the search infos (after running the search)"""
        if self._nr_added > 0:
            self.review_manager.logger.info(
                f"{Colors.GREEN}Retrieved {self._nr_added} records{Colors.END}"
            )
        else:
            self.review_manager.logger.info(
                f"{Colors.GREEN}No additional records retrieved{Colors.END}"
            )

        if self.prep_mode:
            # No need to print the following in prep mode
            # because the main records are not updated/saved
            return

        if self._nr_changed > 0:
            self.review_manager.logger.info(
                f"{Colors.GREEN}Updated {self._nr_changed} records{Colors.END}"
            )
        else:
            if self.records:
                self.review_manager.logger.info(
                    f"{Colors.GREEN}Records (data/records.bib) up-to-date{Colors.END}"
                )

    def get_prev_feed_record(
        self, record: colrev.record.Record
    ) -> colrev.record.Record:
        """Get the previous record dict version"""
        record = deepcopy(record)
        self._set_id(record)
        prev_feed_record_dict = {}
        if record.data[Fields.ID] in self.feed_records:
            prev_feed_record_dict = deepcopy(self.feed_records[record.data[Fields.ID]])
        return colrev.record.Record(data=prev_feed_record_dict)

    def add_update_record(self, retrieved_record: colrev.record.Record) -> bool:
        """Add or update a record in the api_search_feed and records"""

        prev_feed_record = self._get_prev_feed_record(retrieved_record)
        added = self._add_record_to_feed(retrieved_record, prev_feed_record)
        if not self.prep_mode:
            try:
                self._update_record(
                    retrieved_record=retrieved_record.copy(),
                    prev_feed_record=prev_feed_record.copy(),
                )
            except colrev_exceptions.RecordNotFoundException:
                pass
        return added

    def save(self, *, skip_print: bool = False) -> None:
        """Save the feed file and records, printing post-run search infos."""

        if not skip_print and not self.prep_mode:
            self._print_post_run_search_infos()

        if len(self.feed_records) > 0:
            self.feed_file.parents[0].mkdir(parents=True, exist_ok=True)
            write_file(records_dict=self.feed_records, filename=self.feed_file)

            while True:
                try:
                    self.review_manager.load_settings()
                    if self.source.filename.name not in [
                        s.filename.name for s in self.review_manager.settings.sources
                    ]:
                        self.review_manager.settings.sources.append(self.source)
                        self.review_manager.save_settings()

                    self.review_manager.dataset.add_changes(path=self.feed_file)
                    break
                except (
                    FileExistsError,
                    OSError,
                    json.decoder.JSONDecodeError,
                ):  # pragma: no cover
                    self.review_manager.logger.debug("Wait for git")
                    time.sleep(randint(1, 15))  # nosec

        if not self.prep_mode:
            self.review_manager.dataset.save_records_dict(records=self.records)
        self._nr_added = 0
        self._nr_changed = 0
