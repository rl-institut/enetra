import time

from celery import shared_task

from core.models import Progress
from enetra.celery import app  # noqa
from ports.models import Scenario


@shared_task(bind=True)
def simulate_w_celery(self, scenario_id: int, progress_id: int):
    scenario = Scenario.objects.get(id=scenario_id)  # noqa
    progress = Progress.objects.get(id=progress_id)
    progress.status = Progress.Status.RUNNING
    progress.total_work = 66
    progress.save()
    print("started")
    try:
        for _i in range(10):
            time.sleep(2)
            progress.current_work += 10
            progress.save()
            print("Loop", progress.current_work)

        print("success")
        progress.set_success()
    except:
        progress.set_failed()
        raise
