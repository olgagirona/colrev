#! /usr/bin/env python
"""SearchSource: Unknown source (default for all other sources)"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import dacite
import zope.interface
from dacite import from_dict
from dataclasses_jsonschema import JsonSchemaMixin
from rapidfuzz import fuzz

import colrev.env.language_service
import colrev.env.package_manager
import colrev.exceptions as colrev_exceptions
import colrev.ops.load_utils_bib
import colrev.ops.load_utils_md
import colrev.ops.load_utils_ris
import colrev.ops.search
import colrev.record
from colrev.constants import Colors
from colrev.constants import ENTRYTYPES
from colrev.constants import Fields
from colrev.constants import FieldValues

# pylint: disable=unused-argument
# pylint: disable=duplicate-code


@zope.interface.implementer(
    colrev.env.package_manager.SearchSourcePackageEndpointInterface
)
@dataclass
class UnknownSearchSource(JsonSchemaMixin):
    """Unknown SearchSource"""

    settings_class = colrev.env.package_manager.DefaultSourceSettings
    endpoint = "colrev.unknown_source"

    source_identifier = "colrev.unknown_source"
    search_types = [
        colrev.settings.SearchType.DB,
        colrev.settings.SearchType.OTHER,
        colrev.settings.SearchType.BACKWARD_SEARCH,
        colrev.settings.SearchType.FORWARD_SEARCH,
        colrev.settings.SearchType.TOC,
    ]

    ci_supported: bool = False
    heuristic_status = colrev.env.package_manager.SearchSourceHeuristicStatus.na
    short_name = "Unknown Source"
    docs_link = (
        "https://github.com/CoLRev-Environment/colrev/blob/main/"
        + "colrev/ops/built_in/search_sources/unknown_source.md"
    )
    db_url = ""

    HTML_CLEANER = re.compile("<.*?>")
    _padding = 40

    def __init__(
        self, *, source_operation: colrev.operation.Operation, settings: dict
    ) -> None:
        converters = {Path: Path, Enum: Enum}
        self.search_source = from_dict(
            data_class=self.settings_class,
            data=settings,
            config=dacite.Config(type_hooks=converters, cast=[Enum]),  # type: ignore
        )
        self.review_manager = source_operation.review_manager
        self.language_service = colrev.env.language_service.LanguageService()
        self.operation = source_operation

    @classmethod
    def heuristic(cls, filename: Path, data: str) -> dict:
        """Source heuristic for unknown sources"""

        result = {"confidence": 0.1}

        return result

    @classmethod
    def add_endpoint(
        cls,
        operation: colrev.ops.search.Search,
        params: dict,
    ) -> colrev.settings.SearchSource:
        """Add SearchSource as an endpoint (based on query provided to colrev search -a )"""

        return operation.add_db_source(
            search_source_cls=cls,
            params=params,
        )

    def run_search(self, rerun: bool) -> None:
        """Run a search of Crossref"""

        if self.search_source.search_type == colrev.settings.SearchType.DB:
            self.operation.run_db_search(  # type: ignore
                search_source_cls=self.__class__, source=self.search_source
            )

    def get_masterdata(
        self,
        prep_operation: colrev.ops.prep.Prep,
        record: colrev.record.Record,
        save_feed: bool = True,
        timeout: int = 10,
    ) -> colrev.record.Record:
        """Not implemented"""
        return record

    def _rename_erroneous_extensions(self) -> None:
        if self.search_source.filename.suffix in [".xls", ".xlsx"]:
            return
        data = self.search_source.filename.read_text(encoding="utf-8")
        # # Correct the file extension if necessary
        if re.findall(
            r"^%0", data, re.MULTILINE
        ) and self.search_source.filename.suffix not in [".enl"]:
            new_filename = self.search_source.filename.with_suffix(".enl")
            self.review_manager.logger.info(
                f"{Colors.GREEN}Rename to {new_filename} "
                f"(because the format is .enl){Colors.END}"
            )
            self.search_source.filename.rename(new_filename)
            self.review_manager.dataset.add_changes(
                path=self.search_source.filename, remove=True
            )
            self.search_source.filename = new_filename
            self.review_manager.dataset.add_changes(path=new_filename)
            self.review_manager.dataset.create_commit(
                msg=f"Rename {self.search_source.filename}"
            )
            return

        if re.findall(
            r"^TI ", data, re.MULTILINE
        ) and self.search_source.filename.suffix not in [".ris"]:
            new_filename = self.search_source.filename.with_suffix(".ris")
            self.review_manager.logger.info(
                f"{Colors.GREEN}Rename to {new_filename} "
                f"(because the format is .ris){Colors.END}"
            )
            self.search_source.filename.rename(new_filename)
            self.review_manager.dataset.add_changes(
                path=self.search_source.filename, remove=True
            )
            self.search_source.filename = new_filename
            self.review_manager.dataset.add_changes(path=new_filename)
            self.review_manager.dataset.create_commit(
                msg=f"Rename {self.search_source.filename}"
            )

    def _load_ris(self, *, load_operation: colrev.ops.load.Load) -> dict:
        # Based on https://github.com/aurimasv/translators/wiki/RIS-Tag-Map
        reference_types = {
            "JOUR": ENTRYTYPES.ARTICLE,
            "JFULL": ENTRYTYPES.ARTICLE,
            "ABST": ENTRYTYPES.ARTICLE,
            "INPR": ENTRYTYPES.ARTICLE,  # inpress
            "CONF": ENTRYTYPES.INPROCEEDINGS,
            "CPAPER": ENTRYTYPES.INPROCEEDINGS,
            "THES": ENTRYTYPES.PHDTHESIS,
            "REPT": ENTRYTYPES.TECHREPORT,
            "RPRT": ENTRYTYPES.TECHREPORT,
            "CHAP": ENTRYTYPES.INBOOK,
            "BOOK": ENTRYTYPES.BOOK,
            "NEWS": ENTRYTYPES.MISC,
            "BLOG": ENTRYTYPES.MISC,
        }
        key_map = {
            ENTRYTYPES.ARTICLE: {
                "PY": Fields.YEAR,
                "AU": Fields.AUTHOR,
                "TI": Fields.TITLE,
                "T2": Fields.JOURNAL,
                "AB": Fields.ABSTRACT,
                "VL": Fields.VOLUME,
                "IS": Fields.NUMBER,
                "DO": Fields.DOI,
                "PB": Fields.PUBLISHER,
                "UR": Fields.URL,
                "fulltext": Fields.FULLTEXT,
                "PMID": Fields.PUBMED_ID,
                "KW": Fields.KEYWORDS,
                "SP": Fields.PAGES,
            },
            ENTRYTYPES.INPROCEEDINGS: {
                "PY": Fields.YEAR,
                "AU": Fields.AUTHOR,
                "TI": Fields.TITLE,
                # "secondary_title": Fields.BOOKTITLE,
                "DO": Fields.DOI,
                "UR": Fields.URL,
                # "fulltext": Fields.FULLTEXT,
                "PMID": Fields.PUBMED_ID,
                "KW": Fields.KEYWORDS,
                "SP": Fields.PAGES,
            },
            ENTRYTYPES.INBOOK: {
                "PY": Fields.YEAR,
                "AU": Fields.AUTHOR,
                # "primary_title": Fields.CHAPTER,
                # "secondary_title": Fields.TITLE,
                "DO": Fields.DOI,
                "PB": Fields.PUBLISHER,
                # "edition": Fields.EDITION,
                "UR": Fields.URL,
                # "fulltext": Fields.FULLTEXT,
                "KW": Fields.KEYWORDS,
                "SP": Fields.PAGES,
            },
            ENTRYTYPES.BOOK: {
                "PY": Fields.YEAR,
                "AU": Fields.AUTHOR,
                # "primary_title": Fields.CHAPTER,
                # "secondary_title": Fields.TITLE,
                "DO": Fields.DOI,
                "PB": Fields.PUBLISHER,
                # "edition": Fields.EDITION,
                "UR": Fields.URL,
                # "fulltext": Fields.FULLTEXT,
                "KW": Fields.KEYWORDS,
                "SP": Fields.PAGES,
            },
            ENTRYTYPES.PHDTHESIS: {
                "PY": Fields.YEAR,
                "AU": Fields.AUTHOR,
                "TI": Fields.TITLE,
                "UR": Fields.URL,
            },
            ENTRYTYPES.TECHREPORT: {
                "PY": Fields.YEAR,
                "AU": Fields.AUTHOR,
                "TI": Fields.TITLE,
                "UR": Fields.URL,
                # "fulltext": Fields.FULLTEXT,
                "KW": Fields.KEYWORDS,
                "PB": Fields.PUBLISHER,
                "SP": Fields.PAGES,
            },
            ENTRYTYPES.MISC: {
                "PY": Fields.YEAR,
                "AU": Fields.AUTHOR,
                "TI": Fields.TITLE,
                "UR": Fields.URL,
                # "fulltext": Fields.FULLTEXT,
                "KW": Fields.KEYWORDS,
                "PB": Fields.PUBLISHER,
                "SP": Fields.PAGES,
            },
        }

        load_operation.ensure_append_only(file=self.search_source.filename)
        ris_loader = colrev.ops.load_utils_ris.RISLoader(
            filename=self.search_source.filename,
            list_fields={"AU": " and ", "KW": ", "},
            force_mode=False,
            logger=self.review_manager.logger,
        )
        records = ris_loader.load_ris_records()

        for record_dict in records.values():
            # pylint: disable=colrev-missed-constant-usage
            record_dict["ID"] = record_dict["UR"].split("/")[-1]
            if record_dict["TY"] not in reference_types:
                msg = (
                    f"{Colors.RED}TY={record_dict['TY']} not yet supported{Colors.END}"
                )
                if not self.review_manager.force_mode:
                    raise NotImplementedError(msg)
                self.review_manager.logger.error(msg)
                continue
            entrytype = reference_types[record_dict["TY"]]
            record_dict[Fields.ENTRYTYPE] = entrytype

            # fixes
            if entrytype == ENTRYTYPES.ARTICLE:
                if "T1" in record_dict and "TI" not in record_dict:
                    record_dict["TI"] = record_dict.pop("T1")

            # RIS-keys > standard keys
            for ris_key in list(record_dict.keys()):
                if ris_key in ["ENTRYTYPE", "ID"]:
                    continue
                if ris_key not in key_map[entrytype]:
                    del record_dict[ris_key]
                    # print/notify: ris_key
                    continue
                standard_key = key_map[entrytype][ris_key]
                record_dict[standard_key] = record_dict.pop(ris_key)

        return records

    def _load_bib(self, *, load_operation: colrev.ops.load.Load) -> dict:
        records = colrev.ops.load_utils.load(
            filename=self.search_source.filename,
            logger=self.review_manager.logger,
            force_mode=self.review_manager.force_mode,
        )
        return records

    # pylint: disable=colrev-missed-constant-usage
    def _table_set_entrytype(self, *, record_dict: dict) -> None:
        if "type" in record_dict:
            record_dict[Fields.ENTRYTYPE] = record_dict.pop("type")

        if Fields.ENTRYTYPE not in record_dict:
            if record_dict.get(Fields.JOURNAL, "") != "":
                record_dict[Fields.ENTRYTYPE] = ENTRYTYPES.ARTICLE
            elif record_dict.get(Fields.BOOKTITLE, "") != "":
                record_dict[Fields.ENTRYTYPE] = ENTRYTYPES.INPROCEEDINGS
            else:
                record_dict[Fields.ENTRYTYPE] = ENTRYTYPES.MISC

        if record_dict[Fields.ENTRYTYPE] == ENTRYTYPES.INPROCEEDINGS:
            if Fields.JOURNAL in record_dict and Fields.BOOKTITLE not in record_dict:
                record_dict[Fields.BOOKTITLE] = record_dict.pop(Fields.JOURNAL)
        elif record_dict[Fields.ENTRYTYPE] == ENTRYTYPES.ARTICLE:
            if Fields.BOOKTITLE in record_dict and Fields.JOURNAL not in record_dict:
                record_dict[Fields.JOURNAL] = record_dict.pop(Fields.BOOKTITLE)

    # pylint: disable=colrev-missed-constant-usage
    def _table_rename_fields(self, *, record_dict: dict) -> None:
        if "issue" in record_dict and Fields.NUMBER not in record_dict:
            record_dict[Fields.NUMBER] = record_dict.pop("issue")
            if record_dict[Fields.NUMBER] == "no issue":
                del record_dict[Fields.NUMBER]

        if "authors" in record_dict and Fields.AUTHOR not in record_dict:
            record_dict[Fields.AUTHOR] = record_dict.pop("authors")

        if "publication_year" in record_dict and Fields.YEAR not in record_dict:
            record_dict[Fields.YEAR] = record_dict.pop("publication_year")

        # Note: this is a simple heuristic:
        if (
            "journal/book" in record_dict
            and Fields.JOURNAL not in record_dict
            and Fields.DOI in record_dict
        ):
            record_dict[Fields.JOURNAL] = record_dict.pop("journal/book")

    # pylint: disable=colrev-missed-constant-usage
    def _table_drop_fields(self, *, records_dict: dict) -> None:
        for r_dict in records_dict.values():
            for key in list(r_dict.keys()):
                if r_dict[key] in [f"no {key}", "", "nan"]:
                    del r_dict[key]
            if (
                r_dict.get("number_of_cited_references", "NA")
                == "no Number-of-Cited-References"
            ):
                del r_dict["number_of_cited_references"]
            if "no file" in r_dict.get("file_name", "NA"):
                del r_dict["file_name"]

            if r_dict.get("cited_by", "NA") in [
                "no Times-Cited",
            ]:
                del r_dict["cited_by"]

            if "author_count" in r_dict:
                del r_dict["author_count"]
            if "citation_key" in r_dict:
                del r_dict["citation_key"]

    # pylint: disable=colrev-missed-constant-usage
    def _table_fix_authors(self, *, records_dict: dict) -> None:
        for record in records_dict.values():
            if "author" in record and ";" in record["author"]:
                record["author"] = record["author"].replace("; ", " and ")

    def _load_csv(self, *, load_operation: colrev.ops.load.Load) -> dict:

        load_operation.ensure_append_only(file=self.search_source.filename)
        table_loader = colrev.ops.load_utils_table.TableLoader(
            filename=self.search_source.filename,
            logger=load_operation.review_manager.logger,
            force_mode=load_operation.review_manager.force_mode,
        )
        records = table_loader.load_table_entries()
        for record_dict in records.values():
            self._table_set_entrytype(record_dict=record_dict)
            self._table_rename_fields(record_dict=record_dict)
            self._table_drop_fields(records_dict=records)
            self._table_fix_authors(records_dict=records)

        return records

    def _load_xlsx(self, *, load_operation: colrev.ops.load.Load) -> dict:
        load_operation.ensure_append_only(file=self.search_source.filename)
        excel_loader = colrev.ops.load_utils_table.TableLoader(
            filename=self.search_source.filename,
            logger=load_operation.review_manager.logger,
            force_mode=load_operation.review_manager.force_mode,
        )
        records = excel_loader.load_table_entries()
        for record_dict in records.values():
            self._table_set_entrytype(record_dict=record_dict)
            self._table_rename_fields(record_dict=record_dict)
            self._table_drop_fields(records_dict=records)
            self._table_fix_authors(records_dict=records)
        return records

    def _load_md(self, *, load_operation: colrev.ops.load.Load) -> dict:
        load_operation.ensure_append_only(file=self.search_source.filename)

        md_loader = colrev.ops.load_utils_md.MarkdownLoader(
            filename=self.search_source.filename,
            logger=load_operation.review_manager.logger,
            force_mode=load_operation.review_manager.force_mode,
        )
        records = md_loader.load()
        return records

    def _load_enl(self, *, load_operation: colrev.ops.load.Load) -> dict:
        enl_mapping = {
            ENTRYTYPES.ARTICLE: {
                "T": Fields.TITLE,
                "A": Fields.AUTHOR,
                "D": Fields.YEAR,
                "B": Fields.JOURNAL,
                "V": Fields.VOLUME,
                "N": Fields.NUMBER,
                "P": Fields.PAGES,
                "X": Fields.ABSTRACT,
                "U": Fields.URL,
                "8": "date",
                "0": "type",
            },
            ENTRYTYPES.MISC: {
                "T": Fields.TITLE,
                "A": Fields.AUTHOR,
                "D": Fields.YEAR,
                "B": Fields.JOURNAL,
                "V": Fields.VOLUME,
                "N": Fields.NUMBER,
                "P": Fields.PAGES,
                "X": Fields.ABSTRACT,
                "U": Fields.URL,
                "8": "date",
                "0": "type",
            },
        }

        entrytype_map = {
            "Journal Article": ENTRYTYPES.ARTICLE,
            "Inproceedings": ENTRYTYPES.MISC,
        }

        enl_loader = colrev.ops.load_utils_enl.ENLLoader(
            filename=self.search_source.filename,
            list_fields={"A": " and "},
            force_mode=load_operation.review_manager.force_mode,
            logger=load_operation.review_manager.logger,
        )
        records = enl_loader.load_enl_entries()

        for record_dict in records.values():
            if "0" not in record_dict:
                keys_to_check = ["V", "N"]
                if any(k in record_dict for k in keys_to_check):
                    record_dict["0"] = "Journal Article"
                else:
                    record_dict["0"] = "Inproceedings"
            enl_loader.apply_entrytype_mapping(
                record_dict=record_dict, entrytype_map=entrytype_map
            )
            enl_loader.map_keys(record_dict=record_dict, key_map=enl_mapping)
            record_dict[Fields.ID] = record_dict[Fields.URL].replace(
                "https://aisel.aisnet.org/", ""
            )

        return records

    def load(self, load_operation: colrev.ops.load.Load) -> dict:
        """Load the records from the SearchSource file"""

        if not self.search_source.filename.is_file():
            return {}

        self._rename_erroneous_extensions()

        __load_methods = {
            ".ris": self._load_ris,
            ".bib": self._load_bib,
            ".csv": self._load_csv,
            ".xls": self._load_xlsx,
            ".xlsx": self._load_xlsx,
            ".md": self._load_md,
            ".enl": self._load_enl,
        }

        if self.search_source.filename.suffix not in __load_methods:
            raise NotImplementedError

        return __load_methods[self.search_source.filename.suffix](
            load_operation=load_operation
        )

    def _heuristically_fix_entrytypes(
        self, *, record: colrev.record.PrepRecord
    ) -> None:
        """Prepare the record by heuristically correcting erroneous ENTRYTYPEs"""

        # Journal articles should not have booktitles/series set.
        if record.data[Fields.ENTRYTYPE] == "article":
            if Fields.BOOKTITLE in record.data and Fields.JOURNAL not in record.data:
                record.update_field(
                    key=Fields.JOURNAL,
                    value=record.data[Fields.BOOKTITLE],
                    source="unkown_source_prep",
                )
                record.remove_field(key=Fields.BOOKTITLE)
            if Fields.SERIES in record.data and Fields.JOURNAL not in record.data:
                record.update_field(
                    key=Fields.JOURNAL,
                    value=record.data[Fields.SERIES],
                    source="unkown_source_prep",
                )
                record.remove_field(key=Fields.SERIES)

        if self.search_source.filename.suffix == ".md":
            if (
                record.data[Fields.ENTRYTYPE] == "misc"
                and Fields.PUBLISHER in record.data
            ):
                record.update_field(
                    key=Fields.ENTRYTYPE, value="book", source="unkown_source_prep"
                )
            if record.data.get(Fields.YEAR, Fields.YEAR) == record.data.get(
                "date", "date"
            ):
                record.remove_field(key="date")
            if (
                "inbook" == record.data[Fields.ENTRYTYPE]
                and Fields.CHAPTER not in record.data
                and Fields.TITLE in record.data
            ):
                record.rename_field(key=Fields.TITLE, new_key=Fields.CHAPTER)

        if (
            "dissertation" in record.data.get(Fields.FULLTEXT, "NA").lower()
            and record.data[Fields.ENTRYTYPE] != "phdthesis"
        ):
            prior_e_type = record.data[Fields.ENTRYTYPE]
            record.update_field(
                key=Fields.ENTRYTYPE, value="phdthesis", source="unkown_source_prep"
            )
            self.review_manager.report_logger.info(
                f" {record.data[Fields.ID]}".ljust(self._padding, " ")
                + f"Set from {prior_e_type} to phdthesis "
                '("dissertation" in fulltext link)'
            )

        if (
            "thesis" in record.data.get(Fields.FULLTEXT, "NA").lower()
            and record.data[Fields.ENTRYTYPE] != "phdthesis"
        ):
            prior_e_type = record.data[Fields.ENTRYTYPE]
            record.update_field(
                key=Fields.ENTRYTYPE, value="phdthesis", source="unkown_source_prep"
            )
            self.review_manager.report_logger.info(
                f" {record.data[Fields.ID]}".ljust(self._padding, " ")
                + f"Set from {prior_e_type} to phdthesis "
                '("thesis" in fulltext link)'
            )

        if (
            "this thesis" in record.data.get(Fields.ABSTRACT, "NA").lower()
            and record.data[Fields.ENTRYTYPE] != "phdthesis"
        ):
            prior_e_type = record.data[Fields.ENTRYTYPE]
            record.update_field(
                key=Fields.ENTRYTYPE, value="phdthesis", source="unkown_source_prep"
            )
            self.review_manager.report_logger.info(
                f" {record.data[Fields.ID]}".ljust(self._padding, " ")
                + f"Set from {prior_e_type} to phdthesis "
                '("thesis" in abstract)'
            )

    def _format_inproceedings(self, *, record: colrev.record.PrepRecord) -> None:
        if (
            record.data.get(Fields.BOOKTITLE, FieldValues.UNKNOWN)
            == FieldValues.UNKNOWN
        ):
            return

        if (
            FieldValues.UNKNOWN != record.data[Fields.BOOKTITLE]
            and "inbook" != record.data[Fields.ENTRYTYPE]
        ):
            # pylint: disable=colrev-missed-constant-usage
            record.format_if_mostly_upper(key=Fields.BOOKTITLE, case="title")

            stripped_btitle = re.sub(r"\d{4}", "", record.data[Fields.BOOKTITLE])
            stripped_btitle = re.sub(r"\d{1,2}th", "", stripped_btitle)
            stripped_btitle = re.sub(r"\d{1,2}nd", "", stripped_btitle)
            stripped_btitle = re.sub(r"\d{1,2}rd", "", stripped_btitle)
            stripped_btitle = re.sub(r"\d{1,2}st", "", stripped_btitle)
            stripped_btitle = re.sub(r"\([A-Z]{3,6}\)", "", stripped_btitle)
            stripped_btitle = stripped_btitle.replace("Proceedings of the", "").replace(
                "Proceedings", ""
            )
            stripped_btitle = stripped_btitle.lstrip().rstrip()
            record.update_field(
                key=Fields.BOOKTITLE,
                value=stripped_btitle,
                source="unkown_source_prep",
                keep_source_if_equal=True,
            )

    def _format_article(self, record: colrev.record.PrepRecord) -> None:
        if (
            record.data.get(Fields.JOURNAL, FieldValues.UNKNOWN) != FieldValues.UNKNOWN
            and len(record.data[Fields.JOURNAL]) > 10
            and FieldValues.UNKNOWN != record.data[Fields.JOURNAL]
        ):
            # pylint: disable=colrev-missed-constant-usage
            record.format_if_mostly_upper(key=Fields.JOURNAL, case="title")

        if record.data.get(Fields.VOLUME, FieldValues.UNKNOWN) != FieldValues.UNKNOWN:
            record.update_field(
                key=Fields.VOLUME,
                value=record.data[Fields.VOLUME].replace("Volume ", ""),
                source="unkown_source_prep",
                keep_source_if_equal=True,
            )

    def _format_fields(self, *, record: colrev.record.PrepRecord) -> None:
        """Format fields"""

        if record.data.get(Fields.ENTRYTYPE, "") == "inproceedings":
            self._format_inproceedings(record=record)
        elif record.data.get(Fields.ENTRYTYPE, "") == "article":
            self._format_article(record=record)

        if record.data.get(Fields.AUTHOR, FieldValues.UNKNOWN) != FieldValues.UNKNOWN:
            # fix name format
            if (1 == len(record.data[Fields.AUTHOR].split(" ")[0])) or (
                ", " not in record.data[Fields.AUTHOR]
            ):
                record.update_field(
                    key=Fields.AUTHOR,
                    value=colrev.record.PrepRecord.format_author_field(
                        input_string=record.data[Fields.AUTHOR]
                    ),
                    source="unkown_source_prep",
                    keep_source_if_equal=True,
                )
            # Replace nicknames in parentheses
            record.data[Fields.AUTHOR] = re.sub(
                r"\([^)]*\)", "", record.data[Fields.AUTHOR]
            )
            record.data[Fields.AUTHOR] = (
                record.data[Fields.AUTHOR].replace("  ", " ").rstrip()
            )

        if record.data.get(Fields.TITLE, FieldValues.UNKNOWN) != FieldValues.UNKNOWN:
            record.format_if_mostly_upper(key=Fields.TITLE)

        if Fields.PAGES in record.data:
            record.unify_pages_field()
            if (
                not re.match(r"^\d*$", record.data[Fields.PAGES])
                and not re.match(r"^\d*--\d*$", record.data[Fields.PAGES])
                and not re.match(r"^[xivXIV]*--[xivXIV]*$", record.data[Fields.PAGES])
            ):
                self.review_manager.report_logger.info(
                    f" {record.data[Fields.ID]}:".ljust(self._padding, " ")
                    + f"Unusual pages: {record.data[Fields.PAGES]}"
                )

        if (
            Fields.URL in record.data
            and Fields.FULLTEXT in record.data
            and record.data[Fields.URL] == record.data[Fields.FULLTEXT]
        ):
            record.remove_field(key=Fields.FULLTEXT)

        if Fields.LANGUAGE in record.data:
            try:
                self.language_service.unify_to_iso_639_3_language_codes(record=record)
                record.update_field(
                    key=Fields.LANGUAGE,
                    value=record.data[Fields.LANGUAGE],
                    source="unkown_source_prep",
                    keep_source_if_equal=True,
                )
            except colrev_exceptions.InvalidLanguageCodeException:
                del record.data[Fields.LANGUAGE]

    def _remove_redundant_fields(self, *, record: colrev.record.PrepRecord) -> None:
        if (
            record.data[Fields.ENTRYTYPE] == "article"
            and Fields.JOURNAL in record.data
            and Fields.BOOKTITLE in record.data
        ):
            similarity_journal_booktitle = fuzz.partial_ratio(
                record.data[Fields.JOURNAL].lower(),
                record.data[Fields.BOOKTITLE].lower(),
            )
            if similarity_journal_booktitle / 100 > 0.9:
                record.remove_field(key=Fields.BOOKTITLE)

        if record.data.get(Fields.PUBLISHER, "") in ["researchgate.net"]:
            record.remove_field(key=Fields.PUBLISHER)

        if (
            record.data[Fields.ENTRYTYPE] == "inproceedings"
            and Fields.JOURNAL in record.data
            and Fields.BOOKTITLE in record.data
        ):
            similarity_journal_booktitle = fuzz.partial_ratio(
                record.data[Fields.JOURNAL].lower(),
                record.data[Fields.BOOKTITLE].lower(),
            )
            if similarity_journal_booktitle / 100 > 0.9:
                record.remove_field(key=Fields.JOURNAL)

    def _impute_missing_fields(self, *, record: colrev.record.PrepRecord) -> None:
        if "date" in record.data and Fields.YEAR not in record.data:
            year = re.search(r"\d{4}", record.data["date"])
            if year:
                record.update_field(
                    key=Fields.YEAR,
                    value=year.group(0),
                    source="unkown_source_prep",
                    keep_source_if_equal=True,
                )

    def _unify_special_characters(self, *, record: colrev.record.PrepRecord) -> None:
        # Remove html entities
        for field in list(record.data.keys()):
            if field in [Fields.TITLE, Fields.AUTHOR, Fields.JOURNAL, Fields.BOOKTITLE]:
                record.data[field] = re.sub(r"\s+", " ", record.data[field])
                record.data[field] = re.sub(self.HTML_CLEANER, "", record.data[field])

    def prepare(
        self, record: colrev.record.PrepRecord, source: colrev.settings.SearchSource
    ) -> colrev.record.Record:
        """Source-specific preparation for unknown sources"""

        if not record.has_quality_defects() or record.masterdata_is_curated():
            return record

        # we may assign fields heuristically (e.g., to colrev.pubmed.pubmedid)

        self._heuristically_fix_entrytypes(
            record=record,
        )

        self._impute_missing_fields(record=record)

        self._format_fields(record=record)

        self._remove_redundant_fields(record=record)

        self._unify_special_characters(record=record)

        return record
