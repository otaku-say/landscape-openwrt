import copy
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

ROOT = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location('compose_check', ROOT / 'scripts/test-compose.py')
compose = importlib.util.module_from_spec(spec)
spec.loader.exec_module(compose)


def fixture(ipv4='10.10.66', ipv6='fd10:10:66'):
    resolved = {
        'LAND_ROOT_PASSWORD': 'test$with# spaces', 'TZ': 'Etc/UTC',
        'LAND_DNS_ADDR': '8.8.8.8', 'LAND_REDIRECT_LOG_LEVEL': 'INFO',
        'EDGE_BRIDGE_NAME': 'br-custom', 'EDGE_NETWORK_NAME': 'custom-network',
        'LANDSCAPE_SOCKET_DIR': '/tmp/custom/unix_link',
        'CONFIG_VOLUME_NAME': 'custom-config', 'DROPBEAR_VOLUME_NAME': 'custom-dropbear',
        'EDGE_IPV4_SUBNET': f'{ipv4}.0/24', 'EDGE_IPV4_GATEWAY': f'{ipv4}.1', 'EDGE_IPV4_ADDRESS': f'{ipv4}.2',
        'EDGE_IPV6_SUBNET': f'{ipv6}::/64', 'EDGE_IPV6_GATEWAY': f'{ipv6}::1', 'EDGE_IPV6_ADDRESS': f'{ipv6}::2',
        **compose.PORT_DEFAULTS,
    }
    environment = {key: resolved[key].replace('$', '$$') for key in (
        'LAND_ROOT_PASSWORD', 'TZ', 'LAND_DNS_ADDR', 'LAND_REDIRECT_LOG_LEVEL', *compose.PORT_DEFAULTS,
    )}
    config = {
        'services': {'openwrt': {
            'environment': environment,
            'networks': {'edge': {'ipv4_address': resolved['EDGE_IPV4_ADDRESS'], 'ipv6_address': resolved['EDGE_IPV6_ADDRESS']}},
            'volumes': [
                {'type': 'bind', 'source': resolved['LANDSCAPE_SOCKET_DIR'], 'target': '/ld_unix_link', 'read_only': True},
                {'type': 'volume', 'source': 'landscape-openwrt-config', 'target': '/etc/config'},
                {'type': 'volume', 'source': 'landscape-openwrt-dropbear', 'target': '/etc/dropbear'},
            ],
        }},
        'networks': {'edge': {
            'name': resolved['EDGE_NETWORK_NAME'], 'enable_ipv6': True,
            'driver_opts': {
                'com.docker.network.bridge.name': resolved['EDGE_BRIDGE_NAME'],
                'com.docker.network.bridge.enable_ip_masquerade': 'true',
                'com.docker.network.bridge.gateway_mode_ipv4': 'nat-unprotected',
                'com.docker.network.bridge.gateway_mode_ipv6': 'nat-unprotected',
            },
            'ipam': {'config': [
                {'subnet': resolved[f'EDGE_IPV{version}_SUBNET'], 'gateway': resolved[f'EDGE_IPV{version}_GATEWAY']}
                for version in (4, 6)
            ]},
        }},
        'volumes': {
            'landscape-openwrt-config': {'name': resolved['CONFIG_VOLUME_NAME']},
            'landscape-openwrt-dropbear': {'name': resolved['DROPBEAR_VOLUME_NAME']},
        },
    }
    return config, resolved


