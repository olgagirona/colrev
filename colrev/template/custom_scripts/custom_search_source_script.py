#! /usr/bin/env python
"""Template for a custom SearchSource PackageEndpoint"""
from __future__ import annotations

from pathlib import Path

import zope.interface
from dacite import from_dict

import colrev.exceptions as colrev_exceptions
import colrev.operation
from colrev.constants import Fields


@zope.interface.implementer(
    colrev.env.package_manager.SearchSourcePackageEndpointInterface
)
class CustomSearch:
    """Class for custom search scripts"""

    settings_class = colrev.env.package_manager.DefaultSettings
    source_identifier = "custom"

    def __init__(
        self,
        *,
        source_operation: colrev.ops.search.Search,
        settings: dict,
    ) -> None:
        self.search_source = from_dict(data_class=self.settings_class, data=settings)
        self.review_manager = source_operation.review_manager

    def run_search(self, rerun: bool) -> None:
        """Run the search"""

        feed = self.search_source.get_api_feed(
            review_manager=self.review_manager,
            source_identifier=self.source_identifier,
            update_only=(not rerun),
            update_time_variant_fields=rerun,
        )
        retrieved_record = {
            Fields.ID: "ID00001",
            Fields.ENTRYTYPE: "article",
            Fields.AUTHOR: "Smith, M.",
            Fields.TITLE: "Editorial",
            Fields.JOURNAL: "nature",
            Fields.YEAR: "2020",
        }

        feed.add_update_record(retrieved_record)
        feed.save()

    @classmethod
    def validate_search_params(cls, query: str) -> None:
        """Validate the search parameters"""

        if " SCOPE " not in query:
            raise colrev_exceptions.InvalidQueryException(
                "CROSSREF queries require a SCOPE section"
            )

        scope = query[query.find(" SCOPE ") :]
        if "journal_issn" not in scope:
            raise colrev_exceptions.InvalidQueryException(
                "CROSSREF queries require a journal_issn field in the SCOPE section"
            )

    def heuristic(
        self, filename: Path, data: str  # pylint: disable=unused-argument
    ) -> dict:
        """Heuristic to identify the custom source"""

        result = {"confidence": 0}

        return result

    def load(
        self,
        load_operation: colrev.ops.load.Load,  # pylint: disable=unused-argument
    ) -> dict:
        """Load fixes for the custom source"""
        records = {"ID1": {"ID": "ID1", "title": "..."}}

        return records

    def prepare(self, record: colrev.record.Record) -> colrev.record.Record:
        """Source-specific preparation for the custom source"""

        return record
