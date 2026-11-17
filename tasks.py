"""Tasks for use with Invoke.

Copyright (c) 2023, Network to Code, LLC
Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at
  http://www.apache.org/licenses/LICENSE-2.0
Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
"""

import concurrent.futures
import json
import os
import re
import shutil
from pathlib import Path
from time import sleep

from invoke.collection import Collection
from invoke.tasks import task as invoke_task

ORIGINAL_COMPOSE_FILES = [
    "docker-compose.base.yml",
    "docker-compose.redis.yml",
    "docker-compose.postgres.yml",
    "docker-compose.dev.yml",
]


def is_truthy(arg):
    """Convert "truthy" strings into Booleans.

    Examples:
        >>> is_truthy('yes')
        True
    Args:
        arg (str): Truthy string (True values are y, yes, t, true, on and 1; false values are n, no,
        f, false, off and 0. Raises ValueError if val is anything else.
    """
    if isinstance(arg, bool):
        return arg

    val = str(arg).lower()
    if val in ("y", "yes", "t", "true", "on", "1"):
        return True
    elif val in ("n", "no", "f", "false", "off", "0"):
        return False
    else:
        raise ValueError(f"Invalid truthy value: `{arg}`")


# Use pyinvoke configuration for default values, see http://docs.pyinvoke.org/en/stable/concepts/configuration.html
namespace = Collection("nautobot_deployment")
namespace.configure(
    {
        # TODO: UPDATE THIS LINE WITH YOUR OWN DEPLOYMENT NAME
        "nautobot_deployment": {
            "nautobot_ver": "3.2.7",
            "project_name": "nautobot-deploy",
            "python_ver": "3.12",
            "local": False,
            "ephemeral_ports": False,
            "compose_dir": os.path.join(os.path.dirname(__file__), "development"),
            "compose_files": ORIGINAL_COMPOSE_FILES.copy(),
            "compose_http_timeout": "86400",
        }
    }
)


def _is_compose_included(context, name):
    return f"docker-compose.{name}.yml" in context.nautobot_deployment.compose_files


def _await_healthy_service(context, service):
    container_id = docker_compose(context, f"ps -q -- {service}", pty=False, echo=False, hide=True).stdout.strip()
    _await_healthy_container(context, container_id)


def _await_healthy_container(context, container_id):
    while True:
        result = context.run(
            "docker inspect --format='{{.State.Health.Status}}' " + container_id,
            pty=False,
            echo=False,
            hide=True,
        )
        if result.stdout.strip() == "healthy":
            break
        print(f"Waiting for `{container_id}` container to become healthy ...")
        sleep(1)


def _read_command_env(values) -> dict:
    """Reads the environment variables from the values and returns a dictionary.

    Examples:
        >>> _read_command_env('VAR1=VALUE1')
        {'VAR1': 'VALUE1'}
        os.environ["VAR2"] = "VALUE2"
        >>> _read_command_env(['VAR1=VALUE1', 'VAR2', 'VAR3'])
        {'VAR1': 'VALUE1', 'VAR2': 'VALUE2', 'VAR3': ''}
        >>> _read_command_env({'VAR1': 'VALUE1', 'VAR2': 'ANOHTER_VALUE'})
        {'VAR1': 'VALUE1', 'VAR2': 'ANOHTER_VALUE'}
    """
    if not values:
        return {}

    if isinstance(values, dict):
        return values

    def read(envs):
        if isinstance(envs, str):
            if "=" in envs:
                name, value = envs.split("=")
                yield name, value
            else:
                yield envs, os.getenv(envs, "")
        else:
            for env in envs:
                yield from read(env)

    return dict(read(values))


def task(function=None, *args, **kwargs):
    """Task decorator to override the default Invoke task decorator and add each task to the invoke namespace."""

    def task_wrapper(function=None):
        """Wrapper around invoke.task to add the task to the namespace as well."""
        if args or kwargs:
            task_func = invoke_task(*args, **kwargs)(function)
        else:
            task_func = invoke_task(function)
        namespace.add_task(task_func)
        return task_func

    if function:
        # The decorator was called with no arguments
        return task_wrapper(function)
    # The decorator was called with arguments
    return task_wrapper


