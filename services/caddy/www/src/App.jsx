import { useEffect, useMemo, useState, useSyncExternalStore } from 'react';
import { backupStatus } from './backup-status.js';
import { statusSummary } from './status-summary.js';
import './service-grid.css';
import registry from './services.json';
import {
	Box,
	Badge,
	Card,
	Container,
	Dialog,
	Flex,
	Grid,
	Heading,
	Kbd,
	Link,
	Separator,
	Text,
	TextField,
	Theme,
	VisuallyHidden,
} from '@radix-ui/themes';

// Hosts and services come from services.toml via `make generate`.
const configured = (group) => registry.services.filter((service) => service.group === group);

const PRIMARY_SERVICES = configured('primary');

const HEALTH_SERVICES = [
	{ title: 'Tailscale Admin', icon: 'images/tailscale.svg', href: 'https://login.tailscale.com/admin/machines' },
	...configured('health'),
	{ title: 'Live status', icon: 'images/bell-dot.svg', href: '/live-status', status: true },
];

const SERVICES = [...PRIMARY_SERVICES, ...HEALTH_SERVICES];
const STATUS_HOSTS = registry.hosts;
const STATUS_SERVICES = SERVICES.filter(({ service }) => service).map(({ service }) => service);

async function fetchHostStatus(host) {
	const controller = new AbortController();
	const timeout = setTimeout(() => controller.abort(), 15000);
	try {
		const response = await fetch(`/status/${host}`, { signal: controller.signal, cache: 'no-store' });
		if (!response.ok) {
			throw new Error(`HTTP ${response.status}`);
		}
		const data = await response.json();
		if (data.host !== host && !(host === 'northcom' && data.host === 'centcom')) {
			throw new Error('Unexpected status host');
		}
		return { host: data.host, data };
	} catch (error) {
		return { host, error: error.message };
	} finally {
		clearTimeout(timeout);
	}
}

async function fetchApplications() {
	const controller = new AbortController();
	const timeout = setTimeout(() => controller.abort(), 15000);
	try {
		const response = await fetch('/status/applications', { signal: controller.signal, cache: 'no-store' });
		if (!response.ok) {
			throw new Error(`HTTP ${response.status}`);
		}
		const data = await response.json();
		if (!data.applications || typeof data.applications !== 'object') {
			throw new Error('Invalid application status');
		}
		return { applications: data.applications };
	} catch (error) {
		return { error: error.message };
	} finally {
		clearTimeout(timeout);
	}
}

function useHostStatus() {
	const [result, setResult] = useState(null);

	useEffect(() => {
		let active = true;
		async function refresh() {
			const hosts = await Promise.all(STATUS_HOSTS.map(fetchHostStatus));
			if (active) {
				setResult({
					hosts: hosts[0].host === 'centcom' ? hosts.slice(0, 1) : hosts,
					checkedAt: new Date(),
				});
			}
		}
		refresh();
		const timer = setInterval(refresh, 30000);
		return () => {
			active = false;
			clearInterval(timer);
		};
	}, []);

	return result;
}

function useApplicationStatus() {
	const [result, setResult] = useState(null);
	useEffect(() => {
		let active = true;
		async function refresh() {
			const next = await fetchApplications();
			if (active) {
				setResult({ ...next, checkedAt: new Date() });
			}
		}
		refresh();
		const timer = setInterval(refresh, 30000);
		return () => {
			active = false;
			clearInterval(timer);
		};
	}, []);
	return result;
}

function StatusCard({ host, data, error }) {
	const backup = backupStatus(data?.backup);

	return (
		<Card size="2">
			<Flex justify="between" align="center" gap="2">
				<Text weight="medium">{host}</Text>
				<Badge color={error ? 'red' : 'green'}>{error ? 'Agent unreachable' : 'Agent reachable'}</Badge>
			</Flex>
			{error ? (
				<Text as="p" size="2" color="gray" mt="2">Status unavailable: {error}</Text>
			) : (
				<Flex direction="column" gap="1" mt="2">
					<Flex align="center" gap="2" wrap="wrap">
						<Text size="2">Last backup: {backup.detail}</Text>
						<Badge color={backup.state === 'overdue' ? 'red' : backup.state === 'current' ? 'green' : 'amber'}>
							{backup.state === 'overdue' ? 'Overdue (>48h)' : backup.state === 'current' ? 'Current' : 'Unknown'}
						</Badge>
					</Flex>
					{Object.entries(data.dns).map(([name, result]) => (
						<Text key={name} size="2">
							{name === 'primary' ? 'Primary' : 'Secondary'} DNS: {result.ok ? `responding${result.proxy ? ` (${result.proxy} proxy)` : ''}` : `failed (${result.error})`}
						</Text>
					))}
				</Flex>
			)}
		</Card>
	);
}

