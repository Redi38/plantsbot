COMPOSE = docker compose

.PHONY: up down restart bot admin build logs logs-bot logs-admin ps stop clean

## Поднять всё (бот + админка), с пересборкой
up:
	$(COMPOSE) up -d --build

## Собрать и (пере)запустить только бота
bot:
	$(COMPOSE) up -d --build bot

## Собрать и (пере)запустить только админку
admin:
	$(COMPOSE) up -d --build admin

## Собрать образы без запуска
build:
	$(COMPOSE) build

## Остановить и удалить контейнеры
down:
	$(COMPOSE) down

## Остановить контейнеры, не удаляя их
stop:
	$(COMPOSE) stop

## Перезапустить всё
restart: down up

## Статус контейнеров
ps:
	$(COMPOSE) ps

## Логи всех сервисов (следить)
logs:
	$(COMPOSE) logs -f

## Логи только бота
logs-bot:
	$(COMPOSE) logs -f bot

## Логи только админки
logs-admin:
	$(COMPOSE) logs -f admin

## Остановить, удалить контейнеры и volume с данными (ОСТОРОЖНО: удаляет БД)
clean:
	$(COMPOSE) down -v