def docker_compose(context, command, **kwargs):
    """Helper function for running a specific docker compose command with all appropriate parameters and environment.

    Args:
        context (obj): Used to run specific commands
        command (str): Command string to append to the "docker compose ..." command, such as "build", "up", etc.
        **kwargs: Passed through to the context.run() call.
    """
    _ensure_creds_env_file(context)
    build_env = {
        # Note: 'docker compose logs' will stop following after 60 seconds by default,
        # so we are overriding that by setting this environment variable.
        "COMPOSE_HTTP_TIMEOUT": context.nautobot_deployment.compose_http_timeout,
        "NAUTOBOT_VER": context.nautobot_deployment.nautobot_ver,
        "PYTHON_VER": context.nautobot_deployment.python_ver,
        **kwargs.pop("env", {}),
    }
    compose_command_tokens = [
        "docker compose",
        f"--project-name {context.nautobot_deployment.project_name}",
        f'--project-directory "{context.nautobot_deployment.compose_dir}"',
    ]

    for compose_file in context.nautobot_deployment.compose_files:
        compose_file_path = os.path.join(context.nautobot_deployment.compose_dir, compose_file)
        compose_command_tokens.append(f' -f "{compose_file_path}"')

    if context.nautobot_deployment.ephemeral_ports and context.nautobot_deployment.compose_files == ORIGINAL_COMPOSE_FILES:
        compose_file_path = os.path.join(context.nautobot_deployment.compose_dir, "docker-compose.ephemeral-ports.yml")
        compose_command_tokens.append(f' -f "{compose_file_path}"')

    compose_command_tokens.append(command)

    # If `service` was passed as a kwarg, add it to the end.
    service = kwargs.pop("service", None)
    if service is not None:
        compose_command_tokens.append(service)

    if "hide" not in kwargs:
        print(f'Running docker compose command "{command}"')
    compose_command = " ".join(compose_command_tokens)

    return context.run(compose_command, env=build_env, **kwargs)


@task
def dump_service_ports_to_disk(context):
    """Useful for downstream utilities without direct docker access to determine ports.

    This function will sometimes be called asynchronously while containers are still
    firing up, hence the `attempt` loop.
    """
    service_ports = {}

    for _ in range(4):
        result = docker_compose(context, "ps --format json", hide=True)

        for line in result.stdout.splitlines():
            try:
                service_def = json.loads(line)
                service_name = re.search(
                    r"com\.docker\.compose\.service=(?P<service>\w+)", service_def["Labels"]
                ).group("service")

                ports_found = {}
                for port in service_def["Publishers"]:
                    if port.get("PublishedPort", 0):
                        ports_found[port["TargetPort"]] = port["PublishedPort"]

                if ports_found:
                    service_ports[service_name] = ports_found
            except (json.decoder.JSONDecodeError, AttributeError, IndexError, KeyError):
                continue

        # Confirm nautobot has started
        if set(["nautobot"]).issubset(service_ports.keys()):
            break

        sleep(15)

    with open(".service_ports.json", "w", encoding="utf-8") as file:
        json.dump(service_ports, file, indent=4)


def run_command(context, command, service="nautobot", **kwargs):
    """Wrapper to run a command locally or inside the nautobot container."""
    env = _read_command_env(kwargs.pop("env", None))
    if is_truthy(context.nautobot_deployment.local):
        return context.run(command, **kwargs, env=env)
    else:
        # Check if service is running, no need to start another container to run a command
        docker_compose_status = "ps --services --filter status=running"
        results = docker_compose(context, docker_compose_status, hide="out")

        command_env_args = ""
        for env_name in env:
            command_env_args += f" --env={env_name}"

        if service in results.stdout:
            compose_command = f"exec{command_env_args} {service} {command}"
        else:
            compose_command = f"run{command_env_args} --rm --entrypoint='{command}' {service}"

        pty = kwargs.pop("pty", True)

        return docker_compose(context, compose_command, **kwargs, pty=pty, env=env)


# ------------------------------------------------------------------------------
# BUILD
# ------------------------------------------------------------------------------
@task(
    help={
        "force_rm": "Always remove intermediate containers",
        "cache": "Whether to use Docker's cache when building the image (defaults to enabled)",
    }
)
def build(context, force_rm=False, cache=True):
    """Build Nautobot docker image."""
    command = "build"

    if not cache:
        command += " --no-cache"
    if force_rm:
        command += " --force-rm"

    print(f"Building Nautobot with Python {context.nautobot_deployment.python_ver}...")
    docker_compose(context, command)


