# Worklog Reminder Bot

Этот инструмент делает одинаковую проверку в `Kaiten` и в веб-интерфейсе `Моей команды`:

- обходит указанные доски;
- ищет карточки в колонке `В работе`;
- берёт только те, где вы фигурируете как ответственный или участник/наблюдатель;
- оставляет комментарий с напоминанием затрекать время и отписаться по результату.

## Файлы

- [worklog_reminder_bot.py](c:\Users\79201\Desktop\transcrib\worklog_reminder_bot.py)
- [worklog_reminder.config.example.json](c:\Users\79201\Desktop\transcrib\worklog_reminder.config.example.json)

## Что уже реализовано

- `Kaiten`: работа через API.
- `Моя команда`: работа через Selenium и браузер Edge с сохранённой сессией.
- Защита от дублей в течение дня через `.worklog_reminder_state.json`.
- Режим `--dry-run`, чтобы сначала проверить отбор карточек.

## Настройка

1. Скопируйте `worklog_reminder.config.example.json` в `worklog_reminder.config.json`.
2. Для `Kaiten` заполните `base_url`, `board_ids`, `my_user_id`, токен и при необходимости параметры авторизации.
3. Для `Моей команды` заполните:
   - `boards[].url`;
   - `my_identity_variants` вашим ФИО и/или логином в нескольких вариантах;
   - при необходимости `profile_directory`, если вы используете не `Default`.
4. При необходимости подправьте `selectors`, если DOM вашей страницы отличается от шаблона.

## Важный момент по Edge

Скрипт использует профиль Edge с сохранённой авторизацией. Перед запуском лучше закрыть все окна Edge, иначе профиль может быть занят.

## Запуск

Проверка только Kaiten:

```powershell
python worklog_reminder_bot.py --config worklog_reminder.config.json --only kaiten --dry-run
```

Проверка только Моей команды:

```powershell
python worklog_reminder_bot.py --config worklog_reminder.config.json --only myteam --dry-run
```

Полный боевой запуск:

```powershell
python worklog_reminder_bot.py --config worklog_reminder.config.json
```

## GitHub Actions для Kaiten

Для запуска без вашего ПК уже добавлен workflow:

- [.github/workflows/kaiten-reminder.yml](c:\Users\79201\Desktop\transcrib\.github\workflows\kaiten-reminder.yml)

Он запускает только `Kaiten` и не зависит от включённого компьютера.

Что нужно сделать:

1. Залить проект в GitHub-репозиторий.
2. В репозитории открыть `Settings -> Secrets and variables -> Actions`.
3. Создать secret `KAITEN_TOKEN`.
4. Убедиться, что в репозитории включены GitHub Actions.

Расписание:

- workflow настроен на `14:30 UTC`, это `17:30` по Москве.
- также есть ручной запуск через `workflow_dispatch`.

Важно:

- Этот workflow запускает только `Kaiten`.
- Блок `Моя команда` через GitHub Actions в текущем виде не подойдёт, потому что он завязан на локальный браузер Edge и сохранённую авторизацию.

## Что может потребовать донастройки

- Публично задокументированного API задач для “Моей команды” у нас нет, поэтому там используется автоматизация интерфейса.
- Для “Моей команды” почти наверняка придётся один раз уточнить CSS/XPath селекторы под вашу конкретную страницу.
- Если структура карточки Kaiten отличается от ожидаемой, можно расширить правила определения ответственного и наблюдателей.
