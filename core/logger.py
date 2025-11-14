import logging
from contextlib import contextmanager
from pathlib import Path


class ScenarioLogger(logging.Logger):
    def __init__(self, name, scenario, level=logging.INFO):
        super().__init__(name, level)
        self.scenario = scenario

    def _log(self, level, msg, *args, **kwargs):
        # Prefix message with foo attribute
        msg = f"[S:{self.scenario.id}] {msg}"
        super()._log(level, msg, args, **kwargs)


@contextmanager
def create_scenario_logger(name, scenario, level):
    logger = ScenarioLogger(name, scenario, level)
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(levelname)s %(asctime)s: %(message)s"))
    logger.addHandler(handler)

    filehandler = logging.FileHandler(Path(f"./logs/scenario_{scenario.id}"))
    filehandler.setFormatter(logging.Formatter("%(levelname)s %(asctime)s: %(message)s"))
    logger.addHandler(filehandler)
    try:
        yield logger
    finally:
        # Clean up: remove and close handlers
        for h in logger.handlers[:]:
            logger.removeHandler(h)
            h.close()
