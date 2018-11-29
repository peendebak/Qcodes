# Since all other tests of data_set and measurements will inevitably also
# test the sqlite_base module, we mainly test exceptions and small helper
# functions here
from sqlite3 import OperationalError
import tempfile
import os

import pytest
import hypothesis.strategies as hst
from hypothesis import given
import unicodedata
import numpy as np

from qcodes.dataset.descriptions import RunDescriber
from qcodes.dataset.dependencies import InterDependencies
import qcodes.dataset.sqlite_base as mut  # mut: module under test
from qcodes.dataset.database import get_DB_location, path_to_dbfile
from qcodes.dataset.guids import generate_guid
from qcodes.dataset.param_spec import ParamSpec
# pylint: disable=unused-import
from qcodes.tests.dataset.temporary_databases import \
    empty_temp_db, experiment, dataset
from qcodes.tests.dataset.dataset_fixtures import scalar_dataset
from qcodes.tests.dataset.test_database_creation_and_upgrading import \
    error_caused_by
# pylint: enable=unused-import

_unicode_categories = ('Lu', 'Ll', 'Lt', 'Lm', 'Lo', 'Nd', 'Pc', 'Pd', 'Zs')


def test_path_to_dbfile():

    with tempfile.TemporaryDirectory() as tempdir:
        tempdb = os.path.join(tempdir, 'database.db')
        conn = mut.connect(tempdb)
        try:
            assert path_to_dbfile(conn) == tempdb
        finally:
            conn.close()


def test_one_raises(experiment):
    conn = experiment.conn

    with pytest.raises(RuntimeError):
        mut.one(conn.cursor(), column='Something_you_dont_have')


def test_atomic_transaction_raises(experiment):
    conn = experiment.conn

    bad_sql = '""'

    with pytest.raises(RuntimeError):
        mut.atomic_transaction(conn, bad_sql)


def test_atomic_raises(experiment):
    conn = experiment.conn

    bad_sql = '""'

    # it seems that the type of error raised differs between python versions
    # 3.6.0 (OperationalError) and 3.6.3 (RuntimeError)
    # -strange, huh?
    with pytest.raises((OperationalError, RuntimeError)):
        with mut.atomic(conn):
            mut.transaction(conn, bad_sql)


def test_insert_many_values_raises(experiment):
    conn = experiment.conn

    with pytest.raises(ValueError):
        mut.insert_many_values(conn, 'some_string', ['column1'],
                               values=[[1], [1, 3]])


def test_get_metadata_raises(experiment):
    with pytest.raises(RuntimeError) as excinfo:
        mut.get_metadata(experiment.conn, 'something', 'results')
    assert error_caused_by(excinfo, "no such column: something")


@given(table_name=hst.text(max_size=50))
def test__validate_table_raises(table_name):
    should_raise = False
    for char in table_name:
        if unicodedata.category(char) not in _unicode_categories:
            should_raise = True
            break
    if should_raise:
        with pytest.raises(RuntimeError):
            mut._validate_table_name(table_name)
    else:
        assert mut._validate_table_name(table_name)


def test_get_dependents(experiment):

    x = ParamSpec('x', 'numeric')
    t = ParamSpec('t', 'numeric')
    y = ParamSpec('y', 'numeric', depends_on=['x', 't'])

    # Make a dataset
    (_, run_id, _) = mut.create_run(experiment.conn,
                                    experiment.exp_id,
                                    name='testrun',
                                    guid=generate_guid(),
                                    parameters=[x, t, y])

    deps = mut.get_dependents(experiment.conn, run_id)

    layout_id = mut.get_layout_id(experiment.conn,
                                  'y', run_id)

    assert deps == [layout_id]

    # more parameters, more complicated dependencies

    x_raw = ParamSpec('x_raw', 'numeric')
    x_cooked = ParamSpec('x_cooked', 'numeric', inferred_from=['x_raw'])
    z = ParamSpec('z', 'numeric', depends_on=['x_cooked'])

    (_, run_id, _) = mut.create_run(experiment.conn,
                                    experiment.exp_id,
                                    name='testrun',
                                    guid=generate_guid(),
                                    parameters=[x, t, x_raw,
                                                x_cooked, y, z])

    deps = mut.get_dependents(experiment.conn, run_id)

    expected_deps = [mut.get_layout_id(experiment.conn, 'y', run_id),
                     mut.get_layout_id(experiment.conn, 'z', run_id)]

    assert deps == expected_deps


def test_column_in_table(dataset):
    assert mut.is_column_in_table(dataset.conn, "runs", "run_id")
    assert not mut.is_column_in_table(dataset.conn, "runs", "non-existing-column")


def test_run_exist(dataset):
    assert mut.run_exists(dataset.conn, dataset.run_id)
    assert not mut.run_exists(dataset.conn, dataset.run_id + 1)


def test_get_last_run(dataset):
    assert dataset.run_id == mut.get_last_run(dataset.conn, dataset.exp_id)


def test_get_last_run_no_runs(experiment):
    assert None is mut.get_last_run(experiment.conn, experiment.exp_id)


def test_get_last_experiment(experiment):
    assert experiment.exp_id == mut.get_last_experiment(experiment.conn)


def test_get_last_experiment_no_experiments(empty_temp_db):
    conn = mut.connect(get_DB_location())
    assert None is mut.get_last_experiment(conn)


def test_update_runs_description(dataset):

    invalid_descs = ['{}', 'description']

    for idesc in invalid_descs:
        with pytest.raises(ValueError):
            mut.update_run_description(dataset.conn, dataset.run_id, idesc)

    desc = RunDescriber(InterDependencies()).to_json()
    mut.update_run_description(dataset.conn, dataset.run_id, desc)


def test_runs_table_columns(empty_temp_db):
    """
    Ensure that the column names of a pristine runs table are what we expect
    """
    colnames = mut.RUNS_TABLE_COLUMNS.copy()
    conn = mut.connect(get_DB_location())
    query = "PRAGMA table_info(runs)"
    cursor = conn.cursor()
    for row in cursor.execute(query):
        colnames.remove(row['name'])

    assert colnames == []


def test_get_data_no_columns(scalar_dataset):
    ds = scalar_dataset
    ref = mut.get_data(ds.conn, ds.table_name, [])
    assert ref == [[]]


def test_get_parameter_data(scalar_dataset):
    ds = scalar_dataset
    params = ds.parameters.split(',')
    # delete some random parameter to test it with an incomplete list
    del params[-2]

    ref = mut.get_data(ds.conn, ds.table_name, params)
    dut = mut.get_parameter_data(ds.conn, ds.table_name, params)
    for i_row, row in enumerate(ref):
        for i_param, param_name in enumerate(params):
            v_ref = row[i_param]
            v_test = dut[param_name][i_row]
            if isinstance(v_ref, float):
                assert isinstance(v_test, np.float64)
            elif isinstance(v_ref, int):
                assert isinstance(v_test, np.int)
            else:
                raise RuntimeError('Unsupported data type')
            assert np.isclose(v_test, v_ref)
