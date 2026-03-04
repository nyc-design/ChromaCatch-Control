# chromacatch-control-sdk

Python SDK for the ChromaCatch Control backend.

## Install

```bash
pip install chromacatch-control-sdk
```

## Quick example

```python
from chromacatch_control_sdk import ChromaCatchControlClient

client = ChromaCatchControlClient(base_url="https://control.example.com", api_key="secret")
clients = client.list_clients()

if clients.total_clients:
    target = clients.connected_clients[0]
    client.send_command(client_id=target, action="click", params={"button": "left"}, command_type="mouse")
```
