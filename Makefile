.PHONY: up down build test test-integration logs reset help

API_BASE ?= http://localhost:8001
API_KEY ?= change-me-long-random

help:
	@echo "Article Index - dev commands"
	@echo ""
	@echo "  make up          - Start stack (docker compose up -d --build)"
	@echo "  make down        - Stop stack"
	@echo "  make build       - Rebuild images"
	@echo "  make test        - Run async integration tests"
	@echo "  make test-integration - Same as test (explicit)"
	@echo "  make logs        - Tail API, worker, flower logs"
	@echo "  make reset       - Down with volumes, then up"
	@echo "  make smoke       - Run smoke test script"
	@echo ""
	@echo "  API_BASE=http://localhost:8001 make test  # override API base"

up:
	docker compose up -d --build

down:
	docker compose down

build:
	docker compose build

test test-integration:
	API_BASE=$(API_BASE) API_KEY=$(API_KEY) pytest tests/test_async_ingestion.py tests/test_async_failure.py -v -m integration

logs:
	docker compose logs -f api worker flower

reset:
	docker compose down -v
	docker compose up -d --build

smoke:
	API_BASE=$(API_BASE) API_KEY=$(API_KEY) ./scripts/smoke_test.sh
