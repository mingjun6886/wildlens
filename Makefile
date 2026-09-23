# Recipe lines below are indented with a literal TAB. Make requires it;
# spaces produce "missing separator" with no hint as to the cause.
.PHONY: help lint fmt test check clean

help:                ## Show this help
	@grep -E '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

lint:                ## Lint Python
	ruff check .

fmt:                 ## Format Python
	ruff format .
	ruff check --fix .

test:                ## Run unit tests (models mocked, no AWS)
	pytest -m "not integration"

check: lint test     ## Everything CI runs

clean:               ## Remove caches
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	rm -rf .pytest_cache .ruff_cache
