import json
from datetime import date, datetime
from unittest.mock import patch

import eyecite
import pytest
from factory import RelatedFactory

from cl.corpus_importer.court_regexes import match_court_string
from cl.corpus_importer.factories import (
    CaseBodyFactory,
    CaseLawCourtFactory,
    CaseLawFactory,
    CitationFactory,
)
from cl.corpus_importer.import_columbia.parse_opinions import (
    get_state_court_object,
)
from cl.corpus_importer.management.commands.harvard_merge import (
    combine_non_overlapping_data,
    merge_judges,
    merge_opinion_clusters,
)
from cl.corpus_importer.management.commands.harvard_opinions import (
    clean_body_content,
    compare_documents,
    parse_harvard_opinions,
    validate_dt,
    winnow_case_name,
)
from cl.corpus_importer.management.commands.normalize_judges_opinions import (
    normalize_authors_in_opinions,
    normalize_panel_in_opinioncluster,
)
from cl.corpus_importer.tasks import generate_ia_json
from cl.corpus_importer.utils import get_start_of_quarter
from cl.lib.pacer import process_docket_data
from cl.people_db.factories import PersonWithChildrenFactory, PositionFactory
from cl.people_db.lookup_utils import extract_judge_last_name
from cl.people_db.models import Attorney, AttorneyOrganization, Party
from cl.recap.models import UPLOAD_TYPE
from cl.search.factories import (
    CourtFactory,
    DocketFactory,
    OpinionClusterFactory,
    OpinionClusterFactoryMultipleOpinions,
    OpinionClusterFactoryWithChildrenAndParents,
    OpinionClusterWithParentsFactory,
    OpinionFactory,
    OpinionWithChildrenFactory,
)
from cl.search.models import (
    Citation,
    Court,
    Docket,
    Opinion,
    OpinionCluster,
    RECAPDocument,
)
from cl.settings import MEDIA_ROOT
from cl.tests.cases import SimpleTestCase, TestCase


class JudgeExtractionTest(SimpleTestCase):
    def test_get_judge_from_string_columbia(self) -> None:
        """Can we cleanly get a judge value from a string?"""
        tests = (
            (
                "CLAYTON <italic>Ch. Jus. of the Superior Court,</italic> "
                "delivered the following opinion of this Court: ",
                ["clayton"],
            ),
            ("OVERTON, J. &#8212; ", ["overton"]),
            ("BURWELL, J.:", ["burwell"]),
        )
        for q, a in tests:
            self.assertEqual(extract_judge_last_name(q), a)


class CourtMatchingTest(SimpleTestCase):
    """Tests related to converting court strings into court objects."""

    def test_get_court_object_from_string(self) -> None:
        """Can we get a court object from a string and filename combo?

        When importing the Columbia corpus, we use a combination of regexes and
        the file path to determine a match.
        """
        pairs = (
            {
                "args": (
                    "California Superior Court  "
                    "Appellate Division, Kern County.",
                    "california/supreme_court_opinions/documents"
                    "/0dc538c63bd07a28.xml",
                    # noqa
                ),
                "answer": "calappdeptsuperct",
            },
            {
                "args": (
                    "California Superior Court  "
                    "Appellate Department, Sacramento.",
                    "california/supreme_court_opinions/documents"
                    "/0dc538c63bd07a28.xml",
                    # noqa
                ),
                "answer": "calappdeptsuperct",
            },
            {
                "args": (
                    "Appellate Session of the Superior Court",
                    "connecticut/appellate_court_opinions/documents"
                    "/0412a06c60a7c2a2.xml",
                    # noqa
                ),
                "answer": "connsuperct",
            },
            {
                "args": (
                    "Court of Errors and Appeals.",
                    "new_jersey/supreme_court_opinions/documents"
                    "/0032e55e607f4525.xml",
                    # noqa
                ),
                "answer": "nj",
            },
            {
                "args": (
                    "Court of Chancery",
                    "new_jersey/supreme_court_opinions/documents"
                    "/0032e55e607f4525.xml",
                    # noqa
                ),
                "answer": "njch",
            },
            {
                "args": (
                    "Workers' Compensation Commission",
                    "connecticut/workers_compensation_commission/documents"
                    "/0902142af68ef9df.xml",
                    # noqa
                ),
                "answer": "connworkcompcom",
            },
            {
                "args": (
                    "Appellate Session of the Superior Court",
                    "connecticut/appellate_court_opinions/documents"
                    "/00ea30ce0e26a5fd.xml",
                    # noqa
                ),
                "answer": "connsuperct",
            },
            {
                "args": (
                    "Superior Court  New Haven County",
                    "connecticut/superior_court_opinions/documents"
                    "/0218655b78d2135b.xml",
                    # noqa
                ),
                "answer": "connsuperct",
            },
            {
                "args": (
                    "Superior Court, Hartford County",
                    "connecticut/superior_court_opinions/documents"
                    "/0218655b78d2135b.xml",
                    # noqa
                ),
                "answer": "connsuperct",
            },
            {
                "args": (
                    "Compensation Review Board  "
                    "WORKERS' COMPENSATION COMMISSION",
                    "connecticut/workers_compensation_commission/documents"
                    "/00397336451f6659.xml",
                    # noqa
                ),
                "answer": "connworkcompcom",
            },
            {
                "args": (
                    "Appellate Division Of The Circuit Court",
                    "connecticut/superior_court_opinions/documents"
                    "/03dd9ec415bf5bf4.xml",
                    # noqa
                ),
                "answer": "connsuperct",
            },
            {
                "args": (
                    "Superior Court for Law and Equity",
                    "tennessee/court_opinions/documents/01236c757d1128fd.xml",
                ),
                "answer": "tennsuperct",
            },
            {
                "args": (
                    "Courts of General Sessions and Oyer and Terminer "
                    "of Delaware",
                    "delaware/court_opinions/documents/108da18f9278da90.xml",
                ),
                "answer": "delsuperct",
            },
            {
                "args": (
                    "Circuit Court of the United States of Delaware",
                    "delaware/court_opinions/documents/108da18f9278da90.xml",
                ),
                "answer": "circtdel",
            },
            {
                "args": (
                    "Circuit Court of Delaware",
                    "delaware/court_opinions/documents/108da18f9278da90.xml",
                ),
                "answer": "circtdel",
            },
            {
                "args": (
                    "Court of Quarter Sessions "
                    "Court of Delaware,  Kent County.",
                    "delaware/court_opinions/documents/f01f1724cc350bb9.xml",
                ),
                "answer": "delsuperct",
            },
            {
                "args": (
                    "District Court of Appeal.",
                    "florida/court_opinions/documents/25ce1e2a128df7ff.xml",
                ),
                "answer": "fladistctapp",
            },
            {
                "args": (
                    "District Court of Appeal, Lakeland, Florida.",
                    "florida/court_opinions/documents/25ce1e2a128df7ff.xml",
                ),
                "answer": "fladistctapp",
            },
            {
                "args": (
                    "District Court of Appeal Florida.",
                    "florida/court_opinions/documents/25ce1e2a128df7ff.xml",
                ),
                "answer": "fladistctapp",
            },
            {
                "args": (
                    "District Court of Appeal, Florida.",
                    "florida/court_opinions/documents/25ce1e2a128df7ff.xml",
                ),
                "answer": "fladistctapp",
            },
            {
                "args": (
                    "District Court of Appeal of Florida, Second District.",
                    "florida/court_opinions/documents/25ce1e2a128df7ff.xml",
                ),
                "answer": "fladistctapp",
            },
            {
                "args": (
                    "District Court of Appeal of Florida, Second District.",
                    "/data/dumps/florida/court_opinions/documents"
                    "/25ce1e2a128df7ff.xml",
                    # noqa
                ),
                "answer": "fladistctapp",
            },
            {
                "args": (
                    "U.S. Circuit Court",
                    "north_carolina/court_opinions/documents"
                    "/fa5b96d590ae8d48.xml",
                    # noqa
                ),
                "answer": "circtnc",
            },
            {
                "args": (
                    "United States Circuit Court,  Delaware District.",
                    "delaware/court_opinions/documents/6abba852db7c12a1.xml",
                ),
                "answer": "circtdel",
            },
            {
                "args": ("Court of Common Pleas  Hartford County", "asdf"),
                "answer": "connsuperct",
            },
        )
        for d in pairs:
            got = get_state_court_object(*d["args"])
            self.assertEqual(
                got,
                d["answer"],
                msg="\nDid not get court we expected: '%s'.\n"
                "               Instead we got: '%s'" % (d["answer"], got),
            )

    def test_get_fed_court_object_from_string(self) -> None:
        """Can we get the correct federal courts?"""

        pairs = (
            {"q": "Eastern District of New York", "a": "nyed"},
            {"q": "Northern District of New York", "a": "nynd"},
            {"q": "Southern District of New York", "a": "nysd"},
            # When we have unknown first word, we assume it's errant.
            {"q": "Nathan District of New York", "a": "nyd"},
            {"q": "Nate District of New York", "a": "nyd"},
            {"q": "Middle District of Pennsylvania", "a": "pamd"},
            {"q": "Middle Dist. of Pennsylvania", "a": "pamd"},
            {"q": "M.D. of Pennsylvania", "a": "pamd"},
        )
        for test in pairs:
            print(f"Testing: {test['q']}, expecting: {test['a']}")
            got = match_court_string(test["q"], federal_district=True)
            self.assertEqual(test["a"], got)

    def test_get_appellate_court_object_from_string(self) -> None:
        """Can we get the correct federal appellate courts?"""

        pairs = (
            {"q": "U. S. Court of Appeals for the Ninth Circuit", "a": "ca9"},
            {
                # FJC data does not appear to have a space between U. and S.
                "q": "U.S. Court of Appeals for the Ninth Circuit",
                "a": "ca9",
            },
            {"q": "U. S. Circuit Court for the Ninth Circuit", "a": "ca9"},
            {"q": "U.S. Circuit Court for the Ninth Circuit", "a": "ca9"},
        )
        for test in pairs:
            print(f"Testing: {test['q']}, expecting: {test['a']}")
            got = match_court_string(test["q"], federal_appeals=True)
            self.assertEqual(test["a"], got)


@pytest.mark.django_db
class PacerDocketParserTest(TestCase):
    """Can we parse RECAP dockets successfully?"""

    NUM_PARTIES = 3
    NUM_PETRO_ATTYS = 6
    NUM_FLOYD_ROLES = 3
    NUM_DOCKET_ENTRIES = 3

    @classmethod
    def setUpTestData(cls) -> None:
        cls.fp = (
            MEDIA_ROOT / "test" / "xml" / "gov.uscourts.akd.41664.docket.xml"
        )
        docket_number = "3:11-cv-00064"
        cls.court = CourtFactory.create()
        cls.docket = DocketFactory.create(
            source=Docket.RECAP,
            pacer_case_id="41664",
            docket_number=docket_number,
            court=cls.court,
            filepath_local__from_path=str(cls.fp),
        )

    def setUp(self) -> None:
        process_docket_data(self.docket, UPLOAD_TYPE.IA_XML_FILE, self.fp)

    def tearDown(self) -> None:
        Docket.objects.all().delete()
        Party.objects.all().delete()
        Attorney.objects.all().delete()
        AttorneyOrganization.objects.all().delete()

    def test_docket_entry_parsing(self) -> None:
        """Do we get the docket entries we expected?"""
        # Total count is good?
        all_rds = RECAPDocument.objects.all()
        self.assertEqual(self.NUM_DOCKET_ENTRIES, all_rds.count())

        # Main docs exist and look about right?
        rd = RECAPDocument.objects.get(pacer_doc_id="0230856334")
        desc = rd.docket_entry.description
        good_de_desc = all(
            [
                desc.startswith("COMPLAINT"),
                "Filing fee" in desc,
                desc.endswith("2011)"),
            ]
        )
        self.assertTrue(good_de_desc)

        # Attachments have good data?
        att_rd = RECAPDocument.objects.get(pacer_doc_id="02301132632")
        self.assertTrue(
            all(
                [
                    att_rd.description.startswith("Judgment"),
                    "redistributed" in att_rd.description,
                    att_rd.description.endswith("added"),
                ]
            ),
            f"Description didn't match. Got: {att_rd.description}",
        )
        self.assertEqual(att_rd.attachment_number, 1)
        self.assertEqual(att_rd.document_number, "116")
        self.assertEqual(att_rd.docket_entry.date_filed, date(2012, 12, 10))

        # Two documents under the docket entry?
        self.assertEqual(att_rd.docket_entry.recap_documents.all().count(), 2)

    def test_party_parsing(self) -> None:
        """Can we parse an XML docket and get good results in the DB"""
        self.assertEqual(self.docket.parties.all().count(), self.NUM_PARTIES)

        petro = self.docket.parties.get(name__contains="Petro")
        self.assertEqual(petro.party_types.all()[0].name, "Plaintiff")

        attorneys = petro.attorneys.all().distinct()
        self.assertEqual(attorneys.count(), self.NUM_PETRO_ATTYS)

        floyd = petro.attorneys.distinct().get(name__contains="Floyd")
        self.assertEqual(floyd.roles.all().count(), self.NUM_FLOYD_ROLES)
        self.assertEqual(floyd.name, "Floyd G. Short")
        self.assertEqual(floyd.email, "fshort@susmangodfrey.com")
        self.assertEqual(floyd.fax, "(206) 516-3883")
        self.assertEqual(floyd.phone, "(206) 373-7381")

        godfrey_llp = floyd.organizations.all()[0]
        self.assertEqual(godfrey_llp.name, "Susman Godfrey, LLP")
        self.assertEqual(godfrey_llp.address1, "1201 Third Ave.")
        self.assertEqual(godfrey_llp.address2, "Suite 3800")
        self.assertEqual(godfrey_llp.city, "Seattle")
        self.assertEqual(godfrey_llp.state, "WA")


class GetQuarterTest(SimpleTestCase):
    """Can we properly figure out when the quarter that we're currently in
    began?
    """

    def test_january(self) -> None:
        self.assertEqual(
            date(2018, 1, 1), get_start_of_quarter(date(2018, 1, 1))
        )
        self.assertEqual(
            date(2018, 1, 1), get_start_of_quarter(date(2018, 1, 10))
        )

    def test_december(self) -> None:
        self.assertEqual(
            date(2018, 10, 1), get_start_of_quarter(date(2018, 12, 1))
        )


@pytest.mark.django_db
class IAUploaderTest(TestCase):
    """Tests related to uploading docket content to the Internet Archive"""

    fixtures = [
        "test_objects_query_counts.json",
        "attorney_party_dup_roles.json",
    ]

    def test_correct_json_generated(self) -> None:
        """Do we generate the correct JSON for a handful of tricky dockets?

        The most important thing here is that we don't screw up how we handle
        m2m relationships, which have a tendency of being tricky.
        """
        d, j_str = generate_ia_json(1)
        j = json.loads(j_str)
        parties = j["parties"]
        first_party = parties[0]
        first_party_attorneys = first_party["attorneys"]
        expected_num_attorneys = 1
        actual_num_attorneys = len(first_party_attorneys)
        self.assertEqual(
            expected_num_attorneys,
            actual_num_attorneys,
            msg="Got wrong number of attorneys when making IA JSON. "
            "Got %s, expected %s: \n%s"
            % (
                actual_num_attorneys,
                expected_num_attorneys,
                first_party_attorneys,
            ),
        )

        first_attorney = first_party_attorneys[0]
        attorney_roles = first_attorney["roles"]
        expected_num_roles = 1
        actual_num_roles = len(attorney_roles)
        self.assertEqual(
            actual_num_roles,
            expected_num_roles,
            msg="Got wrong number of roles on attorneys when making IA JSON. "
            "Got %s, expected %s" % (actual_num_roles, expected_num_roles),
        )

    def test_num_queries_ok(self) -> None:
        """Have we regressed the number of queries it takes to make the JSON

        It's very easy to use the DRF in a way that generates a LOT of queries.
        Let's avoid that.
        """
        with self.assertNumQueries(11):
            generate_ia_json(1)

        with self.assertNumQueries(9):
            generate_ia_json(2)

        with self.assertNumQueries(5):
            generate_ia_json(3)


class HarvardTests(TestCase):
    def setUp(self):
        """Setup harvard tests

        This setup is a little distinct from normal ones.  Here we are actually
        setting up our patches which are used by the majority of the tests.
        Each one can be used or turned off.  See the teardown for more.
        :return:
        """
        self.make_filepath_patch = patch(
            "cl.corpus_importer.management.commands.harvard_opinions.filepath_list"
        )
        self.filepath_list_func = self.make_filepath_patch.start()
        self.read_json_patch = patch(
            "cl.corpus_importer.management.commands.harvard_opinions.read_json"
        )
        self.read_json_func = self.read_json_patch.start()
        self.find_court_patch = patch(
            "cl.corpus_importer.management.commands.harvard_opinions.find_court"
        )
        self.find_court_func = self.find_court_patch.start()

        # Default values for Harvard Tests
        self.filepath_list_func.return_value = ["/one/fake/filepath.json"]
        self.find_court_func.return_value = ["harvard"]

    @classmethod
    def setUpTestData(cls) -> None:
        for court in ["harvard", "alnb"]:
            CourtFactory.create(id=court)

    def tearDown(self) -> None:
        """Tear down patches and remove added objects"""
        self.make_filepath_patch.stop()
        self.read_json_patch.stop()
        self.find_court_patch.stop()
        Docket.objects.all().delete()
        Court.objects.all().delete()

    def _get_cite(self, case_law) -> Citation:
        """Fetch first citation added to case

        :param case_law: Case object
        :return: First citation found
        """
        cites = eyecite.get_citations(case_law["citations"][0]["cite"])
        cite = Citation.objects.get(
            volume=cites[0].groups["volume"],
            reporter=cites[0].groups["reporter"],
            page=cites[0].groups["page"],
        )
        return cite

    def assertSuccessfulParse(self, expected_count_diff, bankruptcy=False):
        pre_install_count = OpinionCluster.objects.all().count()
        parse_harvard_opinions(
            {
                "reporter": None,
                "volumes": None,
                "page": None,
                "make_searchable": False,
                "court_id": None,
                "location": None,
                "bankruptcy": bankruptcy,
            }
        )
        post_install_count = OpinionCluster.objects.all().count()
        self.assertEqual(
            expected_count_diff, post_install_count - pre_install_count
        )
        print(post_install_count - pre_install_count, "✓")

    def test_partial_dates(self) -> None:
        """Can we validate partial dates?"""
        pairs = (
            {"q": "2019-01-01", "a": "2019-01-01"},
            {"q": "2019-01", "a": "2019-01-15"},
            {"q": "2019-05", "a": "2019-05-15"},
            {"q": "1870-05", "a": "1870-05-15"},
            {"q": "2019", "a": "2019-07-01"},
        )
        for test in pairs:
            print(f"Testing: {test['q']}, expecting: {test['a']}")
            got = validate_dt(test["q"])
            dt_obj = datetime.strptime(test["a"], "%Y-%m-%d").date()
            self.assertEqual(dt_obj, got[0])

    def test_short_opinion_matching(self) -> None:
        """Can we match opinions successfully when very small?"""
        aspby_case_body = '<casebody firstpage="1007" lastpage="1007" \
xmlns="http://nrs.harvard.edu/urn-3:HLS.Libr.US_Case_Law.Schema.Case_Body:v1">\n\
<parties id="b985-7">State, Respondent, v. Aspby, Petitioner,</parties>\n \
<docketnumber id="Apx">No. 73722-3.</docketnumber>\n  <opinion type="majority">\n \
<p id="AJ6">Petition for review of a decision of the Court of Appeals,\
 No. 48369-2-1, September 19, 2002. <em>Denied </em>September 30, 2003.\
</p>\n  </opinion>\n</casebody>\n'

        matching_cl_case = "Petition for review of a decision of the Court of \
Appeals, No. 48369-2-1, September 19, 2002. Denied September 30, 2003."
        nonmatch_cl_case = "Petition for review of a decision of the Court of \
Appeals, No. 19667-4-III, October 31, 2002. Denied September 30, 2003."

        harvard_characters = clean_body_content(aspby_case_body)
        good_characters = clean_body_content(matching_cl_case)
        bad_characters = clean_body_content(nonmatch_cl_case)

        good_match = compare_documents(harvard_characters, good_characters)
        self.assertEqual(good_match, 100)

        bad_match = compare_documents(harvard_characters, bad_characters)
        self.assertEqual(bad_match, 81)

    def test_new_case(self):
        """Can we import a new case?"""
        case_law = CaseLawFactory()
        self.read_json_func.return_value = case_law
        self.assertSuccessfulParse(1)

        cite = self._get_cite(case_law)
        ops = cite.cluster.sub_opinions.all()
        expected_opinion_count = 1
        self.assertEqual(ops.count(), expected_opinion_count)

        op = ops[0]
        expected_op_type = Opinion.LEAD
        self.assertEqual(op.type, expected_op_type)

        expected_author_str = "Cowin"
        self.assertEqual(op.author_str, expected_author_str)

        # Test some cluster attributes
        cluster = cite.cluster

        self.assertEqual(cluster.judges, expected_author_str)
        self.assertEqual(
            cluster.date_filed,
            datetime.strptime(case_law["decision_date"], "%Y-%m-%d").date(),
        )
        self.assertEqual(cluster.case_name_full, case_law["name"])

        expected_other_dates = "March 3, 2009."
        self.assertEqual(cluster.other_dates, expected_other_dates)

        # Test some docket attributes
        docket = cite.cluster.docket
        self.assertEqual(docket.docket_number, case_law["docket_number"])

    def test_new_bankruptcy_case(self):
        """Can we add a bankruptcy court?"""

        # Disable court_func patch to test ability to identify bank. ct.
        self.find_court_patch.stop()

        self.read_json_func.return_value = CaseLawFactory(
            court=CaseLawCourtFactory.create(
                name="United States Bankruptcy Court for the Northern "
                "District of Alabama "
            )
        )
        self.assertSuccessfulParse(0)
        self.assertSuccessfulParse(1, bankruptcy=True)

    def test_syllabus_and_summary_wrapping(self):
        """Did we properly parse syllabus and summary?"""
        data = '<casebody>  <summary id="b283-8"><em>Error from Bourbon \
Bounty.</em></summary>\
<syllabus id="b283-9">Confessions of judgment, provided for in title 11,\
 chap. 3, civil code, must be made in open court; a judgment entered on a \
confession taken by the clerk in vacation, is a nullity. <em>Semble, </em>the \
clerk, in vacation, is only authorized by § 389 to enter in vacation a judgment \
rendered by the court.</syllabus> <opinion type="majority"><p id="AvW"> \
delivered the opinion of the Court.</p></opinion> </casebody>'

        self.read_json_func.return_value = CaseLawFactory.create(
            casebody=CaseBodyFactory.create(data=data),
        )
        self.assertSuccessfulParse(1)
        cite = self._get_cite(self.read_json_func.return_value)
        self.assertEqual(cite.cluster.syllabus.count("<p>"), 1)
        self.assertEqual(cite.cluster.summary.count("<p>"), 1)

    def test_attorney_extraction(self):
        """Did we properly parse attorneys?"""
        data = '<casebody> <attorneys id="b284-5"><em>M. V. Voss, \
</em>for plaintiff in error.</attorneys> <attorneys id="b284-6">\
<em>W. O. Webb, </em>for defendant in error.</attorneys> \
<attorneys id="b284-7"><em>Voss, </em>for plaintiff in error,\
</attorneys> <attorneys id="b289-5"><em>Webb, </em>\
<page-number citation-index="1" label="294">*294</page-number>for \
defendant in error,</attorneys> <opinion type="majority"><p id="AvW"> \
delivered the opinion of the Court.</p></opinion> </casebody>'
        case_law = CaseLawFactory.create(
            casebody=CaseBodyFactory.create(data=data)
        )
        self.read_json_func.return_value = case_law

        self.assertSuccessfulParse(1)
        cite = self._get_cite(case_law)
        self.assertEqual(
            cite.cluster.attorneys,
            "M. V. Voss, for plaintiff in error., W. O. Webb, for defendant "
            "in error., Voss, for plaintiff in error,, Webb, for defendant "
            "in error,",
        )

    def test_per_curiam(self):
        """Did we identify the per curiam case."""
        case_law = CaseLawFactory.create(
            casebody=CaseBodyFactory.create(
                data='<casebody><opinion type="majority"><author '
                'id="b56-3">PER CURIAM:</author></casebody> '
            ),
        )
        self.read_json_func.return_value = case_law
        self.assertSuccessfulParse(1)
        cite = self._get_cite(case_law)

        ops = cite.cluster.sub_opinions.all()
        self.assertEqual(ops[0].author_str, "Per Curiam")
        self.assertTrue(ops[0].per_curiam)

    def test_authors(self):
        """Did we find the authors and the list of judges."""
        casebody = """<casebody>
  <judges id="b246-5">Thomas, J., delivered the opinion of the \
  Court, in which Roberts, C. J., and Scaua, <page-number citation-index="1" \
  label="194">Kennedy, Sotjter, Ginsbtjrg, and Auto, JJ., joined. Stevens, J., \
   filed a dissenting opinion, in which Breyer, J., joined, \
   <em>post, </em>p. 202.</judges>
  <opinion type="majority">
    <author id="b247-5">Justice Thomas</author>
    <p id="AvW">delivered the opinion of the Court.</p>
  </opinion>
  <opinion type="dissent">
    <author id="b254-6">Justice Stevens,</author>
    <p id="Ab5">with whom Justice Breyer joins, dissenting.</p>
  </opinion>
</casebody>
        """
        case_law = CaseLawFactory(
            casebody=CaseBodyFactory.create(data=casebody),
        )
        self.read_json_func.return_value = case_law
        self.assertSuccessfulParse(1)

        cite = self._get_cite(case_law)
        ops = cite.cluster.sub_opinions.all().order_by("author_str")

        self.assertEqual(ops[0].author_str, "Stevens")
        self.assertEqual(ops[1].author_str, "Thomas")

        self.assertEqual(
            cite.cluster.judges,
            "Auto, Breyer, Ginsbtjrg, Kennedy, Roberts, Scaua, Sotjter, "
            "Stevens, Thomas",
        )

    def test_xml_harvard_extraction(self):
        """Did we successfully not remove page citations while
        processing other elements?"""
        data = """
<casebody firstpage="1" lastpage="2">
<opinion type="majority">Everybody <page-number citation-index="1" \
label="194">*194</page-number>
 and next page <page-number citation-index="1" label="195">*195
 </page-number>wins.
 </opinion>
 </casebody>
"""
        case_law = CaseLawFactory.create(
            casebody=CaseBodyFactory.create(data=data),
        )
        self.read_json_func.return_value = case_law
        self.assertSuccessfulParse(1)
        cite = self._get_cite(case_law)

        opinions = cite.cluster.sub_opinions.all().order_by("-pk")
        self.assertEqual(opinions[0].xml_harvard.count("</page-number>"), 2)

    def test_same_citation_different_case(self):
        """Same case name, different opinion - based on a BTA bug"""
        case_law = CaseLawFactory()
        self.read_json_func.return_value = case_law
        self.assertSuccessfulParse(1)

        case_law["casebody"] = CaseBodyFactory.create(
            data='<casebody firstpage="1" lastpage="2">\n  \
            <opinion type="minority">Something else.</opinion>\n</casebody>'
        )
        self.read_json_func.return_value = case_law
        self.filepath_list_func.return_value = ["/another/fake/filepath.json"]
        self.assertSuccessfulParse(1)

    def test_bad_ibid_citation(self):
        """Can we add a case with a bad ibid citation?"""
        citations = [
            "7 Ct. Cl. 65",
            "1 Ct. Cls. R., p. 270, 3 id., p. 10; 7 W. R., p. 666",
        ]
        case_law = CaseLawFactory(
            citations=[CitationFactory(cite=cite) for cite in citations],
        )
        self.read_json_func.return_value = case_law
        self.assertSuccessfulParse(1)
        cite = self._get_cite(case_law)
        self.assertEqual(str(cite), "7 Ct. Cl. 65")

    def test_no_volume_citation(self):
        """Can we handle an opinion that contains a citation without a
        volume?"""
        citations = [
            "Miller's Notebook, 179",
        ]
        case_law = CaseLawFactory(
            citations=[CitationFactory(cite=cite) for cite in citations],
        )
        self.read_json_func.return_value = case_law
        self.assertSuccessfulParse(1)

    def test_case_name_winnowing_comparison(self):
        """
        Test removing "United States" from case names and check if there is an
        overlap between two case names.
        """
        case_name_full = (
            "UNITED STATES of America, Plaintiff-Appellee, "
            "v. Wayne VINSON, Defendant-Appellant "
        )
        case_name_abbreviation = "United States v. Vinson"
        harvard_case = f"{case_name_full} {case_name_abbreviation}"

        case_name_cl = "United States v. Frank Esquivel"
        overlap = winnow_case_name(case_name_cl) & winnow_case_name(
            harvard_case
        )
        self.assertEqual(len(overlap), 0)

    def test_case_names_with_abbreviations(self):
        """
        Test what happens when the case name contains abbreviations
        """

        # Check against itself, there must be an overlap
        case_1_data = {
            "case_name_full": "In the matter of S.J.S., a minor child. "
            "D.L.M. and D.E.M., Petitioners/Respondents v."
            " T.J.S.",
            "case_name_abbreviation": "D.L.M. v. T.J.S.",
            "case_name_cl": "D.L.M. v. T.J.S.",
            "overlaps": 2,
        }

        case_2_data = {
            "case_name_full": "Appeal of HAMILTON & CHAMBERS CO., INC.",
            "case_name_abbreviation": "Appeal of Hamilton & Chambers Co.",
            "case_name_cl": "Appeal of Hamilton & Chambers Co.",
            "overlaps": 4,
        }

        # Check against different case name, there shouldn't be an overlap
        case_3_data = {
            "case_name_full": "Henry B. Wesselman et al., as Executors of "
            "Blanche Wesselman, Deceased, Respondents, "
            "v. The Engel Company, Inc., et al., "
            "Appellants, et al., Defendants",
            "case_name_abbreviation": "Wesselman v. Engel Co.",
            "case_name_cl": " McQuillan v. Schechter",
            "overlaps": 0,
        }

        cases = [case_1_data, case_2_data, case_3_data]

        for case in cases:
            harvard_case = f"{case.get('case_name_full')} {case.get('case_name_abbreviation')}"
            overlap = winnow_case_name(
                case.get("case_name_cl")
            ) & winnow_case_name(harvard_case)

            self.assertEqual(len(overlap), case.get("overlaps"))


class CorpusImporterManagementCommmandsTests(TestCase):
    @classmethod
    def setUpTestData(cls) -> None:
        cls.court = CourtFactory(id="nyappdiv")

        # Create object person
        cls.judge = PersonWithChildrenFactory.create(
            name_first="Paul",
            name_middle="J.",
            name_last="Yesawich",
            name_suffix="jr",
            date_dob="1923-11-27",
            date_granularity_dob="%Y-%m-%d",
        )
        position = PositionFactory.create(court=cls.court, person=cls.judge)
        cls.judge.positions.add(position)

        cls.judge_2 = PersonWithChildrenFactory.create(
            name_first="Harold",
            name_middle="Fleming",
            name_last="Snead",
            name_suffix="jr",
            date_dob="1903-06-16",
            date_granularity_dob="%Y-%m-%d",
            date_dod="1987-12-23",
            date_granularity_dod="%Y-%m-%d",
        )
        position_2 = PositionFactory.create(
            court=cls.court, person=cls.judge_2
        )
        cls.judge_2.positions.add(position_2)

    def test_normalize_author_str(self):
        """Normalize author_str field in opinions in Person object"""

        # Create opinion cluster with opinion and docket
        cluster = (
            OpinionClusterFactoryWithChildrenAndParents(
                docket=DocketFactory(
                    court=self.court,
                    case_name="Foo v. Bar",
                    case_name_full="Foo v. Bar",
                ),
                case_name="Foo v. Bar",
                date_filed=date.today(),
                sub_opinions=RelatedFactory(
                    OpinionWithChildrenFactory,
                    factory_related_name="cluster",
                    plain_text="Sample text",
                    author_str="Yesawich",
                    author=None,
                ),
            ),
        )

        # Check that the opinion doesn't have an author
        self.assertEqual(cluster[0].sub_opinions.all().first().author, None)

        # Run function to normalize authors in opinions
        normalize_authors_in_opinions()

        # Reload field values from the database.
        cluster[0].refresh_from_db()

        #  Check that the opinion now have an author
        self.assertEqual(
            cluster[0].sub_opinions.all().first().author, self.judge
        )

    def test_normalize_panel_str(self):
        """Normalize judges string field into panel field(m2m)"""

        cluster = OpinionClusterWithParentsFactory(
            docket=DocketFactory(
                court=self.court,
                case_name="Lorem v. Ipsum",
                case_name_full="Lorem v. Ipsum",
            ),
            case_name="Lorem v. Ipsum",
            date_filed=date.today(),
            judges="Snead, Yesawich",
        )

        # Check panel is empty
        self.assertEqual(len(cluster.panel.all()), 0)

        # Run function to normalize panel in opinion clusters
        normalize_panel_in_opinioncluster()

        # Reload field values from the database
        cluster.refresh_from_db()

        # Check that the opinion cluster now have judges in panel
        self.assertEqual(len(cluster.panel.all()), 2)


