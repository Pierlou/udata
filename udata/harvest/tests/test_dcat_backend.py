import logging
import os

import pytest

from datetime import date
import xml.etree.ElementTree as ET

from udata.models import Dataset
from udata.core.organization.factories import OrganizationFactory
from udata.core.dataset.factories import LicenseFactory

from .factories import HarvestSourceFactory
from ..backends.dcat import URIS_TO_REPLACE
from .. import actions

log = logging.getLogger(__name__)


TEST_DOMAIN = 'data.test.org'  # Need to be used in fixture file
DCAT_URL_PATTERN = 'http://{domain}/{path}'
DCAT_FILES_DIR = os.path.join(os.path.dirname(__file__), 'dcat')
CSW_DCAT_FILES_DIR = os.path.join(os.path.dirname(__file__), 'csw_dcat')


def mock_dcat(rmock, filename, path=None):
    url = DCAT_URL_PATTERN.format(path=path or filename, domain=TEST_DOMAIN)
    with open(os.path.join(DCAT_FILES_DIR, filename)) as dcatfile:
        body = dcatfile.read()
    rmock.get(url, text=body)
    return url


def mock_pagination(rmock, path, pattern):
    url = DCAT_URL_PATTERN.format(path=path, domain=TEST_DOMAIN)

    def callback(request, context):
        page = request.qs.get('page', [1])[0]
        filename = pattern.format(page=page)
        context.status_code = 200
        with open(os.path.join(DCAT_FILES_DIR, filename)) as dcatfile:
            return dcatfile.read()

    rmock.get(rmock.ANY, text=callback)
    return url


def mock_csw_pagination(rmock, path, pattern):
    url = DCAT_URL_PATTERN.format(path=path, domain=TEST_DOMAIN)

    def callback(request, context):
        request_tree = ET.fromstring(request.body)
        page = int(request_tree.get('startPosition'))
        with open(os.path.join(CSW_DCAT_FILES_DIR, pattern.format(page))) as cswdcatfile:
            return cswdcatfile.read()

    rmock.post(rmock.ANY, text=callback)
    return url


