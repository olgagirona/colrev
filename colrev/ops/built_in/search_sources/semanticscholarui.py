import datetime
import re
from typing import Optional

import inquirer


class SemanticScholarUI:
    """Implements the User Interface for the SemanticScholar API Search within colrev"""

    search_params: dict

    def __init__(self) -> None:
        self.search_params = {}
        self.search_subject = ""

    def main_ui(self) -> None:
        """Display the main Menu and choose the search type"""

        run = True

        print("\nWelcome to SemanticScholar! \n\n")
        while run:
            main_msg = "Please choose one of the options below "
            main_options = [
                "Keyword search",
                "Search for paper by ID",
                "Search for author by ID",
                "Exit Program",
            ]

            fwd_value = self.choose_single_option(msg=main_msg, options=main_options)

            if fwd_value == "Search for paper by ID":
                self.search_subject = "paper"
                run = self.paper_ui()

            elif fwd_value == "Search for author by ID":
                self.search_subject = "author"
                run = self.author_ui()

            elif fwd_value == "Keyword search":
                self.search_subject = "keyword"
                self.keyword_ui()
                run = False

            elif fwd_value == "Exit Program":
                print("\nThanks for using Colrev! This Program will close.")
                run = False
                raise SystemExit

        if not self.search_params:
            print("\n Search cancelled. This program will close.")
            raise SystemExit

    def paper_ui(self) -> bool:
        """Ask user to enter search parameters for distinctive paper search"""

        paper_id_list = []

        while True:
            validation_break = False

            p_msg = "How would you like to search for the paper?"
            p_options = [
                "S2PaperId",
                "CorpusId",
                "DOI",
                "ArXivId",
                "MAG",
                "ACL",
                "PMID",
                "PMCID",
            ]

            param = self.choose_single_option(msg=p_msg, options=p_options)

            if param in p_options:
                param_value = self.enter_text(
                    msg="Please enter the chosen ID in the right format "
                )
                if param == "S2PaperId":
                    while not self.id_validation_with_regex(
                        id_value=param_value, regex=r"^[a-zA-Z0-9]+$"
                    ) and (not validation_break):
                        param_value = self.enter_text(
                            msg="Error: Invalid S2PaperId format. Please try again or press Enter."
                        )
                        if not param_value:
                            validation_break = True

                elif param == "DOI":
                    while (
                        not self.id_validation_with_regex(
                            id_value=param_value, regex=r"^10\..+$"
                        )
                        and not validation_break
                    ):
                        param_value = self.enter_text(
                            msg="Error: Invalid DOI format. Please try again or press Enter."
                        )
                        if not param_value:
                            validation_break = True

                elif param == "ArXivId":
                    while (
                        not self.id_validation_with_regex(
                            id_value=param_value, regex=r"^\d+\.\d+$"
                        )
                        and not validation_break
                    ):
                        param_value = self.enter_text(
                            msg="Error: Invalid ArXivId format. Please try again or press Enter."
                        )
                        if not param_value:
                            validation_break = True

                elif param == "ACL":
                    while (
                        not self.id_validation_with_regex(
                            id_value=param_value, regex=r"^\w+-\w+$"
                        )
                        and not validation_break
                    ):
                        param_value = self.enter_text(
                            msg="Error: Invalid ACL ID format. Please try again or press Enter."
                        )
                        if not param_value:
                            validation_break = True

                else:
                    while (
                        not self.id_validation_with_regex(
                            id_value=param_value, regex=r"^[0-9]+$"
                        )
                        and not validation_break
                    ):
                        param_value = self.enter_text(
                            msg="Error: Invalid ID format. Please try again or press Enter."
                        )
                        if not param_value:
                            validation_break = True

                if not validation_break:
                    paper_id_list.append(param_value)
                    self.search_params["paper_ids"] = paper_id_list

            fwd = self.choose_single_option(
                msg="How would you like to continue?",
                options=[
                    "Conduct Search",
                    "Search for another paper or enter different ID",
                    "Back to main Menu",
                ],
            )

            if fwd == "Conduct Search":
                return False
            elif fwd == "Back to main Menu":
                return True

    def author_ui(self) -> bool:
        """Ask user to enter search parameters for distinctive author search"""

        author_id_list = []

        while True:
            validation_break = False

            param_value = self.enter_text(
                msg="Please enter an S2 author ID in the right format "
            )
            while (
                not self.id_validation_with_regex(
                    id_value=param_value, regex=r"^[a-zA-Z0-9]+$"
                )
                and not validation_break
            ):
                param_value = self.enter_text(
                    msg="Error: Invalid S2AuthorId format. Please try again or press Enter."
                )
                if not param_value:
                    validation_break = True

            if not validation_break:
                author_id_list.append(param_value)
                self.search_params["author_ids"] = author_id_list

            fwd = self.choose_single_option(
                msg="How would you like to continue?",
                options=[
                    "Conduct Search",
                    "Search for another author or enter different ID",
                    "Back to main Menu",
                ],
            )

            if fwd == "Conduct Search":
                return False
            elif fwd == "Back to main Menu":
                return True

    def keyword_ui(self) -> None:
        """Ask user to enter Searchstring and limitations for Keyword search"""

        query = self.enter_text(msg="Please enter the query for your keyword search ")
        while not (query and isinstance(query, str)):
            query = self.enter_text(
                msg="Error: You must enter a query to conduct a search. Please enter a query"
            )

        self.search_params["query"] = query

        year = self.enter_year()
        if year:
            self.search_params["year"] = year

        publication_types = self.enter_pub_types()
        if publication_types:
            self.search_params["publication_types"] = publication_types

        venue = self.enter_text(
            msg="To search for papers from specific venues, enter the venues here."
            + " Separate multiple venues by comma."
            + " Please press Enter to not specify any venues "
        )
        if venue:
            self.search_params["venue"] = venue.split(",")

        fields_of_study = self.enter_study_fields()
        if fields_of_study:
            self.search_params["fields_of_study"] = fields_of_study

        open_access = self.choose_single_option(
            msg="Would you like to only search for items for which the full text is available as pdf?",
            options=["YES", "NO"],
        )
        if open_access == "YES":
            self.search_params["open_access_pdf"] = True
        else:
            self.search_params["open_access_pdf"] = False

    def get_api_key(self, existing_key: str = None) -> str:
        """Method to get API key from user input"""

        ask_again = True

        if existing_key:
            api_key = existing_key
        else:
            api_key = self.enter_text(
                msg="Please enter a valid API key for SemanticScholar. "
                "If you don't have a key, please press Enter."
            )

        while ask_again:
            ask_again = False

            if not api_key:
                print(
                    "\nWARNING: Searching without an API key might not be successful. \n"
                )
                fwd = self.choose_single_option(
                    msg="Would you like to continue?", options=["YES", "NO"]
                )

                if fwd == "NO":
                    api_key = self.enter_text(msg="Please enter an API key ")
                    ask_again = True
                else:
                    return ""

            elif not re.match(r"^\w{40}$", api_key):
                print("Error: Invalid API key.\n")
                fwd = self.choose_single_option(
                    msg="Would you like to enter a different key?",
                    options=["YES", "NO"],
                )

                if fwd == "YES":
                    api_key = self.enter_text(msg="Please enter an API key ")
                    ask_again = True
                else:
                    return ""

            else:
                print("\n" + "API key: " + api_key + "\n")
                fwd = self.choose_single_option(
                    msg="Start search with this API key?", options=["YES", "NO"]
                )

                if fwd == "NO":
                    api_key = self.enter_text(msg="Please enter a different API key ")
                    ask_again = True

        return api_key

    def enter_year(self) -> str:
        """Method to ask a specific year span in the format allowed by the SemanticScholar API"""

        examples = (
            "Examples for valid year spans: '2019'; '2012-2020'; '-2022'; '2015-'"
        )
        ask_again = True
        year_span = self.enter_text(
            msg="Please enter a year span. Please press Enter if you don't wish to specify a year span"
        )
        while year_span and ask_again:
            ask_again = False
            if not re.match(
                "|".join([r"^-\d{4}$", r"^\d{4}-?$", r"^\d{4}-\d{4}$"]), year_span
            ):
                print("Error: Invalid year span.\n" + examples + "\n")
                year_span = self.enter_text(
                    msg="Please enter a year span."
                    + " Please press Enter if you don't wish to specify a year span"
                )
                ask_again = True
            elif re.match(r"^\d{4}-\d{4}", year_span):
                years = year_span.split("-")
                a = int(years[0])

                if int(years[1]):
                    b = int(years[1])
                    if (not a < b) or (b > int(datetime.date.today().year)):
                        print("Error: Invalid year span.\n" + examples + "\n")
                        year_span = self.enter_text(
                            msg="Please enter a year span."
                            + " Please press Enter if you don't wish to specify a year span"
                        )
                        ask_again = True
            elif re.match(r"^-?\d{4}-?$", year_span):
                year = int(re.findall(r"\d{4}", year_span)[0])
                if year > int(datetime.date.today().year):
                    print(
                        "Error: Invalid year span. You cannot search for papers from the future.\n"
                    )
                    year_span = self.enter_text(
                        msg="Please enter a year span."
                        + " Please press Enter if you don't wish to specify a year span"
                    )
                    ask_again = True

        return year_span

    def enter_pub_types(self) -> list:
        """Method to ask a selection of publication types that are allowed by the SemanticScholar API"""

        msg = (
            "Please choose the publication types. "
            "If you want to include all publication types, please press Enter"
        )
        options = [
            "Review",
            "JournalArticle",
            "CaseReport",
            "ClinicalTrial",
            "Dataset",
            "Editorial",
            "LettersAndComments",
            "MetaAnalysis",
            "News",
            "Study",
            "Book",
            "BookSection",
        ]
        pub_types = self.choose_multiple_options(msg=msg, options=options)

        return pub_types

    def enter_study_fields(self) -> list:
        """Method to ask a selection of fields of study that are allowed by the SemanticScholar API"""

        msg = "If you want to restrict your search to certain study fields, select them here or press Enter"
        options = [
            "Computer Science",
            "Medicine",
            "Chemistry",
            "Biology",
            "Materials Science",
            "Physics",
            "Geology",
            "Psychology",
            "Art",
            "History",
            "Geography",
            "Sociology",
            "Business",
            "Political Science",
            "Economics",
            "Philosophy",
            "Mathematics",
            "Engineering",
            "Environmental Science",
            "Agricultural and Food Sciences",
            "Education",
            "Law",
            "Linguistics",
        ]
        study_fields = self.choose_multiple_options(msg=msg, options=options)

        return study_fields

    def choose_single_option(
        self,
        *,
        msg: str,
        options: list,
    ) -> str:
        """Method to display a question with single choice answers to the console using inquirer"""

        question = [
            inquirer.List(
                name="Choice",
                message=msg,
                choices=["%s" % i for i in options],
                carousel=False,
            ),
        ]
        choice = inquirer.prompt(questions=question)

        return choice.get("Choice")

    def choose_multiple_options(
        self,
        *,
        msg: str,
        options: list,
    ) -> list:
        """Method to display a question with multiple choice answers to the console using inquirer"""

        question = [
            inquirer.Checkbox(
                name="Choice",
                message=msg,
                choices=["%s" % i for i in options],
                carousel=False,
            ),
        ]
        choice = inquirer.prompt(questions=question)

        return choice.get("Choice")

    def enter_text(
        self,
        *,
        msg: str,
    ) -> str:
        """Method to display a question with free text entry answer to the console using inquirer."""

        question = [
            inquirer.Text(
                name="Entry",
                message=msg,
            )
        ]
        choice = inquirer.prompt(questions=question)

        return choice.get("Entry")

    def id_validation_with_regex(
        self,
        *,
        id_value: str,
        regex: re,
    ) -> bool:
        """Method to validate ID formats using a regex as an argument"""

        if re.match(regex, id_value):
            return True

        return False
