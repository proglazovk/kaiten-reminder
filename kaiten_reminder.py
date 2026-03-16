import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set

import requests


DEFAULT_CONFIG_PATH = Path("kaiten_reminder.config.json")
DEFAULT_STATE_PATH = Path(".kaiten_reminder_state.json")


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def save_json(path: Path, payload: Any) -> None:
    with path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)


def resolve_env(value: Any) -> Any:
    if isinstance(value, str) and value.startswith("${") and value.endswith("}"):
        return os.environ.get(value[2:-1], "")
    if isinstance(value, list):
        return [resolve_env(item) for item in value]
    if isinstance(value, dict):
        return {key: resolve_env(item) for key, item in value.items()}
    return value


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Remind responsible users in Kaiten to track time and report progress for cards in work."
    )
    parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG_PATH),
        help="Path to JSON config file. Default: kaiten_reminder.config.json",
    )
    parser.add_argument(
        "--state",
        default=str(DEFAULT_STATE_PATH),
        help="Path to JSON state file. Default: .kaiten_reminder_state.json",
    )
    parser.add_argument("--dry-run", action="store_true", help="Do not send comments or messages.")
    parser.add_argument(
        "--date",
        help="Override current date in YYYY-MM-DD format for testing duplicate protection.",
    )
    return parser.parse_args()


class KaitenClient:
    def __init__(self, config: Dict[str, Any]) -> None:
        self.config = config
        self.base_url = config["base_url"].rstrip("/")
        self.api_prefix = config.get("api_prefix", "/api/latest")
        self.session = requests.Session()

        token = config["token"]
        auth_header_name = config.get("auth_header_name", "Authorization")
        auth_template = config.get("auth_header_value_template", "Bearer {token}")
        self.session.headers.update(
            {
                "Accept": "application/json",
                "Content-Type": "application/json",
                auth_header_name: auth_template.format(token=token),
            }
        )

    def _url(self, path: str) -> str:
        if path.startswith("http://") or path.startswith("https://"):
            return path
        return "{}{}{}".format(self.base_url, self.api_prefix, path)

    def get_board(self, board_id: int) -> Dict[str, Any]:
        response = self.session.get(self._url("/boards/{}".format(board_id)), timeout=60)
        response.raise_for_status()
        return response.json()

    def create_comment(self, card_id: int, text: str) -> Dict[str, Any]:
        endpoint = self.config.get("comment_endpoint_template", "/cards/{card_id}/comments")
        field_name = self.config.get("comment_text_field", "text")
        payload = {field_name: text}
        response = self.session.post(
            self._url(endpoint.format(card_id=card_id)),
            data=json.dumps(payload).encode("utf-8"),
            timeout=60,
        )
        response.raise_for_status()
        if response.content:
            return response.json()
        return {}


class MyTeamClient:
    def __init__(self, config: Dict[str, Any]) -> None:
        self.config = config
        self.enabled = bool(config.get("enabled"))
        self.base_url = config.get("base_url", "https://myteam.mail.ru/bot/v1").rstrip("/")
        self.token = config.get("token", "")
        self.chat_id = config.get("chat_id", "")
        self.session = requests.Session()

    def send_summary(self, text: str) -> None:
        if not self.enabled:
            return
        endpoint = self.config.get("send_text_endpoint", "/messages/sendText")
        params = {
            self.config.get("token_param_name", "token"): self.token,
            self.config.get("chat_id_param_name", "chatId"): self.chat_id,
            self.config.get("text_param_name", "text"): text,
        }
        response = self.session.get(self.base_url + endpoint, params=params, timeout=60)
        response.raise_for_status()


def normalize_text(value: str) -> str:
    return " ".join((value or "").strip().lower().split())


def collect_scalar_ids(obj: Any) -> Set[int]:
    ids: Set[int] = set()
    if isinstance(obj, dict):
        for key, value in obj.items():
            lowered = key.lower()
            if lowered in {"id", "user_id", "member_id", "watcher_id", "responsible_id", "owner_id"} and isinstance(
                value, int
            ):
                ids.add(value)
            else:
                ids.update(collect_scalar_ids(value))
    elif isinstance(obj, list):
        for item in obj:
            ids.update(collect_scalar_ids(item))
    return ids


def extract_card_title(card: Dict[str, Any]) -> str:
    for key in ("title", "name", "subject"):
        if card.get(key):
            return str(card[key])
    return "Без названия"


def extract_card_id(card: Dict[str, Any]) -> int:
    if "id" not in card:
        raise KeyError("Card payload has no id field.")
    return int(card["id"])


def extract_card_column_id(card: Dict[str, Any]) -> Optional[int]:
    for key in ("column_id", "lane_id", "status_id"):
        value = card.get(key)
        if isinstance(value, int):
            return value
    return None


def extract_responsible_id(card: Dict[str, Any]) -> Optional[int]:
    for key in ("responsible_id", "owner_id"):
        value = card.get(key)
        if isinstance(value, int):
            return value
    return None


def is_archived(card: Dict[str, Any]) -> bool:
    return bool(card.get("archived") or card.get("is_archived") or card.get("deleted"))


def build_columns_map(board: Dict[str, Any]) -> Dict[int, Dict[str, Any]]:
    columns = board.get("columns") or board.get("lanes") or []
    result = {}
    for column in columns:
        if isinstance(column, dict) and "id" in column:
            result[int(column["id"])] = column
    return result