@pytest.mark.usefixtures('clean_db')
@pytest.mark.options(PLUGINS=['dcat'])
class DcatBackendTest:

    def test_simple_flat(self, rmock):
        filename = 'flat.jsonld'
        url = mock_dcat(rmock, filename)
        org = OrganizationFactory()
        source = HarvestSourceFactory(backend='dcat',
                                      url=url,
                                      organization=org)

        actions.run(source.slug)

        source.reload()

        job = source.get_last_job()
        assert len(job.items) == 3

        datasets = {d.harvest.dct_identifier: d for d in Dataset.objects}

        assert len(datasets) == 3

        for i in '1 2 3'.split():
            d = datasets[i]
            assert d.title == f'Dataset {i}'
            assert d.description == f'Dataset {i} description'
            assert d.harvest.remote_id == i
            assert d.harvest.backend == 'DCAT'
            assert d.harvest.source_id == str(source.id)
            assert d.harvest.domain == source.domain
            assert d.harvest.dct_identifier == i
            assert d.harvest.remote_url == f'http://data.test.org/datasets/{i}'
            assert d.harvest.uri == f'http://data.test.org/datasets/{i}'
            assert d.harvest.created_at.date() == date(2016, 12, 14)
            assert d.harvest.modified_at.date() == date(2016, 12, 14)
            assert d.harvest.last_update.date() == date.today()
            assert d.harvest.archived_at is None
            assert d.harvest.archived is None

        # First dataset
        dataset = datasets['1']
        assert dataset.tags == ['tag-1', 'tag-2', 'tag-3', 'tag-4',
                                'theme-1', 'theme-2']
        assert len(dataset.resources) == 2

        # Second dataset
        dataset = datasets['2']
        assert dataset.tags == ['tag-1', 'tag-2', 'tag-3']
        assert len(dataset.resources) == 2

        # Third dataset
        dataset = datasets['3']
        assert dataset.tags == ['tag-1', 'tag-2']
        assert len(dataset.resources) == 1

    def test_flat_with_blank_nodes(self, rmock):
        filename = 'bnodes.jsonld'
        url = mock_dcat(rmock, filename)
        org = OrganizationFactory()
        source = HarvestSourceFactory(backend='dcat',
                                      url=url,
                                      organization=org)

        actions.run(source.slug)

        datasets = {d.harvest.dct_identifier: d for d in Dataset.objects}

        assert len(datasets) == 3
        assert len(datasets['1'].resources) == 2
        assert len(datasets['2'].resources) == 2
        assert len(datasets['3'].resources) == 1

        assert datasets['1'].resources[0].title == 'Resource 1-1'
        assert datasets['1'].resources[0].description == 'A JSON resource'
        assert datasets['1'].resources[0].format == 'json'
        assert datasets['1'].resources[0].mime == 'application/json'

    def test_flat_with_blank_nodes_xml(self, rmock):
        filename = 'bnodes.xml'
        url = mock_dcat(rmock, filename)
        org = OrganizationFactory()
        source = HarvestSourceFactory(backend='dcat',
                                      url=url,
                                      organization=org)

        actions.run(source.slug)

        datasets = {d.harvest.dct_identifier: d for d in Dataset.objects}

        assert len(datasets) == 3
        assert len(datasets['3'].resources) == 1
        assert len(datasets['1'].resources) == 2
        assert len(datasets['2'].resources) == 2

    def test_simple_nested_attributes(self, rmock):
        filename = 'nested.jsonld'
        url = mock_dcat(rmock, filename)
        source = HarvestSourceFactory(backend='dcat',
                                      url=url,
                                      organization=OrganizationFactory())

        actions.run(source.slug)

        source.reload()

        job = source.get_last_job()
        assert len(job.items) == 1

        dataset = Dataset.objects.first()
        assert dataset.temporal_coverage is not None
        assert dataset.temporal_coverage.start == date(2016, 1, 1)
        assert dataset.temporal_coverage.end == date(2016, 12, 5)
        assert dataset.harvest.remote_url == 'http://data.test.org/datasets/1'

        assert len(dataset.resources) == 1

        resource = dataset.resources[0]
        assert resource.checksum is not None
        assert resource.checksum.type == 'sha1'
        assert (resource.checksum.value
                == 'fb4106aa286a53be44ec99515f0f0421d4d7ad7d')

    def test_idempotence(self, rmock):
        filename = 'flat.jsonld'
        url = mock_dcat(rmock, filename)
        org = OrganizationFactory()
        source = HarvestSourceFactory(backend='dcat',
                                      url=url,
                                      organization=org)

        # Run the same havester twice
        actions.run(source.slug)
        actions.run(source.slug)

        datasets = {d.harvest.dct_identifier: d for d in Dataset.objects}

        assert len(datasets) == 3
        assert len(datasets['1'].resources) == 2
        assert len(datasets['2'].resources) == 2
        assert len(datasets['3'].resources) == 1

    def test_hydra_partial_collection_view_pagination(self, rmock):
        url = mock_pagination(rmock, 'catalog.jsonld',
                              'partial-collection-{page}.jsonld')
        org = OrganizationFactory()
        source = HarvestSourceFactory(backend='dcat',
                                      url=url,
                                      organization=org)

        actions.run(source.slug)

        source.reload()

        job = source.get_last_job()
        assert len(job.items) == 4

    def test_hydra_legacy_paged_collection_pagination(self, rmock):
        url = mock_pagination(rmock, 'catalog.jsonld',
                              'paged-collection-{page}.jsonld')
        org = OrganizationFactory()
        source = HarvestSourceFactory(backend='dcat',
                                      url=url,
                                      organization=org)

        actions.run(source.slug)

        source.reload()

        job = source.get_last_job()
        assert len(job.items) == 4

    def test_failure_on_initialize(self, rmock):
        url = DCAT_URL_PATTERN.format(path='', domain=TEST_DOMAIN)
        rmock.get(url, text='should fail')
        org = OrganizationFactory()
        source = HarvestSourceFactory(backend='dcat',
                                      url=url,
                                      organization=org)

        actions.run(source.slug)

        source.reload()

        job = source.get_last_job()

        assert job.status == 'failed'

    def test_supported_mime_type(self, rmock):
        url = mock_dcat(rmock, 'catalog.xml', path='without/extension')
        rmock.head(url, headers={'Content-Type': 'application/xml; charset=utf-8'})
        org = OrganizationFactory()
        source = HarvestSourceFactory(backend='dcat',
                                      url=url,
                                      organization=org)

        actions.run(source.slug)

        source.reload()

        job = source.get_last_job()

        assert job.status == 'done'
        assert job.errors == []
        assert len(job.items) == 3

    def test_xml_catalog(self, rmock):
        LicenseFactory(id='lov2', title='Licence Ouverte Version 2.0')

        url = mock_dcat(rmock, 'catalog.xml', path='catalog.xml')
        org = OrganizationFactory()
        source = HarvestSourceFactory(backend='dcat',
                                      url=url,
                                      organization=org)

        actions.run(source.slug)

        # test dct:license support
        dataset = Dataset.objects.get(harvest__dct_identifier='3')
        assert dataset.license.id == 'lov2'
        assert dataset.harvest.remote_url == 'http://data.test.org/datasets/3'
        assert dataset.harvest.remote_id == '3'
        assert dataset.harvest.created_at.date() == date(2016, 12, 14)
        assert dataset.harvest.modified_at.date() == date(2016, 12, 14)
        assert dataset.frequency == 'daily'
        assert dataset.description == 'Dataset 3 description'

        assert dataset.temporal_coverage is not None
        assert dataset.temporal_coverage.start == date(2016, 1, 1)
        assert dataset.temporal_coverage.end == date(2016, 12, 5)

        dataset = Dataset.objects.get(harvest__dct_identifier='1')
        # test html abstract description support
        assert dataset.description == '# h1 title\n\n## h2 title\n\n **and bold text**'
        # test DCAT periodoftime
        assert dataset.temporal_coverage is not None
        assert dataset.temporal_coverage.start == date(2016, 1, 1)
        assert dataset.temporal_coverage.end == date(2016, 12, 5)

        assert len(dataset.resources) == 2

        resource_1 = next(res for res in dataset.resources if res.title == 'Resource 1-1')
        # Format is a IANA URI
        assert resource_1.format == 'json'
        assert resource_1.mime == 'application/json'
        assert resource_1.filesize == 12323
        assert resource_1.description == 'A JSON resource'
        assert resource_1.url == 'http://data.test.org/datasets/1/resources/1/file.json'

        resource_2 = next(res for res in dataset.resources if res.title == 'Resource 1-2')
        assert resource_2.format == 'json'
        assert resource_2.description == 'A JSON resource'
        assert resource_2.url == 'http://data.test.org/datasets/1/resources/2/file.json'

    def test_geonetwork_xml_catalog(self, rmock):
        url = mock_dcat(rmock, 'geonetwork.xml', path='catalog.xml')
        org = OrganizationFactory()
        source = HarvestSourceFactory(backend='dcat',
                                      url=url,
                                      organization=org)
        actions.run(source.slug)
        dataset = Dataset.objects.filter(organization=org).first()
        assert dataset is not None
        assert dataset.harvest is not None
        assert dataset.harvest.remote_id == '0c456d2d-9548-4a2a-94ef-231d9d890ce2 https://sig.oreme.org/geonetwork/srv/resources0c456d2d-9548-4a2a-94ef-231d9d890ce2'  # noqa
        assert dataset.harvest.dct_identifier == '0c456d2d-9548-4a2a-94ef-231d9d890ce2 https://sig.oreme.org/geonetwork/srv/resources0c456d2d-9548-4a2a-94ef-231d9d890ce2'  # noqa
        assert dataset.harvest.created_at.date() == date(2004, 11, 3)
        assert dataset.harvest.modified_at is None
        assert dataset.harvest.uri == 'https://sig.oreme.org/geonetwork/srv/resources/datasets/0c456d2d-9548-4a2a-94ef-231d9d890ce2 https://sig.oreme.org/geonetwork/srv/resources0c456d2d-9548-4a2a-94ef-231d9d890ce2'  # noqa
        assert dataset.harvest.remote_url is None  # the uri validation failed
        assert dataset.description.startswith('Data of type chemistry')
        assert dataset.temporal_coverage is not None
        assert dataset.temporal_coverage.start == date(2004, 11, 3)
        assert dataset.temporal_coverage.end == date(2005, 3, 30)

    def test_sigoreme_xml_catalog(self, rmock):
        LicenseFactory(id='fr-lo', title='Licence ouverte / Open Licence')
        url = mock_dcat(rmock, 'sig.oreme.rdf')
        org = OrganizationFactory()
        source = HarvestSourceFactory(backend='dcat',
                                      url=url,
                                      organization=org)
        actions.run(source.slug)
        dataset = Dataset.objects.filter(organization=org).first()

        assert dataset is not None
        assert dataset.frequency == 'irregular'
        assert 'gravi' in dataset.tags  # support dcat:keyword
        assert 'geodesy' in dataset.tags  # support dcat:theme
        assert dataset.license.id == 'fr-lo'
        assert len(dataset.resources) == 1
        assert dataset.description.startswith("Data from the 'National network")
        assert dataset.harvest is not None
        assert dataset.harvest.dct_identifier == '0437a976-cff1-4fa6-807a-c23006df2f8f'
        assert dataset.harvest.remote_id == '0437a976-cff1-4fa6-807a-c23006df2f8f'
        assert dataset.harvest.created_at is None
        assert dataset.harvest.modified_at is None
        assert dataset.harvest.uri == 'https://sig.oreme.org/geonetwork/srv/eng/catalog.search#/metadata//datasets/0437a976-cff1-4fa6-807a-c23006df2f8f'  # noqa
        assert dataset.harvest.remote_url == 'https://sig.oreme.org/geonetwork/srv/eng/catalog.search#/metadata//datasets/0437a976-cff1-4fa6-807a-c23006df2f8f'  # noqa
        assert dataset.harvest.last_update.date() == date.today()

    def test_unsupported_mime_type(self, rmock):
        url = DCAT_URL_PATTERN.format(path='', domain=TEST_DOMAIN)
        rmock.head(url, headers={'Content-Type': 'text/html; charset=utf-8'})
        org = OrganizationFactory()
        source = HarvestSourceFactory(backend='dcat',
                                      url=url,
                                      organization=org)

        actions.run(source.slug)

        source.reload()

        job = source.get_last_job()

        assert job.status == 'failed'
        assert len(job.errors) == 1

        error = job.errors[0]
        assert error.message == 'Unsupported mime type "text/html"'

    def test_unable_to_detect_format(self, rmock):
        url = DCAT_URL_PATTERN.format(path='', domain=TEST_DOMAIN)
        rmock.head(url, headers={'Content-Type': ''})
        org = OrganizationFactory()
        source = HarvestSourceFactory(backend='dcat',
                                      url=url,
                                      organization=org)

        actions.run(source.slug)

        source.reload()

        job = source.get_last_job()

        assert job.status == 'failed'
        assert len(job.errors) == 1

        error = job.errors[0]
        expected = 'Unable to detect format from extension or mime type'
        assert error.message == expected
        
    def test_use_replaced_uris(self, rmock, mocker):
        mocker.patch.dict(
            URIS_TO_REPLACE,
            {'http://example.org/this-url-does-not-exist': 'https://json-ld.org/contexts/person.jsonld'}
        )
        url = DCAT_URL_PATTERN.format(path='', domain=TEST_DOMAIN)
        rmock.get(url, json={
            '@context': 'http://example.org/this-url-does-not-exist',
            '@type': 'dcat:Catalog',
            'dataset': []
        })
        rmock.head(url, headers={'Content-Type': 'application/json'})
        org = OrganizationFactory()
        source = HarvestSourceFactory(backend='dcat',
                                      url=url,
                                      organization=org)
        actions.run(source.slug)

        source.reload()

        job = source.get_last_job()
        assert len(job.items) == 0
        assert job.status == 'done'

    def test_target_404(self, rmock):
        filename = 'obvious-format.jsonld'
        url = url=DCAT_URL_PATTERN.format(path=filename, domain=TEST_DOMAIN)
        rmock.get(url, status_code=404)

        source = HarvestSourceFactory(backend='dcat', url=url, organization=OrganizationFactory())
        actions.run(source.slug)
        source.reload()

        job = source.get_last_job()
        assert job.status == "failed"
        assert len(job.errors) == 1
        assert "404 Client Error" in job.errors[0].message

        filename = 'need-to-head-to-guess-format'
        url = url=DCAT_URL_PATTERN.format(path=filename, domain=TEST_DOMAIN)
        rmock.head(url, status_code=404)

        source = HarvestSourceFactory(backend='dcat', url=url, organization=OrganizationFactory())
        actions.run(source.slug)
        source.reload()

        job = source.get_last_job()
        assert job.status == "failed"
        assert len(job.errors) == 1
        assert "404 Client Error" in job.errors[0].message

        
