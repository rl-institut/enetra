from pathlib import Path

from django.core.management.base import BaseCommand
from django.core.management.base import CommandError

from ports.util import process_file_to_solar_radiation


class Command(BaseCommand):
    help = "Import Solar radiation data from csv files in given directory"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dir",
            type=Path,
            help="Directory to scan",
        )

    def handle(self, *args, **options):
        data_dir = options["dir"]
        if not data_dir.exists():
            raise CommandError(f"Directory does not exist: {data_dir}")
        count = 0
        for file in data_dir.iterdir():
            if file.suffix == ".csv":
                self.stdout.write(f"Scanning {file} ...")
                process_file_to_solar_radiation(file)
                count += 1
        self.stdout.write(self.style.SUCCESS(f"Done. {count} file(s) imported."))
        if not count:
            self.stdout.write(self.style.WARNING("No csv files found )"))
