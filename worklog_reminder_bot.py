import argparse
import datetime as dt
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Set

import requests

if TYPE_CHECKING:
    from selenium.webdriver.remote.webelement import WebElement


DEFAULT_CONFIG_PATH = Path("worklog_reminder.config.json")
DEFAULT_STATE_PATH = Path(".worklog_reminder_state.json")


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
        description="Remind task owners in Kaiten and MyTeam web UI to track time and report work results."
    )
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    parser.add_argument("--state", default=str(DEFAULT_STATE_PATH))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--date", help="Override current date in YYYY-MM-DD format.")
    parser.add_argument("--comment-suffix", default="", help="Append text to each generated comment.")
    parser.add_argument(
        "--only",
        choices=["all", "kaiten", "myteam"],
        default="all",
        help="Run only one integration.",
    )
    return parser.parse_args()


def format_today(value: str) -> str:
    try:
        parsed = dt.datetime.strptime(value, "%Y-%m-%d").date()
        return parsed.strftime("%d.%m.%y")
    except ValueError:
        return value


def wait_for(driver: Any, selector: Dict[str, str], timeout: int = 30) -> "WebElement":
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.support.ui import WebDriverWait

    return WebDriverWait(driver, timeout).until(
        EC.presence_of_element_located((selector["by"], selector["value"]))
    )


def wait_all(driver: Any, selector: Dict[str, str], timeout: int = 30) -> List["WebElement"]:
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.support.ui import WebDriverWait

    WebDriverWait(driver, timeout).until(
        EC.presence_of_element_located((selector["by"], selector["value"]))
    )
    return driver.find_elements(selector["by"], selector["value"])


def normalize_text(value: str) -> str:
    return " ".join((value or "").strip().lower().split())


def text_contains_any(value: str, candidates: List[str]) -> bool:
    normalized = normalize_text(value)
    return any(normalize_text(item) in normalized for item in candidates if item)


def ensure_config(config: Dict[str, Any]) -> None:
    if "kaiten" not in config and "myteam_web" not in config:
        raise ValueError("At least one integration must be configured.")


def make_selector_map(raw: Dict[str, Any]) -> Dict[str, Dict[str, str]]:
    from selenium.webdriver.common.by import By

    selectors = {}
    for key, value in raw.items():
        if isinstance(value, dict) and "by" in value and "value" in value:
            selectors[key] = {"by": getattr(By, value["by"].upper()), "value": value["value"]}
    return selectors