@pytest.mark.usefixtures('clean_db')
@pytest.mark.options(PLUGINS=['csw-dcat'])
class CswDcatBackendTest:

    def test_geonetworkv4(self, rmock):
        url = mock_csw_pagination(rmock, 'geonetwork/srv/eng/csw.rdf', 'geonetworkv4-page-{}.xml')
        org = OrganizationFactory()
        source = HarvestSourceFactory(backend='csw-dcat',
                                      url=url,
                                      organization=org)

        actions.run(source.slug)

        source.reload()

        job = source.get_last_job()
        assert len(job.items) == 6

        datasets = {d.harvest.dct_identifier: d for d in Dataset.objects}

        assert len(datasets) == 6

        # First dataset
        dataset = datasets['https://www.geo2france.fr/2017/accidento']
        assert dataset.title == 'Localisation des accidents de la circulation routière en 2017'
        assert dataset.description == 'Accidents corporels de la circulation en Hauts de France (2017)'
        assert set(dataset.tags) == set([
            'donnee-ouverte', 'accidentologie', 'accident', 'reseaux-de-transport', 'accident-de-la-route',
            'hauts-de-france', 'nord', 'pas-de-calais', 'oise', 'somme', 'aisne'
        ])
        assert dataset.harvest.created_at.date() == date(2017, 1, 1)
        assert len(dataset.resources) == 1
        resource = dataset.resources[0]
        assert resource.title == 'accidento_hdf_L93'
        assert resource.url == 'https://www.geo2france.fr/geoserver/cr_hdf/ows'
        assert resource.format == 'ogc:wms'
