"""Batch migration must preserve calculations and isolate each batch's staging files."""
import xml.etree.ElementTree as ET
from pathlib import PureWindowsPath

import pytest

import archive_amibroker_batches as archive


def source(operation='Optimize', export='Export'):
    root = ET.Element('AmiBroker-Batch', CompactMode='0')
    for action, param in [('LoadProject', r'Z:\Settings\Daily_Project.apx'),
                          ('SetCurrentSymbol', 'ZN'), (operation, ''), (export, r'Z:\Reports\old\ZN.csv'),
                          ('SetCurrentSymbol', 'ZF'), (operation, ''), (export, r'Z:\Reports\old\ZF.csv')]:
        step = ET.SubElement(root, 'Step')
        ET.SubElement(step, 'Action').text = action
        ET.SubElement(step, 'Param').text = param
    return ET.tostring(root, encoding='unicode')


@pytest.mark.parametrize('operation,export,kind', [('Optimize', 'Export', 'Optimization'), ('WalkForward', 'ExportWalkForward', 'WFA')])
def test_archival_batch_preserves_calculations_and_wires_runtime(operation, export, kind):
    xml, config = archive.prepare_batch(source(operation, export), r'Z:\Settings\Daily_Batch.abb', r'Z:\Reports')
    steps = [(s.findtext('Action'), s.findtext('Param')) for s in ET.fromstring(xml)]
    assert [(a,p) for a,p in steps if a in {'SetCurrentSymbol', operation}] == [('SetCurrentSymbol','ZN'),(operation,''),('SetCurrentSymbol','ZF'),(operation,'')]
    assert steps[0][0] == steps[-1][0] == 'ExecuteAndWait'
    assert '-Stage Begin' in steps[0][1]
    assert '-Stage Complete' in steps[-1][1]
    for i, (action, path) in enumerate(steps):
        if action == export:
            assert PureWindowsPath(path).parent == PureWindowsPath(config['staging_dir'])
            assert steps[i+1][0] == 'ExecuteAndWait'
            assert '-Stage Publish' in steps[i+1][1]
    assert config['symbols'] == ['ZN', 'ZF']
    assert config['kind'] == kind
    assert dict(steps)['LoadProject'] == str(PureWindowsPath(config['staging_dir']) / 'project.apx')


def test_batch_names_get_separate_staging():
    _, a = archive.prepare_batch(source(), r'Z:\Settings\Daily_Batch.abb', r'Z:\Reports')
    _, b = archive.prepare_batch(source(), r'Z:\Settings\Intraday_Batch.abb', r'Z:\Reports')
    assert a['staging_dir'] != b['staging_dir']


@pytest.mark.parametrize('bad', [source().replace('<Action>Optimize</Action>', '<Action>Backtest</Action>', 1), source().replace('ZF.csv', 'ZN.csv'), source().replace('>ZF<', '>ZN<')])
def test_reject_unsupported_or_ambiguous_batches(bad):
    with pytest.raises(ValueError):
        archive.prepare_batch(bad, r'Z:\Settings\Daily_Batch.abb', r'Z:\Reports')


def test_refuse_double_wrapping():
    xml, _ = archive.prepare_batch(source(), r'Z:\Settings\Daily_Batch.abb', r'Z:\Reports')
    with pytest.raises(ValueError, match='already|unsupported'):
        archive.prepare_batch(xml, r'Z:\Settings\Daily_Batch.abb', r'Z:\Reports')

# Exercise the real PowerShell helper when a runtime is available (also works on Linux).
import json
import os
from pathlib import Path
import shutil
import subprocess

PWSH = os.environ.get('AMIBROKER_TEST_PWSH') or shutil.which('pwsh') or shutil.which('powershell')


@pytest.fixture
def archive_run(tmp_path):
    if not PWSH:
        pytest.skip('Set AMIBROKER_TEST_PWSH to test the PowerShell runtime')
    project = tmp_path / 'source.apx'
    project.write_text('<AnalysisDoc><General><FormulaPath>Z:\\Strategies\\A &amp; B.afl</FormulaPath><FormulaContent>// saved formula</FormulaContent><Periodicity>0</Periodicity><ChartInterval>86400</ChartInterval><FromDate>2009-01-01</FromDate><ToDate>2019-01-01</ToDate></General></AnalysisDoc>')
    batch = tmp_path / 'batch.abb'
    batch.write_text(source())
    staging = tmp_path / 'staging' / 'daily'
    config = tmp_path / 'config.json'
    config.write_text(json.dumps(dict(schema_version=1, project_path=str(project), batch_path=str(batch), reports_root=str(tmp_path / 'reports'), staging_dir=str(staging), kind='Optimization', symbols=['ZN','ZF'])))

    def run(stage, symbol=None, success=True):
        args = [PWSH, '-NoLogo','-NoProfile','-NonInteractive','-File',str(Path(archive.__file__).with_name('Archive_AmiBroker_Run.ps1')),'-Config',str(config),'-Stage',stage]
        if symbol: args += ['-Symbol',symbol]
        result = subprocess.run(args, capture_output=True, text=True)
        assert (result.returncode == 0) == success, result.stdout + result.stderr
        return result

    def context():
        return json.loads((staging / 'context.json').read_text(encoding='utf-8-sig'))

    return run, context, staging, project