function OperationalStatus({ status, applicationStatus }) {
	return (
		<Box mt="7">
			<Flex justify="between" align="center" wrap="wrap" gap="2" mb="3">
				<Heading as="h2" size="5">Live status</Heading>
				<Link href={serviceHref({ service: 'beszel' })} size="2">Host metrics in Beszel</Link>
			</Flex>
			<Text as="p" size="2" color="gray" mb="3">
				{status ? `Hosts checked ${status.checkedAt.toLocaleTimeString()}; refreshes every 30 seconds.` : 'Checking hosts...'}
			</Text>
			<Text as="p" size="2" color="gray" mb="3">
				Agent reachability does not indicate application health.
			</Text>
			{status && (
				<Grid columns={{ initial: '1', md: '3' }} gap="3">
					{status.hosts.map((host) => <StatusCard key={host.host} {...host} />)}
				</Grid>
			)}
			<Heading as="h3" size="4" mt="5" mb="2">Application reachability</Heading>
			<Text as="p" size="2" color="gray" mb="3">
				HTTPS responses are checked through the active proxy. Reachable does not guarantee application health.
				{applicationStatus && ` Checked ${applicationStatus.checkedAt.toLocaleTimeString()}; refreshes every 30 seconds.`}
			</Text>
			{!applicationStatus ? (
				<Text size="2" color="gray">Checking applications...</Text>
			) : applicationStatus.error ? (
				<Text size="2" color="red">Application checks unavailable: {applicationStatus.error}</Text>
			) : (
				<Grid columns={{ initial: '1', sm: '2', md: '3' }} gap="2">
					{SERVICES.filter(({ service }) => service).map(({ title, service }) => {
						const result = applicationStatus.applications[service];
						return (
							<Card key={service} size="1">
								<Flex justify="between" align="center" gap="2">
									<Text size="2">{title}</Text>
									<Badge color={!result ? 'amber' : result.ok ? 'green' : 'red'}>
										{!result ? 'Unknown' : result.ok ? `Reachable (HTTP ${result.status})` : 'Check failed'}
									</Badge>
								</Flex>
								{result && !result.ok && <Text as="p" size="1" color="red" mt="1">{result.error}</Text>}
							</Card>
						);
					})}
				</Grid>
			)}
		</Box>
	);
}

function serviceHref({ href, service, path = '' }) {
	return href ?? `${window.location.protocol}//${service}.${window.location.hostname}${path}`;
}

const darkQuery = window.matchMedia('(prefers-color-scheme: dark)');

function usePrefersDark() {
	return useSyncExternalStore(
		(onChange) => {
			darkQuery.addEventListener('change', onChange);
			return () => darkQuery.removeEventListener('change', onChange);
		},
		() => darkQuery.matches,
	);
}

function ServiceIcon({ icon, title }) {
	return <img src={icon} alt="" width="24" height="24" style={{ display: 'block' }} />;
}

function ServiceCard({ item, summary }) {
	return (
		<Card asChild size="2">
			<a href={serviceHref(item)} style={{ textDecoration: 'none' }}>
				<Flex align="center" gap="3">
					<ServiceIcon icon={item.icon} title={item.title} />
					<Flex direction="column" gap="1">
						<Text size="3" weight="medium">{item.title}</Text>
						{item.status && <Text size="2" color="gray">{summary}</Text>}
					</Flex>
				</Flex>
			</a>
		</Card>
	);
}