class ComposeValidationTest(unittest.TestCase):
    def test_current_and_all_rfc1918_ranges(self):
        for prefix in ('10.10.66', '10.66.66', '172.30.66', '192.168.66'):
            with self.subTest(prefix=prefix):
                compose.validate_config(*fixture(ipv4=prefix))

    def test_non_rfc1918_ranges_are_rejected(self):
        for prefix in ('172.15.66', '172.32.66', '172.66.66', '100.64.0', '127.0.0', '169.254.0', '192.0.2', '8.8.8'):
            with self.subTest(prefix=prefix), self.assertRaises(AssertionError):
                compose.validate_config(*fixture(ipv4=prefix))

    def test_ipv6_must_be_ula(self):
        for prefix in ('2001:db8:66', 'fe80', 'ff02'):
            with self.subTest(prefix=prefix), self.assertRaises(AssertionError):
                compose.validate_config(*fixture(ipv6=prefix))

    def test_subnet_family_boundaries_and_host_bits(self):
        for key, value in (
            ('EDGE_IPV4_SUBNET', '0.0.0.0/0'), ('EDGE_IPV4_SUBNET', '10.0.0.0/7'),
            ('EDGE_IPV4_SUBNET', 'fd10:10:66::/64'), ('EDGE_IPV6_SUBNET', '10.10.66.0/24'),
            ('EDGE_IPV6_SUBNET', '::/0'), ('EDGE_IPV4_SUBNET', '10.10.66.1/24'),
            ('EDGE_IPV6_SUBNET', 'fd10:10:66::1/64'), ('EDGE_IPV4_SUBNET', 'invalid'),
        ):
            config, resolved = fixture()
            resolved[key] = value
            with self.subTest(key=key, value=value), self.assertRaises((AssertionError, ValueError)):
                compose.validate_config(config, resolved)

    def test_invalid_endpoints_and_gateway_collisions(self):
        for version, suffix, value in (
            (4, 'GATEWAY', '10.10.67.1'), (4, 'ADDRESS', '10.10.67.2'),
            (4, 'ADDRESS', '10.10.66.1'), (4, 'ADDRESS', '10.10.66.0'),
            (4, 'GATEWAY', '10.10.66.0'), (4, 'ADDRESS', '10.10.66.255'),
            (4, 'GATEWAY', '10.10.66.255'), (4, 'ADDRESS', 'fd10:10:66::2'),
            (6, 'GATEWAY', 'fd10:10:67::1'), (6, 'ADDRESS', 'fd10:10:67::2'),
            (6, 'ADDRESS', 'fd10:10:66::1'), (6, 'ADDRESS', 'fd10:10:66::'),
            (6, 'GATEWAY', 'fd10:10:66::'), (6, 'GATEWAY', '10.10.66.1'),
        ):
            config, resolved = fixture()
            resolved[f'EDGE_IPV{version}_{suffix}'] = value
            if suffix == 'GATEWAY':
                config['networks']['edge']['ipam']['config'][0 if version == 4 else 1]['gateway'] = value
            else:
                config['services']['openwrt']['networks']['edge'][f'ipv{version}_address'] = value
            with self.subTest(version=version, suffix=suffix, value=value), self.assertRaises(AssertionError):
                compose.validate_config(config, resolved)

    def test_ipam_order_does_not_matter(self):
        config, resolved = fixture()
        config['networks']['edge']['ipam']['config'].reverse()
        compose.validate_config(config, resolved)

    def test_both_ipam_families_are_required(self):
        for duplicate in (False, True):
            config, resolved = fixture()
            entries = config['networks']['edge']['ipam']['config']
            if duplicate:
                entries[1] = copy.deepcopy(entries[0])
            else:
                entries.pop()
            with self.subTest(duplicate=duplicate), self.assertRaises(AssertionError):
                compose.validate_config(config, resolved)

    def test_rendered_network_must_match_resolved_variables(self):
        for version in (4, 6):
            for field in ('subnet', 'gateway', 'address'):
                config, resolved = fixture()
                entry = config['networks']['edge']['ipam']['config'][0 if version == 4 else 1]
                if field == 'address':
                    config['services']['openwrt']['networks']['edge'][f'ipv{version}_address'] = '10.10.66.3' if version == 4 else 'fd10:10:66::3'
                else:
                    entry[field] = {
                        (4, 'subnet'): '10.10.67.0/24', (6, 'subnet'): 'fd10:10:67::/64',
                        (4, 'gateway'): '10.10.66.3', (6, 'gateway'): 'fd10:10:66::3',
                    }[(version, field)]
                with self.subTest(version=version, field=field), self.assertRaises(AssertionError):
                    compose.validate_config(config, resolved)

    def test_equivalent_ipv6_notation_is_accepted(self):
        config, resolved = fixture()
        config['services']['openwrt']['networks']['edge']['ipv6_address'] = 'fd10:0010:0066:0000:0000:0000:0000:0002'
        compose.validate_config(config, resolved)

    def test_volume_names_must_match_and_remain_separate(self):
        for collision in (False, True):
            config, resolved = fixture()
            config['volumes']['landscape-openwrt-dropbear']['name'] = resolved['CONFIG_VOLUME_NAME']
            if collision:
                resolved['DROPBEAR_VOLUME_NAME'] = resolved['CONFIG_VOLUME_NAME']
            with self.subTest(collision=collision), self.assertRaises(AssertionError):
                compose.validate_config(config, resolved)

    def test_socket_must_not_be_created_or_writable(self):
        for changes in ({'read_only': False}, {'bind': {'create_host_path': True}}, {'source': '/wrong/socket'}):
            config, resolved = fixture()
            config['services']['openwrt']['volumes'][0].update(changes)
            with self.subTest(changes=changes), self.assertRaises(AssertionError):
                compose.validate_config(config, resolved)

    def test_no_host_publishing_or_binding(self):
        for key in ('ports', 'expose', 'LAN_BIND_IP'):
            config, resolved = fixture()
            service = config['services']['openwrt']
            if key == 'LAN_BIND_IP':
                service['environment'][key] = '127.0.0.1'
            else:
                service[key] = []
            with self.subTest(key=key), self.assertRaises(AssertionError):
                compose.validate_config(config, resolved)

    def test_nat_and_ipv6_must_remain_enabled(self):
        for key in ('enable_ipv6', 'enable_ip_masquerade', 'gateway_mode_ipv4', 'gateway_mode_ipv6'):
            config, resolved = fixture()
            edge = config['networks']['edge']
            if key == 'enable_ipv6':
                edge[key] = False
            else:
                edge['driver_opts']['com.docker.network.bridge.' + key] = 'false' if key == 'enable_ip_masquerade' else 'nat'
            with self.subTest(key=key), self.assertRaises(AssertionError):
                compose.validate_config(config, resolved)

    def test_configurable_ports_and_empty_defaults(self):
        for ports in ({}, dict(zip(compose.PORT_DEFAULTS, ('18080', '18443', '12222'))), dict.fromkeys(compose.PORT_DEFAULTS, '')):
            config, resolved = fixture()
            resolved.update(ports)
            for key, default in compose.PORT_DEFAULTS.items():
                config['services']['openwrt']['environment'][key] = resolved[key] or default
            with self.subTest(ports=ports):
                compose.validate_config(config, resolved)

    def test_invalid_bridge_names(self):
        for name in ('x' * 16, '\u754c' * 6, '.', '..', 'br/edge', 'br:edge', 'br edge', ''):
            config, resolved = fixture()
            resolved['EDGE_BRIDGE_NAME'] = name
            config['networks']['edge']['driver_opts']['com.docker.network.bridge.name'] = name
            with self.subTest(name=name), self.assertRaises(AssertionError):
                compose.validate_config(config, resolved)

    def test_example_must_keep_password_placeholder(self):
        with patch.object(compose.Path, 'read_text', return_value="LAND_ROOT_PASSWORD='not-a-placeholder'\n"):
            with patch.object(compose, 'check_case') as check:
                with self.assertRaisesRegex(AssertionError, 'placeholder'):
                    compose.main()
                check.assert_not_called()

    def test_password_mismatch_does_not_print_values(self):
        config, resolved = fixture()
        config['services']['openwrt']['environment']['LAND_ROOT_PASSWORD'] = 'incorrect-value'
        with self.assertRaises(AssertionError) as error:
            compose.validate_config(config, resolved)
        self.assertNotIn(resolved['LAND_ROOT_PASSWORD'], str(error.exception))
        self.assertNotIn('incorrect-value', str(error.exception))
