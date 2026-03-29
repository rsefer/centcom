class ServiceCard extends HTMLElement {
	connectedCallback() {
		if (this.shadowRoot) {
			return;
		}

		const href = this.getAttribute('href') || '#';
		const icon = this.getAttribute('icon') || '';
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
			</style>
			<a href="${href}">
				<h3 class="service__title"><span class="service__icon">${icon}</span><span class="service__title-label">${title}</span></h3>
			</a>
		`;
	}
}

if (!customElements.get('service-card')) {
	customElements.define('service-card', ServiceCard);
}
