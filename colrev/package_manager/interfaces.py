#! /usr/bin/env python
"""Package interfaces."""
from __future__ import annotations

import typing
from pathlib import Path

import zope.interface

from colrev.constants import PackageEndpointType

if typing.TYPE_CHECKING:  # pragma: no cover
    import colrev.process.operation
    import colrev.record.record
    import colrev.settings
    from colrev.constants import SearchSourceHeuristicStatus

# pylint: disable=too-many-ancestors


# pylint: disable=too-few-public-methods
class GeneralInterface(zope.interface.Interface):  # pylint: disable=inherit-non-class
    """The General Interface for all package endpoints

    Each package endpoint must implement the following attributes (methods)"""

    ci_supported = zope.interface.Attribute(
        """Flag indicating whether the package can be run in
        continuous integration environments (e.g. GitHub Actions)"""
    )


# pylint: disable=too-few-public-methods
class ReviewTypePackageEndpointInterface(
    GeneralInterface, zope.interface.Interface
):  # pylint: disable=inherit-non-class
    """The PackageEndpoint interface for ReviewTypes"""

    # pylint: disable=no-self-argument
    def initialize(settings: dict) -> dict:  # type: ignore
        """Initialize the review type"""
        return settings  # pragma: no cover


class SearchSourcePackageEndpointInterface(
    GeneralInterface, zope.interface.Interface
):  # pylint: disable=inherit-non-class
    """The PackageEndpoint interface for SearchSources"""

    settings_class = zope.interface.Attribute("""Class for the package settings""")
    source_identifier = zope.interface.Attribute(
        """Source identifier for search and provenance
        Retrieved records are identified through the source_identifier
        when they are added to/updated in the SearchAPIFeed"""
    )
    search_types = zope.interface.Attribute(
        """SearchTypes associated with the SearchSource"""
    )

    heuristic_status: SearchSourceHeuristicStatus = zope.interface.Attribute(
        """The status of the SearchSource heuristic"""
    )
    short_name = zope.interface.Attribute("""Short name of the SearchSource""")
    docs_link = zope.interface.Attribute("""Link to the SearchSource website""")

    # pylint: disable=no-self-argument
    def heuristic(filename: Path, data: str):  # type: ignore
        """Heuristic to identify the SearchSource"""

    # pylint: disable=no-self-argument
    def add_endpoint(  # type: ignore
        operation: colrev.process.operation.Operation,
        params: dict,
    ) -> colrev.settings.SearchSource:
        """Add the SearchSource as an endpoint based on a query (passed to colrev search -a)
        params:
        - search_file="..." to add a DB search
        """

    # pylint: disable=no-self-argument
    def search(rerun: bool) -> None:  # type: ignore
        """Run a search of the SearchSource"""

    # pylint: disable=no-self-argument
    def prep_link_md(  # type: ignore
        prep_operation: colrev.ops.prep.Prep,
        record: colrev.record.record.Record,
        save_feed: bool = True,
        timeout: int = 10,
    ):
        """Retrieve masterdata from the SearchSource"""

    # pylint: disable=no-self-argument
    def load(  # type: ignore
        load_operation: colrev.ops.load.Load,
    ) -> dict:
        """Load records from the SearchSource (and convert to .bib)"""

    # pylint: disable=no-self-argument
    def prepare(record: dict, source: colrev.settings.SearchSource) -> None:  # type: ignore
        """Run the custom source-prep operation"""


# pylint: disable=too-few-public-methods
class PrepPackageEndpointInterface(
    GeneralInterface, zope.interface.Interface
):  # pylint: disable=inherit-non-class
    """The PackageEndpoint interface for prep operations"""

    settings_class = zope.interface.Attribute("""Class for the package settings""")
    source_correction_hint = zope.interface.Attribute(
        """Hint on how to correct metadata at source"""
    )

    always_apply_changes = zope.interface.Attribute(
        """Flag indicating whether changes should always be applied
        (even if the colrev_status does not transition to md_prepared)"""
    )

    # pylint: disable=no-self-argument
    def prepare(prep_record: dict) -> dict:  # type: ignore
        """Run the prep operation"""


# pylint: disable=too-few-public-methods
class PrepManPackageEndpointInterface(
    GeneralInterface, zope.interface.Interface
):  # pylint: disable=inherit-non-class
    """The PackageEndpoint interface for prep-man operations"""

    settings_class = zope.interface.Attribute("""Class for the package settings""")

    # pylint: disable=no-self-argument
    def prepare_manual(records: dict) -> dict:  # type: ignore
        """Run the prep-man operation"""


# pylint: disable=too-few-public-methods
class DedupePackageEndpointInterface(
    GeneralInterface, zope.interface.Interface
):  # pylint: disable=inherit-non-class
    """The PackageEndpoint interface for dedupe operations"""

    settings_class = zope.interface.Attribute("""Class for the package settings""")

    # pylint: disable=no-self-argument
    # pylint: disable=no-method-argument
    def run_dedupe() -> None:  # type: ignore
        """Run the dedupe operation"""


# pylint: disable=too-few-public-methods
class PrescreenPackageEndpointInterface(
    GeneralInterface, zope.interface.Interface
):  # pylint: disable=inherit-non-class
    """The PackageEndpoint interface for prescreen operations"""

    settings_class = zope.interface.Attribute("""Class for the package settings""")

    # pylint: disable=no-self-argument
    def run_prescreen(records: dict, split: list) -> dict:  # type: ignore
        """Run the prescreen operation"""


# pylint: disable=too-few-public-methods
class PDFGetPackageEndpointInterface(
    GeneralInterface, zope.interface.Interface
):  # pylint: disable=inherit-non-class
    """The PackageEndpoint interface for pdf-get operations"""

    settings_class = zope.interface.Attribute("""Class for the package settings""")

    # pylint: disable=no-self-argument
    def get_pdf(record: dict) -> dict:  # type: ignore
        """Run the pdf-get operation"""
        return record  # pragma: no cover


