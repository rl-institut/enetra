from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.core.management.base import CommandError

from ports.util import process_file_to_solar_radiation


class Command(BaseCommand):
    help = "Import Solar radiation data from solar_radiation/ folders in the data directory"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dir",
            default=settings.BASE_DIR / "data",
            help="Directory to scan (default: BASE_DIR/data).",
        )

    def handle(self, *args, **options):
        data_dir = Path(options["dir"])
        if not data_dir.exists():
            raise CommandError(f"Directory does not exist: {data_dir}")

        self.stdout.write(f"Scanning {data_dir} ...")
        folders = sorted(
            f
            for f in Path(data_dir).iterdir()
            if f.is_dir() and f.name.lower() == ("solar_radiation")
        )
        if not folders:
            self.stdout.write(
                self.style.WARNING(
                    f"No folder named 'solar_radiation' found in {data_dir}.\nAborting."
                )
            )
            return
        assert len(folders) == 1
        count = 0
        for file in folders[0].iterdir():
            if file.suffix == ".csv":
                process_file_to_solar_radiation(file)
                count += 1

        self.stdout.write(self.style.SUCCESS(f"Done. {count} file(s) imported."))
        if not count:
            self.stdout.write(self.style.WARNING("No files csv files found )"))