function CommandPalette({ open, onOpenChange }) {
	const [query, setQuery] = useState('');

	const matches = useMemo(() => {
		const q = query.trim().toLowerCase();
		return SERVICES.filter(({ title }) => title.toLowerCase().includes(q));
	}, [query]);

	// Reset the query whenever the palette closes
	function handleOpenChange(nextOpen) {
		if (!nextOpen) {
			setQuery('');
		}
		onOpenChange(nextOpen);
	}

	function handleSearchKeyDown(event) {
		if (event.key === 'Enter' && matches.length === 1) {
			event.preventDefault();
			window.location.assign(serviceHref(matches[0]));
		}
	}

	return (
		<Dialog.Root open={open} onOpenChange={handleOpenChange}>
			<Dialog.Content size="2" maxWidth="32rem" aria-describedby={undefined}>
				<Dialog.Title size="4">Find a service</Dialog.Title>
				<TextField.Root
					placeholder="Search services..."
					autoFocus
					value={query}
					onChange={(event) => setQuery(event.target.value)}
					onKeyDown={handleSearchKeyDown}
					aria-label="Search services"
				/>
				<Flex direction="column" gap="1" mt="3" style={{ maxHeight: '18rem', overflowY: 'auto' }}>
					{matches.map((item) => (
						<Link
							key={item.title}
							href={serviceHref(item)}
							size="3"
							color="gray"
							highContrast
							style={{ padding: 'var(--space-2) var(--space-3)', borderRadius: 'var(--radius-3)' }}
						>
							{item.title}
						</Link>
					))}
					{matches.length === 0 && (
						<Text size="2" color="gray">No services match your search.</Text>
					)}
				</Flex>
				<VisuallyHidden>
					<Text role="status" aria-live="polite">
						{matches.length === 0 ? 'No services match your search.' : ''}
					</Text>
				</VisuallyHidden>
				<Text as="p" size="1" color="gray" mt="3">
					Press <Kbd>Enter</Kbd> to open a single match. Press <Kbd>Esc</Kbd> to close.
				</Text>
			</Dialog.Content>
		</Dialog.Root>
	);
}

export default function App() {
	const prefersDark = usePrefersDark();
	const [paletteOpen, setPaletteOpen] = useState(false);
	const isStatusPage = window.location.pathname === '/live-status';
	const status = useHostStatus();
	const applicationStatus = useApplicationStatus();
	const summary = statusSummary(status, applicationStatus, STATUS_SERVICES);

	useEffect(() => {
		function handleKeyDown(event) {
			if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
				event.preventDefault();
				setPaletteOpen(true);
			}
		}
		document.addEventListener('keydown', handleKeyDown);
		return () => document.removeEventListener('keydown', handleKeyDown);
	}, []);

	return (
		<Theme
			appearance={prefersDark ? 'dark' : 'light'}
			accentColor="blue"
			grayColor="slate"
			radius="medium"
		>
			<Box minHeight="100dvh" py="7" px="4" style={{ background: 'var(--gray-2)' }}>
				<Container size="3">
					<Heading as="h1" size="8" align="center" mb="6">CENTCOM</Heading>
					{isStatusPage ? (
						<>
							<Link href="/" size="2">Back to services</Link>
							<OperationalStatus status={status} applicationStatus={applicationStatus} />
						</>
					) : (
						<>
							<Heading as="h2" size="1" color="gray" mb="3" style={{ textTransform: 'uppercase', letterSpacing: '0.08em' }}>Apps</Heading>
							<Grid className="service-grid" columns={{ initial: '1', sm: '2' }} gap="3">
								{PRIMARY_SERVICES.map((item) => (
									<ServiceCard key={item.title} item={item} />
								))}
							</Grid>
							<Separator size="4" my="5" style={{ width: '100%' }} />
							<Heading as="h2" size="1" color="gray" mb="3" style={{ textTransform: 'uppercase', letterSpacing: '0.08em' }}>System</Heading>
							<Grid className="service-grid" columns={{ initial: '1', sm: '2' }} gap="3">
								{HEALTH_SERVICES.map((item) => (
									<ServiceCard key={item.title} item={item} summary={summary} />
								))}
							</Grid>
						</>
					)}
					<Flex justify="center" mt="6">
						<Text size="2" color="gray">
							Press <Kbd>⌘K</Kbd> to search services
						</Text>
					</Flex>
				</Container>
			</Box>
			<CommandPalette open={paletteOpen} onOpenChange={setPaletteOpen} />
		</Theme>
	);
}
