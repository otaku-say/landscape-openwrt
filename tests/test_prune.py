import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('prune', Path(__file__).parents[1] / 'scripts/plan-prune.py')
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)


def pkg(depends='', **extra):
    return {'Version': '1', 'Depends': depends, **extra}


class PruneTest(unittest.TestCase):
    def test_removes_only_exclusive_dependencies_leaf_first(self):
        packages = {'luci-app-passwall': pkg('shared'), 'shared': pkg(),
                    'luci-app-openclash': pkg('ruby, shared'), 'ruby': pkg('libruby'),
                    'libruby': pkg(), 'unrelated': pkg()}
        result = p.make_plan(packages)['removed']
        self.assertEqual(result, ['luci-app-openclash', 'ruby', 'libruby'])

    def test_preserves_optional_core_and_its_dependencies(self):
        packages = {'luci-app-passwall': pkg(), 'luci-app-homeproxy': pkg('sing-box'),
                    'sing-box': pkg('tun'), 'tun': pkg()}
        self.assertEqual(p.make_plan(packages)['removed'], ['luci-app-homeproxy'])

    def test_preserves_firewall_and_runtime_shell(self):
        packages = {'luci-app-passwall': pkg(), 'luci-app-openclash': pkg('bash'),
                    'luci-app-homeproxy': pkg('firewall4'), 'bash': pkg(), 'firewall4': pkg()}
        self.assertEqual(set(p.make_plan(packages)['removed']),
                         {'luci-app-openclash', 'luci-app-homeproxy'})

    def test_resolves_virtual_dependencies_and_versions(self):
        packages = {'luci-app-passwall': pkg('core-any (>= 1)'),
                    'luci-app-openclash': pkg('shared-core'),
                    'shared-core': pkg(Provides='core-any')}
        self.assertEqual(p.make_plan(packages)['removed'], ['luci-app-openclash'])

    def test_rejects_breaking_retained_package(self):
        packages = {'luci-app-passwall': pkg('momo'), 'momo': pkg()}
        with self.assertRaises(ValueError):
            p.make_plan(packages)

    def test_rejects_missing_dependencies(self):
        with self.assertRaises(ValueError):
            p.make_plan({'luci-app-passwall': pkg('missing')})

    def test_rejects_upstream_without_passwall(self):
        with self.assertRaises(ValueError):
            p.make_plan({})

    def test_removes_all_translations(self):
        packages = {'luci-app-passwall': pkg(), 'luci-app-homeproxy': pkg(),
                    'luci-i18n-homeproxy-zh-cn': pkg('luci-app-homeproxy'),
                    'luci-i18n-homeproxy-de': pkg('luci-app-homeproxy')}
        result = p.make_plan(packages)['removed']
        self.assertEqual(result[-1], 'luci-app-homeproxy')
        self.assertEqual(len(result), 3)
