# Установка цели по умолчанию
.DEFAULT_GOAL := help

# Переменные для Docker
NAME ?= postgres-fastmcp
REPO ?=
TAG ?= latest
DOCKER_FULL_IMAGE := $(if $(REPO),$(REPO)/$(NAME),$(NAME)):$(TAG)

# Вспомогательная цель для проверки переменных Docker
.PHONY: check-docker-vars check-name check-repo

check-name:
	@if [ -z "$(NAME)" ]; then \
		echo "Error: NAME is not set"; \
		echo "Use: make <target> NAME=your-name"; \
		exit 1; \
	fi

check-repo:
	@if [ -z "$(REPO)" ]; then \
		echo "Error: REPO is not set"; \
		echo "Use: make <target> REPO=repo.example.com/namespace"; \
		exit 1; \
	fi

check-docker-vars: check-name check-repo

.PHONY: help lint format clean commit push release-patch release-minor release-major docker-build docker-push docker-run docker-run-it docker-stop check-docker-vars check-name check-repo

help: ## Показать справку по командам
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}' | sort

lint: ## Проверить код линтерами (ruff + mypy)
	uv run ruff check .
	uv run mypy src/

format: ## Отформатировать код (ruff format + автофиксы)
	uv run ruff format .
	uv run ruff check --fix .

clean: ## Очистить артефакты сборки и кэши
	rm -rf dist/ build/ *.egg-info/ .pytest_cache/ .ruff_cache/ .mypy_cache/ .coverage htmlcov/

# Управление версиями

release-patch: ## Релиз PATCH версии (clean → lock → bump → commit → push → merge)
	@echo "Запускаю релиз PATCH версии..."
	@echo "Очищаю артефакты сборки..."
	$(MAKE) clean
	@echo "Обновляю lock файл..."
	uv lock
	@echo "Увеличиваю PATCH версию..."
	uv version --bump patch
	@NEW_VERSION=$$(uv version --short); \
	echo "Новая версия: $$NEW_VERSION"; \
	echo "Обновляю локальный репозиторий..."; \
	git pull; \
	git add .; \
	git commit -m "Release v$$NEW_VERSION"; \
	git push; \
	echo "Релиз v$$NEW_VERSION создан и отправлен!"; \
	echo "Теперь создайте Merge Request в main ветку"

release-minor: ## Релиз MINOR версии (clean → lock → bump → commit → push → merge)
	@echo "Запускаю релиз MINOR версии..."
	@echo "Очищаю артефакты сборки..."
	$(MAKE) clean
	@echo "Обновляю lock файл..."
	uv lock
	@echo "Увеличиваю MINOR версию..."
	uv version --bump minor
	@NEW_VERSION=$$(uv version --short); \
	echo "Новая версия: $$NEW_VERSION"; \
	echo "Обновляю локальный репозиторий..."; \
	git pull; \
	git add .; \
	git commit -m "Release v$$NEW_VERSION"; \
	git push; \
	echo "Релиз v$$NEW_VERSION создан и отправлен!"; \
	echo "Теперь создайте Merge Request в main ветку"

release-major: ## Релиз MAJOR версии (clean → lock → bump → commit → push → merge)
	@echo "Запускаю релиз MAJOR версии..."
	@echo "Очищаю артефакты сборки..."
	$(MAKE) clean
	@echo "Обновляю lock файл..."
	uv lock
	@echo "Увеличиваю MAJOR версию..."
	uv version --bump major
	@NEW_VERSION=$$(uv version --short); \
	echo "Новая версия: $$NEW_VERSION"; \
	echo "Обновляю локальный репозиторий..."; \
	git pull; \
	git add .; \
	git commit -m "Release v$$NEW_VERSION"; \
	git push; \
	echo "Релиз v$$NEW_VERSION создан и отправлен!"; \
	echo "Теперь создайте Merge Request в main ветку"

# Git команды

commit: ## Сделать коммит с сообщением (интерактивно запрашивает сообщение)
	@echo "📝 Введите сообщение для коммита:"
	@read -p "Сообщение: " msg; \
	echo "🔄 Обновляю локальный репозиторий..."; \
	git pull; \
	git add .; \
	git commit -m "$$msg"; \
	echo "✅ Коммит создан!"

push: ## Сделать коммит и пуш (интерактивно запрашивает сообщение)
	@echo "📝 Введите сообщение для коммита:"
	@read -p "Сообщение: " msg; \
	echo "🔄 Обновляю локальный репозиторий..."; \
	git pull; \
	git add .; \
	git commit -m "$$msg"; \
	git push; \
	echo "✅ Коммит создан и отправлен в удаленный репозиторий!"

# Docker команды

# Сборка образов
docker-build: check-name ## Собрать Docker образ (для локального использования)
	@echo "🐳 Собираю Docker образ $(NAME)..."
	docker buildx build --provenance=false --sbom=false -t $(NAME):latest --load .
	@echo "✅ Docker образ собран успешно!"

# Полный цикл: мультиархитектурная сборка + загрузка
docker-push: check-docker-vars ## Собрать (amd64 + arm64) и загрузить образ в реестр
	@echo "🚀 Мультиархитектурная сборка → загрузка"
	@echo "🐳 Собираю образ для linux/amd64,linux/arm64..."; \
	docker buildx build --platform=linux/amd64,linux/arm64 --provenance=false --sbom=false -t $(DOCKER_FULL_IMAGE) --push -f Dockerfile . && \
	echo "✅ Образ загружен: $(DOCKER_FULL_IMAGE)"

# Запуск контейнеров
docker-run: check-name ## Запустить контейнер в фоновом режиме
	@echo "🚀 Запускаю контейнер $(NAME) в фоновом режиме..."
	@echo "💡 Передаю переменные окружения и .env файл..."
	@if [ -f .env ]; then \
		docker run -d --name $(NAME)-container \
			-p 8000:8000 \
			--env-file .env \
			$(NAME):latest; \
	else \
		docker run -d --name $(NAME)-container \
			-p 8000:8000 \
			$(NAME):latest; \
	fi
	@echo "✅ Контейнер запущен! Используйте 'make docker-stop NAME=$(NAME)' для остановки."

docker-run-it: check-name ## Запустить контейнер в интерактивном режиме
	@echo "🚀 Запускаю контейнер $(NAME) в интерактивном режиме..."
	@echo "💡 Передаю переменные окружения и .env файл..."
	@if [ -f .env ]; then \
		docker run -it --rm --name $(NAME)-interactive \
			-p 8000:8000 \
			--env-file .env \
			$(NAME):latest; \
	else \
		docker run -it --rm --name $(NAME)-interactive \
			-p 8000:8000 \
			$(NAME):latest; \
	fi

docker-stop: check-name ## Остановить и удалить контейнер
	@echo "🛑 Останавливаю контейнер $(NAME)..."
	-docker stop $(NAME)-container
	-docker rm $(NAME)-container
	@echo "✅ Контейнер остановлен и удален!"
