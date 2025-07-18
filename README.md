## Installation

1. Clone this git repository (or [download a specific release](https://github.com/rl-institut/enetra/releases))
    ```bash
    git clone git@github.com:rl-institut/enetra.git
    ```
2. Install prerequisites
     1. This software requires GDAL, which can be installed
         - on Linux via the system's package manager (e.g. `apt install gdal-bin` on Ubuntu)
         - on macOS via [Homebrew](https://brew.sh/) (`brew install gdal`)
         - on Windows via [OSGeo4W](https://trac.osgeo.org/osgeo4w/) (select the `gdal` package)
    2. The software requires a PostgreSQL database with the PostGIS package.
         - The software is found [here](https://www.postgresql.org/download/) and [here](https://postgis.net/documentation/getting_started/) or via your system's (or server's) package manager (e.g. `apt install postgis`)
         - The credentials for the database are set in `enetra/settings.py` in the `DATABASES`variable. **SECURITY WARNING: Do not commit your passwords to GitHub!**
    3. The software can uses celery. A [backend for Celery](https://docs.celeryq.dev/en/stable/getting-started/backends-and-brokers/index.html) needs to be available. Its address is set in the `CELERY_BROKER_URL` in the `.env` file.
         - [rabbitmq](https://www.rabbitmq.com/) can be installed on ubuntu using `apt install rabbitmq-server`
         - A user can be added using the following commands (or the guest user can be used):
             1. `rabbitmqctl add_user $user $password`
             2. `rabbitmqctl set_permissions -p / $user ".*" ".*" ".*"`
    4. install [uv](https://docs.astral.sh/uv/getting-started/installation/)
    5. The dependencies can be installed via
        ```bash
        uv sync
        ```
       This installs the default/main dependencies as well as developer dependencies into .venv.
        - all python commands can be substituted by `uv run` which uses the venv python under the hood
        - alternatively the activating the venv works as usually
            ```macOS or Linux
            source .venv/bin/activate
            ```
            ```Windows powershell
            ``.venv\Scripts\activate`
    6. install pre-commit to make sure every git commit is ruff conform
        `pre-commit install`
        - to force commits during work in progress use
        `git commit --no-verify -m "commit message"`

    6. Django uses an .env file to read user specif data. This file has to be created by the user and is not shared through GitHub to make uploads of sensitive data impossible. Create a file named `.env` with the following input`
   ````text
     ````

3. Set up django (inside the virtual environment)
    1. Set up the database: `uv run manage.py migrate`
    2. Create admin account: `uv run manage.py createsuperuser`

## Running
1. Only if `.env`has a celery broker listed, start a celery worker (in another terminal): `celery -A ebusdjango worker -l info`
    - on macOS `OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES` before the command may be necessary
2. Run the server: `uv run manage.py runserver`

## Development
To run tests your PostgreSQL user needs SUPERUSER rights to be able to create test databases and delete them.

## Setup and Recreation of Database
During development, it might be necessary to delete the database and recreate it. The following instructions seem to work for Linux/Ubuntu
go into terminal and log in as postgres superuser
```bash
sudo -i -u postgres
```
Get into sql
```bash
psql -U postgres
```
Drop the old database
```bash
DROP DATABASE your_database;
```
Create new database
```bash
CREATE DATABASE your_database;
```
go into database via
```bash
\c your_database
```
or exit with \q and connect directly from normal terminal

```bash
psql -U postgres -d your_database
```

Define settings and install postgis and show installation worked

```bash
CREATE EXTENSION IF NOT EXISTS postgis;
SELECT PostGIS_version();
```

Create a user with access to the db
`CREATE USER myprojectuser WITH PASSWORD '1234';`

TODO: Are all privileges needed?
` GRANT ALL PRIVILEGES ON DATABASE your_database TO user_name; `

### restart postgres
postgres can be restarted
check status
`sudo service postgresql status`
restart
`sudo service postgresql restart`

## Loading a SQL dump

A database dump can be used to fill the database.
In your terminal navigate to the dump file ending with sql. If it is a text based dump use
```bash
psql -U YourProjectuser -h 127.0.0.1 YourDBName < DumpFileName.sql;
```
For some reason specifying the host seems to be needed on my machine. This has something to do how authentification seems to work.

## Docker install

To build a docker locally install docker using
https://docs.docker.com/engine/install/

Make sure to not have other dockers installed
https://docs.docker.com/engine/install/ubuntu/#uninstall-old-versions

For me only installing from package worked
https://docs.docker.com/engine/install/ubuntu/#install-from-a-package

wsl users can try following this
https://docs.docker.com/desktop/wsl/

## Docker build and run
Go into your django-enetra root containing manage.py

### With docker compose
Make sure your .env file reflects the docker-compose.yml properties
   ````text
DATABASE_URL=postgis://myprojectuser:1234@my-docker-postgres:5432/mydb
CELERY_BROKER_URL=redis://my-docker-redis:6379/0
````
When running locally for development some security settings need to be applied by setting in the .env file
   ````text
DJANGO_LOCAL_DEVELOPMENT=True
````
Navigate to your cloned repo of django-enetra in a terminal.
Now running the following line, builds and starts a docker container including creating a database
```bash
sudo docker compose up
```
After this enetra will be available under http://127.0.0.1:8000/

To stop containers and remove containers, networks, volumes, and images created by up, run
```bash
sudo docker compose down
```
The build has to be repeated if dependencies change. This can be fixed by rebuilding the docker or removing containers, volumes and images and running again
```bash
sudo docker compose up
```
Changes in the source code or templates are reflected live, even while running the docker container.

### Using Docker without compose
Create a network for your postgres and django-app to communicate
```bash
 docker network create mynetwork
 ```
Optional check your networks
```bash
 sudo docker network ls
  ```
run a postgis instance in this network. Set your database according to your settings
```bash
 sudo docker run --name my-docker-postgres -e POSTGRES_PASSWORD=1234 -e POSTGRES_USER=myprojectuser -e POSTGRES_DB=mydb -d --network=mynetwork postgis/postgis
  ```

 Go into your Django .env file and make sure the host is the same as in the above db, e.g. my-docker-postgres. This replaces "localhost" in the database url, e.g.
```bash
DATABASE_URL=postgis://myprojectuser:1234@my-docker-postgres:5432/mydb
  ```
The .env used while building will define which configuration the dockerimage will use
Build your Django-enetra docker
```bash
 sudo docker build -t django-enetra .
  ```
run the created docker in this network and expose the port
```bash
 sudo docker run -p 8000:8000 --network=mynetwork django-enetra
  ```
Optional you can use the flag -d to start the container as detached. This means closing the terminal will NOT stop the container
to stop the container
```bash
 sudo docker stop CONTAINER_ID
 ```

In the above options only docker compose uses a permanent storage for the database. By creating the volume postgres_data. In other words stopping the container and starting it again, the database will not have lost its data. At the same time, migrations have to respect the existing data as well.
### Volumes can be checked using
```bash
sudo docker volume ls
```

or removed
```bash
sudo docker volume rm VOLUME_ID
```
but only if the docker is not running
