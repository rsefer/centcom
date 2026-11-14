class ServiceCard extends HTMLElement {
	connectedCallback() {
		if (this.shadowRoot) {
			return;
		}

		const service = this.getAttribute('service');
		const path = this.getAttribute('path') || '';
		const href = service
			? `${window.location.protocol}//${service}.${window.location.hostname}${path}`
			: this.getAttribute('href') || '#';
		const icon = this.getAttribute('icon') || '';
		const iconImage = this.getAttribute('iconImage') || '';
		const title = this.getAttribute('title') || 'Service';

		this.attachShadow({ mode: 'open' });
		this.shadowRoot.innerHTML = `
			<style>
				a {
					display: block;
					padding: 1rem;
					background: var(--color-grey-light);
					border-radius: 0.25rem;
					border-left: 0.25rem solid var(--color-primary);
					text-decoration: none;
				}
				.service__title {
					display: flex;
					flex-flow: row wrap;
					justify-content: flex-start;
					align-items: center;
					gap: 0.5em;
					margin: 0;
					color: var(--color-secondary);
				}
				.service__icon {
					font-size: 1.5em;
				}
				.service__icon img {
					display: block;
					width: 1em;
					height: 1em;
				}
			</style>
			<a href="${href}">
				<h3 class="service__title"><span class="service__icon">${iconImage ? `<img src="${iconImage}" alt="${title} icon">` : icon}</span><span class="service__title-label">${title}</span></h3>
			</a>
		`;
	}
}

if (!customElements.get('service-card')) {
	customElements.define('service-card', ServiceCard);
}

const search = document.querySelector('#service-search');
const searchStatus = document.querySelector('#service-search-status');
const dialog = document.querySelector('#service-dialog');
const results = document.querySelector('#service-results');
const serviceResults = Array.from(document.querySelectorAll('service-card')).map((card) => {
	const title = card.getAttribute('title') || 'Service';
	const item = document.createElement('li');
	const link = document.createElement('a');

	link.href = card.shadowRoot.querySelector('a').href;
	link.textContent = title;
	item.append(link);
	results.append(item);

	return { title: title.toLowerCase(), item, link };
});

function filterServices() {
	const query = search.value.trim().toLowerCase();
	let visibleCount = 0;

	serviceResults.forEach(({ title, item }) => {
		const matches = title.includes(query);
		item.hidden = !matches;
		visibleCount += matches ? 1 : 0;
	});

	searchStatus.textContent = visibleCount === 0 ? 'No services match your search.' : '';
}

search.addEventListener('input', filterServices);
search.addEventListener('keydown', (event) => {
	if (event.key !== 'Enter') {
		return;
	}

	const visibleResults = serviceResults.filter(({ item }) => !item.hidden);
	if (visibleResults.length === 1) {
		event.preventDefault();
		visibleResults[0].link.click();
	}
});

document.querySelector('.dialog-close').addEventListener('click', () => dialog.close());
dialog.addEventListener('close', () => {
	search.value = '';
	filterServices();
});

document.addEventListener('keydown', (event) => {
	if (event.metaKey && event.key.toLowerCase() === 'k') {
		event.preventDefault();
		if (!dialog.open) {
			dialog.showModal();
			search.focus();
		}
	}
});
