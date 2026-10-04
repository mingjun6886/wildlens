# Recipe lines below are indented with a literal TAB. Make requires it;
# spaces produce "missing separator" with no hint as to the cause.
.PHONY: help lint fmt test test-web build-web check clean

help:                ## Show this help
	@grep -E '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

lint:                ## Lint Python
	ruff check .

fmt:                 ## Format Python
	ruff format .
	ruff check --fix .

test:                ## Run Python unit tests (models mocked, no AWS)
	pytest -m "not integration"

test-web:            ## Run browser-module tests (no browser needed)
# node:test rather than a runner with a config file. These modules are pure
# functions - hashing and PKCE - and the two things they must get exactly right
# are checked against external references: shasum's digest and the worked example
# in RFC 7636. A bug in either produces a valid-looking wrong answer.
	node --test web/src/*.test.mjs

build-web:           ## Build the client, which also type-checks the imports
	cd web && npm run build

check: lint test test-web  ## Everything CI runs

clean:               ## Remove caches and build output
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	rm -rf .pytest_cache .ruff_cache web/dist
	rm -rf infra/terraform/modules/api_functions/.build
