#!/usr/bin/env python
"""Test the PLOS SearchSource"""
from pathlib import Path

import pytest
import requests_mock

import colrev.ops.prep
import colrev.packages.plos.src.plos_search_source
import colrev.record.record
import colrev.record.record_prep
from colrev.packages.plos.src import plos_api


@pytest.mark.parametrize(
    "doi, expected_dict",
    [
        (
            "10.1371/journal.pone.0022081",
            {
                "doi": "10.1371/JOURNAL.PONE.0022081",
                "ENTRYTYPE": "article",
                "author": "Burastero, Samuele E. and Frigerio, Barbara and Lopalco, Lucia and Sironi, Francesca and Breda, Daniela and Longhi, Renato and Scarlatti, Gabriella "
                "and Canevari, Silvana and Figini, Mariangela and Lusso, Paolo",
                "journal": "PLoS ONE",
                "title": "Broad-Spectrum Inhibition of HIV-1 by a Monoclonal Antibody Directed against a gp120-Induced Epitope of CD4",
                "abstract": "To penetrate susceptible cells, HIV-1 sequentially interacts with two highly conserved cellular receptors, CD4 and a chemokine receptor like CCR5 or CXCR4."
                 " Monoclonal antibodies (MAbs) directed against such receptors are currently under clinical investigation as potential preventive or therapeutic agents."
                  " We immunized Balb/c mice with molecular complexes of the native, trimeric HIV-1 envelope (Env) bound to a soluble form of the human CD4 receptor. Sera from " 
                  "immunized mice were found to contain gp120-CD4 complex-enhanced antibodies and showed broad-spectrum HIV-1-inhibitory activity. A proportion of MAbs derived "
                  "from these mice preferentially recognized complex-enhanced epitopes. In particular, a CD4-specific MAb designated DB81 (IgG1Κ) was found to preferentially bind "
                  "to a complex-enhanced epitope on the D2 domain of human CD4. MAb DB81 also recognized chimpanzee CD4, but not baboon or macaque CD4, which exhibit sequence divergence "
                  "in the D2 domain. Functionally, MAb DB81 displayed broad HIV-1-inhibitory activity, but it did not exert suppressive effects on T-cell activation in vitro. The variable "
                   "regions of the heavy and light chains of MAb DB81 were sequenced. Due to its broad-spectrum anti-HIV-1 activity and lack of immunosuppressive effects, a humanized derivative "
                   "of MAb DB81 could provide a useful complement to current preventive or therapeutic strategies against HIV-1.",
                "year": "2011",
            },
        ),
        
    ],
)
def test_plos_query(doi: str, expected_dict: dict) -> None:
    api = plos_api.PlosAPI(params={})

    filename = Path(__file__).parent / f"data/{doi.replace('/', '_')}.json"
    print(filename)

    with open(filename, encoding="utf-8") as file:
        json_str = file.read()



    with requests_mock.Mocker() as req_mock:
        # https://api.plos.org/solr/examples/
        req_mock.get(
            f"https://api.plos.org/search?q=id:{doi}", content=json_str.encode("utf-8")
        )
        print(f"https://api.plos.org/search?q=id:{doi}")
 

        actual = api.query_doi(doi=doi)
        expected = colrev.record.record_prep.PrepRecord(expected_dict)
        print("el actual es" + str(actual.data))
        print("el expected es" + str(expected.data))
        assert actual.data == expected.data