# pylint: disable=too-few-public-methods
class PDFGetManPackageEndpointInterface(
    GeneralInterface, zope.interface.Interface
):  # pylint: disable=inherit-non-class
    """The PackageEndpoint interface for pdf-get-man operations"""

    settings_class = zope.interface.Attribute("""Class for the package settings""")

    # pylint: disable=no-self-argument
    def pdf_get_man(records: dict) -> dict:
        """Run the pdf-get-man operation"""
        return records  # pragma: no cover


# pylint: disable=too-few-public-methods
class PDFPrepPackageEndpointInterface(
    GeneralInterface, zope.interface.Interface
):  # pylint: disable=inherit-non-class
    """The PackageEndpoint interface for pdf-prep operations"""

    settings_class = zope.interface.Attribute("""Class for the package settings""")

    # pylint: disable=unused-argument
    # pylint: disable=no-self-argument
    def prep_pdf(  # type: ignore
        record: colrev.record.record_pdf.PDFRecord,
        pad: int,
    ) -> dict:
        """Run the prep-pdf operation"""
        return record.data  # pragma: no cover


# pylint: disable=too-few-public-methods
class PDFPrepManPackageEndpointInterface(
    GeneralInterface, zope.interface.Interface
):  # pylint: disable=inherit-non-class
    """The PackageEndpoint interface for pdf-prep-man operations"""

    settings_class = zope.interface.Attribute("""Class for the package settings""")

    # pylint: disable=no-self-argument
    def pdf_prep_man(records: dict) -> dict:
        """Run the prep-man operation"""
        return records  # pragma: no cover


# pylint: disable=too-few-public-methods
class ScreenPackageEndpointInterface(
    GeneralInterface, zope.interface.Interface
):  # pylint: disable=inherit-non-class
    """The PackageEndpoint interface for screen operations"""

    settings_class = zope.interface.Attribute("""Class for the package settings""")

    # pylint: disable=no-self-argument
    def run_screen(records: dict, split: list) -> dict:  # type: ignore
        """Run the screen operation"""


class DataPackageEndpointInterface(
    GeneralInterface, zope.interface.Interface
):  # pylint: disable=inherit-non-class
    """The PackageEndpoint interface for data operations"""

    settings_class = zope.interface.Attribute("""Class for the package settings""")

    # pylint: disable=no-self-argument

    def update_data(  # type: ignore
        records: dict,
        synthesized_record_status_matrix: dict,
        silent_mode: bool,
    ) -> None:
        """
        Update the data by running the data operation. This includes data extraction,
        analysis, and synthesis.

        Parameters:
        records (dict): The records to be updated.
        synthesized_record_status_matrix (dict): The status matrix for the synthesized records.
        silent_mode (bool): Whether the operation is run in silent mode
        (for checks of review_manager/status).
        """

    def update_record_status_matrix(  # type: ignore
        synthesized_record_status_matrix: dict,
        endpoint_identifier: str,
    ) -> None:
        """Update the record status matrix,
        i.e., indicate whether the record is rev_synthesized for the given endpoint_identifier
        """

    # pylint: disable=no-method-argument
    def get_advice() -> dict:  # type: ignore
        """Get advice on how to operate the data package endpoint"""


PACKAGE_TYPE_OVERVIEW = {
    PackageEndpointType.review_type: {
        "import_name": ReviewTypePackageEndpointInterface,
        "custom_class": "CustomReviewType",
        "operation_name": "operation",
    },
    PackageEndpointType.search_source: {
        "import_name": SearchSourcePackageEndpointInterface,
        "custom_class": "CustomSearchSource",
        "operation_name": "source_operation",
    },
    PackageEndpointType.prep: {
        "import_name": PrepPackageEndpointInterface,
        "custom_class": "CustomPrep",
        "operation_name": "prep_operation",
    },
    PackageEndpointType.prep_man: {
        "import_name": PrepManPackageEndpointInterface,
        "custom_class": "CustomPrepMan",
        "operation_name": "prep_man_operation",
    },
    PackageEndpointType.dedupe: {
        "import_name": DedupePackageEndpointInterface,
        "custom_class": "CustomDedupe",
        "operation_name": "dedupe_operation",
    },
    PackageEndpointType.prescreen: {
        "import_name": PrescreenPackageEndpointInterface,
        "custom_class": "CustomPrescreen",
        "operation_name": "prescreen_operation",
    },
    PackageEndpointType.pdf_get: {
        "import_name": PDFGetPackageEndpointInterface,
        "custom_class": "CustomPDFGet",
        "operation_name": "pdf_get_operation",
    },
    PackageEndpointType.pdf_get_man: {
        "import_name": PDFGetManPackageEndpointInterface,
        "custom_class": "CustomPDFGetMan",
        "operation_name": "pdf_get_man_operation",
    },
    PackageEndpointType.pdf_prep: {
        "import_name": PDFPrepPackageEndpointInterface,
        "custom_class": "CustomPDFPrep",
        "operation_name": "pdf_prep_operation",
    },
    PackageEndpointType.pdf_prep_man: {
        "import_name": PDFPrepManPackageEndpointInterface,
        "custom_class": "CustomPDFPrepMan",
        "operation_name": "pdf_prep_man_operation",
    },
    PackageEndpointType.screen: {
        "import_name": ScreenPackageEndpointInterface,
        "custom_class": "CustomScreen",
        "operation_name": "screen_operation",
    },
    PackageEndpointType.data: {
        "import_name": DataPackageEndpointInterface,
        "custom_class": "CustomData",
        "operation_name": "data_operation",
    },
}