import assert from 'node:assert/strict';
import { test } from 'node:test';
import { backupStatus } from './backup-status.js';

const now = Date.parse('2026-11-21T12:00:00Z');
const backup = (time) => ({ last_success: new Date(time).toISOString(), offsite: true });

test('marks backups overdue only after 48 hours', () => {
	assert.equal(backupStatus(backup(now - 48 * 60 * 60 * 1000), now).state, 'current');
	assert.equal(backupStatus(backup(now - 48 * 60 * 60 * 1000 - 1), now).state, 'overdue');
});

test('reports missing, invalid, and future backup timestamps as unknown', () => {
	assert.equal(backupStatus({}, now).state, 'unknown');
	assert.equal(backupStatus({ error: 'unreadable' }, now).detail, 'Backup marker unreadable');
	assert.equal(backupStatus({ last_success: 'invalid' }, now).state, 'unknown');
	assert.match(backupStatus(backup(now + 1), now).detail, /future/);
});
