.PHONY: run build docker-run docker-build test

run:
	uvicorn app.main:app --reload

build:  # local venv install
	python -m pip install --upgrade pip
	pip install -r requirements.txt

test:
	pytest -q

docker-build:
	docker build -t itnr-api .

docker-run:
	docker run --rm -p 8000:8000 --env-file .env itnr-api
