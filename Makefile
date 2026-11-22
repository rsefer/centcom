.DEFAULT_GOAL := help

.PHONY: help setup setup-cron reload deploy check-hosts check-services check-proxy generate dns-records boot-recover refresh-rss backup backup-infra test dashboard-test dashboard-build

help: ## Show available targets
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | awk -F ':.*## ' '{printf "  %-14s %s\n", $$1, $$2}'

setup: ## Install Docker and cron jobs on the host (run on the Pi)
	./scripts/setup.sh

setup-cron: ## Install or verify the cron jobs
	./scripts/setup-cron.sh

reload: ## Pull, build, and restart the full stack (run on the Pi)
	./scripts/reload.sh

deploy: ## Deploy published GitHub changes to all hosts: make deploy [HOST=host] [BRANCH=main]
	./scripts/remote-deploy.sh "$(or $(HOST),all)" "$(or $(BRANCH),main)"

check-hosts: ## Report drift between hosts (read-only): make check-hosts [HOST=host] [BRANCH=main]
	python3 scripts/check-hosts.py "$(or $(HOST),all)" "$(or $(BRANCH),main)"

check-services: ## Check DNS and service URLs without changing the stack
	python3 scripts/check-services.py

generate: ## Regenerate Caddy upstreams and dashboard services from services.toml
	python3 scripts/services.py generate

dns-records: ## Print the proxy DNS records: make dns-records [PROXY=primary|standby] [BATCH=1]
	python3 scripts/services.py dns $(or $(PROXY),primary) $(if $(BATCH),--change-batch)

check-proxy: ## Check a proxy directly: make check-proxy DOMAIN=example.com PROXY_IP=100.x.x.x
	sh scripts/check-proxy.sh "$(DOMAIN)" "$(PROXY_IP)"

test: ## Run the Python test suite
	PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v

dashboard-test: generate ## Run dashboard unit tests
	node --test services/caddy/www/src/*.test.js

dashboard-build: generate ## Build the dashboard with installed npm dependencies
	npm --prefix services/caddy/www run build

boot-recover: ## Run the boot recovery routine manually (run on the Pi)
	./scripts/boot-recover.sh

refresh-rss: ## Trigger a FreshRSS feed refresh (run on the Pi)
	./services/freshrss/refresh-rss.sh

backup: ## Back up all persistent data now (run on the Pi)
	./scripts/backup.sh

backup-infra: ## Deploy S3 backup bucket + IAM policy: make backup-infra BUCKET=name IAM_USER=name [DAYS=100] (admin AWS creds)
	./scripts/backup-infra.sh $(BUCKET) $(IAM_USER) $(or $(DAYS),100)
