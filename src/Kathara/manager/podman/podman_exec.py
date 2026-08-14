import json

def _exec_create(api, container_id, cmd, tty=True):
    resp = api.post(
        f"/containers/{container_id}/exec",
        headers={"Content-Type": "application/json"},
        data=json.dumps({
            "AttachStdin": True, "AttachStdout": True, "AttachStderr": True,
            "Tty": tty, "Cmd": cmd,
        }),
    )
    resp.raise_for_status()
    return resp.json()["Id"]

def _exec_start_hijack(api, exec_id, tty=True):
    resp = api.post(
        f"/exec/{exec_id}/start",
        headers={
            "Content-Type": "application/json",
            "Connection": "Upgrade", "Upgrade": "tcp",
        },
        data=json.dumps({"Detach": False, "Tty": tty}),
        stream=True,
    )
    sock = resp.raw.connection.sock
    sock._hijacked_response = resp   # impedisce a urllib3 di riprendersi la connessione
    return sock

def _exec_resize(api, exec_id, cols, rows):
    api.post(f"/exec/{exec_id}/resize", params={"h": rows, "w": cols}).raise_for_status()