def read_manifest(context):
    return json.loads(Path(context['manifest_path']).read_text(encoding='utf-8-sig'))


def test_runtime_retains_two_runs_and_project_snapshots(archive_run):
    run, context, staging, project = archive_run
    run('Begin')
    first = context()
    run_dir = Path(first['run_dir'])
    assert run_dir.parent.parts[-3:] == ('A & B', 'Daily', 'Optimization')
    assert (run_dir / 'project.apx').read_bytes() == project.read_bytes()
    assert (run_dir / 'formula.afl').read_text() == '// saved formula'
    for symbol in ['ZN','ZF']:
        (staging / f'{symbol}.csv').write_text('Net Profit,Profit Factor\n100,1.5\n')
        run('Publish',symbol)
    run('Complete')
    assert read_manifest(first)['status'] == 'COMPLETE'
    project.write_text(project.read_text().replace('A &amp; B','New Strategy').replace('<Periodicity>0','<Periodicity>8').replace('86400','900'))
    run('Begin')
    assert context()['run_dir'] != first['run_dir']
    assert Path(context()['run_dir']).parent.parts[-3:] == ('New Strategy','Intraday_15m','Optimization')
    assert (run_dir / 'ZN.csv').is_file()
    assert read_manifest(first)['status'] == 'COMPLETE'
    assert b'New Strategy' not in (run_dir / 'project.apx').read_bytes()


def test_runtime_interruption_preserves_unpublished_files(archive_run):
    run, context, staging, _ = archive_run
    run('Begin')
    first = context()
    (staging / 'ZN.csv').write_text('old partial export')
    run('Begin')
    assert read_manifest(first)['status'] == 'INTERRUPTED'
    assert not (staging / 'ZN.csv').exists()
    leftovers = list(staging.parent.glob('daily.previous.*/ZN.csv'))
    assert len(leftovers) == 1
    assert leftovers[0].read_text() == 'old partial export'
    run('Publish','ZN', success=False)
    assert read_manifest(context())['status'] == 'FAILED'


@pytest.mark.parametrize('failure', ['missing', 'empty', 'duplicate', 'incomplete', 'modified'])
def test_runtime_rejects_bad_exports_and_cannot_complete(archive_run, failure):
    run, context, staging, _ = archive_run
    run('Begin')
    if failure == 'missing':
        run('Publish','ZN',success=False)
    elif failure == 'empty':
        (staging / 'ZN.csv').touch()
        run('Publish','ZN',success=False)
    elif failure == 'incomplete':
        run('Complete',success=False)
    else:
        for symbol in ['ZN','ZF']:
            (staging / f'{symbol}.csv').write_text('header\n100\n')
            run('Publish',symbol)
        if failure == 'duplicate':
            (staging / 'ZN.csv').write_text('replacement')
            run('Publish','ZN',success=False)
            assert (Path(context()['run_dir']) / 'ZN.csv').read_text() == 'header\n100\n'
        else:
            (Path(context()['run_dir']) / 'ZN.csv').write_text('modified')
            run('Complete',success=False)
    assert read_manifest(context())['status'] == 'FAILED'
    run('Complete',success=False)


def test_runtime_failed_begin_cannot_reuse_old_context(archive_run):
    run, context, staging, project = archive_run
    run('Begin')
    old = context()
    project.unlink()
    run('Begin',success=False)
    assert not (staging / 'context.json').exists()
    (staging / 'ZN.csv').write_text('new data must not attach to old run')
    run('Publish','ZN',success=False)
    assert not (Path(old['run_dir']) / 'ZN.csv').exists()


def test_runtime_broken_prior_manifest_cannot_reuse_old_context(archive_run):
    run, context, staging, _ = archive_run
    run('Begin')
    Path(context()['manifest_path']).write_text('broken manifest')
    run('Begin', success=False)
    assert not (staging / 'context.json').exists()