def _ensure_creds_env_file(context):
    """Ensure that the development/creds.env file exists."""
    if not os.path.exists(os.path.join(context.nautobot_deployment.compose_dir, "creds.env")):
        # Warn the user that the creds.env file does not exist and that we are copying the example file to it
        print("⚠️⚠️ The creds.env file does not exist, using the example file to create it. ⚠️⚠️")
        # Copy the creds.example.env file to creds.env
        shutil.copy(
            os.path.join(context.nautobot_deployment.compose_dir, "creds.example.env"),
            os.path.join(context.nautobot_deployment.compose_dir, "creds.env"),
        )


# ------------------------------------------------------------------------------
# START / STOP / DEBUG
# ------------------------------------------------------------------------------
@task(
    help={
        "service": "If specified, only affect the specified service(s); can be provided multiple times (i.e. -s nautobot -s worker)."
    },
    iterable=["service"],
)
def debug(context, service=None):
    """Start specified or all services and its dependencies in debug mode."""
    service = " ".join(service) if service else ""
    print(f"Starting {service or 'all services'} in debug mode...")
    with concurrent.futures.ThreadPoolExecutor() as executor:
        executor.submit(dump_service_ports_to_disk, context)
        docker_compose(context, "up", service=service)


@task(
    help={
        "service": "If specified, only affect the specified service(s); can be provided multiple times (i.e. -s nautobot -s worker)."
    },
    iterable=["service"],
)
def start(context, service=None):
    """Start specified service(s) or all services and its dependencies in detached mode."""
    service = " ".join(service) if service else ""
    print(f"Starting {service or 'all services'} in detached mode...")
    docker_compose(context, "up --detach", service=service)
    dump_service_ports_to_disk(context)


@task(
    help={
        "service": "If specified, only affect the specified service(s); can be provided multiple times (i.e. -s nautobot -s worker)."
    },
    iterable=["service"],
)
def restart(context, service=None):
    """Gracefully restart specified or all services."""
    service = " ".join(service) if service else ""
    print(f"Restarting {service or 'all services'}...")
    docker_compose(context, "restart", service=service)


@task(
    help={
        "service": "If specified, only affect the specified service(s); can be provided multiple times (i.e. -s nautobot -s worker)."
    },
    iterable=["service"],
)
def stop(context, service=None):
    """Stop specified or all services, if service is not specified, remove all containers."""
    service = " ".join(service) if service else ""
    print(f"Stopping {service or 'all services'}...")
    docker_compose(context, "stop" if service else "down --remove-orphans", service=service)


@task(
    aliases=("down",),
    help={
        "volumes": "Remove Docker compose volumes (default: True)",
        "import-db-file": "Import database from `import-db-file` file into the fresh environment (default: empty)",
    },
)
def destroy(context, volumes=True, import_db_file=""):
    """Destroy all containers and volumes."""
    print("Destroying Nautobot...")
    docker_compose(context, f"down --remove-orphans {'--volumes' if volumes else ''}")

    if not import_db_file:
        return

    if not volumes:
        raise ValueError("Cannot specify `--no-volumes` and `--import-db-file` arguments at the same time.")

    print(f"Importing database file: {import_db_file}...")

    input_path = Path(import_db_file).absolute()
    if not input_path.is_file():
        raise ValueError(f"File not found: {input_path}")

    command = [
        "run",
        "--rm",
        "--detach",
        f"--volume='{input_path}:/docker-entrypoint-initdb.d/dump.sql'",
        "--",
        "db",
    ]

    container_id = docker_compose(context, " ".join(command), pty=False, echo=False, hide=True).stdout.strip()
    _await_healthy_container(context, container_id)
    print("Stopping database container...")
    context.run(f"docker stop {container_id}", pty=False, echo=False, hide=True)

    print("Database import complete, you can start Nautobot with the following command:")
    print("invoke start")


@task
def export(context):
    """Export docker compose configuration to `compose.yaml` file.

    Useful to:

    - Debug docker compose configuration.
    - Allow using `docker compose` command directly without invoke.
    """
    docker_compose(context, "convert > compose.yaml")


