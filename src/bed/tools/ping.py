"""Connect to a BED WebSocket, send a ping, prompt for a moniker, then exit."""
import argparse
import asyncio
import json

import websockets


async def main_async(host: str, port: int) -> None:
    url = f"ws://{host}:{port}/"
    async with websockets.connect(url) as ws:
        await ws.send(json.dumps({"type": "ping"}))
        pong = json.loads(await ws.recv())
        assert pong.get("type") == "pong", f"expected pong, got {pong!r}"
        print(f"<- {pong}")
        moniker = input("moniker: ").strip()
        auth = {"type": "auth", "moniker": moniker, "password": ""}
        await ws.send(json.dumps(auth))
        result = json.loads(await ws.recv())
        print(f"<- {result}")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--host", default="localhost")
    p.add_argument("--port", type=int, default=8765)
    args = p.parse_args()
    asyncio.run(main_async(args.host, args.port))


if __name__ == "__main__":
    main()
