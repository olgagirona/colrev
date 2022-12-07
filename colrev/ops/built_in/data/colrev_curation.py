#! /usr/bin/env python
"""Creation of a markdown manuscript as part of the data operations"""
from __future__ import annotations

import collections
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING

import zope.interface
from dacite import from_dict
from dataclasses_jsonschema import JsonSchemaMixin
from tqdm import tqdm

import colrev.env.package_manager
import colrev.env.utils
import colrev.record

if TYPE_CHECKING:
    import colrev.ops.data


@zope.interface.implementer(colrev.env.package_manager.DataPackageEndpointInterface)
@dataclass
class ColrevCuration(JsonSchemaMixin):
    """CoLRev Curation"""

    @dataclass
    class ColrevCurationSettings(JsonSchemaMixin):
        """Colrev Curation settings"""

        endpoint: str
        version: str
        curation_url: str
        curated_masterdata: bool
        masterdata_restrictions: dict
        curated_fields: list

    settings_class = ColrevCurationSettings

    def __init__(
        self,
        *,
        data_operation: colrev.ops.data.Data,
        settings: dict,
    ) -> None:

        # Set default values (if necessary)
        if "version" not in settings:
            settings["version"] = "0.1"

        self.settings = from_dict(
            data_class=self.settings_class,
            data=settings,
        )

        self.data_operation = data_operation

    def get_default_setup(self) -> dict:
        """Get the default setup"""

        curation_endpoint_details = {
            "endpoint": "colrev_built_in.colrev_curation",
            "version": "0.1",
            "curation_url": "TODO",
            "curated_masterdata": True,
            "masterdata_restrictions": {
                1900: {
                    "ENTRYTYPE": "article",
                    "journal": "TODO",
                    "volume": True,
                    "number": True,
                }
            },
            "curated_fields": ["doi", "url"],
        }

        return curation_endpoint_details

    def __get_stats(
        self,
        *,
        records: dict,
        review_manager: colrev.review_manager.ReviewManager,
        sources: list,
    ) -> dict:

        # pylint: disable=too-many-branches

        stats: dict = {}
        for record_dict in tqdm(records.values()):
            r_status = str(record_dict["colrev_status"])
            if r_status == "rev_prescreen_excluded":
                continue
            if record_dict[
                "colrev_status"
            ] in colrev.record.RecordState.get_post_x_states(
                state=colrev.record.RecordState.md_processed
            ):
                r_status = str(colrev.record.RecordState.md_processed)
            elif record_dict[
                "colrev_status"
            ] in colrev.record.RecordState.get_post_x_states(
                state=colrev.record.RecordState.pdf_prepared
            ):
                r_status = str(colrev.record.RecordState.pdf_prepared)
            else:
                r_status = str(colrev.record.RecordState.md_imported)

            if "journal" in record_dict:
                key = (
                    f"{record_dict.get('year','-')}-"
                    f"{record_dict.get('volume','-')}-"
                    f"{record_dict.get('number','-')}"
                )
            elif "booktitle" in record_dict:
                key = record_dict.get("year", "-")
            else:
                review_manager.logger.error(f"TOC not supported: {record_dict}")
                continue
            if not all(
                source in [o.split("/")[0] for o in record_dict["colrev_origin"]]
                for source in sources
            ):
                if key in stats:
                    if "all_merged" in stats[key]:
                        stats[key]["all_merged"] = "NO"
                    else:
                        stats[key]["all_merged"] = "NO"
                else:
                    stats[key] = {"all_merged": "NO"}

            for origin in record_dict["colrev_origin"]:
                source = origin.split("/")[0]
                if key in stats:
                    if source in stats[key]:
                        if r_status in stats[key][source]:
                            stats[key][source][r_status] += 1
                        else:
                            stats[key][source][r_status] = 1
                    else:
                        stats[key][source] = {r_status: 1}
                else:
                    stats[key] = {source: {r_status: 1}}
        return stats

    def __get_stats_markdown_table(self, *, stats: dict, sources: list) -> str:

        cell_width = 16
        sources += ["all_merged"]
        output = "|TOC".ljust(cell_width - 1, " ") + "|"
        sub_header_lines = "|".ljust(cell_width - 1, "-") + "|"
        for source in sources:
            output += f"{source}".ljust(cell_width, " ") + "|"
            sub_header_lines += "".ljust(cell_width, "-") + "|"

        output += "\n" + sub_header_lines
        ordered_stats = collections.OrderedDict(sorted(stats.items(), reverse=True))

        for key, row in ordered_stats.items():

            output += f"\n|{key}".ljust(cell_width, " ") + "|"
            for source in sources:
                if source != "all_merged":
                    if source in row:
                        cell_text = ""
                        if "md_imported" in row[source]:
                            cell_text += f"*{row[source]['md_imported']}*"
                        if "md_processed" in row[source]:
                            cell_text += f"{row[source]['md_processed']}"
                        if "pdf_prepared" in row[source]:
                            cell_text += f"**{row[source]['pdf_prepared']}**"

                        output += cell_text.rjust(cell_width, " ") + "|"
                    else:
                        output += "-".rjust(cell_width, " ") + "|"
                else:
                    output += row.get("all_merged", "").rjust(cell_width, " ") + "|"

        output += "\n\nLegend: *md_imported*, md_processed, **pdf_prepared**"
        return output

    def __update_table_in_readme(
        self,
        *,
        review_manager: colrev.review_manager.ReviewManager,
        markdown_output: str,
    ) -> None:
        # pylint: disable=duplicate-code
        table_summary_tag = "<!-- TABLE_SUMMARY -->"
        readme_path = review_manager.readme
        with open(readme_path, "r+b") as file:
            appended = False
            seekpos = file.tell()
            line = file.readline()
            while line:
                if table_summary_tag.encode("utf-8") in line:
                    line = file.readline()
                    while (
                        b"Legend: *md_imported*, md_processed" not in line and line
                    ):  # replace: drop the current record
                        line = file.readline()

                    remaining = file.read()
                    file.seek(seekpos)
                    file.write(table_summary_tag.encode("utf-8"))
                    file.write(b"\n\n")
                    file.write(markdown_output.encode("utf-8"))
                    file.write(b"\n")
                    seekpos = file.tell()
                    file.flush()
                    os.fsync(file)
                    file.write(remaining)
                    file.truncate()  # if the replacement is shorter...
                    file.seek(seekpos)
                    appended = True

                seekpos = file.tell()
                line = file.readline()
            if not appended:
                file.write(b"\n")
                file.write(table_summary_tag.encode("utf-8"))
                file.write(b"\n\n")
                file.write(markdown_output.encode("utf-8"))
                file.write(b"\n")

    def __update_stats_in_readme(
        self,
        *,
        records: dict,
        review_manager: colrev.review_manager.ReviewManager,
    ) -> None:

        review_manager.logger.info("Calculate statistics for readme")

        # alternatively: get sources from search_sources.filename (name/stem?)
        sources = []
        for record_dict in records.values():
            for origin in record_dict["colrev_origin"]:
                source = origin.split("/")[0]
                if source not in sources:
                    sources.append(source)

        stats = self.__get_stats(
            records=records, review_manager=review_manager, sources=sources
        )
        markdown_output = self.__get_stats_markdown_table(stats=stats, sources=sources)
        self.__update_table_in_readme(
            review_manager=review_manager, markdown_output=markdown_output
        )
        review_manager.dataset.add_changes(path=review_manager.README_RELATIVE)

    def update_data(
        self,
        data_operation: colrev.ops.data.Data,
        records: dict,
        synthesized_record_status_matrix: dict,  # pylint: disable=unused-argument
    ) -> None:
        """Update the CoLRev curation"""

        for record_dict in records.values():
            if (
                record_dict["colrev_status"]
                == colrev.record.RecordState.rev_prescreen_excluded
            ):
                continue
            if record_dict.get("year", "UNKNOWN") == "UNKNOWN":
                record_dict[
                    "colrev_status"
                ] = colrev.record.RecordState.md_needs_manual_preparation
                colrev.record.Record(data=record_dict).add_masterdata_provenance(
                    key="year",
                    source="colrev_curation.masterdata_restrictions",
                    note="missing",
                )
                continue

            applicable_restrictions = (
                data_operation.review_manager.dataset.get_applicable_restrictions(
                    record_dict=record_dict,
                )
            )

            colrev.record.Record(data=record_dict).apply_restrictions(
                restrictions=applicable_restrictions
            )

        self.__update_stats_in_readme(
            records=records, review_manager=data_operation.review_manager
        )

    def update_record_status_matrix(
        self,
        data_operation: colrev.ops.data.Data,  # pylint: disable=unused-argument
        synthesized_record_status_matrix: dict,
        endpoint_identifier: str,
    ) -> None:
        """Update the record_status_matrix"""

        for item in synthesized_record_status_matrix:
            synthesized_record_status_matrix[item][endpoint_identifier] = True


if __name__ == "__main__":
    pass