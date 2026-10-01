.PHONY: install run up down migrate seed test docs

install:
	pip install -r requirements.txt

run:
	uvicorn app.main:app --reload --port 8000

up:
	docker compose up --build

down:
	docker compose down

migrate:
	alembic upgrade head

seed:
	python scripts/seed.py

test:
	pytest -q

docs:
	python scripts/export_openapi.py
