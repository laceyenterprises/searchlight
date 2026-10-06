"""Complete the committed lighthouse fixture matrix with CI-only smoke budgets."""
import json
import shutil
import tempfile
from pathlib import Path

import yaml

from sew.catalog import module_root
from sew.runner import SuiteRunner


def run_battery(source=None):
    with tempfile.TemporaryDirectory(prefix='searchlight-fixture-') as temporary:
        root = Path(temporary)
        assets = root / 'assets'
        # Preserve every task, fixture, arm and grade; only raise aggregate budgets
        # in a disposable copy so the normal operator smoke limits stay intact.
        for name in ('catalogs', 'fixtures', 'config', 'tasks'):
            shutil.copytree((source or module_root()) / name, assets / name,
                            ignore=shutil.ignore_patterns('__pycache__'))
        manifest = assets / 'catalogs/lighthouse/suite.yaml'
        suite = yaml.safe_load(manifest.read_text())
        suite['budgets'].update(max_provider_calls=10000, max_provider_result_chars=10000000,
                                max_total_tokens=10000000)
        manifest.write_text(yaml.safe_dump(suite))
        result = SuiteRunner(module_base=assets, state_root=root / 'state').run(
            'lighthouse', mode='fixture', resume=False)
        if (result['remaining_cells'] or result['completed_cells'] != result['total_cells']
                or not result['status_counts'].get('succeeded') or result['stopped_reason']
                or set(result['status_counts']) - {'succeeded', 'not_applicable'}):
            raise RuntimeError('offline fixture battery did not complete successfully')
        return {key: result[key] for key in ('completed_cells', 'total_cells', 'status_counts')}


if __name__ == '__main__':
    print(json.dumps(run_battery(), indent=2))
