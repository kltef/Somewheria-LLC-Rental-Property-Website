from .console import get_console_logger


class AppointmentService:
    def __init__(self, config) -> None:
        self.config = config
        self.logger = get_console_logger("appointments")

    def print_check_file(self, path, purpose: str) -> None:
        abs_path = path.resolve()
        status = "exists" if abs_path.exists() else "does NOT exist"
        self.logger.info("%s: %s (%s)", purpose, abs_path, status)

    def load(self) -> dict[str, set[str]]:
        appointments: dict[str, set[str]] = {}
        abs_path = self.config.property_appointments_file.resolve()
        self.logger.info("Loading appointments from %s", abs_path)
        if not self.config.property_appointments_file.exists():
            self.logger.info("Appointments file does not exist yet: %s", abs_path)
            return appointments
        with self.config.property_appointments_file.open("r", encoding="utf-8") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    property_id, dates = line.split(":", 1)
                    appointments[property_id.strip()] = {item for item in dates.split(",") if item}
                except Exception:
                    continue
        return appointments

    def save(self, appointments: dict[str, set[str]]) -> None:
        abs_path = self.config.property_appointments_file.resolve()
        self.logger.info("Saving %s appointment sets to %s", len(appointments), abs_path)
        with self.config.property_appointments_file.open("w", encoding="utf-8") as handle:
            for property_id, date_set in appointments.items():
                print(f"{property_id}:{','.join(sorted(date_set))}", file=handle)
        self.print_check_file(self.config.property_appointments_file, "Appointments saved")
