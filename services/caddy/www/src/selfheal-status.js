const RECENT_REBOOT_MS = 24 * 60 * 60 * 1000;

export function formatUptime(seconds) {
	const days = Math.floor(seconds / 86400);
	const hours = Math.floor((seconds % 86400) / 3600);
	const minutes = Math.floor((seconds % 3600) / 60);
	if (days) return `${days}d ${hours}h`;
	if (hours) return `${hours}h ${minutes}m`;
	return `${minutes}m`;
}

export function selfHealStatus(selfheal) {
	const events = selfheal?.events ?? [];
	if (selfheal?.error) {
		return { state: 'unknown', detail: 'Self-heal log unreadable', events };
	}
	if (!Number.isFinite(selfheal?.uptime_seconds)) {
		return { state: 'unknown', detail: 'Uptime unavailable', events };
	}
	const detail = `Up ${formatUptime(selfheal.uptime_seconds)}`;
	if (events.at(-1)?.includes('reboot suppressed')) {
		return { state: 'attention', detail, events };
	}
	if (selfheal.uptime_seconds * 1000 < RECENT_REBOOT_MS) {
		return { state: 'rebooted', detail, events };
	}
	return { state: 'ok', detail, events };
}
