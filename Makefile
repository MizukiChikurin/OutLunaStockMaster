# 开发常用命令

.PHONY: install install-all test lint format clean build-plugin

install:
	pip install -e .

install-all:
	pip install -e ".[all]"

test:
	pytest

lint:
	ruff check outluna tests
	mypy outluna

format:
	ruff format outluna tests

build-plugin:
	python scripts/build_plugin.py

clean:
	rm -rf build dist *.egg-info .pytest_cache
	rm -rf astrbot_plugin_outluna