@task(name="ps", help={"all": "Show all, including stopped containers"})
def ps_task(context, all=False):
    """List containers."""
    docker_compose(context, f"ps {'--all' if all else ''}")


@task(
    help={
        "service": "If specified, only display logs for the specified service(s) (default: all); can be provided multiple times (i.e. -s nautobot -s worker)",
        "follow": "Flag to follow logs (default: False)",
        "tail": "Tail N number of lines (default: all)",
    },
    iterable=["service"],
)
def logs(context, service=None, follow=False, tail=0):
    """View the logs of a docker compose service."""
    command = "logs "

    if follow:
        command += "--follow "
    if tail:
        command += f"--tail={tail} "
    service = " ".join(service) if service else None

    docker_compose(context, command, service=service)


# ------------------------------------------------------------------------------
# ACTIONS
# ------------------------------------------------------------------------------
@task(
    help={
        "file": "Python file to execute",
        "env": "Environment variables to pass to the command e.g.: `--env VAR1=VALUE1 --env VAR2`",
        "plain": "Flag to run nbshell in plain mode (default: False)",
    },
    iterable=["env"],
)
def nbshell(context, file="", env=None, plain=False):
    """Launch an interactive nbshell session.

    Files passed to the command can't contain unindented empty lines, as it breaks the nbshell interpreter.
    """
    command = [
        "nautobot-server",
        "nbshell",
        "--plain" if plain else "",
        f"< '{file}'" if file else "",
    ]
    run_command(context, " ".join(command), pty=not bool(file), env=env)


@task
def shell_plus(context):
    """Launch an interactive shell_plus session."""
    command = "nautobot-server shell_plus"
    run_command(context, command)


@task(
    help={
        "service": "Docker compose service name to launch cli in (default: nautobot).",
    }
)
def cli(context, service="nautobot"):
    """Launch a bash shell inside the container."""
    run_command(context, "bash", service=service)


@task(
    help={
        "user": "name of the superuser to create (default: admin)",
    }
)
def createsuperuser(context, user="admin"):
    """Create a new Nautobot superuser account (default: "admin"), will prompt for password."""
    command = f"nautobot-server createsuperuser --username {user}"

    run_command(context, command)

@task
def migrate(context):
    """Perform migrate operation in Django."""
    command = "nautobot-server migrate"

    run_command(context, command)


@task(help={})
def post_upgrade(context):
    """
    Performs Nautobot common post-upgrade operations using a single entrypoint.

    This will run the following management commands with default settings, in order:

    - migrate
    - trace_paths
    - collectstatic
    - remove_stale_contenttypes
    - clearsessions
    - invalidate all
    """
    command = "nautobot-server post_upgrade"

    run_command(context, command)


@task(
    help={
        "service": "Docker compose service name to run command in (default: nautobot).",
        "command": "Command to run (default: bash).",
        "file": "File to run command with (default: empty)",
    },
)
def exec(context, service="nautobot", command="bash", file=""):
    """Launch a command inside the running container (defaults to bash shell inside nautobot container)."""
    command = [
        "exec",
        "--",
        service,
        command,
        f"< '{file}'" if file else "",
    ]
    docker_compose(context, " ".join(command), pty=not bool(file))


@task(
    help={
        "db-name": "Database name (default: Nautobot database)",
        "input-file": "SQL file to execute and quit (default: empty, start interactive CLI)",
        "output-file": "Ouput file, overwrite if exists (default: empty, output to stdout)",
        "query": "SQL command to execute and quit (default: empty)",
    }
)
def dbshell(context, db_name="", input_file="", output_file="", query=""):
    """Start database CLI inside the running `db` container.

    Doesn't use `nautobot-server dbshell`, using started `db` service container only.
    """
    if input_file and query:
        raise ValueError("Cannot specify both, `input_file` and `query` arguments")
    if output_file and not (input_file or query):
        raise ValueError("`output_file` argument requires `input_file` or `query` argument")

    env = {}
    if query:
        env["_SQL_QUERY"] = query

    command = [
        "exec",
        "--env=_SQL_QUERY" if query else "",
        "-- db sh -c '",
    ]

    if _is_compose_included(context, "mysql"):
        command += [
            "mysql",
            "--user=$MYSQL_USER",
            "--password=$MYSQL_PASSWORD",
            f"--database={db_name or '$MYSQL_DATABASE'}",
        ]
    elif _is_compose_included(context, "postgres"):
        command += [
            "psql",
            "--username=$POSTGRES_USER",
            f"--dbname={db_name or '$POSTGRES_DB'}",
        ]
    else:
        raise ValueError("Unsupported database backend.")

    command += [
        "'",
        '<<<"$_SQL_QUERY"' if query else "",
        f"< '{input_file}'" if input_file else "",
        f"> '{output_file}'" if output_file else "",
    ]

    docker_compose(
        context,
        " ".join(command),
        env=env,
        pty=not (input_file or output_file or query),
    )


