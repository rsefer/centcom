import assert from 'node:assert/strict';
import { test } from 'node:test';
import { statusSummary } from './status-summary.js';

const services = ['rss', 'pihole', 'gym'];

test('summarizes reachable systems and applications, including failed and missing checks', () => {
	const status = { hosts: [{}, { error: 'unreachable' }, {}] };
	const applications = { applications: { rss: { ok: true }, pihole: { ok: false } } };
	assert.equal(statusSummary(status, applications, services),
		'2 of 3 systems reachable · 1 of 3 applications reachable');
});

test('shows loading and unavailable checks without inventing counts', () => {
	assert.equal(statusSummary(null, null, services), 'Checking systems · Checking applications');
	assert.equal(statusSummary({ hosts: [{}] }, { error: 'unavailable' }, services),
		'1 of 1 systems reachable · Application checks unavailable');
});
