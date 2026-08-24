import json
from pathlib import Path

from django.conf import settings
from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from django.core.management.base import CommandError

from ports.util import process_geojson_dict_to_scenarios


class Command(BaseCommand):
    help = "Import scenarios and areas from scenario_area_data_* folders in the data directory."

    def add_arguments(self, parser):
        parser.add_argument(
            "--username",
            default=settings.DATA_USER,
            help="Username to assign as manager of imported scenarios (default: data).",
        )
        parser.add_argument(
            "--dir",
            default=settings.BASE_DIR / "data",
            help="Directory to scan (default: BASE_DIR/data).",
        )

    def handle(self, *args, **options):
        try:
            user = User.objects.get(username=options["username"])
        except User.DoesNotExist:
            raise CommandError(
                f"User '{options['username']}' does not exist."
            ) from User.DoesNotExist

        data_dir = Path(options["dir"])
        if not data_dir.exists():
            raise CommandError(f"Directory does not exist: {data_dir}")

        self.stdout.write(f"Scanning {data_dir} ...")
        folders = sorted(
            f
            for f in Path(data_dir).iterdir()
            if f.is_dir() and f.name.lower().startswith("scenario_area_data_")
        )
        if not folders:
            self.stdout.write(
                self.style.WARNING(
                    f"No folder starting with 'scenario_area_data_' found in {data_dir}.\nAborting."
                )
            )
            return
        count = 0
        for folder in folders:
            # get regions file and buildings file
            files = list(folder.iterdir())
            regions_file = next((f for f in files if "regions" in f.name.lower()), None)
            buildings_file = next((f for f in files if "buildings" in f.name.lower()), None)
            if not regions_file or not buildings_file:
                # either file does not exist -> skip folder
                continue
            count += 1
            with open(regions_file) as f:
                regions = json.load(f)
            with open(buildings_file) as f:
                buildings = json.load(f)
            process_geojson_dict_to_scenarios(regions, buildings, user)
        if count > 0:
            self.stdout.write(self.style.SUCCESS(f"Done. {count} folder(s) imported."))
        else:
            self.stdout.write(
                self.style.WARNING(
                    "No folder found containing at least one file with the name containing ('regions' or 'buildings )"
                )
            )