class KaitenClient:
    def __init__(self, config: Dict[str, Any]) -> None:
        self.config = config
        self.base_url = config["base_url"].rstrip("/")
        self.api_prefix = config.get("api_prefix", "/api/latest")
        self.transport = config.get("transport", "requests")
        self.session = requests.Session()

        auth_header_name = config.get("auth_header_name", "Authorization")
        auth_template = config.get("auth_header_value_template", "Bearer {token}")
        self.session.headers.update(
            {
                "Accept": "application/json",
                "Content-Type": "application/json",
                auth_header_name: auth_template.format(token=config["token"]),
            }
        )

    def _url(self, path: str) -> str:
        return "{}{}{}".format(self.base_url, self.api_prefix, path)

    def _powershell_request(self, method: str, path: str, payload: Optional[Dict[str, Any]] = None) -> Any:
        url = self._url(path)
        auth_header_name = self.config.get("auth_header_name", "Authorization")
        auth_template = self.config.get("auth_header_value_template", "Bearer {token}")
        token = self.config["token"]
        body_json = json.dumps(payload, ensure_ascii=False) if payload is not None else ""

        script = [
            "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8",
            "$headers = @{{ '{}' = '{}' ; 'Accept' = 'application/json' ; 'Content-Type' = 'application/json' }}".format(
                auth_header_name,
                auth_template.format(token=token).replace("'", "''"),
            ),
        ]
        if payload is not None:
            script.append("$body = @'\n{}\n'@".format(body_json))
        if method.upper() == "GET":
            script.append("$response = Invoke-RestMethod -Method Get -Uri '{}' -Headers $headers".format(url))
        elif method.upper() == "POST":
            script.append(
                "$bytes = [System.Text.Encoding]::UTF8.GetBytes($body)"
            )
            script.append(
                "$response = Invoke-RestMethod -Method Post -Uri '{}' -Headers $headers -Body $bytes -ContentType 'application/json; charset=utf-8'".format(
                    url
                )
            )
        else:
            raise ValueError("Unsupported method: {}".format(method))
        script.append("$response | ConvertTo-Json -Depth 100")

        completed = subprocess.run(
            ["powershell", "-NoProfile", "-Command", "\n".join(script)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=True,
        )
        output = completed.stdout.strip()
        return json.loads(output) if output else {}

    def get_board(self, board_id: int) -> Dict[str, Any]:
        path = "/boards/{}".format(board_id)
        if self.transport == "powershell":
            return self._powershell_request("GET", path)
        response = self.session.get(self._url(path), timeout=60)
        response.raise_for_status()
        return response.json()

    def create_comment(self, card_id: int, text: str) -> None:
        endpoint = self.config.get("comment_endpoint_template", "/cards/{card_id}/comments")
        field_name = self.config.get("comment_text_field", "text")
        payload = {field_name: text}
        if self.transport == "powershell":
            self._powershell_request("POST", endpoint.format(card_id=card_id), payload)
            return
        response = self.session.post(
            self._url(endpoint.format(card_id=card_id)),
            data=json.dumps(payload).encode("utf-8"),
            timeout=60,
        )
        response.raise_for_status()


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


def collect_strings(obj: Any) -> Set[str]:
    values: Set[str] = set()
    if isinstance(obj, dict):
        for value in obj.values():
            values.update(collect_strings(value))
    elif isinstance(obj, list):
        for item in obj:
            values.update(collect_strings(item))
    elif isinstance(obj, str):
        values.add(obj)
    return values


def extract_card_title(card: Dict[str, Any]) -> str:
    for key in ("title", "name", "subject"):
        if card.get(key):
            return str(card[key])
    return "Без названия"


def extract_card_id(card: Dict[str, Any]) -> int:
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


def build_columns_map(board: Dict[str, Any]) -> Dict[int, Dict[str, Any]]:
    columns = board.get("columns") or board.get("lanes") or []
    result = {}
    for column in columns:
        if isinstance(column, dict) and "id" in column:
            result[int(column["id"])] = column
    return result


def get_work_column_ids(config: Dict[str, Any], columns_map: Dict[int, Dict[str, Any]]) -> Set[int]:
    ids = set(int(item) for item in config.get("work_column_ids", []))
    wanted_titles = {normalize_text(item) for item in config.get("work_column_titles", ["В работе"])}
    for column_id, column in columns_map.items():
        title = column.get("title") or column.get("name") or ""
        normalized_title = normalize_text(title)
        if normalized_title in wanted_titles:
            ids.add(column_id)
            continue
        if any(wanted in normalized_title or normalized_title in wanted for wanted in wanted_titles):
            ids.add(column_id)
    return ids


def card_is_related_to_me(card: Dict[str, Any], my_identity: Any, identity_variants: List[str]) -> bool:
    if isinstance(my_identity, int):
        if extract_responsible_id(card) == my_identity:
            return True
        if my_identity in collect_scalar_ids(card):
            return True

    searchable_strings = collect_strings(card)
    for value in searchable_strings:
        if text_contains_any(value, identity_variants):
            return True
    return False


def process_kaiten(
    config: Dict[str, Any], state: Dict[str, Any], today: str, dry_run: bool, comment_suffix: str
) -> List[Dict[str, Any]]:
    if "kaiten" not in config:
        return []

    kaiten = KaitenClient(config["kaiten"])
    results = []
    my_identity = config["kaiten"]["my_user_id"]
    identity_variants = config["kaiten"].get("my_identity_variants", [])
    if isinstance(my_identity, str) and my_identity:
        identity_variants = [my_identity] + identity_variants
    comment_template = config["notification"]["comment_template"]

    for board_id in config["kaiten"]["board_ids"]:
        board = kaiten.get_board(int(board_id))
        columns_map = build_columns_map(board)
        work_column_ids = get_work_column_ids(config["kaiten"], columns_map)
        board_name = board.get("title") or board.get("name") or "Board {}".format(board_id)
        reminders = []

        for card in board.get("cards") or []:
            if not isinstance(card, dict):
                continue

            column_id = extract_card_column_id(card)
            if column_id not in work_column_ids:
                continue
            if not card_is_related_to_me(card, my_identity, identity_variants):
                continue

            card_id = extract_card_id(card)
            state_key = "kaiten:{}:{}".format(today, card_id)
            if state.get("sent", {}).get(state_key):
                continue

            text = comment_template.format(
                today=format_today(today),
                task_title=extract_card_title(card),
                column_name=(columns_map.get(column_id) or {}).get("title", "В работе"),
                system_name="Kaiten",
            )
            if comment_suffix:
                text = "{} {}".format(text.rstrip(), comment_suffix).strip()

            if not dry_run:
                kaiten.create_comment(card_id, text)
                state.setdefault("sent", {})[state_key] = {
                    "system": "kaiten",
                    "board_id": board_id,
                    "task_id": card_id,
                    "sent_at": dt.datetime.utcnow().isoformat() + "Z",
                }

            reminders.append({"task_id": card_id, "task_title": extract_card_title(card), "text": text})

        results.append({"system": "kaiten", "board_name": board_name, "reminders": reminders})
    return results


class MyTeamWebBot:
    def __init__(self, config: Dict[str, Any]) -> None:
        from selenium import webdriver
        from selenium.webdriver import EdgeOptions
        from selenium.webdriver.edge.service import Service as EdgeService

        self.config = config
        self.selectors = make_selector_map(config["selectors"])
        options = EdgeOptions()
        options.use_chromium = True
        if config.get("browser_binary"):
            options.binary_location = config["browser_binary"]
        if config.get("headless"):
            options.add_argument("--headless=new")
        if config.get("user_data_dir"):
            options.add_argument("--user-data-dir={}".format(config["user_data_dir"]))
        if config.get("profile_directory"):
            options.add_argument("--profile-directory={}".format(config["profile_directory"]))
        for arg in config.get("browser_args", []):
            options.add_argument(arg)
        service = None
        driver_path = config.get("driver_path")
        if driver_path:
            service = EdgeService(executable_path=driver_path)
        self.driver = webdriver.Edge(service=service, options=options)
        self.driver.set_window_size(1440, 1200)
        self.wait_seconds = int(config.get("wait_seconds", 20))

    def close(self) -> None:
        self.driver.quit()

    def open_board(self, url: str) -> None:
        self.driver.get(url)
        if "board_ready" in self.selectors:
            wait_for(self.driver, self.selectors["board_ready"], self.wait_seconds)
        else:
            time.sleep(5)

    def find_task_cards(self) -> List["WebElement"]:
        return wait_all(self.driver, self.selectors["task_card"], self.wait_seconds)

    def extract_text(self, root: "WebElement", selector_name: str) -> str:
        from selenium.common.exceptions import NoSuchElementException

        selector = self.selectors.get(selector_name)
        if not selector:
            return ""
        try:
            return root.find_element(selector["by"], selector["value"]).text
        except NoSuchElementException:
            return ""

    def card_matches(self, card: "WebElement") -> bool:
        column_text = self.extract_text(card, "column_name") or card.text
        if not text_contains_any(column_text, self.config.get("work_column_titles", ["В работе"])):
            return False

        identities = self.config.get("my_identity_variants", [])
        responsible_text = self.extract_text(card, "responsible")
        participants_text = self.extract_text(card, "participants")
        watchers_text = self.extract_text(card, "watchers")
        searchable = "\n".join([responsible_text, participants_text, watchers_text, card.text])
        return text_contains_any(searchable, identities)

    def open_card(self, card: "WebElement") -> None:
        selector = self.selectors.get("open_card_click_target")
        target = card
        if selector:
            target = card.find_element(selector["by"], selector["value"])
        self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", target)
        target.click()
        wait_for(self.driver, self.selectors["comment_input"], self.wait_seconds)

    def current_task_id(self) -> str:
        selector = self.selectors.get("task_id")
        if selector:
            try:
                return self.driver.find_element(selector["by"], selector["value"]).text.strip()
            except Exception:
                pass
        return self.driver.current_url

    def current_task_title(self) -> str:
        selector = self.selectors.get("task_title")
        if selector:
            try:
                return self.driver.find_element(selector["by"], selector["value"]).text.strip()
            except Exception:
                pass
        return "Без названия"

    def write_comment(self, text: str, dry_run: bool) -> None:
        input_box = wait_for(self.driver, self.selectors["comment_input"], self.wait_seconds)
        input_box.click()
        input_box.send_keys(text)
        if dry_run:
            return
        submit = self.driver.find_element(
            self.selectors["comment_submit"]["by"], self.selectors["comment_submit"]["value"]
        )
        submit.click()
        time.sleep(self.config.get("comment_submit_sleep_seconds", 2))

    def close_card_dialog(self) -> None:
        from selenium.common.exceptions import NoSuchElementException

        selector = self.selectors.get("close_card")
        if not selector:
            self.driver.back()
            return
        try:
            self.driver.find_element(selector["by"], selector["value"]).click()
        except NoSuchElementException:
            self.driver.back()
        time.sleep(self.config.get("between_cards_sleep_seconds", 1))


def process_myteam(
    config: Dict[str, Any], state: Dict[str, Any], today: str, dry_run: bool, comment_suffix: str
) -> List[Dict[str, Any]]:
    if "myteam_web" not in config:
        return []

    bot = MyTeamWebBot(config["myteam_web"])
    comment_template = config["notification"]["comment_template"]
    results = []
    try:
        for board in config["myteam_web"]["boards"]:
            board_name = board.get("name", board["url"])
            bot.open_board(board["url"])
            reminders = []

            cards = bot.find_task_cards()
            for card in cards:
                if not bot.card_matches(card):
                    continue

                bot.open_card(card)
                task_id = bot.current_task_id()
                state_key = "myteam:{}:{}".format(today, task_id)
                if state.get("sent", {}).get(state_key):
                    bot.close_card_dialog()
                    continue

                title = bot.current_task_title()
                text = comment_template.format(
                    today=format_today(today),
                    task_title=title,
                    column_name="В работе",
                    system_name="Моя команда",
                )
                if comment_suffix:
                    text = "{} {}".format(text.rstrip(), comment_suffix).strip()

                bot.write_comment(text, dry_run=dry_run)
                if not dry_run:
                    state.setdefault("sent", {})[state_key] = {
                        "system": "myteam",
                        "board": board_name,
                        "task_id": task_id,
                        "sent_at": dt.datetime.utcnow().isoformat() + "Z",
                    }

                reminders.append({"task_id": task_id, "task_title": title, "text": text})
                bot.close_card_dialog()

            results.append({"system": "myteam", "board_name": board_name, "reminders": reminders})
    finally:
        bot.close()

    return results


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

    all_results = []
    if args.only in ("all", "kaiten"):
        all_results.extend(process_kaiten(config, state, today, args.dry_run, args.comment_suffix))
    if args.only in ("all", "myteam"):
        all_results.extend(process_myteam(config, state, today, args.dry_run, args.comment_suffix))

    for result in all_results:
        print("[{}] {} -> {} reminder(s)".format(result["system"], result["board_name"], len(result["reminders"])))
        for item in result["reminders"]:
            print("  - {} {}".format(item["task_id"], item["task_title"]))

    if not args.dry_run:
        save_json(state_path, state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
