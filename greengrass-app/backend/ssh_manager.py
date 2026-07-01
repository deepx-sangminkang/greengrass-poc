from pathlib import Path
import time

import paramiko

from backend.config import SSH_KEY_DIR

DEFAULT_CONNECT_TIMEOUT_SECONDS = 30
DEFAULT_COMMAND_TIMEOUT_SECONDS = 600


def _is_relative_to(path: Path, base: Path) -> bool:
    try:
        path.relative_to(base)
        return True
    except ValueError:
        return False


def validate_ssh_request(
    host: str,
    username: str,
    password: str | None = None,
    private_key_path: str | None = None,
    port: int = 22,
) -> list[str]:
    errors: list[str] = []
    if not host:
        errors.append("host는 필수입니다.")
    if not username:
        errors.append("username은 필수입니다.")
    if port < 1 or port > 65535:
        errors.append("port는 1부터 65535 사이여야 합니다.")
    if password and private_key_path:
        errors.append("password 또는 private_key_path 중 하나만 입력하세요.")
    if not password and not private_key_path:
        errors.append("password 또는 private_key_path 중 하나는 필요합니다.")
    if private_key_path:
        key_path = Path(private_key_path).expanduser().resolve()
        if not _is_relative_to(key_path, SSH_KEY_DIR):
            errors.append(f"허용된 SSH 키 디렉터리({SSH_KEY_DIR}) 안의 key만 사용할 수 있습니다.")
    return errors


def _build_connect_kwargs(
    host: str,
    username: str,
    password: str | None,
    private_key_path: str | None,
    port: int,
    connect_timeout_seconds: int,
) -> dict:
    connect_kwargs = {
        "hostname": host,
        "username": username,
        "port": port,
        "timeout": connect_timeout_seconds,
    }
    if password:
        connect_kwargs["password"] = password
    if private_key_path:
        connect_kwargs["key_filename"] = private_key_path
    return connect_kwargs


def run_script_over_ssh(
    host: str,
    username: str,
    script: str,
    password: str | None = None,
    private_key_path: str | None = None,
    port: int = 22,
    connect_timeout_seconds: int = DEFAULT_CONNECT_TIMEOUT_SECONDS,
    command_timeout_seconds: int = DEFAULT_COMMAND_TIMEOUT_SECONDS,
) -> dict:
    errors = validate_ssh_request(host, username, password, private_key_path, port)
    if errors:
        return {"ok": False, "errors": errors, "stdout": "", "stderr": ""}

    client = paramiko.SSHClient()
    client.load_system_host_keys()
    client.set_missing_host_key_policy(paramiko.RejectPolicy())
    connect_kwargs = _build_connect_kwargs(
        host, username, password, private_key_path, port, connect_timeout_seconds
    )

    client.connect(**connect_kwargs)
    try:
        _, stdout, stderr = client.exec_command(
            f"bash -s <<'EOF'\n{script}\nEOF",
            timeout=command_timeout_seconds,
        )
        exit_code = stdout.channel.recv_exit_status()
        return {
            "ok": exit_code == 0,
            "exitCode": exit_code,
            "stdout": stdout.read().decode("utf-8", errors="replace"),
            "stderr": stderr.read().decode("utf-8", errors="replace"),
        }
    finally:
        client.close()


def stream_script_over_ssh(
    host: str,
    username: str,
    script: str,
    password: str | None = None,
    private_key_path: str | None = None,
    port: int = 22,
    connect_timeout_seconds: int = DEFAULT_CONNECT_TIMEOUT_SECONDS,
    command_timeout_seconds: int = DEFAULT_COMMAND_TIMEOUT_SECONDS,
    poll_interval_seconds: float = 0.05,
):
    errors = validate_ssh_request(host, username, password, private_key_path, port)
    if errors:
        for message in errors:
            yield message + "\n"
        yield "[exitCode=1]\n"
        return

    client = paramiko.SSHClient()
    client.load_system_host_keys()
    client.set_missing_host_key_policy(paramiko.RejectPolicy())
    connect_kwargs = _build_connect_kwargs(
        host, username, password, private_key_path, port, connect_timeout_seconds
    )

    try:
        client.connect(**connect_kwargs)
    except (OSError, paramiko.SSHException) as error:
        client.close()
        yield f"SSH 연결 실패: {error}\n"
        yield "[exitCode=1]\n"
        return

    try:
        channel = client.get_transport().open_session()
        channel.settimeout(command_timeout_seconds)
        channel.exec_command(f"bash -s <<'EOF'\n{script}\nEOF")
        while True:
            produced = False
            while channel.recv_ready():
                data = channel.recv(4096)
                if data:
                    yield data.decode("utf-8", errors="replace")
                    produced = True
            while channel.recv_stderr_ready():
                data = channel.recv_stderr(4096)
                if data:
                    yield data.decode("utf-8", errors="replace")
                    produced = True
            if (
                channel.exit_status_ready()
                and not channel.recv_ready()
                and not channel.recv_stderr_ready()
            ):
                break
            if not produced:
                time.sleep(poll_interval_seconds)
        exit_code = channel.recv_exit_status()
        yield f"[exitCode={exit_code}]\n"
    finally:
        client.close()
