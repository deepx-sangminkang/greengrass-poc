from backend.ssh_manager import (
    run_script_over_ssh,
    stream_script_over_ssh,
    validate_ssh_request,
)


def test_validate_ssh_request_rejects_password_and_key_together():
    errors = validate_ssh_request(
        host="192.0.2.10",
        username="ubuntu",
        password="secret",
        private_key_path="/tmp/key.pem",
    )

    assert "password 또는 private_key_path 중 하나만 입력하세요." in errors


def test_validate_ssh_request_rejects_key_path_outside_allowlist():
    errors = validate_ssh_request(
        host="192.0.2.10",
        username="ubuntu",
        private_key_path="/etc/passwd",
    )

    assert "허용된 SSH 키 디렉터리" in errors[0]


def test_validate_ssh_request_rejects_invalid_port():
    errors = validate_ssh_request(
        host="192.0.2.10",
        username="ubuntu",
        password="secret",
        port=70000,
    )

    assert "port는 1부터 65535 사이여야 합니다." in errors


def test_run_script_over_ssh_loads_system_host_keys(monkeypatch):
    calls = []

    class FakeChannel:
        @staticmethod
        def recv_exit_status():
            return 0

    class FakeStream:
        channel = FakeChannel()

        @staticmethod
        def read():
            return b""

    class FakeSshClient:
        def load_system_host_keys(self):
            calls.append("load_system_host_keys")

        def set_missing_host_key_policy(self, _):
            calls.append("set_missing_host_key_policy")

        def connect(self, **_):
            calls.append("connect")

        def exec_command(self, *_args, **_kwargs):
            return None, FakeStream(), FakeStream()

        def close(self):
            calls.append("close")

    monkeypatch.setattr("paramiko.SSHClient", FakeSshClient)

    result = run_script_over_ssh(
        host="192.0.2.10",
        username="ubuntu",
        password="secret",
        script="echo ok",
    )

    assert result["ok"] is True
    assert calls[:2] == ["load_system_host_keys", "set_missing_host_key_policy"]


def test_run_script_over_ssh_passes_port_to_paramiko(monkeypatch):
    connect_kwargs = {}

    class FakeChannel:
        @staticmethod
        def recv_exit_status():
            return 0

    class FakeStream:
        channel = FakeChannel()

        @staticmethod
        def read():
            return b""

    class FakeSshClient:
        def load_system_host_keys(self):
            pass

        def set_missing_host_key_policy(self, _):
            pass

        def connect(self, **kwargs):
            connect_kwargs.update(kwargs)

        def exec_command(self, *_args, **_kwargs):
            return None, FakeStream(), FakeStream()

        def close(self):
            pass

    monkeypatch.setattr("paramiko.SSHClient", FakeSshClient)

    result = run_script_over_ssh(
        host="192.0.2.10",
        username="ubuntu",
        password="secret",
        port=2222,
        script="echo ok",
    )

    assert result["ok"] is True
    assert connect_kwargs["port"] == 2222


def test_run_script_over_ssh_uses_long_command_timeout(monkeypatch):
    captured = {}

    class FakeChannel:
        @staticmethod
        def recv_exit_status():
            return 0

    class FakeStream:
        channel = FakeChannel()

        @staticmethod
        def read():
            return b""

    class FakeSshClient:
        def load_system_host_keys(self):
            pass

        def set_missing_host_key_policy(self, _):
            pass

        def connect(self, **kwargs):
            captured["connect_timeout"] = kwargs.get("timeout")

        def exec_command(self, *_args, **kwargs):
            captured["command_timeout"] = kwargs.get("timeout")
            return None, FakeStream(), FakeStream()

        def close(self):
            pass

    monkeypatch.setattr("paramiko.SSHClient", FakeSshClient)

    result = run_script_over_ssh(
        host="192.0.2.10",
        username="ubuntu",
        password="secret",
        script="echo ok",
    )

    assert result["ok"] is True
    assert captured["connect_timeout"] <= 60
    assert captured["command_timeout"] >= 300


def _make_streaming_ssh_client(stdout_chunks, stderr_chunks, exit_code):
    class FakeChannel:
        def __init__(self):
            self._stdout = list(stdout_chunks)
            self._stderr = list(stderr_chunks)

        def settimeout(self, _):
            pass

        def exec_command(self, _command):
            pass

        def recv_ready(self):
            return bool(self._stdout)

        def recv(self, _size):
            return self._stdout.pop(0)

        def recv_stderr_ready(self):
            return bool(self._stderr)

        def recv_stderr(self, _size):
            return self._stderr.pop(0)

        def exit_status_ready(self):
            return not self._stdout and not self._stderr

        def recv_exit_status(self):
            return exit_code

    class FakeTransport:
        def open_session(self):
            return FakeChannel()

    class FakeSshClient:
        def load_system_host_keys(self):
            pass

        def set_missing_host_key_policy(self, _):
            pass

        def connect(self, **_kwargs):
            pass

        def get_transport(self):
            return FakeTransport()

        def close(self):
            pass

    return FakeSshClient


def test_stream_script_over_ssh_yields_incremental_output(monkeypatch):
    monkeypatch.setattr(
        "paramiko.SSHClient",
        _make_streaming_ssh_client([b"line1\n", b"line2\n"], [b"warn\n"], 0),
    )

    chunks = list(
        stream_script_over_ssh(
            host="192.0.2.10",
            username="ubuntu",
            password="secret",
            script="echo ok",
            poll_interval_seconds=0,
        )
    )
    text = "".join(chunks)

    assert "line1" in text
    assert "line2" in text
    assert "warn" in text
    assert "[exitCode=0]" in text


def test_stream_script_over_ssh_reports_connection_failure(monkeypatch):
    import paramiko

    class FailingSshClient:
        def load_system_host_keys(self):
            pass

        def set_missing_host_key_policy(self, _):
            pass

        def connect(self, **_kwargs):
            raise paramiko.SSHException("host key rejected")

        def close(self):
            pass

    monkeypatch.setattr("paramiko.SSHClient", FailingSshClient)

    text = "".join(
        stream_script_over_ssh(
            host="192.0.2.10",
            username="ubuntu",
            password="secret",
            script="echo ok",
        )
    )

    assert "SSH 연결 실패" in text
    assert "[exitCode=1]" in text