def get_work_column_ids(config: Dict[str, Any], columns_map: Dict[int, Dict[str, Any]]) -> Set[int]:
    ids = set(int(item) for item in config.get("work_column_ids", []))
    wanted_titles = {normalize_text(item) for item in config.get("work_column_titles", [])}
    for column_id, column in columns_map.items():
        title = column.get("title") or column.get("name") or ""
        if normalize_text(title) in wanted_titles:
            ids.add(column_id)
    return ids


def card_is_related_to_me(card: Dict[str, Any], my_user_id: int) -> bool:
    if extract_responsible_id(card) == my_user_id:
        return True
    return my_user_id in collect_scalar_ids(card)


def render_comment(template: str, card: Dict[str, Any], column_name: str, today: str) -> str:
    return template.format(
        card_id=extract_card_id(card),
        card_title=extract_card_title(card),
        column_name=column_name,
        today=today,
    ).strip()


def render_summary(template: str, board_name: str, reminders: List[Dict[str, Any]], today: str) -> str:
    cards_lines = []
    for item in reminders:
        cards_lines.append("- #{card_id} {card_title}".format(**item))

    cards_block = "\n".join(cards_lines) if cards_lines else "- Напоминания не отправлялись"
    return template.format(board_name=board_name, cards_block=cards_block, count=len(reminders), today=today).strip()


def ensure_config(config: Dict[str, Any]) -> None:
    required_paths = [
        ("kaiten", "base_url"),
        ("kaiten", "token"),
        ("kaiten", "my_user_id"),
        ("kaiten", "board_ids"),
        ("notification", "comment_template"),
    ]
    for section, key in required_paths:
        if not config.get(section, {}).get(key):
            raise ValueError("Missing config value: {}.{}".format(section, key))


def process_board(
    kaiten: KaitenClient,
    config: Dict[str, Any],
    board_id: int,
    today: str,
    state: Dict[str, Any],
    dry_run: bool,
) -> Dict[str, Any]:
    board = kaiten.get_board(board_id)
    board_name = board.get("title") or board.get("name") or "Board {}".format(board_id)
    columns_map = build_columns_map(board)
    work_column_ids = get_work_column_ids(config["kaiten"], columns_map)
    cards = board.get("cards") or []
    my_user_id = int(config["kaiten"]["my_user_id"])
    comment_template = config["notification"]["comment_template"]
    reminders = []

    for card in cards:
        if not isinstance(card, dict) or is_archived(card):
            continue

        column_id = extract_card_column_id(card)
        if column_id not in work_column_ids:
            continue

        if not card_is_related_to_me(card, my_user_id):
            continue

        card_id = extract_card_id(card)
        state_key = "{}:{}".format(today, card_id)
        if state.get("sent", {}).get(state_key):
            continue

        column_name = ""
        if column_id in columns_map:
            column_name = columns_map[column_id].get("title") or columns_map[column_id].get("name") or ""

        text = render_comment(comment_template, card, column_name, today)
        reminder_item = {
            "card_id": card_id,
            "card_title": extract_card_title(card),
            "comment_text": text,
            "board_name": board_name,
        }

        if not dry_run:
            kaiten.create_comment(card_id, text)
            state.setdefault("sent", {})[state_key] = {
                "card_id": card_id,
                "board_id": board_id,
                "board_name": board_name,
                "sent_at": dt.datetime.utcnow().isoformat() + "Z",
            }

        reminders.append(reminder_item)

    return {"board_name": board_name, "reminders": reminders}


def main() -> int:
    args = parse_args()
    config_path = Path(args.config)
    state_path = Path(args.state)

    if not config_path.exists():
        print("Config file not found: {}".format(config_path), file=sys.stderr)
        return 1

    config = resolve_env(load_json(config_path, {}))
    ensure_config(config)
    state = load_json(state_path, {"sent": {}})

    today = args.date or dt.date.today().isoformat()
    kaiten = KaitenClient(config["kaiten"])
    myteam = MyTeamClient(config.get("myteam", {}))

    all_reminders = []
    for board_id in config["kaiten"]["board_ids"]:
        board_result = process_board(kaiten, config, int(board_id), today, state, args.dry_run)
        all_reminders.append(board_result)

    summary_template = config.get(
        "myteam",
        {},
    ).get(
        "summary_template",
        "Напоминания за {today}\nДоска: {board_name}\nКоличество: {count}\n{cards_block}",
    )

    for board_result in all_reminders:
        reminders = board_result["reminders"]
        print(
            "[{}] {} reminder(s) on board '{}'".format(
                "DRY-RUN" if args.dry_run else "OK", len(reminders), board_result["board_name"]
            )
        )
        for item in reminders:
            print("  - #{} {}".format(item["card_id"], item["card_title"]))

        if reminders:
            summary_text = render_summary(summary_template, board_result["board_name"], reminders, today)
            if args.dry_run:
                print("\n--- MyTeam summary preview ---\n{}\n".format(summary_text))
            else:
                myteam.send_summary(summary_text)

    if not args.dry_run:
        save_json(state_path, state)

    return 0


if __name__ == "__main__":
    sys.exit(main())
