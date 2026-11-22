const BACKUP_MAX_AGE_MS = 48 * 60 * 60 * 1000;

export function backupStatus(backup, now = Date.now()) {
	if (backup?.error) {
		return { state: 'unknown', detail: 'Backup marker unreadable' };
	}
	if (!backup?.last_success) {
		return { state: 'unknown', detail: 'No successful backup recorded yet' };
	}
	const timestamp = Date.parse(backup.last_success);
	if (!Number.isFinite(timestamp)) {
		return { state: 'unknown', detail: 'Invalid backup timestamp' };
	}
	if (timestamp > now) {
		return { state: 'unknown', detail: 'Backup timestamp is in the future; check clocks' };
	}
	return {
		state: now - timestamp > BACKUP_MAX_AGE_MS ? 'overdue' : 'current',
		detail: `${new Date(timestamp).toLocaleString()} (${backup.offsite ? 'off-site' : 'local only'})`,
	};
}