class HarvardMergerTests(TestCase):
    def setUp(self):
        """Setup harvard tests

        This setup is a little distinct from normal ones.  Here we are actually
        setting up our patches which are used by the majority of the tests.
        Each one can be used or turned off.  See the teardown for more.
        :return:
        """
        self.read_json_patch = patch(
            "cl.corpus_importer.management.commands.harvard_merge.read_json"
        )
        self.read_json_func = self.read_json_patch.start()

    def tearDown(self) -> None:
        """Tear down patches and remove added objects"""
        Docket.objects.all().delete()
        self.read_json_patch.stop()

    def test_merger(self):
        """Can we identify opinions correctly even when they are slightly different."""

        case_data = {
            "name": "CANNON v. THE STATE",
            "name_abbreviation": "Cannon v. State",
            "decision_date": "1944-11-18",
            "docket_number": "30614",
            "casebody": {
                "status": "ok",
                "data": '<casebody firstpage="757" lastpage="758" xmlns="http://nrs.harvard.edu/urn-3:HLS.Libr.US_Case_Law.Schema.Case_Body:v1">\n  <docketnumber id="b795-7">30614.</docketnumber>\n  <parties id="AAY">CANNON <em>v. </em>THE STATE.</parties>\n  <decisiondate id="b795-9">Decided November 18, 1944.</decisiondate>\n  <attorneys id="b796-4"><page-number citation-index="1" label="758">*758</page-number><em>B. B. Giles, </em>for plaintiff in error.</attorneys>\n  <attorneys id="b796-5"><em>Lindley W. Gamp, solicitor, John A. Boyhin, solicitor-general,. Durwood T. Bye, </em>contra.</attorneys>\n  <opinion type="majority">\n    <author id="b796-6">Broyles, C. J.</author>\n    <p id="Auq">(After stating the foregoing facts.) After the-disposal of counts 2 and 3, the only charge before the court and jury was that the defendant had sold distilled spirits and alcohol as a retail dealer, without first obtaining a license from the State Revenue Commissioner. The evidence adduced to show the guilt, of the accused on count 1 was wholly circumstantial, and was insufficient to exclude every reasonable hypothesis except that of his-guilt, and it failed to show beyond a reasonable doubt that he had sold distilled spirits or alcohol. The cases of <em>Thomas </em>v. <em>State, </em>65 <em>Ga. App. </em>749 (16 S. E. 2d, 447), and <em>Martin </em>v. <em>State, </em>68 <em>Ga. App. </em>169 (22 S. E. 2d, 193), cited in behalf of the defendant in error, are distinguished by their facts from this case. The verdict was-contrary to law and the evidence; and the overruling of the certiorari was error. <em>Judgment reversed.</em></p>\n    <judges id="Ae85">\n      <em>MacIntyre, J., concurs.</em>\n    </judges>\n  </opinion>\n  <opinion type="concurrence">\n    <author id="b796-7">Gardner, J.,</author>\n    <p id="AK2">concurring specially: Under the record the judgment should be reversed for another reason. Since the jury, based on the same evidence, found the defendant not guilty on count 2 for possessing liquors, and a verdict finding him guilty on count 1 for selling intoxicating liquors, the verdicts are repugnant and void as being inconsistent verdicts by the same jury based on the same \'evidence. <em>Britt </em>v. <em>State, </em>36 <em>Ga. App. </em>668 (137 S. E. 791), and cit.; <em>Kuck </em>v. <em>State, </em>149 <em>Ga. </em>191 (99 S. E. 622). I concur in the reversal for this additional reason.</p>\n  </opinion>\n</casebody>\n',
            },
        }
        self.read_json_func.return_value = case_data

        lead = """<p>The overruling of the certiorari was error.</p>
            <p><center>                       DECIDED NOVEMBER 18, 1944.</center>
            John Cannon was tried in the criminal court of Fulton County on an accusation containing three counts. Count I charged that in said county on July 24, 1943, he "did engage in and sell, as a retail dealer, distilled spirits and alcohol, without first obtaining a license from the State Revenue Commissioner of the State of Georgia." Count 2 charged that on July 24, 1943, he possessed forty-eight half pints and three pints of whisky in Fulton County, and had not been licensed by the State Revenue Commissioner to sell whisky as a retail or wholesale dealer. Count 3 charged that on September 24, 1943, in said county, he sold malt beverages as a retail dealer, without first securing a license from the State Revenue Commissioner. On the trial, after the close of the State's evidence, counsel for the accused made a motion that count 2 be stricken, and that a verdict for the defendant be directed on counts 1 and 3. The court sustained the motion as to counts 2 and 3, but overruled it as to count 1. The jury returned a verdict of guilty on count 1, and of not guilty on counts 2 and 3. Subsequently the defendant's certiorari was overruled by a judge of the superior court and that judgment is assigned as error. <span class="star-pagination">*Page 758</span>
            After the disposal of counts 2 and 3, the only charge before the court and jury was that the defendant had sold distilled spirits and alcohol as a retail dealer, without first obtaining a license from the State Revenue Commissioner. The evidence adduced to show the guilt of the accused on count 1 was wholly circumstantial, and was insufficient to exclude every reasonable hypothesis except that of his guilt, and it failed to show beyond a reasonable doubt that he had sold distilled spirits or alcohol. The cases of <em>Thomas</em> v. <em>State,</em> <cross_reference><span class="citation no-link">65 Ga. App. 749</span></cross_reference> (<cross_reference><span class="citation" data-id="3407553"><a href="/opinion/3412403/thomas-v-state/">16 S.E.2d 447</a></span></cross_reference>), and <em>Martin</em> v. <em>State,</em> <cross_reference><span class="citation no-link">68 Ga. App. 169</span></cross_reference> (<cross_reference><span class="citation" data-id="3405716"><a href="/opinion/3410794/martin-v-state/">22 S.E.2d 193</a></span></cross_reference>), cited in behalf of the defendant in error, are distinguished by their facts from this case. The verdict was contrary to law and the evidence; and the overruling of the certiorari was error.</p>
            <p><em>Judgment reversed. MacIntyre, J., concurs.</em></p>"""
        concurrence = """<p>Under the record the judgment should be reversed for another reason. Since the jury, based on the same evidence, found the defendant not guilty on count 2 for possessing liquors, and a verdict finding him guilty on count 1 for selling intoxicating liquors, the verdicts are repugnant and void as being inconsistent verdicts by the same jury based on the same evidence. <em>Britt</em> v. <em>State,</em> <cross_reference><span class="citation no-link">36 Ga. App. 668</span></cross_reference>
            (<cross_reference><span class="citation no-link">137 S.E. 791</span></cross_reference>), and cit.; <em>Kuck</em> v. <em>State,</em> <cross_reference><span class="citation" data-id="5582722"><a href="/opinion/5732248/kuck-v-state/">149 Ga. 191</a></span></cross_reference>
            (<cross_reference><span class="citation no-link">99 S.E. 622</span></cross_reference>). I concur in the reversal for this additional reason.</p>"""

        cluster = OpinionClusterFactoryMultipleOpinions(
            docket=DocketFactory(),
            sub_opinions__data=[
                {"type": "020lead", "html_with_citations": lead},
                {"type": "030concurrence", "html_with_citations": concurrence},
            ],
        )

        self.assertEqual(
            OpinionCluster.objects.get(id=cluster.id).attorneys, "", msg="WHAT"
        )

        self.assertEqual(Opinion.objects.all().count(), 2)
        merge_opinion_clusters(cluster_id=cluster.id)
        self.assertEqual(Opinion.objects.all().count(), 2)

    def test_non_overlapping(self):
        """Can we find fields that need merging"""

        case_data = {
            "casebody": {
                "status": "ok",
                "data": '<casebody> <attorneys><page-number citation-index="1" label="758">*758</page-number><em>B. B. Giles, </em>for plaintiff in error.</attorneys>\n  <attorneys id="b796-5"><em>Lindley W. Gamp, solicitor, John A. Boyhin, solicitor-general,. Durwood T. Bye, </em>contra.</attorneys>\n  <opinion type="majority"> a simple opinion</opinion>\n</casebody>\n',
            },
        }
        self.read_json_func.return_value = case_data

        cluster = OpinionClusterFactoryMultipleOpinions(
            docket=DocketFactory(),
            attorneys="B. B. Giles, Lindley W. Gamp, and John A. Boyhin",  # cl value
        )
        clean_dictionary = combine_non_overlapping_data(cluster.id, case_data)
        self.assertEqual(
            clean_dictionary,
            {
                "attorneys": (
                    "B. B. Giles, for plaintiff in error., Lindley W. Gamp, solicitor, John A. Boyhin, solicitor-general,. Durwood T. Bye, contra.",
                    "B. B. Giles, Lindley W. Gamp, and John A. Boyhin",
                )
            },
            msg="Should find differences to merge",
        )

        # Test that we can ignore matching fields
        cluster = OpinionClusterFactoryMultipleOpinions(
            docket=DocketFactory(),
            attorneys="B. B. Giles, for plaintiff in error., Lindley W. Gamp, solicitor, John A. Boyhin, solicitor-general,. Durwood T. Bye, contra.",
        )
        clean_dictionary = combine_non_overlapping_data(cluster.id, case_data)
        self.assertEqual(clean_dictionary, {}, msg="Attorneys are the same")

    def test_merge_overlap_judges(self):
        """Test merge judge names when overlap exist"""

        # Test 1: Example from CL #4575556
        cluster = OpinionClusterWithParentsFactory(
            judges="Barbera",
        )
        harvard = {
            "casebody": {
                "data": "<casebody><opinion> <author>Argued before Barbera, "
                "C.J., Greene,<footnotemark>*</footnotemark> Adkins, "
                "McDonald, Watts, Hotten, Getty JJ.</author><p> "
                "Some opinion</p> </opinion>\n</casebody>"
            },
        }
        clean_dictionary_1 = combine_non_overlapping_data(cluster.pk, harvard)
        self.assertEqual(
            clean_dictionary_1,
            {
                "judges": (
                    "Adkins, Barbera, Getty, Greene, Hotten, McDonald, Watts",
                    "Barbera",
                )
            },
            msg="Missing data",
        )

        # Can we merge judges appropriately
        self.assertEqual(cluster.judges, "Barbera")
        merge_judges(cluster.pk, "judges", clean_dictionary_1.get("judges"))
        cluster.refresh_from_db()

        # Test best option selected for judges is in harvard data
        self.assertEqual(
            cluster.judges,
            "Adkins, Barbera, Getty, Greene, Hotten, McDonald, Watts",
        )

        # # Test 2: best option for judges is already in courtlistener
        # # From cluster id 4573873
        # cluster_2 = OpinionClusterWithParentsFactory(
        #     docket=DocketFactory(
        #         court=Court.objects.get(id="pacommwct"),
        #         case_name_short="PBPP",
        #         case_name="D. Marshall v. PBPP",
        #         case_name_full="",
        #     ),
        #     id=4573873,
        #     case_name="D. Marshall v. PBPP",
        #     case_name_short="PBPP",
        #     case_name_full="",
        #     date_filed=date(2018, 12, 17),
        #     judges="Simpson, J. ~ Concurring Opinion by Pellegrini, Senior Judge",
        # )
        #
        # case_2_data = {
        #     "name": "Dwight MARSHALL, Petitioner v. PENNSYLVANIA BOARD OF PROBATION AND PAROLE, Respondent",
        #     "name_abbreviation": "Marshall v. Pa. Bd. of Prob. & Parole",
        #     "decision_date": "2018-12-17",
        #     "casebody": {
        #         "data": '<?xml version=\'1.0\' encoding=\'utf-8\'?>\n<casebody xmlns="http://nrs.harvard.edu/urn-3:HLS.Libr.US_Case_Law.Schema.Case_Body:v1" firstpage="643" lastpage="653">\n  <parties id="p-1">Dwight MARSHALL, Petitioner<br/> v.<br/> PENNSYLVANIA BOARD OF PROBATION AND PAROLE, Respondent</parties>\n  <docketnumber id="p-2">No. 172 M.D. 2018</docketnumber>\n  <court id="p-3">Commonwealth Court of Pennsylvania.</court>\n  <decisiondate id="p-4">Submitted on Briefs August 24, 2018<br/> Decided December 17, 2018</decisiondate>\n  <attorneys id="p-5">Lonny Fish, Philadelphia, for petitioner.</attorneys>\n  <attorneys id="p-6">Timothy P. Keating, Assistant Counsel, Harrisburg, for respondent.</attorneys>\n  <p id="p-7">BEFORE: HONORABLE ROBERT SIMPSON, Judge, HONORABLE CHRISTINE FIZZANO CANNON, Judge, HONORABLE DAN PELLEGRINI, Senior Judge</p>\n  <opinion type="majority">\n    <author id="p-8">OPINION BY JUDGE SIMPSON</author>\n    <p id="p-9">Dwight Marshall (Marshall) petitions for review from an order of the Pennsylvania Board of Probation and Parole (Board) that denied his administrative appeal. He challenges the Board\'s recommitment order that extended his maximum sentence date based on an out-of-state conviction, asserting the Pennsylvania crime used to discern the recommitment range was more severe. He also argues the Board abused its discretion in denying him credit for his time spent at liberty on parole and inadequately explaining its denial. We vacate the Board\'s order, and remand to the Board to explain its credit determination sufficiently to enable appellate review.</p>\n    <p id="p-10"><strong>I. Background</strong></p>\n    <p id="p-11">In 1998, Marshall was sentenced to 11 to 22 years in prison for murder in the third degree and robbery, with a maximum date of January 15, 2019. He obtained release on parole on March 3, 2008. Almost nine years later, as a result of a traffic stop in Delaware, Marshall was charged with multiple crimes related to his possession of 200.49 grams of powder cocaine and 67.94 grams of crack cocaine. Specifically, a court in the State of Delaware, Kent County convicted Marshall for "DDEAL Tier 4 (F) Cocaine" under 16 Del. C. \u00a7 4752, and it sentenced him to eight years, custody level 5. Certified Record (C.R.) at 17 (Sentence Order, 5/10/17). Delaware then extradited Marshall to serve his sentence in Pennsylvania.</p>\n    <p id="p-12">The new conviction subjected Marshall to a parole revocation hearing. Marshall acknowledged his conviction, and signed a waiver of his right to a hearing. C.R. at 36. The parole revocation hearing report recommended "taking [his] street time" because Marshall "was on parole for Murder and was convicted of felony drug related crimes." C.R. at 35.</p>\n    <p id="p-13">As a result of his out-of-state conviction, the Board recommitted Marshall to serve 24 months as a convicted parole violator (CPV). In determining the appropriate recommitment range, the Board determined that Marshall\'s Delaware conviction most closely related to the Pennsylvania crime <a id="p647" href="#p647" data-label="647" data-citation-index="1" class="page-label">*647</a>of possession with intent to manufacture or deliver a controlled substance (cocaine) under Section 13(a)(30) of The Controlled Substance, Drug, Device and Cosmetic Act (Controlled Substance Act),<footnotemark>1</footnotemark> that carries a statutory maximum sentence of 10 years. <extracted-citation url="https://cite.case.law/citations/?q=37%20Pa.%20Code%20%C2%A7%2075.1" index="0">37 Pa. Code \u00a7 75.1</extracted-citation>. Pursuant to <extracted-citation url="https://cite.case.law/citations/?q=37%20Pa.%20Code%20%C2%A7%2075.2" index="1">37 Pa. Code \u00a7 75.2</extracted-citation>, the presumptive recommitment range for that new offense is 18 to 24 months. The Board did not award Marshall credit for his time spent at liberty on parole because of his "felony drug related crimes." C.R. at 49. Based on his conviction, the Board recalculated Marshall\'s maximum sentence date as April 29, 2028.</p>\n    <p id="p-14">Through counsel, Marshall filed an administrative appeal of the Board\'s recommitment order. The Board issued a decision, affirming and explaining the term of recommitment. Because it stated a reason for denying Marshall credit for his time spent at liberty on parole, the Board deemed moot his challenge to its exercise of discretion in its denial of credit. C.R. at 73.</p>\n    <p id="p-15">Marshall filed a timely petition for review to this Court (Petition) seeking an order vacating the Board\'s decision and remanding to the Board to modify his recommitment using the appropriate range corresponding to his criminal conduct, possession of cocaine. He seeks credit for almost nine years spent at liberty on parole, and a recalculation of his maximum sentence date to reflect that credit.</p>\n    <p id="p-16"><strong>II. Discussion</strong></p>\n    <p id="p-17">On appeal,<footnotemark>2</footnotemark> Marshall asserts the Board violated his due process rights when it did not notify him, at the time he waived his revocation hearing, that a new maximum sentence date was a possible consequence. He challenges the Board\'s authority to alter his original maximum date beyond his judicially-imposed sentence. He also argues the Board applied an incorrect recommitment range corresponding to a more severe offense. Using the most closely related crime of possession, as opposed to possession with intent to distribute, he maintains the appropriate recommitment range was 3 to 6 months, not 18 to 24 months. In addition, Marshall contends the Board abused its discretion when it denied him credit for time spent at liberty on parole, and he challenges the adequacy and accuracy of its reason for doing so.</p>\n    <p id="p-18"><strong>A. Notice</strong></p>\n    <p id="p-19">First, we address Marshall\'s argument that the Board violated his due process rights because it did not notify him, at the time he waived his revocation hearing, that a possible consequence was a new maximum sentence date. The Board responds that Marshall waived this challenge when he did not raise it during the administrative proceedings. We agree.</p>\n    <p id="p-20">The record shows Marshall did not challenge the adequacy of notice for the revocation hearing in his several-page administrative remedies form. C.R. at 53-60. Significantly, Marshall\'s counsel filed his administrative appeal. C.R. at 53. On the pre-printed administrative remedies form, counsel checked the box for "Violation of Constitutional Law (Due process, double jeopardy, etc.)." C.R. at 53. Other than checking that box, there is no suggestion of this issue for the Board\'s review. Marshall\'s failure to raise this issue before the <a id="p648" href="#p648" data-label="648" data-citation-index="1" class="page-label">*648</a>Board results in waiver. <em>Chesson v. Pa. Bd. of Prob. &amp; Parole</em>, <extracted-citation url="https://cite.case.law/a3d/47/875/" index="2" case-ids="7313355">47 A.3d 875</extracted-citation> (Pa. Cmwlth. 2012).</p>\n    <p id="p-21">In any event, Marshall cites no authority for the proposition that the Board must provide specific notice as to all possible legal consequences of a parole revocation hearing.</p>\n    <p id="p-22"><strong>B. Recalculation of Maximum Sentence Date</strong></p>\n    <p id="p-23">Next, we consider Marshall\'s contention that in recalculating his maximum sentence date, the Board imposed additional time beyond his judicially-authorized sentence.</p>\n    <p id="p-24">When a parolee violates the terms and conditions of his parole, the Board may recommit him to serve all or part of the remainder of his original sentence. <em>Yates v. Pa. Bd. of Prob. &amp; Parole</em>, <extracted-citation url="https://cite.case.law/a3d/48/496/" index="3" case-ids="7312661">48 A.3d 496</extracted-citation> (Pa. Cmwlth. 2012). The time served on recommitment is known as backtime. <em><extracted-citation url="https://cite.case.law/a3d/48/496/" index="4" case-ids="7312661">Id.</extracted-citation></em> Thus, backtime cannot exceed the time remaining on the original judicial sentence. <em><extracted-citation url="https://cite.case.law/a3d/48/496/" index="5" case-ids="7312661">Id.</extracted-citation></em> By definition, when the Board imposes backtime, it does not alter a judicially-imposed sentence; it simply requires the prisoner to serve some or all of the time remaining on the original sentence. <em><extracted-citation url="https://cite.case.law/a3d/48/496/" index="6" case-ids="7312661">Id.</extracted-citation></em> The Board is authorized to recalculate the maximum date of a sentence beyond the original date where it is not adding to the total length of the sentence. <em>Hughes v. Pa. Bd. of Prob. &amp; Parole</em>, <extracted-citation url="https://cite.case.law/a3d/179/117/" index="7" case-ids="12492562">179 A.3d 117</extracted-citation> (Pa. Cmwlth. 2018) (maximum length of sentence, not maximum date, is controlling).</p>\n    <p id="p-25">Here, at the time of his release on parole, Marshall had over 10 years remaining on his original sentence. In recalculating Marshall\'s maximum sentence date, the Board did no more than require him to serve that sentence. <em>Yates</em>. Thus, to the extent Marshall posits that the Board lacked authority to recalculate his maximum sentence date, he is incorrect.</p>\n    <p id="p-26"><strong>C. Recommitment Range</strong></p>\n    <p id="p-27">Marshall also asserts the Board applied an incorrect recommitment range of 18 to 24 months when the conduct underlying his Delaware conviction most closely related to possession of a controlled substance, a misdemeanor in Pennsylvania, with a presumptive range of 3 to 6 months. He contends the Board erred in analogizing the conduct underlying his Delaware conviction to possession with intent to distribute, which is subject to a 10-year prison term under Pennsylvania law.</p>\n    <p id="p-28">In determining the applicable presumptive recommitment ranges for out-of-state convictions, the Board compares the out-of-state offense to those listed in its regulations. When the list does not include the offense, the Board assesses the criminal conduct to discern the most closely related crime category under Pennsylvania law. <em>See</em> <extracted-citation url="https://cite.case.law/citations/?q=37%20Pa.%20Code%20%C2%A7%2075.1" index="8">37 Pa. Code \u00a7\u00a7 75.1</extracted-citation>, 75.2.</p>\n    <p id="p-29">"The presumptive ranges are intended to directly relate to the severity of the crime for which the parolee has been convicted." <extracted-citation url="https://cite.case.law/citations/?q=37%20Pa.%20Code%20%C2%A7%2075.1" index="9">37 Pa. Code \u00a7 75.1</extracted-citation>(d). Further, "[t]he severity ranking of crimes listed in \u00a7 75.2... is not intended to be exhaustive, and the most closely related crime category in terms of severity and the presumptive range will be followed if the specific crime which resulted in conviction is not contained within the listing." <extracted-citation url="https://cite.case.law/citations/?q=37%20Pa.%20Code%20%C2%A7%2075.1" index="10">37 Pa. Code \u00a7 75.1</extracted-citation>(e). For drug offenses, the presumptive range depends on the maximum term of imprisonment for the offense. <extracted-citation url="https://cite.case.law/citations/?q=37%20Pa.%20Code%20%C2%A7%2075.2" index="11">37 Pa. Code \u00a7 75.2</extracted-citation>. However, "[i]t is the severity of the criminal conduct that determines the presumptive range, not the severity of the punishment." <em>Rodriguez v. Pa. Bd. of Prob. &amp; Parole</em> (Pa. Cmwlth., No. 1997 C.D. 2015, filed March 28, 2016), slip op. at 7, <extracted-citation url="https://cite.case.law/citations/?q=2016%20WL%201221840" index="12">2016 WL 1221840</extracted-citation>, at *2 <a id="p649" href="#p649" data-label="649" data-citation-index="1" class="page-label">*649</a>(unreported) (quoting <em>Harrington v. Pa. Bd. of Prob. &amp; Parole</em>, 96 Pa.Cmwlth. 556, <extracted-citation url="https://cite.case.law/pa-commw/96/556/#p1315" index="13" case-ids="1346084">507 A.2d 1313</extracted-citation>, 1315 (1986) ).</p>\n    <p id="p-30">Here, the Delaware court adjudged Marshall guilty of "DDEAL, Tier 4 (Cocaine)," a felony in Delaware. C.R. at 17. Although the charges included various crimes under Section 4752 of the Delaware Penal Code, Marshall\'s conviction record does not specify a subsection. Premised on this omission, Marshall claims his crime amounted to mere possession of a controlled substance, not possession with intent to deliver as the Board found. We disagree.</p>\n    <p id="p-31">Section 4752 of the Delaware Penal Code is captioned "drug dealing-aggravated possession; class B felony." 16 Del. C. \u00a7 4752. It consists of five subsections, each of which includes the offense "possesses a controlled substance," in various quantity levels, with or without aggravating factors. <em><extracted-citation url="https://cite.case.law/pa-commw/96/556/#p1315" index="14" case-ids="1346084">Id.</extracted-citation></em></p>\n    <p id="p-32">Notwithstanding Marshall\'s contentions, none of these offenses is analogous to mere possession of a controlled substance under Pennsylvania law. Thus, we are unpersuaded by Marshall\'s argument that Section 13(a)(16) of the Controlled Substance Act, 35 P.S. \u00a7 780-113(a)(16), is the most closely related crime. First, that provision pertains to possession of a controlled substance "by a person not registered under this act, or a practitioner not registered or licensed by the appropriate State Board ..." without regard to the quantity of the drug. <em><extracted-citation url="https://cite.case.law/pa-commw/96/556/#p1315" index="15" case-ids="1346084">Id.</extracted-citation></em> Second, violating that provision is a misdemeanor punishable by a maximum of one year under Section 13(b) of the Controlled Substance Act, 35 P.S. \u00a7 780-113(b), for which the presumptive recommitment range is 3 to 6 months. Third, in reviewing other subsections in the same section, Section 13(30) of the Controlled Substance Act, 35 P.S. \u00a7 780-113(a)(30), is more analogous to Marshall\'s conduct in Delaware. Section 13(a)(30) of the Controlled Substance Act, 35 P.S. \u00a7 780-113(a)(30), classifies possession with intent to deliver a controlled substance as a felony punishable by a maximum term of 10 years in prison. Significantly, Marshall\'s Delaware conviction was also a felony, for which he received an eight-year sentence.</p>\n    <p id="p-33">Despite that the Sentencing Order does not specify the subsection of Section 4752 of the Delaware Penal Code, it states the conviction corresponds to a Tier 4 quantity. Notably, subsection 1 sets forth this quantity in the offense; it reads: "manufactures, delivers, or <em>possesses with intent to manufacture or deliver</em> a controlled substance in a <em>Tier 4 quantity</em>." 16 Del. C. \u00a7 4752(1) (emphasis added). Therefore, the conduct underlying Marshall\'s conviction most closely relates to the Pennsylvania crime of possession <em>with intent</em> to deliver under Section 13(a)(30) of the Controlled Substance Act, 35 P.S. \u00a7 780-113(a)(30), as the Board concluded. The Board did not err in utilizing the presumptive range corresponding to that crime.</p>\n    <p id="p-34">This Court will not interfere with the Board\'s discretion as long as the amount of backtime the Board imposed was within the applicable presumptive range. <em>Ward v. Pa. Bd. of Prob. &amp; Parole</em>, 114 Pa.Cmwlth. 255, <extracted-citation url="https://cite.case.law/pa-commw/114/255/" index="16" case-ids="1370780">538 A.2d 971</extracted-citation> (1988). Here, the recommitment period of 24 months is within the presumptive range corresponding to the closely related offense the Board identified. <extracted-citation url="https://cite.case.law/citations/?q=37%20Pa.%20Code%20%C2%A7%2075.2" index="17">37 Pa. Code \u00a7 75.2</extracted-citation>.</p>\n    <p id="p-35"><strong>D. Credit for Time at Liberty on Parole</strong></p>\n    <p id="p-36">Finally, Marshall argues the Board erred in its refusal to credit his almost nine years of time spent at liberty on parole in recalculating his maximum <a id="p650" href="#p650" data-label="650" data-citation-index="1" class="page-label">*650</a>sentence date. Although he acknowledges the Board\'s discretion to deny such credit under the Prisons and Parole Code, he contends the Board violated the constitutional requirements in <em>Pittman v. Pennsylvania Board of Probation &amp; Parole</em>, <extracted-citation url="https://cite.case.law/pa/639/40/" index="18" case-ids="12278111">639 Pa. 40</extracted-citation>, <extracted-citation url="https://cite.case.law/a3d/159/466/" index="19" case-ids="12323540,12278111">159 A.3d 466</extracted-citation> (2017), because it "denied [him] credit without conducting any <em>individual assessment of the facts and circumstances</em> surrounding his parole revocation." <em><extracted-citation url="https://cite.case.law/a3d/159/466/" index="20" case-ids="12323540,12278111">Id.</extracted-citation></em><extracted-citation url="https://cite.case.law/a3d/159/466/" index="20" case-ids="12323540,12278111"> at 474</extracted-citation> (emphasis added); <em>see</em> Pet\'r\'s Br. at 19. We discern merit in this argument.</p>\n    <p id="p-37">Section 6138(a)(1) of the Prisons and Parole Code provides that any parolee who commits a crime punishable by imprisonment while on parole, and is convicted or found guilty of that crime, may be recommitted as a CPV. 61 Pa. C.S. \u00a7 6138(a)(1). Further, Section 6138(a)(2.1) of the Prisons and Parole Code, 61 Pa. C.S. \u00a7 6138(a)(2.1), "unambiguously grants the Board discretion to award credit to a CPV recommitted to serve the remainder of his sentence," except when the recommitment involves the reasons in subsections 6138(a)(2.1)(i) and (ii) (including violent and sexual offender crimes), not present here. <em>Pittman</em>, <extracted-citation url="https://cite.case.law/a3d/159/466/" index="21" case-ids="12323540,12278111">159 A.3d at 473</extracted-citation>.</p>\n    <p id="p-38">Relevant here, in <em>Pittman</em>, our Supreme Court held that in not explaining its exercise of discretion with reasons for awarding or denying credit, the Board violated its statutory mandate and denied a parolee\'s constitutional due process rights.<footnotemark>3</footnotemark> The Court reasoned that the Board satisfies constitutional due process by stating the reasons for exercising its discretion to deny credit for the time a parolee spent at liberty on parole. However, the Court did not set forth criteria for such a statement, noting only that it need not "be extensive and a single sentence explanation is likely sufficient in most instances." <em><extracted-citation url="https://cite.case.law/a3d/159/466/" index="22" case-ids="12323540,12278111">Id.</extracted-citation></em> at 475 n.12.</p>\n    <p id="p-39">Here, the Board\'s reason for denying Marshall credit for time spent at liberty on parole consisted of four words: "felony drug related crimes." C.R. at 49. Although the word "felony" connotes the severity of the offense, it remains unclear how a drug-related conviction warrants denying credit for almost nine years of street time, which is more than the sentence Marshall received for his new conviction.<footnotemark>4</footnotemark> Further, the phrase implies he committed multiple felony drug crimes when he was convicted of just one. <em>See</em> Pet\'r\'s Br. at 10, 19-22.</p>\n    <p id="p-40">Recently, when presented with a <em>Pittman</em> challenge to the sufficiency of a reason, this Court found a five-word reason for denying credit for street time "just barely sufficient." <em>See</em> <em>Smoak v. Pa. Bd. of Prob. &amp; Parole</em>, <extracted-citation url="https://cite.case.law/a3d/193/1160/#p1165" index="23" case-ids="12500932">193 A.3d 1160</extracted-citation>, 1165 (Pa. Cmwlth. 2018) ("unresolved drug and alcohol issues" was adequate). In <em>Smoak</em>, the Board\'s stated reason indicated multiple issues of an ongoing nature. There, the parolee was convicted on drug-related offenses, and was recommitted when he attempted to furnish drug-free urine to pass a drug test. The reason was accurate and related to the parolee\'s offenses. This Court was able to assess the Board\'s exercise of discretion in denying Smoak credit for approximately two years of street time.</p>\n    <p id="p-41">Since the Court decided <em>Pittman</em>, this Court assessed the Board\'s exercise of discretion in a number of cases. <em>See,</em> <em>e.g.</em>, <em>Hayward v. Pa. Bd. of Prob. &amp; Parole</em> (Pa. Cmwlth., No. 1735 C.D. 2017, filed July 18, 2018), slip op. at 5, <extracted-citation url="https://cite.case.law/citations/?q=2018%20WL%203447033" index="24">2018 WL 3447033</extracted-citation>, at *2 (unreported) (deeming "conviction involved possession of a weapon" sufficient reason);</p>\n    <p id="p-42"><a id="p651" href="#p651" data-label="651" data-citation-index="1" class="page-label">*651</a><em>Vann v. Pa. Bd. of Prob. &amp; Parole</em> (Pa. Cmwlth., No. 1067 C.D. 2017, filed April 10, 2018), slip op. at 18, <extracted-citation url="https://cite.case.law/citations/?q=2018%20WL%201722658" index="25">2018 WL 1722658</extracted-citation>, at *7 (unreported) (holding reason of "prior history of supervision failures" and "unresolved drug and alcohol issues" sufficed). We upheld the Board\'s discretion in denying street time credit when the reason showed recidivism. <em>See</em> <em>Johnson v. Pa. Bd. of Prob. &amp; Parole</em> (Pa. Cmwlth., No. 25 C.D. 2018, filed October 12, 2018), slip op. at 9, <extracted-citation url="https://cite.case.law/citations/?q=2018%20WL%204940327" index="26">2018 WL 4940327</extracted-citation>, at *5 (unreported) ("third firearm conviction" was sufficient reason); <em>Harvey v. Pa. Bd. of Prob. &amp; Parole</em> (Pa. Cmwlth., No. 1375 C.D. 2017, filed September 7, 2018), slip op. at 9, <extracted-citation url="https://cite.case.law/citations/?q=2018%20WL%204264935" index="27">2018 WL 4264935</extracted-citation>, at *3 (unreported) ("extensive history of illegal drug involvement" and new drug-related conviction were adequate reasons).</p>\n    <p id="p-43">However, in none of the prior cases has the Board used as few words that have no apparent relationship to the parolee. Further, none of these cases involved an unexplained inaccuracy in the stated reason.</p>\n    <p id="p-44"><em>Pittman</em> requires the Board to articulate a reason for exercising its discretion to deny credit for street time because "an appellate court hearing the matter must have method to assess the Board\'s exercise of discretion." <em>Id.</em> at 474. To meet the constitutional guarantees of due process, an agency\'s decision must be explained "in sufficient detail to permit meaningful appellate review." <em>Fisler v. State Sys. of Higher Educ., Cal. Univ. of Pa.</em>, <extracted-citation url="https://cite.case.law/a3d/78/30/#p41" index="28" case-ids="7302055">78 A.3d 30</extracted-citation>, 41 (Pa. Cmwlth. 2013) (quoting <em>Peak v. Unemployment Comp. Bd. of Review</em>, <extracted-citation url="https://cite.case.law/pa/509/267/" index="29" case-ids="1795308">509 Pa. 267</extracted-citation>, <extracted-citation url="https://cite.case.law/pa/509/267/" index="30" case-ids="1795308">501 A.2d 1383</extracted-citation>, 1389 (1985) ). There must be safeguards to ensure against arbitrary decision-making. <em>Peak</em>.</p>\n    <p id="p-45">Here, other than reference to a felony conviction, the Board\'s stated reason does not contain any facts that relate to this parolee. The significance of the "drug-related" modifier of crime is also unclear from this record. The record does not indicate that his prior conviction was drug related, or otherwise suggest recidivism. As to the commission of a felony while on parole, the commission of a felony could be one of the factors that the Board considers when exercising its discretion to award or withhold credit; however, standing alone, the commission of a felony is an insufficient articulation of the Board\'s reasoning.</p>\n    <p id="p-46">Our task here is to evaluate whether the Board abused its discretion in denying Marshall credit for almost nine years of street time for conviction of a drug-related felony. The Board\'s articulated reason simply restates the conviction without an individual assessment of the facts surrounding Marshall\'s parole revocation. Without further explication of the stated reason, the Board\'s reason for denying Marshall credit is not amenable to appellate review. <em>See</em> <em>Hinkle v. City of Phila.</em>, <extracted-citation url="https://cite.case.law/a2d/881/22/#p26" index="31" case-ids="8921395">881 A.2d 22</extracted-citation>, 26 (Pa. Cmwlth. 2005) ; <em>see</em> <em>also</em> <em>Pocono Mtn. Charter Sch., Inc. v. Pocono Mtn. Sch. Dist.</em>, <extracted-citation url="https://cite.case.law/a3d/88/275/#p290" index="32" case-ids="7324323">88 A.3d 275</extracted-citation>, 290 (Pa. Cmwlth. 2014) ("lack of written findings and reasons goes to the reviewability of a decision, not its validity.").</p>\n    <p id="p-47">Remand is appropriate to ensure this Court has a proper decision, capable of review, before it. <em>Newman Dev. Grp. of Pottstown, LLC v. Genuardi\'s Family Markets, Inc.</em>, <extracted-citation url="https://cite.case.law/pa/617/265/" index="33" case-ids="4210108">617 Pa. 265</extracted-citation>, <extracted-citation url="https://cite.case.law/a3d/52/1233/" index="34" case-ids="7313136,4210108">52 A.3d 1233</extracted-citation>, 1247 (2012) ("remands may encompass a variety of proceedings" including remand for an explanation). Accordingly, we remand to the Board for the limited purpose of explaining its exercise of discretion in its credit determination, and to correct any error in exercising that discretion based on the facts and circumstances of Marshall\'s <a id="p652" href="#p652" data-label="652" data-citation-index="1" class="page-label">*652</a>parole revocation.<footnotemark>5</footnotemark></p>\n    <p id="p-48">On remand, the Board should "articulate with reasonable clarity its reasons for decision, and identify the significance of the crucial facts." <em>Gruzinski v. Dep\'t of Pub. Welfare</em>, <extracted-citation url="https://cite.case.law/a2d/731/246/" index="35" case-ids="11663682">731 A.2d 246</extracted-citation>, 251 n.14 (Pa. Cmwlth. 1999). The Board\'s credit decision should contain sufficient facts related to the parolee "to ensure the decision is not arbitrary." <em>Barge v. Pa. Bd. of Prob. &amp; Parole</em>, <extracted-citation url="https://cite.case.law/a3d/39/530/#p548" index="36" case-ids="7314642">39 A.3d 530</extracted-citation>, 548 (Pa. Cmwlth. 2012). The Board\'s statement of reasons should be informed by aggravating and mitigating circumstances and account for the parolee\'s individual circumstances. Similar considerations guide a trial court\'s reasons in the sentencing context. <em>Accord</em> <em>Commonwealth v. Riggins</em>, <extracted-citation url="https://cite.case.law/pa/474/115/" index="37" case-ids="1732146">474 Pa. 115</extracted-citation>, <extracted-citation url="https://cite.case.law/pa/474/115/" index="38" case-ids="1732146">377 A.2d 140</extracted-citation> (1977) (vacating sentence and remanding to trial court for resentencing that states reasons for the particular sentence imposed). At a minimum, the Board\'s statement of reasons must accurately reflect the facts informing its decision.</p>\n    <p id="p-49"><strong>III. Conclusion</strong></p>\n    <p id="p-50">We vacate the Board\'s order, and remand the matter to the Board for the limited purpose of articulating reasons for its credit determination based on the facts and Marshall\'s circumstances in accordance with <em>Pittman</em>. In all other respects, the Board\'s decision is affirmed.</p>\n    <p id="p-51"><strong><em>ORDER</em></strong></p>\n    <p id="p-52"><strong>AND NOW</strong> , this 17<sup>th</sup> day of December, 2018, the order of the Pennsylvania Board of Probation and Parole (Board) is <strong>AFFIRMED IN PART</strong> , as to parole revocation and the recommitment range, and <strong>VACATED IN PART,</strong> as to the credit for time spent at liberty on parole determination. The matter is <strong>REMANDED</strong> to the Board to explain its exercise of discretion in its credit determination as to the time Petitioner Dwight Marshall spent at liberty on parole.</p>\n    <p id="p-53">Jurisdiction is relinquished.</p>\n    <p id="p-54">CONCURRING OPINION BY SENIOR JUDGE PELLEGRINI</p>\n    <p id="p-55">I join with the well-reasoned majority opinion but write separately to make clear that just because a parolee is being recommitted for a felony does not justify the Pennsylvania Board of Probation and Parole (Board) from withholding street time. 61 Pa.C.S. \u00a7 6138(a)(2.1) provides:</p>\n    <blockquote id="p-56">The crime committed during the period of parole or while delinquent on parole is a crime of violence as defined in 42 Pa.C.S. \u00a7 9714(g) (relating to sentences for second and subsequent offenses) or a crime requiring registration under 42 Pa.C.S. Ch. 97 Subch. H (relating to registration of sexual offenders).</blockquote>\n    <p id="p-57">Under this provision, the General Assembly has mandated that a parolee who is recommitted for a felony that is not violent or is not a sexual offense remains eligible for street time unless the Board articulates reasons that a longer period of incarceration is warranted for rehabilitative reasons. The Board cannot take away street time to punish a parolee just because he committed a non-excluded felony while on parole. Courts impose punishment, not the Board. If prior offenses auger in favor of more time in prison, it is up to the sentencing judge to take that into consideration <a id="p653" href="#p653" data-label="653" data-citation-index="1" class="page-label">*653</a>when determining an appropriate sentence on the new felony.</p>\n    <p id="p-58">Moreover, under 61 Pa.C.S. \u00a7 6138(a)(2.1), the General Assembly intended the Board to award street time unless there are exceptional circumstances that require the Board to take it away. While those circumstances can include the facts underlying the new felony, those facts have to be weighed against other factors before street time can be taken away. For example, if parole is revoked for felony theft that was committed to obtain drug money, then the Board could take away street time so that there is sufficient time for the parolee to receive drug treatment in prison. However, that may not be a sufficient reason to take the time away if the sentence on the larceny charge is sufficient for the parolee to complete drug treatment. As this example illustrates, each situation is extremely fact specific requiring the Board to go into detail to explain the reasoning behind its decision not to award street time.</p>\n    <footnote label="1">\n      <p id="p-61">Act of April 14, 1972, P.L. 233, <em>as</em> <em>amended</em>, 35 P.S. \u00a7 780-113(a)(30).</p>\n    </footnote>\n    <footnote label="2">\n      <p id="p-62">"Our review of the Board\'s decision is limited to determining whether constitutional rights were violated, whether the decision is in accordance with the law, or whether necessary findings are supported by substantial evidence." <em>Kerak v. Pa. Bd. of Prob. &amp; Parole</em>, <extracted-citation url="https://cite.case.law/a3d/153/1134/" index="39" case-ids="12320559">153 A.3d 1134</extracted-citation>, 1138 n.9 (Pa. Cmwlth. 2016).</p>\n    </footnote>\n    <footnote label="3">\n      <p id="p-63"><em>See</em> PA. Const. art. V, \u00a7 9 (providing a right to appeal from agency decisions).</p>\n    </footnote>\n    <footnote label="4">\n      <p id="p-64">Notably, Marshall\'s felony conviction is not listed among the crimes for which the Board lacks the discretion to award credit. <em>See</em> 61 Pa. C.S. \u00a7 6138(a)(2.1).</p>\n    </footnote>\n    <footnote label="5">\n      <p id="p-65"><em>See</em> <em>Hoover v. Pa. Bd. of Prob. &amp; Parole</em> (Pa. Cmwlth., No. 609 C.D. 2017, filed December 14, 2017), <extracted-citation url="https://cite.case.law/citations/?q=2017%20WL%206374750" index="40">2017 WL 6374750</extracted-citation> (unreported) (Board requested this Court order a remand limited to providing a reason for denial of credit for time spent at liberty on parole).</p>\n    </footnote>\n  </opinion>\n</casebody>\n',
        #         "status": "ok",
        #     },
        # }
        #
        # clean_dictionary_2 = combine_non_overlapping_data(
        #     cluster_2.pk, case_2_data
        # )
        #
        # merge_judges(cluster_2.pk, "judges", clean_dictionary_2.get("judges"))
        #
        # # Check that judges in harvard data is correct
        # self.assertEqual(clean_dictionary_2.get("judges")[0], "Simpson")
        #
        # cluster_2.refresh_from_db()
        #
        # # Test best option selected for judges is already in cl
        # self.assertEqual(
        #     cluster_2.judges,
        #     "Simpson, J. ~ Concurring Opinion by Pellegrini, Senior Judge",
        # )
        #
        # # Test 3: best option for judges is in harvard data
        # # From cluster id 4576003
        # cluster_3 = OpinionClusterWithParentsFactory(
        #     docket=DocketFactory(
        #         court=Court.objects.get(id="ohio"),
        #         case_name_short="",
        #         case_name="State v. Bishop (Slip Opinion)",
        #         case_name_full="",
        #     ),
        #     id=4576003,
        #     case_name="State v. Bishop (Slip Opinion)",
        #     case_name_short="",
        #     case_name_full="",
        #     date_filed=date(2018, 12, 21),
        #     judges="French, J.",
        # )
        #
        # case_3_data = {
        #     "name": "The STATE of Ohio, Appellant, v. BISHOP, Appellee.",
        #     "name_abbreviation": "State v. Bishop",
        #     "decision_date": "2018-12-21",
        #     "casebody": {
        #         "data": '<?xml version=\'1.0\' encoding=\'utf-8\'?>\n<casebody xmlns="http://nrs.harvard.edu/urn-3:HLS.Libr.US_Case_Law.Schema.Case_Body:v1" firstpage="766" lastpage="786">\n  <parties id="p-1">The STATE of Ohio, Appellant,<br/> v.<br/> BISHOP, Appellee.</parties>\n  <docketnumber id="p-2">Nos. 2017-1715<br/> 2017-1716</docketnumber>\n  <court id="p-3">Supreme Court of Ohio.</court>\n  <decisiondate id="p-4">Submitted July 18, 2018<br/> Decided December 21, 2018</decisiondate>\n  <attorneys id="p-5">Mathias H. Heck Jr., Montgomery County Prosecuting Attorney, and Michael J. Scarpelli and Andrew T. French, Assistant Prosecuting Attorneys, for appellant.</attorneys>\n  <attorneys id="p-6">Carl Bryan, Yellow Springs, for appellee.</attorneys>\n  <opinion type="majority">\n    <author id="p-7">French, J.</author>\n    <p id="p-8"><a id="p156" href="#p156" data-label="156" data-citation-index="1" class="page-label">*156</a><strong>{\u00b6 1}</strong> We are asked to resolve a certified conflict between judgments of the Second District Court of Appeals and the Fifth and Eighth District Courts of Appeals on the question "[w]hether a criminal defendant on [postrelease control] for a prior felony must be advised, during his plea hearing in a new felony case, of the trial court\'s ability under R.C. 2929.141 to terminate his existing [postrelease control] and to impose a consecutive prison sentence for the [postrelease-control] violation." <extracted-citation url="https://cite.case.law/ohio-st-3d/152/1404/" index="0" case-ids="12549477,12549478,12549482,12549483,12549461,12549463,12549465,12549470">152 Ohio St.3d 1404</extracted-citation>, <extracted-citation url="https://cite.case.law/ohio/2018/723/" index="1" case-ids="12549437,12549438,12549445,12549446,12549447,12549448,12549449,12549450,12549451,12549452,12549453,12549454,12549455,12549456,12549457,12549458,12549459,12549460,12549461,12549462,12549463,12549464,12549465,12549466,12549467,12549468,12549469,12549470,12549471,12549472,12549473,12549474,12549475,12549476,12549477,12549478,12549479,12549480,12549481,12549482,12549483,12549484,12549485,12549486,12549487,12549488,12549489,12549490,12549491,12549492,12549493,12549494,12549495,12549496,12549497,12549498,12549499,12549500,12549501,12549502,12549503,12549504,12549505,12549506,12549507,12549508,12549509,12549510,12549511,12549512,12549513,12549514,12549515,12549516,12549517,12549518,12549519,12549520,12549521,12549522,12549523,12549524,12549525,12549526,12549527,12549528,12549529,12549530,12549531,12549532,12549533,12549534,12549535,12549536,12549537,12549538,12549539,12549540,12549541,12549542,12549543,12549544,12549545,12549546,12549547,12549548,12549549,12549550,12549551,12549552,12549553,12549554,12549555,12549556,12549557,12549558,12549559,12549560,12549561,12549562,12549563,12549564,12549565,12549566,12549567,12549568,12549569,12549570,12549571,12549572,12549573,12549574,12549575,12549576,12549577,12549578,12549579,12549580,12549581,12549582,12549583,12549584,12549585,12549586,12549587,12549588,12549589,12549590,12549591,12549592,12549593,12549594,12549595,12549596,12549597,12549598,12549599,12549600,12549601,12549602">2018-Ohio-723</extracted-citation>, <extracted-citation url="https://cite.case.law/ne3d/92/877/" index="2" case-ids="12549472,12549473,12549474,12549475,12549476,12549477,12549478,12549479,12549480,12549481,12549482,12549483,12549484,12549470,12549471">92 N.E.3d 877</extracted-citation>. We conclude that Crim.R. 11(C)(2)(a) requires that advisement. We answer the certified question in the affirmative and affirm the judgment of the Second District Court of Appeals.</p>\n    <p id="p-9"><strong>I. Facts and Procedural History</strong></p>\n    <p id="p-10"><strong>{\u00b6 2}</strong> While on postrelease control for a prior felony conviction, appellee, Dustin Bishop, was indicted on one count of possession of heroin, a fifth-degree felony, and one count of possession of drug paraphernalia, a misdemeanor.</p>\n    <p id="p-11"><strong>{\u00b6 3}</strong> Bishop pleaded guilty to the possession count, and the state dismissed the drug-paraphernalia count. At Bishop\'s plea hearing, the trial court informed Bishop that the court could place him on postrelease control for the possession offense. It also informed him that if he committed a new felony while on that postrelease control, the court could sentence him to serve one year in prison or the time remaining on his postrelease control, whichever was longer. The trial <a id="p157" href="#p157" data-label="157" data-citation-index="1" class="page-label">*157</a>court did not inform Bishop that once he pleaded guilty to the possession offense, the court would have the authority under R.C. 2929.141 to terminate Bishop\'s existing postrelease control and impose a prison term that he would serve consecutively to the term of imprisonment imposed for the possession offense. The trial court accepted Bishop\'s guilty plea and set the matter for sentencing.</p>\n    <p id="p-12"><strong>{\u00b6 4}</strong> The trial court sentenced Bishop to serve a nine-month term of imprisonment for the possession offense. For the postrelease-control violation, the court ordered Bishop to serve a one-year prison term under R.C. 2929.141 consecutively to the sentence for the possession offense.</p>\n    <p id="p-13"><strong>{\u00b6 5}</strong> Bishop appealed to the Second District Court of Appeals, raising two assignments of error. Bishop first argued that he had not knowingly, intelligently, and voluntarily pleaded guilty to the possession offense because the trial court had not informed him of its authority under R.C. 2929.141 to terminate his postrelease control and to order him to serve a prison term consecutively to any term of imprisonment imposed for the felony offense to which he was pleading guilty. The appellate court, relying on its prior decisions in <em>State v. Branham</em> , 2d Dist. Clark No. 2013 CA 49, <extracted-citation url="https://cite.case.law/citations/?q=2014-Ohio-5067" index="3">2014-Ohio-5067</extracted-citation>, <extracted-citation url="https://cite.case.law/citations/?q=2014%20WL%206090404" index="4">2014 WL 6090404</extracted-citation>, and <a id="p769" href="#p769" data-label="769" data-citation-index="1" class="page-label">*769</a><em>State v. Landgraf</em> , 2d Dist. Clark No. 2014 CA 12, <extracted-citation url="https://cite.case.law/citations/?q=2014-Ohio-5448" index="5">2014-Ohio-5448</extracted-citation>, <extracted-citation url="https://cite.case.law/citations/?q=2014%20WL%207004830" index="6">2014 WL 7004830</extracted-citation>, sustained Bishop\'s first assignment of error and concluded that the trial court erred by failing to advise Bishop, at the time of his plea, that he could have to serve an additional, consecutive sentence for his current postrelease-control violation. <extracted-citation url="https://cite.case.law/citations/?q=2017-Ohio-8332" index="7">2017-Ohio-8332</extracted-citation>, \u00b6 7. The appellate court deemed Bishop\'s second assignment of error moot, reversed the trial court\'s judgment, vacated Bishop\'s guilty plea, and remanded the matter for further proceedings. <em>Id.</em> at \u00b6 8-9.</p>\n    <p id="p-14"><strong>{\u00b6 6}</strong> The appellate court, upon the state\'s motion, certified that its decision conflicted with the Fifth District Court of Appeals\' decision in <em>State v. Hicks</em> , 5th Dist. Delaware No. 09CAA090088, <extracted-citation url="https://cite.case.law/citations/?q=2010-Ohio-2985" index="8">2010-Ohio-2985</extracted-citation>, <extracted-citation url="https://cite.case.law/citations/?q=2010%20WL%202595153" index="9">2010 WL 2595153</extracted-citation>, and the Eighth District Court of Appeals\' decision in <em>State v. Dotson</em> , 8th Dist. Cuyahoga No. 101911, <extracted-citation url="https://cite.case.law/citations/?q=2015-Ohio-2392" index="10">2015-Ohio-2392</extracted-citation>, <extracted-citation url="https://cite.case.law/citations/?q=2015%20WL%203819178" index="11">2015 WL 3819178</extracted-citation>. The state filed a notice of certified conflict and a jurisdictional appeal in this court. We determined that a conflict exists and consolidated the conflict case with the state\'s jurisdictional appeal. <extracted-citation url="https://cite.case.law/ohio-st-3d/152/1404/" index="12" case-ids="12549477,12549478,12549482,12549483,12549461,12549463,12549465,12549470">152 Ohio St.3d 1404</extracted-citation>, <extracted-citation url="https://cite.case.law/ohio/2018/723/" index="13" case-ids="12549437,12549438,12549445,12549446,12549447,12549448,12549449,12549450,12549451,12549452,12549453,12549454,12549455,12549456,12549457,12549458,12549459,12549460,12549461,12549462,12549463,12549464,12549465,12549466,12549467,12549468,12549469,12549470,12549471,12549472,12549473,12549474,12549475,12549476,12549477,12549478,12549479,12549480,12549481,12549482,12549483,12549484,12549485,12549486,12549487,12549488,12549489,12549490,12549491,12549492,12549493,12549494,12549495,12549496,12549497,12549498,12549499,12549500,12549501,12549502,12549503,12549504,12549505,12549506,12549507,12549508,12549509,12549510,12549511,12549512,12549513,12549514,12549515,12549516,12549517,12549518,12549519,12549520,12549521,12549522,12549523,12549524,12549525,12549526,12549527,12549528,12549529,12549530,12549531,12549532,12549533,12549534,12549535,12549536,12549537,12549538,12549539,12549540,12549541,12549542,12549543,12549544,12549545,12549546,12549547,12549548,12549549,12549550,12549551,12549552,12549553,12549554,12549555,12549556,12549557,12549558,12549559,12549560,12549561,12549562,12549563,12549564,12549565,12549566,12549567,12549568,12549569,12549570,12549571,12549572,12549573,12549574,12549575,12549576,12549577,12549578,12549579,12549580,12549581,12549582,12549583,12549584,12549585,12549586,12549587,12549588,12549589,12549590,12549591,12549592,12549593,12549594,12549595,12549596,12549597,12549598,12549599,12549600,12549601,12549602">2018-Ohio-723</extracted-citation>, <extracted-citation url="https://cite.case.law/ne3d/92/877/" index="14" case-ids="12549472,12549473,12549474,12549475,12549476,12549477,12549478,12549479,12549480,12549481,12549482,12549483,12549484,12549470,12549471">92 N.E.3d 877</extracted-citation>.</p>\n    <p id="p-15"><strong>II. Intervening Trial-Court Proceedings</strong></p>\n    <p id="p-16"><strong>{\u00b6 7}</strong> According to the state\'s merit brief, on January 29, 2018-after the state had appealed the court of appeals\' judgment to this court but prior to our accepting jurisdiction-the trial court accepted Bishop\'s new guilty plea to the same possession offense and sentenced him to time served. We must address whether this case is moot.</p>\n    <p id="p-17"><a id="p158" href="#p158" data-label="158" data-citation-index="1" class="page-label">*158</a><strong>{\u00b6 8}</strong> Nothing in the record before us confirms that the trial court did, in fact, accept a new guilty plea. But even if the court did accept a new plea, we have held that we may resolve a matter, even if it is moot with respect to the parties, when it involves an issue of great public or general interest that will outlive the instant controversy. <em>See, e.g.</em> , <em>Franchise Developers, Inc. v. Cincinnati</em> , <extracted-citation url="https://cite.case.law/ohio-st-3d/30/28/#p31" index="15" case-ids="1826455">30 Ohio St.3d 28</extracted-citation>, 31, <extracted-citation url="https://cite.case.law/citations/?q=505%20N.E.2d%20966" index="16">505 N.E.2d 966</extracted-citation> (1987). We have recognized this exception to the mootness doctrine in other certified-conflict cases and held that it was appropriate to resolve the question of law presented. <em>State v. Massien</em> , <extracted-citation url="https://cite.case.law/ohio-st-3d/125/204/" index="17" case-ids="4094971">125 Ohio St.3d 204</extracted-citation>, <extracted-citation url="https://cite.case.law/citations/?q=2010-Ohio-1864" index="18">2010-Ohio-1864</extracted-citation>, <extracted-citation url="https://cite.case.law/citations/?q=926%20N.E.2d%201282" index="19">926 N.E.2d 1282</extracted-citation>, \u00b6 4, fn. 1 ; <em>State v. Brooks</em> , <extracted-citation url="https://cite.case.law/ohio-st-3d/103/134/" index="20" case-ids="1503035">103 Ohio St.3d 134</extracted-citation>, <extracted-citation url="https://cite.case.law/citations/?q=2004-Ohio-4746" index="21">2004-Ohio-4746</extracted-citation>, <extracted-citation url="https://cite.case.law/citations/?q=814%20N.E.2d%20837" index="22">814 N.E.2d 837</extracted-citation>, \u00b6 5. For this reason, we proceed to consider the certified-conflict question.</p>\n    <p id="p-18"><strong>III. Analysis</strong></p>\n    <p id="p-19"><strong>{\u00b6 9}</strong> Turning to the merits, we confront a conflict between judgments of the Second District Court of Appeals and the Fifth and Eighth District Courts of Appeals regarding an interpretation of the requirements of Crim.R. 11(C). The Second District Court of Appeals has held that the trial court must inform a defendant who is on postrelease control and is pleading guilty to a new felony offense of the trial court\'s authority to revoke the defendant\'s postrelease control and impose a prison term to be served consecutively to any term of imprisonment it imposes for that new felony offense. <em>See</em> <extracted-citation url="https://cite.case.law/citations/?q=2017-Ohio-8332" index="23">2017-Ohio-8332</extracted-citation> at \u00b6 7 ; <em>Branham</em> , 2d Dist. Clark No. 2013 CA 49, <extracted-citation url="https://cite.case.law/citations/?q=2014-Ohio-5067" index="24">2014-Ohio-5067</extracted-citation>, at \u00b6 14. The Second District has interpreted that requirement to be part of the trial court\'s duty under Crim.R. 11(C)(2)(a) to advise the defendant of "the maximum penalty involved." <em>See</em> <em>Landgraf</em> , 2d Dist. Clark No. 2014 CA 12, <extracted-citation url="https://cite.case.law/citations/?q=2014-Ohio-5448" index="25">2014-Ohio-5448</extracted-citation>, at \u00b6 23 (lead opinion). In contrast, the Fifth and Eighth District Courts of Appeals have held that Crim.R. 11 does not require the trial court to inform the defendant of the possible effects of his guilty plea to a new offense on his postrelease control. <em>Hicks</em> , 5th Dist. Delaware No. 09CAA090088, <extracted-citation url="https://cite.case.law/citations/?q=2010-Ohio-2985" index="26">2010-Ohio-2985</extracted-citation>, at \u00b6 10-13 ( Crim.R. 11(D) did not require the trial court to inform <a id="p770" href="#p770" data-label="770" data-citation-index="1" class="page-label">*770</a>the defendant, who was pleading guilty to a misdemeanor offense, of the possible effects of his plea on his postrelease control); <em>Dotson</em> , 8th Dist. Cuyahoga No. 101911, <extracted-citation url="https://cite.case.law/citations/?q=2015-Ohio-2392" index="27">2015-Ohio-2392</extracted-citation>, at \u00b6 13 ( Crim.R. 11(C) did not require the trial court to inform the defendant, who was pleading guilty to a felony offense, of the possible effects of his plea on his postrelease control).</p>\n    <p id="p-20"><strong>{\u00b6 10}</strong> A criminal defendant\'s choice to enter a guilty plea is a serious decision. <em>State v. Clark</em> , <extracted-citation url="https://cite.case.law/ohio-st-3d/119/239/" index="28" case-ids="3843146">119 Ohio St.3d 239</extracted-citation>, <extracted-citation url="https://cite.case.law/citations/?q=2008-Ohio-3748" index="29">2008-Ohio-3748</extracted-citation>, <extracted-citation url="https://cite.case.law/citations/?q=893%20N.E.2d%20462" index="30">893 N.E.2d 462</extracted-citation>, \u00b6 25. Due process requires that a defendant\'s plea be made knowingly, intelligently, and voluntarily; otherwise, the defendant\'s plea is invalid. <em><extracted-citation url="https://cite.case.law/citations/?q=893%20N.E.2d%20462" index="31">Id.</extracted-citation></em></p>\n    <p id="p-21"><strong>{\u00b6 11}</strong> Crim.R. 11(C) prescribes the process that a trial court must use before accepting a plea of guilty to a felony.</p>\n    <p id="p-22"><a id="p159" href="#p159" data-label="159" data-citation-index="1" class="page-label">*159</a><em>State v. Veney</em> , <extracted-citation url="https://cite.case.law/ohio-st-3d/120/176/" index="32" case-ids="3695059">120 Ohio St.3d 176</extracted-citation>, <extracted-citation url="https://cite.case.law/citations/?q=2008-Ohio-5200" index="33">2008-Ohio-5200</extracted-citation>, <extracted-citation url="https://cite.case.law/citations/?q=897%20N.E.2d%20621" index="34">897 N.E.2d 621</extracted-citation>, \u00b6 8. The trial court must follow certain procedures and engage the defendant in a detailed colloquy before accepting his or her plea. <em>Clark</em> at \u00b6 26 ; <em>see</em> Crim.R. 11(C). The court must make the determinations and give the warnings that Crim.R. 11(C)(2)(a) and (b) require and must notify the defendant of the constitutional rights that Crim.R. 11(C)(2)(c) identifies. <em>Veney</em> at \u00b6 13. While the court must strictly comply with the requirements listed in Crim.R. 11(C)(2)(c), the court need only substantially comply with the requirements listed in Crim.R. 11(C)(2)(a) and (b). <em>Id.</em> at \u00b6 18.</p>\n    <p id="p-23"><strong>{\u00b6 12}</strong> Most relevant here, Crim.R. 11(C)(2) includes the following among the determinations a trial court must make:</p>\n    <blockquote id="p-24">(a) Determining that the defendant is making the plea voluntarily, with understanding of the nature of the charges and of the maximum penalty involved, and if applicable, that the defendant is not eligible for probation or for the imposition of community control sanctions at the sentencing hearing.</blockquote>\n    <p id="p-25"><strong>{\u00b6 13}</strong> We must also consider the specifics of R.C. 2929.141. That statute provides that when a defendant who is on postrelease control is convicted of or pleads guilty to a new felony, the trial court may terminate the postrelease-control term and convert it into additional prison time. R.C. 2929.141(A)(1). This additional penalty is often referred to as a "judicial sanction." <em>See, e.g.</em> , <em>State v. Grimes</em> , <extracted-citation url="https://cite.case.law/ohio-st-3d/151/19/" index="35" case-ids="12280189">151 Ohio St.3d 19</extracted-citation>, <extracted-citation url="https://cite.case.law/ohio-st-3d/151/19/" index="36" case-ids="12280189">2017-Ohio-2927</extracted-citation>, <extracted-citation url="https://cite.case.law/citations/?q=85%20N.E.3d%20700" index="37">85 N.E.3d 700</extracted-citation>, \u00b6 25. The additional term can be as long as the greater of 12 months or the amount of time that remained on the existing postrelease-control term. R.C. 2929.141(A)(1). The court is not required to impose an additional prison term for the violation. <em>See <extracted-citation url="https://cite.case.law/citations/?q=85%20N.E.3d%20700" index="38">id.</extracted-citation></em> But if it does, the defendant must serve the additional term consecutively to the prison term for the new felony. <em><extracted-citation url="https://cite.case.law/citations/?q=85%20N.E.3d%20700" index="39">Id.</extracted-citation></em></p>\n    <p id="p-26"><strong><em>A. Crim.R. 11(C)(2)(a) -The "maximum penalty involved" includes the potential R.C. 2929.141(A) sentence</em></strong></p>\n    <p id="p-27"><strong>{\u00b6 14}</strong> At issue here is the impact of R.C. 2929.141(A) on the portion of Crim.R. 11(C)(2)(a) that requires a trial court to ensure during the plea hearing that the defendant is entering his guilty plea "with understanding of the nature of the charges and of the maximum penalty involved." In arguing that the trial court need not inform a defendant of a potential consecutive prison term under R.C. 2929.141(A), appellant, the state of Ohio, bypasses the plain language of the statute and the rule and looks instead to this court\'s decision in <em>State v. Johnson</em> , <extracted-citation url="https://cite.case.law/ohio-st-3d/40/130/" index="40" case-ids="1415447">40 Ohio St.3d 130</extracted-citation>, <extracted-citation url="https://cite.case.law/citations/?q=532%20N.E.2d%201295" index="41">532 N.E.2d 1295</extracted-citation> (1988). In <em>Johnson</em> , we reasoned that neither the United States Constitution nor the Ohio Constitution requires a trial court to <a id="p771" href="#p771" data-label="771" data-citation-index="1" class="page-label">*771</a><a id="p160" href="#p160" data-label="160" data-citation-index="1" class="page-label">*160</a>inform a defendant during his plea hearing of the maximum total of the sentences he faces or that the sentences can be imposed consecutively. <em>Id.</em> at 133, <extracted-citation url="https://cite.case.law/citations/?q=532%20N.E.2d%201295" index="42">532 N.E.2d 1295</extracted-citation>. Regarding Crim.R. 11, we said that "[i]t would seem to be beyond a reasonable interpretation to suggest that the rule refers cumulatively to the total of all sentences received for all charges which a criminal defendant may answer in a single proceeding." <em><extracted-citation url="https://cite.case.law/citations/?q=532%20N.E.2d%201295" index="43">Id.</extracted-citation></em> We concluded that because the trial court in <em>Johnson</em> explained to the defendant the individual maximum sentences possible, his guilty plea was proper. <em><extracted-citation url="https://cite.case.law/citations/?q=532%20N.E.2d%201295" index="44">Id.</extracted-citation></em></p>\n    <p id="p-28"><strong>{\u00b6 15}</strong> Crim.R. 11(C)(2)(a) has been amended since <em>Johnson</em> so that a single plea can now apply to multiple charges, <em>see</em> 83 Ohio St.3d xciii, cix (effective July 1, 1998). Nevertheless, the state argues that the rule\'s advisements still apply only to the "maximum penalty involved" for the crimes to which the defendant pleads guilty. We disagree.</p>\n    <p id="p-29"><strong>{\u00b6 16}</strong> First, what happened to the defendant in <em>Johnson</em> is a far cry from what happened to Bishop. Johnson was told of his potential sentences for each individual offense; the trial court just failed to tell Johnson the sentences for each offense could run consecutively. Here, the trial court told Bishop that he could receive a maximum sentence of 12 months for his fifth-degree-felony conviction. But the trial court did not tell Bishop that he was also subject to a separate consecutive 12-month sentence for his postrelease-control violation.</p>\n    <p id="p-30"><strong>{\u00b6 17}</strong> Second, and more importantly, we must look to the plain language of the statutes involved. R.C. 2929.141(A)(1) provides that "[u]pon the conviction of or plea of guilty to a felony by a person on post-release control at the time of the commission of the felony, the court may terminate the term of post-release control" and impose a consecutive prison term. Sentences imposed under R.C. 2929.141(A) cannot stand alone. The court may impose the sentence only upon a conviction for or plea of guilty to a new felony, making the sentence for committing a new felony while on postrelease control and that for the new felony itself inextricably intertwined. By any fair reading of Crim.R. 11(C)(2), the potential R.C. 2929.141(A) sentence was part of the "maximum penalty involved" in this case.</p>\n    <p id="p-31"><strong><em>B. Bishop need not show prejudice</em></strong></p>\n    <p id="p-32"><strong>{\u00b6 18}</strong> Finally, Bishop need not show that the trial court\'s error prejudiced him-i.e., that he would not have entered the guilty plea if he had known that the trial court could terminate his existing postrelease control and convert it into additional prison time, <em>see</em> <em>State v. Nero</em> , <extracted-citation url="https://cite.case.law/ohio-st-3d/56/106/#p108" index="45" case-ids="1427556">56 Ohio St.3d 106</extracted-citation>, 108, <extracted-citation url="https://cite.case.law/citations/?q=564%20N.E.2d%20474" index="46">564 N.E.2d 474</extracted-citation> (1990), citing <em>State v. Stewart</em> , <extracted-citation url="https://cite.case.law/ohio-st-2d/51/86/#p93" index="47" case-ids="1800393">51 Ohio St.2d 86</extracted-citation>, 93, <extracted-citation url="https://cite.case.law/citations/?q=364%20N.E.2d%201163" index="48">364 N.E.2d 1163</extracted-citation> (1977).</p>\n    <p id="p-33"><strong>{\u00b6 19}</strong> A trial court need only substantially comply with the nonconstitutional advisements listed in Crim.R. 11(C)(2)(a).</p>\n    <p id="p-34"><a id="p161" href="#p161" data-label="161" data-citation-index="1" class="page-label">*161</a><em>Veney</em> , <extracted-citation url="https://cite.case.law/ohio-st-3d/120/176/" index="49" case-ids="3695059">120 Ohio St.3d 176</extracted-citation>, <extracted-citation url="https://cite.case.law/citations/?q=2008-Ohio-5200" index="50">2008-Ohio-5200</extracted-citation>, <extracted-citation url="https://cite.case.law/citations/?q=897%20N.E.2d%20621" index="51">897 N.E.2d 621</extracted-citation>, at \u00b6 18. But "[w]hen the trial judge does not <em>substantially</em> comply with Crim.R. 11 in regard to a nonconstitutional right, reviewing courts must determine whether the trial court <em>partially</em> complied or <em>failed</em> to comply with the rule." (Emphasis sic.) <em>Clark</em> , <extracted-citation url="https://cite.case.law/ohio-st-3d/119/239/" index="52" case-ids="3843146">119 Ohio St.3d 239</extracted-citation>, <extracted-citation url="https://cite.case.law/citations/?q=2008-Ohio-3748" index="53">2008-Ohio-3748</extracted-citation>, <extracted-citation url="https://cite.case.law/citations/?q=893%20N.E.2d%20462" index="54">893 N.E.2d 462</extracted-citation>, at \u00b6 32. "If the trial judge partially complied, e.g., by mentioning mandatory postrelease control without explaining it, the plea may be vacated only if the defendant demonstrates a prejudicial effect." <em><extracted-citation url="https://cite.case.law/citations/?q=893%20N.E.2d%20462" index="55">Id.</extracted-citation></em> But if the trial court completely failed to comply with the rule, the plea must be vacated. <em><extracted-citation url="https://cite.case.law/citations/?q=893%20N.E.2d%20462" index="56">Id.</extracted-citation></em> Complete failure " \'to comply with the rule does not implicate an analysis of prejudice.\' " <em><extracted-citation url="https://cite.case.law/citations/?q=893%20N.E.2d%20462" index="57">Id.</extracted-citation></em> , quoting <a id="p772" href="#p772" data-label="772" data-citation-index="1" class="page-label">*772</a><em>State v. Sarkozy</em> , <extracted-citation url="https://cite.case.law/ohio-st-3d/117/86/" index="58" case-ids="3801976">117 Ohio St.3d 86</extracted-citation>, <extracted-citation url="https://cite.case.law/citations/?q=2008-Ohio-509" index="59">2008-Ohio-509</extracted-citation>, <extracted-citation url="https://cite.case.law/citations/?q=881%20N.E.2d%201224" index="60">881 N.E.2d 1224</extracted-citation>, \u00b6 22.</p>\n    <p id="p-35"><strong>{\u00b6 20}</strong> Here, the trial court completely failed to inform Bishop that a consecutive prison sentence under R.C. 2929.141(A) was possible. That is not partial compliance. Bishop need not show prejudice.</p>\n    <p id="p-36"><strong>IV. Conclusion</strong></p>\n    <p id="p-37"><strong>{\u00b6 21}</strong> We conclude that Crim.R. 11(C)(2)(a) requires a trial court to advise a criminal defendant on postrelease control for a prior felony, during his plea hearing in a new felony case, of the trial court\'s authority under R.C. 2929.141 to terminate the defendant\'s existing postrelease control and to impose a consecutive prison sentence for the postrelease-control violation. We therefore answer the certified question in the affirmative and affirm the judgment of the Second District Court of Appeals.</p>\n    <p id="p-38">Judgment affirmed.</p>\n    <p id="p-39">O\'Connor, C.J., and O\'Donnell, J., concur.</p>\n    <p id="p-40">DeWine, J., concurs in judgment only, with an opinion.</p>\n    <p id="p-41">Kennedy, J., dissents, with an opinion.</p>\n    <p id="p-42">Fischer, J., dissents, with an opinion joined by Brown, J.</p>\n    <p id="p-43">Susan D. Brown, J., of the Tenth District Court of Appeals, sitting for DeGenaro, J.</p>\n    <p id="p-44">DeWine, J., concurring in judgment only.</p>\n    <p id="p-45"><strong>{\u00b6 22}</strong> I agree that the judgment of the court of appeals should be affirmed. The potential sentence for a postrelease-control violation is part of the "maximum penalty involved" when a defendant pleads guilty to a new felony. I write separately, however, because I disagree with the lead opinion\'s dictum about mootness.</p>\n    <p id="p-46"><a id="p162" href="#p162" data-label="162" data-citation-index="1" class="page-label">*162</a><strong>{\u00b6 23}</strong> There is no question that this case is not moot. As the lead opinion notes, there is nothing in the record to confirm that the trial court accepted Dustin Bishop\'s guilty plea following the state\'s notice of appeal to this court. And even if the trial court did act, its order would be void because it acted without jurisdiction.</p>\n    <p id="p-47"><strong>{\u00b6 24}</strong> In its decision on October 27, 2017, the court of appeals remanded this case for resentencing by the trial court. The state filed a timely notice of appeal on December 7, 2017. According to the state\'s merit brief, before this court had accepted jurisdiction, the trial court, acting on the remand order, resentenced Bishop. But once the notice of appeal was filed in this court, the trial court was divested of jurisdiction. We were confronted with a similar situation in <em>State v. Washington</em> , <extracted-citation url="https://cite.case.law/ohio-st-3d/137/427/" index="61" case-ids="3852447">137 Ohio St.3d 427</extracted-citation>, <extracted-citation url="https://cite.case.law/citations/?q=2013-Ohio-4982" index="62">2013-Ohio-4982</extracted-citation>, <extracted-citation url="https://cite.case.law/citations/?q=999%20N.E.2d%20661" index="63">999 N.E.2d 661</extracted-citation>, in which, after the state filed a notice of appeal but before this court accepted jurisdiction, the trial court acted on a remand order to resentence a defendant. The defendant moved to dismiss the state\'s appeal as moot. This court denied the motion:</p>\n    <blockquote id="p-48">"An appeal is perfected upon the filing of a written notice of appeal. Once a case has been appealed, the trial court loses jurisdiction except to take action in aid of the appeal." Thus, the trial court in this case had no jurisdiction to resentence the defendant once the state had filed its notice of appeal.</blockquote>\n    <p id="p-49">(Citations omitted.) <em>Id.</em> at \u00b6 8, quoting <em>In re S.J.</em> , <extracted-citation url="https://cite.case.law/ohio-st-3d/106/11/" index="64" case-ids="1257860">106 Ohio St.3d 11</extracted-citation>, <extracted-citation url="https://cite.case.law/citations/?q=2005-Ohio-3215" index="65">2005-Ohio-3215</extracted-citation>, <extracted-citation url="https://cite.case.law/citations/?q=829%20N.E.2d%201207" index="66">829 N.E.2d 1207</extracted-citation>, \u00b6 9. Likewise, the trial court here had no jurisdiction to resentence <a id="p773" href="#p773" data-label="773" data-citation-index="1" class="page-label">*773</a>Bishop. Therefore, even if the trial court did act, its order would be void and the state\'s appeal would not be moot.</p>\n    <p id="p-50"><strong>{\u00b6 25}</strong> Because the state\'s appeal is not moot, there is no need to digress into a discussion of the propriety of considering certified-conflict questions in moot cases. But because the lead opinion takes that path, I write to explain why I believe its dictum is misguided.</p>\n    <p id="p-51"><strong>{\u00b6 26}</strong> The Ohio Constitution vests the "judicial power of the state" in "a supreme court, courts of appeals, courts of common pleas and divisions thereof, and such other courts inferior to the supreme court as may from time to time be established by law." Ohio Constitution, Article IV, Section 1. While the language of our Constitution does not mirror the "cases" and "controversies" language of the United States Constitution, <em>see</em> United States Constitution, Article III, Section 2, it is generally understood that the grant of the judicial power requires that we decide only "actual controversies where the judgment can be carried into effect, and not to give opinions upon moot questions or abstract propositions, or to declare principles or rules of law which cannot affect the matter at issue in the <a id="p163" href="#p163" data-label="163" data-citation-index="1" class="page-label">*163</a>case before it," <em>Travis v. Pub. Util. Comm.</em> , <extracted-citation url="https://cite.case.law/ohio-st/123/355/#p359" index="67" case-ids="999335">123 Ohio St. 355</extracted-citation>, 359, <extracted-citation url="https://cite.case.law/citations/?q=175%20N.E.%20586" index="68">175 N.E. 586</extracted-citation> (1931). When a case becomes moot, there is no longer a controversy for this court to decide.</p>\n    <p id="p-52"><strong>{\u00b6 27}</strong> We have recognized exceptions to this principle and have decided cases that were moot after having found that the issues presented were capable of repetition yet evading review. <em>See</em> <em>Adkins v. McFaul</em> , <extracted-citation url="https://cite.case.law/ohio-st-3d/76/350/#p350" index="69" case-ids="1749807">76 Ohio St.3d 350</extracted-citation>, 350-351, <extracted-citation url="https://cite.case.law/citations/?q=667%20N.E.2d%201171" index="70">667 N.E.2d 1171</extracted-citation> (1996). But there is no reason to believe that the issue in this case-plea-hearing requirements for defendants currently on postrelease control-will evade review. Nor does the fact that this case raises a question of public or great general interest militate against applying the mootness doctrine. By definition, many cases we accept on jurisdictional appeal involve questions of "public or great general interest pursuant to Article IV, Section 2(B)(2)(e) of the Ohio Constitution." S.Ct.Prac.R. 5.02(A)(3). But being of public or great general interest has never been considered sufficient to allow us to decide a jurisdictional appeal that has been rendered moot by subsequent events.</p>\n    <p id="p-53"><strong>{\u00b6 28}</strong> Despite the constitutional provision tying our authority to the judicial power, the justices joining the lead opinion apparently believe that different rules apply to appeals that come to us as certified conflicts. But like our review of jurisdictional appeals, our review of certified-conflict questions depends on the existence of a case. If a court of appeals finds that its judgment conflicts with that of another court, it certifies "the record of <em>the case</em> to the supreme court for review and <em>final determination</em> ." (Emphasis added.) Ohio Constitution, Article IV, Section 3 (B)(4); <em>see</em> S.Ct.Prac.R. 8.02(D). Unlike certified state-law questions from federal court, which we answer without deciding the underlying case, we decide certified-conflict cases and enter judgment. If a case becomes moot, there is no controversy for us to decide and we should dismiss it.</p>\n    <p id="p-54"><strong>{\u00b6 29}</strong> But all of this discussion is unnecessarily advisory. This case is not moot. We should limit our discussion to the controversy before us.</p>\n  </opinion>\n  <opinion type="dissent">\n    <author id="p-55">Kennedy, J., dissenting.</author>\n    <p id="p-56"><strong>{\u00b6 30}</strong> When an offender violates the terms of his or her postrelease control by committing a new felony, the offender may be prosecuted for the new felony and judicially sanctioned with a prison term for the postrelease-control violation.</p>\n    <p id="p-57"><a id="p774" href="#p774" data-label="774" data-citation-index="1" class="page-label">*774</a>R.C. 2929.141(A). At issue in this case is whether Crim.R. 11(C)(2)(a) requires a trial court taking a guilty plea to the new felony to advise the accused that an additional, consecutive sentence for the postrelease-control violation may be imposed.</p>\n    <p id="p-58"><strong>{\u00b6 31}</strong> A trial court may accept a plea only if it is knowingly, intelligently, and voluntarily made, and relevant here, Crim.R. 11(C)(2)(a) directs the court to inform the accused of the maximum penalty for each offense charged that will be <a id="p164" href="#p164" data-label="164" data-citation-index="1" class="page-label">*164</a>resolved by the plea. However, a postrelease-control violation does not result in a criminal "charge" because it is not a new criminal offense and involves only a possible judicial sanction separate from the punishment that may be imposed for the new felony. Therefore, because the trial court is not required to advise the accused about the judicial sanction that may be imposed pursuant to R.C. 2929.141(A), I dissent and would answer the certified question in the negative and reverse the judgment of the Second District Court of Appeals.</p>\n    <p id="p-59"><strong>Facts and Procedural History</strong></p>\n    <p id="p-60"><strong>{\u00b6 32}</strong> Appellee, Dustin Bishop, was indicted on two counts: possession of heroin, a fifth-degree felony, and possession of drug paraphernalia, a misdemeanor. Appellant, the state of Ohio, and Bishop entered into a plea agreement in which he agreed to plead guilty to heroin possession in exchange for the dismissal of the drug-paraphernalia count.</p>\n    <p id="p-61"><strong>{\u00b6 33}</strong> At the plea hearing, the trial court informed Bishop that the fifth-degree felony count of heroin possession carried a maximum penalty of 12 months in prison and a $2,500 fine. It also advised him that he could be placed on community control and that if he violated its terms, he could be imprisoned for 12 months. The court further told him:</p>\n    <blockquote id="p-62">Upon finishing any prison sentence, you may be placed on what\'s called post-release control or PRC wherein you\'d be under the supervision of the parole board for three years. Do you understand that?</blockquote>\n    <blockquote id="p-63">THE DEFENDANT: Yes.</blockquote>\n    <blockquote id="p-64">THE COURT: If you violate any of the terms of your release from prison or you violate any law while you\'re under the supervision of the parole board then the parole board can add onto your sentence nine months for each individual violation up to a total of 50 percent of the stated prison term for multiple violations.</blockquote>\n    <blockquote id="p-65">If your violation is a felony, you could receive from the Court a prison term of either one year or whatever time is remaining on the post-release control, whichever is the longer time, plus you could be prosecuted and sentenced for the new felony, itself.</blockquote>\n    <blockquote id="p-66">Also, for any violations, the parole board could extend the length of the post-release control or impose other more restrictive sanctions upon you.</blockquote>\n    <blockquote id="p-67">I mentioned there, I believe, three items without giving you a chance to respond right away. Do you understand all that?</blockquote>\n    <blockquote id="p-68">THE DEFENDANT: Yes.</blockquote>\n    <p id="p-69"><a id="p165" href="#p165" data-label="165" data-citation-index="1" class="page-label">*165</a>(Capitalization sic.)</p>\n    <p id="p-70"><strong>{\u00b6 34}</strong> Neither Bishop nor defense counsel informed the trial court that Bishop was on postrelease control when he committed the new felony, and there was no objection to the court\'s failure to inform Bishop that R.C. 2929.141(A) might subject him to a consecutive prison sentence for the postrelease-control violation. After being <a id="p775" href="#p775" data-label="775" data-citation-index="1" class="page-label">*775</a>informed of the constitutional rights he would be waiving, Bishop pleaded guilty to heroin possession. The court ordered a presentence investigation and scheduled a sentencing hearing.</p>\n    <p id="p-71"><strong>{\u00b6 35}</strong> The presentence-investigation report contains the earliest mention in the record of the fact that Bishop was on postrelease control when he committed the new felony. At sentencing, the trial court noted Bishop\'s significant criminal history (including 14 prior felony convictions) and that he was on postrelease control at the time of his newest offense, and it imposed a 12-month sentence to be served consecutively with a 9-month sentence for heroin possession. Neither Bishop nor defense counsel objected, and Bishop did not move to withdraw his plea due to a surprise at sentencing.</p>\n    <p id="p-72"><strong>{\u00b6 36}</strong> Rather, Bishop challenged the validity of his plea for the first time on appeal, asserting that he had not knowingly, intelligently, and voluntarily entered the plea because the trial court had not informed him that R.C. 2929.141 permitted the court to terminate his postrelease control and order him to serve consecutive prison terms for the new felony and the violation of the terms of his postrelease control. The court of appeals agreed that the plea was invalid because of the lack of this advisement, and it vacated the guilty plea and remanded the matter for further proceedings. <extracted-citation url="https://cite.case.law/citations/?q=2017-Ohio-8332" index="71">2017-Ohio-8332</extracted-citation>, \u00b6 7, 9. We accepted the state\'s discretionary appeal and recognized that the Second District\'s decision conflicted with decisions of the Fifth and Eighth District Courts of Appeals. <extracted-citation url="https://cite.case.law/ohio-st-3d/152/1404/" index="72" case-ids="12549477,12549478,12549482,12549483,12549461,12549463,12549465,12549470">152 Ohio St.3d 1404</extracted-citation>, <extracted-citation url="https://cite.case.law/ohio/2018/723/" index="73" case-ids="12549437,12549438,12549445,12549446,12549447,12549448,12549449,12549450,12549451,12549452,12549453,12549454,12549455,12549456,12549457,12549458,12549459,12549460,12549461,12549462,12549463,12549464,12549465,12549466,12549467,12549468,12549469,12549470,12549471,12549472,12549473,12549474,12549475,12549476,12549477,12549478,12549479,12549480,12549481,12549482,12549483,12549484,12549485,12549486,12549487,12549488,12549489,12549490,12549491,12549492,12549493,12549494,12549495,12549496,12549497,12549498,12549499,12549500,12549501,12549502,12549503,12549504,12549505,12549506,12549507,12549508,12549509,12549510,12549511,12549512,12549513,12549514,12549515,12549516,12549517,12549518,12549519,12549520,12549521,12549522,12549523,12549524,12549525,12549526,12549527,12549528,12549529,12549530,12549531,12549532,12549533,12549534,12549535,12549536,12549537,12549538,12549539,12549540,12549541,12549542,12549543,12549544,12549545,12549546,12549547,12549548,12549549,12549550,12549551,12549552,12549553,12549554,12549555,12549556,12549557,12549558,12549559,12549560,12549561,12549562,12549563,12549564,12549565,12549566,12549567,12549568,12549569,12549570,12549571,12549572,12549573,12549574,12549575,12549576,12549577,12549578,12549579,12549580,12549581,12549582,12549583,12549584,12549585,12549586,12549587,12549588,12549589,12549590,12549591,12549592,12549593,12549594,12549595,12549596,12549597,12549598,12549599,12549600,12549601,12549602">2018-Ohio-723</extracted-citation>, <extracted-citation url="https://cite.case.law/ne3d/92/877/" index="74" case-ids="12549472,12549473,12549474,12549475,12549476,12549477,12549478,12549479,12549480,12549481,12549482,12549483,12549484,12549470,12549471">92 N.E.3d 877</extracted-citation>.</p>\n    <p id="p-73"><strong>{\u00b6 37}</strong> The sole issue presented in this case is whether Crim.R. 11(C)(2)(a) requires a trial court accepting a guilty plea to a felony to inform the accused of a possible judicial sanction that could be imposed pursuant to R.C. 2929.141(A) for a violation of the terms of postrelease control.</p>\n    <p id="p-74"><strong>Law and Analysis</strong></p>\n    <p id="p-75"><strong>{\u00b6 38}</strong> Crim.R. 11(C)(2) provides:</p>\n    <blockquote id="p-76">In felony cases the court may refuse to accept a plea of guilty or a plea of no contest, and shall not accept a plea of guilty or no contest without first addressing the defendant personally and doing all of the following:</blockquote>\n    <blockquote id="p-77"><a id="p166" href="#p166" data-label="166" data-citation-index="1" class="page-label">*166</a>(a) Determining that the defendant is making the plea voluntarily, with understanding of the nature of the charges and of the maximum penalty involved, and if applicable, that the defendant is not eligible for probation or for the imposition of community control sanctions at the sentencing hearing.</blockquote>\n    <p id="p-78"><strong>{\u00b6 39}</strong> "To interpret court rules, this court applies general principles of statutory construction. * * * Therefore, we must read undefined words or phrases in context and then construe them according to rules of grammar and common usage." <em>State ex rel. Law Office of Montgomery Cty. Pub. Defender v. Rosencrans</em> , <extracted-citation url="https://cite.case.law/ohio-st-3d/111/338/" index="75" case-ids="3760710">111 Ohio St.3d 338</extracted-citation>, <extracted-citation url="https://cite.case.law/citations/?q=2006-Ohio-5793" index="76">2006-Ohio-5793</extracted-citation>, <extracted-citation url="https://cite.case.law/citations/?q=856%20N.E.2d%20250" index="77">856 N.E.2d 250</extracted-citation>, \u00b6 23. We must give effect to the words used in the rule, refraining from inserting or deleting words. <em>Cleveland Elec. Illum. Co. v. Cleveland</em> , <extracted-citation url="https://cite.case.law/ohio-st-3d/37/50/#p53" index="78" case-ids="1831057">37 Ohio St.3d 50</extracted-citation>, 53, <extracted-citation url="https://cite.case.law/citations/?q=524%20N.E.2d%20441" index="79">524 N.E.2d 441</extracted-citation> (1988). If the language of a rule is plain and unambiguous and conveys a clear and definite meaning, then there is no need for this court to resort to the rules of interpretation; rather, we apply the rule as written. <em>State ex rel. Potts v. Comm. on Continuing Legal Edn.</em> , <extracted-citation url="https://cite.case.law/ohio-st-3d/93/452/#p456" index="80" case-ids="248653">93 Ohio St.3d 452</extracted-citation>, 456, <extracted-citation url="https://cite.case.law/citations/?q=755%20N.E.2d%20886" index="81">755 N.E.2d 886</extracted-citation> (2001).</p>\n    <p id="p-79"><a id="p776" href="#p776" data-label="776" data-citation-index="1" class="page-label">*776</a><strong>{\u00b6 40}</strong> The language of Crim.R. 11(C)(2)(a) is plain and unambiguous. Crim.R. 11(C)(2)(a) requires a trial court accepting a guilty "plea" from a defendant to ensure that the defendant understands the "charges" and the "maximum penalty involved." The words "plea," "charges," and "maximum penalty" are not defined in either the Criminal Rules or the Revised Code, but they have common, everyday meanings that we can apply.</p>\n    <p id="p-80"><strong>{\u00b6 41}</strong> A "plea" is "[a]n accused person\'s response of \'guilty,\' \'not guilty,\' or \'no contest\' to a criminal charge." <em>Black\'s Law Dictionary</em> 1337 (10th Ed.2014). A "charge" is "[a] formal accusation of an offense as a preliminary step to prosecution." <em>Id.</em> at 282. The term "maximum penalty" refers to "[t]he heaviest punishment permitted by law." <em>Id.</em> at 1314.</p>\n    <p id="p-81"><strong>{\u00b6 42}</strong> Accordingly, the plea is the defendant\'s response to a charge filed alleging an offense, and the maximum penalty is the heaviest punishment prescribed by statute for that offense. Crim.R. 11(C)(2)(a) therefore requires the trial court to advise the defendant of the maximum penalty for each of the charges that the accused is resolving with the plea. Here, that means that the trial court was required to inform Bishop that he could be sentenced to up to 12 months in prison and a $2,500 fine for possession of heroin, and the trial court did that in the plea colloquy.</p>\n    <p id="p-82"><strong>{\u00b6 43}</strong> Our decision in <em>State v. Johnson</em> , <extracted-citation url="https://cite.case.law/ohio-st-3d/40/130/" index="82" case-ids="1415447">40 Ohio St.3d 130</extracted-citation>, <extracted-citation url="https://cite.case.law/citations/?q=532%20N.E.2d%201295" index="83">532 N.E.2d 1295</extracted-citation> (1988), supports this plain reading of the rule. In that case, the accused had agreed to plead guilty to aggravated robbery, robbery, and forgery, and in its plea colloquy, the trial court informed him of the maximum possible penalty for each individual charge without advising him that the court had authority to run <a id="p167" href="#p167" data-label="167" data-citation-index="1" class="page-label">*167</a>the sentences consecutively. <em>Id.</em> at 130-131, <extracted-citation url="https://cite.case.law/citations/?q=532%20N.E.2d%201295" index="84">532 N.E.2d 1295</extracted-citation>. The accused pleaded guilty, the court accepted the pleas, and it imposed consecutive sentences. <em>Id.</em> at 131, <extracted-citation url="https://cite.case.law/citations/?q=532%20N.E.2d%201295" index="85">532 N.E.2d 1295</extracted-citation>. The Second District Court of Appeals reversed and invalidated the pleas, holding that the trial court had failed to advise the accused "as to the maximum sentence possible for such violations because the trial court failed to inform him that the sentences may be imposed to run consecutively, rather than concurrently." <em>Id.</em> at 131-132, <extracted-citation url="https://cite.case.law/citations/?q=532%20N.E.2d%201295" index="86">532 N.E.2d 1295</extracted-citation>.</p>\n    <p id="p-83"><strong>{\u00b6 44}</strong> We reversed, concluding that the trial court\'s application of Crim.R. 11(C)(2)(a) was not prejudicial error. <em>Id.</em> at 134-135, <extracted-citation url="https://cite.case.law/citations/?q=532%20N.E.2d%201295" index="87">532 N.E.2d 1295</extracted-citation>. We explained:</p>\n    <blockquote id="p-84">Upon its face the rule speaks in the singular. The term "the charge" indicates a single and individual criminal charge. So, too, does "the plea" refer to "a plea" which the court "shall not accept" until the dictates of the rule have been observed. Consequently, the term "the maximum penalty" which is required to be explained is also to be understood as referring to a single penalty. <em>In the context of "the plea" to "the charge," the reasonable interpretation of the text is that "the maximum penalty" is for the single crime for which "the plea" is offered.</em> It would seem to be beyond a reasonable interpretation to suggest that the rule refers cumulatively to the total of all sentences received for all charges which a criminal defendant may answer in a single proceeding.</blockquote>\n    <p id="p-85">(Emphasis added.) <em>Id.</em> at 133, <extracted-citation url="https://cite.case.law/citations/?q=532%20N.E.2d%201295" index="88">532 N.E.2d 1295</extracted-citation>.</p>\n    <p id="p-86"><strong>{\u00b6 45}</strong> We further reasoned that</p>\n    <blockquote id="p-87"><a id="p777" href="#p777" data-label="777" data-citation-index="1" class="page-label">*777</a>Crim.R. 11 applies only to the entry and acceptance of the plea. It has no relevance to the exercise of the trial court\'s sentencing discretion at that stage other than directing the court to proceed with or impose sentencing. Thus, it can hardly be said that the rule <em>imposes upon a trial judge a duty to explain what particular matters he may, at a later date, determine are significant to the exercise of his discretion.</em> Moreover, explaining definitions of basic terms and calculating potential sentences are matters which are within the purview of legal representation, and of which even minimally competent trial counsel are capable.</blockquote>\n    <p id="p-88">(Emphasis added.) <em>Id.</em> at 134, <extracted-citation url="https://cite.case.law/citations/?q=532%20N.E.2d%201295" index="89">532 N.E.2d 1295</extracted-citation>.</p>\n    <p id="p-89"><strong>{\u00b6 46}</strong> <em>Johnson</em> therefore stands for the proposition that the trial court is required to inform the accused of the maximum penalty for each charged offense that will be resolved by the plea.</p>\n    <p id="p-90"><a id="p168" href="#p168" data-label="168" data-citation-index="1" class="page-label">*168</a><strong>{\u00b6 47}</strong> The lead opinion correctly notes that since we decided <em>Johnson</em> , Crim.R. 11(C)(2)(a) has been amended to require the trial court to ensure that the accused understands the nature of the "charges" and the maximum penalty involved. However, we amended the rule in 1998-almost a decade after we decided <em>Johnson</em> -"in light of changes in terminology used in the criminal law of Ohio effective July 1, 1996," by Am.Sub.S.B. No. 2, 146 Ohio Laws, Part IV, 7136 ("S.B. 2"), and the staff comment to the amendment does not indicate that making the word "charge" plural was intended to be a substantive change. 83 Ohio St.3d xciii, cxi. We do not make significant revisions to our procedural rules cryptically, and we have never held that our holding in <em>Johnson</em> has been abrogated by the amended rule. Ohio appellate courts continue to follow <em>Johnson</em> and hold that Crim.R. 11(C)(2)(a) does not require the trial court to advise a defendant during a plea colloquy of the possibility of consecutive sentencing. <em>E.g.</em> , <em>State v. Dansby-East</em> , <extracted-citation url="https://cite.case.law/citations/?q=2016-Ohio-202" index="90">2016-Ohio-202</extracted-citation>, <extracted-citation url="https://cite.case.law/citations/?q=57%20N.E.3d%20450" index="91">57 N.E.3d 450</extracted-citation>, \u00b6 16-17 (8th Dist.) ; <em>State v. Gabel</em> , 6th Dist. Sandusky Nos. S-14-038, S-14-042, S-14-043, and S-14-045, <extracted-citation url="https://cite.case.law/citations/?q=2015-Ohio-2803" index="92">2015-Ohio-2803</extracted-citation>, <extracted-citation url="https://cite.case.law/citations/?q=2015%20WL%204171049" index="93">2015 WL 4171049</extracted-citation>, \u00b6 13-14 ; <em>State v. Mack</em> , 1st Dist. Hamilton No. C-140054, <extracted-citation url="https://cite.case.law/citations/?q=2015-Ohio-1430" index="94">2015-Ohio-1430</extracted-citation>, <extracted-citation url="https://cite.case.law/citations/?q=2015%20WL%201737552" index="95">2015 WL 1737552</extracted-citation>, \u00b6 25.</p>\n    <p id="p-91"><strong>{\u00b6 48}</strong> Importantly, the judicial sanction authorized by R.C. 2929.141 was not enacted by the General Assembly until 2002, so it could not have been contemplated by the amendment to Crim.R. 11(C)(2)(a). <em>See</em> Am.Sub.H.B. No. 327, 149 Ohio Laws IV, 7536, 7576, 7626. But in any case, the amendment does not support the lead opinion\'s conclusion that the trial court is required to inform the defendant about penalties that may result from the guilty plea but that are not part of the "maximum penalty involved" for the "charges" resolved by the "plea." Simply put, there is no "charge" brought for a violation of the terms of postrelease control, because the General Assembly has not made a postrelease-control violation a separate crime as it has, for example, in criminalizing the violation of a protective order. <em>See</em> R.C. 2919.27. This conclusion is dictated by an understanding of how postrelease control works.</p>\n    <p id="p-92"><strong>{\u00b6 49}</strong> In 1996, the General Assembly enacted the postrelease-control statute as part of a comprehensive revision of Ohio\'s criminal sentencing scheme, S.B. 2, and its companion bill, Am.Sub.S.B. No. 269, 146 Ohio Laws, Part VI, 10752 ("S.B. 269"). As we explained in <em>Woods v. Telb</em> , <extracted-citation url="https://cite.case.law/ohio-st-3d/89/504/#p508" index="96" case-ids="1090900">89 Ohio St.3d 504</extracted-citation>, 508, <extracted-citation url="https://cite.case.law/citations/?q=733%20N.E.2d%201103" index="97">733 N.E.2d 1103</extracted-citation> (2000), our first decision to address the postrelease-control <a id="p778" href="#p778" data-label="778" data-citation-index="1" class="page-label">*778</a>statute, S.B. 2 and S.B. 269 "chang[ed] the landscape of Ohio\'s sentencing system" to provide "truth in sentencing," primarily accomplished by eliminating both indefinite sentences and parole and replacing them with definite sentences and postrelease control. The legislature removed the Adult Parole Board\'s authority to determine how long an offender stays in prison and instead provided that offenders are subject to mandatory and discretionary terms of postrelease control that commence upon release from imprisonment. <a id="p169" href="#p169" data-label="169" data-citation-index="1" class="page-label">*169</a><strong>{\u00b6 50}</strong> Postrelease control is a "period of supervision by the adult parole authority after a prisoner\'s release from imprisonment that includes one or more post-release control sanctions imposed under section 2967.28 of the Revised Code." R.C. 2967.01(N). The parole board has authority to impose "conditions of release under a post-release control sanction that the board or court considers appropriate, and the conditions of release may include [a] community residential sanction, community nonresidential sanction, or financial sanction." R.C. 2967.28(D)(1).</p>\n    <p id="p-93"><strong>{\u00b6 51}</strong> An offender who is released on postrelease control is under the general jurisdiction of the Adult Parole Authority and supervised by parole officers as if the offender had been placed on parole. R.C. 2967.28(F)(1). If the Adult Parole Authority determines that an offender has violated a condition of postrelease control, it may impose a more restrictive condition (but not a residential sanction that includes a prison term) or it may refer the matter for a hearing before the parole board, which has the authority to impose a prison term for a postrelease-control violation. R.C. 2967.28(F)(2) and (3). Importantly, courts are not involved in determining whether a violation occurred or what the sanction should be. The sanction, even if a prison term, is administratively imposed.</p>\n    <p id="p-94"><strong>{\u00b6 52}</strong> However, if an offender violates the terms of postrelease control by committing a new felony, upon the conviction or plea of guilty for that offense, the court <em>may</em> terminate postrelease control and impose either community-control sanctions or a prison term for the postrelease-control violation for the greater of 12 months or the time remaining to be served on postrelease control. R.C. 2929.141(A). <em>If</em> a prison term is imposed, it is to be served consecutively to the sentence for the new felony but must be reduced by any prison term administratively imposed by the parole board. R.C. 2929.141(A)(1).</p>\n    <p id="p-95"><strong>{\u00b6 53}</strong> R.C. 2929.141(A)(1) expressly distinguishes between the penalty imposed for a new felony and the sanction imposed for a postrelease-control violation, stating that the court may impose a prison term for the postrelease-control violation "[i]n addition to any prison term for the new felony." Our decisions have long recognized this distinction as well.</p>\n    <p id="p-96"><strong>{\u00b6 54}</strong> In <em>Woods</em> , we rejected the argument that permitting the Adult Parole Board to impose postrelease control on offenders violated the separation-of-powers doctrine by allowing the executive branch to exercise judicial authority, exactly because "post-release control is part of the original judicially imposed sentence" and because postrelease-control sanctions are "aimed at behavior modification in the attempt to reintegrate the offender safely into the community, not mere punishment for an additional crime." <extracted-citation url="https://cite.case.law/ohio-st-3d/89/504/#p508" index="98" case-ids="1090900">89 Ohio St.3d at 512</extracted-citation>, <extracted-citation url="https://cite.case.law/citations/?q=733%20N.E.2d%201103" index="99">733 N.E.2d 1103</extracted-citation>.</p>\n    <p id="p-97"><a id="p170" href="#p170" data-label="170" data-citation-index="1" class="page-label">*170</a><strong>{\u00b6 55}</strong> Similarly, in <em>State v. Martello</em> , we held that it does not offend the double-jeopardy protections of the Ohio and United States Constitutions to prosecute an offender who was sanctioned for violating the terms of postrelease control for the same conduct that was the reason for the sanction.</p>\n    <p id="p-98"><a id="p779" href="#p779" data-label="779" data-citation-index="1" class="page-label">*779</a><extracted-citation url="https://cite.case.law/ohio-st-3d/97/398/" index="100" case-ids="1133298">97 Ohio St.3d 398</extracted-citation>, <extracted-citation url="https://cite.case.law/citations/?q=2002-Ohio-6661" index="101">2002-Ohio-6661</extracted-citation>, <extracted-citation url="https://cite.case.law/citations/?q=780%20N.E.2d%20250" index="102">780 N.E.2d 250</extracted-citation>, \u00b6 1. We explained that "the General Assembly has indicated its clear intent that the prison term imposed for the violation of postrelease control is a reinstatement of part of the original sentence for violating the conditions of supervision, and is not meant to be a separate criminal punishment." <em>Id.</em> at \u00b6 19. We continued: "[J]eopardy does not attach when a defendant receives a term of incarceration for the violation of conditions of postrelease control. Such a term of incarceration is attributable to the original sentence and is not a \'criminal punishment\' for Double Jeopardy Clause purposes * * *." <em>Id.</em> at \u00b6 26.</p>\n    <p id="p-99"><strong>{\u00b6 56}</strong> Accordingly, as the statutory scheme demonstrates, a violation of the terms of postrelease control is not separately charged when the accused commits a new felony, and it is not part of the charge resolved by the accused\'s guilty plea resolving the new felony charged in the case. Nor is any sanction imposed for the postrelease-control violation part of the "maximum penalty involved," because it is not part of a new sentence that may be imposed for a new felony but, rather, is part of the original sentence that imposed postrelease control.</p>\n    <p id="p-100"><strong>{\u00b6 57}</strong> Nonetheless, the lead opinion reasons that a prison term imposed pursuant to R.C. 2929.141(A) "cannot stand alone" and is "inextricably intertwined" with the sentence imposed for the new felony that constitutes the postrelease-control violation. Lead opinion at \u00b6 17. It is unclear whether the justices joining the lead opinion view the postrelease-control violation as a "charge" or whether they view the judicial sanction imposed as part of the maximum penalty involved. But either way, the lead opinion\'s analysis cannot be squared with the language of the postrelease-control statute or our decisions recognizing that a sanction for a postrelease-control violation is not punishment for the commission of a new offense. It is true that the postrelease-control violation is connected to the new felony, but that is only because the guilty plea or conviction is the form of proof that the General Assembly has specified for showing that an offender violated the terms of his or her postrelease control by committing a felony. Standing alone, that does not make the violation any part of the charge resolved by the plea or make the sanction any part of the punishment for the conviction.</p>\n    <p id="p-101"><strong>{\u00b6 58}</strong> And as the lead opinion notes, at the time of the plea, there was only a "potential R.C. 2929.141(A) sentence." <em>Id.</em> at \u00b6 17. This language implies that a Crim.R. 11(C)(2)(a) advisement is required for any "possible" or "potential" sanction that may be imposed as a collateral consequence of pleading guilty to a <a id="p171" href="#p171" data-label="171" data-citation-index="1" class="page-label">*171</a>felony. But as we recognized in <em>Johnson</em> , Crim.R. 11(C)(2)(a) simply does not impose any duty on the trial court to inform the accused about its sentencing discretion. <extracted-citation url="https://cite.case.law/ohio-st-3d/40/130/" index="103" case-ids="1415447">40 Ohio St.3d at 134</extracted-citation>, <extracted-citation url="https://cite.case.law/citations/?q=532%20N.E.2d%201295" index="104">532 N.E.2d 1295</extracted-citation>. Rather, calculating potential sentences and informing the accused of the collateral consequences of a conviction are matters within the purview of legal representation. It is incumbent on defense counsel to know that the client committed a felony while on postrelease control, and an accused\'s guilty plea to an offense without knowing the legal consequences that may result might raise an issue of ineffective assistance of counsel but is not invalid.</p>\n    <p id="p-102"><strong>{\u00b6 59}</strong> Lastly, the lead opinion fails to appreciate the logical consequences of this court\'s judgment today. Its reasoning applies equally to an offender who violates community-control sanctions by committing a new offense. Although Crim.R. 11(C)(2) does not apply to community-control revocation proceedings, <em>e.g.</em> , <a id="p780" href="#p780" data-label="780" data-citation-index="1" class="page-label">*780</a><em>State v. Mayle</em> , <extracted-citation url="https://cite.case.law/ne3d/101/490/" index="105" case-ids="12533253">2017-Ohio-8942</extracted-citation>, <extracted-citation url="https://cite.case.law/ne3d/101/490/" index="106" case-ids="12533253">101 N.E.3d 490</extracted-citation>, \u00b6 13-14 (11th Dist.) (citing cases), the possible imposition of a sentence for an offender\'s violation of the terms of his or her probation is "inextricably intertwined" with the commission of the new offense that constitutes the probation violation. Following the lead opinion\'s logic, the trial court\'s failure to advise the defendant that a probation violation could result in the imposition of a sentence served consecutively to the sentence for the new offense would likewise be a complete failure to comply with Crim.R. 11(C)(2)(a), invalidating the plea. <em>See generally</em> R.C. 2929.25(A)(3)(c) ; <em>State v. Jones</em> , <extracted-citation url="https://cite.case.law/citations/?q=2017-Ohio-943" index="107">2017-Ohio-943</extracted-citation>, <extracted-citation url="https://cite.case.law/citations/?q=86%20N.E.3d%20821" index="108">86 N.E.3d 821</extracted-citation>, \u00b6 19 (7th Dist.) (upholding consecutive sentences for multiple probation violations). We have never interpreted Crim.R. 11(C)(2)(a) in this manner, and we should not do so today.</p>\n    <p id="p-103"><strong>{\u00b6 60}</strong> More fundamentally, for more than a decade, we have grappled with case after case addressing the consequences of a trial court\'s failure to properly impose postrelease control, debating whether the resulting sentence is void or voidable. <em>See</em> <em>State v. Jordan</em> , <extracted-citation url="https://cite.case.law/ohio-st-3d/104/21/" index="109" case-ids="430528">104 Ohio St.3d 21</extracted-citation>, <extracted-citation url="https://cite.case.law/citations/?q=2004-Ohio-6085" index="110">2004-Ohio-6085</extracted-citation>, <extracted-citation url="https://cite.case.law/citations/?q=817%20N.E.2d%20864" index="111">817 N.E.2d 864</extracted-citation>, \u00b6 23 (holding that a trial court\'s failure to properly impose a statutorily mandated term of postrelease control renders the sentence contrary to law and void); <em>State v. Bezak</em> , <extracted-citation url="https://cite.case.law/ohio-st-3d/114/94/" index="112" case-ids="3615756">114 Ohio St.3d 94</extracted-citation>, <extracted-citation url="https://cite.case.law/citations/?q=2007-Ohio-3250" index="113">2007-Ohio-3250</extracted-citation>, <extracted-citation url="https://cite.case.law/citations/?q=868%20N.E.2d%20961" index="114">868 N.E.2d 961</extracted-citation>, \u00b6 12-13 (explaining that a void sentence is a nullity and a de novo sentencing hearing therefore is required to correct it); <em>State v. Fischer</em> , <extracted-citation url="https://cite.case.law/ohio-st-3d/128/92/" index="115" case-ids="5758933">128 Ohio St.3d 92</extracted-citation>, <extracted-citation url="https://cite.case.law/citations/?q=2010-Ohio-6238" index="116">2010-Ohio-6238</extracted-citation>, <extracted-citation url="https://cite.case.law/citations/?q=942%20N.E.2d%20332" index="117">942 N.E.2d 332</extracted-citation>, \u00b6 17, 36 (overruling <em>Bezak</em> , holding that the improper imposition of postrelease control does not affect the valid parts of the conviction and sentence, and stating that resentencing is limited to properly imposing postrelease control); <em>State v. Billiter</em> , <extracted-citation url="https://cite.case.law/ohio-st-3d/134/103/" index="118" case-ids="4124175">134 Ohio St.3d 103</extracted-citation>, <extracted-citation url="https://cite.case.law/citations/?q=2012-Ohio-5144" index="119">2012-Ohio-5144</extracted-citation>, <extracted-citation url="https://cite.case.law/citations/?q=980%20N.E.2d%20960" index="120">980 N.E.2d 960</extracted-citation>, \u00b6 12 (allowing an offender to challenge an escape conviction by collaterally attacking the imposition of postrelease control); <em>State v. Gordon</em> , <extracted-citation url="https://cite.case.law/ne3d/109/1201/" index="121" case-ids="12537476">153 Ohio St.3d 601</extracted-citation>, <extracted-citation url="https://cite.case.law/ne3d/109/1201/" index="122" case-ids="12537476">2018-Ohio-1975</extracted-citation>, <extracted-citation url="https://cite.case.law/ne3d/109/1201/" index="123" case-ids="12537476">109 N.E.3d 1201</extracted-citation>, \u00b6 12 ( R.C. 2929.19(B)(2)(e) does not require the trial <a id="p172" href="#p172" data-label="172" data-citation-index="1" class="page-label">*172</a>court at sentencing to advise an offender of the judicial sanction authorized by R.C. 2929.141(A) for committing a new felony while on postrelease control).</p>\n    <p id="p-104"><strong>{\u00b6 61}</strong> This court\'s judgment today sparks a new debate by creating a new form of postrelease-control error on par with these cases. Courts of this state have held that a guilty plea that was not knowing, intelligent, and voluntary was obtained in violation of due process and is "void." <em>E.g.</em> , <em>State v. Gheen</em> , 7th Dist. Belmont No. 17 BE 0023, <extracted-citation url="https://cite.case.law/citations/?q=2018-Ohio-1924" index="124">2018-Ohio-1924</extracted-citation>, <extracted-citation url="https://cite.case.law/citations/?q=2018%20WL%202246947" index="125">2018 WL 2246947</extracted-citation>, \u00b6 9, citing <em>Boykin v. Alabama</em> , <extracted-citation url="https://cite.case.law/us/395/238/#p243" index="126" case-ids="1771759">395 U.S. 238</extracted-citation>, 243, <extracted-citation url="https://cite.case.law/us/395/238/#p243" index="127" case-ids="1771759">89 S.Ct. 1709</extracted-citation>, <extracted-citation url="https://cite.case.law/us/395/238/#p243" index="128" case-ids="1771759">23 L.Ed.2d 274</extracted-citation> (1969) ; <em>State v. Miller</em> , 8th Dist. Cuyahoga No. 102848, <extracted-citation url="https://cite.case.law/citations/?q=2015-Ohio-4688" index="129">2015-Ohio-4688</extracted-citation>, <extracted-citation url="https://cite.case.law/citations/?q=2015%20WL%207078619" index="130">2015 WL 7078619</extracted-citation>, \u00b6 5 ; <em>State v. Davis</em> , 2d Dist. Montgomery No. 24927, <extracted-citation url="https://cite.case.law/citations/?q=2012-Ohio-4745" index="131">2012-Ohio-4745</extracted-citation>, <extracted-citation url="https://cite.case.law/citations/?q=2012%20WL%204846991" index="132">2012 WL 4846991</extracted-citation>, \u00b6 4. According to the lead opinion, an accused\'s plea is not knowing, intelligent, and voluntary if he or she is not informed that the trial court can impose a prison term for a violation of the terms of postrelease control when the accused pleads guilty to a felony that also constitutes the violation. Such a plea is presumed invalid, and no showing of prejudice is needed; that is, it is void.</p>\n    <p id="p-105"><strong>{\u00b6 62}</strong> However, during the plea hearing, the trial court generally will not know that an offender was on postrelease control at the time of the offense unless the offender or defense counsel volunteers that information; for example, that fact does not appear in this record until the filing of the presentence-investigation report. But if the court does not know that the R.C. 2929.141(A) judicial sanction is in play, it has no reason to give the advisement required by this court\'s judgment today. The <a id="p781" href="#p781" data-label="781" data-citation-index="1" class="page-label">*781</a>court\'s judgment therefore brings the validity of countless guilty pleas into question, regardless of whether the accused was prejudiced by any error. It also gives defendants a perverse incentive to conceal the fact that they were on postrelease control when they committed their new offense so that they may "wait and see" what sentence is imposed and then raise the issue like a rabbit from the hat in the court of appeals. Our decisions should not countenance such gamesmanship, but this court\'s judgment today makes that a winning strategy.</p>\n    <p id="p-106"><strong>Conclusion</strong></p>\n    <p id="p-107"><strong>{\u00b6 63}</strong> The General Assembly has enacted a clear-cut statutory scheme of supervision of offenders reentering society after a term of incarceration. It made policy choices by providing that a violation of postrelease control is not a crime and by granting trial courts discretion in deciding whether to impose a prison term as a sanction for that violation. Rather than second-guessing these policy choices in the guise of interpreting a court rule, we should leave the policymaking to the General Assembly, the sole arbiter of public policy.</p>\n    <p id="p-108"><strong>{\u00b6 64}</strong> Crim.R. 11(C)(2)(a) advisements were never intended for nonexistent criminal offenses that cannot be charged or for a potential penalty that cannot be known at the time of a plea. Rather, the trial court\'s duty in accepting a guilty <a id="p173" href="#p173" data-label="173" data-citation-index="1" class="page-label">*173</a>plea is to ensure that the accused understands the nature of the charges to be resolved by the plea and the maximum penalty that may be imposed on each of those charges. Because a violation of the terms of postrelease control is not a new charge and because the judicial sanction imposed for the violation is not a punishment imposed on the guilty plea to an offense, Bishop\'s plea hearing complied with Crim.R. 11(C)(2)(a).</p>\n    <p id="p-109"><strong>{\u00b6 65}</strong> For these reasons, I would answer the certified question in the negative and reverse the judgment of the Second District Court of Appeals.</p>\n  </opinion>\n  <opinion type="dissent">\n    <author id="p-110">Fischer, J., dissenting.</author>\n    <p id="p-111"><strong>{\u00b6 66}</strong> I respectfully dissent. When a defendant pleads guilty to a new felony offense while on postrelease control for a prior felony, Crim.R. 11(C)(2)(a) does not require a trial court to advise that defendant at the plea hearing for the new felony offense of the court\'s sentencing discretion under R.C. 2929.141(A) to terminate the defendant\'s existing postrelease control and impose a consecutive prison sentence for the postrelease-control violation.</p>\n    <p id="p-112"><strong>I. This case is not moot</strong></p>\n    <p id="p-113"><strong>{\u00b6 67}</strong> As the lead opinion notes, there is nothing in the record before this court to show that after the court of appeals\' remand of the case, appellee, Dustin Bishop, entered a new guilty plea to possession of heroin and that the trial court accepted this new guilty plea and resentenced him. Because the record before us indicates that there is a live controversy, this case is not moot.</p>\n    <p id="p-114"><strong>{\u00b6 68}</strong> Moreover, despite the analysis set forth in the lead opinion and as the opinion concurring in judgment only explains, we need not consider this court\'s ability to address moot questions of law; even if the trial court had accepted a guilty plea and resentenced Bishop pursuant to the appellate court\'s remand of the case, our precedent is clear that the trial court lacked jurisdiction to do so. <em>See</em> <em>State v. Washington</em> , <extracted-citation url="https://cite.case.law/ohio-st-3d/137/427/" index="133" case-ids="3852447">137 Ohio St.3d 427</extracted-citation>, <extracted-citation url="https://cite.case.law/citations/?q=2013-Ohio-4982" index="134">2013-Ohio-4982</extracted-citation>, <extracted-citation url="https://cite.case.law/citations/?q=999%20N.E.2d%20661" index="135">999 N.E.2d 661</extracted-citation>, \u00b6 8. Neither party challenges our decision in <em>Washington</em> ; <em>Washington</em> remains good law. Any purported resentencing after the state had perfected its appeal could not, therefore, render the certified-conflict question before this court moot.</p>\n    <p id="p-115"><a id="p782" href="#p782" data-label="782" data-citation-index="1" class="page-label">*782</a><strong>II. Crim.R. 11(C)(2)(a) does not require advisement of a trial court\'s R.C. 2929.141(A) discretionary authority</strong></p>\n    <p id="p-116"><strong>{\u00b6 69}</strong> The lead opinion contains the conclusion that "[b]y any fair reading of Crim.R. 11(C)(2), the potential R.C. 2929.141(A) sentence was part of the \'maximum penalty involved\' in this case." Lead opinion at \u00b6 17. This conclusion is not supported by our caselaw interpreting the language of Crim.R. 11(C)(2)(a).</p>\n    <p id="p-117"><strong>{\u00b6 70}</strong> " Crim.R. 11(C) governs the process that a trial court must use before accepting a felony plea of guilty * * *."</p>\n    <p id="p-118"><a id="p174" href="#p174" data-label="174" data-citation-index="1" class="page-label">*174</a><em>State v. Veney</em> , <extracted-citation url="https://cite.case.law/ohio-st-3d/120/176/" index="136" case-ids="3695059">120 Ohio St.3d 176</extracted-citation>, <extracted-citation url="https://cite.case.law/citations/?q=2008-Ohio-5200" index="137">2008-Ohio-5200</extracted-citation>, <extracted-citation url="https://cite.case.law/citations/?q=897%20N.E.2d%20621" index="138">897 N.E.2d 621</extracted-citation>, \u00b6 8. Pursuant to Crim.R. 11(C)(2)(a), a trial court shall not accept a plea of guilty without</p>\n    <blockquote id="p-119">[d]etermining that the defendant is making <em>the plea</em> voluntarily, with understanding of the nature of the charges and of <em>the maximum penalty involved</em> , and if applicable, that the defendant is not eligible for probation or for the imposition of community control sanctions at the sentencing hearing.</blockquote>\n    <p id="p-120">(Emphasis added.)</p>\n    <p id="p-121"><strong>{\u00b6 71}</strong> Crim.R. 11(C)(2)(a) sets out distinct concepts. <em>See</em> <em>State v. Jones</em> , <extracted-citation url="https://cite.case.law/ohio-st-3d/116/211/" index="139" case-ids="5569431">116 Ohio St.3d 211</extracted-citation>, <extracted-citation url="https://cite.case.law/citations/?q=2007-Ohio-6093" index="140">2007-Ohio-6093</extracted-citation>, <extracted-citation url="https://cite.case.law/citations/?q=877%20N.E.2d%20677" index="141">877 N.E.2d 677</extracted-citation>, \u00b6 22. One of these distinct concepts is that the trial court must inform the defendant who is pleading guilty of "the maximum penalty involved."</p>\n    <p id="p-122"><strong>{\u00b6 72}</strong> This court, in an opinion that analyzed a prior version of Crim.R. 11(C)(2)(a), determined that " \'the maximum penalty\' " is the penalty "for the single crime for which \'the plea\' is offered." <em>State v. Johnson</em> , <extracted-citation url="https://cite.case.law/ohio-st-3d/40/130/" index="142" case-ids="1415447">40 Ohio St.3d 130</extracted-citation>, 133, <extracted-citation url="https://cite.case.law/citations/?q=532%20N.E.2d%201295" index="143">532 N.E.2d 1295</extracted-citation> (1988), quoting former Crim.R. 11(C)(2)(a), 46 Ohio St.2d xxxi, xxxii (effective July 1, 1976). The lead opinion distinguishes this court\'s analysis in <em>Johnson</em> on the bases that Crim.R. 11(C)(2)(a) has since been amended to allow for a single plea to apply to multiple charges and that the facts in <em>Johnson</em> are dissimilar to the facts in this case.</p>\n    <p id="p-123"><strong>{\u00b6 73}</strong> While we did interpret a prior version of Crim.R. 11(C)(2)(a) in <em>Johnson</em> , the plain language of the rule still demonstrates that " Crim.R. 11 applies only to the entry and acceptance of the plea," <em>Johnson</em> at 134, <extracted-citation url="https://cite.case.law/citations/?q=532%20N.E.2d%201295" index="144">532 N.E.2d 1295</extracted-citation>, and that "the reasonable interpretation of the text is that \'the maximum penalty\' is for the <em>single crime</em> [now "crimes"] for which \'the plea\' is offered" (emphasis added), <em>id.</em> at 133, <extracted-citation url="https://cite.case.law/citations/?q=532%20N.E.2d%201295" index="145">532 N.E.2d 1295</extracted-citation>. In <em>Johnson</em> , the specific facts of the case had no bearing on this court\'s interpretation of the language of former Crim.R. 11(C)(2)(a). The court reviewed the plain language of former Crim.R. 11(C)(2)(a) and determined that "the maximum penalty involved" means the penalty for the "crime" for which "the plea" was offered, not that "the maximum penalty involved" means any and all possible future consequences of the plea.</p>\n    <p id="p-124"><strong>{\u00b6 74}</strong> A plea of guilty is a complete admission of the defendant\'s guilt of the offense or offenses to which the plea is entered. Crim.R. 11(B)(1). As used in the Revised Code, the term "offenses" includes "aggravated murder, murder, felonies of the first, second, third, fourth, and fifth degree, misdemeanors of the first, second, third, and fourth degree, minor misdemeanors, and offenses not specifically classified." R.C. 2901.02(A). Thus, a guilty plea is entered to a charged offense, and "the maximum penalty involved" is the maximum penalty for that <a id="p175" href="#p175" data-label="175" data-citation-index="1" class="page-label">*175</a>offense to which the defendant pleads guilty and not additional or collateral possible punishments that are an indirect consequence of the guilty plea. <a id="p783" href="#p783" data-label="783" data-citation-index="1" class="page-label">*783</a><strong>{\u00b6 75}</strong> The judicial sanction that the trial court could impose for a defendant\'s violation of the terms of his or her postrelease control is not a part of the penalty for the offense to which the plea is entered; instead, it is a potential sanction for the defendant\'s postrelease-control violation. The defendant\'s existing postrelease control is a part of his or her <em>prior</em> felony sentence, <em>see</em> <em>Woods v. Telb</em> , <extracted-citation url="https://cite.case.law/ohio-st-3d/89/504/#p508" index="146" case-ids="1090900">89 Ohio St.3d 504</extracted-citation>, 512, <extracted-citation url="https://cite.case.law/citations/?q=733%20N.E.2d%201103" index="147">733 N.E.2d 1103</extracted-citation> (2000) ; <em>State v. Qualls</em> , <extracted-citation url="https://cite.case.law/ohio-st-3d/131/499/" index="148" case-ids="4116933">131 Ohio St.3d 499</extracted-citation>, <extracted-citation url="https://cite.case.law/citations/?q=2012-Ohio-1111" index="149">2012-Ohio-1111</extracted-citation>, <extracted-citation url="https://cite.case.law/citations/?q=967%20N.E.2d%20718" index="150">967 N.E.2d 718</extracted-citation>, \u00b6 34 (Lanzinger, J., dissenting), not the sentence for the offense to which the defendant is later pleading guilty. Therefore, a defendant\'s punishment for violating the terms of postrelease control, a part of the defendant\'s prior sentence, cannot be considered a part of "the maximum penalty involved" for the criminal offense to which the current plea is entered.</p>\n    <p id="p-125"><strong>{\u00b6 76}</strong> This conclusion is supported by the language of R.C. 2929.141(A). That statute specifically provides that "[u]pon * * * [a] plea of guilty to a felony by a person on post-release control at the time of the commission of the felony, the court may terminate the term of post-release control, and the court may * * * impose a prison term <em>for the post-release control violation</em> ." (Emphasis added.) R.C. 2929.141(A)(1). The General Assembly made it clear that the judicial sanction permitted under R.C. 2929.141(A) is <em>not</em> imposed for the offense but may be imposed for the violation of the terms of the defendant\'s existing postrelease control. It is our duty to give effect to the words used in the statute, not to insert or delete words. <em>Cline v. Bur. of Motor Vehicles</em> , <extracted-citation url="https://cite.case.law/ohio-st-3d/61/93/#p97" index="151" case-ids="1708874">61 Ohio St.3d 93</extracted-citation>, 97, <extracted-citation url="https://cite.case.law/citations/?q=573%20N.E.2d%2077" index="152">573 N.E.2d 77</extracted-citation> (1991). Thus, pursuant to the plain language of R.C. 2929.141(A), the penalty for violating the terms of postrelease control cannot also be considered "the maximum penalty involved" for the new offense to which the plea is entered.</p>\n    <p id="p-126"><strong>{\u00b6 77}</strong> The lead opinion would expand this court\'s interpretation of "the maximum penalty involved" to include a judicial sanction that may be imposed for the defendant\'s violation of the terms of his or her existing postrelease control by committing a felony offense. That conclusion is reached by relying on the proposition that "the sentence for committing a new felony while on postrelease control and that for the new felony itself [are] inextricably intertwined." Lead opinion at \u00b6 17. The trial court\'s discretionary sentencing authority should have no bearing on this court\'s interpretation of Crim.R. 11(C), which governs strictly what occurs at a plea hearing. The implicit definition of "the maximum penalty involved" that is found in Crim.R. 11(C) has not changed since we decided <em>Johnson</em> , and as we stated in that case, Crim.R. 11(C) "has no relevance to the exercise of the trial court\'s sentencing discretion at [the plea hearing] other than directing the court to proceed with or impose sentencing," <extracted-citation url="https://cite.case.law/ohio-st-3d/40/130/" index="153" case-ids="1415447">40 Ohio St.3d at 134</extracted-citation>, <extracted-citation url="https://cite.case.law/citations/?q=532%20N.E.2d%201295" index="154">532 N.E.2d 1295</extracted-citation>. The effect of the lead opinion would be to make "the maximum <a id="p176" href="#p176" data-label="176" data-citation-index="1" class="page-label">*176</a>penalty involved" include the speculative consequences of the plea in addition to the penalty for the charged offense to which the defendant is pleading guilty. Pursuant to R.C. 2929.141(A)(1), the trial court may "impose a prison term for the post-release control violation" that "shall be the greater of twelve months or the period of post-release control for the earlier felony minus any time the person has spent under the post-release control for the earlier felony." The trial court, without knowing the terms of the defendant\'s prior felony sentence, specifically the terms of the defendant\'s existing postrelease control, will not be able to inform the defendant of "the maximum penalty involved." At best, under the lead opinion\'s interpretation <a id="p784" href="#p784" data-label="784" data-citation-index="1" class="page-label">*784</a>of Crim.R. 11(C)(2)(a), the trial court would be able to inform the defendant of an indeterminate range-from one year to a period of time that is equal to the time left on the defendant\'s postrelease-control term, whatever that might be-that the court may impose, at its discretion, that could be added to the defendant\'s sentence for the postrelease-control violation. The lead opinion would add confusion to "the maximum penalty involved" and would leave the defendant to speculate as to "the maximum penalty" that he or she would receive for pleading guilty to the felony offense. Crim.R. 11(C)(2)(a), the statutes governing postrelease control, and our caselaw simply do not support the lead opinion\'s conclusion in this case.</p>\n    <p id="p-127"><strong>{\u00b6 78}</strong> In order for the trial court to accept a guilty plea to a charge of possession of heroin in violation of R.C. 2925.11(A), the court must inform the defendant of "the maximum penalty involved" when a defendant is convicted of possession of heroin. The following exchange occurred at Bishop\'s plea hearing:</p>\n    <blockquote id="p-128">THE COURT: The charge you\'re pleading guilty to is classified as a felony of the fifth degree. With that classification, the maximum penalty in terms of incarceration is 12 months in prison. Do you understand that?</blockquote>\n    <blockquote id="p-129">THE DEFENDANT: Yes.</blockquote>\n    <blockquote id="p-130">THE COURT: The maximum penalty in terms of a fine is $2,500. Do you understand that?</blockquote>\n    <blockquote id="p-131">THE DEFENDANT: Yes.</blockquote>\n    <p id="p-132">(Capitalization sic.) The trial court informed Bishop of "the maximum penalty involved" for a possession-of-heroin offense.</p>\n    <p id="p-133"><strong>{\u00b6 79}</strong> I would hold that the trial court complied with Crim.R. 11(C)(2)(a) by notifying Bishop of "the maximum penalty involved" for his possession-of-heroin offense. Crim.R. 11(C)(2)(a) did not require that the court inform Bishop of its discretionary authority under R.C. 2929.141(A) to sentence him to a consecutive <a id="p177" href="#p177" data-label="177" data-citation-index="1" class="page-label">*177</a>term of incarceration for violating the terms of the postrelease control that was imposed as a part of his prior felony conviction.</p>\n    <p id="p-134"><strong>III. When does the trial court need to inform a defendant of its R.C. 2929.141(A) discretionary authority?</strong></p>\n    <p id="p-135"><strong>{\u00b6 80}</strong> One potential criticism of determining that the term "the maximum penalty involved" used in Crim.R. 11(C)(2)(a) does not include the potential penalty that may be imposed by the trial court under R.C. 2929.141(A) is that the defendant may not be made aware of such possible consequence. This criticism, however, is speculative.</p>\n    <p id="p-136"><strong>{\u00b6 81}</strong> This court has previously held, applying the plain language of R.C. 2929.19(B)(2)(e), that "the statute does not require that a trial court notify an offender at his initial sentencing hearing of the penalty provisions contained in R.C. 2929.141(A)(1) and (2) (provisions that apply only when an offender is convicted of committing a new felony while serving a period of postrelease control)." <em>State v. Gordon</em> , <extracted-citation url="https://cite.case.law/ne3d/109/1201/" index="155" case-ids="12537476">153 Ohio St.3d 601</extracted-citation>, <extracted-citation url="https://cite.case.law/ne3d/109/1201/" index="156" case-ids="12537476">2018-Ohio-1975</extracted-citation>, <extracted-citation url="https://cite.case.law/ne3d/109/1201/" index="157" case-ids="12537476">109 N.E.3d 1201</extracted-citation>, \u00b6 2 ; <em>see also</em> <em>State v. Grimes</em> , <extracted-citation url="https://cite.case.law/ohio-st-3d/151/19/" index="158" case-ids="12280189">151 Ohio St.3d 19</extracted-citation>, <extracted-citation url="https://cite.case.law/ohio-st-3d/151/19/" index="159" case-ids="12280189">2017-Ohio-2927</extracted-citation>, <extracted-citation url="https://cite.case.law/citations/?q=85%20N.E.3d%20700" index="160">85 N.E.3d 700</extracted-citation>, \u00b6 19 (holding that a trial court need not identify in a sentencing entry the judicial sanctions that may be imposed for violating the terms of postrelease control).</p>\n    <p id="p-137"><strong>{\u00b6 82}</strong> In this case, Bishop asserts that his plea was not knowingly, intelligently, and voluntarily made because the trial court did not advise him of its discretionary authority under R.C. 2929.141(A) to revoke his existing postrelease control and <a id="p785" href="#p785" data-label="785" data-citation-index="1" class="page-label">*785</a>impose a consecutive prison term for violating the terms of his postrelease control. Bishop argues that Crim.R. 11(C)(2)(a) requires that advisement as a part of "the maximum penalty involved" and that the trial court\'s failure to follow Crim.R. 11 violated his federal due-process rights. As explained above, Crim.R. 11(C)(2)(a) does not require such an advisement; therefore, the trial court did not violate Bishop\'s due-process rights when it did not advise him of its R.C. 2929.141(A) authority.</p>\n    <p id="p-138"><strong>{\u00b6 83}</strong> The parties did <em>not</em> raise on appeal whether any statute, constitutional guarantee, or rule other than Crim.R. 11(C)(2)(a) independently requires that a defendant pleading guilty to a felony be informed at his or her initial sentencing hearing or in the sentencing entry in which the trial court imposes his or her postrelease control of the trial court\'s ability to later revoke that postrelease control and impose a consecutive prison term when the defendant is convicted of or pleads guilty to a new felony offense. Nor did the parties raise whether any other rule, statute, or constitutional guarantee requires that the defendant be provided such information at any other time.</p>\n    <p id="p-139"><strong>{\u00b6 84}</strong> A defendant is not foreclosed from raising other arguments-statutory, rule-based, or constitutional-to attack the validity of a judicial sanction imposed <a id="p178" href="#p178" data-label="178" data-citation-index="1" class="page-label">*178</a>pursuant to R.C. 2929.141(A) when that defendant feels that the information provided prior to the judicial sanction being imposed was insufficient. In my opinion, however, a defendant cannot successfully base such a challenge on the language of Crim.R. 11(C)(2)(a), which is the only issue at bar.</p>\n    <p id="p-140"><strong>IV. The new requirement proposed by the lead opinion under Crim.R. 11(C)(2)(a) would place an unreasonable burden on trial courts</strong></p>\n    <p id="p-141"><strong>{\u00b6 85}</strong> The new requirement proposed by the lead opinion under Crim.R. 11(C)(2)(a) would place an unreasonable burden on trial courts in many cases. I foresee multiple problems that this requirement would create for trial courts attempting to comply with Crim.R. 11(C)(2)(a), and this new requirement might allow certain defendants to abuse the system.</p>\n    <p id="p-142"><strong>{\u00b6 86}</strong> As a result of the new requirement proposed in the lead opinion, if a trial court failed to inform a defendant of a potential and speculative judicial sanction, the defendant\'s guilty plea would not be valid. The lead opinion does not include an explanation of what would happen when the court is not aware of the defendant\'s existing postrelease control. In many cases, the judicial sanction will not be imposed by the judge that sentenced the defendant to postrelease control in that defendant\'s prior felony case; indeed, the prior felony conviction may not even have been entered in the same jurisdiction. <em>See</em> <em>State v. Hicks</em> , 5th Dist. Delaware No. 09CAA090088, <extracted-citation url="https://cite.case.law/citations/?q=2010-Ohio-2985" index="161">2010-Ohio-2985</extracted-citation>, <extracted-citation url="https://cite.case.law/citations/?q=2010%20WL%202595153" index="162">2010 WL 2595153</extracted-citation>, \u00b6 9 ; <em>State v. Dixon</em> , 5th Dist. Stark No. 2008CA00254, <extracted-citation url="https://cite.case.law/citations/?q=2009-Ohio-3137" index="163">2009-Ohio-3137</extracted-citation>, <extracted-citation url="https://cite.case.law/citations/?q=2009%20WL%201835006" index="164">2009 WL 1835006</extracted-citation>, \u00b6 20.</p>\n    <p id="p-143"><strong>{\u00b6 87}</strong> Moreover, as noted above, pursuant to R.C. 2929.141(A)(1), the trial court may "impose a prison term for the post-release control violation" that "shall be the greater of twelve months or the period of post-release control for the earlier felony minus any time the person has spent under the post-release control for the earlier felony." Thus, to comply with the requirement proposed in the lead opinion, the trial court would need to inform the defendant of the maximum penalty involved, but the trial court would have to know not only that the defendant was serving a period of postrelease control but also know the details of the underlying felony conviction <a id="p786" href="#p786" data-label="786" data-citation-index="1" class="page-label">*786</a>and of the defendant\'s existing postrelease-control term.</p>\n    <p id="p-144"><strong>{\u00b6 88}</strong> This would place an unreasonable burden on the trial court to be aware of every defendant\'s existing postrelease control. The trial court is often not made aware of the defendant\'s existing postrelease control and prior felony convictions until after the plea hearing through a presentence-investigation report. <em>See</em> R.C. 2951.03 ; Crim.R. 32.2. Would trial courts now be required to do their own investigation prior to a guilty plea? Would prosecuting attorneys now be required to provide the trial court with the defendant\'s rap sheet prior to the plea? Or would it be the defendant\'s burden to provide such information, as the defendant is likely the only individual to know whether or not he or she is on postrelease <a id="p179" href="#p179" data-label="179" data-citation-index="1" class="page-label">*179</a>control? If it would be the defendant\'s burden to inform the trial court, then any error by the trial court would have been invited by the defendant. And what would happen if a defendant pleaded guilty at arraignment? Would trial courts be required to delay such a plea in order to conduct such an investigation?</p>\n    <p id="p-145"><strong>{\u00b6 89}</strong> Further, the practical reality of the position taken by the lead opinion is that it might allow for the potential abuse of our plea system. When a defendant, who is likely in the best position to inform the trial court that he or she is serving a period of postrelease control, fails to provide that information to the trial court, the court will not provide notice of "the maximum penalty involved." Moreover, if the defendant waives a presentence-investigation report, <em>see</em> R.C. 2951.03(A)(1), then that court might not revoke the defendant\'s postrelease control at sentencing at all. The practical implication of this court adopting the lead opinion\'s conclusion would be that the defendant then could successfully argue that his plea was not knowingly, intelligently, and voluntarily made simply based on an error that the defendant had invited. And the defendant would not be required to show prejudice because "the trial court completely failed to inform [the defendant] that a consecutive prison sentence under R.C. 2929.141(A) was possible," lead opinion at \u00b6 20, even though the lengthier sentence was not realistically possible because the trial court could not impose the lengthier sentence without having the information that the defendant withheld from the trial court. In that scenario, a defendant who had suffered no prejudice would get another bite at the apple simply because <em>that defendant</em> failed to provide to the trial court information related to the defendant\'s existing postrelease control.</p>\n    <p id="p-146"><strong>{\u00b6 90}</strong> The conclusion of the lead opinion would likely place an unreasonable burden on the trial court and might provide defendants who are on postrelease control with the opportunity to abuse the plea system.</p>\n    <p id="p-147"><strong>V. Conclusion</strong></p>\n    <p id="p-148"><strong>{\u00b6 91}</strong> I would reverse the judgment of the Second District Court of Appeals and hold that pursuant to Crim.R. 11(C)(2)(a), a trial court does not need to advise a criminal defendant on postrelease control for a prior felony, during a plea hearing in a new felony case, of the trial court\'s ability under R.C. 2929.141(A) to terminate the defendant\'s existing postrelease control and impose a consecutive prison sentence for the postrelease-control violation. Therefore, I respectfully dissent.</p>\n    <p id="p-149">Brown, J., concurs in the foregoing opinion.</p>\n  </opinion>\n</casebody>\n',
        #         "status": "ok",
        #     },
        # }
        #
        # clean_dictionary_3 = combine_non_overlapping_data(
        #     cluster_3.pk, case_3_data
        # )
        #
        # merge_judges(cluster_3.pk, "judges", clean_dictionary_3.get("judges"))
        #
        # cluster_3.refresh_from_db()
        #
        # # Test best option selected for judges is in harvard data
        # self.assertEqual(cluster_3.judges, "Fischer, French, Kennedy")

    # class HarvardMergerTests(TestCase):
    #     def setUp(self):
    #         """Setup harvard tests
    #
    #         This setup is a little distinct from normal ones.  Here we are actually
    #         setting up our patches which are used by the majority of the tests.
    #         Each one can be used or turned off.  See the teardown for more.
    #         :return:
    #         """
    #         self.read_json_patch = patch(
    #             "cl.corpus_importer.management.commands.harvard_merge.read_json"
    #         )
    #         self.read_json_func = self.read_json_patch.start()
    #
    #     def tearDown(self) -> None:
    #         """Tear down patches and remove added objects"""
    #         # Docket.objects.all().delete()
    #         self.read_json_patch.stop()
    #
    #     def test_merge_opinions(self):
    #         harvard_data = {
    #             "casebody": {
    #                 # "data": "<?xml version='1.0' encoding='utf-8'?>\n<casebody xmlns=\"http://nrs.harvard.edu/urn-3:HLS.Libr.US_Case_Law.Schema.Case_Body:v1\" xmlns:xlink=\"http://www.w3.org/1999/xlink\" firstpage=\"279\" lastpage=\"293\"><docketnumber id=\"b279-3\" pgmap=\"279\">(No. 23002.</docketnumber><parties id=\"b279-4\" pgmap=\"279\">Henry R. Levy, Appellant, vs. The Broadway-Carmen Building Corporation, Appellee.</parties><decisiondate id=\"b279-5\" pgmap=\"279\">Opinion filed April 16, 1937.</decisiondate><p id=\"b280-3\" pgmap=\"280\">Stone and Shaw, JJ., specially concurring.</p><p id=\"b280-4\" pgmap=\"280\">Herrick, C. J., and Orr, J., dissenting.</p><attorneys id=\"b280-6\" pgmap=\"280\">Isaac B. Lipson, (A. C. Lewis, of counsel,) for appellant.</attorneys><attorneys id=\"b280-7\" pgmap=\"280\">Kamfner, Halligan &amp; Marks, (Samuer M. Lanoff, of counsel,) for appellee.</attorneys> <opinion type=\"majority\"> <author id=\"b280-8\" pgmap=\"280\">Mr. Justice Farthing</author> <p id=\"ADb\" pgmap=\"280\">delivered the opinion of the court:</p> <p id=\"b280-9\" pgmap=\"280(154) 281(364) 282(102)\">Henry R. Levy, the appellant, was the principal stockholder in the Studebaker Sales Company of Chicago. In January, 1926, David Gordon purchased the land involved in this foreclosure, from that company. He paid $35,000, in cash, and secured the balance of $100,000 by a mortgage. He reduced the debt to $70,000, and on April 13, 1931, when this became due, an agreement was made between Gordon and Levy by which $5000 more was paid and the property was deeded to the Broadway-Carmen Building Corporation, organized by Gordon. That company gave its note for $65,000, guaranteed by Gordon and his wife, and secured by a trust deed on the premises. In addition, Levy received a commission of \" $1950. After $2500 had been paid on the principal debt, default was made by the mortgagor, and Levy obtained a judgment at law against David Gordon and Ida Gordon, his wife, for $66,691.87. He also brought this suit to foreclose the trust deed. The superior court of Cook county rendered a decree on May 26, 1933) and found that $70,246.91 was due Levy on March 15, 1933. It ordered the mortgaged premises sold at public auction and directed the master in chancery to carry out the decree. Accordingly the master, on June 21, 1933, struck off the property to appellant, Levy, for $50,000. The appellee filed objections to the report of sale. It claimed that the property was reasonably worth $80,000, but that economic conditions had resulted in the destruction of the market for real estate in Chicago. It prayed that the court establish the value of the premises and credit that value on the amount found due Levy; that a deficiency judgment be denied and that the judgment previously rendered against Gordon and wife, be satisfied in full. It stated that it had offered, and was then willing, to deed the property to the appellant in cancellation of the debt. Levy answered, denying that the premises were worth more than $50,000. He set up the fact that the property was being managed by a receiver and that the rental was $150 per month, plus $5 for each automobile sold by the lessee; that the 1929, 1930 and 1931 taxes, totalling $6000, were unpaid. He offered to assign his certificate of purchase to anyone for the amount of his bid. The mortgaged premises are located at a street intersection and are known as No. 5100 Broadway. The lot is 97 by 100 feet and is improved with a one-story automobile display building and service garage. Both parties introduced affidavits as to value. Those on behalf of appellee, set the value at $77,400 to $80,000, which was based on a rental value of $400 per month, and a possible rental of $500 if the building were divided into storerooms. The affidavits on behalf of appellant showed that the improvements cost $35,902 thirteen years before, and that their replacement value was $22,500. They showed the reasonable rental value to be $250 per month and fixed the value of the premises at from $40,000 to $50,000. At the close of the hearing on the objections, the chancellor ordered that the sale be approved, provided the appellant released and cancelled the outstanding judgment against the Gordons, and the mortgage indebtedness, but Levy refused to do this. A decree was entered on January 20, 1934, denying confirmation of the master’s report of sale. It ordered a re-sale of the property at an upset price of $71,508.45, the amount then due, together with interest and costs of suit. The Appellate Court for the First District affirmed this decree on appeal. We granted leave and the cause is here on appeal.</p> <p id=\"b282-3\" pgmap=\"282\">Appellant contends that the order of January 20, 1934, denying confirmation of the master’s report and directing a new sale at an upset price, was an amendment to the foreclosure decree dated May 26, 1933, and that the later decree was of no effect, because'it was rendered at a succeeding term. This contention cannot be upheld. It was the duty of the court to supervise the sale and to approve or reject the report. If rejected, it was the duty of the court to order a re-sale and directions as to time, place and terms of sale were but incidents to such order. Mariner v. Ingraham, 230 Ill. 130; L. R A. 1915A, 699.</p> <p id=\"b282-4\" pgmap=\"282(145) 283(382) 284(21)\">As stated by appellant the remaining points for consideration are embodied in the question: May the chancellor, in a suit to foreclose a real estate mortgage, require the plaintiff to waive his right to a deficiency decree as a condition precedent to confirming the master’s report of sale, or, in the alternative, may the chancellor fix a value ■ and direct the master not to accept a bid lower than this reserved or upset price? Appellant says a court of equity is without power to disapprove a master’s report of sale in a foreclosure suit, except there be mistake, fraud or some violation of duty by the purchaser or the master. He says that no matter how grossly inadequate the bid may be, it does not constitute fraud, or warrant the chancellor in disapproving the sale. No argument is required to disclose or sustain the wisdom of the rule that public policy and the interest of debtors require stability in judicial sales and that these sales should not be disturbed without cause. However, it is to be observed that the rule requiring more than mere inadequacy of price, and the showing of a breach of duty by the purchaser or the officer, or a fraud upon the debtor, arose out of cases where the judicial sales had been consummated and not out of mere offers to buy from a court. For example in Skakel v. Cycle Trade Publishing Co. 237 Ill. 482, the complainant brought his action to set aside a sheriff’s sale and a deed already executed. The cases of Mixer v. Sibley, 53 Ill. 61, Davis v. Pickett, 72 id. 483, O’Callaghan v. O’Callaghan, 91 id. 228, and Smith v. Huntoon, 134 id. 24, all involved sales under executions at law. Dobbins v. Wilson, 107 Ill. 17, concerned a deed issued following a United States marshal’s sale. Quigley v. Breckenridge, 180 Ill. 627, involved a sale made pursuant to a decree for partition, and although we held that the sale was fair and the master’s report.of sale should have been approved, nevertheless we re-affirmed the doctrine that a court of chancery possesses a large discretion in passing upon masters’ reports of sale. In that case we pointed out the fact that such a sale is not completed until it is confirmed, and that until then, it confers no right in the land upon the purchaser. The sale in Bondurant v. Bondurant, 251 Ill. 324, was made by a trustee who had power to sell the land at public vendue and was not a judicial sale in the legal sense. In the case of Allen v. Shepard, 87 Ill. 314, we exercised our judicial power to determine whether or not the bid made at an administrator’s sale was adequate, and determined that it was. In Clegg v. Christensen, 346 Ill. 314, we again exercised the same power. Abbott v. Beebe, 226 Ill. 417, concerned a partition sale. The land brought more than two-thirds of the appraised value. We again declared that there was power in the chancellor to set aside a judicial sale for inadequacy of price but we held that the- facts showed the sale under consideration was fairly made. The record did not disclose any inadequacy in the price.</p> <p id=\"b284-3\" pgmap=\"284\">In sales by conservators, guardians and trustees, involving consideration of objections filed before reports of sale were approved, inadequacy of price has always been considered in determining whether the sale was fairly made and whether the report should be approved and confirmed. In most of the cases the objector tendered a larger bid and very often the bid was required to be secured, but the fact that there ,was such an increased bid was, at most, evidence that the sale price was inadequate. In Kloepping v. Stellmacher, 21 N. J. Eq. 328, a sheriff sold property worth $2000 for $52. The owner was ignorant, stupid and perverse, and would not believe his property would be sold for so trifling an amount, although he had been forewarned. Redemption was allowed upon payment of the purchase price and costs. The court said: “But when such gross inadequacy is combined with fraud or mistake, or any other ground of relief in equity, it will incline the court strongly to afford relief. The sale in this case is a great oppression on the complainants. They are ignorant, stupid, perverse and poor. They lose by it all their property, and are ill fitted to acquire more. They are such as this court should incline to protect, notwithstanding perverseness.”</p> <p id=\"b284-4\" pgmap=\"284(111) 285(146)\">In Graffam v. Burgess, 117 U. S. 180, 29 L. ed. 839, the Supreme Court of the United States, speaking through Mr. Justice Bradley, said: “It was formerly the rule in England, in chancery sales, that until confirmation of the master’s report, the bidding would be opened upon a mere offer to advance the price 10 per centum. (2 Daniell, Ch. Pr. 1st ed. 924; 2d ed. by Perkins, 1465, 1467; Sugden, V. &amp; P. 14th ed. 114.) But Lord Eldon expressed much dissatisfaction with this practice of opening biddings upon a mere offer of an advanced price, as tending to diminish confidence in such sales, to keep bidders from attending, and to diminish the amount realized. (White v. Wilson, 14 Ves. 151; Williams v. Attleborough, Tur. &amp; Rus. 76; White v. Damon, 7 Ves. 34.) Lord Eldon’s views were finally adopted in England in the Sale of Land by Auction act, 1857, (30 and 31 Victoria, chap. 48, sec. 7,) so that now the highest bidder at a sale by auction of land, under an order of the court, provided he has'bid a sum equal to or higher than the reserved price (if any), will be declared and allowed the purchaser, unless the court or judge, on the ground of fraud or improper conduct in the management of the sale, upon the application of any person interested in the land, either opens the bidding or orders the property to be resold. 1 Sugden, V. &amp; P. 14th ed. by Perkins, 14 note (a).</p> <p id=\"b285-3\" pgmap=\"285\">“In this country Lord Eldon’s views were adopted at an early day by the courts; and the rule has become almost universal that a sale will not be set aside for inadequacy of price unless the inadequacy be so great as to shock the conscience, or unless there be additional circumstances against its fairness; being very much the rule that always prevailed in England as to setting aside sales after the master’s report had been confirmed. [Citing many cases.]</p> <p id=\"b285-4\" pgmap=\"285\">“From the cases here cited we may draw the general conclusion that if the inadequacy of price is so gross as to shock the conscience, or if in addition to gross inadequacy, the purchaser has been guilty of any unfairness, or has taken any undue advantage, or if the owner of the property, or party interested in it, has been for any other reason, misled or surprised, then the sale will be regarded as fraudulent and void, or the party injured will be permitted to redeem the property sold. Great inadequacy requires only slight circumstances of unfairness in the conduct of the party benefited by the sale to raise the presumption of fraud.”</p> <p id=\"b285-5\" pgmap=\"285(22) 286(22)\">In Pewabic Mining Co. v. Mason, 145 U. S. 349, 36 L. ed. 732, Mr. Justice Brewer said, at page 367: “Indeed even before confirmation the sale would not be set aside for mere inadequacy, unless so great as to shock the conscience.”</p> <p id=\"b286-3\" pgmap=\"286\">Stability must be given to judicial sales which have reached the point where title has vested in the purchaser, otherwise bidding would be discouraged. But where a bidder does not become vested with any interest in the land but has only made an offer to buy, subject to the approval of his offer by the court, and he bids with that condition, there can be no good reason why bidding would be discouraged by reason of the court’s power to approve or disapprove the sale for gross inadequacy of bid. Sales by masters are not sales in a legal sense, until they are confirmed. Until then, they are sales only in a popular sense. The accepted bidder acquires no independent right to'have his purchase completed, but remains only a preferred proposer until confirmation of the sale by the court, as agreed to by its ministerial agent. Confirmation is final consent, and the court, being in fact the vendor, may consent or not, in its discretion. (Hart v. Burch, 130 Ill. 426; Jennings v. Dunphy, 174 id. 86; Pewabic Mining Co. v. Mason, supra; Smith v. Arnold, 5 Mason, 414.) In the case last •cited, Mr. Justice Story said, at page 420: “In sales directed by the court of chancery, the whole business is transacted by a public officer, under the guidance and superintendence of the court itself. Even after the sale is made, it is not final until a report is made to the court and it is approved and confirmed.”</p> <p id=\"b286-4\" pgmap=\"286(86) 287(251)\">Many of the decisions, relied upon and cited by appel- . lant, arose out of sales of lands under mortgages or trust deeds which contained a power of sale and were made at a time when there was no redemption, unless it was provided for in the mortgage or trust deed. Such sales were not subject to approval or disapproval by courts and the only remedy the mortgagor had against fraud or other misconduct was by a bill in equity to set aside the conveyanee, or for redemption. In 1843 the legislature passed an act regulating the foreclosure of mortgages on real property which created a redemption period in favor of mortgagors, but the act did not purport to govern trust deeds containing a power of sale. Thereafter the foreclosing of mortgages was committed to courts of chancery, under their general equity powers, except as to certain prescribed matters of procedure. From that time mortgage foreclosure sales were made by an officer who was required to report the sale to the court. Purchasers did not become vested with any interest in the land sold until the report of sale was approved. The court fixed the terms and conditions of foreclosure sales and this practice still continues. In 1879 the legislature provided that no real estate should be sold by virtue of any power of sale contained in any mortgage, trust deed, or other conveyance in the nature of a mortgage, but that thereafter such real estate should be sold in the same manner provided for foreclosure of mortgages containing no power of sale, and then only in pursuance of a judgment or decree of a court of competent jurisdiction. (State Bar Stat. 1935, chap. 95, par. 24; 95 S. H. A. 23.) The history of this legislation is conclusive proof that it was the legislative intent that foreclosure sales should be made only upon such terms and conditions as were approved by the courts. Garrett v. Moss, 20 Ill. 549.</p> <p id=\"b287-3\" pgmap=\"287(103) 288(66)\">Unfairness from any cause which operates to the prejudice of an interested party, will abundantly justify a chancery court in refusing to approve a sale. We said in Roberts v. Goodin, 288 Ill. 561: “The setting aside of the sale and ordering the property re-sold was a matter which rested largely within the discretion of the chancellor, whose duty it was to see that the lien be enforced with the least damage possible to the property rights of the mortgagor. Counsel cite numerous cases touching their contention that the chancellor erred in setting aside this sale. The cases cited, however, arose after the sale had once been confirmed, and not where, as here, the objection to the confirmation of the sale was filed immediately after the sale and before any confirmation had taken place. The chancellor has a broad discretion in the matter of approving or disapproving a master’s sale made subject to the court’s approval by the terms of the decree.”</p> <p id=\"b288-3\" pgmap=\"288\">The legislature’s purpose would be defeated if any other interpretation were given to the statutes on the subject of mortgage foreclosure. It is unusual for land to bring its full, fair market value at a forced sale. While courts can not guarantee that mortgaged property will bring its full value, they can prevent unwarranted sacrifice of a debtor’s property. Mortgage creditors resort to courts of equity for relief and those courts prescribe equitable terms upon which they may receive that relief, and it is within their power to prevent creditors from taking undue and unconscionable advantage of debtors, under the guise of collecting a debt. A slight inadequacy is not sufficient reason to disapprove a master’s sale, but where the amount bid is so grossly inadequate that it shocks the conscience of a court of equity, it is the chancellor’s duty to disapprove the report of sale. Connely v. Rue, 148 Ill. 207; Kiebel v. Reick, 216 id. 474; Wilson v. Ford, 190 id. 614; Ballentyne v. Smith, 205 U. S. 285, 51 L. ed. 803.</p> <p id=\"b288-4\" pgmap=\"288(113) 289(137)\">The case of Slack v. Cooper, 219 Ill. 138, illustrates the rule. In that case the master sold the land, upon which the mortgage had been foreclosed, to the solicitor for the mortgagor for $3000. He acted under the mistaken impression that the buyer was the solicitor for the mortgagee, who appeared shortly thereafter and bid $7000. The master then announced publicly that since no cash had been deposited by the original bidder, and because of the misapprehension stated and his haste in making the sale, it would be re-opened for higher and better bids. At page 144 of that decision we said: “If the chancellor finds upon the coming in of the report of a master, that the sale as made is not to the best interest of all concerned and is inequitable, or that any fraud or misconduct has been practiced upon the master or the court or any irregularities in the proceedings, it is his duty to set aside the sale as made and order another sale of the premises. The chancellor has a broad discretion in passing upon the acts of the master and approving or disapproving his acts in reference to sales and entering his own decrees, (Quigley v. Breckenridge, 180 Ill. 627,) and his decree will not be disturbed by this court unless it is shown that he has abused his discretion and entered such an order or decree as would not seem equitable between the parties interested.”</p> <p id=\"b289-3\" pgmap=\"289\">We have limited our discussion to the power of a court of chancery to approve or disapprove a master’s report of sale in a foreclosure suit and we hold that the court has broad discretionary powers over such sales. Where it appears that the bid offered the court for the premises is so grossly inadequate that its acceptance amounts to a fraud, the court has the power to reject the bid and order a re-sale.</p> <p id=\"b289-4\" pgmap=\"289(159) 290(95)\">There is little or no difference between the equitable jurisdiction' and power in a chancery court to refuse approval to a report of sale on foreclosure, and the power to fix, in advance, a reserved or upset price, as a minimum at which the property may be sold. We have referred to the acts of 1843 and 1879 which require trust deeds and mortgages to be foreclosed in chancery courts and have pointed out that courts of equity, exercising their general equity powers in such cases, have the right to fix reasonable terms and conditions for the carrying out of the provisions of the foreclosure decree, and that such courts may order a new sale and set the old aside for the violation of some duty by the master, or for fraud or mistake. No reason appears why the chancellor cannot prevent a sale at a grossly inadequate price by fixing á reasonable sale price in advance. The same judicial power 'is involved in either action. What is necessary to be done in the end, to prevent fraud and injustice, may be forestalled by proper judicial action in the beginning. Such a course is not against the policy of the law in this State and it is not the equivalent of an appraisal statute. It is common practice in both the State and Federal courts to fix an upset price in mortgage foreclosure suits. This is in harmony with the accepted principles governing judicial power in mortgage foreclosures.</p> <p id=\"b290-3\" pgmap=\"290\">In First National Bank v. Bryn Mawr Beach Building Corp. 365 Ill. 409, we pointed out the fact that such property as was there under consideration seldom sells at anything like its reproduction cost, or even its fair cash market value, at a judicial sale. We recognized the fact that the equity powers of State courts are no more limited than those of Federal courts, and that equity jurisdiction over mortgage foreclosures is general rather than limited or statutory. In part we said: “It would seem that since equity courts have always exercised jurisdiction to decree the enforcement of mortgage liens and to supervise foreclosure sales, such jurisdiction need not expire merely because the questions or conditions surrounding the exercise of such time-honored functions are new or complicated. If it may reasonably be seen that the exercise of the jurisdiction of a court of equity beyond the sale of the property will result in better protection to parties before it, it would seem not only to be germane to matters of undisputed jurisdiction, but to make for the highest exercise of the court’s admitted functions.” We there held that a court of equity has jurisdiction, in connection with an application for approval of a foreclosure sale, to approve a re-organization plan submitted by a bondholders’ committee. The question is somewhat different from that presented in the case before us, but we there recognized the continuing vitality and growth of equity jurisprudence.</p> <p id=\"b291-2\" pgmap=\"291\">Cases wherein an upset price has been fixed are not confined to large properties for which, by reason of their great value, the market is limited or there is no market whatever. In McClintic-Marshall Co. v. Scandinavian-American Building Co. 296 Fed. 601, a building was constructed on two lots covered by the mortgage, and one lot belonging to the mortgagor that was not mortgaged. It was necessary, under the circumstances, to sell all the property, and to protect the mortgagor a reserved price was fixed. The fact of an upset price is referred to, although there was no objection to it being fixed, in Northern Pacific Railway Co. v. Boyd, 228 U. S. 482, 57 L. ed. 931, and Pewabic Mining Co. v. Mason, supra, and the power has been exercised in numerous other cases. 104 A. L. R. 375; 90 id. 1321; 88 id. 1481.</p> <p id=\"b291-3\" pgmap=\"291\">The appellant did not raise constitutional objections in the trial court and by appealing to the Appellate Court for the First District he would waive such questions. However, the fixing of an upset price does not violate section 10 of article 1 of the Federal constitution nor section 14 of article 2 of the Illinois constitution, which inhibit the impairment of the obligation of contracts. The reserved price dealt only with the remedy, and it was within the court’s power to establish it as one of the terms and conditions of the sale. The appellant was not deprived of his right to enforce the contract, and his remedy was neither denied, nor so embarrassed, as to seriously impair the value of his contract or the right to enforce it. Penniman’s Case, 103 U. S. 714, 26 L. ed. 502; Town of Cheney’s Grove v. Van Scoyoc, 357 Ill. 52.</p> <p id=\"b291-4\" pgmap=\"291\">It is contended that the present holding conflicts with what we said in Chicago Title and Trust Co. v. Robin, 361 Ill. 261. It was not necessary to that decision to pass upon the power to fix an upset price, and what we said on that subject is not adhered to.</p> <p id=\"b292-2\" pgmap=\"292\">Each case must be based upon its own facts, and from this record we are of the opinion that no such gross inadequacy existed in the bid of $50,000, as would warrant the chancellor in refusing approval of the master’s sale. Although the rents were pledged in the trust deed, they would amount to but little more than the taxes on the property of approximately $2000 per annum. Appellee’s affidavits base the estimate of value largely on the rental value of the premises. They were rented for $150 per month, plus $5 for each automobile sold by the lessee, and this amounted to a total of $200 a month. Even if the premises brought $400, or the $500 per month which appellee’s witnesses said could be had if the property was divided into storerooms, the cost of these changes is not given. This testimony did not warrant the chancellor in finding that these premises were worth $80,000. Although the property had been sold, before the panic, for $135,000, the value of real estate was greater then than at the time of the master’s sale. The proof did not sustain a greater value than $50,000 at the time of the sale, but if it be assumed- that this was somewhat inadequate, the fact that there was a depressed market for real estate would not be a sufficient circumstance, coupled with the supposed inadequacy in the bid, to warrant the chancellor in disapproving the master’s report of sale. The power to disapprove a sale for gross inadequacy of bid exists independent of an economic depression. The chancellor abused his discretion and erred in refusing to approve the sale at $50,000.</p> <p id=\"b292-3\" pgmap=\"292\">The judgment of the Appellate Court and the decree of the superior court are reversed and the cause is remanded to the superior court of Cook county, with directions to approve the master’s report of sale.</p> <p id=\"b292-4\" pgmap=\"292\">Reversed and remanded, with directions.</p> </opinion> <opinion type=\"concurrence\"> <author id=\"b292-5\" pgmap=\"292\">Stone and Shaw, JJ.,</author> <p id=\"ASY\" pgmap=\"292\">specially concurring:</p> <p id=\"b292-6\" pgmap=\"292\">We agree with the result reached but not in all that is said in the opinion.</p> </opinion> <opinion type=\"dissent\"> <author id=\"b293-2\" pgmap=\"293\">Mr. Chief Justice Herrick,</author> <p id=\"ADM\" pgmap=\"293\">dissenting:</p> <p id=\"b293-3\" pgmap=\"293\">I concur in the legal conclusion reached in the majority opinion that the chancellor had the power to fix an upset price for the sale of the property against which foreclosure was sought. He set the upset price on the re-sale order at $71,508.45. He found that the market value of the property was $80,000. The majority opinion shows that the hearing as to the value of the property was on affidavits. Those of appellant tended to establish a value of $40,000 to $50,000; those of appellee, from $77,400 to $80,000. The upset price established by the chancellor was clearly within the scope of the evidence. This court has consistently held on issues'involving the value of property, where the value was fixed by the verdict of a jury on conflicting evidence, that, in the absence of material error, this court would not disturb the finding of the jury where the amount determined was within the range of the evidence and not the result of passion and prejudice. (Department of Public Works v. Foreman Bank, 363 Ill. 13, 24.) In my opinion we should accord to the finding of the chancellor on the question of value the same credit we do to a verdict of a jury on that subject. The application of this rule to the instant cause would result in the affirmance of the decree. The judgment of the Appellate Court and the order of the superior court should each have been affirmed.</p> </opinion> <opinion type=\"dissent\"> <author id=\"b293-4\" pgmap=\"293\">Mr. Justice Orr,</author> <p id=\"AU6\" pgmap=\"293\">also dissenting:</p> <p id=\"b293-5\" pgmap=\"293\">I disagree with that portion of the opinion holding that a court of chancery, in a foreclosure case, has inherent power to fix an upset price to be bid at the sale. In my opinion, this court should adhere to the contrary rule laid down in Chicago Title and Trust Co. v. Robin, 361 Ill. 261.</p> </opinion> </casebody> ",
    #                 "data": '<?xml version=\'1.0\' encoding=\'utf-8\'?>\n<casebody xmlns="http://nrs.harvard.edu/urn-3:HLS.Libr.US_Case_Law.Schema.Case_Body:v1" xmlns:xlink="http://www.w3.org/1999/xlink" firstpage="279" lastpage="293"><docketnumber id="b279-3" pgmap="279">(No. 23002.</docketnumber><parties id="b279-4" pgmap="279">Henry R. Levy, Appellant, vs. The Broadway-Carmen Building Corporation, Appellee.</parties><decisiondate id="b279-5" pgmap="279">Opinion filed April 16, 1937.</decisiondate><p id="b280-3" pgmap="280">Stone and Shaw, JJ., specially concurring.</p><p id="b280-4" pgmap="280">Herrick, C. J., and Orr, J., dissenting.</p><attorneys id="b280-6" pgmap="280">Isaac B. Lipson, (A. C. Lewis, of counsel,) for appellant.</attorneys><attorneys id="b280-7" pgmap="280">Kamfner, Halligan &amp; Marks, (Samuer M. Lanoff, of counsel,) for appellee.</attorneys> <opinion type="majority"> <author id="b280-8" pgmap="280">Mr. Justice Farthing</author> <p id="ADb" pgmap="280">delivered the opinion of the court:</p> <p id="b280-9" pgmap="280(154) 281(364) 282(102)">Henry R. Levy, the appellant, was the principal stockholder in the Studebaker Sales Company of Chicago. In January, 1926, David Gordon purchased the land involved in this foreclosure, from that company. He paid $35,000, in cash, and secured the balance of $100,000 by a mortgage. He reduced the debt to $70,000, and on April 13, 1931, when this became due, an agreement was made between Gordon and Levy by which $5000 more was paid and the property was deeded to the Broadway-Carmen Building Corporation, organized by Gordon. That company gave its note for $65,000, guaranteed by Gordon and his wife, and secured by a trust deed on the premises. In addition, Levy received a commission of " $1950. After $2500 had been paid on the principal debt, default was made by the mortgagor, and Levy obtained a judgment at law against David Gordon and Ida Gordon, his wife, for $66,691.87. He also brought this suit to foreclose the trust deed. The superior court of Cook county rendered a decree on May 26, 1933) and found that $70,246.91 was due Levy on March 15, 1933. It ordered the mortgaged premises sold at public auction and directed the master in chancery to carry out the decree. Accordingly the master, on June 21, 1933, struck off the property to appellant, Levy, for $50,000. The appellee filed objections to the report of sale. It claimed that the property was reasonably worth $80,000, but that economic conditions had resulted in the destruction of the market for real estate in Chicago. It prayed that the court establish the value of the premises and credit that value on the amount found due Levy; that a deficiency judgment be denied and that the judgment previously rendered against Gordon and wife, be satisfied in full. It stated that it had offered, and was then willing, to deed the property to the appellant in cancellation of the debt. Levy answered, denying that the premises were worth more than $50,000. He set up the fact that the property was being managed by a receiver and that the rental was $150 per month, plus $5 for each automobile sold by the lessee; that the 1929, 1930 and 1931 taxes, totalling $6000, were unpaid. He offered to assign his certificate of purchase to anyone for the amount of his bid. The mortgaged premises are located at a street intersection and are known as No. 5100 Broadway. The lot is 97 by 100 feet and is improved with a one-story automobile display building and service garage. Both parties introduced affidavits as to value. Those on behalf of appellee, set the value at $77,400 to $80,000, which was based on a rental value of $400 per month, and a possible rental of $500 if the building were divided into storerooms. The affidavits on behalf of appellant showed that the improvements cost $35,902 thirteen years before, and that their replacement value was $22,500. They showed the reasonable rental value to be $250 per month and fixed the value of the premises at from $40,000 to $50,000. At the close of the hearing on the objections, the chancellor ordered that the sale be approved, provided the appellant released and cancelled the outstanding judgment against the Gordons, and the mortgage indebtedness, but Levy refused to do this. A decree was entered on January 20, 1934, denying confirmation of the master’s report of sale. It ordered a re-sale of the property at an upset price of $71,508.45, the amount then due, together with interest and costs of suit. The Appellate Court for the First District affirmed this decree on appeal. We granted leave and the cause is here on appeal.</p> <p id="b282-3" pgmap="282">Appellant contends that the order of January 20, 1934, denying confirmation of the master’s report and directing a new sale at an upset price, was an amendment to the foreclosure decree dated May 26, 1933, and that the later decree was of no effect, because\'it was rendered at a succeeding term. This contention cannot be upheld. It was the duty of the court to supervise the sale and to approve or reject the report. If rejected, it was the duty of the court to order a re-sale and directions as to time, place and terms of sale were but incidents to such order. Mariner v. Ingraham, 230 Ill. 130; L. R A. 1915A, 699.</p> <p id="b282-4" pgmap="282(145) 283(382) 284(21)">As stated by appellant the remaining points for consideration are embodied in the question: May the chancellor, in a suit to foreclose a real estate mortgage, require the plaintiff to waive his right to a deficiency decree as a condition precedent to confirming the master’s report of sale, or, in the alternative, may the chancellor fix a value ■ and direct the master not to accept a bid lower than this reserved or upset price? Appellant says a court of equity is without power to disapprove a master’s report of sale in a foreclosure suit, except there be mistake, fraud or some violation of duty by the purchaser or the master. He says that no matter how grossly inadequate the bid may be, it does not constitute fraud, or warrant the chancellor in disapproving the sale. No argument is required to disclose or sustain the wisdom of the rule that public policy and the interest of debtors require stability in judicial sales and that these sales should not be disturbed without cause. However, it is to be observed that the rule requiring more than mere inadequacy of price, and the showing of a breach of duty by the purchaser or the officer, or a fraud upon the debtor, arose out of cases where the judicial sales had been consummated and not out of mere offers to buy from a court. For example in Skakel v. Cycle Trade Publishing Co. 237 Ill. 482, the complainant brought his action to set aside a sheriff’s sale and a deed already executed. The cases of Mixer v. Sibley, 53 Ill. 61, Davis v. Pickett, 72 id. 483, O’Callaghan v. O’Callaghan, 91 id. 228, and Smith v. Huntoon, 134 id. 24, all involved sales under executions at law. Dobbins v. Wilson, 107 Ill. 17, concerned a deed issued following a United States marshal’s sale. Quigley v. Breckenridge, 180 Ill. 627, involved a sale made pursuant to a decree for partition, and although we held that the sale was fair and the master’s report.of sale should have been approved, nevertheless we re-affirmed the doctrine that a court of chancery possesses a large discretion in passing upon masters’ reports of sale. In that case we pointed out the fact that such a sale is not completed until it is confirmed, and that until then, it confers no right in the land upon the purchaser. The sale in Bondurant v. Bondurant, 251 Ill. 324, was made by a trustee who had power to sell the land at public vendue and was not a judicial sale in the legal sense. In the case of Allen v. Shepard, 87 Ill. 314, we exercised our judicial power to determine whether or not the bid made at an administrator’s sale was adequate, and determined that it was. In Clegg v. Christensen, 346 Ill. 314, we again exercised the same power. Abbott v. Beebe, 226 Ill. 417, concerned a partition sale. The land brought more than two-thirds of the appraised value. We again declared that there was power in the chancellor to set aside a judicial sale for inadequacy of price but we held that the- facts showed the sale under consideration was fairly made. The record did not disclose any inadequacy in the price.</p> <p id="b284-3" pgmap="284">In sales by conservators, guardians and trustees, involving consideration of objections filed before reports of sale were approved, inadequacy of price has always been considered in determining whether the sale was fairly made and whether the report should be approved and confirmed. In most of the cases the objector tendered a larger bid and very often the bid was required to be secured, but the fact that there ,was such an increased bid was, at most, evidence that the sale price was inadequate. In Kloepping v. Stellmacher, 21 N. J. Eq. 328, a sheriff sold property worth $2000 for $52. The owner was ignorant, stupid and perverse, and would not believe his property would be sold for so trifling an amount, although he had been forewarned. Redemption was allowed upon payment of the purchase price and costs. The court said: “But when such gross inadequacy is combined with fraud or mistake, or any other ground of relief in equity, it will incline the court strongly to afford relief. The sale in this case is a great oppression on the complainants. They are ignorant, stupid, perverse and poor. They lose by it all their property, and are ill fitted to acquire more. They are such as this court should incline to protect, notwithstanding perverseness.”</p> <p id="b284-4" pgmap="284(111) 285(146)">In Graffam v. Burgess, 117 U. S. 180, 29 L. ed. 839, the Supreme Court of the United States, speaking through Mr. Justice Bradley, said: “It was formerly the rule in England, in chancery sales, that until confirmation of the master’s report, the bidding would be opened upon a mere offer to advance the price 10 per centum. (2 Daniell, Ch. Pr. 1st ed. 924; 2d ed. by Perkins, 1465, 1467; Sugden, V. &amp; P. 14th ed. 114.) But Lord Eldon expressed much dissatisfaction with this practice of opening biddings upon a mere offer of an advanced price, as tending to diminish confidence in such sales, to keep bidders from attending, and to diminish the amount realized. (White v. Wilson, 14 Ves. 151; Williams v. Attleborough, Tur. &amp; Rus. 76; White v. Damon, 7 Ves. 34.) Lord Eldon’s views were finally adopted in England in the Sale of Land by Auction act, 1857, (30 and 31 Victoria, chap. 48, sec. 7,) so that now the highest bidder at a sale by auction of land, under an order of the court, provided he has\'bid a sum equal to or higher than the reserved price (if any), will be declared and allowed the purchaser, unless the court or judge, on the ground of fraud or improper conduct in the management of the sale, upon the application of any person interested in the land, either opens the bidding or orders the property to be resold. 1 Sugden, V. &amp; P. 14th ed. by Perkins, 14 note (a).</p> <p id="b285-3" pgmap="285">“In this country Lord Eldon’s views were adopted at an early day by the courts; and the rule has become almost universal that a sale will not be set aside for inadequacy of price unless the inadequacy be so great as to shock the conscience, or unless there be additional circumstances against its fairness; being very much the rule that always prevailed in England as to setting aside sales after the master’s report had been confirmed. [Citing many cases.]</p> <p id="b285-4" pgmap="285">“From the cases here cited we may draw the general conclusion that if the inadequacy of price is so gross as to shock the conscience, or if in addition to gross inadequacy, the purchaser has been guilty of any unfairness, or has taken any undue advantage, or if the owner of the property, or party interested in it, has been for any other reason, misled or surprised, then the sale will be regarded as fraudulent and void, or the party injured will be permitted to redeem the property sold. Great inadequacy requires only slight circumstances of unfairness in the conduct of the party benefited by the sale to raise the presumption of fraud.”</p> <p id="b285-5" pgmap="285(22) 286(22)">In Pewabic Mining Co. v. Mason, 145 U. S. 349, 36 L. ed. 732, Mr. Justice Brewer said, at page 367: “Indeed even before confirmation the sale would not be set aside for mere inadequacy, unless so great as to shock the conscience.”</p> <p id="b286-3" pgmap="286">Stability must be given to judicial sales which have reached the point where title has vested in the purchaser, otherwise bidding would be discouraged. But where a bidder does not become vested with any interest in the land but has only made an offer to buy, subject to the approval of his offer by the court, and he bids with that condition, there can be no good reason why bidding would be discouraged by reason of the court’s power to approve or disapprove the sale for gross inadequacy of bid. Sales by masters are not sales in a legal sense, until they are confirmed. Until then, they are sales only in a popular sense. The accepted bidder acquires no independent right to\'have his purchase completed, but remains only a preferred proposer until confirmation of the sale by the court, as agreed to by its ministerial agent. Confirmation is final consent, and the court, being in fact the vendor, may consent or not, in its discretion. (Hart v. Burch, 130 Ill. 426; Jennings v. Dunphy, 174 id. 86; Pewabic Mining Co. v. Mason, supra; Smith v. Arnold, 5 Mason, 414.) In the case last •cited, Mr. Justice Story said, at page 420: “In sales directed by the court of chancery, the whole business is transacted by a public officer, under the guidance and superintendence of the court itself. Even after the sale is made, it is not final until a report is made to the court and it is approved and confirmed.”</p> <p id="b286-4" pgmap="286(86) 287(251)">Many of the decisions, relied upon and cited by appel- . lant, arose out of sales of lands under mortgages or trust deeds which contained a power of sale and were made at a time when there was no redemption, unless it was provided for in the mortgage or trust deed. Such sales were not subject to approval or disapproval by courts and the only remedy the mortgagor had against fraud or other misconduct was by a bill in equity to set aside the conveyanee, or for redemption. In 1843 the legislature passed an act regulating the foreclosure of mortgages on real property which created a redemption period in favor of mortgagors, but the act did not purport to govern trust deeds containing a power of sale. Thereafter the foreclosing of mortgages was committed to courts of chancery, under their general equity powers, except as to certain prescribed matters of procedure. From that time mortgage foreclosure sales were made by an officer who was required to report the sale to the court. Purchasers did not become vested with any interest in the land sold until the report of sale was approved. The court fixed the terms and conditions of foreclosure sales and this practice still continues. In 1879 the legislature provided that no real estate should be sold by virtue of any power of sale contained in any mortgage, trust deed, or other conveyance in the nature of a mortgage, but that thereafter such real estate should be sold in the same manner provided for foreclosure of mortgages containing no power of sale, and then only in pursuance of a judgment or decree of a court of competent jurisdiction. (State Bar Stat. 1935, chap. 95, par. 24; 95 S. H. A. 23.) The history of this legislation is conclusive proof that it was the legislative intent that foreclosure sales should be made only upon such terms and conditions as were approved by the courts. Garrett v. Moss, 20 Ill. 549.</p> <p id="b287-3" pgmap="287(103) 288(66)">Unfairness from any cause which operates to the prejudice of an interested party, will abundantly justify a chancery court in refusing to approve a sale. We said in Roberts v. Goodin, 288 Ill. 561: “The setting aside of the sale and ordering the property re-sold was a matter which rested largely within the discretion of the chancellor, whose duty it was to see that the lien be enforced with the least damage possible to the property rights of the mortgagor. Counsel cite numerous cases touching their contention that the chancellor erred in setting aside this sale. The cases cited, however, arose after the sale had once been confirmed, and not where, as here, the objection to the confirmation of the sale was filed immediately after the sale and before any confirmation had taken place. The chancellor has a broad discretion in the matter of approving or disapproving a master’s sale made subject to the court’s approval by the terms of the decree.”</p> <p id="b288-3" pgmap="288">The legislature’s purpose would be defeated if any other interpretation were given to the statutes on the subject of mortgage foreclosure. It is unusual for land to bring its full, fair market value at a forced sale. While courts can not guarantee that mortgaged property will bring its full value, they can prevent unwarranted sacrifice of a debtor’s property. Mortgage creditors resort to courts of equity for relief and those courts prescribe equitable terms upon which they may receive that relief, and it is within their power to prevent creditors from taking undue and unconscionable advantage of debtors, under the guise of collecting a debt. A slight inadequacy is not sufficient reason to disapprove a master’s sale, but where the amount bid is so grossly inadequate that it shocks the conscience of a court of equity, it is the chancellor’s duty to disapprove the report of sale. Connely v. Rue, 148 Ill. 207; Kiebel v. Reick, 216 id. 474; Wilson v. Ford, 190 id. 614; Ballentyne v. Smith, 205 U. S. 285, 51 L. ed. 803.</p> <p id="b288-4" pgmap="288(113) 289(137)">The case of Slack v. Cooper, 219 Ill. 138, illustrates the rule. In that case the master sold the land, upon which the mortgage had been foreclosed, to the solicitor for the mortgagor for $3000. He acted under the mistaken impression that the buyer was the solicitor for the mortgagee, who appeared shortly thereafter and bid $7000. The master then announced publicly that since no cash had been deposited by the original bidder, and because of the misapprehension stated and his haste in making the sale, it would be re-opened for higher and better bids. At page 144 of that decision we said: “If the chancellor finds upon the coming in of the report of a master, that the sale as made is not to the best interest of all concerned and is inequitable, or that any fraud or misconduct has been practiced upon the master or the court or any irregularities in the proceedings, it is his duty to set aside the sale as made and order another sale of the premises. The chancellor has a broad discretion in passing upon the acts of the master and approving or disapproving his acts in reference to sales and entering his own decrees, (Quigley v. Breckenridge, 180 Ill. 627,) and his decree will not be disturbed by this court unless it is shown that he has abused his discretion and entered such an order or decree as would not seem equitable between the parties interested.”</p> <p id="b289-3" pgmap="289">We have limited our discussion to the power of a court of chancery to approve or disapprove a master’s report of sale in a foreclosure suit and we hold that the court has broad discretionary powers over such sales. Where it appears that the bid offered the court for the premises is so grossly inadequate that its acceptance amounts to a fraud, the court has the power to reject the bid and order a re-sale.</p> <p id="b289-4" pgmap="289(159) 290(95)">There is little or no difference between the equitable jurisdiction\' and power in a chancery court to refuse approval to a report of sale on foreclosure, and the power to fix, in advance, a reserved or upset price, as a minimum at which the property may be sold. We have referred to the acts of 1843 and 1879 which require trust deeds and mortgages to be foreclosed in chancery courts and have pointed out that courts of equity, exercising their general equity powers in such cases, have the right to fix reasonable terms and conditions for the carrying out of the provisions of the foreclosure decree, and that such courts may order a new sale and set the old aside for the violation of some duty by the master, or for fraud or mistake. No reason appears why the chancellor cannot prevent a sale at a grossly inadequate price by fixing á reasonable sale price in advance. The same judicial power \'is involved in either action. What is necessary to be done in the end, to prevent fraud and injustice, may be forestalled by proper judicial action in the beginning. Such a course is not against the policy of the law in this State and it is not the equivalent of an appraisal statute. It is common practice in both the State and Federal courts to fix an upset price in mortgage foreclosure suits. This is in harmony with the accepted principles governing judicial power in mortgage foreclosures.</p> <p id="b290-3" pgmap="290">In First National Bank v. Bryn Mawr Beach Building Corp. 365 Ill. 409, we pointed out the fact that such property as was there under consideration seldom sells at anything like its reproduction cost, or even its fair cash market value, at a judicial sale. We recognized the fact that the equity powers of State courts are no more limited than those of Federal courts, and that equity jurisdiction over mortgage foreclosures is general rather than limited or statutory. In part we said: “It would seem that since equity courts have always exercised jurisdiction to decree the enforcement of mortgage liens and to supervise foreclosure sales, such jurisdiction need not expire merely because the questions or conditions surrounding the exercise of such time-honored functions are new or complicated. If it may reasonably be seen that the exercise of the jurisdiction of a court of equity beyond the sale of the property will result in better protection to parties before it, it would seem not only to be germane to matters of undisputed jurisdiction, but to make for the highest exercise of the court’s admitted functions.” We there held that a court of equity has jurisdiction, in connection with an application for approval of a foreclosure sale, to approve a re-organization plan submitted by a bondholders’ committee. The question is somewhat different from that presented in the case before us, but we there recognized the continuing vitality and growth of equity jurisprudence.</p> <p id="b291-2" pgmap="291">Cases wherein an upset price has been fixed are not confined to large properties for which, by reason of their great value, the market is limited or there is no market whatever. In McClintic-Marshall Co. v. Scandinavian-American Building Co. 296 Fed. 601, a building was constructed on two lots covered by the mortgage, and one lot belonging to the mortgagor that was not mortgaged. It was necessary, under the circumstances, to sell all the property, and to protect the mortgagor a reserved price was fixed. The fact of an upset price is referred to, although there was no objection to it being fixed, in Northern Pacific Railway Co. v. Boyd, 228 U. S. 482, 57 L. ed. 931, and Pewabic Mining Co. v. Mason, supra, and the power has been exercised in numerous other cases. 104 A. L. R. 375; 90 id. 1321; 88 id. 1481.</p> <p id="b291-3" pgmap="291">The appellant did not raise constitutional objections in the trial court and by appealing to the Appellate Court for the First District he would waive such questions. However, the fixing of an upset price does not violate section 10 of article 1 of the Federal constitution nor section 14 of article 2 of the Illinois constitution, which inhibit the impairment of the obligation of contracts. The reserved price dealt only with the remedy, and it was within the court’s power to establish it as one of the terms and conditions of the sale. The appellant was not deprived of his right to enforce the contract, and his remedy was neither denied, nor so embarrassed, as to seriously impair the value of his contract or the right to enforce it. Penniman’s Case, 103 U. S. 714, 26 L. ed. 502; Town of Cheney’s Grove v. Van Scoyoc, 357 Ill. 52.</p> <p id="b291-4" pgmap="291">It is contended that the present holding conflicts with what we said in Chicago Title and Trust Co. v. Robin, 361 Ill. 261. It was not necessary to that decision to pass upon the power to fix an upset price, and what we said on that subject is not adhered to.</p> <p id="b292-2" pgmap="292">Each case must be based upon its own facts, and from this record we are of the opinion that no such gross inadequacy existed in the bid of $50,000, as would warrant the chancellor in refusing approval of the master’s sale. Although the rents were pledged in the trust deed, they would amount to but little more than the taxes on the property of approximately $2000 per annum. Appellee’s affidavits base the estimate of value largely on the rental value of the premises. They were rented for $150 per month, plus $5 for each automobile sold by the lessee, and this amounted to a total of $200 a month. Even if the premises brought $400, or the $500 per month which appellee’s witnesses said could be had if the property was divided into storerooms, the cost of these changes is not given. This testimony did not warrant the chancellor in finding that these premises were worth $80,000. Although the property had been sold, before the panic, for $135,000, the value of real estate was greater then than at the time of the master’s sale. The proof did not sustain a greater value than $50,000 at the time of the sale, but if it be assumed- that this was somewhat inadequate, the fact that there was a depressed market for real estate would not be a sufficient circumstance, coupled with the supposed inadequacy in the bid, to warrant the chancellor in disapproving the master’s report of sale. The power to disapprove a sale for gross inadequacy of bid exists independent of an economic depression. The chancellor abused his discretion and erred in refusing to approve the sale at $50,000.</p> <p id="b292-3" pgmap="292">The judgment of the Appellate Court and the decree of the superior court are reversed and the cause is remanded to the superior court of Cook county, with directions to approve the master’s report of sale.</p> <p id="b292-4" pgmap="292">Reversed and remanded, with directions.</p> </opinion> <opinion type="concurrence"> <author id="b292-5" pgmap="292">Stone and Shaw, JJ.,</author> <p id="ASY" pgmap="292">specially concurring:</p> <p id="b292-6" pgmap="292">We agree with the result reached but not in all that is said in the opinion.</p> </opinion> <opinion type="dissent"> <author id="b293-2" pgmap="293">Mr. Chief Justice Herrick,</author> <p id="ADM" pgmap="293">dissenting:</p> <p id="b293-3" pgmap="293">I concur in the legal conclusion reached in the majority opinion that the chancellor had the power to fix an upset price for the sale of the property against which foreclosure was sought. He set the upset price on the re-sale order at $71,508.45. He found that the market value of the property was $80,000. The majority opinion shows that the hearing as to the value of the property was on affidavits. Those of appellant tended to establish a value of $40,000 to $50,000; those of appellee, from $77,400 to $80,000. The upset price established by the chancellor was clearly within the scope of the evidence. This court has consistently held on issues\'involving the value of property, where the value was fixed by the verdict of a jury on conflicting evidence, that, in the absence of material error, this court would not disturb the finding of the jury where the amount determined was within the range of the evidence and not the result of passion and prejudice. (Department of Public Works v. Foreman Bank, 363 Ill. 13, 24.) In my opinion we should accord to the finding of the chancellor on the question of value the same credit we do to a verdict of a jury on that subject. The application of this rule to the instant cause would result in the affirmance of the decree. The judgment of the Appellate Court and the order of the superior court should each have been affirmed.</p> </opinion> <opinion type="dissent"> <author id="b293-4" pgmap="293">Mr. Justice Orr,</author> <p id="AU6" pgmap="293">also dissenting:</p> <p id="b293-5" pgmap="293">I disagree with that portion of the opinion holding that a court of chancery, in a foreclosure case, has inherent power to fix an upset price to be bid at the sale. In my opinion, this court should adhere to the contrary rule laid down in Chicago Title and Trust Co. v. Robin, 361 Ill. 261.</p> </opinion> </casebody> ',
    #             }
    #         }
    #         self.read_json_func.return_value = harvard_data
    #         cluster = (
    #             OpinionClusterFactoryWithChildrenAndParents(
    #                 docket=DocketFactory(),
    #                 sub_opinions=RelatedFactoryList(
    #                     OpinionWithChildrenFactory,
    #                     factory_related_name="cluster",
    #                     size=3,
    #                 ),
    #             ),
    #         )
    #         test_idea = [
    #             ("020lead", "this is the first opinion"),
    #             ("030concurrence", "this is the the concurrence"),
    #             ("040dissent", "this is the dissent"),
    #         ]
    #         for op in Opinion.objects.filter(cluster_id=cluster[0].id):
    #             op_type, html_columbia = test_idea.pop()
    #             op.type = op_type
    #             op.html_columbia = html_columbia
    #             op.save()
    #
    #         self.assertEqual(
    #             Opinion.objects.filter(cluster__id=1).count(),
    #             3,
    #             msg="Opinions not set up",
    #         )
    #         map_and_merge_opinions(cluster[0].id)
    #         self.assertEqual(
    #             Opinion.objects.filter(cluster__id=1).count(),
    #             4,
    #             msg="Opinion not added",
    #         )
    #         self.assertEqual(cluster[0].id, 1, msg="NOT 2")
    #
    #         # this test should properly add an opinion lead to
    #
    #     def test_merge_opinion_children(self):
    #         """"""
    #         cluster = OpinionClusterFactoryMultipleOpinions(
    #             docket=DocketFactory(),
    #             sub_opinions__data=[
    #                 {
    #                     "type": "020lead",
    #                     "html_columbia": "<p>this is the lead opinion</p>",
    #                 },
    #                 {
    #                     "type": "030concurrence",
    #                     "html_columbia": "<p>this is the concurrence</p>",
    #                     "author_str": "kevin ramirez",
    #                 },
    #                 {
    #                     "type": "040dissent",
    #                     "html_columbia": "<p>this is the dissent</p>",
    #                 },
    #             ],
    #         )
    #         harvard_data = {
    #             "casebody": {
    #                 "data": "<?xml version='1.0' encoding='utf-8'?>\n<casebody> "
    #                 "<opinion>this is lead opinion</opinion> "
    #                 "<opinion>this is the the concurrence </opinion> "
    #                 "<opinion>this is the dissent</opinion> </casebody> ",
    #             }
    #         }
    #         self.read_json_func.return_value = harvard_data
    #         self.assertEqual(
    #             Opinion.objects.filter(cluster__id=1).count(),
    #             3,
    #             msg="Opinions not set up",
    #         )
    #         self.assertEqual(
    #             Opinion.objects.filter(cluster__id=1)[0].xml_harvard,
    #             "",
    #             msg="Shouldnt have opinion",
    #         )
    #         map_and_merge_opinions(cluster.id)
    #         self.assertEqual(
    #             Opinion.objects.filter(cluster__id=1)[0].xml_harvard,
    #             "this is the first opinion",
    #             msg="Should have content",
    #         )
    #         self.assertEqual(
    #             Opinion.objects.filter(cluster__id=1).count(),
    #             3,
    #             msg="Opinions not set up",
    #         )
    #
    #     def test_merge_opinions_opinions(self):
    #         # cluster = (
    #         #     OpinionClusterFactory(
    #         #         docket=DocketFactory(),
    #         #     ),
    #         # )
    #         # test_idea = [
    #         #     ("040dissent", "this is the dissent"),
    #         #     ("030concurrence", "this is the the concurrence"),
    #         #     ("020lead", "this is the first opinion"),
    #         # ]
    #         # for idea in test_idea:
    #         #     OpinionFactory.create(
    #         #         cluster=cluster[0],
    #         #         type=idea[0],
    #         #         html_columbia=idea[1]
    #         #     )
    #         cluster = OpinionClusterFactory(docket=DocketFactory())
    #         test_idea = [
    #             ("040dissent", "this is the dissent"),
    #             ("030concurrence", "this is the the concurrence"),
    #             ("020lead", "this is the first opinion"),
    #         ]
    #         for idea in test_idea:
    #             OpinionFactory.create(
    #                 cluster=cluster, type=idea[0], html_columbia=idea[1]
    #             )
    #
    #         self.assertEqual(
    #             Opinion.objects.filter(cluster__id=1).count(),
    #             3,
    #             msg="Opinions not set up",
    #         )
    #
    #         # cluster = (
    #         #     OpinionClusterFactoryWithChildrenAndParents(
    #         #         docket=DocketFactory(),
    #         #         sub_opinions=RelatedFactoryList(
    #         #             OpinionWithChildrenFactory,
    #         #             factory_related_name="cluster",
    #         #             size=3
    #         #         )
    #         #     ),
    #         # )
    #         # self.assertEqual(cluster[0].id, 1, msg="hmmm")
    #         # self.assertEqual(Opinion.objects.filter(cluster__id=cluster[0].id).count(), 3, msg="Opinion are tough")
    #
    #         # harvard_data = {
    #         #     "casebody": {
    #         #         "data": "<?xml version='1.0' encoding='utf-8'?>\n<casebody> "
    #         #                 "<opinion>this is the first opinion </opinion> "
    #         #                 "<opinion>this is the the concurrence </opinion> "
    #         #                 "<opinion>this is the dissent</opinion> </casebody> ",
    #         #     }
    #         # }
    #         # self.read_json_func.return_value = harvard_data
    #         # test_idea = [
    #         #     ("040dissent", "this is the dissent"),
    #         #     ("030concurrence", "this is the the concurrence"),
    #         #     ("020lead", "this is the first opinion"),
    #         # ]
    #         # for op in Opinion.objects.filter(cluster_id=cluster[0].id):
    #         #     op_type, html_columbia = test_idea.pop()
    #         #     op.type = op_type
    #         #     op.html_columbia = html_columbia
    #         #     op.save()
    #         #
    #         # self.assertEqual(Opinion.objects.filter(cluster__id=1).count(), 3, msg="Opinions not set up")
    #         # map_and_merge_opinions(cluster[0].id)
    #         # self.assertEqual(Opinion.objects.filter(cluster__id=1).count(), 3, msg="Opinion added weird")
    #         #
    #         # for op in Opinion.objects.filter(cluster__id=1):
    #         #     self.assertEqual(op.html_columbia, op.xml_harvard, msg="NOT MATCHED")
    #
    #     def test_merger(self):
    #         # import requests
    #         # r = requests.get('https://ia903106.us.archive.org/18/items/law.free.cap.ga-app.71/757.1525757.json').json()
    #         r = {
    #             "name": "CANNON v. THE STATE",
    #             "name_abbreviation": "Cannon v. State",
    #             "decision_date": "1944-11-18",
    #             "docket_number": "30614",
    #             "casebody": {
    #                 "status": "ok",
    #                 "data": '<casebody firstpage="757" lastpage="758" xmlns="http://nrs.harvard.edu/urn-3:HLS.Libr.US_Case_Law.Schema.Case_Body:v1">\n  <docketnumber id="b795-7">30614.</docketnumber>\n  <parties id="AAY">CANNON <em>v. </em>THE STATE.</parties>\n  <decisiondate id="b795-9">Decided November 18, 1944.</decisiondate>\n  <attorneys id="b796-4"><page-number citation-index="1" label="758">*758</page-number><em>B. B. Giles, </em>for plaintiff in error.</attorneys>\n  <attorneys id="b796-5"><em>Lindley W. Gamp, solicitor, John A. Boyhin, solicitor-general,. Durwood T. Bye, </em>contra.</attorneys>\n  <opinion type="majority">\n    <author id="b796-6">Broyles, C. J.</author>\n    <p id="Auq">(After stating the foregoing facts.) After the-disposal of counts 2 and 3, the only charge before the court and jury was that the defendant had sold distilled spirits and alcohol as a retail dealer, without first obtaining a license from the State Revenue Commissioner. The evidence adduced to show the guilt, of the accused on count 1 was wholly circumstantial, and was insufficient to exclude every reasonable hypothesis except that of his-guilt, and it failed to show beyond a reasonable doubt that he had sold distilled spirits or alcohol. The cases of <em>Thomas </em>v. <em>State, </em>65 <em>Ga. App. </em>749 (16 S. E. 2d, 447), and <em>Martin </em>v. <em>State, </em>68 <em>Ga. App. </em>169 (22 S. E. 2d, 193), cited in behalf of the defendant in error, are distinguished by their facts from this case. The verdict was-contrary to law and the evidence; and the overruling of the certiorari was error. <em>Judgment reversed.</em></p>\n    <judges id="Ae85">\n      <em>MacIntyre, J., concurs.</em>\n    </judges>\n  </opinion>\n  <opinion type="concurrence">\n    <author id="b796-7">Gardner, J.,</author>\n    <p id="AK2">concurring specially: Under the record the judgment should be reversed for another reason. Since the jury, based on the same evidence, found the defendant not guilty on count 2 for possessing liquors, and a verdict finding him guilty on count 1 for selling intoxicating liquors, the verdicts are repugnant and void as being inconsistent verdicts by the same jury based on the same \'evidence. <em>Britt </em>v. <em>State, </em>36 <em>Ga. App. </em>668 (137 S. E. 791), and cit.; <em>Kuck </em>v. <em>State, </em>149 <em>Ga. </em>191 (99 S. E. 622). I concur in the reversal for this additional reason.</p>\n  </opinion>\n</casebody>\n',
    #             },
    #         }
    #         self.read_json_func.return_value = r
    #
    #         lead = """<p>The overruling of the certiorari was error.</p>
    # <p><center>                       DECIDED NOVEMBER 18, 1944.</center>
    # John Cannon was tried in the criminal court of Fulton County on an accusation containing three counts. Count I charged that in said county on July 24, 1943, he "did engage in and sell, as a retail dealer, distilled spirits and alcohol, without first obtaining a license from the State Revenue Commissioner of the State of Georgia." Count 2 charged that on July 24, 1943, he possessed forty-eight half pints and three pints of whisky in Fulton County, and had not been licensed by the State Revenue Commissioner to sell whisky as a retail or wholesale dealer. Count 3 charged that on September 24, 1943, in said county, he sold malt beverages as a retail dealer, without first securing a license from the State Revenue Commissioner. On the trial, after the close of the State's evidence, counsel for the accused made a motion that count 2 be stricken, and that a verdict for the defendant be directed on counts 1 and 3. The court sustained the motion as to counts 2 and 3, but overruled it as to count 1. The jury returned a verdict of guilty on count 1, and of not guilty on counts 2 and 3. Subsequently the defendant's certiorari was overruled by a judge of the superior court and that judgment is assigned as error. <span class="star-pagination">*Page 758</span>
    # After the disposal of counts 2 and 3, the only charge before the court and jury was that the defendant had sold distilled spirits and alcohol as a retail dealer, without first obtaining a license from the State Revenue Commissioner. The evidence adduced to show the guilt of the accused on count 1 was wholly circumstantial, and was insufficient to exclude every reasonable hypothesis except that of his guilt, and it failed to show beyond a reasonable doubt that he had sold distilled spirits or alcohol. The cases of <em>Thomas</em> v. <em>State,</em> <cross_reference><span class="citation no-link">65 Ga. App. 749</span></cross_reference> (<cross_reference><span class="citation" data-id="3407553"><a href="/opinion/3412403/thomas-v-state/">16 S.E.2d 447</a></span></cross_reference>), and <em>Martin</em> v. <em>State,</em> <cross_reference><span class="citation no-link">68 Ga. App. 169</span></cross_reference> (<cross_reference><span class="citation" data-id="3405716"><a href="/opinion/3410794/martin-v-state/">22 S.E.2d 193</a></span></cross_reference>), cited in behalf of the defendant in error, are distinguished by their facts from this case. The verdict was contrary to law and the evidence; and the overruling of the certiorari was error.</p>
    # <p><em>Judgment reversed. MacIntyre, J., concurs.</em></p>"""
    #         concurrence = """<p>Under the record the judgment should be reversed for another reason. Since the jury, based on the same evidence, found the defendant not guilty on count 2 for possessing liquors, and a verdict finding him guilty on count 1 for selling intoxicating liquors, the verdicts are repugnant and void as being inconsistent verdicts by the same jury based on the same evidence. <em>Britt</em> v. <em>State,</em> <cross_reference><span class="citation no-link">36 Ga. App. 668</span></cross_reference>
    # (<cross_reference><span class="citation no-link">137 S.E. 791</span></cross_reference>), and cit.; <em>Kuck</em> v. <em>State,</em> <cross_reference><span class="citation" data-id="5582722"><a href="/opinion/5732248/kuck-v-state/">149 Ga. 191</a></span></cross_reference>
    # (<cross_reference><span class="citation no-link">99 S.E. 622</span></cross_reference>). I concur in the reversal for this additional reason.</p>"""
    #         cluster = OpinionClusterFactoryMultipleOpinions(
    #             docket=DocketFactory(),
    #             sub_opinions__data=[
    #                 {"type": "020lead", "html_with_citations": lead},
    #                 {"type": "030concurrence", "html_with_citations": concurrence},
    #             ],
    #         )
    #         self.assertEqual(Opinion.objects.all().count(), 2)
    #         merge_opinion_clusters(cluster_id=cluster.id)
    #         self.assertEqual(Opinion.objects.all().count(), 2)
    #
    #     def test_merge_case_names(self):
    #         self.read_json_func.return_value = {
    #             "name": "KELLEY’S ESTATE",
    #             "name_abbreviation": "Kelley's Estate",
    #         }
    #         cluster = (
    #             OpinionClusterFactoryWithChildrenAndParents(
    #                 docket=DocketFactory(
    #                     case_name_short="",
    #                     case_name="",
    #                     case_name_full="",
    #                 ),
    #                 case_name_short="",
    #                 case_name="Kelley's Estate",
    #                 case_name_full="KELLEY’S ESTATE",
    #                 date_filed=date.today(),
    #                 sub_opinions=RelatedFactory(
    #                     OpinionWithChildrenFactory,
    #                     factory_related_name="cluster",
    #                 ),
    #             ),
    #         )
    #         start_merger(cluster_id=cluster[0].id)
    #
    #         self.assertEqual(
    #             cluster[0].case_name,
    #             cluster[0].sub_opinions.all().first().author_str,
    #         )
    #
    #     # def test_merge_judges(self):
    #     #     """
    #     #     discrepenacy in the first name an H or no H
    #     #     this example comes from
    #     #     Cluster, Harvard ID
    #     #     (2027381, 6554605)
    #     #
    #     #     """
    #     #     harvard_data = {
    #     #         "casebody": {
    #     #             "data": "<casebody> <author>JOHN J. CHINEN, Bankruptcy Judge.</author><opinion> *xyz </opinion>\n</casebody>\n"
    #     #         }
    #     #     }
    #     #     cluster = OpinionClusterFactoryWithChildrenAndParents(
    #     #         docket=DocketFactory(),
    #     #         judges="Jon J. Chinen",
    #     #     )
    #     #     judge_matches = judges_in_harvard(cluster, harvard_data)
    #     #     self.assertTrue(judge_matches)
    #     #
    #     #     # ('Lamar W. Davis, Jr.', 'Davis')
    #     #     harvard_data = {
    #     #         "casebody": {
    #     #             "data": "<casebody> <author>LAMAR W. DAVIS, JR., Bankruptcy Judge.</author><opinion> *xyz </opinion>\n</casebody>\n"
    #     #         }
    #     #     }
    #     #     cluster = OpinionClusterFactoryWithChildrenAndParents(
    #     #         docket=DocketFactory(),
    #     #         judges="Lamar W. Davis, Jr.",
    #     #     )
    #     #     judge_matches = judges_in_harvard(cluster, harvard_data)
    #     #     self.assertTrue(judge_matches)
    #     #
    #     #     # Cluster: 2597372 Harvard_id: 299727 #law.free.cap.f-supp.89/545.299727.json
    #     #     harvard_data = {
    #     #         "casebody": {
    #     #             "data": '<casebody>\n  <parties id="b597-14">PENNER INSTALLATION CORPORATION v. UNITED STATES.</parties>\n  <docketnumber id="b597-15">No. 47266.</docketnumber>\n  <court id="b597-16">United States Court of Claims.</court>\n  <decisiondate id="b597-17">April 3, 1950.</decisiondate>\n  <attorneys id="b598-21"><page-number citation-index="1" label="546">*546</page-number>Albert Foreman, New York City, for the plaintiff. M. Carl Levine, Morgulas &amp; Foreman, New York City, were on the brief.</attorneys>\n  <attorneys id="b598-22">John R. Franklin, Washington, D. C., with whom was Assistant Attorney General H. G. Morison, for the defendant.</attorneys>\n  <p id="b598-23">Before JONES, Chief Judge, and WHITAKER, HOWELL, MADDEN and LITTLETON, Judges.</p>\n  <opinion type="majority">\n    <author id="b598-24">WHITAKER, Judge.</author>\n   </opinion>\n</casebody>\n'
    #     #         }
    #     #     }
    #     #     cluster = OpinionClusterFactoryWithChildrenAndParents(
    #     #         docket=DocketFactory(),
    #     #         judges="Jones, Chief Judge, and Whitaker, Howell, Madden and Littleton, Judges",
    #     #     )
    #     #     judge_matches = judges_in_harvard(cluster, harvard_data)
    #     #     self.assertTrue(judge_matches)
    #
    #     def test_docket_number_merges(self):
    #         """"""
    #         # /storage/harvard_corpus/law.free.cap.mj.74/793.4355654.json
    #         # ----
    #         # Cluster: 2829548 Harvard_id: 4355654
    #         # id               (2829548, 4355654)
    #         # docket_number            ('201400102', 'NMCCA 201400102 GENERAL COURT-MARTIAL')
    #         harvard_data = {
    #             "docket_number": "NMCCA 201400102 GENERAL COURT-MARTIAL",
    #         }
    #         cluster = OpinionClusterFactoryWithChildrenAndParents(
    #             docket=DocketFactory(
    #                 docket_number="201400102", docket_number_core=""
    #             ),
    #         )
    #
    #         # /storage/harvard_corpus/law.free.cap.f-supp-3d.352/1312.12528902.json
    #         # ----
    #         # Cluster: 4568330 Harvard_id: 12528902
    #         # id               (4568330, 12528902)
    #         # judges           ('Choe-Groves', 'Choe, Groves')
    #         # docket_number            ('17-00031', 'Slip Op. 18-165; Court No. 17-00031')
    #         harvard_data = {
    #             "docket_number": "Slip Op. 18-165; Court No. 17-00031",
    #         }
    #         cluster = OpinionClusterFactoryWithChildrenAndParents(
    #             docket=DocketFactory(
    #                 docket_number="17-00031", docket_number_core=""
    #             ),
    #         )
    #         # How do we handle slip opinions...
    #         # there are so many variations
    #
    #         # /storage/harvard_corpus/law.free.cap.vet-app.28/222.12274823.json
    #         # ----
    #         # Cluster: 4248491 Harvard_id: 12274823
    #         # id               (4248491, 12274823)
    #         # case_name                ('Garzav. McDonald', 'Garza v. McDonald')
    #         # docket_number            ('14-2711', 'No. 14-2711')
    #
    #         harvard_data = {
    #             "docket_number": "No. 14-2711",
    #         }
    #         cluster = OpinionClusterFactoryWithChildrenAndParents(
    #             docket=DocketFactory(
    #                 docket_number="14-2711", docket_number_core=""
    #             ),
    #         )
    #
    #     def test_case_name_merger(self):
    #         """"""
    #
    #         harvard_data = {
    #             "name": "Travelodge International, Inc. v. Continental Properties, Inc. (In re Continental Properties, Inc.)",
    #             "name_abbreviation": "",
    #             "casebody": {
    #                 "data": "<casebody> <author>JOHN J. CHINEN, Bankruptcy Judge.</author><opinion> *xyz </opinion>\n</casebody>\n"
    #             },
    #         }
    #         cluster = OpinionClusterFactoryWithChildrenAndParents(
    #             docket=DocketFactory(
    #                 case_name_short="",
    #                 case_name="",
    #                 case_name_full="",
    #             ),
    #             case_name_short="",
    #             case_name="In Re Continental Properties, Inc",
    #             case_name_full="",
    #         )
    #
    #         # With dockets, im ready to say that if adocket when normalized is a subset of the toher we overwrite the docket
    #
    #     def test_wrong_opinion_total(self):
    #         """"""
    #         # https://www.courtlistener.com/opinion/3246772/tyler-v-state/
    #         # http://archive.org/download/law.free.cap.ala-app.19/380.8825727.json
    #
    #         harvard_data = {
    #             "casebody": {
    #                 "data": '<casebody firstpage="380" lastpage="383" xmlns="http://nrs.harvard.edu/urn-3:HLS.Libr.US_Case_Law.Schema.Case_Body:v1">\n  <citation id="b396-21">(97 South. 573)</citation>\n  <citation id="A05o">(6 Div. 152.)</citation>\n  <parties id="b396-22">TYLER v. STATE.</parties>\n  <court id="b396-23">(Court of Appeals of Alabama.</court>\n  <decisiondate id="AVW">April 3, 1923.</decisiondate>\n  <otherdate id="AKHV">Rehearing Denied April 17, 1923.</otherdate>\n  <otherdate id="Ahci">Affirmed\' on Mandate July 10, 1923.</otherdate>\n  <otherdate id="Aan">Rehearing Denied Oct. 16, 1923.)</otherdate>\n  <attorneys id="b398-8"><page-number citation-index="1" label="382">*382</page-number>Pinkney Scott, of Bessemer, for appellant.</attorneys>\n  <attorneys id="b398-10">Harwell G. Davis, Atty. Gen., and Lamar Eield, Asst.\' Atty. Gen., and Ben G. Perry, of Bessemer, for the State.</attorneys>\n  <opinion type="majority">\n    <author id="b398-13">BRICKEN, P. J.</author>\n    <p id="AvZ1">This is the third appeal in this case; the first being from an order of the judge of the circuit court denying defendant bail, and resulting in an affirmance here. Ex parte Tyler, 17 Ala. App. 698, 89 South. 926. The second appeal was from a judgment of conviction for murder in the first degree resulting in a reversal by the Supreme Court. Tyler v. State, 207 Ala. 129, 92 South. 478.</p>\n    <p id="b398-14">The evidence offered by the state tended to show that the defendant, on December 12, 1920, while under the influence of liquor, went to the home of J. M. Tyler, defendant’s father, where the deceased was a guest visiting the widowed sister of the defendant, Mrs. Silvia; that defendant had protested against deceased’s attention to Mrs. Silvia; that when defendant arrived at the home of his father the deceased was playing with two, of Mrs. Silvia’s children in the kitchen, and that Mrs. Silvia and the baby were in the adjoining room; that defendant, without the slightest provocation or semblance of justification, shot the deceased, inflicting upon his person wounds that caused his death.</p>\n    <p id="b398-15">The defendant offered some evidence tending to show that he was on his way to his brother’s, carrying his brother some medicine; that he stopped by his father’s home, and entered the home by the kitchen door; that deceased seized and attacked\' him; and, in a scuffle for defendant’s pistol, the weapon was discharged, inflicting the wounds that caused the death of the deceased.</p>\n    <p id="b398-16">The defendant’s motion to quash the venire and his objecting to being put to trial because two of the veniremen drawn for the defendant’s trial had served as jurors on the former trial of the defendant was without merit, and was\'properly overruled. Stover v. State, 204 Ala. 311, 85 South. 393; Morris v. State, 18 Ala. App. 135, 90 South. 57.</p>\n    <p id="b398-17">The veniremen who had served on the jury on the previous trial were subject to challenge for cause and by exercising the right of challenge for cause, if they were objectionable to defendant, these veniremen would have been stricken from the list, without curtailing the defendant’s strikes or peremptory challenges. Wickard v. State,” 109 Ala. 45, 19 South. 491; Stover v. State, supra.</p>\n    <p id="b398-18">The question addressed to Mary Alexander, “Who -\\yere your physicians, who treated him?” and that addressed to Dr. Wilkinson, “How long have you known him (deceased) ?” were preliminary in character, and the defendant’s objection was properly overruled.</p>\n    <p id="b398-19"> There was evidence tending to show that the motive prompting the homicide was <page-number citation-index="1" label="383">*383</page-number>to put an end to the attention the deceased was showing Mrs. Silvia, and her testimony that her husband was dead, that she was living at her father’s, that she was the mother of the children present in the house at the time of the homicide, and the ages of the children, was not without relevancy as shedding light on the motive of the defendant and the conduct of the deceased at the( time of the homicide. While evidence as to motive is not essential, it is always competent. Jones v. State, 13 Ala. App. 10, 68 South. 690; Brunson v. State, 124 Ala. 40, 27 South. 410.</p>\n    <p id="b399-4"> It is not essential that a dying declaration, if made under a sense of impending death, should be wholly voluntary.</p>\n    <blockquote id="b399-5">They “are admitted upon the theory that the consciousness of approaching death dispels from the mind all motive for making a false statement, in view of the fact that the party recognizes the fact that he shall soon appear in the.presence of his Maker.” Parker v. State, 165 Ala. 1, 51 South. 260.</blockquote>\n    <p id="b399-6">The predicate was sufficient to authorize the admission of the dying declaration. Tyler v. State; 207 Ala. 129, 92 South. 478.</p>\n    <p id="b399-7">The testimony /Of the witness Mrs. George Silvia, given on the preliminary trial, was only admissible to impeach her testimony on the present trial, after proper predicate had beeji laid for such purpose, and the court properly admitted such as •tended to contradict her and corresponding to the several predicates laid on her cross-examination, and properly excluded the other portion of her testimony.</p>\n    <p id="b399-8">The witness, Mrs. E. S. Tyler, testified: “I did not say in that statement that Lon was drunk, but he must have been drunk or something, I reckon, but I don’t know how that was,” and so much of the written signed statement made by this witness, to wit, “Lon was drunk” was admissible to contradict her -testimony; hence the defendant’s general objection to all of the statement, was not well taken. Longmire v. State, 130 Ala. 67, 30 South. 413; Wright v. State, 136 Ala. 139, 145, 34 South. 233.</p>\n    <p id="b399-9">The solicitor, in his closing argument to the jury\', made the following statements to the jury: “We have got too much killing around here.” “Don’t you know we have.” “Do you know why?” The defendant objected to each of these statements and moved to exclude them because they were improper. The court overruled the defendant’s objection and motion and to these rulings the defendant reserved, exception.</p>\n    <p id="b399-10">In each of these rulings the court committed reversible error. The statement of the solicitor, “We have got too much killing around here,” was the statement of a fact, of which there was no evidence, and if evidence had been offered of this fact it would not have been admissible. Alabama Fuel <em>&amp; </em>Iron Co. v. Williams, 207 Ala. 99, 91 South. 879; McAdory v. State, 62 Ala. 154; Cross v. State, 68 Ala. 476; Flowers v. State, 15 Ala. App. 220, 73 South. 126; Strother v. State, 15 Ala. App. 106, 72 South. 566; B. R. L. &amp; P. Co. v. Drennen, 175 Ala. 349, 57 South. 876, Ann. Cas. 1914C, 1037; B’ham Ry. L. &amp; P. Co. v. Gonzalez, 183 Ala. 273, 61 South. 80, Ann. Cas. 1916A, 543.</p>\n    <p id="b399-12">In some of the authorities cited, the Supreme Court said:</p>\n    <blockquote id="b399-13">“However reluctant an appellate court may be to interfere with the discretion of a primary court in regulating the trial of causes, if it should appear that it had refused, to the prejudice of a party, to compel counsel to confine their arguments and comments to the jury, to the law and evidence of the case under consideration — if it had permitted them to.refer to and comment upon facts not in evidence, or which would not be admissible as evidence, it would be a fatal error.” 62 Ala. 163.</blockquote>\n    <blockquote id="b399-14">. “Now, there was not only no evidence before the jury of that other homicide, or its details, but such evidence, if offered, would have been illegal and irrelevant. This was not argument, and could furnish no .safe or permissible aid to the jury in considering and weighing the testimony before them. The jury\', in their deliberations, should consider no facts, save those given in evidence.” 68 Ala. 476.</blockquote>\n    <p id="b399-15">The statements here brought in question were not only argument, but their scope and. effect, however innocently made, was an appeal to the mob spirit to convict the defendant, regardless of the evidence because other killings had occurred in that county. The tendency and effect of their argument was to incense the minds of the jury and draw them away from the facts in the case. The defendant was entitled to have his case tried on the evidence, without regard to other killings. The argument of defendant’s counsel was clearly within the issues and the improper argument of the solicitor cannot be justified on the theory that it was made in answer to the argument of defendant’s attorney. . -</p>\n    <p id="b399-16">Charge 1, refused to the defendant, assumes that Lon Tyler was a guest at his father’s house, and was invasive of the province of the jury. Charge 2 is argumentative, elliptical, and otherwise faulty. Charge 4 is involved and relieved the defendant fr-om the duty of retreating. Charge 5 pretermits imminent danger. Charge 7 was properly refused; the burden is not on the state to “disprove” that defendant was not free from fault. Charge 8 is not the law. Charge 9 is bad. Deliberation and premeditation is not essential to murder in the second degree.</p>\n    <p id="b399-17">For the error pointed out, the judgment is reversed.</p>\n    <p id="b399-18">Reversed and remanded.</p>\n    <author id="b399-19">PER CURIAM.</author>\n    <p id="ARX">Affirmed on authority of Ex parte State, ex rel. Attorney General, In re Lon Tyler v. State, 210 Ala. 96, 97 South. 573.</p>\n  </opinion>\n</casebody>\n',
    #                 "status": "ok",
    #             }
    #         }
    #         cluster = OpinionClusterWithParentsFactory.create()
    #
    #         # We have a p
    #         OpinionFactory.create(
    #             cluster=cluster,
    #             type="010combined",
    #         )
    #         OpinionFactory.create(
    #             cluster=cluster,
    #             type="050addendum",
    #         )
    #         self.assertEqual(cluster.id, 1, msg="wrong id")
    #         self.assertEqual(
    #             Opinion.objects.all().count(),
    #             2,
    #             msg=f"{Opinion.objects.all().count()}",
    #         )
    #         self.assertEqual(
    #             Opinion.objects.filter(cluster__id=cluster).count(),
    #             2,
    #             msg="Wrong total",
    #         )
