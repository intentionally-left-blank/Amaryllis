# Быстрый старт

## Цель

Запустить локальный runtime и получить первый ответ без ручной настройки моделей.

## API

1. Проверить профиль онбординга: `GET /models/onboarding/profile`
2. Получить план активации: `GET /models/onboarding/activation-plan`
3. Активировать пакет модели: `POST /models/onboarding/activate`

## Проверка

- Сервис отвечает на `/v1/chat/completions`
- Онбординг завершается со статусом `activated` или `activated_with_smoke_warning`