def test_default_analysis_reports_stay_with_archived_run(tmp_path):
    import run_amibroker_analysis as runner
    run = tmp_path / 'run_1'
    run.mkdir()
    assert runner.default_output_root(run) == tmp_path / 'Analysis_Reports'
    (run / 'run_manifest.json').write_text('{}')
    assert runner.default_output_root(run) == run / 'Analysis_Reports'


def test_runtime_xml_encoding_and_reserved_strategy_names(archive_run):
    run, context, _, project = archive_run
    xml = project.read_text().replace('A &amp; B', 'Élan 東京').replace('// saved formula', '// café 日本語')
    project.write_bytes(('<?xml version="1.0" encoding="utf-8"?>' + xml).encode('utf-8'))
    run('Begin')
    first = context()
    assert read_manifest(first)['strategy'] == 'Élan 東京'
    assert Path(first['run_dir']).parent.parent.parent.name == 'Élan 東京'
    assert (Path(first['run_dir']) / 'formula.afl').read_text() == '// café 日本語'
    project.write_text(xml.replace('Élan 東京', 'CON'))
    run('Begin')
    assert Path(context()['run_dir']).parent.parent.parent.name.startswith('_CON_')


def test_archive_helper_declares_optional_experiment_audit_contract():
    helper = Path(archive.__file__).with_name('Archive_AmiBroker_Run.ps1').read_text()
    assert "PublishAudit" in helper
    assert "expected_audit_symbols" in helper
    assert "audit_exports" in helper
    assert "experiment_artifacts" in helper


def test_runtime_archives_entry_audit_and_experiment_artifacts(tmp_path):
    if not PWSH:
        pytest.skip('Set AMIBROKER_TEST_PWSH to test the PowerShell runtime')
    project = tmp_path / 'source.apx'
    project.write_text('<AnalysisDoc><General><FormulaPath>Z:\\Strategies\\Demo.afl</FormulaPath><FormulaContent>Buy=True;</FormulaContent><Periodicity>8</Periodicity><ChartInterval>900</ChartInterval><FromDate>2009-01-01</FromDate><ToDate>2019-01-01</ToDate></General></AnalysisDoc>')
    batch = tmp_path / 'batch.abb'; batch.write_text(source())
    matrix = tmp_path / 'matrix.json'; matrix.write_text('{}')
    profile = tmp_path / 'profile.json'; profile.write_text('{}')
    build = tmp_path / 'build_manifest.json'; build.write_text('{}')
    policy = tmp_path / 'policy.afl'; policy.write_text('// policy')
    staging = tmp_path / 'staging'
    config = tmp_path / 'config.json'
    config.write_text(json.dumps({
        'schema_version': 2, 'project_path': str(project), 'batch_path': str(batch),
        'reports_root': str(tmp_path / 'reports'), 'staging_dir': str(staging),
        'kind': 'Optimization', 'symbols': ['ES'], 'audit_symbols': ['ES'],
        'experiment': {'matrix_path': str(matrix), 'source_afl': str(policy),
                       'analysis_profile': str(profile), 'build_manifest': str(build),
                       'shared_policy': str(policy)},
    }))
    helper = Path(archive.__file__).with_name('Archive_AmiBroker_Run.ps1')
    def invoke(stage):
        args = [PWSH, '-NoProfile', '-File', str(helper), '-Config', str(config), '-Stage', stage]
        if stage in {'Publish', 'PublishAudit'}: args += ['-Symbol', 'ES']
        result = subprocess.run(args, capture_output=True, text=True)
        assert result.returncode == 0, result.stdout + result.stderr
    invoke('Begin')
    context = json.loads((staging / 'context.json').read_text(encoding='utf-8-sig'))
    (staging / 'ES.csv').write_text('Net Profit,Profit Factor,# Trades\n100,1.2,10\n')
    (staging / 'entry_audit' / 'ES.csv').write_text('DateTime,TimeNum,Buy,Short\n2018-01-01 11:00,110000,1,0\n')
    invoke('Publish'); invoke('PublishAudit'); invoke('Complete')
    manifest = json.loads(Path(context['manifest_path']).read_text(encoding='utf-8-sig'))
    assert manifest['status'] == 'COMPLETE'
    assert manifest['audit_exports'][0]['file'] == 'entry_audit/ES.csv'
    assert {item['name'] for item in manifest['experiment_artifacts']} == {
        'matrix_path', 'source_afl', 'analysis_profile', 'build_manifest', 'shared_policy'
    }
