import { useEffect, useMemo, useState, useSyncExternalStore } from 'react';
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
	Text,
	TextField,
	Theme,
	VisuallyHidden,
} from '@radix-ui/themes';

const SERVICES = [
	{ title: 'Home Assistant', icon: 'images/home-assistant.svg', service: 'ha' },
	{ title: 'Pi-hole', icon: 'images/pi-hole.svg', service: 'pihole', path: '/admin/' },
	{ title: 'Go Links', icon: 'images/square-up-right.svg', service: 'go' },
	{ title: 'FreshRSS', icon: 'images/freshrss.svg', service: 'rss' },
	{ title: 'Excalidraw', icon: 'images/excalidraw.svg', service: 'draw' },
	{ title: 'OpenGym', icon: 'images/opengym.svg', service: 'gym' },
	{ title: 'Tailscale Admin', icon: 'images/tailscale.svg', href: 'https://login.tailscale.com/admin/machines' },
	{ title: 'Beszel', icon: 'images/beszel.svg', service: 'beszel' },
];

const STATUS_HOSTS = ['northcom', 'southcom', 'eastcom'];

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

function StatusCard({ host, data, error }) {
	const backup = data?.backup;
	const lastBackup = backup?.last_success
		? `${new Date(backup.last_success).toLocaleString()} (${backup.offsite ? 'off-site' : 'local only'})`
		: backup?.error ? 'Marker unreadable' : 'No successful backup recorded yet';

	return (
		<Card size="2">
			<Flex justify="between" align="center" gap="2">
				<Text weight="medium">{host}</Text>
				<Badge color={error ? 'red' : 'green'}>{error ? 'Unavailable' : 'Agent reachable'}</Badge>
			</Flex>
			{error ? (
				<Text as="p" size="2" color="gray" mt="2">Status unavailable: {error}</Text>
			) : (
				<Flex direction="column" gap="1" mt="2">
					<Text size="2">Last backup: {lastBackup}</Text>
					{Object.entries(data.dns).map(([name, result]) => (
						<Text key={name} size="2">
							{name === 'primary' ? 'Primary' : 'Secondary'} DNS: {result.ok ? 'responding' : `failed (${result.error})`}
						</Text>
					))}
				</Flex>
			)}
		</Card>
	);
}

function OperationalStatus() {
	const status = useHostStatus();
	return (
		<Box mt="7">
			<Flex justify="between" align="center" wrap="wrap" gap="2" mb="3">
				<Heading as="h2" size="5">Live status</Heading>
				<Link href={serviceHref({ service: 'beszel' })} size="2">Host metrics in Beszel</Link>
			</Flex>
			<Text as="p" size="2" color="gray" mb="3">
				{status ? `Checked ${status.checkedAt.toLocaleTimeString()}; refreshes every 30 seconds.` : 'Checking hosts...'}
			</Text>
			{status && (
				<Grid columns={{ initial: '1', md: '3' }} gap="3">
					{status.hosts.map((host) => <StatusCard key={host.host} {...host} />)}
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

function ServiceCard({ item }) {
	return (
		<Card asChild size="2">
			<a href={serviceHref(item)} style={{ textDecoration: 'none' }}>
				<Flex align="center" gap="3">
					<ServiceIcon icon={item.icon} title={item.title} />
					<Text size="3" weight="medium">{item.title}</Text>
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
					<Grid columns={{ initial: '1', sm: '2' }} gap="3">
						{SERVICES.map((item) => (
							<ServiceCard key={item.title} item={item} />
						))}
					</Grid>
					<OperationalStatus />
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
