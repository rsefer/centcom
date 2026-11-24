import assert from 'node:assert/strict';
import { test } from 'node:test';
import { formatUptime, selfHealStatus } from './selfheal-status.js';

test('formats uptime at day, hour and minute granularity', () => {
	assert.equal(formatUptime(93784), '1d 2h');
	assert.equal(formatUptime(3700), '1h 1m');
	assert.equal(formatUptime(59), '0m');
});

test('flags hosts that rebooted within 24 hours', () => {
	assert.equal(selfHealStatus({ uptime_seconds: 86399, events: [] }).state, 'rebooted');
	assert.equal(selfHealStatus({ uptime_seconds: 86400, events: [] }).state, 'ok');
});

test('flags a suppressed reboot as needing attention', () => {
	const events = ['2026-11-24 07:00:00 reboot suppressed: 3 consecutive attempts'];
	assert.equal(selfHealStatus({ uptime_seconds: 999999, events }).state, 'attention');
});

test('reports missing, unreadable data as unknown', () => {
	assert.equal(selfHealStatus(undefined).state, 'unknown');
	assert.equal(selfHealStatus({ error: 'denied' }).detail, 'Self-heal log unreadable');
});