@task(
    help={
        "db-name": "Database name to create (default: Nautobot database)",
        "input-file": "SQL dump file to replace the existing database with. This can be generated using `invoke backup-db` (default: `dump.sql`).",
    }
)
def import_db(context, db_name="", input_file="dump.sql"):
    """Stop Nautobot containers and replace the current database with the dump into `db` container."""
    docker_compose(context, "stop -- nautobot worker beat")
    start(context, ["db"])
    _await_healthy_service(context, "db")

    command = ["exec -- db sh -c '"]

    if _is_compose_included(context, "mysql"):
        if not db_name:
            db_name = "$MYSQL_DATABASE"
        command += [
            "mysql --user root --password=$MYSQL_ROOT_PASSWORD",
            '--execute="',
            f"DROP DATABASE IF EXISTS {db_name};",
            f"CREATE DATABASE {db_name};",
            (
                ""
                if db_name == "$MYSQL_DATABASE"
                else f"GRANT ALL PRIVILEGES ON {db_name}.* TO $MYSQL_USER; FLUSH PRIVILEGES;"
            ),
            '"',
            "&&",
            "mysql",
            f"--database={db_name}",
            "--user=$MYSQL_USER",
            "--password=$MYSQL_PASSWORD",
        ]
    elif _is_compose_included(context, "postgres"):
        if not db_name:
            db_name = "$POSTGRES_DB"
        command += [
            f"dropdb --if-exists --user=$POSTGRES_USER {db_name} &&",
            f"createdb --user=$POSTGRES_USER {db_name} &&",
            f"psql --user=$POSTGRES_USER --dbname={db_name}",
        ]
    else:
        raise ValueError("Unsupported database backend.")

    command += [
        "'",
        f"< '{input_file}'",
    ]

    docker_compose(context, " ".join(command), pty=False)

    print("Database import complete, you can start Nautobot now: `invoke start`")


@task(
    help={
        "db-name": "Database name to backup (default: Nautobot database)",
        "output-file": "Ouput file, overwrite if exists (default: `dump.sql`)",
        "readable": "Flag to dump database data in more readable format (default: `True`)",
    }
)
def backup_db(context, db_name="", output_file="dump.sql", readable=True):
    """Dump database into `output_file` file from `db` container."""
    start(context, ["db"])
    _await_healthy_service(context, "db")

    command = ["exec -- db sh -c '"]

    if _is_compose_included(context, "mysql"):
        command += [
            "mysqldump",
            "--user=root",
            "--password=$MYSQL_ROOT_PASSWORD",
            "--skip-extended-insert" if readable else "",
            db_name if db_name else "$MYSQL_DATABASE",
        ]
    elif _is_compose_included(context, "postgres"):
        command += [
            "pg_dump",
            "--username=$POSTGRES_USER",
            f"--dbname={db_name or '$POSTGRES_DB'}",
            "--inserts" if readable else "",
        ]
    else:
        raise ValueError("Unsupported database backend.")

    command += [
        "'",
        f"> '{output_file}'",
    ]

    docker_compose(context, " ".join(command), pty=False)

    print(50 * "=")
    print("The database backup has been successfully completed and saved to the following file:")
    print(output_file)
    print("You can import this database backup with the following command:")
    print(f"invoke import-db --input-file '{output_file}'")
    print(50 * "=")




@task(name="help")
def help_task(context):
    """Print the help of available tasks."""
    import tasks  # pylint: disable=all

    root = Collection.from_module(tasks)
    for task_name in sorted(root.task_names):
        print(50 * "-")
        print(f"invoke {task_name} --help")
        context.run(f"invoke {task_name} --help")
