import sys

from somewheria_app import create_app
from somewheria_app.services.console import get_console_logger, set_console_log_level


app = create_app()
logger = get_console_logger("startup")


def start_cache_refresh_thread() -> None:
    app.extensions["somewheria_services"].properties.start_background_refresh()


def print_check_file(path, purpose) -> None:
    app.extensions["somewheria_services"].appointments.print_check_file(path, purpose)


def _prompt_choice(prompt: str, default: str, valid_choices: dict[str, str]) -> str:
    while True:
        answer = input(f"{prompt} [{default}]: ").strip().lower()
        if not answer:
            return default
        if answer in valid_choices:
            return answer
        print(f"Please enter one of: {', '.join(valid_choices)}")


def _prompt_yes_no(prompt: str, default: bool) -> bool:
    default_label = "y" if default else "n"
    while True:
        answer = input(f"{prompt} [y/n, default {default_label}]: ").strip().lower()
        if not answer:
            return default
        if answer in {"y", "yes"}:
            return True
        if answer in {"n", "no"}:
            return False
        print("Please answer y or n.")


def _prompt_port(default: int) -> int:
    while True:
        answer = input(f"Which port should the server use? [{default}]: ").strip()
        if not answer:
            return default
        if answer.isdigit():
            port = int(answer)
            if 1 <= port <= 65535:
                return port
        print("Please enter a valid port number between 1 and 65535.")


def run_startup_questions() -> dict[str, object]:
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return {
            "console_level": "INFO",
            "show_request_logs": True,
            "warm_cache": True,
            "host": "0.0.0.0",
            "port": 5000,
            "show_startup_summary": True,
        }

    print()
    print("Startup setup")
    print("1. How much console output do you want?")
    console_choice = _prompt_choice(
        "Choose quiet / normal / debug",
        "normal",
        {"quiet": "WARNING", "normal": "INFO", "debug": "DEBUG"},
    )

    print()
    print("2. Do you want each web request printed to the console?")
    show_request_logs = _prompt_yes_no("Show request logs", True)

    print()
    print("3. Should the property cache warm up before the server starts?")
    warm_cache = _prompt_yes_no("Warm cache now", True)

    print()
    print("4. Should the site be reachable only on this computer?")
    local_only = _prompt_yes_no("Bind to localhost only", False)

    print()
    print("5. Which port should the server run on?")
    port = _prompt_port(5000)

    print()
    print("6. Do you want a quick startup summary with useful URLs?")
    show_startup_summary = _prompt_yes_no("Show startup summary", True)

    return {
        "console_level": {"quiet": "WARNING", "normal": "INFO", "debug": "DEBUG"}[console_choice],
        "show_request_logs": show_request_logs,
        "warm_cache": warm_cache,
        "host": "127.0.0.1" if local_only else "0.0.0.0",
        "port": port,
        "show_startup_summary": show_startup_summary,
    }


if __name__ == "__main__":
    services = app.extensions["somewheria_services"]
    startup_options = run_startup_questions()
    set_console_log_level(startup_options["console_level"])
    app.config["SHOW_REQUEST_LOGS"] = startup_options["show_request_logs"]

    logger.info("Starting Somewheria web application")
    print_check_file(services.config.property_appointments_file, "Appointments file at startup")
    logger.info(
        "Startup options: level=%s, request_logs=%s, warm_cache=%s, host=%s, port=%s",
        startup_options["console_level"],
        "on" if startup_options["show_request_logs"] else "off",
        "yes" if startup_options["warm_cache"] else "no",
        startup_options["host"],
        startup_options["port"],
    )
    if startup_options["warm_cache"]:
        logger.info("Warming property cache before launch")
        services.properties.refresh_cache()
    start_cache_refresh_thread()
    if startup_options["show_startup_summary"]:
        logger.info(
            "Quick links: home=http://localhost:%s  status=http://localhost:%s/admin/status",
            startup_options["port"],
            startup_options["port"],
        )
        logger.info(
            "Server visibility: %s",
            "localhost only" if startup_options["host"] == "127.0.0.1" else "localhost and local network",
        )
    logger.info("Listening on http://%s:%s", startup_options["host"], startup_options["port"])
    app.run(startup_options["host"], port=startup_options["port"], debug=False)
