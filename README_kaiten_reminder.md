# Kaiten Reminder

Скрипт автоматически:

- обходит указанные доски Kaiten;
- находит карточки в колонке `В работе`;
- фильтрует их так, чтобы взять только те, где вы фигурируете как ответственный или участник/наблюдатель;
- оставляет комментарий в карточке с напоминанием затрекать время и отписаться по результатам;
- отправляет сводку в `Мою команду`.

## Файлы

- [kaiten_reminder.py](c:\Users\79201\Desktop\transcrib\kaiten_reminder.py)
- [kaiten_reminder.config.example.json](c:\Users\79201\Desktop\transcrib\kaiten_reminder.config.example.json)

## Настройка

1. Скопируйте `kaiten_reminder.config.example.json` в `kaiten_reminder.config.json`.
2. Заполните:
   - `kaiten.base_url` адресом вашего Kaiten;
   - `kaiten.board_ids` идентификаторами досок, которые нужно проверять;
   - `kaiten.my_user_id` вашим ID в Kaiten;
   - `work_column_titles` или `work_column_ids`;
   - токены `KAITEN_TOKEN` и `MYTEAM_BOT_TOKEN`.
3. Если в вашей инсталляции Kaiten комментарии принимаются не в поле `text`, а в другом поле, поменяйте `comment_text_field`.
4. Если авторизация в Kaiten отличается от `Authorization: Bearer ...`, поменяйте `auth_header_name` и `auth_header_value_template`.

## Переменные окружения

Скрипт умеет подставлять значения вида `${NAME}` из переменных окружения.

PowerShell:

```powershell
$env:KAITEN_TOKEN="..."
$env:MYTEAM_BOT_TOKEN="..."
```

## Запуск

Проверочный запуск без отправки комментариев:

```powershell
python kaiten_reminder.py --config kaiten_reminder.config.json --dry-run
```

Боевой запуск:

```powershell
python kaiten_reminder.py --config kaiten_reminder.config.json
```

Скрипт хранит отправленные за день напоминания в `.kaiten_reminder_state.json`, чтобы не дублировать комментарии при повторном запуске в тот же день.

## Планировщик Windows

Для запуска в конце дня можно создать задачу в Task Scheduler:

```powershell
schtasks /Create /SC DAILY /TN "KaitenReminder" /TR "powershell -NoProfile -Command \"$env:KAITEN_TOKEN='...'; $env:MYTEAM_BOT_TOKEN='...'; cd 'c:\Users\79201\Desktop\transcrib'; python kaiten_reminder.py --config kaiten_reminder.config.json\"" /ST 18:30
```

## Что может потребовать подстройки

- В разных инсталляциях Kaiten могут отличаться путь API, схема авторизации и поле текста комментария.
- В `Моей команде` могут отличаться базовый URL bot API и идентификатор чата.
- Определение “наблюдателя” зависит от структуры карточки в вашей инсталляции Kaiten. Скрипт уже проверяет `responsible_id`, `owner_id`, вложенные `members`, `watchers`, `member_ids`, `watcher_ids` и похожие поля, но при необходимости это можно расширить под ваш JSON.
