export function statusSummary(status, applicationStatus, services) {
	const systems = status
		? `${status.hosts.filter(({ error }) => !error).length} of ${status.hosts.length} systems reachable`
		: 'Checking systems';
	const applications = !applicationStatus
		? 'Checking applications'
		: applicationStatus.error
			? 'Application checks unavailable'
			: `${services.filter((service) => applicationStatus.applications[service]?.ok).length} of ${services.length} applications reachable`;
	return `${systems} · ${applications}`;
}
