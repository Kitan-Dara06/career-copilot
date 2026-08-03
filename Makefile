.PHONY: dev lint typecheck test clean migrate migrate-new serve

dev: lint typecheck test
	@echo "✅ All checks passed"

lint:
	uv run ruff check . --fix
	uv run ruff format . --check

typecheck:
	uv run mypy career_copilot backbone agents --ignore-missing-imports --explicit-package-bases

test:
	uv run pytest -v

clean:
	rm -rf .pytest_cache .ruff_cache .mypy_cache
	rm -rf .coverage htmlcov
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

migrate:
	uv run alembic upgrade head

migrate-new:
	@read -p "Migration name: " name; \
	uv run alembic revision --autogenerate -m "$$name"

serve:
	uv run python -m career_copilot serve
